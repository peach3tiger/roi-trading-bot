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
    _latest_closed_bar_date,
    load_state_snapshot,
    process_one_bar,
    write_state_snapshot,
)

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
    def __init__(self) -> None:
        self.order_id = "1"
        self.order_link_id = "abc"
        self.status = "FILLED"
        self.filled_qty = Decimal("0.1")


class _FakeOrderExecutor:
    def __init__(self) -> None:
        self.exchange_client = _FakeExchangeClient()
        self.submit_order_calls: list[Any] = []
        self.modify_stop_calls: list[tuple[str, Decimal]] = []
        self.close_position_calls: list[str] = []

    def submit_order(self, signal: Any) -> _FakeOrderResult:
        self.submit_order_calls.append(signal)
        return _FakeOrderResult()

    def modify_stop(self, symbol: str, new_stop: Decimal) -> bool:
        self.modify_stop_calls.append((symbol, new_stop))
        return True

    def close_position(self, symbol: str) -> _FakeOrderResult:
        self.close_position_calls.append(symbol)
        return _FakeOrderResult()


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


def _risk_manager(tmp_path: Path) -> RiskManager:
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
    return RiskManager(config, halt_lock_path=tmp_path / "trading_halted.lock")


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
