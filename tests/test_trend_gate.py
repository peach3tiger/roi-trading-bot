"""tests.test_trend_gate — Gate chỉ giảm, không tăng. Implement ở Phase 3.5."""

import pytest


def test_gate_never_increases_allocation() -> None:
    pytest.skip("TODO: Phase 3.5 — core/trend_gate.py")


def test_buffer_prevents_whipsaw() -> None:
    pytest.skip("TODO: Phase 3.5 — buffer 2% quanh SMA200")


def test_confirm_bars_delays_state_change() -> None:
    pytest.skip("TODO: Phase 3.5 — xác nhận 5 bar")


def test_cap_only_tightens_immediately_loosens_after_confirmation() -> None:
    pytest.skip("TODO: Phase 3.5 — bất đối xứng siết/nới")
