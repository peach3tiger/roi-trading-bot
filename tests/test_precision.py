"""tests.test_precision — làm tròn qty/price ở mọi biên. Implement ở Phase 6.

Nguồn lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto — xem
CLAUDE.md bất biến #3. Sau khi implement, file này KHÔNG được skip,
không được xfail, không được comment out (bất biến #15).
"""

import pytest


def test_round_qty_rounds_down_never_up() -> None:
    pytest.skip("TODO: Phase 6 — broker/instrument_rules.py")


def test_round_qty_at_precision_boundary() -> None:
    pytest.skip("TODO: Phase 6 — làm tròn ở biên base_precision")


def test_tiny_quantity_rounds_to_zero() -> None:
    pytest.skip("TODO: Phase 6 — số lượng cực nhỏ")


def test_quantity_above_max_order_qty_rejected() -> None:
    pytest.skip("TODO: Phase 6 — số lượng vượt max")


def test_order_value_just_below_minimum_rejected() -> None:
    pytest.skip("TODO: Phase 6 — giá trị lệnh ngay dưới mức tối thiểu")


def test_round_price_to_tick_size() -> None:
    pytest.skip("TODO: Phase 6 — làm tròn giá theo tick_size")
