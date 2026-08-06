"""tests.test_ccxt_client — retry whitelist, live-confirmation, mapping
response ccxt/Binance -> dataclass nội bộ.

Dùng `exchange` giả (tiêm qua `CCXTClient(..., exchange=...)`) — KHÔNG cần
mạng/API key thật. Response giả lập khớp đúng shape đã xác nhận bằng gọi
thật tới `testnet.binance.vision` (public endpoint: `load_markets`/
`fetch_ohlcv`/`fetch_order_book`) lúc viết `broker/ccxt_client.py` — xem
comment ở từng fixture. Phần cần auth (`fetch_balance`/`create_order`/
`cancel_order`/`fetch_open_orders`) theo đúng ccxt unified structure đã
xác nhận bằng introspection (`ccxt/binance.py`, `ccxt/base/exchange.py`
`safe_balance`), không suy đoán.

`time.sleep` bị monkeypatch thành no-op trong toàn file — các test retry/
backoff không cần chờ thời gian thật.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import ccxt
import pytest

from broker.base import OrderRequest, OrderSide, OrderStatus, OrderType
from broker.ccxt_client import CCXTClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


# Shape khớp thật (xem docstring module): "BTC/USDT" (spot) VÀ
# "BTC/USDT:USDT" (swap) CÙNG tồn tại trong markets_by_id["BTCUSDT"] trên
# Binance thật — _to_ccxt_symbol() phải chọn đúng bản spot bằng cách tra
# theo unified symbol "BTC/USDT", không dựa vào thứ tự markets_by_id.
_MARKETS = {
    "BTC/USDT": {
        "symbol": "BTC/USDT",
        "type": "spot",
        "spot": True,
        "precision": {"amount": 1e-05, "price": 0.01, "quote": 1e-08},
        "limits": {"amount": {"min": 1e-05, "max": 9000.0}, "cost": {"min": 5.0}},
    },
    "BTC/USDT:USDT": {
        "symbol": "BTC/USDT:USDT",
        "type": "swap",
        "spot": False,
        "precision": {"amount": 0.0001, "price": 0.1, "quote": 1e-08},
        "limits": {"amount": {"min": 0.0001, "max": 1000.0}, "cost": {"min": 50.0}},
    },
}


class _FakeExchange:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.markets = dict(_MARKETS)
        self.sandbox_mode: bool | None = None
        self.raise_n_times: dict[str, list[Exception]] = {}
        self.balance = {
            "free": {"USDT": 9500.5, "BTC": 0.1},
            "used": {"USDT": 500.0, "BTC": 0.0},
            "total": {"USDT": 10000.5, "BTC": 0.1},
        }
        self.ticker_last = 65000.0
        self.ohlcv_page: list[list[Any]] = []
        self.order_book = {
            "bids": [[64000.0, 1.0], [63999.0, 2.0]],
            "asks": [[64010.0, 1.5]],
            "timestamp": 1785900000000,
        }
        self.open_orders: list[dict] = []
        self.create_order_response: dict = {}
        self.last_create_order_args: tuple[Any, ...] | None = None
        self.last_create_order_params: dict | None = None
        self.last_cancel_order_args: tuple[Any, ...] | None = None

    def _maybe_raise(self, name: str) -> None:
        queue = self.raise_n_times.get(name)
        if queue:
            raise queue.pop(0)

    def load_markets(self) -> None:
        self.calls.append("load_markets")

    def set_sandbox_mode(self, flag: bool) -> None:
        self.sandbox_mode = flag

    def fetch_balance(self) -> dict:
        self.calls.append("fetch_balance")
        self._maybe_raise("fetch_balance")
        return self.balance

    def fetch_ticker(self, symbol: str) -> dict:
        self.calls.append("fetch_ticker")
        self._maybe_raise("fetch_ticker")
        return {"last": self.ticker_last}

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list:
        self.calls.append("fetch_ohlcv")
        self._maybe_raise("fetch_ohlcv")
        return self.ohlcv_page

    def create_order(
        self, symbol: str, type: str, side: str, amount: str, price: Any, params: dict
    ) -> dict:
        self.calls.append("create_order")
        self.last_create_order_args = (symbol, type, side, amount, price)
        self.last_create_order_params = params
        self._maybe_raise("create_order")
        return self.create_order_response

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        self.calls.append("cancel_order")
        self.last_cancel_order_args = (order_id, symbol)
        self._maybe_raise("cancel_order")
        return {}

    def fetch_open_orders(self, symbol: str) -> list:
        self.calls.append("fetch_open_orders")
        self._maybe_raise("fetch_open_orders")
        return self.open_orders

    def fetch_order_book(self, symbol: str, limit: int) -> dict:
        self.calls.append("fetch_order_book")
        self._maybe_raise("fetch_order_book")
        return self.order_book


def _client(exchange: _FakeExchange | None = None, **kwargs: Any) -> CCXTClient:
    kwargs.setdefault("testnet", True)
    return CCXTClient(
        exchange_id="binance",
        symbol="BTCUSDT",
        exchange=exchange or _FakeExchange(),
        **kwargs,
    )


# ----------------------------------------------------------------------
# Khởi tạo
# ----------------------------------------------------------------------


def test_init_unsupported_exchange_id_raises() -> None:
    with pytest.raises(ValueError, match="khong_ton_tai"):
        CCXTClient(exchange_id="khong_ton_tai", symbol="BTCUSDT", exchange=_FakeExchange())


def test_init_testnet_calls_set_sandbox_mode_true() -> None:
    exchange = _FakeExchange()
    _client(exchange, testnet=True)
    assert exchange.sandbox_mode is True


def test_init_loads_markets() -> None:
    exchange = _FakeExchange()
    _client(exchange)
    assert "load_markets" in exchange.calls


def test_live_confirmation_correct_phrase_allows_mainnet() -> None:
    CCXTClient(
        exchange_id="binance",
        symbol="BTCUSDT",
        testnet=False,
        exchange=_FakeExchange(),
        input_fn=lambda _prompt: "YES I UNDERSTAND THE RISKS",
    )


def test_live_confirmation_wrong_phrase_blocks_mainnet() -> None:
    with pytest.raises(PermissionError):
        CCXTClient(
            exchange_id="binance",
            symbol="BTCUSDT",
            testnet=False,
            exchange=_FakeExchange(),
            input_fn=lambda _prompt: "khong dung",
        )


def test_live_confirmation_not_prompted_for_testnet() -> None:
    def _fail_if_called(_prompt: str) -> str:
        raise AssertionError("không được hỏi xác nhận khi testnet=True")

    CCXTClient(
        exchange_id="binance",
        symbol="BTCUSDT",
        testnet=True,
        exchange=_FakeExchange(),
        input_fn=_fail_if_called,
    )


# ----------------------------------------------------------------------
# _to_ccxt_symbol — "BTCUSDT" -> "BTC/USDT", KHÔNG lẫn sang swap
# ----------------------------------------------------------------------


def test_to_ccxt_symbol_resolves_spot_not_swap() -> None:
    client = _client()
    assert client._to_ccxt_symbol("BTCUSDT") == "BTC/USDT"


def test_to_ccxt_symbol_passthrough_when_already_unified() -> None:
    client = _client()
    assert client._to_ccxt_symbol("BTC/USDT") == "BTC/USDT"


# ----------------------------------------------------------------------
# Retry whitelist — NetworkError retry, ExchangeError fail ngay
# ----------------------------------------------------------------------


def test_network_error_retries_then_succeeds() -> None:
    exchange = _FakeExchange()
    exchange.raise_n_times["fetch_balance"] = [
        ccxt.RequestTimeout("timeout"),
        ccxt.NetworkError("blip"),
    ]
    client = _client(exchange)

    balance = client.get_balance()

    assert balance.total == Decimal("10000.5")
    assert exchange.calls.count("fetch_balance") == 3


def test_network_error_exhausts_retries_then_raises() -> None:
    exchange = _FakeExchange()
    exchange.raise_n_times["fetch_balance"] = [ccxt.NetworkError("blip")] * 10
    client = _client(exchange)

    with pytest.raises(ccxt.NetworkError):
        client.get_balance()

    assert exchange.calls.count("fetch_balance") == 4  # 1 lần đầu + 3 retry


def test_rate_limit_exceeded_is_a_network_error_retried() -> None:
    """RateLimitExceeded/DDoSProtection là lớp con NetworkError (xác nhận
    bằng introspection cây kế thừa ccxt) — phải được retry, không fail ngay."""
    exchange = _FakeExchange()
    exchange.raise_n_times["fetch_balance"] = [ccxt.RateLimitExceeded("429")]
    client = _client(exchange)

    client.get_balance()

    assert exchange.calls.count("fetch_balance") == 2


def test_exchange_error_fails_immediately_no_retry() -> None:
    exchange = _FakeExchange()
    exchange.raise_n_times["fetch_balance"] = [ccxt.AuthenticationError("bad key")]
    client = _client(exchange)

    with pytest.raises(ccxt.AuthenticationError):
        client.get_balance()

    assert exchange.calls.count("fetch_balance") == 1  # không retry


def test_exchange_error_logs_message_before_raising(caplog: pytest.LogCaptureFixture) -> None:
    exchange = _FakeExchange()
    exchange.raise_n_times["fetch_balance"] = [ccxt.AuthenticationError("bad key xyz")]
    client = _client(exchange)

    with caplog.at_level("ERROR"):
        with pytest.raises(ccxt.AuthenticationError):
            client.get_balance()

    assert "bad key xyz" in caplog.text
    assert "KHÔNG retry" in caplog.text


# ----------------------------------------------------------------------
# Tài khoản
# ----------------------------------------------------------------------


def test_get_balance_maps_usdt_free_used_total() -> None:
    client = _client()
    balance = client.get_balance()

    assert balance.asset == "USDT"
    assert balance.total == Decimal("10000.5")
    assert balance.available == Decimal("9500.5")
    assert balance.locked == Decimal("500.0")


def test_get_positions_derives_from_non_quote_asset_balance() -> None:
    client = _client()
    positions = client.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].qty == Decimal("0.1")
    assert positions[0].entry_price == positions[0].current_price == Decimal("65000.0")
    assert positions[0].unrealized_pnl == Decimal("0")


def test_get_positions_skips_asset_without_a_market() -> None:
    """Coin trong ví không có thị trường spot với quote_asset (vd. bụi coin
    airdrop) — không định giá được, không phải lỗi cần raise."""
    exchange = _FakeExchange()
    exchange.balance["total"]["DUST"] = 123.0
    client = _client(exchange)

    positions = client.get_positions()

    assert {p.symbol for p in positions} == {"BTCUSDT"}


def test_get_instrument_rules_maps_precision_and_limits() -> None:
    client = _client()
    rules = client.get_instrument_rules("BTCUSDT")

    assert rules.base_precision == Decimal(str(1e-05))
    assert rules.tick_size == Decimal("0.01")
    assert rules.min_order_qty == Decimal(str(1e-05))
    assert rules.max_order_qty == Decimal("9000.0")
    assert rules.min_order_amt == Decimal("5.0")


# ----------------------------------------------------------------------
# Lệnh
# ----------------------------------------------------------------------


def _order_request(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.LIMIT,
    price: Decimal | None = Decimal("64000"),
) -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=side,
        order_type=order_type,
        qty=Decimal("0.01234"),
        price=price,
        order_link_id="abc123",
    )


def test_submit_order_passes_client_order_id_in_params() -> None:
    """`clientOrderId` trong params là cách ccxt map orderLinkId sang
    `newClientOrderId` gốc của Binance — xác nhận bằng grep trực tiếp
    ccxt/binance.py::create_order."""
    exchange = _FakeExchange()
    exchange.create_order_response = {
        "id": "999", "clientOrderId": "abc123", "status": "open", "filled": None, "average": None,
    }
    client = _client(exchange)

    client.submit_order(_order_request())

    assert exchange.last_create_order_params == {"clientOrderId": "abc123"}


def test_submit_order_passes_qty_and_price_as_strings_not_float() -> None:
    """CLAUDE.md bất biến #3 — không bao giờ để float chạm vào qty/price
    trên đường thực thi, kể cả ở biên gọi ccxt."""
    exchange = _FakeExchange()
    exchange.create_order_response = {"id": "1", "status": "open"}
    client = _client(exchange)

    client.submit_order(_order_request())

    _symbol, _type, _side, amount, price = exchange.last_create_order_args  # type: ignore[misc]
    assert isinstance(amount, str) and amount == "0.01234"
    assert isinstance(price, str) and price == "64000"


def test_submit_order_maps_buy_sell_and_limit_market() -> None:
    exchange = _FakeExchange()
    exchange.create_order_response = {"id": "1", "status": "open"}
    client = _client(exchange)

    client.submit_order(_order_request(side=OrderSide.SELL, order_type=OrderType.MARKET, price=None))

    symbol, order_type, side, _amount, price = exchange.last_create_order_args  # type: ignore[misc]
    assert symbol == "BTC/USDT"
    assert order_type == "market"
    assert side == "sell"
    assert price is None


def test_submit_order_limit_without_price_raises() -> None:
    client = _client()
    with pytest.raises(ValueError, match="price"):
        client.submit_order(_order_request(price=None))


def test_submit_order_maps_result_status_and_fill() -> None:
    exchange = _FakeExchange()
    exchange.create_order_response = {
        "id": "999",
        "clientOrderId": "abc123",
        "status": "closed",
        "filled": 0.01234,
        "average": 63990.5,
    }
    client = _client(exchange)

    result = client.submit_order(_order_request())

    assert result.order_id == "999"
    assert result.order_link_id == "abc123"
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == Decimal(str(0.01234))
    assert result.avg_fill_price == Decimal(str(63990.5))


def test_cancel_order_uses_configured_symbol_directly() -> None:
    """Khác BybitClient (phải tra symbol từ get_open_orders trước) —
    CCXTClient chỉ giao dịch một symbol đã cấu hình, dùng thẳng."""
    exchange = _FakeExchange()
    client = _client(exchange)

    ok = client.cancel_order("999")

    assert ok is True
    assert exchange.last_cancel_order_args == ("999", "BTC/USDT")


def test_cancel_order_not_found_returns_false_not_raise() -> None:
    exchange = _FakeExchange()
    exchange.raise_n_times["cancel_order"] = [ccxt.OrderNotFound("gone")]
    client = _client(exchange)

    assert client.cancel_order("999") is False


def test_get_open_orders_passes_configured_symbol() -> None:
    exchange = _FakeExchange()
    exchange.open_orders = [
        {
            "id": "1",
            "clientOrderId": "link1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.05,
            "price": 64000.0,
            "status": "open",
            "timestamp": 1785900000000,
        }
    ]
    client = _client(exchange)

    orders = client.get_open_orders()

    assert exchange.calls.count("fetch_open_orders") == 1
    assert len(orders) == 1
    order = orders[0]
    assert order.order_id == "1"
    assert order.order_link_id == "link1"
    assert order.symbol == "BTCUSDT"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT
    assert order.qty == Decimal(str(0.05))
    assert order.price == Decimal(str(64000.0))
    assert order.status == OrderStatus.NEW


def test_get_open_orders_handles_missing_timestamp() -> None:
    exchange = _FakeExchange()
    exchange.open_orders = [
        {
            "id": "1",
            "clientOrderId": None,
            "symbol": "BTC/USDT",
            "side": "sell",
            "type": "market",
            "amount": 0.1,
            "price": None,
            "status": "closed",
            "timestamp": None,
        }
    ]
    client = _client(exchange)

    orders = client.get_open_orders()

    assert orders[0].order_link_id == ""
    assert orders[0].price is None
    assert orders[0].status == OrderStatus.FILLED


def test_get_orderbook_maps_bids_asks_best_price() -> None:
    client = _client()
    ob = client.get_orderbook("BTCUSDT")

    assert ob.best_bid == Decimal(str(64000.0))
    assert ob.best_ask == Decimal(str(64010.0))
    assert len(ob.bids) == 2
    assert len(ob.asks) == 1


def test_get_orderbook_falls_back_to_now_when_timestamp_missing() -> None:
    """Xác nhận thật (testnet.binance.vision, lúc viết ccxt_client.py):
    fetch_order_book có thể trả timestamp=None."""
    exchange = _FakeExchange()
    exchange.order_book["timestamp"] = None
    client = _client(exchange)

    ob = client.get_orderbook("BTCUSDT")

    assert ob.timestamp is not None


# ----------------------------------------------------------------------
# get_historical_klines
# ----------------------------------------------------------------------


def test_get_historical_klines_returns_sorted_ascending_windowed() -> None:
    exchange = _FakeExchange()
    exchange.ohlcv_page = [
        [1785801600000, 64000.0, 64500.0, 63800.0, 64200.0, 100.0],
        [1785888000000, 64200.0, 65000.0, 64100.0, 64800.0, 120.0],
    ]
    client = _client(exchange)

    import datetime as dt

    # 1785801600000 = 2026-08-04T00:00Z, 1785888000000 = 2026-08-05T00:00Z
    # (xác nhận bằng gọi thật lúc viết ccxt_client.py).
    start = dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 8, 6, tzinfo=dt.timezone.utc)
    df = client.get_historical_klines("BTCUSDT", "1D", start, end)

    assert list(df.index) == sorted(df.index)
    assert df.iloc[0]["close"] == 64200.0
    assert df.iloc[-1]["close"] == 64800.0


def test_get_historical_klines_unsupported_interval_raises() -> None:
    client = _client()
    import datetime as dt

    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="1H"):
        client.get_historical_klines("BTCUSDT", "1H", start, end)


def test_get_historical_klines_empty_page_returns_empty_frame() -> None:
    exchange = _FakeExchange()
    exchange.ohlcv_page = []
    client = _client(exchange)
    import datetime as dt

    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
    df = client.get_historical_klines("BTCUSDT", "1D", start, end)

    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
