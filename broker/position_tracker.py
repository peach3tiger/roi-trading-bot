"""broker.position_tracker — theo dõi vị thế qua polling REST.

REST polling, không WebSocket — quyết định kiến trúc 2026-08-06 (xem
`docs/DECISIONS.md`, mục "Đổi sàn Bybit -> Binance (ccxt)"). Đã bỏ
`on_execution()` (từng được gọi qua `ExchangeClient.subscribe_executions`,
nay không còn tồn tại trong ABC — xem `broker/base.py`): không còn cơ chế
nào đẩy `OrderResult` vào lớp này giữa hai lần đối soát, nên giữ lại nó là
code chết. Nguồn cập nhật DUY NHẤT bây giờ là đối soát với sàn — lúc khởi
động (`reconcile_on_startup()`) VÀ định kỳ trong lúc chạy (`poll()`, gọi
từ main loop mỗi vòng, cùng logic, khác tên chỉ để chỗ gọi đọc rõ ý định).

Đối soát khi khởi động: ở thị trường 24/7, bot offline trong lúc thị
trường vẫn chạy là chuyện thường ngày, không phải trường hợp hiếm. Nếu
trạng thái đã lưu lệch với số dư thực tế trên sàn, tin sàn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from broker.base import ExchangeClient

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
    sàn không lưu (đặc biệt đúng với spot, không có khái niệm "position"
    như derivatives — xem ghi chú ở
    `broker/ccxt_client.py::CCXTClient.get_positions`).
    """

    def __init__(self, exchange_client: ExchangeClient) -> None:
        self.exchange_client = exchange_client
        self._positions: dict[str, TrackedPosition] = {}

    def reconcile_on_startup(self) -> None:
        """So số dư thực tế trên sàn với trạng thái đã lưu; nếu lệch, tin
        sàn và log cảnh báo — không tự "sửa" số dư sàn để khớp state cũ
        (Brain-Crypto-Bybit.md §6.5)."""
        self._reconcile()

    def poll(self) -> None:
        """Đối soát định kỳ trong lúc chạy — cùng logic
        `reconcile_on_startup()`. Gọi từ main loop mỗi vòng poll REST
        (thay cho việc nhận đẩy qua `on_execution()` trước đây), tên riêng
        chỉ để chỗ gọi đọc rõ ý định (đối soát định kỳ, không phải chỉ lúc
        khởi động)."""
        self._reconcile()

    def _reconcile(self) -> None:
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

        # Local có vị thế nhưng sàn không còn — đã đóng (bot offline, hoặc
        # chỉ đơn giản là kể từ lần poll() trước).
        for symbol in list(self._positions):
            if symbol not in exchange_positions:
                logger.warning(
                    "Đối soát: %s có trong local nhưng sàn không còn — coi như đã đóng, xoá khỏi tracking.",
                    symbol,
                )
                del self._positions[symbol]

    def get_position(self, symbol: str) -> TrackedPosition | None:
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[TrackedPosition]:
        return list(self._positions.values())
