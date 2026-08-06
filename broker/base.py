"""broker.base — ExchangeClient ABC.

Thay đổi kiến trúc quan trọng nhất so với bản gốc: bản gốc gắn chặt vào
Alpaca. Interface trừu tượng này tách tầng strategy/risk khỏi bất kỳ SDK
sàn cụ thể nào, để đổi sàn hoặc chuyển spot → perps không phải viết lại
tầng trên. Tầng strategy và risk KHÔNG BAO GIỜ import trực tiếp `pybit`
hay `ccxt` — chỉ qua interface này. Chính bất biến này là lý do đổi sàn
Bybit → Binance (Bybit bị chặn theo khu vực, retCode 10024 — xem
`docs/DECISIONS.md`) chỉ cần một implementation mới
(`broker/ccxt_client.py`), không phải viết lại `broker/order_executor.py`
hay bất kỳ tầng nào phía trên.

KHÔNG còn `subscribe_klines`/`subscribe_executions` (đã bỏ khỏi ABC) — hệ
thống chạy bar `1D`, WebSocket là công nghệ cho tần suất cao mà dự án
không cần; polling REST (30-60s) qua `get_historical_klines`/
`get_positions`/`get_open_orders` đã sẵn có, đơn giản hơn nhiều so với
heartbeat/reconnect của WebSocket. `broker/bybit_client.py` (deprecated,
giữ lại làm bằng chứng) vẫn còn implement hai phương thức đó — chỉ là
không nằm trong hợp đồng ABC nữa, không ai gọi qua interface này.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

import pandas as pd

from broker.instrument_rules import InstrumentRules

# CLAUDE.md bất biến #6: chuyển sang mainnet yêu cầu gõ tay chuỗi xác nhận
# đầy đủ. Đặt hằng số + cổng chặn ở đây (không phải trong từng
# ExchangeClient implementation riêng) để bất biến áp dụng cho MỌI sàn,
# kể cả sàn thêm sau này — không thể vô tình bỏ sót khi viết implementation
# mới. `broker/bybit_client.py` có bản sao riêng của logic này (viết trước
# khi hàm dùng chung này tồn tại) — cố tình để nguyên, không refactor lại,
# vì file đó đã deprecated và chỉ giữ làm bằng chứng lịch sử.
LIVE_CONFIRMATION_PHRASE = "YES I UNDERSTAND THE RISKS"


def require_live_confirmation(input_fn: Callable[[str], str] = input) -> None:
    """Chặn kết nối mainnet cho tới khi gõ đúng `LIVE_CONFIRMATION_PHRASE`.

    `input_fn` tiêm được (mặc định `input` thật) — để test mô phỏng gõ
    đúng/sai/bỏ trống mà không cần stdin thật.
    """
    print("⚠️  LIVE TRADING VỚI TIỀN THẬT.")
    typed = input_fn(f"Gõ '{LIVE_CONFIRMATION_PHRASE}' để xác nhận: ")
    if typed.strip() != LIVE_CONFIRMATION_PHRASE:
        raise PermissionError(
            "Xác nhận live trading không đúng cụm từ yêu cầu — dừng, không kết nối mainnet."
        )


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Balance:
    asset: str
    total: Decimal
    available: Decimal
    locked: Decimal


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    price: Optional[Decimal]
    order_link_id: str


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    order_link_id: str
    status: OrderStatus
    filled_qty: Decimal
    avg_fill_price: Optional[Decimal]
    raw_response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Order:
    order_id: str
    order_link_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    price: Optional[Decimal]
    status: OrderStatus
    created_at: datetime


@dataclass(frozen=True)
class OrderBook:
    """Sổ lệnh rút gọn — đủ để kiểm tra spread trước khi risk_manager
    duyệt lệnh (Brain-Crypto-Bybit.md §5.4/§6.6), không phải full L2 depth.
    `bids`/`asks`: (price, qty), sắp theo giá tốt nhất trước.
    """

    symbol: str
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]
    timestamp: datetime

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0][0] if self.bids else Decimal("0")

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0][0] if self.asks else Decimal("0")


class ExchangeClient(ABC):
    """Interface mà BybitClient và CCXTClient đều phải implement."""

    @abstractmethod
    def get_balance(self) -> Balance: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_instrument_rules(self, symbol: str) -> InstrumentRules: ...

    @abstractmethod
    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame: ...

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_open_orders(self) -> list[Order]: ...

    @abstractmethod
    def get_orderbook(self, symbol: str) -> OrderBook: ...
