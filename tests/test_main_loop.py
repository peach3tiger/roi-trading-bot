"""tests.test_main_loop — Phase 10: process_one_bar(), state_snapshot.json,
_latest_closed_bar_date().

Trọng tâm: `--dry-run` PHẢI không đặt lệnh nào (nghiệm thu #1 của
prompts/phase-10-main-loop.md); breach stop loss PHẢI đóng vị thế thay vì
sinh signal mới; signal bị risk_manager từ chối PHẢI giữ nguyên allocation/
stop cũ, không âm thầm đổi. Không cần mạng/testnet thật — dùng
`SignalGenerator` THẬT (đã có test riêng ở tests/test_signal_generator.py
cho phần ghép tầng) nhưng fake `order_executor`/`position_tracker` (biên
duy nhất chạm ra sàn thật) để đo được chính xác lệnh gì ĐÃ/CHƯA được gọi.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.risk_manager import RiskManager
from core.signal_generator import SignalGenerator
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from main import (
    LiveLoopState,
    _check_spread_and_alert,
    _fire_bar_alerts,
    _latest_closed_bar_date,
    compute_bars_behind,
    load_state_snapshot,
    process_one_bar,
    write_state_snapshot,
)
from monitoring.alerts import Alert, AlertManager, AlertType
from monitoring.logger import get_logger

_SYMBOL = "BTCUSDT"


# ----------------------------------------------------------------------
# Fakes cho biên chạm sàn thật — chỉ ở order_executor/position_tracker,
# SignalGenerator (HMM/strategy/trend_gate/risk_manager) dùng bản THẬT.
# ----------------------------------------------------------------------


class _FakeBalance:
    def __init__(self, total: Decimal, available: Decimal) -> None:
        self.asset = "USDT"
        self.total = total
        self.available = available


class _FakePosition:
    def __init__(self, symbol: str, qty: Decimal) -> None:
        self.symbol = symbol
        self.qty = qty


class _FakeExchangeClient:
    def __init__(self) -> None:
        self.balance = _FakeBalance(Decimal("10000"), Decimal("10000"))
        self.positions: list[_FakePosition] = []

    def get_balance(self) -> _FakeBalance:
        return self.balance

    def get_positions(self) -> list[_FakePosition]:
        return list(self.positions)


class _FakeOrderResult:
    def __init__(self, fee_cost: str | None = None) -> None:
        self.order_id = "1"
        self.order_link_id = "abc"
        self.status = "FILLED"
        self.filled_qty = Decimal("0.1")
        # `raw_response` khớp cấu trúc ccxt thật (broker/ccxt_client.py::
        # submit_order truyền thẳng response thô của sàn) — fee_cost=None
        # mô phỏng lệnh chưa có thông tin phí (xem _extract_fee_paid()).
        self.raw_response: dict = {"fee": {"cost": fee_cost, "currency": "USDT"}} if fee_cost else {}


class _FakeOrderExecutor:
    def __init__(self, fee_cost: str | None = None, exchange_client: Any = None) -> None:
        self.exchange_client = exchange_client if exchange_client is not None else _FakeExchangeClient()
        self.submit_order_calls: list[Any] = []
        self.modify_stop_calls: list[tuple[str, Decimal]] = []
        self.close_position_calls: list[str] = []
        self._fee_cost = fee_cost

    def submit_order(self, signal: Any) -> _FakeOrderResult:
        self.submit_order_calls.append(signal)
        return _FakeOrderResult(self._fee_cost)

    def modify_stop(self, symbol: str, new_stop: Decimal) -> bool:
        self.modify_stop_calls.append((symbol, new_stop))
        return True

    def close_position(self, symbol: str) -> _FakeOrderResult:
        self.close_position_calls.append(symbol)
        return _FakeOrderResult(self._fee_cost)


class _FakePositionTracker:
    def __init__(self) -> None:
        self.poll_calls = 0

    def poll(self) -> None:
        self.poll_calls += 1


# ----------------------------------------------------------------------
# SignalGenerator thật, dữ liệu tổng hợp — cùng kỹ thuật
# tests/test_signal_generator.py (declining bars -> BEAR_STRUCTURE, cap
# thấp hơn mọi mức strategy có thể đề xuất, không phụ thuộc state HMM nào
# thắng).
# ----------------------------------------------------------------------


def _synthetic_features(n: int = 250) -> pd.DataFrame:
    rng = np.random.default_rng(2)
    index = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"log_return_1": rng.normal(0.0, 0.01, size=n)}, index=index)


def _bars(n: int = 60, *, declining: bool) -> pd.DataFrame:
    index = pd.date_range("2024-06-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(
        np.linspace(100.0, 50.0, n) if declining else np.full(n, 100.0), index=index
    )
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999, "close": close, "volume": 1.0},
        index=index,
    )


def _fitted_engine() -> HMMRegimeEngine:
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


def _risk_manager_config() -> dict[str, object]:
    return {
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


def _risk_manager(tmp_path: Path) -> RiskManager:
    return RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "trading_halted.lock")


def _signal_generator(tmp_path: Path) -> SignalGenerator:
    trend_gate = StructuralTrendGate(
        TrendGateConfig(
            sma_period=10,
            slope_lookback=5,
            buffer_pct=Decimal("2.0"),
            confirm_bars=3,
            cap_bull_structure=Decimal("1.00"),
            cap_transition=Decimal("0.60"),
            cap_bear_structure=Decimal("0.30"),
        )
    )
    orchestrator = StrategyOrchestrator(min_confidence=0.0, rebalance_threshold_pct=Decimal("0"))
    return SignalGenerator(_fitted_engine(), trend_gate, orchestrator, _risk_manager(tmp_path))


def _fresh_state() -> LiveLoopState:
    now_iso = datetime.now(timezone.utc).isoformat()
    return LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=None,
        current_allocation_pct="0",
        current_regime_id=None,
        current_regime_label=None,
        session_started_at_utc=now_iso,
        written_at_utc=now_iso,
    )


# ----------------------------------------------------------------------
# --dry-run KHÔNG đặt lệnh — nghiệm thu #1 của phase-10-main-loop.md
# ----------------------------------------------------------------------


def test_dry_run_never_calls_submit_order_or_modify_stop_or_poll(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    features = _synthetic_features()
    bar_ts = ohlcv.index[-1]

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=features,
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
    )

    assert order_executor.submit_order_calls == []
    assert order_executor.modify_stop_calls == []
    assert position_tracker.poll_calls == 0


def test_dry_run_still_updates_returned_state_for_snapshot_testing(tmp_path: Path) -> None:
    """--dry-run không đặt lệnh thật, nhưng VẪN phải cập nhật state trả về
    — nghiệm thu #3 (kill + restart -> khôi phục đúng) chạy được ở chế độ
    dry-run, không cần lệnh thật nào đã xảy ra."""
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
    )

    assert new_state.last_processed_bar == bar_ts.date().isoformat()
    assert new_state.current_allocation_pct == "0.30"  # trend_gate cap, xem test_signal_generator.py
    assert new_state.current_stop_loss is not None


# ----------------------------------------------------------------------
# Live mode — đặt lệnh thật (qua fake), cập nhật stop, đối soát
# ----------------------------------------------------------------------


def test_live_mode_submits_order_modifies_stop_and_polls(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=False,
    )

    assert len(order_executor.submit_order_calls) == 1
    assert len(order_executor.modify_stop_calls) == 1
    assert position_tracker.poll_calls == 1


# ----------------------------------------------------------------------
# Stop loss breach — đóng vị thế, KHÔNG sinh signal mới bar này
# ----------------------------------------------------------------------


def test_stop_loss_breach_closes_position_instead_of_new_signal(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]
    close_price = Decimal(str(ohlcv.loc[bar_ts, "close"]))

    state = _fresh_state()
    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=str(close_price + Decimal("1")),  # stop CAO hơn giá đóng -> breach
        current_allocation_pct="0.5",
        current_regime_id=1,
        current_regime_label="BULL",
        session_started_at_utc=state.session_started_at_utc,
        written_at_utc=state.written_at_utc,
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
    )

    assert order_executor.close_position_calls == [_SYMBOL]
    assert order_executor.submit_order_calls == []  # KHÔNG sinh signal mới bar này
    assert new_state.current_stop_loss is None
    assert new_state.current_allocation_pct == "0"


def test_stop_loss_breach_in_dry_run_does_not_close_position_for_real(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]
    close_price = Decimal(str(ohlcv.loc[bar_ts, "close"]))
    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=str(close_price + Decimal("1")),
        current_allocation_pct="0.5",
        current_regime_id=None,
        current_regime_label=None,
        session_started_at_utc="x",
        written_at_utc="x",
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=True,
    )

    assert order_executor.close_position_calls == []
    assert new_state.current_stop_loss is None  # state vẫn cập nhật để test được khôi phục


# ----------------------------------------------------------------------
# Signal bị risk_manager từ chối — giữ nguyên allocation/stop cũ
# ----------------------------------------------------------------------


def test_rejected_signal_keeps_previous_allocation_and_stop(tmp_path: Path) -> None:
    (tmp_path / "trading_halted.lock").write_text("PEAK_HALT test", encoding="utf-8")
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    position_tracker = _FakePositionTracker()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss="40",
        current_allocation_pct="0.42",
        current_regime_id=2,
        current_regime_label="BEAR",
        session_started_at_utc="x",
        written_at_utc="x",
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=position_tracker,
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
    )

    assert order_executor.submit_order_calls == []
    assert new_state.current_allocation_pct == "0.42"  # KHÔNG đổi
    assert new_state.current_stop_loss == "40"  # KHÔNG đổi


# ----------------------------------------------------------------------
# state_snapshot.json — round-trip, hỏng không raise
# ----------------------------------------------------------------------


def test_state_snapshot_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state_snapshot.json"
    state = LiveLoopState(
        last_processed_bar="2026-08-06",
        current_stop_loss="64000.5",
        current_allocation_pct="0.95",
        current_regime_id=2,
        current_regime_label="BULL",
        session_started_at_utc="2026-08-06T00:00:00+00:00",
        written_at_utc="2026-08-06T00:01:00+00:00",
    )

    write_state_snapshot(path, state)
    restored = load_state_snapshot(path)

    assert restored == state


def test_state_snapshot_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_state_snapshot(tmp_path / "does_not_exist.json") is None


def test_state_snapshot_corrupt_file_returns_none_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "state_snapshot.json"
    path.write_text("{ khong phai json hop le", encoding="utf-8")

    assert load_state_snapshot(path) is None


def test_state_snapshot_write_is_atomic_no_tmp_file_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "state_snapshot.json"
    write_state_snapshot(path, _fresh_state())

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    json.loads(path.read_text(encoding="utf-8"))  # phải là JSON hợp lệ, đọc được ngay


# ----------------------------------------------------------------------
# _latest_closed_bar_date — cùng tính chất đã kiểm chứng ở
# forward/logger.py::latest_closed_bar_date (bản sao cố ý, xem docstring)
# ----------------------------------------------------------------------


def test_latest_closed_bar_date_excludes_in_progress_bar() -> None:
    now = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)
    assert _latest_closed_bar_date(now) == pd.Timestamp("2026-08-05", tz="UTC")


def test_latest_closed_bar_date_stable_regardless_of_hour() -> None:
    early = _latest_closed_bar_date(datetime(2026, 8, 6, 0, 1, tzinfo=timezone.utc))
    late = _latest_closed_bar_date(datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc))
    assert early == late == pd.Timestamp("2026-08-05", tz="UTC")


# ----------------------------------------------------------------------
# reset_daily/reset_weekly — mỗi bar là một ngày mới (timeframe 1D)
# ----------------------------------------------------------------------


def test_process_one_bar_calls_reset_daily_every_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _signal_generator(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(generator.risk_manager, "reset_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(generator.risk_manager, "reset_weekly", lambda: calls.append("weekly"))

    ohlcv = _bars(declining=True)
    # index >= 14 để đủ warmup trend_gate (sma_period+slope_lookback=15,
    # xem _signal_generator()) — 2024-06-21 là Thứ Sáu, không phải Thứ Hai.
    bar_ts = pd.Timestamp("2024-06-21", tz="UTC")
    assert bar_ts in ohlcv.index
    assert bar_ts.weekday() != 0

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=_FakeOrderExecutor(),
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
    )

    assert calls == ["daily"]  # KHÔNG weekly — không phải Thứ Hai


def test_process_one_bar_calls_reset_weekly_on_monday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _signal_generator(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(generator.risk_manager, "reset_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(generator.risk_manager, "reset_weekly", lambda: calls.append("weekly"))

    ohlcv = _bars(declining=True)
    # index >= 14 để đủ warmup trend_gate — 2024-06-17 là Thứ Hai.
    bar_ts = pd.Timestamp("2024-06-17", tz="UTC")
    assert bar_ts in ohlcv.index
    assert bar_ts.weekday() == 0

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=_FakeOrderExecutor(),
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
    )

    assert calls == ["daily", "weekly"]


# ----------------------------------------------------------------------
# cumulative_fees_paid — Phase 11 (monitoring), phí THẬT đọc từ
# OrderResult.raw_response, cộng dồn qua state, ngược tương thích với
# snapshot cũ chưa có field này.
# ----------------------------------------------------------------------


def test_live_submit_order_accumulates_real_fee(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor(fee_cost="1.25")
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=False,
    )

    assert new_state.cumulative_fees_paid == "1.25"


def test_fees_accumulate_across_bars_not_overwritten(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor(fee_cost="1.25")
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    state = replace(_fresh_state(), cumulative_fees_paid="10")
    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
    )

    assert new_state.cumulative_fees_paid == "11.25"


def test_dry_run_does_not_accumulate_fees(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor(fee_cost="1.25")
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
    )

    assert new_state.cumulative_fees_paid == "0"
    assert order_executor.submit_order_calls == []


def test_stop_loss_breach_close_position_also_accumulates_fee(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor(fee_cost="0.75")
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]
    close_price = Decimal(str(ohlcv.loc[bar_ts, "close"]))

    base = _fresh_state()
    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=str(close_price + Decimal("1")),  # breach
        current_allocation_pct="0.5",
        current_regime_id=1,
        current_regime_label="BULL",
        session_started_at_utc=base.session_started_at_utc,
        written_at_utc=base.written_at_utc,
        cumulative_fees_paid="5",
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
    )

    assert new_state.cumulative_fees_paid == "5.75"


def test_extract_fee_paid_returns_zero_when_no_fee_info() -> None:
    from main import _extract_fee_paid

    class _NoFeeResult:
        raw_response: dict = {}

    assert _extract_fee_paid(_NoFeeResult()) == Decimal("0")


def test_extract_fee_paid_sums_multiple_fills() -> None:
    from main import _extract_fee_paid

    class _MultiFeeResult:
        raw_response = {"fees": [{"cost": "0.5", "currency": "USDT"}, {"cost": "0.3", "currency": "USDT"}]}

    assert _extract_fee_paid(_MultiFeeResult()) == Decimal("0.8")


def test_extract_fee_paid_mutation_kill_ignores_fee_field() -> None:
    """Đột biến kiểm chứng (CLAUDE.md #16): nếu _extract_fee_paid() LUÔN
    trả 0 (bỏ qua hoàn toàn raw_response), test single-fee ở trên PHẢI đỏ.
    Mô phỏng trực tiếp hành vi hỏng, xác nhận assertion gốc không vô nghĩa."""

    def _broken_extract_fee_paid(order_result: Any) -> Decimal:
        return Decimal("0")

    class _FeeResult:
        raw_response = {"fee": {"cost": "1.25", "currency": "USDT"}}

    assert _broken_extract_fee_paid(_FeeResult()) != Decimal("1.25")


def test_load_state_snapshot_backward_compatible_missing_fee_field(tmp_path: Path) -> None:
    """Snapshot ghi TRƯỚC khi cumulative_fees_paid tồn tại (Phase 10) vẫn
    phải load được — field mới dùng default "0", KHÔNG bị coi là hỏng."""
    old_snapshot = {
        "last_processed_bar": "2026-08-06",
        "current_stop_loss": "50000",
        "current_allocation_pct": "0.5",
        "current_regime_id": 1,
        "current_regime_label": "BULL",
        "session_started_at_utc": "2026-08-06T00:00:00+00:00",
        "written_at_utc": "2026-08-06T00:00:00+00:00",
    }
    path = tmp_path / "state_snapshot.json"
    path.write_text(json.dumps(old_snapshot), encoding="utf-8")

    loaded = load_state_snapshot(path)
    assert loaded is not None
    assert loaded.cumulative_fees_paid == "0"
    assert loaded.current_regime_label == "BULL"


def test_write_then_load_state_snapshot_roundtrips_fee_field(tmp_path: Path) -> None:
    path = tmp_path / "state_snapshot.json"
    state = replace(_fresh_state(), cumulative_fees_paid="123.45")
    write_state_snapshot(path, state)

    loaded = load_state_snapshot(path)
    assert loaded is not None
    assert loaded.cumulative_fees_paid == "123.45"


# ----------------------------------------------------------------------
# Phase 11 — monitoring/alerts.py wiring: _fire_bar_alerts(), _check_spread_and_alert()
# ----------------------------------------------------------------------


class _SpyAlertManager(AlertManager):
    """Ghi lại mọi Alert đã "gửi" thay vì gọi mạng thật — kế thừa
    AlertManager thật (không fake toàn bộ) để rate-limit vẫn hoạt động
    đúng, chỉ chặn các kênh mạng (console/telegram/email/webhook đều tắt
    qua tham số __init__)."""

    def __init__(self) -> None:
        super().__init__(rate_limit_seconds=0, console_enabled=False)
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        sent = super().send(alert)
        if sent:
            self.sent.append(alert)
        return sent


class _FakeBreakerLevel:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeBreakerStatus:
    def __init__(self, level: str, daily_dd: str = "0", weekly_dd: str = "0", peak_dd: str = "0") -> None:
        self.level = _FakeBreakerLevel(level)
        self.daily_dd = Decimal(daily_dd)
        self.weekly_dd = Decimal(weekly_dd)
        self.peak_dd = Decimal(peak_dd)


class _FakeCircuitBreaker:
    def __init__(self, history: list[_FakeBreakerStatus]) -> None:
        self._history = history

    def get_history(self) -> list[_FakeBreakerStatus]:
        return self._history


class _FakeRiskManagerForAlerts:
    def __init__(self, history: list[_FakeBreakerStatus]) -> None:
        self.circuit_breaker = _FakeCircuitBreaker(history)


class _FakeSignalGeneratorForAlerts:
    def __init__(self, history: list[_FakeBreakerStatus]) -> None:
        self.risk_manager = _FakeRiskManagerForAlerts(history)


class _FakeResult:
    def __init__(self, is_flickering: bool) -> None:
        self.is_flickering = is_flickering


def test_fire_bar_alerts_regime_change() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=0, current_regime_label="BEAR")

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts([]),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert [a.alert_type for a in manager.sent] == [AlertType.REGIME_CHANGE]


def test_fire_bar_alerts_no_regime_change_no_alert() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=1, current_regime_label="BULL")

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts([]),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert manager.sent == []


def test_fire_bar_alerts_first_bar_no_regime_change_alert() -> None:
    """state.current_regime_id is None (phiên mới) -> KHÔNG coi là "đổi
    regime" (không có gì để so sánh), tránh cảnh báo giả ở bar đầu tiên."""
    manager = _SpyAlertManager()
    state = _fresh_state()
    assert state.current_regime_id is None

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts([]),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert manager.sent == []


def test_fire_bar_alerts_trend_gate_change() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=1, current_trend_structure="BEAR_STRUCTURE")

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts([]),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert [a.alert_type for a in manager.sent] == [AlertType.TREND_GATE_CHANGE]


def test_fire_bar_alerts_flicker() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=1)

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts([]),
        state=state,
        result=_FakeResult(is_flickering=True),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert [a.alert_type for a in manager.sent] == [AlertType.FLICKER_THRESHOLD_EXCEEDED]


def test_fire_bar_alerts_circuit_breaker_and_large_pnl() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=1)
    history = [_FakeBreakerStatus("DAILY_REDUCE", daily_dd="5.0")]

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts(history),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    types = [a.alert_type for a in manager.sent]
    assert AlertType.CIRCUIT_BREAKER in types
    assert AlertType.LARGE_PNL in types  # 5.0% >= ngưỡng 2.0%


def test_fire_bar_alerts_circuit_breaker_none_below_large_pnl_threshold_no_alert() -> None:
    manager = _SpyAlertManager()
    state = replace(_fresh_state(), current_regime_id=1)
    history = [_FakeBreakerStatus("NONE", daily_dd="0.5")]

    _fire_bar_alerts(
        alert_manager=manager,
        signal_generator=_FakeSignalGeneratorForAlerts(history),
        state=state,
        result=_FakeResult(is_flickering=False),
        regime_id=1,
        regime_label="BULL",
        new_trend_structure="BULL_STRUCTURE",
        large_pnl_alert_pct=Decimal("2.0"),
    )

    assert manager.sent == []


class _FakeOrderBook:
    def __init__(self, bid: str, ask: str) -> None:
        self.best_bid = Decimal(bid)
        self.best_ask = Decimal(ask)


class _FakeExchangeClientWithOrderbook:
    def __init__(self, orderbook: _FakeOrderBook | None = None, raises: bool = False) -> None:
        self._orderbook = orderbook
        self._raises = raises

    def get_orderbook(self, symbol: str) -> _FakeOrderBook:
        if self._raises:
            raise ConnectionError("simulated feed loss")
        assert self._orderbook is not None
        return self._orderbook


def test_check_spread_and_alert_normal_spread_no_alert(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    risk_manager = RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "halt.lock")
    exchange_client = _FakeExchangeClientWithOrderbook(_FakeOrderBook("50000", "50005"))

    _check_spread_and_alert(
        alert_manager=manager, risk_manager=risk_manager, exchange_client=exchange_client, symbol=_SYMBOL
    )

    assert manager.sent == []


def test_check_spread_and_alert_wide_spread_fires_abnormal_spread(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    risk_manager = RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "halt.lock")
    # spread_max_pct mặc định 0.10% — bid/ask cách nhau 5% chắc chắn vượt.
    exchange_client = _FakeExchangeClientWithOrderbook(_FakeOrderBook("50000", "52500"))

    _check_spread_and_alert(
        alert_manager=manager, risk_manager=risk_manager, exchange_client=exchange_client, symbol=_SYMBOL
    )

    assert [a.alert_type for a in manager.sent] == [AlertType.ABNORMAL_SPREAD]


def test_check_spread_and_alert_orderbook_fetch_fails_fires_data_feed_lost(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    risk_manager = RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "halt.lock")
    exchange_client = _FakeExchangeClientWithOrderbook(raises=True)

    _check_spread_and_alert(
        alert_manager=manager, risk_manager=risk_manager, exchange_client=exchange_client, symbol=_SYMBOL
    )

    assert [a.alert_type for a in manager.sent] == [AlertType.DATA_FEED_LOST]


# ----------------------------------------------------------------------
# process_one_bar() — wiring end-to-end với alert_manager/regime_state_logger thật
# ----------------------------------------------------------------------


def test_process_one_bar_writes_regime_state_log(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    regime_logger = get_logger("regime", str(tmp_path / "logs"))
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=_FakeOrderExecutor(fee_cost="0.42"),
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=False,
        regime_state_logger=regime_logger,
    )
    for h in regime_logger.handlers:
        h.flush()

    log_file = tmp_path / "logs" / "regime.log"
    assert log_file.exists()
    payload = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert payload["cumulative_fees_paid"] == "0.42"
    assert "regime" in payload
    assert "probability" in payload


def test_process_one_bar_alert_manager_receives_regime_change(tmp_path: Path) -> None:
    generator = _signal_generator(tmp_path)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]
    manager = _SpyAlertManager()

    # regime_id chắc chắn khác bất kỳ id thật nào HMM có thể trả (0..6) —
    # đảm bảo REGIME_CHANGE fire bất kể model thật gán regime nào.
    state = replace(_fresh_state(), current_regime_id=-1, current_regime_label="UNKNOWN")

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=_FakeOrderExecutor(),
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=True,
        alert_manager=manager,
    )

    assert AlertType.REGIME_CHANGE in [a.alert_type for a in manager.sent]


# ----------------------------------------------------------------------
# Phase 11 (bổ sung) — poll telemetry thay ws_connected/ws_last_message_
# seconds_ago: compute_bars_behind(), last_poll_at/poll_latency_ms trong
# LiveLoopState/state_snapshot.json.
# ----------------------------------------------------------------------


def test_compute_bars_behind_none_last_processed_is_zero() -> None:
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert compute_bars_behind(None, now) == 0


def test_compute_bars_behind_zero_when_caught_up() -> None:
    # Bar đã đóng mới nhất tính tới 2026-08-07 12:00 UTC là 2026-08-06
    # (ranh giới ngày 00:00 UTC, xem _latest_closed_bar_date).
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert compute_bars_behind("2026-08-06", now) == 0


def test_compute_bars_behind_positive_when_stale() -> None:
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert compute_bars_behind("2026-08-03", now) == 3


def test_compute_bars_behind_never_negative_when_ahead() -> None:
    """last_processed_bar trong TƯƠNG LAI so với now (không nên xảy ra
    thật, nhưng hàm không được trả số âm — max(0, ...))."""
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert compute_bars_behind("2026-08-10", now) == 0


def test_compute_bars_behind_mutation_kill_ignores_last_processed() -> None:
    """Đột biến kiểm chứng (CLAUDE.md #16): nếu compute_bars_behind() LUÔN
    trả 0 bất kể input, test staleness ở trên PHẢI đỏ."""

    def _broken(last_processed_bar: str | None, now: datetime) -> int:
        return 0

    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert _broken("2026-08-03", now) != 3


def test_live_loop_state_has_no_websocket_fields() -> None:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(LiveLoopState)}
    assert "ws_connected" not in field_names
    assert {"last_poll_at", "poll_latency_ms"} <= field_names


def test_write_then_load_state_snapshot_roundtrips_poll_telemetry(tmp_path: Path) -> None:
    path = tmp_path / "state_snapshot.json"
    state = replace(_fresh_state(), last_poll_at="2026-08-07T00:01:03+00:00", poll_latency_ms=123.0)
    write_state_snapshot(path, state)

    loaded = load_state_snapshot(path)
    assert loaded is not None
    assert loaded.last_poll_at == "2026-08-07T00:01:03+00:00"
    assert loaded.poll_latency_ms == 123.0


def test_load_state_snapshot_backward_compatible_missing_poll_telemetry(tmp_path: Path) -> None:
    """Snapshot ghi TRƯỚC khi last_poll_at/poll_latency_ms tồn tại vẫn
    phải load được — dùng default None, không bị coi là hỏng."""
    old_snapshot = {
        "last_processed_bar": "2026-08-06",
        "current_stop_loss": None,
        "current_allocation_pct": "0.5",
        "current_regime_id": 1,
        "current_regime_label": "BULL",
        "session_started_at_utc": "2026-08-06T00:00:00+00:00",
        "written_at_utc": "2026-08-06T00:00:00+00:00",
    }
    path = tmp_path / "state_snapshot.json"
    path.write_text(json.dumps(old_snapshot), encoding="utf-8")

    loaded = load_state_snapshot(path)
    assert loaded is not None
    assert loaded.last_poll_at is None
    assert loaded.poll_latency_ms is None


# ----------------------------------------------------------------------
# Lệch đồng hồ (2026-08-07) — main.py::_check_clock_drift(), halt gate ở
# đầu process_one_bar(). Không dùng monitoring.clock trực tiếp ở đây (đã
# test riêng, chính xác, ở tests/test_monitoring_clock.py) — chỉ kiểm tra
# NGƯỠNG và HÀNH ĐỘNG (alert/halt) được kích hoạt đúng lúc main.py gọi nó.
# ----------------------------------------------------------------------


class _FakeExchangeClientWithClockAndOrderbook(_FakeExchangeClient):
    """`drift_ms` xấp xỉ (không tuyệt đối chính xác — round-trip trong
    tiến trình test gần như 0, nên sai số cỡ vài ms, đủ nhỏ so với biên
    1000/2500ms mà các test này kiểm tra). `get_orderbook()` trả spread
    hẹp để `_check_spread_and_alert()` (cũng chạy khi alert_manager được
    truyền) không tự phát sinh alert gây nhiễu assertion."""

    def __init__(self, drift_ms: float = 0.0) -> None:
        super().__init__()
        self._drift_ms = drift_ms

    def get_server_time(self) -> int:
        import time

        return int(time.time() * 1000 + self._drift_ms)

    def get_orderbook(self, symbol: str) -> _FakeOrderBook:
        return _FakeOrderBook("50000", "50005")


def _clock_process_one_bar(
    tmp_path: Path, *, drift_ms: float, manager: _SpyAlertManager, state: LiveLoopState | None = None
) -> LiveLoopState:
    generator = _signal_generator(tmp_path)
    exchange_client = _FakeExchangeClientWithClockAndOrderbook(drift_ms=drift_ms)
    order_executor = _FakeOrderExecutor(exchange_client=exchange_client)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    return process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state if state is not None else _fresh_state(),
        dry_run=True,
        alert_manager=manager,
    )


def test_clock_drift_below_alert_threshold_no_alert(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    _clock_process_one_bar(tmp_path, drift_ms=500.0, manager=manager)
    assert AlertType.CLOCK_DRIFT not in [a.alert_type for a in manager.sent]


def test_clock_drift_between_alert_and_halt_thresholds_alerts_but_does_not_halt(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    exchange_client = _FakeExchangeClientWithClockAndOrderbook(drift_ms=1500.0)
    order_executor = _FakeOrderExecutor(exchange_client=exchange_client)
    generator = _signal_generator(tmp_path)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
        alert_manager=manager,
    )

    assert AlertType.CLOCK_DRIFT in [a.alert_type for a in manager.sent]
    # KHÔNG halt ở 1500ms (< 2500ms mặc định) — signal vẫn được sinh ra
    # bình thường (declining bars -> BEAR_STRUCTURE, regime được gán).
    assert new_state.current_regime_id is not None


def test_clock_drift_above_halt_threshold_halts_no_order_and_keeps_state(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    exchange_client = _FakeExchangeClientWithClockAndOrderbook(drift_ms=3000.0)
    order_executor = _FakeOrderExecutor(exchange_client=exchange_client)
    generator = _signal_generator(tmp_path)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    base = _fresh_state()
    state = replace(
        base,
        current_stop_loss="40",
        current_allocation_pct="0.5",
        current_regime_id=1,
        current_regime_label="BULL",
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
        alert_manager=manager,
    )

    assert AlertType.CLOCK_DRIFT in [a.alert_type for a in manager.sent]
    # "Giữ vị thế và stop hiện có" — KHÔNG đổi allocation/stop/regime dù
    # đang ở dry_run=False (đủ điều kiện gửi lệnh thật nếu không bị halt).
    assert new_state.current_stop_loss == "40"
    assert new_state.current_allocation_pct == "0.5"
    assert new_state.current_regime_id == 1
    assert order_executor.submit_order_calls == []
    assert order_executor.close_position_calls == []
    assert order_executor.modify_stop_calls == []


def test_clock_drift_halt_still_updates_telemetry_and_last_processed_bar(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    new_state = _clock_process_one_bar(tmp_path, drift_ms=3000.0, manager=manager)

    assert new_state.last_clock_drift_ms is not None
    assert abs(new_state.last_clock_drift_ms - 3000.0) < 50.0
    assert new_state.last_processed_bar is not None


def test_clock_drift_negative_offset_also_triggers_halt(tmp_path: Path) -> None:
    """Ngưỡng so trên |drift|, không phải drift dương — đồng hồ CHẬM hơn
    sàn cũng nguy hiểm như NHANH hơn."""
    manager = _SpyAlertManager()
    new_state = _clock_process_one_bar(tmp_path, drift_ms=-3000.0, manager=manager)

    assert AlertType.CLOCK_DRIFT in [a.alert_type for a in manager.sent]
    assert new_state.current_allocation_pct == "0"  # _fresh_state() mặc định, không đổi


def test_clock_check_logged_every_bar_even_without_alert(tmp_path: Path) -> None:
    manager = _SpyAlertManager()
    regime_logger = get_logger("regime_clock_test", str(tmp_path / "logs"))
    exchange_client = _FakeExchangeClientWithClockAndOrderbook(drift_ms=100.0)
    order_executor = _FakeOrderExecutor(exchange_client=exchange_client)
    generator = _signal_generator(tmp_path)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
        alert_manager=manager,
        regime_state_logger=regime_logger,
    )
    for h in regime_logger.handlers:
        h.flush()

    log_text = (tmp_path / "logs" / "regime_clock_test.log").read_text(encoding="utf-8")
    assert '"event": "clock_check"' in log_text
    # 100ms < ngưỡng alert 1000ms mặc định — KHÔNG có CLOCK_DRIFT alert...
    assert AlertType.CLOCK_DRIFT not in [a.alert_type for a in manager.sent]
    # ... nhưng vẫn được LOG (spec: "mỗi bar, phát ClockCheck vào log").


def test_check_clock_drift_measurement_failure_does_not_halt(tmp_path: Path) -> None:
    """get_server_time() raise -> KHÔNG halt (đó là việc của DATA_FEED_LOST/
    API_LOST, không phải CLOCK_DRIFT — xem docstring _check_clock_drift)."""

    class _BrokenClockExchangeClient(_FakeExchangeClient):
        def get_server_time(self) -> int:
            raise ConnectionError("no network")

        def get_orderbook(self, symbol: str) -> _FakeOrderBook:
            return _FakeOrderBook("50000", "50005")

    manager = _SpyAlertManager()
    order_executor = _FakeOrderExecutor(exchange_client=_BrokenClockExchangeClient())
    generator = _signal_generator(tmp_path)
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_fresh_state(),
        dry_run=True,
        alert_manager=manager,
    )

    assert AlertType.CLOCK_DRIFT not in [a.alert_type for a in manager.sent]
    assert new_state.current_regime_id is not None  # tiếp tục xử lý bar bình thường


