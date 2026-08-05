"""backtest.backtester — walk-forward, theo allocation chứ không theo từng lệnh.

Mỗi bar đặt một tỷ trọng danh mục mục tiêu dựa trên regime vol phát hiện
được, rebalance khi lệch đủ nhiều. Dùng Decimal cho toàn bộ số lượng/giá
trong đường thực thi — int() sẽ làm tròn vị thế BTC dưới một đơn vị về 0
(xem CLAUDE.md bất biến #3).

**Cảnh báo cấu hình:** `settings.yaml` có `hmm.min_train_bars: 730` nhưng
`backtest.is_bars: 365` — hai giá trị này KHÔNG tương thích cho walk-forward
(mỗi window IS chỉ có đúng `is_bars` bar để train). `HMMRegimeEngine` truyền
vào `WalkForwardBacktester` PHẢI được cấu hình `min_train_bars <= is_bars`,
nếu không `select_and_train` sẽ raise ngay ở window đầu tiên. Đây không phải
lỗi của module này — `min_train_bars: 730` đọc như một ngưỡng an toàn chung
cho training "sống" (đủ 2 năm dữ liệu thật trước khi tin một model live),
trong khi §4.1 của spec định nghĩa CHÍNH XÁC IS=365 bar cho walk-forward.
Người gọi (main.py cho live, script backtest cho ở đây) chịu trách nhiệm
dựng engine với `min_train_bars` phù hợp với mục đích của nó.

**Phạm vi phase này:** `RiskManager` (Phase 5 trong đánh số spec, phase-08
trong đánh số prompts/ của dự án) chưa được implement. Backtester kết hợp
HMM → strategy → trend gate qua `compose_layer_allocations()` và dừng ở đó
— không có bước risk_manager.validate_signal() trong vòng lặp mô phỏng này.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import cast

import pandas as pd

from backtest.cost_model import CostModel, CostReport
from broker.instrument_rules import InstrumentRules
from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.signal_generator import compose_layer_allocations
from core.trend_gate import StructuralTrendGate
from data.feature_engineering import FeatureConfig, compute_tier1_features

_STRATEGY_BARS_LOOKBACK = 300  # đủ cho EMA50/ATR14 hội tụ, tránh tính lại toàn bộ lịch sử mỗi bar


@dataclass(frozen=True)
class WalkForwardConfig:
    is_bars: int = 365
    oos_bars: int = 182
    step_bars: int = 182
    fill_delay_bars: int = 1
    rebalance_threshold_pct: Decimal = Decimal("25")
    instrument_rules: InstrumentRules = field(
        default_factory=lambda: InstrumentRules(
            symbol="BTCUSDT",
            base_precision=Decimal("0.000001"),
            quote_precision=Decimal("0.0000001"),
            tick_size=Decimal("0.1"),
            min_order_qty=Decimal("0.000001"),
            min_order_amt=Decimal("5"),
            max_order_qty=Decimal("230"),
        )
    )
    initial_equity: Decimal = Decimal("10000")


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trade_log: pd.DataFrame
    regime_history: pd.DataFrame
    cost_report: CostReport
    metadata: dict = field(default_factory=dict)
    # Một dòng mỗi window: BIC chọn ra n_components nào, và chọn với biên bao
    # nhiêu. Xem `_record_model_selection` để biết vì sao cột `bic_margin`
    # là cột đáng đọc nhất ở đây.
    model_selection: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class _PortfolioState:
    cash: Decimal
    qty: Decimal


class WalkForwardBacktester:
    def __init__(
        self,
        hmm_engine: HMMRegimeEngine,
        strategy_orchestrator: StrategyOrchestrator,
        trend_gate: StructuralTrendGate,
        cost_model: CostModel,
        config: WalkForwardConfig,
    ) -> None:
        self.hmm_engine = hmm_engine
        self.strategy_orchestrator = strategy_orchestrator
        self.trend_gate = trend_gate
        self.cost_model = cost_model
        self.config = config

    def run(self, symbol: str, ohlcv: pd.DataFrame, start: datetime, end: datetime) -> BacktestResult:
        """Cửa sổ trượt IS/OOS: train HMM trên IS, đi qua OOS từng bar bằng
        filtered inference, mark-to-market bằng Decimal, rebalance khi lệch
        > ngưỡng, ghi một 'trade' mỗi lần allocation thay đổi.
        """
        ohlcv = ohlcv.sort_index()
        features = compute_tier1_features(ohlcv, FeatureConfig())

        # Trần trend gate KHÔNG phụ thuộc HMM/cửa sổ walk-forward — tính một
        # lần trên TOÀN BỘ lịch sử giá thô rồi tra bảng theo bar, thay vì gọi
        # lại get_allocation_cap() (thuần, không cache) mỗi bar trong vòng
        # lặp: mỗi lần gọi lại quét tuần tự từ đầu, O(n) mỗi lần → O(n^2)
        # toàn backtest. Tính một lần và tra cứu vẫn cho kết quả HỆT NHAU
        # (get_structure_history là hàm thuần trên tiền tố dữ liệu) nhưng
        # rẻ hơn nhiều bậc.
        trend_gate_history = self.trend_gate.get_structure_history(ohlcv)

        windows = self._plan_windows(features, start, end)
        if not windows:
            raise ValueError(
                f"Không đủ dữ liệu cho bất kỳ window nào trong [{start}, {end}] "
                f"với is_bars={self.config.is_bars}."
            )

        portfolio = _PortfolioState(cash=self.config.initial_equity, qty=Decimal("0"))
        pending: deque[tuple[pd.Timestamp, Decimal]] = deque()  # (bar thực thi, target_allocation)

        equity_rows: list[dict] = []
        trade_rows: list[dict] = []
        regime_rows: list[dict] = []
        model_selection_rows: list[dict] = []

        for window_idx, (is_start, is_end, oos_start, oos_new_end) in enumerate(windows):
            is_features = features.iloc[is_start:is_end]
            self.hmm_engine.select_and_train(is_features)
            regime_infos = self.hmm_engine.regime_infos
            model_selection_rows.append(
                self._record_model_selection(
                    window_idx, features.index[is_start], features.index[is_end - 1], len(is_features)
                )
            )

            for i in range(is_end, oos_new_end):
                ts = features.index[i]
                current_price = Decimal(str(ohlcv.loc[ts, "open"]))
                close_price = Decimal(str(ohlcv.loc[ts, "close"]))

                # 1) Thực thi quyết định đã đến hạn (fill delay), dùng giá OPEN của bar này.
                if pending and pending[0][0] == ts:
                    _, target_allocation = pending.popleft()
                    self._execute_rebalance(
                        symbol, ts, target_allocation, current_price, portfolio, trade_rows
                    )

                # 2) Suy luận regime + signal dùng dữ liệu tới HẾT bar này (đóng).
                features_so_far = features.iloc[is_start : i + 1]
                regime_state = self.hmm_engine.predict_regime_filtered(features_so_far)
                is_flickering = self.hmm_engine.is_flickering()

                bars_window = ohlcv.loc[:ts].tail(_STRATEGY_BARS_LOOKBACK)
                equity_now = portfolio.cash + portfolio.qty * close_price
                current_allocation = (
                    (portfolio.qty * close_price / equity_now) if equity_now > 0 else Decimal("0")
                )
                signal = self.strategy_orchestrator.generate_signal(
                    symbol, regime_state, regime_infos, bars_window, current_allocation, is_flickering
                )

                trend_gate_row = trend_gate_history.loc[ts]
                trend_gate_cap = trend_gate_row["cap"]
                final_allocation = compose_layer_allocations(signal.target_allocation_pct, trend_gate_cap)

                execute_at_idx = i + self.config.fill_delay_bars
                if execute_at_idx < len(features.index):
                    pending.append((features.index[execute_at_idx], final_allocation))

                # 3) Ghi lại — mark-to-market bằng giá ĐÓNG cửa của bar này.
                equity_close = portfolio.cash + portfolio.qty * close_price
                if portfolio.cash < 0:
                    raise RuntimeError(
                        f"cash âm ({portfolio.cash}) tại {ts} — có bug, xem CLAUDE.md §4.2."
                    )
                equity_rows.append(
                    {
                        "timestamp": ts,
                        "cash": portfolio.cash,
                        "qty": portfolio.qty,
                        "price": close_price,
                        "equity": equity_close,
                        "allocation_pct": (
                            (portfolio.qty * close_price / equity_close) if equity_close > 0 else Decimal("0")
                        ),
                    }
                )
                regime_rows.append(
                    {
                        "timestamp": ts,
                        "regime_id": regime_state.state_id,
                        "regime_label": regime_state.label,
                        "regime_probability": regime_state.probability,
                        "is_confirmed": regime_state.is_confirmed,
                        "consecutive_bars": regime_state.consecutive_bars,
                        "is_flickering": is_flickering,
                        "trend_gate_raw_state": trend_gate_row["raw_state"],
                        "trend_gate_confirmed_state": trend_gate_row["confirmed_state"],
                        "trend_gate_cap": trend_gate_cap,
                        "strategy_target_allocation_pct": signal.target_allocation_pct,
                        "final_allocation_pct": final_allocation,
                    }
                )

        equity_curve = pd.DataFrame(equity_rows).set_index("timestamp")
        trade_log = pd.DataFrame(
            trade_rows,
            columns=["timestamp", "delta_qty", "price", "fee", "slippage_cost", "notional"],
        )
        if not trade_log.empty:
            trade_log = trade_log.set_index("timestamp")
        regime_history = pd.DataFrame(regime_rows).set_index("timestamp")

        gross_profit = equity_curve["equity"].iloc[-1] - self.config.initial_equity
        cost_report = self.cost_model.total_cost_report(trade_log, gross_profit=gross_profit)

        model_selection = pd.DataFrame(model_selection_rows)
        if not model_selection.empty:
            model_selection = model_selection.set_index("window_idx")

        return BacktestResult(
            equity_curve=equity_curve,
            trade_log=trade_log,
            regime_history=regime_history,
            cost_report=cost_report,
            model_selection=model_selection,
            metadata={
                "symbol": symbol,
                "start": start,
                "end": end,
                "n_windows": len(windows),
                "config": self.config,
            },
        )

    def _plan_windows(
        self, features: pd.DataFrame, start: datetime, end: datetime
    ) -> list[tuple[int, int, int, int]]:
        """Trả về danh sách `(is_start, is_end, oos_start, oos_new_end)` theo
        VỊ TRÍ (không phải ngày) trong `features`. `oos_new_end` chặn ở
        `min(oos_bars, step_bars)` bar đầu của OOS — tránh đếm lặp phần OOS
        bị window sau ghi đè khi `step_bars < oos_bars`.
        """
        n = len(features)
        cfg = self.config
        new_bars_per_window = min(cfg.oos_bars, cfg.step_bars)

        index = cast(pd.DatetimeIndex, features.index)
        start_ts = self._normalize_timestamp(start, index)
        end_ts = self._normalize_timestamp(end, index)

        start_pos = int(index.searchsorted(start_ts))
        oos_start = max(start_pos, cfg.is_bars)

        windows: list[tuple[int, int, int, int]] = []
        while oos_start < n:
            is_start = oos_start - cfg.is_bars
            is_end = oos_start
            if index[oos_start] > end_ts:
                break
            oos_new_end = min(oos_start + new_bars_per_window, n)
            # Không vượt quá `end` yêu cầu.
            while oos_new_end > oos_start and index[oos_new_end - 1] > end_ts:
                oos_new_end -= 1
            if oos_new_end > oos_start:
                windows.append((is_start, is_end, oos_start, oos_new_end))
            oos_start += cfg.step_bars

        return windows

    @staticmethod
    def _normalize_timestamp(dt: datetime, index: pd.DatetimeIndex) -> pd.Timestamp:
        """Khớp tz-awareness của `dt` với `index` — người gọi có thể truyền
        datetime naive hoặc aware, không nên crash vì khác quy ước."""
        ts = pd.Timestamp(dt)
        if index.tz is not None and ts.tzinfo is None:
            return ts.tz_localize(index.tz)
        if index.tz is None and ts.tzinfo is not None:
            return ts.tz_localize(None)
        return ts

    def _execute_rebalance(
        self,
        symbol: str,
        ts: pd.Timestamp,
        target_allocation: Decimal,
        current_price: Decimal,
        portfolio: _PortfolioState,
        trade_rows: list[dict],
    ) -> None:
        """Toán allocation đúng §4.2 — Decimal + ROUND_DOWN xuyên suốt, không
        bao giờ int(). `target_qty` làm tròn qua `InstrumentRules.round_qty`
        (floor theo bội số base_precision, không phải `.quantize` — xem
        ghi chú trong broker/instrument_rules.py về vì sao hai cái khác nhau).
        """
        equity = portfolio.cash + portfolio.qty * current_price
        target_qty = self._compute_target_qty(equity, target_allocation, current_price)
        delta = target_qty - portfolio.qty

        if delta == 0:
            return
        notional = abs(delta) * current_price
        if notional < self.config.instrument_rules.min_order_amt:
            return

        cost = self.cost_model.rebalance_cost(delta, current_price)
        portfolio.cash -= delta * current_price + cost
        portfolio.qty += delta

        trade_rows.append(
            {
                "timestamp": ts,
                "delta_qty": delta,
                "price": current_price,
                "fee": self.cost_model.fee_cost(delta, current_price),
                "slippage_cost": self.cost_model.slippage_cost(delta, current_price),
                "notional": notional,
            }
        )

    def _record_model_selection(
        self,
        window_idx: int,
        is_start_ts: pd.Timestamp,
        is_end_ts: pd.Timestamp,
        n_is_bars: int,
    ) -> dict:
        """Ghi lại BIC đã chọn n_components nào ở window này, và với biên bao nhiêu.

        Lý do tồn tại: nếu cực tiểu BIC nông — tức `bic_margin` giữa ứng viên
        thắng và á quân nhỏ — thì một nhiễu loạn nhỏ trong dữ liệu IS đủ để lật
        lựa chọn sang n_components khác, kéo theo định nghĩa regime khác và
        allocation khác. Đó là đường truyền khả dĩ từ "dịch start date 7 ngày"
        tới "total return đổi một bậc độ lớn". Không có cột này thì tính bất ổn
        của model selection là vô hình: `regime_history` chỉ ghi state đã chọn,
        không ghi việc lựa chọn đó suýt soát tới mức nào.

        `samples_per_param` là chỉ số hỗ trợ: `covariance_type="full"` làm số
        tham số tăng bậc hai theo số feature (CLAUDE.md bất biến #13), nên một
        model thắng BIC vẫn có thể được ước lượng quá mỏng để ổn định.
        """
        results = sorted(self.hmm_engine.bic_results, key=lambda r: r.bic)
        best = results[0]
        runner_up = results[1] if len(results) > 1 else None

        return {
            "window_idx": window_idx,
            "is_start": is_start_ts,
            "is_end": is_end_ts,
            "n_is_bars": n_is_bars,
            "selected_n_components": best.n_components,
            "selected_bic": best.bic,
            "selected_n_params": best.n_params,
            "converged": best.converged,
            "n_iter": best.n_iter,
            "runner_up_n_components": runner_up.n_components if runner_up else None,
            "runner_up_bic": runner_up.bic if runner_up else None,
            # Biên càng nhỏ, lựa chọn càng dễ lật khi dữ liệu IS xê dịch.
            "bic_margin": (runner_up.bic - best.bic) if runner_up else None,
            "samples_per_param": n_is_bars / best.n_params if best.n_params else None,
            "bic_curve": {r.n_components: round(r.bic, 2) for r in results},
        }

    def _compute_target_qty(
        self,
        equity: Decimal,
        target_allocation: Decimal,
        current_price: Decimal,
    ) -> Decimal:
        """Làm tròn XUỐNG theo base_precision — không bao giờ int()."""
        target_notional = equity * target_allocation
        target_qty_raw = target_notional / current_price
        return self.config.instrument_rules.round_qty(target_qty_raw)
