"""data.history_loader — tải & cache OHLCV dài hạn qua CCXT, kiểm tra toàn vẹn.

Cache ra parquet, chỉ tải phần thiếu ở lần chạy sau — tránh tải lại toàn
bộ lịch sử mỗi lần backtest, và tránh rate limit của sàn nguồn dữ liệu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from broker.ccxt_client import CCXTClient


@dataclass(frozen=True)
class DataIntegrityReport:
    missing_bars: int
    duplicate_timestamps: int
    zero_volume_bars: int
    is_valid: bool


class HistoryLoader:
    def __init__(self, ccxt_client: CCXTClient, cache_dir: Path) -> None:
        ...

    def load(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Đọc từ cache parquet, chỉ tải phần thiếu qua CCXT (phân trang ~1000 bar/request)."""
        raise NotImplementedError

    def check_integrity(self, ohlcv: pd.DataFrame, timeframe: str) -> DataIntegrityReport:
        """Không thiếu bar, không trùng timestamp, không bar volume = 0."""
        raise NotImplementedError

    def write_metadata(self, symbol: str, source: str, start: datetime, end: datetime) -> None:
        """Ghi nguồn dữ liệu và khoảng thời gian vào metadata của mỗi backtest."""
        raise NotImplementedError
