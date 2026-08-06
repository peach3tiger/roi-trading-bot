"""tests.test_market_data — MarketDataService: heartbeat/feed-alive, uỷ quyền.

File MỚI. `is_feed_alive()` là cơ chế bắt "mất WebSocket im lặng" duy nhất
(Brain-Crypto-Bybit.md §6.6) — sai ngưỡng ở đây nghĩa là bot giao dịch mù
trên dữ liệu cũ mà không biết, đáng để test thật.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd
import pytest

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
from data.market_data import MarketDataService

_SYMBOL = "BTCUSDT"


class _FakeExchange(ExchangeClient):
    def __init__(self) -> None:
        self.historical_calls: list[tuple[str, str, datetime, datetime]] = []
        self.subscribed: list[tuple[str, str]] = []
        self._kline_callback: Callable[[pd.Series], None] | None = None
        self.historical_df = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-08-01", tz="UTC")]),
        )

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        raise NotImplementedError

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        self.historical_calls.append((symbol, interval, start, end))
        return self.historical_df

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    def get_orderbook(self, symbol: str) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            bids=[(Decimal("64000"), Decimal("1"))],
            asks=[(Decimal("64010"), Decimal("1"))],
            timestamp=datetime.now(timezone.utc),
        )

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        self.subscribed.append((symbol, interval))
        self._kline_callback = callback

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        raise NotImplementedError

    def fire_kline(self, kline: pd.Series) -> None:
        """Test helper — mô phỏng một bar mới tới qua WebSocket."""
        assert self._kline_callback is not None, "chưa subscribe_klines"
        self._kline_callback(kline)


def _make_service() -> tuple[MarketDataService, _FakeExchange]:
    exchange = _FakeExchange()
    return MarketDataService(exchange, _SYMBOL, "1D"), exchange


# ----------------------------------------------------------------------
# Khởi tạo
# ----------------------------------------------------------------------


def test_rejects_unsupported_timeframe() -> None:
    with pytest.raises(ValueError, match="1H"):
        MarketDataService(_FakeExchange(), _SYMBOL, "1H")


# ----------------------------------------------------------------------
# Uỷ quyền cho ExchangeClient
# ----------------------------------------------------------------------


def test_get_historical_klines_delegates_with_symbol_and_timeframe() -> None:
    service, exchange = _make_service()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    service.get_historical_klines(start, end)

    assert exchange.historical_calls == [(_SYMBOL, "1D", start, end)]


def test_get_orderbook_delegates_to_exchange_client() -> None:
    service, _ = _make_service()
    ob = service.get_orderbook()
    assert ob.symbol == _SYMBOL
    assert ob.best_bid == Decimal("64000")
    assert ob.best_ask == Decimal("64010")


# ----------------------------------------------------------------------
# Heartbeat / feed-alive (§6.6)
# ----------------------------------------------------------------------


def test_feed_not_alive_before_any_data_received() -> None:
    service, _ = _make_service()
    assert service.is_feed_alive() is False


def test_feed_alive_right_after_receiving_a_bar() -> None:
    service, exchange = _make_service()
    service.subscribe_klines(lambda k: None)

    bar = pd.Series({"open": 1, "high": 1, "low": 1, "close": 1}, name=pd.Timestamp.now(tz="UTC"))
    exchange.fire_kline(bar)

    assert service.is_feed_alive() is True


def test_feed_dead_after_more_than_2x_bar_period_silent() -> None:
    """`1D` -> ngưỡng chết là > 2 ngày không nhận bar mới (§6.6)."""
    service, exchange = _make_service()
    service.subscribe_klines(lambda k: None)
    exchange.fire_kline(pd.Series({"close": 1}, name=pd.Timestamp.now(tz="UTC")))
    assert service.is_feed_alive() is True

    # Giả lập thời gian trôi qua > 2x chu kỳ bar mà không có bar mới nào —
    # chỉnh thẳng _last_received_at thay vì mock datetime.now() toàn cục.
    service._last_received_at = datetime.now(timezone.utc) - timedelta(days=2, minutes=1)

    assert service.is_feed_alive() is False


def test_feed_alive_just_under_2x_bar_period_boundary() -> None:
    service, exchange = _make_service()
    service.subscribe_klines(lambda k: None)
    exchange.fire_kline(pd.Series({"close": 1}, name=pd.Timestamp.now(tz="UTC")))

    # Cách biên `2 * bar_period` một khoảng an toàn (1 phút) — kiểm tra
    # đúng phía "còn sống" của ngưỡng mà không phụ thuộc thời gian thực
    # thi giữa lúc set và lúc gọi is_feed_alive() (tránh flaky ở đúng biên).
    service._last_received_at = datetime.now(timezone.utc) - timedelta(days=2) + timedelta(minutes=1)

    assert service.is_feed_alive() is True


def test_subscribe_klines_forwards_kline_to_caller_callback() -> None:
    service, exchange = _make_service()
    received: list[pd.Series] = []
    service.subscribe_klines(received.append)

    kline = pd.Series({"close": 64000.0}, name=pd.Timestamp.now(tz="UTC"))
    exchange.fire_kline(kline)

    assert len(received) == 1
    assert received[0]["close"] == 64000.0


# ----------------------------------------------------------------------
# get_latest_kline
# ----------------------------------------------------------------------


def test_get_latest_kline_returns_cached_bar_after_subscribe() -> None:
    service, exchange = _make_service()
    service.subscribe_klines(lambda k: None)
    kline = pd.Series({"close": 65000.0}, name=pd.Timestamp.now(tz="UTC"))
    exchange.fire_kline(kline)

    assert service.get_latest_kline()["close"] == 65000.0


def test_get_latest_kline_falls_back_to_rest_when_never_subscribed() -> None:
    service, exchange = _make_service()
    latest = service.get_latest_kline()
    assert latest["close"] == exchange.historical_df["close"].iloc[-1]
    assert exchange.historical_calls  # đã gọi REST để bootstrap


def test_get_latest_kline_raises_when_no_data_anywhere() -> None:
    service, exchange = _make_service()
    exchange.historical_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    with pytest.raises(RuntimeError):
        service.get_latest_kline()
