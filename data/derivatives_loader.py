"""data.derivatives_loader — funding rate & open interest từ Bybit v5.

MỚI — không có trong spec gốc, thuộc Tầng 2 của feature engineering
(§2.3 của docs/Brain-Crypto-Bybit.md). ĐỂ RIÊNG, CHƯA DÙNG Ở PHASE 3:
funding/OI có lịch sử ngắn hơn giá rất nhiều (BTCUSDT trên Bybit có dữ
liệu funding từ ~04/2020, giá qua Binance có từ 2017) — bật tầng này sẽ
rút ngắn khoảng backtest khả dụng. Chỉ bật sau khi ablation test chứng
minh giá trị (xem CLAUDE.md bất biến #13).

Không cần API key — đây là market data công khai của hợp đồng linear
perpetual (category=linear). Dùng ccxt's implicit API cho Bybit thay vì
thư viện `requests` riêng, để không thêm dependency ngoài spec.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import ccxt
import pandas as pd

_MAX_LIMIT = 200
_MAX_KLINE_LIMIT = 1000
_DEFAULT_PROBE_START = datetime(2018, 1, 1, tzinfo=timezone.utc)

OpenInterestInterval = Literal["5min", "15min", "30min", "1h", "4h", "1d"]


@dataclass(frozen=True)
class AvailableRange:
    symbol: str
    earliest: datetime | None
    latest: datetime | None
    n_records: int


class DerivativesLoader:
    """Tải funding rate và open interest công khai từ Bybit v5 (category=linear)."""

    def __init__(self, category: str = "linear") -> None:
        self._category = category
        self._exchange = ccxt.bybit({"enableRateLimit": True})

    # ------------------------------------------------------------------
    # Funding rate
    # ------------------------------------------------------------------

    def load_funding_rate(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Phân trang LÙI theo `endTime` — endpoint này không có cursor,
        trả về mới nhất trước, tối đa 200 bản ghi/request."""
        rows = self._paginate_funding_backward(symbol, start, end)
        return self._funding_rows_to_frame(rows)

    def get_funding_rate_available_range(
        self, symbol: str, probe_start: datetime = _DEFAULT_PROBE_START
    ) -> AvailableRange:
        """Kéo toàn bộ lịch sử funding rate từ probe_start tới hiện tại và
        báo cáo khoảng thời gian thực tế có dữ liệu."""
        df = self.load_funding_rate(symbol, probe_start, datetime.now(timezone.utc))
        if df.empty:
            return AvailableRange(symbol, None, None, 0)
        return AvailableRange(
            symbol, df.index.min().to_pydatetime(), df.index.max().to_pydatetime(), len(df)
        )

    def _paginate_funding_backward(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[dict]:
        rows: list[dict] = []
        cursor_end_ms = int(end.timestamp() * 1000)
        start_ms = int(start.timestamp() * 1000)

        while cursor_end_ms >= start_ms:
            params = {
                "category": self._category,
                "symbol": symbol,
                "endTime": cursor_end_ms,
                "limit": _MAX_LIMIT,
            }
            response = self._exchange.publicGetV5MarketFundingHistory(params)
            page = response["result"]["list"]
            if not page:
                break
            rows.extend(page)

            oldest_ms = min(int(r["fundingRateTimestamp"]) for r in page)
            if oldest_ms < start_ms or len(page) < _MAX_LIMIT:
                break
            next_end_ms = oldest_ms - 1
            if next_end_ms >= cursor_end_ms:
                break
            cursor_end_ms = next_end_ms
            time.sleep(self._exchange.rateLimit / 1000)

        return rows

    @staticmethod
    def _funding_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            empty = pd.DataFrame(columns=["funding_rate"])
            empty.index = pd.DatetimeIndex([], name="timestamp", tz="UTC")
            return empty
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(
            df["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True
        )
        df["funding_rate"] = df["fundingRate"].astype(float)
        df = df.set_index("timestamp")[["funding_rate"]].sort_index()
        return df[~df.index.duplicated(keep="last")]

    # ------------------------------------------------------------------
    # Open interest
    # ------------------------------------------------------------------

    def load_open_interest(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: OpenInterestInterval = "1d",
    ) -> pd.DataFrame:
        """Phân trang qua `nextPageCursor` do chính API trả về."""
        rows = self._paginate_open_interest(symbol, start, end, interval)
        return self._oi_rows_to_frame(rows)

    def get_open_interest_available_range(
        self,
        symbol: str,
        interval: OpenInterestInterval = "1d",
        probe_start: datetime = _DEFAULT_PROBE_START,
    ) -> AvailableRange:
        """Dùng granularity thô (mặc định 1d) để khảo sát khoảng khả dụng
        mà không kéo hàng chục nghìn điểm dữ liệu chi tiết."""
        df = self.load_open_interest(symbol, probe_start, datetime.now(timezone.utc), interval)
        if df.empty:
            return AvailableRange(symbol, None, None, 0)
        return AvailableRange(
            symbol, df.index.min().to_pydatetime(), df.index.max().to_pydatetime(), len(df)
        )

    def _paginate_open_interest(
        self, symbol: str, start: datetime, end: datetime, interval: OpenInterestInterval
    ) -> list[dict]:
        rows: list[dict] = []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        cursor: str | None = None

        while True:
            params = {
                "category": self._category,
                "symbol": symbol,
                "intervalTime": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": _MAX_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            response = self._exchange.publicGetV5MarketOpenInterest(params)
            result = response["result"]
            page = result["list"]
            rows.extend(page)
            cursor = result.get("nextPageCursor") or None
            if not cursor or not page:
                break
            time.sleep(self._exchange.rateLimit / 1000)

        return rows

    @staticmethod
    def _oi_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            empty = pd.DataFrame(columns=["open_interest"])
            empty.index = pd.DatetimeIndex([], name="timestamp", tz="UTC")
            return empty
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        df["open_interest"] = df["openInterest"].astype(float)
        df = df.set_index("timestamp")[["open_interest"]].sort_index()
        return df[~df.index.duplicated(keep="last")]

    # ------------------------------------------------------------------
    # Perp close (cho perp_spot_basis — spot lấy từ HistoryLoader/Binance
    # riêng, ở đây chỉ lấy giá đóng cửa của hợp đồng linear perpetual)
    # ------------------------------------------------------------------

    def load_perp_close(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Giá đóng cửa daily của hợp đồng linear perpetual (`/v5/market/kline`,
        category=linear) — không phải fetch_ohlcv chuẩn hoá của ccxt, dùng
        raw endpoint để nhất quán với `load_funding_rate`/`load_open_interest`.
        Phân trang LÙI theo `end`, không có cursor (giống funding rate)."""
        rows = self._paginate_kline_backward(symbol, start, end)
        return self._kline_rows_to_frame(rows)

    def _paginate_kline_backward(self, symbol: str, start: datetime, end: datetime) -> list[list]:
        rows: list[list] = []
        cursor_end_ms = int(end.timestamp() * 1000)
        start_ms = int(start.timestamp() * 1000)

        while cursor_end_ms >= start_ms:
            params = {
                "category": self._category,
                "symbol": symbol,
                "interval": "D",
                "start": start_ms,
                "end": cursor_end_ms,
                "limit": _MAX_KLINE_LIMIT,
            }
            response = self._exchange.publicGetV5MarketKline(params)
            page = response["result"]["list"]
            if not page:
                break
            rows.extend(page)

            oldest_ms = min(int(r[0]) for r in page)
            if oldest_ms <= start_ms or len(page) < _MAX_KLINE_LIMIT:
                break
            next_end_ms = oldest_ms - 1
            if next_end_ms >= cursor_end_ms:
                break
            cursor_end_ms = next_end_ms
            time.sleep(self._exchange.rateLimit / 1000)

        return rows

    @staticmethod
    def _kline_rows_to_frame(rows: list[list]) -> pd.DataFrame:
        if not rows:
            empty = pd.DataFrame(columns=["perp_close"])
            empty.index = pd.DatetimeIndex([], name="timestamp", tz="UTC")
            return empty
        df = pd.DataFrame(rows, columns=["start", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["start"].astype("int64"), unit="ms", utc=True)
        df["perp_close"] = df["close"].astype(float)
        df = df.set_index("timestamp")[["perp_close"]].sort_index()
        return df[~df.index.duplicated(keep="last")]

    # ------------------------------------------------------------------
    # Bundle Tầng 2 — gộp funding/OI/perp thành một frame căn theo bar 1d
    # ------------------------------------------------------------------

    def load_tier2_bundle(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Gộp funding_rate (8h -> daily mean), open_interest (đã 1d), và
        perp_close (đã 1d) thành một DataFrame căn theo mốc 00:00 UTC — khớp
        với OHLCV daily offset0 mặc định của `HistoryLoader`. Dùng
        `bar_offset_hours` khác 0 với dữ liệu Tầng 2 CHƯA được hỗ trợ (Bybit
        không có tham số timeZone cho các endpoint này như Binance klines).
        """
        funding = self.load_funding_rate(symbol, start, end)
        oi = self.load_open_interest(symbol, start, end, interval="1d")
        perp = self.load_perp_close(symbol, start, end)

        funding_daily = funding["funding_rate"].resample("1D").mean()
        funding_daily.index.name = "timestamp"

        combined = pd.DataFrame(
            {
                "funding_rate": funding_daily,
                "open_interest": oi["open_interest"],
                "perp_close": perp["perp_close"],
            }
        )
        return combined.sort_index()
