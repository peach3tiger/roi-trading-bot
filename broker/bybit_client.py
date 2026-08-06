"""broker.bybit_client — BybitClient implement ExchangeClient qua pybit.

DEPRECATED kể từ 2026-08-06 — KHÔNG dùng cho code mới, KHÔNG xoá file này.

Bybit chặn theo khu vực (regulatory restrictions, retCode 10024) — không
kết nối được cả testnet lẫn mainnet từ môi trường vận hành hiện tại. Xem
`docs/DECISIONS.md`, mục "Đổi sàn Bybit -> Binance (ccxt)", cho bằng chứng
đầy đủ. Sàn LIVE THẬT của dự án là `broker/ccxt_client.py::CCXTClient`
(Binance qua ccxt).

File này giữ nguyên, không sửa logic, không xoá — nó là bằng chứng cho
quyết định đổi sàn (retCode thật, thời điểm phát hiện, toàn bộ lịch sử
sửa lỗi `_call_with_retry` trước khi chặn khu vực lộ ra) và là ví dụ tham
khảo cho `ExchangeClient` ABC hoạt động đúng ra sao: mọi lệnh gọi qua
`broker/order_executor.py`/`broker/position_tracker.py` không cần đổi một
dòng nào khi CCXTClient thay thế nó — đúng mục đích interface này được
dựng lên từ đầu (xem `broker/base.py`).

Testnet (api-testnet.bybit.com) là mặc định — xem CLAUDE.md bất biến #6.
Chuyển sang mainnet yêu cầu gõ tay chuỗi xác nhận đầy đủ, không có cờ tắt
qua middleware.

Mọi field trả về từ pybit (chuỗi JSON) được ép sang `Decimal` qua
`Decimal(str(...))`, không bao giờ qua `float` trung gian — CLAUDE.md bất
biến #3.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import pandas as pd
import requests
from pybit.exceptions import FailedRequestError, InvalidRequestError
from pybit.unified_trading import HTTP

from broker.base import (
    Balance,
    ExchangeClient,
    Order,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from broker.instrument_rules import InstrumentRules

logger = logging.getLogger(__name__)

LIVE_CONFIRMATION_PHRASE = "YES I UNDERSTAND THE RISKS"

# 600 request / 5 giây / IP — nguyên văn giới hạn Bybit v5, chặn ở tầng
# client thay vì chờ retCode 10006 (Brain-Crypto-Bybit.md §6.3).
_RATE_LIMIT_MAX_REQUESTS = 600
_RATE_LIMIT_WINDOW_SECONDS = 5.0

# Lệch đồng hồ > 1s là nguyên nhân số 1 gây lỗi auth với Bybit (§6.3).
_CLOCK_DRIFT_WARN_MS = 1000.0

_MAX_KLINES_PER_REQUEST = 1000
_MAX_RETRIES = 3

# pybit (5.14.0, phiên bản đang dùng — xác nhận bằng cách gọi thẳng
# HTTP._handle_response() với response giả lập retCode=10006, không suy
# luận từ đọc source) tự chặn một số retCode "retryable" CỦA RIÊNG NÓ
# TRƯỚC KHI chúng trở thành InvalidRequestError: nó tự sleep đúng thời
# gian (đọc header X-Bapi-Limit-Reset-Timestamp cho riêng retCode 10006),
# rồi raise một `Exception` THƯỜNG với message cố định này, KHÔNG có
# status_code. pybit không tự lặp lại sau đó — Exception đó không được
# vòng lặp retry của chính nó bắt lại (một lỗ hổng trong pybit, không phải
# thiết kế có chủ đích) — nên việc gọi lại phải làm ở tầng này. Vì pybit
# đã sleep đúng khoảng rồi, không cộng thêm backoff của riêng mình.
_PYBIT_INTERNAL_RETRYABLE_MESSAGE = "Retryable error occurred, retrying..."

# retCode mà pybit coi là "retryable" nội bộ (đọc từ chính source
# pybit._http_manager._V5HTTPManager.__post_init__ — 10002 recv_window,
# 10006 rate limit, 30034/30035/130035/130150 lỗi khớp lệnh nội bộ sàn).
# Bình thường không đường nào tới đây thấy các mã này dưới dạng
# InvalidRequestError (pybit đã chặn trước, xem hằng số trên) — giữ lại
# CHỈ để phòng thủ nếu hành vi pybit đổi ở phiên bản khác.
_PYBIT_INTERNAL_RETRY_CODES = frozenset({10002, 10006, 30034, 30035, 130035, 130150})

# "1D" (quy ước timeframe của settings.yaml/toàn hệ thống) -> "D" (quy ước
# interval của Bybit v5 kline). Chỉ hỗ trợ đúng khung đang dùng — thêm khi
# thật sự cần khung khác, không đoán trước.
_INTERVAL_MAP = {"1D": "D", "D": "D"}

_ORDER_STATUS_MAP = {
    "New": OrderStatus.NEW,
    "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "Rejected": OrderStatus.REJECTED,
    "PartiallyFilledCanceled": OrderStatus.CANCELLED,
    "Deactivated": OrderStatus.CANCELLED,
}


class _TokenBucketRateLimiter:
    """Sliding-window token bucket — `acquire()` chặn (sleep) nếu đã chạm
    `max_requests` trong `window_seconds` gần nhất, thay vì để request thứ
    601 bay thẳng ra sàn rồi mới biết bị từ chối.
    """

    def __init__(
        self, max_requests: int = _RATE_LIMIT_MAX_REQUESTS, window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def _evict_expired(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] > self._window_seconds:
            self._timestamps.popleft()

    def acquire(self) -> None:
        now = time.monotonic()
        self._evict_expired(now)
        if len(self._timestamps) >= self._max_requests:
            sleep_for = self._window_seconds - (now - self._timestamps[0])
            if sleep_for > 0:
                logger.warning("Rate limit nội bộ (600 req/5s) — chờ %.2fs.", sleep_for)
                time.sleep(sleep_for)
            now = time.monotonic()
            self._evict_expired(now)
        self._timestamps.append(now)


class BybitClient(ExchangeClient):
    """Bọc SDK `pybit` (Bybit v5 unified API).

    Credentials đọc từ .env, không bao giờ hardcode hay log — kể cả một
    phần. Đồng bộ thời gian server lúc khởi động (lệch recv_window là
    nguyên nhân số 1 gây lỗi auth với Bybit); implement token bucket rate
    limit ở tầng này (600 request / 5 giây / IP) thay vì chờ bị sàn chặn.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        category: str = "spot",
        recv_window_ms: int = 5000,
        input_fn: Callable[[str], str] = input,
        session: Optional[Any] = None,
    ) -> None:
        """`session` tiêm được (mặc định `None` -> dựng `pybit.unified_trading.HTTP`
        thật) — chỉ để test thay bằng một session giả không cần mạng/API
        key thật; caller thường (main.py tương lai) không bao giờ cần
        truyền tham số này."""
        if not testnet:
            self._require_live_confirmation(input_fn)

        self.testnet = testnet
        self.category = category
        self._rate_limiter = _TokenBucketRateLimiter()
        self._session = session if session is not None else HTTP(
            testnet=testnet, api_key=api_key, api_secret=api_secret, recv_window=recv_window_ms
        )
        self.clock_drift_ms: Optional[float] = None
        self._sync_server_time()

    # ------------------------------------------------------------------
    # Khởi động
    # ------------------------------------------------------------------

    def _require_live_confirmation(self, input_fn: Callable[[str], str]) -> None:
        """Bắt buộc gõ tay LIVE_CONFIRMATION_PHRASE trước khi cho phép mainnet.

        `input_fn` tiêm được (mặc định `input` thật) — để test mô phỏng gõ
        đúng/sai/bỏ trống mà không cần stdin thật.
        """
        print("⚠️  LIVE TRADING VỚI TIỀN THẬT.")
        typed = input_fn(f"Gõ '{LIVE_CONFIRMATION_PHRASE}' để xác nhận: ")
        if typed.strip() != LIVE_CONFIRMATION_PHRASE:
            raise PermissionError(
                "Xác nhận live trading không đúng cụm từ yêu cầu — dừng, không kết nối mainnet."
            )

    def _sync_server_time(self) -> None:
        """Cảnh báo nếu lệch > 1 giây. Đo bằng trung điểm (before+after)/2
        của request local để bù độ trễ mạng round-trip — xấp xỉ hợp lý,
        không cần giao thức NTP đầy đủ cho mục đích cảnh báo."""
        local_before = time.time()
        response = self._call_with_retry(self._session.get_server_time)
        local_after = time.time()

        server_time_s = int(response["result"]["timeSecond"])
        local_mid = (local_before + local_after) / 2
        drift_ms = abs(local_mid - server_time_s) * 1000
        self.clock_drift_ms = drift_ms

        if drift_ms > _CLOCK_DRIFT_WARN_MS:
            logger.warning(
                "Lệch đồng hồ với Bybit server: %.0fms > %.0fms — nguyên nhân số 1 gây lỗi "
                "auth với Bybit (Brain-Crypto-Bybit.md §6.3).",
                drift_ms,
                _CLOCK_DRIFT_WARN_MS,
            )

    # ------------------------------------------------------------------
    # Gọi API — WHITELIST retry, không phải blacklist. CHỈ thử lại khi lỗi
    # thuộc tập nhất thời đã biết:
    #   1. pybit tự chặn retCode rate-limit/nội bộ của chính nó, đã sleep
    #      sẵn (xem _PYBIT_INTERNAL_RETRYABLE_MESSAGE) — gọi lại ngay,
    #      không cộng thêm backoff.
    #   2. Lỗi mạng thật (timeout/connection reset/SSL) — pybit mặc định
    #      KHÔNG tự retry (force_retry=False, giá trị mặc định của
    #      pybit.unified_trading.HTTP, không đổi ở đây) — backoff mũ ở tầng
    #      này.
    #   3. HTTP 5xx (`FailedRequestError.status_code` 500-599) — lỗi phía
    #      sàn, có cơ hội tự khỏi — backoff mũ ở tầng này.
    #
    # MỌI lỗi khác — retCode != 0 không nằm trong hai tập trên (tham số
    # sai, số dư không đủ, symbol không tồn tại, orderLinkId trùng, key
    # sai...) hoặc HTTP 4xx — THẤT BẠI NGAY, không retry, không backoff.
    # Các lỗi này không tự khỏi theo thời gian; thử lại chỉ tốn thời gian
    # (7s cho 3 lần backoff mũ cũ) và làm nhiễu log. Log đủ retCode + retMsg
    # NGUYÊN VĂN trước khi raise — cách duy nhất biết được mã lỗi thật của
    # những trường hợp như orderLinkId trùng khi chạy được với mạng thật.
    # ------------------------------------------------------------------

    def _call_with_retry(self, fn: Callable[..., dict], **kwargs: Any) -> dict:
        attempt = 0
        while True:
            self._rate_limiter.acquire()
            try:
                return fn(**kwargs)

            except InvalidRequestError as exc:
                if exc.status_code in _PYBIT_INTERNAL_RETRY_CODES:
                    attempt = self._retry_wait(
                        attempt, "retCode nội bộ pybit (dự phòng)", extra_backoff=False
                    )
                    continue
                logger.error(
                    "Bybit từ chối yêu cầu — KHÔNG retry. retCode=%s retMsg=%s request=%s",
                    exc.status_code,
                    exc.message,
                    exc.request,
                )
                raise

            except FailedRequestError as exc:
                status = exc.status_code
                if isinstance(status, int) and 500 <= status < 600:
                    attempt = self._retry_wait(attempt, f"HTTP {status}", extra_backoff=True)
                    continue
                logger.error(
                    "Bybit trả lỗi HTTP — KHÔNG retry. status_code=%s message=%s request=%s",
                    status,
                    exc.message,
                    exc.request,
                )
                raise

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
            ) as exc:
                attempt = self._retry_wait(attempt, f"lỗi mạng ({type(exc).__name__})", extra_backoff=True)
                continue

            except Exception as exc:
                if str(exc) == _PYBIT_INTERNAL_RETRYABLE_MESSAGE:
                    attempt = self._retry_wait(
                        attempt, "rate limit/retCode nội bộ pybit", extra_backoff=False
                    )
                    continue
                raise

    def _retry_wait(self, attempt: int, label: str, *, extra_backoff: bool) -> int:
        """Tăng `attempt`, sleep nếu cần, trả `attempt` mới để caller tiếp
        tục vòng lặp — hoặc raise (giữ traceback gốc bằng bare `raise`,
        không bọc lại exception) nếu đã hết `_MAX_RETRIES`.

        `extra_backoff=False` khi pybit đã tự sleep đúng khoảng cho ta rồi
        (rate limit) — gọi lại ngay không cộng thêm backoff mũ của riêng
        module này; `True` cho lỗi mạng/HTTP 5xx, nơi không ai đã sleep hộ.
        """
        attempt += 1
        if attempt > _MAX_RETRIES:
            logger.error("%s — hết %d lần thử, dừng.", label, _MAX_RETRIES)
            raise
        if extra_backoff:
            backoff = 2 ** (attempt - 1)
            logger.warning("%s (lần %d/%d) — backoff %ss, thử lại.", label, attempt, _MAX_RETRIES, backoff)
            time.sleep(backoff)
        else:
            logger.warning("%s (lần %d/%d) — pybit đã tự sleep, gọi lại ngay.", label, attempt, _MAX_RETRIES)
        return attempt

    # ------------------------------------------------------------------
    # Tài khoản
    # ------------------------------------------------------------------

    def get_balance(self) -> Balance:
        response = self._call_with_retry(
            self._session.get_wallet_balance, accountType="UNIFIED"
        )
        coins = response["result"]["list"][0]["coin"]
        for coin in coins:
            if coin["coin"] == "USDT":
                total = Decimal(str(coin["walletBalance"] or "0"))
                locked = Decimal(str(coin.get("locked") or "0"))
                available = total - locked
                return Balance(asset="USDT", total=total, available=available, locked=locked)
        return Balance(asset="USDT", total=Decimal("0"), available=Decimal("0"), locked=Decimal("0"))

    def get_positions(self) -> list[Position]:
        """Spot KHÔNG có khái niệm "position" native trên Bybit v5 (đó là
        đối tượng của derivatives/margin) — với spot, "vị thế" chỉ là số
        dư coin cơ sở. `entry_price`/`unrealized_pnl` không suy ra được
        chỉ từ số dư sàn; đây là công việc của
        `broker/position_tracker.py::PositionTracker` (theo dõi cục bộ,
        cập nhật mỗi lần khớp lệnh). Phương thức này trả về snapshot
        best-effort: nếu có số dư base asset, coi đó là một "position" với
        `entry_price=current_price` (không biết entry thật) và
        `unrealized_pnl=0` — CHỈ dùng để đối soát thô, không dùng để tính
        P&L thật.
        """
        response = self._call_with_retry(self._session.get_wallet_balance, accountType="UNIFIED")
        coins = response["result"]["list"][0]["coin"]
        positions: list[Position] = []
        for coin in coins:
            symbol_base = coin["coin"]
            if symbol_base == "USDT":
                continue
            qty = Decimal(str(coin["walletBalance"] or "0"))
            if qty <= 0:
                continue
            current_price = self._get_last_price(f"{symbol_base}USDT")
            positions.append(
                Position(
                    symbol=f"{symbol_base}USDT",
                    qty=qty,
                    entry_price=current_price,
                    current_price=current_price,
                    unrealized_pnl=Decimal("0"),
                )
            )
        return positions

    def _get_last_price(self, symbol: str) -> Decimal:
        response = self._call_with_retry(
            self._session.get_kline, category=self.category, symbol=symbol, interval="D", limit=1
        )
        rows = response["result"]["list"]
        if not rows:
            return Decimal("0")
        return Decimal(str(rows[0][4]))  # close

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        response = self._call_with_retry(
            self._session.get_instruments_info, category=self.category, symbol=symbol
        )
        items = response["result"]["list"]
        if not items:
            raise ValueError(f"Bybit không trả instrument info cho {symbol} (category={self.category})")
        info = items[0]
        lot = info["lotSizeFilter"]
        price = info["priceFilter"]
        return InstrumentRules(
            symbol=symbol,
            base_precision=Decimal(str(lot["basePrecision"])),
            quote_precision=Decimal(str(lot.get("quotePrecision", "0.01"))),
            tick_size=Decimal(str(price["tickSize"])),
            min_order_qty=Decimal(str(lot["minOrderQty"])),
            min_order_amt=Decimal(str(lot["minOrderAmt"])),
            max_order_qty=Decimal(str(lot["maxOrderQty"])),
        )

    # ------------------------------------------------------------------
    # Dữ liệu giá
    # ------------------------------------------------------------------

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Phân trang lùi dần (Bybit trả mới nhất trước, tối đa 1000 bar/
        request) — dùng cho live loop lấy vài trăm bar gần nhất để warm-up
        chỉ báo, KHÔNG dùng cho backtest dài hạn (Bybit spot chỉ có dữ
        liệu từ ~2021, xem data/history_loader.py cho nguồn dài hạn qua
        Binance)."""
        bybit_interval = _INTERVAL_MAP.get(interval)
        if bybit_interval is None:
            raise ValueError(f"Chưa hỗ trợ interval {interval!r} — chỉ {list(_INTERVAL_MAP)}")

        # naive -> coi là UTC (không phải giờ local) — cùng quy ước
        # data/history_loader.py::HistoryLoader._ensure_utc.
        start = start if start.tzinfo is not None else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        rows: list[list[str]] = []
        cursor_end_ms = end_ms
        while cursor_end_ms > start_ms:
            response = self._call_with_retry(
                self._session.get_kline,
                category=self.category,
                symbol=symbol,
                interval=bybit_interval,
                start=start_ms,
                end=cursor_end_ms,
                limit=_MAX_KLINES_PER_REQUEST,
            )
            page = response["result"]["list"]
            if not page:
                break
            rows.extend(page)
            oldest_ms = int(page[-1][0])
            if oldest_ms <= start_ms or len(page) < _MAX_KLINES_PER_REQUEST:
                break
            cursor_end_ms = oldest_ms - 1

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"]
        )
        df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        # Dùng lại start_ms/end_ms (đã tính đúng epoch UTC ở trên qua
        # .timestamp(), bất kể `start`/`end` truyền vào aware hay naive) —
        # không dựng lại pd.Timestamp(start, tz="UTC") vì lỗi nếu `start`
        # đã có tzinfo (ValueError: "Cannot pass a datetime ... with tz").
        start_ts = pd.Timestamp(start_ms, unit="ms", tz="UTC")
        end_ts = pd.Timestamp(end_ms, unit="ms", tz="UTC")
        windowed = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        return windowed[["open", "high", "low", "close", "volume"]]

    # ------------------------------------------------------------------
    # Lệnh
    # ------------------------------------------------------------------

    def submit_order(self, order: OrderRequest) -> OrderResult:
        kwargs: dict[str, Any] = {
            "category": self.category,
            "symbol": order.symbol,
            "side": "Buy" if order.side is OrderSide.BUY else "Sell",
            "orderType": "Limit" if order.order_type is OrderType.LIMIT else "Market",
            "qty": str(order.qty),
            "orderLinkId": order.order_link_id,
        }
        if order.order_type is OrderType.LIMIT:
            if order.price is None:
                raise ValueError("OrderRequest LIMIT phải có price")
            kwargs["price"] = str(order.price)
            kwargs["timeInForce"] = "GTC"

        response = self._call_with_retry(self._session.place_order, **kwargs)
        result = response["result"]
        return OrderResult(
            order_id=result["orderId"],
            order_link_id=result.get("orderLinkId", order.order_link_id),
            status=OrderStatus.NEW,
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw_response=response,
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            symbol = self._symbol_of(order_id)
            self._call_with_retry(
                self._session.cancel_order, category=self.category, symbol=symbol, orderId=order_id
            )
            return True
        except (InvalidRequestError, FailedRequestError, ValueError) as exc:
            # ValueError: _symbol_of không tìm thấy order_id trong danh sách
            # lệnh mở (đã khớp/huỷ rồi, hoặc id sai) — cùng kết quả với lỗi
            # API thật: không huỷ được, trả False chứ không để lộ traceback
            # (ExchangeClient.cancel_order cam kết trả bool, không raise).
            logger.warning("cancel_order(%s) thất bại: %s", order_id, exc)
            return False

    def _symbol_of(self, order_id: str) -> str:
        """cancel_order của Bybit v5 cần `symbol` — tra từ danh sách lệnh
        đang mở thay vì bắt caller tự truyền thêm tham số (giữ đúng chữ ký
        `cancel_order(order_id) -> bool` của ExchangeClient)."""
        for order in self.get_open_orders():
            if order.order_id == order_id:
                return order.symbol
        raise ValueError(f"Không tìm thấy lệnh đang mở {order_id} để suy ra symbol")

    def get_open_orders(self) -> list[Order]:
        response = self._call_with_retry(
            self._session.get_open_orders, category=self.category, symbol=""
        )
        orders: list[Order] = []
        for item in response["result"]["list"]:
            orders.append(
                Order(
                    order_id=item["orderId"],
                    order_link_id=item.get("orderLinkId", ""),
                    symbol=item["symbol"],
                    side=OrderSide.BUY if item["side"] == "Buy" else OrderSide.SELL,
                    order_type=OrderType.LIMIT if item["orderType"] == "Limit" else OrderType.MARKET,
                    qty=Decimal(str(item["qty"])),
                    price=Decimal(str(item["price"])) if item.get("price") else None,
                    status=_ORDER_STATUS_MAP.get(item["orderStatus"], OrderStatus.NEW),
                    created_at=datetime.fromtimestamp(int(item["createdTime"]) / 1000, tz=timezone.utc),
                )
            )
        return orders

    def get_orderbook(self, symbol: str) -> OrderBook:
        """Để risk_manager.check_spread() kiểm tra trước khi duyệt lệnh
        (§5.4). `limit=1` — chỉ cần best bid/ask cho spread, không cần
        depth đầy đủ."""
        response = self._call_with_retry(
            self._session.get_orderbook, category=self.category, symbol=symbol, limit=1
        )
        result = response["result"]
        bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in result["b"]]
        asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in result["a"]]
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=datetime.fromtimestamp(int(result["ts"]) / 1000, tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # WebSocket — chưa xác nhận được bằng test thật (cần kết nối mạng
    # sống), xem ops/RUNBOOK.md mục "Mất WebSocket" cho quy trình vận hành.
    # ------------------------------------------------------------------

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        from pybit.unified_trading import WebSocket

        bybit_interval = _INTERVAL_MAP.get(interval)
        if bybit_interval is None:
            raise ValueError(f"Chưa hỗ trợ interval {interval!r} — chỉ {list(_INTERVAL_MAP)}")

        ws = WebSocket(channel_type=self.category, testnet=self.testnet)

        def _on_message(message: dict) -> None:
            for row in message.get("data", []):
                series = pd.Series(
                    {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "confirm": bool(row["confirm"]),
                    },
                    name=pd.Timestamp(int(row["start"]), unit="ms", tz="UTC"),
                )
                callback(series)

        ws.kline_stream(interval=bybit_interval, symbol=symbol, callback=_on_message)
        self._kline_ws = ws  # giữ tham chiếu — để GC không đóng kết nối

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        from pybit.unified_trading import WebSocket

        ws = WebSocket(
            channel_type="private",
            testnet=self.testnet,
            api_key=self._session.api_key,
            api_secret=self._session.api_secret,
        )

        def _on_message(message: dict) -> None:
            for item in message.get("data", []):
                callback(
                    OrderResult(
                        order_id=item["orderId"],
                        order_link_id=item.get("orderLinkId", ""),
                        status=_ORDER_STATUS_MAP.get(item.get("orderStatus", ""), OrderStatus.NEW),
                        filled_qty=Decimal(str(item.get("cumExecQty", "0"))),
                        avg_fill_price=(
                            Decimal(str(item["avgPrice"])) if item.get("avgPrice") else None
                        ),
                        raw_response=item,
                    )
                )

        ws.order_stream(callback=_on_message)
        self._order_ws = ws  # giữ tham chiếu — để GC không đóng kết nối
