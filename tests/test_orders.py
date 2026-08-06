"""tests.test_orders — OrderExecutor: idempotency, partial fill, modify_stop.

Dùng `_FakeExchange` (implement đầy đủ `ExchangeClient` ABC) thay vì mock
rời rạc — để mọi thay đổi chữ ký ở `broker/base.py` làm file này fail lúc
khởi tạo, thay vì âm thầm test một interface đã lỗi thời.

Không cần mạng, không cần API key: mọi test ở đây là logic thuần của
`OrderExecutor`. Các tiêu chí nghiệm thu cần testnet thật (kết nối, đặt/
huỷ lệnh qua sàn, ngắt mạng giữa chừng) nằm ở checklist thủ công trong
`prompts/phase-09-bybit-broker.md` — không giả lập được ở đây một cách
trung thực.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

import pandas as pd

from broker.base import (
    Balance,
    ExchangeClient,
    Order,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
)
from broker.instrument_rules import InstrumentRules
from broker.order_executor import OrderExecutor

_T0 = datetime(2026, 8, 5, 0, 0, 0, tzinfo=timezone.utc)

_BTCUSDT_RULES = InstrumentRules(
    symbol="BTCUSDT",
    base_precision=Decimal("0.000001"),
    quote_precision=Decimal("0.0000001"),
    tick_size=Decimal("0.1"),
    min_order_qty=Decimal("0.000001"),
    min_order_amt=Decimal("5"),
    max_order_qty=Decimal("10"),
)


@dataclass(frozen=True)
class _Signal:
    """Khớp `broker.order_executor.SignalLike` — KHÔNG import
    core.regime_strategies.Signal (giữ tầng broker độc lập với strategy)."""

    symbol: str = "BTCUSDT"
    target_allocation_pct: Decimal = Decimal("0.95")
    timestamp: datetime = _T0
    entry_price: Decimal = Decimal("64000")


class _FakeExchange(ExchangeClient):
    """Sàn giả lập trong bộ nhớ. Ghi lại mọi lệnh đã nhận (`submitted`) để
    test kiểm chứng ĐÚNG cái được gửi đi, và mô phỏng idempotency phía sàn
    bằng cách từ chối `order_link_id` đã thấy (giống Bybit thật)."""

    def __init__(
        self,
        balance_usdt: Decimal = Decimal("10000"),
        position_qty: Decimal = Decimal("0"),
        reject_duplicate_link_id: bool = True,
    ) -> None:
        self.submitted: list[OrderRequest] = []
        self.cancelled: list[str] = []
        self._balance_usdt = balance_usdt
        self._position_qty = position_qty
        self._reject_duplicate_link_id = reject_duplicate_link_id
        self._seen_link_ids: set[str] = set()
        self._open_orders: list[Order] = []
        self._next_order_id = 1

    def get_balance(self) -> Balance:
        return Balance(
            asset="USDT",
            total=self._balance_usdt,
            available=self._balance_usdt,
            locked=Decimal("0"),
        )

    def get_positions(self) -> list[Position]:
        if self._position_qty <= 0:
            return []
        return [
            Position(
                symbol="BTCUSDT",
                qty=self._position_qty,
                entry_price=Decimal("64000"),
                current_price=Decimal("64000"),
                unrealized_pnl=Decimal("0"),
            )
        ]

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        return _BTCUSDT_RULES

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def submit_order(self, order: OrderRequest) -> OrderResult:
        if self._reject_duplicate_link_id and order.order_link_id in self._seen_link_ids:
            # Bybit thật trả retCode khác 0 cho orderLinkId trùng; ở đây
            # phản ánh bằng OrderResult REJECTED để test kiểm chứng được
            # rằng lệnh thứ hai KHÔNG tạo vị thế mới.
            return OrderResult(
                order_id="",
                order_link_id=order.order_link_id,
                status=OrderStatus.REJECTED,
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                raw_response={"retMsg": "duplicate orderLinkId"},
            )
        self._seen_link_ids.add(order.order_link_id)
        self.submitted.append(order)
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        return OrderResult(
            order_id=order_id,
            order_link_id=order.order_link_id,
            status=OrderStatus.NEW,
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            raw_response={},
        )

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    def get_open_orders(self) -> list[Order]:
        return list(self._open_orders)

    def get_orderbook(self, symbol: str) -> OrderBook:
        raise NotImplementedError("Không dùng trong test này")

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        raise NotImplementedError("Không dùng trong test này")

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        raise NotImplementedError("Không dùng trong test này")


def _make_executor(exchange: Optional[_FakeExchange] = None) -> tuple[OrderExecutor, _FakeExchange]:
    ex = exchange if exchange is not None else _FakeExchange()
    # timeout_seconds=0 — bỏ hẳn vòng poll chờ khớp, test không phải sleep
    # thật. Logic timeout/huỷ được test riêng ở
    # test_unfilled_order_cancelled_after_timeout.
    return OrderExecutor(ex, limit_offset_pct=Decimal("0.05"), timeout_seconds=0), ex


# ----------------------------------------------------------------------
# orderLinkId — khoá idempotency (CLAUDE.md bất biến #8)
# ----------------------------------------------------------------------


def test_order_link_id_deterministic() -> None:
    executor, _ = _make_executor()
    args = ("BTCUSDT", _T0, Decimal("0.95"))

    first = executor.generate_order_link_id(*args)
    second = executor.generate_order_link_id(*args)

    assert first == second
    # Một instance MỚI (mô phỏng bot vừa crash-restart) phải cho cùng id —
    # đây chính là điều làm idempotency hoạt động qua restart.
    fresh_executor, _ = _make_executor()
    assert fresh_executor.generate_order_link_id(*args) == first


def test_order_link_id_differs_when_any_input_differs() -> None:
    executor, _ = _make_executor()
    base = executor.generate_order_link_id("BTCUSDT", _T0, Decimal("0.95"))

    assert executor.generate_order_link_id("ETHUSDT", _T0, Decimal("0.95")) != base
    assert executor.generate_order_link_id("BTCUSDT", _T0 + timedelta(days=1), Decimal("0.95")) != base
    assert executor.generate_order_link_id("BTCUSDT", _T0, Decimal("0.60")) != base


def test_order_link_id_within_bybit_length_limit() -> None:
    """Bybit v5 giới hạn orderLinkId 36 ký tự — vượt là lệnh bị từ chối ở
    sàn, không phải lỗi rõ ràng ở phía bot."""
    executor, _ = _make_executor()
    link_id = executor.generate_order_link_id(
        "SOMEVERYLONGSYMBOLNAMEUSDT", _T0, Decimal("0.123456789")
    )
    assert 0 < len(link_id) <= 36
    assert link_id.isalnum()  # không ký tự lạ (`:`/`+` của ISO timestamp)


def test_duplicate_order_link_id_rejected_by_exchange() -> None:
    """Gửi HAI LẦN cùng một signal → chỉ MỘT lệnh thật được đặt.

    Đây là kịch bản crash-restart: bot đặt lệnh, crash trước khi ghi nhận,
    khởi động lại và gửi lại cùng signal đó."""
    executor, exchange = _make_executor()
    signal = _Signal()

    first = executor.submit_order(signal)
    second = executor.submit_order(signal)

    assert first.order_link_id == second.order_link_id
    assert len(exchange.submitted) == 1  # chỉ một lệnh tới sàn
    assert second.status is OrderStatus.REJECTED


# ----------------------------------------------------------------------
# submit_order — sizing, giá LIMIT, huỷ khi quá hạn
# ----------------------------------------------------------------------


def test_submit_order_sizes_from_target_allocation() -> None:
    executor, exchange = _make_executor()
    executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))

    assert len(exchange.submitted) == 1
    order = exchange.submitted[0]
    # equity 10000, target 50%, giá 64000 -> 5000/64000 = 0.078125 BTC
    assert order.qty == Decimal("0.078125")
    assert order.side.value == "BUY"


def test_submit_order_limit_price_offset_direction() -> None:
    """Mua đặt DƯỚI giá hiện tại, bán đặt TRÊN — ±0.05% quanh entry_price."""
    executor, exchange = _make_executor()
    executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))
    buy_price = exchange.submitted[0].price
    assert buy_price is not None
    assert buy_price < Decimal("64000")
    assert buy_price >= Decimal("64000") * Decimal("0.9995")

    # Đang giữ nhiều hơn target -> bán bớt.
    sell_exchange = _FakeExchange(balance_usdt=Decimal("1000"), position_qty=Decimal("0.5"))
    sell_executor, _ = _make_executor(sell_exchange)
    sell_executor.submit_order(_Signal(target_allocation_pct=Decimal("0.10")))
    sell_order = sell_exchange.submitted[0]
    assert sell_order.side.value == "SELL"
    assert sell_order.price is not None
    assert sell_order.price > Decimal("64000")


def test_submit_order_noop_when_already_at_target() -> None:
    """Delta = 0 thì KHÔNG gửi lệnh nào — tránh trả phí cho một lệnh không
    đổi gì (CLAUDE.md bất biến #7: phí là chi phí thật, không bỏ qua)."""
    # equity = 1000 USDT + 0.140625 BTC * 64000 = 10000; target 90% -> đúng qty đang giữ.
    exchange = _FakeExchange(balance_usdt=Decimal("1000"), position_qty=Decimal("0.140625"))
    executor, _ = _make_executor(exchange)

    result = executor.submit_order(_Signal(target_allocation_pct=Decimal("0.9")))

    assert exchange.submitted == []
    assert result.filled_qty == Decimal("0")


def test_submit_order_rejects_below_min_order_amt() -> None:
    """Lệnh dưới 5 USDT bị chặn TRƯỚC khi gửi — rẻ hơn nhiều so với bị sàn
    từ chối (Brain-Crypto-Bybit.md §6.2)."""
    exchange = _FakeExchange(balance_usdt=Decimal("4"))
    executor, _ = _make_executor(exchange)

    result = executor.submit_order(_Signal(target_allocation_pct=Decimal("0.95")))

    assert exchange.submitted == []
    assert result.status is OrderStatus.REJECTED
    assert "min_order_amt" in str(result.raw_response)


def test_unfilled_order_cancelled_after_timeout() -> None:
    """Lệnh còn trong danh sách mở sau timeout → bị huỷ."""
    exchange = _FakeExchange()
    executor = OrderExecutor(exchange, limit_offset_pct=Decimal("0.05"), timeout_seconds=1)

    # Ép lệnh "còn treo" bằng cách trả về nó ở get_open_orders sau khi đặt.
    original_submit = exchange.submit_order

    def _submit_and_keep_open(order: OrderRequest) -> OrderResult:
        result = original_submit(order)
        exchange._open_orders.append(
            Order(
                order_id=result.order_id,
                order_link_id=result.order_link_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                qty=order.qty,
                price=order.price,
                status=OrderStatus.NEW,
                created_at=_T0,
            )
        )
        return result

    exchange.submit_order = _submit_and_keep_open  # type: ignore[method-assign]

    result = executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))

    assert exchange.cancelled == [result.order_id]


# ----------------------------------------------------------------------
# Khớp một phần
# ----------------------------------------------------------------------


def test_partial_fill_tracks_remaining_qty() -> None:
    executor, exchange = _make_executor()
    executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))
    submitted_qty = exchange.submitted[0].qty
    link_id = exchange.submitted[0].order_link_id

    partial = OrderResult(
        order_id="1",
        order_link_id=link_id,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_qty=submitted_qty / 4,
        avg_fill_price=Decimal("64000"),
        raw_response={},
    )
    remaining = executor.handle_partial_fill(partial)

    assert remaining == submitted_qty - submitted_qty / 4
    assert remaining > 0


def test_fully_filled_leaves_zero_remaining() -> None:
    executor, exchange = _make_executor()
    executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))
    submitted_qty = exchange.submitted[0].qty
    link_id = exchange.submitted[0].order_link_id

    filled = OrderResult(
        order_id="1",
        order_link_id=link_id,
        status=OrderStatus.FILLED,
        filled_qty=submitted_qty,
        avg_fill_price=Decimal("64000"),
        raw_response={},
    )
    assert executor.handle_partial_fill(filled) == Decimal("0")


def test_overfill_never_returns_negative_remaining() -> None:
    """filled > requested (không nên xảy ra, nhưng nếu sàn báo vậy) phải
    trả 0, không phải số âm — số âm sẽ biến thành lệnh NGƯỢC chiều ở
    caller."""
    executor, exchange = _make_executor()
    executor.submit_order(_Signal(target_allocation_pct=Decimal("0.50")))
    link_id = exchange.submitted[0].order_link_id

    overfilled = OrderResult(
        order_id="1",
        order_link_id=link_id,
        status=OrderStatus.FILLED,
        filled_qty=exchange.submitted[0].qty * 2,
        avg_fill_price=Decimal("64000"),
        raw_response={},
    )
    assert executor.handle_partial_fill(overfilled) == Decimal("0")


def test_partial_fill_unknown_order_returns_zero() -> None:
    """order_link_id lạ (không do executor này đặt) → 0, không đoán bừa."""
    executor, _ = _make_executor()
    unknown = OrderResult(
        order_id="999",
        order_link_id="khong-ton-tai",
        status=OrderStatus.PARTIALLY_FILLED,
        filled_qty=Decimal("0.01"),
        avg_fill_price=Decimal("64000"),
        raw_response={},
    )
    assert executor.handle_partial_fill(unknown) == Decimal("0")


# ----------------------------------------------------------------------
# modify_stop — chỉ siết, không bao giờ nới (CLAUDE.md bất biến #5)
# ----------------------------------------------------------------------


def test_modify_stop_never_loosens() -> None:
    executor, _ = _make_executor()

    assert executor.modify_stop("BTCUSDT", Decimal("60000")) is True  # lần đầu, chưa có stop
    assert executor.modify_stop("BTCUSDT", Decimal("61000")) is True  # siết chặt hơn -> OK
    assert executor.modify_stop("BTCUSDT", Decimal("59000")) is False  # nới rộng -> TỪ CHỐI
    assert executor.modify_stop("BTCUSDT", Decimal("61000")) is False  # bằng stop hiện tại -> không đổi

    # Stop cuối cùng vẫn là mức chặt nhất đã đặt, không bị lần nới rộng làm hỏng.
    assert executor.modify_stop("BTCUSDT", Decimal("61000.01")) is True


def test_modify_stop_is_per_symbol() -> None:
    executor, _ = _make_executor()
    assert executor.modify_stop("BTCUSDT", Decimal("60000")) is True
    # Symbol khác chưa có stop nào — không bị chặn bởi stop của BTCUSDT.
    assert executor.modify_stop("ETHUSDT", Decimal("3000")) is True
