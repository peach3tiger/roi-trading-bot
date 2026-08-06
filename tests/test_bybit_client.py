"""tests.test_bybit_client — rate limiter, retry/backoff, live-confirmation,
mapping response Bybit v5 -> dataclass nội bộ.

Dùng `session` giả (tiêm qua `BybitClient(..., session=...)`) — KHÔNG cần
mạng/API key thật. Response giả lập khớp đúng shape đã xác nhận bằng gọi
thật tới Bybit testnet (public endpoint) lúc viết `broker/bybit_client.py`
— xem comment ở từng fixture.

`time.sleep` bị monkeypatch thành no-op trong toàn file — các test retry/
backoff không cần chờ thời gian thật.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest
import requests
from pybit.exceptions import FailedRequestError, InvalidRequestError

from broker.base import OrderRequest, OrderSide, OrderType
from broker.bybit_client import (
    _PYBIT_INTERNAL_RETRYABLE_MESSAGE,
    BybitClient,
    OrderStatus,
    _TokenBucketRateLimiter,
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def _invalid_request_error(status_code: int, message: str = "err") -> InvalidRequestError:
    """`status_code` ở đây là retCode Bybit (vd. 10003, 10006), KHÔNG phải
    HTTP status — khớp đúng những gì pybit gán vào field này cho
    InvalidRequestError (xem broker/bybit_client.py, đã xác nhận bằng gọi
    thật)."""
    return InvalidRequestError(
        request="POST /v5/order/create: {'symbol': 'BTCUSDT'}",
        message=message,
        status_code=status_code,
        time="t",
        resp_headers=None,
    )


def _failed_request_error(http_status_code: int, message: str = "err") -> FailedRequestError:
    """`status_code` ở đây là HTTP status thật (vd. 401, 500) — khớp đúng
    những gì pybit gán vào field này cho FailedRequestError khi response
    HTTP không phải 200 (xem broker/bybit_client.py, đã xác nhận bằng gọi
    thật)."""
    return FailedRequestError(
        request="GET /v5/account/wallet-balance: {}",
        message=message,
        status_code=http_status_code,
        time="t",
        resp_headers=None,
    )


def _pybit_internal_retryable_exception() -> Exception:
    """pybit tự raise Exception THƯỜNG (không status_code) cho retCode nội
    bộ của nó (rate limit, recv_window...) sau khi đã tự sleep — xác nhận
    bằng gọi thẳng HTTP._handle_response() với response giả lập retCode
    =10006, xem comment ở broker/bybit_client.py::_PYBIT_INTERNAL_RETRYABLE_MESSAGE."""
    return Exception(_PYBIT_INTERNAL_RETRYABLE_MESSAGE)


class _FakeSession:
    """Response shape khớp Bybit v5 thật — đã verify bằng gọi trực tiếp
    testnet lúc viết bybit_client.py (get_server_time/get_instruments_info/
    get_kline/get_orderbook), phần còn lại (wallet/order) theo đúng schema
    tài liệu Bybit v5 unified trading."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.server_time_offset_s = 0.0
        self.raise_n_times: dict[str, list[Exception]] = {}

    def _maybe_raise(self, name: str) -> None:
        queue = self.raise_n_times.get(name)
        if queue:
            raise queue.pop(0)

    def get_server_time(self) -> dict:
        self.calls.append("get_server_time")
        self._maybe_raise("get_server_time")
        now = time.time() + self.server_time_offset_s
        return {"result": {"timeSecond": str(int(now))}}

    def get_wallet_balance(self, **kwargs: Any) -> dict:
        self.calls.append("get_wallet_balance")
        self._maybe_raise("get_wallet_balance")
        return {
            "result": {
                "list": [
                    {
                        "coin": [
                            {"coin": "USDT", "walletBalance": "10000.5", "locked": "500"},
                            {"coin": "BTC", "walletBalance": "0.1", "locked": "0"},
                        ]
                    }
                ]
            }
        }

    def get_instruments_info(self, **kwargs: Any) -> dict:
        self.calls.append("get_instruments_info")
        self._maybe_raise("get_instruments_info")
        return {
            "result": {
                "list": [
                    {
                        "symbol": kwargs["symbol"],
                        "lotSizeFilter": {
                            "basePrecision": "0.000001",
                            "quotePrecision": "0.0000001",
                            "minOrderQty": "0.000001",
                            "maxOrderQty": "10",
                            "minOrderAmt": "5",
                        },
                        "priceFilter": {"tickSize": "0.1"},
                    }
                ]
            }
        }

    def get_kline(self, **kwargs: Any) -> dict:
        self.calls.append("get_kline")
        self._maybe_raise("get_kline")
        return {
            "result": {
                "list": [
                    ["1785974400000", "64234.7", "64682.5", "64234.7", "64681.6", "81.26", "5240898.3"],
                ]
            }
        }

    def place_order(self, **kwargs: Any) -> dict:
        self.calls.append("place_order")
        self._maybe_raise("place_order")
        return {"result": {"orderId": "order-1", "orderLinkId": kwargs["orderLinkId"]}}

    def cancel_order(self, **kwargs: Any) -> dict:
        self.calls.append("cancel_order")
        self._maybe_raise("cancel_order")
        return {"result": {}}

    def get_open_orders(self, **kwargs: Any) -> dict:
        self.calls.append("get_open_orders")
        self._maybe_raise("get_open_orders")
        return {
            "result": {
                "list": [
                    {
                        "orderId": "order-1",
                        "orderLinkId": "link-1",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "orderType": "Limit",
                        "qty": "0.01",
                        "price": "64000",
                        "orderStatus": "New",
                        "createdTime": "1785974400000",
                    }
                ]
            }
        }

    def get_orderbook(self, **kwargs: Any) -> dict:
        self.calls.append("get_orderbook")
        self._maybe_raise("get_orderbook")
        return {
            "result": {
                "s": kwargs["symbol"],
                "a": [["64686.4", "0.386327"]],
                "b": [["64686.3", "0.330825"]],
                "ts": "1786010533508",
            }
        }


def _make_client(session: _FakeSession | None = None, **kwargs: Any) -> tuple[BybitClient, _FakeSession]:
    s = session if session is not None else _FakeSession()
    client = BybitClient(api_key="k", api_secret="s", testnet=True, session=s, **kwargs)
    return client, s


# ----------------------------------------------------------------------
# Khởi động — clock sync, live confirmation
# ----------------------------------------------------------------------


def test_clock_drift_computed_from_server_time() -> None:
    session = _FakeSession()
    session.server_time_offset_s = 0.05  # 50ms, dưới ngưỡng cảnh báo 1000ms
    client, _ = _make_client(session)
    assert client.clock_drift_ms is not None
    assert client.clock_drift_ms < 1000


def test_clock_drift_over_1s_does_not_raise_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    session = _FakeSession()
    session.server_time_offset_s = -5.0  # lệch 5 giây
    client, _ = _make_client(session)
    assert client.clock_drift_ms is not None
    assert client.clock_drift_ms >= 4900  # ~5000ms, cho phép sai số đo


def test_live_confirmation_correct_phrase_allows_mainnet() -> None:
    session = _FakeSession()
    client = BybitClient(
        api_key="k",
        api_secret="s",
        testnet=False,
        session=session,
        input_fn=lambda _prompt: "YES I UNDERSTAND THE RISKS",
    )
    assert client.testnet is False


def test_live_confirmation_wrong_phrase_blocks_mainnet() -> None:
    with pytest.raises(PermissionError):
        BybitClient(
            api_key="k",
            api_secret="s",
            testnet=False,
            session=_FakeSession(),
            input_fn=lambda _prompt: "yes i understand",
        )


def test_live_confirmation_not_prompted_for_testnet() -> None:
    def _boom(_prompt: str) -> str:
        raise AssertionError("không được hỏi xác nhận khi testnet=True")

    client, _ = _make_client(input_fn=_boom)
    assert client.testnet is True


# ----------------------------------------------------------------------
# Rate limiter — thuần, không cần BybitClient
# ----------------------------------------------------------------------


def test_rate_limiter_allows_up_to_max_without_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    limiter = _TokenBucketRateLimiter(max_requests=3, window_seconds=5.0)

    for _ in range(3):
        limiter.acquire()

    assert slept == []


def test_rate_limiter_sleeps_when_over_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    limiter = _TokenBucketRateLimiter(max_requests=2, window_seconds=5.0)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # request thứ 3 vượt capacity trong cửa sổ -> phải chờ

    assert len(slept) == 1
    assert slept[0] > 0


# ----------------------------------------------------------------------
# Retry / backoff — WHITELIST: chỉ 3 tập lỗi nhất thời được thử lại (rate
# limit nội bộ pybit, mạng, HTTP 5xx). Mọi lỗi khác thất bại NGAY, không
# retry, không backoff — xem docstring _call_with_retry.
# ----------------------------------------------------------------------


def test_retries_on_pybit_internal_retryable_exception_then_succeeds() -> None:
    """Đường THẬT pybit đi qua cho retCode 10006/10002/... — Exception
    thường, không status_code (xác nhận bằng gọi thật, xem comment
    _PYBIT_INTERNAL_RETRYABLE_MESSAGE). Gọi lại NGAY, không cộng backoff
    (pybit đã tự sleep hộ)."""
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [
        _pybit_internal_retryable_exception(),
        _pybit_internal_retryable_exception(),
    ]
    balance = client.get_balance()

    assert balance.total == Decimal("10000.5")
    assert session.calls.count("get_wallet_balance") == 3  # 2 lần fail + 1 lần thành công


def test_retries_on_pybit_internal_retry_retcode_as_invalid_request_error() -> None:
    """Đường DỰ PHÒNG — nếu một phiên bản pybit khác lại để retCode nội bộ
    (10006...) lọt qua thành InvalidRequestError thay vì Exception thường,
    vẫn phải retry (status_code nằm trong _PYBIT_INTERNAL_RETRY_CODES)."""
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [_invalid_request_error(10006)]
    balance = client.get_balance()

    assert balance.total == Decimal("10000.5")
    assert session.calls.count("get_wallet_balance") == 2


def test_non_whitelisted_retcode_fails_immediately_no_retry() -> None:
    """retCode KHÔNG nằm trong tập nhất thời (vd. 10003 key sai, hay bất
    kỳ lỗi tham số/nghiệp vụ nào) -> thất bại ở LẦN GỌI ĐẦU TIÊN, không có
    lần thử thứ hai."""
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [
        _invalid_request_error(10003, message="API key is invalid."),
        _invalid_request_error(10003, message="API key is invalid."),  # sẽ KHÔNG bị tiêu tới
    ]
    with pytest.raises(InvalidRequestError):
        client.get_balance()

    assert session.calls.count("get_wallet_balance") == 1  # đúng 1 lần, không retry


def test_non_whitelisted_retcode_logs_full_retcode_and_retmsg(caplog: pytest.LogCaptureFixture) -> None:
    session = _FakeSession()
    client, _ = _make_client(session)
    session.raise_n_times["place_order"] = [
        _invalid_request_error(110001, message="Order link ID is duplicate.")
    ]

    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("0.01"),
        price=Decimal("64000"),
        order_link_id="link-1",
    )
    with pytest.raises(InvalidRequestError):
        client.submit_order(request)

    log_text = caplog.text
    assert "110001" in log_text
    assert "Order link ID is duplicate." in log_text
    assert "KHÔNG retry" in log_text


def test_http_5xx_retries_then_succeeds() -> None:
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [_failed_request_error(502), _failed_request_error(503)]
    balance = client.get_balance()

    assert balance.total == Decimal("10000.5")
    assert session.calls.count("get_wallet_balance") == 3


def test_http_5xx_exhausts_retries_then_raises() -> None:
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [
        _failed_request_error(500),
        _failed_request_error(500),
        _failed_request_error(500),
        _failed_request_error(500),
    ]
    with pytest.raises(FailedRequestError):
        client.get_balance()


def test_http_4xx_fails_immediately_no_retry() -> None:
    """4xx (lỗi phía CLIENT — request sai, không phải sàn) không thuộc
    whitelist -> thất bại ngay, khác hẳn 5xx."""
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [_failed_request_error(404)]
    with pytest.raises(FailedRequestError):
        client.get_balance()

    assert session.calls.count("get_wallet_balance") == 1


def test_network_error_retries_then_succeeds() -> None:
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [
        requests.exceptions.ConnectionError("connection reset"),
        requests.exceptions.Timeout("read timeout"),
    ]
    balance = client.get_balance()

    assert balance.total == Decimal("10000.5")
    assert session.calls.count("get_wallet_balance") == 3


def test_network_error_exhausts_retries_then_raises() -> None:
    session = _FakeSession()
    client, _ = _make_client(session)

    session.raise_n_times["get_wallet_balance"] = [
        requests.exceptions.ConnectionError("x"),
        requests.exceptions.ConnectionError("x"),
        requests.exceptions.ConnectionError("x"),
        requests.exceptions.ConnectionError("x"),
    ]
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get_balance()


# ----------------------------------------------------------------------
# Mapping response Bybit v5 -> dataclass
# ----------------------------------------------------------------------


def test_get_balance_maps_usdt_available_correctly() -> None:
    client, _ = _make_client()
    balance = client.get_balance()
    assert balance.total == Decimal("10000.5")
    assert balance.locked == Decimal("500")
    assert balance.available == Decimal("9500.5")


def test_get_positions_derives_from_non_usdt_coin_balance() -> None:
    client, _ = _make_client()
    positions = client.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].qty == Decimal("0.1")


def test_get_instrument_rules_maps_lot_and_price_filter() -> None:
    client, _ = _make_client()
    rules = client.get_instrument_rules("BTCUSDT")
    assert rules.base_precision == Decimal("0.000001")
    assert rules.tick_size == Decimal("0.1")
    assert rules.min_order_amt == Decimal("5")
    assert rules.max_order_qty == Decimal("10")


def test_submit_order_maps_side_and_type_to_bybit_strings() -> None:
    client, session = _make_client()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("0.01"),
        price=Decimal("64000"),
        order_link_id="link-1",
    )
    result = client.submit_order(request)

    assert result.order_id == "order-1"
    assert result.status is OrderStatus.NEW


def test_cancel_order_looks_up_symbol_from_open_orders() -> None:
    client, session = _make_client()
    ok = client.cancel_order("order-1")
    assert ok is True
    assert "cancel_order" in session.calls


def test_cancel_order_unknown_id_returns_false_not_raise() -> None:
    client, _ = _make_client()
    assert client.cancel_order("does-not-exist") is False


def test_get_open_orders_maps_status_and_side() -> None:
    client, _ = _make_client()
    orders = client.get_open_orders()
    assert len(orders) == 1
    assert orders[0].side is OrderSide.BUY
    assert orders[0].status is OrderStatus.NEW


def test_get_orderbook_maps_bids_asks_best_price() -> None:
    client, _ = _make_client()
    ob = client.get_orderbook("BTCUSDT")
    assert ob.best_bid == Decimal("64686.3")
    assert ob.best_ask == Decimal("64686.4")


def test_get_historical_klines_returns_sorted_ascending() -> None:
    client, _ = _make_client()
    import datetime as dt

    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)
    df = client.get_historical_klines("BTCUSDT", "1D", start, end)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing
