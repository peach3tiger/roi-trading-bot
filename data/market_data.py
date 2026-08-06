"""data.market_data — kline lịch sử/mới nhất và orderbook qua ExchangeClient.

REST polling, không WebSocket — quyết định kiến trúc 2026-08-06 (xem
`docs/DECISIONS.md`, mục "Đổi sàn Bybit -> Binance (ccxt)"): bot chạy bar
`1D`, main loop poll `get_latest_kline()` mỗi 30-60s là đủ, không cần công
nghệ tần suất cao của WebSocket. Đã bỏ toàn bộ heartbeat/`is_feed_alive`/
cache bar mới nhất (`_latest_kline`/`_last_received_at`) — với polling
không có khái niệm "mất kết nối im lặng" cần phát hiện: mỗi lần gọi
`get_latest_kline()` LÀ một lần hỏi sàn trực tiếp, không có gì để "chết"
giữa hai lần gọi. Nếu REST call thất bại, nó raise ngay tại chỗ gọi (qua
`ExchangeClient` implementation, vd. `CCXTClient._call_with_retry`) —
không cần một cơ chế phát hiện riêng ở tầng này.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from broker.base import ExchangeClient, OrderBook

logger = logging.getLogger(__name__)

# "1D" là timeframe DUY NHẤT hệ thống dùng (settings.yaml: exchange.timeframe).
# Thêm khung khác khi thật sự cần, không đoán trước — cùng nguyên tắc với
# _INTERVAL_MAP của broker/bybit_client.py.
_TIMEFRAME_TO_TIMEDELTA = {"1D": timedelta(days=1), "D": timedelta(days=1)}


class MarketDataService:
    """Bọc `ExchangeClient` cho tầng chiến lược — poll REST, không WebSocket."""

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str) -> None:
        if timeframe not in _TIMEFRAME_TO_TIMEDELTA:
            raise ValueError(f"Chưa hỗ trợ timeframe {timeframe!r} — chỉ {list(_TIMEFRAME_TO_TIMEDELTA)}")
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self._bar_period = _TIMEFRAME_TO_TIMEDELTA[timeframe]

    def get_historical_klines(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Có phân trang — uỷ quyền hoàn toàn cho
        `ExchangeClient.get_historical_klines`, vốn đã tự phân trang (xem
        `CCXTClient.get_historical_klines`)."""
        return self.exchange_client.get_historical_klines(self.symbol, self.timeframe, start, end)

    def get_latest_kline(self) -> pd.Series:
        """Bar mới nhất, qua REST trực tiếp mỗi lần gọi — không cache.

        Tải 3 bar gần nhất (không chỉ 1) để có dư nếu sàn chưa đóng nến
        mới nhất tại thời điểm gọi; luôn lấy dòng cuối cùng của kết quả."""
        end = datetime.now(timezone.utc)
        start = end - self._bar_period * 3
        df = self.get_historical_klines(start, end)
        if df.empty:
            raise RuntimeError(f"REST không trả bar nào cho {self.symbol}")
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
