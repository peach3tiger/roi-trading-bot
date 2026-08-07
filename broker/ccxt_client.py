"""broker.ccxt_client — CCXTClient: implement ExchangeClient qua ccxt.

Thay `broker/bybit_client.py` (deprecated) kể từ 2026-08-06: Bybit chặn
theo khu vực (regulatory restrictions, retCode 10024) — không dùng được cả
testnet lẫn mainnet từ môi trường vận hành hiện tại. Xem
`docs/DECISIONS.md`, mục "Đổi sàn Bybit -> Binance (ccxt)".

`exchange.name`/`exchange.testnet` đọc từ `settings.yaml` — implementation
này không hardcode "binance", dù hiện tại chỉ Binance được xác nhận bằng
test thật (ccxt hỗ trợ hàng chục sàn cùng interface `create_order`/
`fetch_ohlcv`/... nên đổi sàn khác chỉ cần đổi config, xem
`broker/base.py` cho lý do kiến trúc). Testnet/mainnet chuyển qua
`exchange.set_sandbox_mode(True)` của chính ccxt, không tự dựng URL riêng.

KHÔNG WebSocket — polling REST (xem `docs/DECISIONS.md`): bot chạy bar
`1D`, WebSocket là công nghệ cho tần suất cao mà dự án không cần.
`ExchangeClient` (broker/base.py) không còn `subscribe_klines`/
`subscribe_executions`.

Mọi field trả về từ ccxt (thường là `float`, ccxt tự parse số từ sàn) được
ép sang `Decimal` qua `Decimal(str(...))`, không bao giờ `Decimal(x)` trực
tiếp trên float — CLAUDE.md bất biến #3, tránh mang theo sai số biểu diễn
nhị phân của float vào Decimal. Lệnh gửi đi cũng truyền qty/price dạng
`str(Decimal)` cho ccxt, không phải `float`, cùng lý do.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import ccxt
import pandas as pd

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
    require_live_confirmation,
)
from broker.instrument_rules import InstrumentRules

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_MAX_KLINES_PER_REQUEST = 1000

# "1D" (quy ước timeframe của settings.yaml/toàn hệ thống) -> "1d" (quy ước
# timeframe của ccxt). Chỉ hỗ trợ đúng khung đang dùng — thêm khi thật sự
# cần khung khác, không đoán trước (cùng nguyên tắc _INTERVAL_MAP của
# bybit_client.py).
_TIMEFRAME_MAP = {"1D": "1d", "1d": "1d"}

# ccxt chuẩn hoá trạng thái lệnh về các chuỗi này bất kể sàn cụ thể (xác
# nhận qua ccxt/base/types.py::Order — trường "status") — khác quy ước
# "New"/"Filled"/... của Bybit v5.
_ORDER_STATUS_MAP = {
    "open": OrderStatus.NEW,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}


class CCXTClient(ExchangeClient):
    """Bọc thư viện `ccxt` — mặc định Binance, xem docstring module.

    Symbol truyền vào mọi phương thức dùng quy ước toàn hệ thống, không
    dấu gạch chéo (vd. `"BTCUSDT"`, xem `settings.yaml: strategy.symbol`)
    — giống hệt `BybitClient`. Chuyển đổi sang ký hiệu hợp nhất của ccxt
    (`"BTC/USDT"`) xảy ra nội bộ, tầng trên không cần biết ccxt tồn tại
    (đúng mục đích của `ExchangeClient` ABC).
    """

    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
        quote_asset: str = "USDT",
        input_fn: Callable[[str], str] = input,
        exchange: Optional[Any] = None,
    ) -> None:
        """`symbol` (quy ước toàn hệ thống, vd. `"BTCUSDT"` — xem
        `settings.yaml: strategy.symbol`) bắt buộc truyền từ config, không
        có default ngầm — dự án chỉ giao dịch một symbol duy nhất
        (CLAUDE.md), và `get_open_orders`/`cancel_order` CẦN biết symbol
        này để gọi ccxt hiệu quả (xem `_call_with_retry` docstring: gọi
        `fetch_open_orders()` KHÔNG kèm symbol trên Binance bị chặn bởi
        chính ccxt — `ExchangeError` "WARNING... 10 times more" rate-limit
        weight — xác nhận bằng gọi thật, không suy luận từ tài liệu; luôn
        truyền symbol tường minh để tránh cả lỗi lẫn phí rate-limit thừa).

        `exchange` tiêm được (mặc định `None` -> dựng đối tượng ccxt thật)
        — chỉ để test thay bằng một đối tượng giả không cần mạng/API key
        thật; caller thường (main.py) không bao giờ cần truyền tham số
        này."""
        if not testnet:
            require_live_confirmation(input_fn)

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"ccxt không hỗ trợ sàn {exchange_id!r}")

        self.testnet = testnet
        self.quote_asset = quote_asset
        self._exchange = (
            exchange
            if exchange is not None
            else exchange_class(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                }
            )
        )
        if testnet:
            self._exchange.set_sandbox_mode(True)
        self._exchange.load_markets()
        self._trading_symbol = symbol
        self._unified_symbol = self._to_ccxt_symbol(symbol)

    # ------------------------------------------------------------------
    # Gọi API — WHITELIST retry, cùng nguyên tắc broker/bybit_client.py,
    # dựng trên cây kế thừa exception của ccxt (xác nhận bằng introspection
    # thật trên ccxt.NetworkError/ExchangeError, không suy luận từ tên):
    #   - ccxt.NetworkError (và các lớp con: RequestTimeout,
    #     ExchangeNotAvailable, RateLimitExceeded, DDoSProtection,
    #     InvalidNonce, OnMaintenance) — lỗi nhất thời, có cơ hội tự khỏi —
    #     backoff mũ rồi thử lại, tối đa _MAX_RETRIES lần.
    #   - ccxt.ExchangeError (và các lớp con: AuthenticationError,
    #     InsufficientFunds, InvalidOrder, BadSymbol, BadRequest,
    #     OrderNotFound, PermissionDenied...) — sàn cố tình từ chối yêu
    #     cầu, KHÔNG tự khỏi theo thời gian — thất bại NGAY, log nguyên văn
    #     message (đã chứa mã lỗi gốc của sàn, ccxt không che mất).
    # ------------------------------------------------------------------

    def _call_with_retry(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            try:
                return fn(*args, **kwargs)
            except ccxt.NetworkError as exc:
                attempt += 1
                if attempt > _MAX_RETRIES:
                    logger.error(
                        "%s — hết %d lần thử, dừng: %s", type(exc).__name__, _MAX_RETRIES, exc
                    )
                    raise
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "%s (lần %d/%d) — backoff %ss, thử lại: %s",
                    type(exc).__name__,
                    attempt,
                    _MAX_RETRIES,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
            except ccxt.ExchangeError as exc:
                logger.error("Sàn từ chối yêu cầu — KHÔNG retry. %s: %s", type(exc).__name__, exc)
                raise

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """`"BTCUSDT"` (quy ước toàn hệ thống) -> `"BTC/USDT"` (ký hiệu hợp
        nhất ccxt).

        KHÔNG dùng `self._exchange.market(symbol)` trực tiếp với id thô
        của sàn — xác nhận bằng gọi thật: Binance có CẢ spot
        (`"BTC/USDT"`) LẪN USDT-M perpetual (`"BTC/USDT:USDT"`) cùng chia
        sẻ id thô `"BTCUSDT"` trong `markets_by_id`; ccxt giải quyết nhập
        nhằng đó bằng thứ tự nội bộ không có gì đảm bảo ổn định qua phiên
        bản, và dự án này CHỈ giao dịch spot, không leverage (CLAUDE.md).
        Tách hậu tố `quote_asset` bằng chuỗi, tường minh, không phụ thuộc
        cách ccxt sắp xếp `markets_by_id`.
        """
        if symbol.endswith(self.quote_asset):
            base = symbol[: -len(self.quote_asset)]
            unified = f"{base}/{self.quote_asset}"
            if unified in self._exchange.markets:
                return unified
        # symbol đã ở dạng "BTC/USDT", hoặc không khớp hậu tố quote_asset —
        # thử nguyên văn, để lỗi lộ rõ nếu symbol thật sự không hợp lệ thay
        # vì âm thầm trả về symbol sai.
        return symbol

    # ------------------------------------------------------------------
    # Tài khoản
    # ------------------------------------------------------------------

    def get_balance(self) -> Balance:
        response = self._call_with_retry(self._exchange.fetch_balance)
        total = Decimal(str(response.get("total", {}).get(self.quote_asset) or "0"))
        free = Decimal(str(response.get("free", {}).get(self.quote_asset) or "0"))
        used = Decimal(str(response.get("used", {}).get(self.quote_asset) or "0"))
        return Balance(asset=self.quote_asset, total=total, available=free, locked=used)

    def get_positions(self) -> list[Position]:
        """Spot KHÔNG có khái niệm "position" native (đó là đối tượng của
        derivatives/margin) — với spot, "vị thế" chỉ là số dư coin cơ sở.
        `entry_price`/`unrealized_pnl` không suy ra được chỉ từ số dư sàn;
        đây là công việc của `broker/position_tracker.py::PositionTracker`
        (theo dõi cục bộ, cập nhật mỗi lần khớp lệnh). Phương thức này trả
        về snapshot best-effort giống hệt `BybitClient.get_positions()`:
        nếu có số dư base asset, coi đó là một "position" với
        `entry_price=current_price` (không biết entry thật) và
        `unrealized_pnl=0` — CHỈ dùng để đối soát thô.
        """
        response = self._call_with_retry(self._exchange.fetch_balance)
        totals = response.get("total", {})
        positions: list[Position] = []
        for asset, raw_qty in totals.items():
            if asset == self.quote_asset or not raw_qty:
                continue
            qty = Decimal(str(raw_qty))
            if qty <= 0:
                continue
            symbol = f"{asset}{self.quote_asset}"
            current_price = self._get_last_price(symbol)
            if current_price <= 0:
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    entry_price=current_price,
                    current_price=current_price,
                    unrealized_pnl=Decimal("0"),
                )
            )
        return positions

    def _get_last_price(self, symbol: str) -> Decimal:
        unified = self._to_ccxt_symbol(symbol)
        if unified not in self._exchange.markets:
            # Coin trong ví không có thị trường spot với quote_asset (vd.
            # bụi coin từ airdrop/rebate) — không định giá được, không phải
            # lỗi cần retry.
            return Decimal("0")
        ticker = self._call_with_retry(self._exchange.fetch_ticker, unified)
        last = ticker.get("last")
        return Decimal(str(last)) if last is not None else Decimal("0")

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        unified = self._to_ccxt_symbol(symbol)
        market = self._exchange.markets.get(unified)
        if market is None:
            raise ValueError(f"ccxt không có market cho {symbol!r} (unified={unified!r})")
        precision = market["precision"]
        limits = market["limits"]
        return InstrumentRules(
            symbol=symbol,
            base_precision=Decimal(str(precision["amount"])),
            quote_precision=Decimal(str(precision.get("quote", "0.01"))),
            tick_size=Decimal(str(precision["price"])),
            min_order_qty=Decimal(str(limits["amount"]["min"])),
            min_order_amt=Decimal(str(limits["cost"]["min"])),
            max_order_qty=Decimal(str(limits["amount"]["max"])),
        )

    # ------------------------------------------------------------------
    # Dữ liệu giá
    # ------------------------------------------------------------------

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Phân trang tiến (ccxt `fetch_ohlcv` trả cũ nhất trước theo
        `since`, khác Bybit trả mới nhất trước) — dùng cho live loop lấy
        vài trăm bar gần nhất để warm-up chỉ báo, KHÔNG dùng cho backtest
        dài hạn (xem `data/history_loader.py` cho nguồn dài hạn riêng qua
        endpoint raw của Binance)."""
        timeframe = _TIMEFRAME_MAP.get(interval)
        if timeframe is None:
            raise ValueError(f"Chưa hỗ trợ interval {interval!r} — chỉ {list(_TIMEFRAME_MAP)}")

        unified = self._to_ccxt_symbol(symbol)
        start = start if start.tzinfo is not None else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        rows: list[list[Any]] = []
        cursor_since_ms = start_ms
        while cursor_since_ms <= end_ms:
            page = self._call_with_retry(
                self._exchange.fetch_ohlcv,
                unified,
                timeframe=timeframe,
                since=cursor_since_ms,
                limit=_MAX_KLINES_PER_REQUEST,
            )
            if not page:
                break
            rows.extend(page)
            newest_ms = int(page[-1][0])
            if len(page) < _MAX_KLINES_PER_REQUEST or newest_ms <= cursor_since_ms:
                break
            cursor_since_ms = newest_ms + 1

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        start_ts = pd.Timestamp(start_ms, unit="ms", tz="UTC")
        end_ts = pd.Timestamp(end_ms, unit="ms", tz="UTC")
        windowed = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        return windowed[["open", "high", "low", "close", "volume"]]

    # ------------------------------------------------------------------
    # Lệnh
    # ------------------------------------------------------------------

    def submit_order(self, order: OrderRequest) -> OrderResult:
        unified = self._to_ccxt_symbol(order.symbol)
        side = "buy" if order.side is OrderSide.BUY else "sell"
        order_type = "limit" if order.order_type is OrderType.LIMIT else "market"
        price: Optional[str] = None
        if order.order_type is OrderType.LIMIT:
            if order.price is None:
                raise ValueError("OrderRequest LIMIT phải có price")
            price = str(order.price)

        # qty/price truyền dạng str(Decimal), không phải float — CLAUDE.md
        # bất biến #3, xem docstring module. `clientOrderId` trong params
        # là cách ccxt map orderLinkId sang tham số gốc của Binance
        # (`newClientOrderId`) — xác nhận bằng grep trực tiếp
        # ccxt/binance.py::create_order, không suy luận từ tài liệu.
        response = self._call_with_retry(
            self._exchange.create_order,
            unified,
            order_type,
            side,
            str(order.qty),
            price,
            {"clientOrderId": order.order_link_id},
        )
        status = _ORDER_STATUS_MAP.get(str(response.get("status")), OrderStatus.NEW)
        filled = response.get("filled")
        average = response.get("average")
        return OrderResult(
            order_id=str(response["id"]),
            order_link_id=response.get("clientOrderId") or order.order_link_id,
            status=status,
            filled_qty=Decimal(str(filled)) if filled is not None else Decimal("0"),
            avg_fill_price=Decimal(str(average)) if average is not None else None,
            raw_response=response,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Khác `BybitClient.cancel_order` (phải tra `symbol` từ danh sách
        lệnh mở trước, vì Bybit query "mọi symbol" miễn phí): CCXTClient
        chỉ giao dịch đúng một symbol đã cấu hình (`self._unified_symbol`,
        xem `__init__`), nên không cần tra cứu — dùng thẳng, ít một lượt
        gọi mạng."""
        try:
            self._call_with_retry(self._exchange.cancel_order, order_id, self._unified_symbol)
            return True
        except ccxt.ExchangeError as exc:
            # vd. OrderNotFound (đã khớp/huỷ rồi, hoặc id sai) — cùng kết
            # quả với lỗi API thật: không huỷ được, trả False chứ không để
            # lộ traceback (ExchangeClient.cancel_order cam kết trả bool,
            # không raise).
            logger.warning("cancel_order(%s) thất bại: %s", order_id, exc)
            return False

    def get_open_orders(self) -> list[Order]:
        # Truyền symbol tường minh — xem __init__ docstring: ccxt tự chặn
        # fetch_open_orders() KHÔNG kèm symbol trên Binance (ExchangeError
        # "WARNING... 10 times more" rate-limit weight, xác nhận bằng gọi
        # thật), và dự án chỉ giao dịch một symbol nên không mất gì khi
        # luôn truyền nó.
        response = self._call_with_retry(self._exchange.fetch_open_orders, self._unified_symbol)
        orders: list[Order] = []
        for item in response:
            symbol = item.get("symbol", "")
            created_ms = item.get("timestamp")
            price = item.get("price")
            orders.append(
                Order(
                    order_id=str(item["id"]),
                    order_link_id=item.get("clientOrderId") or "",
                    symbol=symbol.replace("/", ""),
                    side=OrderSide.BUY if item.get("side") == "buy" else OrderSide.SELL,
                    order_type=(
                        OrderType.LIMIT if item.get("type") == "limit" else OrderType.MARKET
                    ),
                    qty=Decimal(str(item.get("amount") or "0")),
                    price=Decimal(str(price)) if price is not None else None,
                    status=_ORDER_STATUS_MAP.get(str(item.get("status")), OrderStatus.NEW),
                    created_at=(
                        datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                        if created_ms is not None
                        else datetime.now(timezone.utc)
                    ),
                )
            )
        return orders

    def get_orderbook(self, symbol: str) -> OrderBook:
        """Để `risk_manager.check_spread()` kiểm tra trước khi duyệt lệnh
        (§5.4). `limit=5` — ccxt/Binance không đảm bảo `limit=1` hợp lệ ở
        mọi sàn (một số yêu cầu limit tối thiểu), lấy dư rồi chỉ dùng
        best bid/ask."""
        unified = self._to_ccxt_symbol(symbol)
        response = self._call_with_retry(self._exchange.fetch_order_book, unified, 5)
        bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in response["bids"]]
        asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in response["asks"]]
        ts_ms = response.get("timestamp")
        # Một số sàn (Binance testnet, xác nhận bằng gọi thật) không trả
        # timestamp trong order book — dùng đồng hồ máy chủ ccxt thay vì để
        # None lọt vào OrderBook.timestamp (đường trên có thể so sánh thời
        # gian, vd. cảnh báo dữ liệu cũ).
        timestamp = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if ts_ms is not None
            else datetime.now(timezone.utc)
        )
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=timestamp)

    def get_server_time(self) -> int:
        """Epoch milliseconds theo đồng hồ Binance — `fetch_time()` của
        ccxt trả về int mili-giây trực tiếp (cùng lời gọi đã xác nhận qua
        `ops/health_check.py::check_exchange_reachable`, xem
        `monitoring/clock.py::measure_clock_drift()` cho cách dùng có
        hiệu chỉnh round-trip)."""
        return int(self._call_with_retry(self._exchange.fetch_time))
