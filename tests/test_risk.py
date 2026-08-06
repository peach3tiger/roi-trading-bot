"""tests.test_risk — RiskManager, CircuitBreaker. Implement ở Phase 5 (phase-08)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from core.risk_manager import BreakerLevel, CircuitBreaker, PortfolioState, RiskManager

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Signal:
    """Dataclass tối thiểu khớp `core.risk_manager.SignalLike` — KHÔNG
    import `core.regime_strategies.Signal` (giữ test độc lập với HMM/strategy,
    đúng tinh thần bất biến #4). Là dataclass thật (không phải mock) để
    `dataclasses.replace()` trong RiskManager.validate_signal hoạt động
    đúng như với Signal thật.
    """

    symbol: str = "BTCUSDT"
    direction: str = "LONG"
    target_allocation_pct: Decimal = Decimal("0.95")
    stop_loss: Optional[Decimal] = Decimal("60000")
    timestamp: datetime = field(default_factory=lambda: _T0)


def _risk_config(**overrides: object) -> dict:
    base: dict = {
        "max_position_pct": 100,
        "max_risk_per_trade_pct": 1.0,
        "min_order_value_usdt": 5,
        "min_cash_buffer_pct": 5.0,
        "max_trades_per_day": 6,
        "max_leverage": 1.0,
        "spread_max_pct": 0.10,
        "usdt_depeg_threshold_pct": 0.5,
        "duplicate_order_window_seconds": 60,
        "circuit_breaker": {
            "daily_dd_reduce_pct": 4.0,
            "daily_dd_halt_pct": 6.0,
            "weekly_dd_reduce_pct": 10.0,
            "weekly_dd_halt_pct": 14.0,
            "peak_dd_halt_pct": 20.0,
        },
    }
    base.update(overrides)
    return base


def _portfolio(equity: Decimal) -> PortfolioState:
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


def _make_rm(tmp_path: Path, **config_overrides: object) -> RiskManager:
    return RiskManager(_risk_config(**config_overrides), halt_lock_path=tmp_path / "trading_halted.lock")


# ----------------------------------------------------------------------
# Độc lập với HMM/strategy — kiểm tra TĨNH bằng AST, không phải chỉ đọc mắt
# ----------------------------------------------------------------------


def test_risk_manager_does_not_import_hmm_or_strategies() -> None:
    """Kiểm chứng tĩnh: core/risk_manager.py không import core.hmm_engine
    hay core.regime_strategies — độc lập là lý do nó vẫn bảo vệ được khi
    HMM sai hoàn toàn (CLAUDE.md bất biến #4)."""
    source = Path("core/risk_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="core/risk_manager.py")

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {"core.hmm_engine", "core.regime_strategies"}
    hit = {m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)}
    assert not hit, f"core/risk_manager.py import cấm (bất biến #4 vi phạm): {hit}"


# ----------------------------------------------------------------------
# validate_signal — stop loss, cap allocation, lệnh trùng, max trades/ngày
# ----------------------------------------------------------------------


def test_position_requires_stop_loss(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    decision = rm.validate_signal(_Signal(stop_loss=None), _portfolio(Decimal("10000")))
    assert decision.approved is False
    assert decision.rejection_reason is not None
    assert "stop loss" in decision.rejection_reason.lower()


def test_allocation_over_max_gets_capped(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    decision = rm.validate_signal(_Signal(target_allocation_pct=Decimal("2.0")), _portfolio(Decimal("10000")))
    assert decision.approved is True
    assert decision.modified_signal is not None
    # max_position_pct=100% nhưng min_cash_buffer_pct=5% khắt khe hơn -> trần thật 95%.
    assert decision.modified_signal.target_allocation_pct == Decimal("0.95")
    assert decision.modifications


def test_allocation_within_limit_unchanged(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    signal = _Signal(target_allocation_pct=Decimal("0.50"))
    decision = rm.validate_signal(signal, _portfolio(Decimal("10000")))
    assert decision.approved is True
    assert decision.modified_signal is not None
    assert decision.modified_signal.target_allocation_pct == Decimal("0.50")
    assert decision.modifications == []


def test_duplicate_order_blocked_within_60s(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)

    first = rm.validate_signal(_Signal(timestamp=_T0), _portfolio(Decimal("10000")))
    assert first.approved is True

    second = rm.validate_signal(_Signal(timestamp=_T0 + timedelta(seconds=30)), _portfolio(Decimal("10000")))
    assert second.approved is False
    assert second.rejection_reason is not None
    assert "trùng" in second.rejection_reason.lower()

    third = rm.validate_signal(_Signal(timestamp=_T0 + timedelta(seconds=61)), _portfolio(Decimal("10000")))
    assert third.approved is True


def test_duplicate_window_is_per_symbol_direction(tmp_path: Path) -> None:
    """Khác symbol hoặc khác hướng không bị coi là trùng, dù cùng thời điểm."""
    rm = _make_rm(tmp_path)
    a = rm.validate_signal(_Signal(symbol="BTCUSDT", timestamp=_T0), _portfolio(Decimal("10000")))
    b = rm.validate_signal(_Signal(symbol="ETHUSDT", timestamp=_T0), _portfolio(Decimal("10000")))
    assert a.approved is True
    assert b.approved is True


def test_rejected_signal_does_not_consume_duplicate_window(tmp_path: Path) -> None:
    """Một lệnh bị từ chối vì lý do KHÁC (thiếu stop loss) không được phép
    'tiêu' mất cửa sổ chống trùng — lệnh hợp lệ ngay sau đó vẫn phải qua."""
    rm = _make_rm(tmp_path)
    rejected = rm.validate_signal(_Signal(timestamp=_T0, stop_loss=None), _portfolio(Decimal("10000")))
    assert rejected.approved is False

    approved = rm.validate_signal(_Signal(timestamp=_T0 + timedelta(seconds=1)), _portfolio(Decimal("10000")))
    assert approved.approved is True


def test_max_trades_per_day_rejects_after_limit(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path, max_trades_per_day=3)
    equity = Decimal("10000")

    results = []
    for i in range(4):
        ts = _T0 + timedelta(seconds=i * 120)  # cách xa nhau, không dính chặn trùng lệnh
        results.append(rm.validate_signal(_Signal(timestamp=ts), _portfolio(equity)))

    assert [r.approved for r in results] == [True, True, True, False]
    assert results[-1].rejection_reason is not None
    assert "max_trades_per_day" in results[-1].rejection_reason


def test_reset_daily_clears_trade_count_and_day_reference(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path, max_trades_per_day=1)
    ts = _T0
    equity = _portfolio(Decimal("10000"))
    first = rm.validate_signal(_Signal(timestamp=ts), equity)
    assert first.approved is True

    blocked = rm.validate_signal(_Signal(timestamp=ts + timedelta(seconds=200)), equity)
    assert blocked.approved is False

    rm.reset_daily()
    after_reset = rm.validate_signal(_Signal(timestamp=ts + timedelta(seconds=400)), equity)
    assert after_reset.approved is True


def test_check_correlation_always_approved_v1(tmp_path: Path) -> None:
    """§5.6 — bỏ logic ở v1 (một tài sản), giữ interface luôn approved."""
    rm = _make_rm(tmp_path)
    decision = rm.check_correlation(_Signal(), positions={})
    assert decision.approved is True


# ----------------------------------------------------------------------
# CircuitBreaker — độc lập, chuỗi lỗ kích hoạt đúng ngưỡng đúng thứ tự
# ----------------------------------------------------------------------


def _isolated_breaker(**overrides: Decimal) -> CircuitBreaker:
    """CircuitBreaker với daily threshold thật, weekly/peak vô hiệu hoá
    (ngưỡng cực cao) để cô lập đúng một trục khi test — và ngược lại."""
    defaults = dict(
        daily_dd_reduce_pct=Decimal("4"),
        daily_dd_halt_pct=Decimal("6"),
        weekly_dd_reduce_pct=Decimal("10"),
        weekly_dd_halt_pct=Decimal("14"),
        peak_dd_halt_pct=Decimal("20"),
    )
    defaults.update(overrides)
    return CircuitBreaker(**defaults)


def test_daily_drawdown_circuit_breaker() -> None:
    cb = _isolated_breaker(weekly_dd_reduce_pct=Decimal("1000"), weekly_dd_halt_pct=Decimal("1000"))

    cb.update(Decimal("10000"))
    assert cb.check().level is BreakerLevel.NONE

    cb.update(Decimal("9700"))  # -3% : dưới ngưỡng reduce 4%
    assert cb.check().level is BreakerLevel.NONE

    cb.update(Decimal("9500"))  # -5% : giữa reduce(4%) và halt(6%)
    status = cb.check()
    assert status.level is BreakerLevel.DAILY_REDUCE
    assert status.size_multiplier == Decimal("0.5")

    cb.update(Decimal("9300"))  # -7% : vượt halt 6%
    status = cb.check()
    assert status.level is BreakerLevel.DAILY_HALT
    assert status.size_multiplier == Decimal("0")

    levels = [s.level for s in cb.get_history()]
    assert levels == [
        BreakerLevel.NONE,
        BreakerLevel.NONE,
        BreakerLevel.DAILY_REDUCE,
        BreakerLevel.DAILY_HALT,
    ]


def test_weekly_drawdown_circuit_breaker() -> None:
    cb = _isolated_breaker(daily_dd_reduce_pct=Decimal("1000"), daily_dd_halt_pct=Decimal("1000"))

    cb.update(Decimal("10000"))
    assert cb.check().level is BreakerLevel.NONE

    cb.update(Decimal("9100"))  # -9% : dưới ngưỡng reduce tuần 10%
    assert cb.check().level is BreakerLevel.NONE

    cb.update(Decimal("8800"))  # -12% : giữa reduce(10%) và halt(14%)
    status = cb.check()
    assert status.level is BreakerLevel.WEEKLY_REDUCE
    assert status.size_multiplier == Decimal("0.5")

    cb.update(Decimal("8500"))  # -15% : vượt halt tuần 14%
    status = cb.check()
    assert status.level is BreakerLevel.WEEKLY_HALT
    assert status.size_multiplier == Decimal("0")


def test_peak_drawdown_level_outranks_others() -> None:
    """Peak DD luôn thắng — kể cả khi daily/weekly cũng đang halt cùng lúc."""
    cb = _isolated_breaker()
    cb.update(Decimal("10000"))
    cb.check()
    cb.update(Decimal("7000"))  # -30% mọi trục cùng lúc: daily/weekly/peak đều vượt halt
    status = cb.check()
    assert status.level is BreakerLevel.PEAK_HALT


def test_reset_daily_updates_day_reference_not_peak() -> None:
    cb = _isolated_breaker()
    cb.update(Decimal("10000"))
    cb.update(Decimal("9500"))  # -5%, DAILY_REDUCE nếu chưa reset
    cb.reset_daily()
    status = cb.check()
    assert status.level is BreakerLevel.NONE  # day reference dời về 9500, dd = 0
    assert status.peak_dd > Decimal("0")  # nhưng peak vẫn nhớ đỉnh 10000 cũ


def test_circuit_breaker_get_history_is_a_copy() -> None:
    cb = _isolated_breaker()
    cb.update(Decimal("10000"))
    cb.check()
    history = cb.get_history()
    history.append(None)  # type: ignore[arg-type]
    assert len(cb.get_history()) == 1  # không bị ảnh hưởng bởi sửa bản đã lấy ra


# ----------------------------------------------------------------------
# Peak DD -> trading_halted.lock, chặn mọi signal sau đó
# ----------------------------------------------------------------------


def test_peak_drawdown_halts_trading(tmp_path: Path) -> None:
    lock_path = tmp_path / "trading_halted.lock"
    rm = RiskManager(_risk_config(), halt_lock_path=lock_path)

    equity_10k = _portfolio(Decimal("10000"))
    ok = rm.validate_signal(_Signal(timestamp=_T0), equity_10k)
    assert ok.approved is True
    assert not lock_path.exists()

    # -25% từ đỉnh 10000 -> vượt peak_dd_halt_pct=20%
    equity_7500 = _portfolio(Decimal("7500"))
    halted = rm.validate_signal(_Signal(timestamp=_T0 + timedelta(seconds=1)), equity_7500)
    assert halted.approved is False
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8")  # có nội dung, không rỗng

    # Dù equity phục hồi HOÀN TOÀN, vẫn bị chặn — phải xoá file thủ công.
    ts2 = _T0 + timedelta(seconds=2)
    still_halted = rm.validate_signal(_Signal(timestamp=ts2), equity_10k)
    assert still_halted.approved is False
    assert still_halted.rejection_reason is not None
    assert "trading_halted.lock" in still_halted.rejection_reason

    lock_path.unlink()
    # >60s sau lần approve gần nhất (_T0) — tránh dính chặn lệnh trùng, thứ
    # đang kiểm tra ở đây là halt lock, không phải duplicate-order.
    ts3 = _T0 + timedelta(seconds=120)
    recovered = rm.validate_signal(_Signal(timestamp=ts3), equity_10k)
    assert recovered.approved is True


# ----------------------------------------------------------------------
# compute_position_size — công thức risk-per-trade, cap theo regime rồi danh mục
# ----------------------------------------------------------------------


def test_compute_position_size_matches_risk_formula(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)  # max_risk_per_trade_pct=1%, max_position_pct=100%
    equity = Decimal("10000")
    entry = Decimal("60000")
    stop_loss = Decimal("58000")  # risk/unit = 2000

    qty = rm.compute_position_size(equity, entry, stop_loss, max_allocation=Decimal("1.0"))

    # risk-based: (10000*0.01)/2000 = 0.05 BTC; regime cap: 1.0*10000/60000 ≈ 0.1667;
    # portfolio cap: 1.0*10000/60000 ≈ 0.1667 -> risk-based nhỏ nhất, thắng.
    assert qty == Decimal("0.05")


def test_compute_position_size_capped_by_regime_allocation(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    equity = Decimal("10000")
    entry = Decimal("100")
    stop_loss = Decimal("99")  # risk/unit = 1 -> risk-based qty = 100 (rất lớn)

    qty = rm.compute_position_size(equity, entry, stop_loss, max_allocation=Decimal("0.5"))
    # regime cap: 0.5*10000/100 = 50 -> nhỏ hơn risk-based (100), thắng.
    assert qty == Decimal("50")


def test_compute_position_size_zero_risk_per_unit_returns_zero(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    qty = rm.compute_position_size(Decimal("10000"), Decimal("100"), Decimal("100"), Decimal("1.0"))
    assert qty == Decimal("0")


# ----------------------------------------------------------------------
# Rule đặc thù crypto (§5.4)
# ----------------------------------------------------------------------


def test_spread_check_rejects_wide_spread(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    assert rm.check_spread(bid=Decimal("60000"), ask=Decimal("60010")) is True  # ~0.017%, hẹp
    assert rm.check_spread(bid=Decimal("60000"), ask=Decimal("60100")) is False  # ~0.17% > 0.10%


def test_stablecoin_depeg_pauses_trading(tmp_path: Path) -> None:
    rm = _make_rm(tmp_path)
    assert rm.check_stablecoin_peg(Decimal("0.999")) is True  # lệch 0.1%, trong ngưỡng 0.5%
    assert rm.check_stablecoin_peg(Decimal("0.99")) is False  # lệch 1%, vượt ngưỡng
