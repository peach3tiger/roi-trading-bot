"""broker.order_executor — submit/cancel/modify qua ExchangeClient, idempotent.

orderLinkId sinh deterministic từ (symbol, bar_timestamp, target_allocation)
là khoá idempotency bắt buộc: thị trường 24/7 nên bot SẼ crash-restart
giữa lúc có lệnh đang chờ; không có khoá này một lần restart có thể nhân
đôi vị thế — xem CLAUDE.md bất biến #8.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from broker.base import ExchangeClient, OrderResult


class SignalLike(Protocol):
    """Hình dạng tối thiểu cần từ một signal — tránh phụ thuộc trực tiếp
    vào core.regime_strategies.Signal, giữ tầng broker độc lập với tầng
    strategy (đối xứng với CLAUDE.md bất biến #4 áp cho risk_manager).

    `@property` chỉ đọc, không phải thuộc tính thường — Signal thật là
    frozen dataclass, xem ghi chú tương tự ở core/risk_manager.py.
    """

    @property
    def symbol(self) -> str: ...
    @property
    def target_allocation_pct(self) -> Decimal: ...
    @property
    def timestamp(self) -> datetime: ...


class OrderExecutor:
    def __init__(
        self, exchange_client: ExchangeClient, limit_offset_pct: Decimal, timeout_seconds: int
    ) -> None:
        ...

    def generate_order_link_id(
        self, symbol: str, bar_timestamp: datetime, target_allocation: Decimal
    ) -> str:
        """Deterministic — cùng bộ ba input luôn cho cùng id, để sàn từ
        chối lệnh trùng thay vì đặt hai lần khi bot restart giữa chừng."""
        raise NotImplementedError

    def submit_order(self, signal: SignalLike) -> OrderResult:
        """LIMIT mặc định, đặt ±0.05% quanh giá hiện tại; huỷ sau
        `timeout_seconds` nếu chưa khớp; tuỳ chọn đặt lại bằng MARKET."""
        raise NotImplementedError

    def handle_partial_fill(self, order_result: OrderResult) -> Decimal:
        """Theo dõi qty đã khớp, trả về phần còn thiếu cần rebalance tiếp."""
        raise NotImplementedError

    def modify_stop(self, symbol: str, new_stop: Decimal) -> bool:
        """Chỉ được siết chặt, không bao giờ nới rộng — xem CLAUDE.md bất biến #5."""
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def close_position(self, symbol: str) -> OrderResult:
        raise NotImplementedError

    def close_all_positions(self) -> list[OrderResult]:
        raise NotImplementedError
