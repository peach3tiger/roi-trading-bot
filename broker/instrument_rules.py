"""broker.instrument_rules — tick/lot/precision, không có tương đương ở equities.

Lấy từ /v5/market/instruments-info khi khởi động và cache. Cổ phiếu là số
nguyên; crypto chia lẻ tuỳ theo basePrecision của từng symbol. Đây là nguồn
lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto — xem
CLAUDE.md bất biến #3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


def _round_to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    """Làm tròn theo bội số của `step`, hướng do `rounding` quyết định.

    Không dùng `Decimal.quantize` vì quantize chỉ làm tròn theo số chữ số thập
    phân (luỹ thừa của 10). `basePrecision` của Bybit thường là 0.000001 nên
    quantize *tình cờ* đúng, nhưng `min_order_qty`/lot size của một số symbol là
    0.5 hoặc 0.05 — quantize sẽ âm thầm sai ở đúng những symbol đó. Chia–làm
    tròn–nhân đúng với mọi step.
    """
    if step <= 0:
        raise ValueError(f"step phải dương, nhận được {step}")
    return (value / step).to_integral_value(rounding=rounding) * step


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Luôn XUỐNG — dùng cho SỐ LƯỢNG (xem `round_qty`)."""
    return _round_to_step(value, step, ROUND_DOWN)


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

    def round_price(self, price: Decimal, rounding: str = ROUND_DOWN) -> Decimal:
        """Làm tròn theo tick_size, HƯỚNG do caller chọn.

        CLAUDE.md bất biến #3 quy định `ROUND_DOWN` cho **SỐ LƯỢNG**
        (`round_qty`), KHÔNG phải cho GIÁ. Tham số hướng ở đây **không vi
        phạm bất biến đó** — nó nằm ở một đại lượng khác. Ghi rõ điều này
        vì bản trước ép ROUND_DOWN cho cả giá "cho nhất quán với
        round_qty", và nếu không ghi lại thì lần review sau sẽ có người
        "sửa lại cho đúng" và tái tạo chính bug này.

        Hướng đúng phụ thuộc CHIỀU lệnh, vì "an toàn" nghĩa ngược nhau ở
        hai chiều:

        - **BUY → `ROUND_DOWN`**: giá thấp hơn → notional thấp hơn → không
          bao giờ vô tình vượt số dư khả dụng.
        - **SELL (rebalance giảm tỷ trọng) → `ROUND_UP`**: bán là NHẬN
          tiền, làm tròn xuống nghĩa là tự nguyện nhận ít hơn ở mỗi lệnh.
          Làm tròn lên cho giá tốt hơn và không đụng tới số dư.

        **Ngoại lệ — thoát bảo vệ khi thủng stop:** ưu tiên KHỚP ĐƯỢC hơn
        giá tốt. `close_position()` dùng `OrderType.MARKET` nên không đi
        qua hàm này; nếu sau này chuyển sang LIMIT, phải dùng `ROUND_DOWN`
        (giá dễ khớp) chứ KHÔNG phải `ROUND_UP` như rebalance — một lệnh
        thoát không khớp là giữ nguyên vị thế đang lỗ.

        Mặc định `ROUND_DOWN` giữ nguyên hành vi cũ cho mọi caller chưa
        chỉ định — hướng an toàn khi không biết chiều lệnh.
        """
        return _round_to_step(price, self.tick_size, rounding)

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
