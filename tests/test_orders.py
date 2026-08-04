"""tests.test_orders — OrderExecutor: idempotency, partial fill, modify_stop. Implement ở Phase 6."""

import pytest


def test_order_link_id_deterministic() -> None:
    pytest.skip("TODO: Phase 6 — broker/order_executor.py")


def test_duplicate_order_link_id_rejected_by_exchange() -> None:
    pytest.skip("TODO: Phase 6 — idempotency qua orderLinkId")


def test_partial_fill_tracks_remaining_qty() -> None:
    pytest.skip("TODO: Phase 6 — broker/order_executor.py")


def test_modify_stop_never_loosens() -> None:
    pytest.skip("TODO: Phase 6 — modify_stop chỉ siết chặt")
