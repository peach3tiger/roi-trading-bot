"""broker.position_tracker — theo dõi vị thế qua WebSocket private stream.

Đối soát khi khởi động: ở thị trường 24/7, bot offline trong lúc thị
trường vẫn chạy là chuyện thường ngày, không phải trường hợp hiếm. Nếu
trạng thái đã lưu lệch với số dư thực tế trên sàn, tin sàn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from broker.base import ExchangeClient, OrderResult


@dataclass
class TrackedPosition:
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    current_price: Decimal
    qty: Decimal
    unrealized_pnl: Decimal
    stop_loss: Decimal
    regime_at_entry: str
    regime_current: str


class PositionTracker:
    def __init__(self, exchange_client: ExchangeClient) -> None:
        ...

    def reconcile_on_startup(self) -> None:
        """So số dư thực tế trên sàn với trạng thái đã lưu; nếu lệch, tin sàn và log cảnh báo."""
        raise NotImplementedError

    def on_execution(self, order_result: OrderResult) -> None:
        """Cập nhật PortfolioState và CircuitBreaker sau mỗi lần khớp."""
        raise NotImplementedError

    def get_position(self, symbol: str) -> TrackedPosition | None:
        raise NotImplementedError

    def get_all_positions(self) -> list[TrackedPosition]:
        raise NotImplementedError
