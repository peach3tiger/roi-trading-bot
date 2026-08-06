"""tests.test_market_data — MarketDataService: poll REST, uỷ quyền cho ExchangeClient.

REST polling thay WebSocket (2026-08-06, xem `docs/DECISIONS.md`) — không
còn heartbeat/`is_feed_alive` để test; mỗi `get_latest_kline()` là một lần
gọi REST trực tiếp, kiểm tra bằng cách đếm/xác nhận nội dung lời gọi
`get_historical_klines` bên dưới.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

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
# get_latest_kline — REST trực tiếp mỗi lần gọi, không cache
# ----------------------------------------------------------------------


def test_get_latest_kline_calls_rest_every_time() -> None:
    service, exchange = _make_service()

    service.get_latest_kline()
    service.get_latest_kline()

    assert len(exchange.historical_calls) == 2, "không cache — mỗi lần gọi phải là một REST call mới"


def test_get_latest_kline_returns_last_row_of_rest_result() -> None:
    service, exchange = _make_service()
    exchange.historical_df = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 65000.0],
            "volume": [1.0, 1.0],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp("2026-08-04", tz="UTC"), pd.Timestamp("2026-08-05", tz="UTC")]
        ),
    )

    latest = service.get_latest_kline()

    assert latest["close"] == 65000.0
    assert latest.name == pd.Timestamp("2026-08-05", tz="UTC")


def test_get_latest_kline_requests_a_window_covering_at_least_three_bars() -> None:
    service, exchange = _make_service()

    service.get_latest_kline()

    (_, _, start, end) = exchange.historical_calls[0]
    assert end - start >= service._bar_period * 3


def test_get_latest_kline_raises_when_no_data_anywhere() -> None:
    service, exchange = _make_service()
    exchange.historical_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    with pytest.raises(RuntimeError):
        service.get_latest_kline()
