"""tests.test_risk — RiskManager, CircuitBreaker. Implement ở Phase 5."""

import pytest


def test_position_requires_stop_loss() -> None:
    pytest.skip("TODO: Phase 5 — core/risk_manager.py")


def test_daily_drawdown_circuit_breaker() -> None:
    pytest.skip("TODO: Phase 5 — daily DD 4%/6%")


def test_weekly_drawdown_circuit_breaker() -> None:
    pytest.skip("TODO: Phase 5 — weekly DD 10%/14%")


def test_peak_drawdown_halts_trading() -> None:
    pytest.skip("TODO: Phase 5 — peak DD 20% tạo trading_halted.lock")


def test_spread_check_rejects_wide_spread() -> None:
    pytest.skip("TODO: Phase 5 — spread > 0.10%")


def test_stablecoin_depeg_pauses_trading() -> None:
    pytest.skip("TODO: Phase 5 — USDT lệch peg > 0.5%")


def test_duplicate_order_blocked_within_60s() -> None:
    pytest.skip("TODO: Phase 5 — chặn lệnh trùng")


def test_risk_manager_does_not_import_hmm_or_strategies() -> None:
    """Kiểm chứng tĩnh: core/risk_manager.py không import core.hmm_engine
    hay core.regime_strategies — độc lập là lý do nó vẫn bảo vệ được khi
    HMM sai hoàn toàn (CLAUDE.md bất biến #4)."""
    pytest.skip("TODO: Phase 5 — kiểm tra bằng AST hoặc import graph")
