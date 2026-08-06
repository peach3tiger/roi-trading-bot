"""broker.base — ExchangeClient ABC.

Thay đổi kiến trúc quan trọng nhất so với bản gốc: bản gốc gắn chặt vào
Alpaca. Interface trừu tượng này tách tầng strategy/risk khỏi bất kỳ SDK
sàn cụ thể nào, để đổi sàn hoặc chuyển spot → perps không phải viết lại
tầng trên. Tầng strategy và risk KHÔNG BAO GIỜ import trực tiếp `pybit`
hay `ccxt` — chỉ qua interface này.
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

    @abstractmethod
    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None: ...

    @abstractmethod
    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None: ...
