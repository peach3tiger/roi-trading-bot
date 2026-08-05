"""tests.test_precision — làm tròn qty/price ở mọi biên.

Nguồn lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto — xem
CLAUDE.md bất biến #3. File này KHÔNG được skip, không được xfail, không
được comment out (bất biến #15).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from broker.instrument_rules import InstrumentRules

# Tham số BTC/USDT spot trên Bybit. basePrecision 6 chữ số thập phân.
BTCUSDT = InstrumentRules(
    symbol="BTCUSDT",
    base_precision=Decimal("0.000001"),
    quote_precision=Decimal("0.01"),
    tick_size=Decimal("0.01"),
    min_order_qty=Decimal("0.000048"),
    min_order_amt=Decimal("5"),
    max_order_qty=Decimal("71.73956"),
)

# Symbol có lot size không phải luỹ thừa của 10 — quantize() sẽ sai ở đây.
COARSE_LOT = InstrumentRules(
    symbol="FAKE",
    base_precision=Decimal("0.5"),
    quote_precision=Decimal("0.01"),
    tick_size=Decimal("0.05"),
    min_order_qty=Decimal("0.5"),
    min_order_amt=Decimal("5"),
    max_order_qty=Decimal("1000"),
)


def test_round_qty_rounds_down_never_up() -> None:
    # 0.0000019 -> 0.000001, không phải 0.000002
    assert BTCUSDT.round_qty(Decimal("0.0000019")) == Decimal("0.000001")
    # 0.9999999 -> 0.999999, không phải 1.0
    assert BTCUSDT.round_qty(Decimal("0.9999999")) == Decimal("0.999999")


def test_round_qty_never_exceeds_input() -> None:
    """Bất biến: làm tròn lên có thể tạo lệnh vượt số dư khả dụng."""
    for raw in ["0.1234567", "1.9999999", "0.0000005", "12.345678901"]:
        qty = Decimal(raw)
        assert BTCUSDT.round_qty(qty) <= qty


def test_round_qty_at_precision_boundary() -> None:
    # Đúng bội số của base_precision thì không đổi.
    assert BTCUSDT.round_qty(Decimal("0.000001")) == Decimal("0.000001")
    assert BTCUSDT.round_qty(Decimal("1.000000")) == Decimal("1.000000")
    # Ngay dưới biên tiếp theo.
    assert BTCUSDT.round_qty(Decimal("0.00000199")) == Decimal("0.000001")


def test_round_qty_with_non_power_of_ten_lot_size() -> None:
    """quantize() chỉ làm tròn theo chữ số thập phân nên sai với lot 0.5.

    0.7 quantize về 1 chữ số thập phân vẫn là 0.7 — nhưng bội số hợp lệ gần nhất
    tính xuống là 0.5.
    """
    assert COARSE_LOT.round_qty(Decimal("0.7")) == Decimal("0.5")
    assert COARSE_LOT.round_qty(Decimal("1.4")) == Decimal("1.0")
    assert COARSE_LOT.round_qty(Decimal("2.5")) == Decimal("2.5")


def test_round_qty_negative_rounds_toward_zero() -> None:
    """Bán bớt: làm tròn phải giảm ĐỘ LỚN, không tăng.

    Nếu floor chạy thẳng trên số âm thì -0.0000019 thành -0.000002, tức bán
    nhiều hơn yêu cầu — ngược hướng an toàn.
    """
    assert BTCUSDT.round_qty(Decimal("-0.0000019")) == Decimal("-0.000001")
    assert abs(BTCUSDT.round_qty(Decimal("-0.9999999"))) <= Decimal("0.9999999")


def test_tiny_quantity_rounds_to_zero() -> None:
    """Dưới một base_precision thì về 0 — và bất biến #3 nói rõ đây là lý do
    không được dùng int(): với int(), MỌI vị thế BTC dưới 1 đơn vị đều về 0."""
    assert BTCUSDT.round_qty(Decimal("0.0000009")) == Decimal("0")
    assert BTCUSDT.round_qty(Decimal("0")) == Decimal("0")

    # Đối chứng: 0.5 BTC là vị thế hợp lệ, int() sẽ giết nó.
    assert BTCUSDT.round_qty(Decimal("0.5")) == Decimal("0.5")
    assert int(Decimal("0.5")) == 0


def test_quantity_above_max_order_qty_rejected() -> None:
    ok, reason = BTCUSDT.is_valid_order(Decimal("100"), Decimal("100000"))
    assert ok is False
    assert "max_order_qty" in reason

    ok, _ = BTCUSDT.is_valid_order(Decimal("71.73956"), Decimal("100000"))
    assert ok is True


def test_quantity_below_min_order_qty_rejected() -> None:
    ok, reason = BTCUSDT.is_valid_order(Decimal("0.000047"), Decimal("100000"))
    assert ok is False
    assert "min_order_qty" in reason


def test_order_value_just_below_minimum_rejected() -> None:
    """min_order_amt = 5 USDT. Ở giá 100k, 0.00004999 BTC = 4.999 USDT."""
    ok, reason = BTCUSDT.is_valid_order(Decimal("0.00004999"), Decimal("100000"))
    assert ok is False
    assert "min_order_amt" in reason

    # Ngay trên ngưỡng thì qua.
    ok, _ = BTCUSDT.is_valid_order(Decimal("0.00005001"), Decimal("100000"))
    assert ok is True


def test_zero_or_negative_qty_rejected() -> None:
    for qty in [Decimal("0"), Decimal("-1")]:
        ok, reason = BTCUSDT.is_valid_order(qty, Decimal("100000"))
        assert ok is False
        assert "dương" in reason


def test_round_price_to_tick_size() -> None:
    assert BTCUSDT.round_price(Decimal("100000.017")) == Decimal("100000.01")
    assert BTCUSDT.round_price(Decimal("100000.00")) == Decimal("100000.00")
    # tick 0.05: 12.34 -> 12.30
    assert COARSE_LOT.round_price(Decimal("12.34")) == Decimal("12.30")


def test_round_price_never_exceeds_input() -> None:
    for raw in ["100000.019", "99999.999", "0.011"]:
        price = Decimal(raw)
        assert BTCUSDT.round_price(price) <= price


def test_zero_step_rejected() -> None:
    """base_precision = 0 là config hỏng; phải nổ chứ không chia cho 0."""
    broken = InstrumentRules(
        symbol="BROKEN",
        base_precision=Decimal("0"),
        quote_precision=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        min_order_qty=Decimal("0"),
        min_order_amt=Decimal("5"),
        max_order_qty=Decimal("100"),
    )
    with pytest.raises(ValueError, match="dương"):
        broken.round_qty(Decimal("1.5"))
