"""Shadow mode — chạy đủ pipeline, KHÔNG có đường nào tới tầng đặt lệnh.

Phase 12c §B.

## Shadow mode KHÔNG dùng để kiểm logic

`ops/compare_versions.py` (§A) làm việc đó tốt hơn nhiều: hàng nghìn bar
thay vì vài chục, tái lập được, mất vài chục giây. Chạy §A TRƯỚC; shadow
chỉ có nghĩa sau khi §A đã xanh.

Shadow trả lời đúng những câu backtest **không** trả lời được:

- phản hồi API thật (mã lỗi, giới hạn tần suất, trường thiếu)
- `instrumentRules` THẬT của sàn — làm tròn qty/price theo `basePrecision`
  và `tickSize` mà backtest chỉ giả định
- lệch đồng hồ thật
- hành vi khi mạng chập chờn

## CHẶN Ở TẦNG KIẾN TRÚC, KHÔNG BẰNG CỜ BOOLEAN

File này **không import** `broker.order_executor`. Đó là toàn bộ cơ chế
an toàn, và nó là loại cơ chế đúng: một cờ `dry_run=True` có thể bị lật
nhầm — bởi một biến môi trường, một dòng config, một lần copy-paste — còn
một import không tồn tại thì không có cách nào bị lật.

Cùng cách `forward/logger.py` đã làm. `tests/test_shadow.py` ghim điều này
bằng cách đọc MÃ NGUỒN file này, vì "không có đường tới order_executor" là
thứ không quan sát được từ hành vi: một shadow runner đúng và một shadow
runner chưa từng gặp tình huống đặt lệnh trông y hệt nhau.

## Ghi CÙNG định dạng trace với `main.py`

`monitoring/trace.py::log_layer` — sáu dòng một bar, `trace_id` tất định.
Nhờ đó `ops/shadow_diff.py` dùng **một** parser cho cả hai nguồn thay vì
một parser riêng cho từng bên. Đó là lý do §C.4 đòi đồng bộ định dạng.

`forward/logger.py` KHÔNG phát trace (file đóng băng, CLAUDE.md #15) —
nhưng `trace_id` tất định nên vẫn so chéo được theo bar khi cần.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

SHADOW_DIR = _REPO_ROOT / "ops" / "shadow"


def shadow_log_path(day: Optional[date] = None, *, base: Optional[Path] = None) -> Path:
    """`ops/shadow/YYYY-MM-DD.jsonl`. Một file một ngày — cùng quy ước với
    `logs/digest/`, và nó làm `shadow_diff` cắt khoảng thời gian bằng tên
    file thay vì phải parse cả file."""
    d = day or datetime.now(timezone.utc).date()
    return (base or SHADOW_DIR) / f"{d.isoformat()}.jsonl"


class _ShadowLogger:
    """Logger tối thiểu ghi JSONL, API tương thích `logging.Logger.info`
    ở đúng phần mà `monitoring.trace.log_layer` dùng.

    Không dùng `monitoring/logger.py::get_logger` vì file này phải nằm ở
    `ops/shadow/`, tách hẳn khỏi `logs/` của instance production — trộn
    hai nguồn vào một thư mục là cách chắc chắn để `shadow_diff` so nhầm
    một file với chính nó.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.filters: list[Any] = []

    def addFilter(self, f: Any) -> None:  # noqa: N802 — khớp API logging
        self.filters.append(f)

    def info(self, msg: str, *, extra: Optional[dict[str, Any]] = None) -> None:
        ban_ghi = {"message": msg, **(extra or {})}
        # `TraceFilter` chèn `trace` vào `record.trace`; ở đây ta gọi tay
        # vì không đi qua `logging.LogRecord`.
        from monitoring.trace import current_trace

        ban_ghi.setdefault("trace", current_trace())
        ban_ghi.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ban_ghi, ensure_ascii=False, default=str) + "\n")


@dataclass(frozen=True)
class ShadowBar:
    """Kết quả một bar shadow. Trả về để test khẳng định được mà không
    phải đọc lại file vừa ghi."""

    trace_id: str
    bar_ts: str
    regime_id: int
    hmm_allocation: Decimal
    trend_gate_cap: Decimal
    risk_manager_cap: Decimal
    final_allocation: Decimal
    capped_by: str


def run_shadow_bar(
    *,
    symbol: str,
    signal_generator: Any,
    exchange_client: Any,
    features: Any,
    bars: Any,
    bar_ts: Any,
    current_allocation: Decimal,
    log: _ShadowLogger,
    instrument_rules: Any = None,
    clock_skew_ms: Optional[float] = None,
    api_latency_ms: Optional[float] = None,
) -> ShadowBar:
    """Một bar: feature -> HMM -> strategy -> trend gate -> risk manager.

    KHÔNG có bước thứ bảy. Đường dây dừng ở `risk_manager` và kết quả chỉ
    đi vào log.
    """
    import main as main_mod
    from monitoring import trace as t

    trace_id = t.set_bar_trace(bar_ts.to_pydatetime(), symbol)

    close_price = Decimal(str(bars.loc[bar_ts, "close"]))
    portfolio_state, _balance, _positions, _equity = main_mod._build_portfolio_state(
        exchange_client=exchange_client,
        signal_generator=signal_generator,
        symbol=symbol,
        close_price=close_price,
    )

    features_so_far = features.loc[:bar_ts]
    bars_window = bars.loc[:bar_ts]
    result = signal_generator.generate(
        symbol, features_so_far, bars_window, current_allocation, portfolio_state
    )
    trend_state = signal_generator.trend_gate.get_structure_state(bars_window).value
    capped = t.capped_by(result.hmm_allocation, result.trend_gate_cap, result.risk_manager_cap)

    t.log_layer(log, t.LAYER_FEATURES, n_features=len(features_so_far.columns))
    t.log_layer(
        log,
        t.LAYER_HMM,
        regime=result.regime_state.label,
        regime_id=result.regime_state.state_id,
        conf=round(float(result.regime_state.probability), 4),
        is_flickering=result.is_flickering,
        alloc_out=str(result.hmm_allocation),
    )
    t.log_layer(log, t.LAYER_TREND_GATE, state=trend_state, cap=str(result.trend_gate_cap))
    t.log_layer(
        log,
        t.LAYER_RISK,
        cap=str(result.risk_manager_cap),
        breaker=main_mod.read_breaker_level(signal_generator.risk_manager),
    )
    t.log_layer(
        log,
        t.LAYER_COMPOSE,
        final=str(result.final_allocation),
        capped_by=capped,
        # `instrument_rules` THẬT của sàn — thứ backtest chỉ giả định, và
        # là một trong bốn câu hỏi duy nhất shadow mode tồn tại để trả lời.
        instrument_rules=_rules_as_dict(instrument_rules),
        clock_skew_ms=clock_skew_ms,
        api_latency_ms=api_latency_ms,
    )
    # KHÔNG có tầng `rebalance` ở shadow: rebalance là quyết định ĐẶT LỆNH,
    # và shadow không có tầng đó. Ghi một dòng "skipped" ở đây sẽ vẽ ra một
    # bước chưa từng chạy.
    return ShadowBar(
        trace_id=trace_id,
        bar_ts=str(bar_ts),
        regime_id=result.regime_state.state_id,
        hmm_allocation=result.hmm_allocation,
        trend_gate_cap=result.trend_gate_cap,
        risk_manager_cap=result.risk_manager_cap,
        final_allocation=result.final_allocation,
        capped_by=capped,
    )


def _rules_as_dict(rules: Any) -> Optional[dict[str, str]]:
    """`InstrumentRules` -> dict CHUỖI. `Decimal` phải qua `str`, không
    qua `float`: `basePrecision` là thứ quyết định làm tròn qty, và ép
    float ở đây làm mất đúng chữ số cuối mà nó tồn tại để giữ."""
    if rules is None:
        return None
    return {
        ten: str(getattr(rules, ten))
        for ten in (
            "symbol",
            "base_precision",
            "quote_precision",
            "tick_size",
            "min_order_qty",
            "min_order_amt",
            "max_order_qty",
        )
        if hasattr(rules, ten)
    }


def run_shadow_loop(
    args: argparse.Namespace,
    settings: dict[str, Any],
    *,
    max_iterations: Optional[int] = None,
    log_base: Optional[Path] = None,
) -> list[ShadowBar]:
    """Vòng lặp poll giống `main.py::run_live_loop` nhưng KHÔNG đặt lệnh.

    `max_iterations` chỉ để test (cùng lý do đã ghi ở `run_live_loop`).
    """
    import pandas as pd

    import main as main_mod
    from data.feature_engineering import compute_all_features
    from data.history_loader import HistoryLoader

    symbol = settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
    poll_interval = int(settings["execution"]["poll_interval_seconds"])

    exchange_client = main_mod.build_exchange_client(settings, testnet=not args.live)
    instrument_rules = exchange_client.get_instrument_rules(symbol)
    hmm_engine = main_mod.build_hmm_engine(settings)
    signal_generator = main_mod.build_signal_generator(settings, hmm_engine)
    feature_config = main_mod.build_feature_config(settings)
    history_loader = HistoryLoader()
    # Cần `zscore_lookback` bar warmup + một window IS trước mốc đánh giá
    # — cùng phép tính `run_live_loop` dùng, gọi lại thay vì chép.
    wf = main_mod.build_walk_forward_config(settings)
    data_start = main_mod.resolve_data_start(
        args, datetime.now(timezone.utc), settings, wf.is_bars
    )

    log = _ShadowLogger(shadow_log_path(base=log_base))
    logger.info("SHADOW MODE — không có đường nào tới tầng đặt lệnh. Ghi %s", log.path)

    da_xu_ly: list[ShadowBar] = []
    da_thay: set[Any] = set()
    vong = 0
    while max_iterations is None or vong < max_iterations:
        vong += 1
        now = datetime.now(timezone.utc)
        latest_bar = main_mod._latest_closed_bar_date(now)

        bat_dau = time.monotonic()
        ohlcv = history_loader.load(ccxt_symbol, "1D", data_start, latest_bar.to_pydatetime())
        api_latency_ms = (time.monotonic() - bat_dau) * 1000

        features = compute_all_features(ohlcv, feature_config)
        if latest_bar not in features.index or latest_bar in da_thay:
            time.sleep(poll_interval)
            continue
        da_thay.add(latest_bar)

        current_allocation = (
            da_xu_ly[-1].final_allocation if da_xu_ly else Decimal("0")
        )
        da_xu_ly.append(
            run_shadow_bar(
                symbol=symbol,
                signal_generator=signal_generator,
                exchange_client=exchange_client,
                features=features,
                bars=ohlcv,
                bar_ts=pd.Timestamp(latest_bar),
                current_allocation=current_allocation,
                log=log,
                instrument_rules=instrument_rules,
                api_latency_ms=api_latency_ms,
            )
        )
        time.sleep(poll_interval)
    return da_xu_ly


def main(argv: Optional[Sequence[str]] = None) -> int:
    import main as main_mod

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--live", action="store_true", help="mainnet (mặc định testnet)")
    parser.add_argument("--data-start", default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = main_mod.load_settings(args.config)
    run_shadow_loop(args, settings, max_iterations=args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
