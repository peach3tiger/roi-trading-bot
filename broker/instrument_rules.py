"""broker.instrument_rules — tick/lot/precision, không có tương đương ở equities.

Lấy từ /v5/market/instruments-info khi khởi động và cache. Cổ phiếu là số
nguyên; crypto chia lẻ tuỳ theo basePrecision của từng symbol. Đây là nguồn
lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto — xem
CLAUDE.md bất biến #3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Làm tròn XUỐNG theo bội số của `step`.

    Không dùng `Decimal.quantize` vì quantize chỉ làm tròn theo số chữ số thập
    phân (luỹ thừa của 10). `basePrecision` của Bybit thường là 0.000001 nên
    quantize *tình cờ* đúng, nhưng `min_order_qty`/lot size của một số symbol là
    0.5 hoặc 0.05 — quantize sẽ âm thầm sai ở đúng những symbol đó. Chia–floor–
    nhân đúng với mọi step.
    """
    if step <= 0:
        raise ValueError(f"step phải dương, nhận được {step}")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class InstrumentRules:
    symbol: str
    base_precision: Decimal
    quote_precision: Decimal
    tick_size: Decimal
    min_order_qty: Decimal
    min_order_amt: Decimal
    max_order_qty: Decimal

    def round_qty(self, qty: Decimal) -> Decimal:
        """Làm tròn XUỐNG theo base_precision. Luôn xuống, không bao giờ
        lên — làm tròn lên có thể tạo lệnh vượt số dư khả dụng."""
        # Với qty âm (bán bớt), làm tròn theo độ lớn rồi trả lại dấu. Nếu để
        # floor chạy thẳng trên số âm thì -0.0000005 thành -0.000001, tức là
        # bán NHIỀU hơn yêu cầu — ngược đúng hướng an toàn mà docstring nói tới.
        if qty < 0:
            return -_floor_to_step(-qty, self.base_precision)
        return _floor_to_step(qty, self.base_precision)

    def round_price(self, price: Decimal) -> Decimal:
        """Làm tròn theo tick_size.

        Spec không nói hướng làm tròn giá. Chọn ROUND_DOWN cho nhất quán với
        `round_qty`: giá thấp hơn → notional ước lượng thấp hơn → không bao giờ
        vô tình vượt số dư (CLAUDE.md: spec không rõ thì chọn phương án giảm rủi
        ro và ghi lại lý do).
        """
        return _floor_to_step(price, self.tick_size)

    def is_valid_order(self, qty: Decimal, price: Decimal) -> tuple[bool, str]:
        """Kiểm tra min/max qty và min_order_amt trước khi gửi — rẻ hơn
        nhiều so với bị sàn từ chối."""
        if qty <= 0:
            return False, f"qty phải dương, nhận được {qty}"
        if qty < self.min_order_qty:
            return False, f"qty {qty} < min_order_qty {self.min_order_qty}"
        if qty > self.max_order_qty:
            return False, f"qty {qty} > max_order_qty {self.max_order_qty}"

        order_amt = qty * price
        if order_amt < self.min_order_amt:
            return False, f"giá trị lệnh {order_amt} < min_order_amt {self.min_order_amt}"

        return True, ""
