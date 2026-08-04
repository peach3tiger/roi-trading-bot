"""data.market_data — kline lịch sử/live và orderbook qua ExchangeClient."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from broker.base import ExchangeClient


class MarketDataService:
    """Heartbeat WebSocket bắt buộc: Bybit ngắt kết nối im lặng. Nếu không
    nhận dữ liệu quá 2x chu kỳ bar, coi như mất feed — tạm dừng signal,
    cảnh báo, nhưng không dừng stop loss.
    """

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str) -> None:
        ...

    def get_historical_klines(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Có phân trang."""
        raise NotImplementedError

    def subscribe_klines(self, callback: Callable[[pd.Series], None]) -> None:
        raise NotImplementedError

    def get_latest_kline(self) -> pd.Series:
        raise NotImplementedError

    def get_orderbook(self) -> dict:
        """Để kiểm tra spread trước khi risk_manager duyệt lệnh."""
        raise NotImplementedError

    def is_feed_alive(self) -> bool:
        """False nếu không nhận dữ liệu quá 2x chu kỳ bar."""
        raise NotImplementedError
