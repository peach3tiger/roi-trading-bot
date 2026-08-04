"""broker.instrument_rules — tick/lot/precision, không có tương đương ở equities.

Lấy từ /v5/market/instruments-info khi khởi động và cache. Cổ phiếu là số
nguyên; crypto chia lẻ tuỳ theo basePrecision của từng symbol. Đây là nguồn
lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto — xem
CLAUDE.md bất biến #3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


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
        raise NotImplementedError

    def round_price(self, price: Decimal) -> Decimal:
        """Làm tròn theo tick_size."""
        raise NotImplementedError

    def is_valid_order(self, qty: Decimal, price: Decimal) -> tuple[bool, str]:
        """Kiểm tra min/max qty và min_order_amt trước khi gửi — rẻ hơn
        nhiều so với bị sàn từ chối."""
        raise NotImplementedError
