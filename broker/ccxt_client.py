"""broker.ccxt_client — CCXTClient: fallback đa sàn + nguồn dữ liệu lịch sử.

Bybit spot BTC/USDT chỉ có dữ liệu từ ~2021. Backtest dùng CCXT tải OHLCV
từ Binance (có từ 2017) để có đủ chiều dài lịch sử cho walk-forward; live
execution vẫn qua BybitClient. Chênh lệch giá giữa hai sàn ở BTC/USDT
không đáng kể so với sai số của chính chiến lược.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from broker.base import (
    Balance,
    ExchangeClient,
    Order,
    OrderBook,
    OrderRequest,
    OrderResult,
    Position,
)
from broker.instrument_rules import InstrumentRules


class CCXTClient(ExchangeClient):
    """Implement ExchangeClient qua ccxt — chủ yếu dùng để tải dữ liệu
    lịch sử dài hạn; submit_order/cancel_order có thể không được hỗ trợ
    đầy đủ tuỳ sàn, chỉ dùng khi BybitClient không khả dụng.
    """

    def __init__(
        self, exchange_id: str, api_key: str | None = None, api_secret: str | None = None
    ) -> None:
        ...

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        raise NotImplementedError

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Tải có phân trang, mỗi request tối đa ~1000 bar."""
        raise NotImplementedError

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    def get_orderbook(self, symbol: str) -> OrderBook:
        raise NotImplementedError

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        raise NotImplementedError

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        raise NotImplementedError
