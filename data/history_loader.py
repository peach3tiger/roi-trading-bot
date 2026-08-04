"""data.history_loader — tải & cache OHLCV dài hạn qua CCXT (Binance), kiểm tra toàn vẹn.

Bybit spot BTC/USDT chỉ có dữ liệu từ ~2021. Backtest cần nhiều hơn, nên
tải từ Binance (BTC/USDT có từ 2017) qua endpoint raw của Binance thay vì
`fetch_ohlcv` chuẩn hoá của ccxt — bản chuẩn hoá bỏ mất `trade_count`, thứ
spec dùng thay cho volume (xem §2.3 của docs/Brain-Crypto-Bybit.md: volume
trên sàn crypto bị bóp méo bởi wash trading).

Cache ra parquet trong `data/cache/`, chỉ tải phần thiếu ở lần chạy sau.
Kiểm tra toàn vẹn chạy lại trên TOÀN BỘ dữ liệu đã cache mỗi lần load —
không chỉ phần mới tải — để bắt được cache bị hỏng ở giữa file, không chỉ
ở phần đuôi mới nối vào.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

_MAX_BARS_PER_REQUEST = 1000

# Chỉ hỗ trợ bar ngày — đây là khung thời gian mặc định của spec (§2.1).
# bar_offset_hours dịch mốc "nửa đêm" của Binance bằng tham số `timeZone`
# của chính API Binance, không phải resample thủ công — xem ghi chú ở
# _fetch_range() để biết vì sao cách này chính xác hơn.
_SUPPORTED_TIMEFRAMES = {"1D", "1d"}
_BAR_DELTA = timedelta(days=1)


class DataIntegrityError(Exception):
    """Dữ liệu OHLCV không đạt kiểm tra toàn vẹn — không được dùng để backtest."""

    def __init__(self, report: "DataIntegrityReport") -> None:
        self.report = report
        super().__init__(
            "Kiểm tra toàn vẹn dữ liệu thất bại: "
            + "; ".join(report.issues)
        )


@dataclass(frozen=True)
class DataIntegrityReport:
    missing_bars: int
    duplicate_timestamps: int
    zero_volume_bars: int
    invalid_ohlc_bars: int
    extreme_move_bars: int
    is_valid: bool
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoadMetadata:
    symbol: str
    timeframe: str
    source: str
    bar_offset_hours: int
    start: datetime
    end: datetime
    n_bars: int
    data_hash: str
    fetched_at: datetime

    def to_json_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "bar_offset_hours": self.bar_offset_hours,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "n_bars": self.n_bars,
            "data_hash": self.data_hash,
            "fetched_at": self.fetched_at.isoformat(),
        }


class HistoryLoader:
    """Tải OHLCV lịch sử dài hạn qua Binance, cache ra parquet, kiểm tra toàn vẹn.

    Đi thẳng qua ccxt's raw/implicit API (`publicGetKlines`) thay vì
    `fetch_ohlcv` chuẩn hoá, vì cần giữ `trade_count` (index 8 của kline
    Binance) — không có trong OHLCV chuẩn hoá của ccxt.
    """

    def __init__(self, exchange_id: str = "binance", cache_dir: Path | str = "data/cache") -> None:
        self._exchange_id = exchange_id
        self._exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        bar_offset_hours: int = 0,
    ) -> pd.DataFrame:
        """Đọc từ cache parquet, chỉ tải phần thiếu qua Binance (phân
        trang ~1000 bar/request), rồi kiểm tra toàn vẹn trên toàn bộ dữ
        liệu đã cache trong khoảng [start, end].

        Raises:
            DataIntegrityError: nếu dữ liệu (cache cũ hoặc mới tải) không
                đạt kiểm tra toàn vẹn — cache hỏng giữa file cũng bị bắt
                ở đây vì kiểm tra chạy trên toàn bộ dữ liệu, không chỉ
                phần mới nối vào.
        """
        if timeframe not in _SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Chỉ hỗ trợ timeframe 1D, nhận: {timeframe!r}")
        if bar_offset_hours not in (0, 6, 12, 18):
            raise ValueError(f"bar_offset_hours phải là 0/6/12/18, nhận: {bar_offset_hours}")

        start = self._ensure_utc(start)
        end = self._ensure_utc(end)

        cache_path = self._cache_path(symbol, bar_offset_hours)
        cached = self._read_cache(cache_path)

        # Mở rộng cache theo CẢ hai hướng: về sau (bar mới kể từ lần tải
        # trước) và về trước (nếu `start` yêu cầu sớm hơn dữ liệu đang có
        # trong cache — ví dụ lần đầu chỉ tải một khoảng ngắn để test, sau
        # đó gọi lại với `start` xa hơn). Bỏ chiều nào cũng âm thầm trả về
        # ít dữ liệu hơn những gì caller yêu cầu mà không báo lỗi.
        new_pieces: list[pd.DataFrame] = [cached] if cached is not None else []

        if cached is not None and not cached.empty and start < cached.index.min():
            backfill_end = cached.index.min() - _BAR_DELTA
            if start <= backfill_end:
                backfill = self._fetch_range(symbol, start, backfill_end, bar_offset_hours)
                if not backfill.empty:
                    new_pieces.append(backfill)

        fetch_start = start
        if cached is not None and not cached.empty:
            fetch_start = max(start, cached.index.max() + _BAR_DELTA)

        if fetch_start <= end:
            fresh = self._fetch_range(symbol, fetch_start, end, bar_offset_hours)
            if not fresh.empty:
                new_pieces.append(fresh)

        if len(new_pieces) > 1 or (len(new_pieces) == 1 and cached is None):
            combined = pd.concat(new_pieces)
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            self._write_cache(combined, cache_path)
            cached = combined

        if cached is None or cached.empty:
            return cached if cached is not None else pd.DataFrame()

        windowed = cached.loc[(cached.index >= start) & (cached.index <= end)].copy()

        report = self.check_integrity(windowed)
        if not report.is_valid:
            raise DataIntegrityError(report)

        self.write_metadata(
            symbol=symbol,
            timeframe=timeframe,
            source=f"{self._exchange_id} (raw klines)",
            bar_offset_hours=bar_offset_hours,
            start=windowed.index.min().to_pydatetime(),
            end=windowed.index.max().to_pydatetime(),
            df=windowed,
        )

        return windowed

    def check_integrity(self, ohlcv: pd.DataFrame) -> DataIntegrityReport:
        """Không thiếu bar, không trùng timestamp, không bar volume = 0,
        OHLC hợp lệ (high >= low, close trong [low, high]), không có
        bước nhảy giá > 50% trong một bar — đó nhiều khả năng là lỗi dữ
        liệu hơn là một sự kiện thị trường thật.
        """
        issues: list[str] = []

        if ohlcv.empty:
            return DataIntegrityReport(0, 0, 0, 0, 0, is_valid=True, issues=[])

        duplicate_timestamps = int(ohlcv.index.duplicated().sum())
        if duplicate_timestamps:
            issues.append(f"{duplicate_timestamps} timestamp trùng lặp")

        deduped_index = ohlcv.index[~ohlcv.index.duplicated(keep="first")].sort_values()
        expected_bars = int(round((deduped_index.max() - deduped_index.min()) / _BAR_DELTA)) + 1
        missing_bars = max(0, expected_bars - len(deduped_index))
        if missing_bars:
            issues.append(f"{missing_bars} bar bị thiếu (khoảng trống trong chuỗi timestamp)")

        zero_volume_bars = int((ohlcv["volume"] == 0).sum())
        if zero_volume_bars:
            issues.append(f"{zero_volume_bars} bar có volume = 0")

        invalid_ohlc = (ohlcv["high"] < ohlcv["low"]) | (ohlcv["close"] > ohlcv["high"]) | (
            ohlcv["close"] < ohlcv["low"]
        )
        invalid_ohlc_bars = int(invalid_ohlc.sum())
        if invalid_ohlc_bars:
            issues.append(f"{invalid_ohlc_bars} bar có OHLC không hợp lệ")

        pct_change = ohlcv["close"].sort_index().pct_change().abs()
        extreme_move_bars = int((pct_change > 0.50).sum())
        if extreme_move_bars:
            issues.append(f"{extreme_move_bars} bar có giá nhảy > 50% — nghi ngờ lỗi dữ liệu")

        is_valid = not issues
        return DataIntegrityReport(
            missing_bars=missing_bars,
            duplicate_timestamps=duplicate_timestamps,
            zero_volume_bars=zero_volume_bars,
            invalid_ohlc_bars=invalid_ohlc_bars,
            extreme_move_bars=extreme_move_bars,
            is_valid=is_valid,
            issues=issues,
        )

    def write_metadata(
        self,
        symbol: str,
        timeframe: str,
        source: str,
        bar_offset_hours: int,
        start: datetime,
        end: datetime,
        df: pd.DataFrame,
    ) -> None:
        """Ghi nguồn dữ liệu, khoảng thời gian, số bar, và hash dữ liệu vào
        metadata JSON cạnh file cache — bằng chứng tái lập cho mỗi backtest."""
        metadata = LoadMetadata(
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            bar_offset_hours=bar_offset_hours,
            start=start,
            end=end,
            n_bars=len(df),
            data_hash=self._compute_data_hash(df),
            fetched_at=datetime.now(timezone.utc),
        )
        path = self._metadata_path(symbol, bar_offset_hours)
        path.write_text(json.dumps(metadata.to_json_dict(), indent=2, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Fetch / pagination
    # ------------------------------------------------------------------

    def _fetch_range(
        self, symbol: str, start: datetime, end: datetime, bar_offset_hours: int
    ) -> pd.DataFrame:
        """Phân trang qua Binance raw klines, tối đa 1000 bar/request.

        `bar_offset_hours` dùng tham số `timeZone` CỦA CHÍNH Binance thay
        vì resample thủ công từ dữ liệu 1h: Binance dịch mốc "nửa đêm"
        dùng để gộp bar 1d theo `timeZone` được yêu cầu, cho ra đúng bộ
        bar mà sàn coi là "1 ngày" ở múi giờ đó — chính xác hơn và rẻ hơn
        nhiều so với tự gộp từ granularity nhỏ hơn.
        """
        binance_symbol = symbol.replace("/", "")
        time_zone = "0:00" if bar_offset_hours == 0 else f"-{bar_offset_hours}:00"

        all_rows: list[list] = []
        cursor_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        while cursor_ms <= end_ms:
            params = {
                "symbol": binance_symbol,
                "interval": "1d",
                "startTime": cursor_ms,
                "endTime": end_ms,
                "timeZone": time_zone,
                "limit": _MAX_BARS_PER_REQUEST,
            }
            rows = self._exchange.publicGetKlines(params)
            if not rows:
                break
            all_rows.extend(rows)
            last_open_ms = int(rows[-1][0])
            next_cursor_ms = last_open_ms + int(_BAR_DELTA.total_seconds() * 1000)
            if next_cursor_ms <= cursor_ms:
                break
            cursor_ms = next_cursor_ms
            if len(rows) < _MAX_BARS_PER_REQUEST:
                break
            time.sleep(self._exchange.rateLimit / 1000)

        return self._rows_to_frame(all_rows)

    @staticmethod
    def _rows_to_frame(rows: list[list]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trade_count",
                "taker_buy_base_volume",
                "taker_buy_quote_volume",
                "ignore",
            ],
        )
        df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "quote_volume",
                    "taker_buy_base_volume", "taker_buy_quote_volume"]:
            df[col] = df[col].astype(float)
        df["trade_count"] = df["trade_count"].astype("int64")
        df = df.set_index("timestamp")
        return df[
            ["open", "high", "low", "close", "volume", "quote_volume",
             "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]
        ].sort_index()

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------

    def _cache_path(self, symbol: str, bar_offset_hours: int) -> Path:
        safe_symbol = symbol.replace("/", "")
        return self._cache_dir / f"{safe_symbol}_1d_offset{bar_offset_hours}.parquet"

    def _metadata_path(self, symbol: str, bar_offset_hours: int) -> Path:
        return self._cache_path(symbol, bar_offset_hours).with_suffix(".meta.json")

    @staticmethod
    def _read_cache(path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        return pd.read_parquet(path)

    @staticmethod
    def _write_cache(df: pd.DataFrame, path: Path) -> None:
        df.to_parquet(path)

    @staticmethod
    def _compute_data_hash(df: pd.DataFrame) -> str:
        hasher = hashlib.sha256()
        hasher.update(pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes())
        return hasher.hexdigest()

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
