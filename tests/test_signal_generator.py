"""tests.test_signal_generator — SignalGenerator: HMM -> strategy -> trend
gate -> risk manager, đúng thứ tự, chỉ giảm không bao giờ tăng.

Module này (`core/signal_generator.py::SignalGenerator`) trước phiên này
KHÔNG có caller lẫn test nào trong repo — chỉ hàm thuần
`compose_layer_allocations` (dùng lại bởi `forward/logger.py`) từng được
test. Viết test này cùng lúc đổi API `generate()` trả về
`SignalGeneratorResult` (thêm `regime_state`/`is_flickering`, cần cho
main.py Phase 10 ghi state_snapshot.json/log) — không phải re-test toán
học của HMM/strategy/trend_gate (đã có test riêng ở các module đó), mà
test ĐÚNG THỨ TỰ GHÉP các tầng qua class này.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.risk_manager import PortfolioState, RiskManager
from core.signal_generator import SignalGenerator, SignalGeneratorResult
from core.trend_gate import StructuralTrendGate, TrendGateConfig

_SYMBOL = "BTCUSDT"


def _synthetic_features(n: int = 250) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    index = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"log_return_1": rng.normal(0.0, 0.01, size=n)}, index=index)


def _declining_bars(n: int = 60) -> pd.DataFrame:
    """Giá giảm ĐỀU (không nhiễu) — trend gate phải xác nhận BEAR_STRUCTURE
    một cách chắc chắn, không phụ thuộc may rủi ngẫu nhiên."""
    index = pd.date_range("2024-06-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(np.linspace(100.0, 50.0, n), index=index)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1.0,
        },
        index=index,
    )


def _fitted_engine() -> HMMRegimeEngine:
    """`_REGIME_LABELS` (core/hmm_engine.py) chỉ định nghĩa cho n_components
    3–7 nên n_candidates=[1] không hợp lệ — dùng [3]. State THẬT nào thắng
    sau khi fit trên nhiễu i.i.d. không đoán trước được (phụ thuộc EM), nên
    test dựa vào tính chất này KHÔNG giả định state cụ thể — xem
    `cap_bear_structure` trong `_generator()`, đặt thấp hơn CẢ BA mức
    allocation mà bất kỳ chiến lược nào có thể đề xuất (0.50/0.60/0.95),
    để trend_gate luôn là ràng buộc quyết định bất kể HMM chọn state nào."""
    engine = HMMRegimeEngine(
        n_candidates=[3],
        n_init=1,
        covariance_type="diag",
        min_train_bars=200,
        stability_bars=1,
        flicker_window=20,
        flicker_threshold=4,
    )
    engine.select_and_train(_synthetic_features())
    return engine


def _risk_manager(tmp_path: Path, **overrides: object) -> RiskManager:
    config: dict[str, object] = {
        "max_position_pct": 100,
        "max_risk_per_trade_pct": 1.0,
        "min_order_value_usdt": 5,
        "min_cash_buffer_pct": 0.0,
        "max_trades_per_day": 100,
        "max_leverage": 1.0,
        "spread_max_pct": 0.10,
        "usdt_depeg_threshold_pct": 0.5,
        "duplicate_order_window_seconds": 0,
        "circuit_breaker": {
            "daily_dd_reduce_pct": 50.0,
            "daily_dd_halt_pct": 90.0,
            "weekly_dd_reduce_pct": 50.0,
            "weekly_dd_halt_pct": 90.0,
            "peak_dd_halt_pct": 90.0,
        },
    }
    config.update(overrides)
    return RiskManager(config, halt_lock_path=tmp_path / "trading_halted.lock")


def _portfolio_state(equity: Decimal = Decimal("10000")) -> PortfolioState:
    return PortfolioState(
        equity=equity,
        cash=equity,
        available_balance=equity,
        positions={},
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        peak_equity=equity,
        drawdown=Decimal("0"),
        circuit_breaker_status={},
        flicker_rate=0.0,
    )


def _generator(tmp_path: Path, *, cap_bear_structure: Decimal = Decimal("0.30")) -> SignalGenerator:
    trend_gate = StructuralTrendGate(
        TrendGateConfig(
            sma_period=10,
            slope_lookback=5,
            buffer_pct=Decimal("2.0"),
            confirm_bars=3,
            cap_bull_structure=Decimal("1.00"),
            cap_transition=Decimal("0.60"),
            cap_bear_structure=cap_bear_structure,
        )
    )
    orchestrator = StrategyOrchestrator(min_confidence=0.0, rebalance_threshold_pct=Decimal("0"))
    return SignalGenerator(_fitted_engine(), trend_gate, orchestrator, _risk_manager(tmp_path))


# ----------------------------------------------------------------------
# Thứ tự ghép tầng: HMM/strategy -> trend_gate cap -> risk_manager
# ----------------------------------------------------------------------


def test_trend_gate_caps_allocation_below_strategy_target(tmp_path: Path) -> None:
    """Bất kể HMM chọn state nào, strategy tương ứng đề xuất >= 0.50 (mức
    thấp nhất có thể, HighVolDefensiveStrategy); giá đang giảm đều -> trend
    gate xác nhận BEAR_STRUCTURE, trần 0.30 < 0.50. Kết quả cuối PHẢI bị
    giới hạn ở 0.30 (min), không phải mức strategy đề xuất — nếu
    SignalGenerator quên áp trend_gate cap, test này bắt được ngay."""
    generator = _generator(tmp_path)
    result = generator.generate(
        _SYMBOL, _synthetic_features(), _declining_bars(), Decimal("0"), _portfolio_state()
    )

    assert isinstance(result, SignalGeneratorResult)
    assert result.decision.approved is True
    signal = result.decision.modified_signal
    assert signal is not None
    assert signal.target_allocation_pct == Decimal("0.30")


def test_regime_state_and_flickering_exposed_on_result(tmp_path: Path) -> None:
    """Trước bản sửa, generate() chỉ trả RiskDecision — regime_state/
    is_flickering tính xong rồi vứt, main.py (Phase 10) không đọc lại
    được để ghi state_snapshot.json/log."""
    generator = _generator(tmp_path)
    result = generator.generate(
        _SYMBOL, _synthetic_features(), _declining_bars(), Decimal("0"), _portfolio_state()
    )

    assert 0 <= result.regime_state.state_id < 3
    assert isinstance(result.is_flickering, bool)


def test_risk_manager_veto_overrides_everything_above(tmp_path: Path) -> None:
    """halted.lock tồn tại -> risk_manager từ chối TUYỆT ĐỐI, bất kể HMM/
    strategy/trend_gate đề xuất gì — quyền phủ quyết tối thượng (CLAUDE.md
    bất biến #4)."""
    (tmp_path / "trading_halted.lock").write_text("PEAK_HALT test", encoding="utf-8")
    generator = _generator(tmp_path)

    result = generator.generate(
        _SYMBOL, _synthetic_features(), _declining_bars(), Decimal("0"), _portfolio_state()
    )

    assert result.decision.approved is False
    assert result.decision.modified_signal is None
    assert "trading_halted.lock" in (result.decision.rejection_reason or "")


def test_risk_manager_never_raises_allocation_above_trend_gate_cap(tmp_path: Path) -> None:
    """Đối chứng cho CLAUDE.md bất biến #2: risk_manager (max_position_pct
    100%, cash_buffer 0%, không circuit breaker active) không được phép
    NÂNG allocation đã bị trend_gate giảm xuống 0.30 lên lại — kết quả
    cuối cùng vẫn phải <= 0.30."""
    generator = _generator(tmp_path)
    result = generator.generate(
        _SYMBOL, _synthetic_features(), _declining_bars(), Decimal("0"), _portfolio_state()
    )

    assert result.decision.modified_signal is not None
    assert result.decision.modified_signal.target_allocation_pct <= Decimal("0.30")
