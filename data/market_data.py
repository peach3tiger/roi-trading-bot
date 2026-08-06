"""data.market_data — kline lịch sử/live và orderbook qua ExchangeClient."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from broker.base import ExchangeClient, OrderBook

logger = logging.getLogger(__name__)

# "1D" là timeframe DUY NHẤT hệ thống dùng (settings.yaml: exchange.timeframe).
# Thêm khung khác khi thật sự cần, không đoán trước — cùng nguyên tắc với
# _INTERVAL_MAP của broker/bybit_client.py.
_TIMEFRAME_TO_TIMEDELTA = {"1D": timedelta(days=1), "D": timedelta(days=1)}


class MarketDataService:
    """Heartbeat WebSocket bắt buộc: Bybit ngắt kết nối im lặng. Nếu không
    nhận dữ liệu quá 2x chu kỳ bar, coi như mất feed — tạm dừng signal,
    cảnh báo, nhưng không dừng stop loss.
    """

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str) -> None:
        if timeframe not in _TIMEFRAME_TO_TIMEDELTA:
            raise ValueError(f"Chưa hỗ trợ timeframe {timeframe!r} — chỉ {list(_TIMEFRAME_TO_TIMEDELTA)}")
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self._bar_period = _TIMEFRAME_TO_TIMEDELTA[timeframe]

        self._latest_kline: pd.Series | None = None
        self._last_received_at: datetime | None = None

    def get_historical_klines(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Có phân trang — uỷ quyền hoàn toàn cho
        `ExchangeClient.get_historical_klines`, vốn đã tự phân trang (xem
        `BybitClient.get_historical_klines`)."""
        return self.exchange_client.get_historical_klines(self.symbol, self.timeframe, start, end)

    def subscribe_klines(self, callback: Callable[[pd.Series], None]) -> None:
        """Bọc callback của caller để cập nhật heartbeat
        (`_last_received_at`/`_latest_kline`) TRƯỚC khi gọi callback thật —
        `is_feed_alive()` phải phản ánh đúng lần nhận gần nhất bất kể
        callback của caller có lỗi hay không."""

        def _tracking_callback(kline: pd.Series) -> None:
            self._latest_kline = kline
            self._last_received_at = datetime.now(timezone.utc)
            callback(kline)

        self.exchange_client.subscribe_klines(self.symbol, self.timeframe, _tracking_callback)

    def get_latest_kline(self) -> pd.Series:
        """Trả bar mới nhất đã nhận qua `subscribe_klines`; nếu chưa
        subscribe bao giờ (vd. mới khởi động), tải một bar gần nhất qua
        REST thay vì raise — vẫn hữu ích để bootstrap trạng thái ban đầu."""
        if self._latest_kline is not None:
            return self._latest_kline

        end = datetime.now(timezone.utc)
        start = end - self._bar_period * 3
        df = self.get_historical_klines(start, end)
        if df.empty:
            raise RuntimeError(
                f"Chưa có kline nào (chưa subscribe, và REST cũng không trả bar nào cho {self.symbol})"
            )
        last_row = df.iloc[-1]
        last_row.name = df.index[-1]
        return last_row

    def get_orderbook(self) -> OrderBook:
        """Để kiểm tra spread trước khi risk_manager duyệt lệnh.

        Trả `OrderBook` (broker/base.py), không phải `dict` thô như spec
        pseudocode gốc — nhất quán với phần còn lại của dự án (dataclass
        cho mọi cấu trúc dữ liệu, CLAUDE.md phong cách code); `OrderBook.
        best_bid`/`.best_ask` đủ cho `RiskManager.check_spread(bid, ask)`.
        """
        return self.exchange_client.get_orderbook(self.symbol)

    def is_feed_alive(self) -> bool:
        """False nếu không nhận dữ liệu quá 2x chu kỳ bar, hoặc chưa từng
        nhận bar nào (chưa subscribe — coi là "chưa sống" chứ không phải
        "đã chết", nhưng kết quả bool giống nhau: không tin cậy để giao
        dịch)."""
        if self._last_received_at is None:
            return False
        return datetime.now(timezone.utc) - self._last_received_at <= 2 * self._bar_period
