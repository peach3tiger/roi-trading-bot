"""broker.position_tracker — theo dõi vị thế qua WebSocket private stream.

Đối soát khi khởi động: ở thị trường 24/7, bot offline trong lúc thị
trường vẫn chạy là chuyện thường ngày, không phải trường hợp hiếm. Nếu
trạng thái đã lưu lệch với số dư thực tế trên sàn, tin sàn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from broker.base import ExchangeClient, OrderResult

logger = logging.getLogger(__name__)

_UNKNOWN_REGIME = "UNKNOWN"


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
    """Nguồn sự thật cho `qty`/`current_price` LUÔN là sàn — trạng thái cục
    bộ (`entry_price`, `regime_at_entry`, ...) chỉ là bối cảnh bổ sung mà
    sàn không lưu (đặc biệt đúng với spot trên Bybit, không có khái niệm
    "position" như derivatives — xem ghi chú ở
    `broker/bybit_client.py::BybitClient.get_positions`).

    `on_execution()` cần biết symbol/side của lần khớp để cập nhật đúng
    hướng, nhưng `OrderResult` (broker/base.py) không mang hai trường đó
    trực tiếp — đọc từ `raw_response` (luồng execution thật của Bybit có
    `symbol`/`side`, xem `BybitClient.subscribe_executions`). Thiếu một
    trong hai thì log cảnh báo và bỏ qua, không đoán.
    """

    def __init__(self, exchange_client: ExchangeClient) -> None:
        self.exchange_client = exchange_client
        self._positions: dict[str, TrackedPosition] = {}

    def reconcile_on_startup(self) -> None:
        """So số dư thực tế trên sàn với trạng thái đã lưu; nếu lệch, tin
        sàn và log cảnh báo — không tự "sửa" số dư sàn để khớp state cũ
        (Brain-Crypto-Bybit.md §6.5)."""
        exchange_positions = {p.symbol: p for p in self.exchange_client.get_positions()}

        for symbol, exchange_pos in exchange_positions.items():
            local = self._positions.get(symbol)
            if local is None:
                logger.warning(
                    "Đối soát: sàn có vị thế %s qty=%s mà local không biết — tạo mới, "
                    "entry_price=giá hiện tại (không biết entry thật, xem docstring lớp).",
                    symbol,
                    exchange_pos.qty,
                )
                self._positions[symbol] = TrackedPosition(
                    symbol=symbol,
                    entry_time=datetime.now(timezone.utc),
                    entry_price=exchange_pos.entry_price,
                    current_price=exchange_pos.current_price,
                    qty=exchange_pos.qty,
                    unrealized_pnl=exchange_pos.unrealized_pnl,
                    stop_loss=Decimal("0"),
                    regime_at_entry=_UNKNOWN_REGIME,
                    regime_current=_UNKNOWN_REGIME,
                )
                continue

            if local.qty != exchange_pos.qty:
                logger.warning(
                    "Đối soát: %s lệch qty — local=%s, sàn=%s. Tin sàn.",
                    symbol,
                    local.qty,
                    exchange_pos.qty,
                )
            local.qty = exchange_pos.qty
            local.current_price = exchange_pos.current_price
            local.unrealized_pnl = exchange_pos.unrealized_pnl

        # Local có vị thế nhưng sàn không còn — đã đóng trong lúc bot offline.
        for symbol in list(self._positions):
            if symbol not in exchange_positions:
                logger.warning(
                    "Đối soát: %s có trong local nhưng sàn không còn — coi như đã đóng, xoá khỏi tracking.",
                    symbol,
                )
                del self._positions[symbol]

    def on_execution(self, order_result: OrderResult) -> None:
        """Cập nhật vị thế cục bộ sau mỗi lần khớp. Chỉ cập nhật `qty`/
        `current_price` — cập nhật `PortfolioState`/`CircuitBreaker` là
        việc của caller (main loop, Phase 10) sau khi có equity mới, không
        phải việc của lớp này (giữ phạm vi đúng như tên class: theo dõi vị
        thế, không phải toàn bộ portfolio/risk)."""
        symbol = order_result.raw_response.get("symbol")
        side = order_result.raw_response.get("side")
        if not symbol or not side:
            logger.warning(
                "on_execution(%s): raw_response thiếu symbol/side — không cập nhật được vị thế.",
                order_result.order_link_id,
            )
            return

        fill_price = order_result.avg_fill_price
        if fill_price is None:
            logger.warning("on_execution(%s): thiếu avg_fill_price — không cập nhật.", symbol)
            return

        signed_qty = order_result.filled_qty if side == "Buy" else -order_result.filled_qty
        existing = self._positions.get(symbol)

        if existing is None:
            if signed_qty <= 0:
                return  # khớp bán mà chưa có vị thế cục bộ nào — không tạo vị thế âm
            self._positions[symbol] = TrackedPosition(
                symbol=symbol,
                entry_time=datetime.now(timezone.utc),
                entry_price=fill_price,
                current_price=fill_price,
                qty=signed_qty,
                unrealized_pnl=Decimal("0"),
                stop_loss=Decimal("0"),
                regime_at_entry=_UNKNOWN_REGIME,
                regime_current=_UNKNOWN_REGIME,
            )
            return

        new_qty = existing.qty + signed_qty
        if new_qty <= 0:
            del self._positions[symbol]
            return
        existing.qty = new_qty
        existing.current_price = fill_price

    def get_position(self, symbol: str) -> TrackedPosition | None:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[TrackedPosition]:
        return list(self._positions.values())
