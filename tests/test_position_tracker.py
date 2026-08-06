"""tests.test_position_tracker — đối soát REST khi khởi động và định kỳ.

File MỚI — không nằm trong bốn/năm file bắt buộc ở CLAUDE.md bất biến #15,
nhưng logic đối soát ("tin sàn khi lệch") là đúng loại bug im lặng nguy
hiểm nếu sai (Brain-Crypto-Bybit.md §6.5), nên viết test thật thay vì chỉ
đọc code.

`on_execution()` đã bị xoá (2026-08-06, REST polling thay WebSocket, xem
`docs/DECISIONS.md`) — không còn cơ chế đẩy `OrderResult` vào tracker
giữa hai lần đối soát; `poll()` (mới) là đường cập nhật duy nhất trong lúc
chạy, cùng logic `reconcile_on_startup()`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd

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
from broker.position_tracker import PositionTracker


class _FakeExchange(ExchangeClient):
    def __init__(self, positions: list[Position] | None = None) -> None:
        self._positions = positions or []

    def get_balance(self) -> Balance:
        return Balance(asset="USDT", total=Decimal("0"), available=Decimal("0"), locked=Decimal("0"))

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        raise NotImplementedError

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raise NotImplementedError

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    def get_orderbook(self, symbol: str) -> OrderBook:
        raise NotImplementedError


def _position(symbol: str = "BTCUSDT", qty: Decimal = Decimal("0.1")) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        unrealized_pnl=Decimal("100"),
    )


# ----------------------------------------------------------------------
# reconcile_on_startup
# ----------------------------------------------------------------------


def test_reconcile_creates_local_position_from_exchange_when_unknown() -> None:
    exchange = _FakeExchange(positions=[_position(qty=Decimal("0.2"))])
    tracker = PositionTracker(exchange)

    tracker.reconcile_on_startup()

    pos = tracker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.2")


def test_reconcile_trusts_exchange_when_local_qty_differs() -> None:
    exchange = _FakeExchange(positions=[_position(qty=Decimal("0.5"))])
    tracker = PositionTracker(exchange)
    tracker.reconcile_on_startup()  # local giờ có 0.5

    # Sàn báo qty khác (giả lập bot offline lúc có lệnh khớp) — tin sàn.
    exchange._positions = [_position(qty=Decimal("0.3"))]
    tracker.reconcile_on_startup()

    assert tracker.get_position("BTCUSDT").qty == Decimal("0.3")  # type: ignore[union-attr]


def test_reconcile_removes_local_position_closed_while_offline() -> None:
    exchange = _FakeExchange(positions=[_position()])
    tracker = PositionTracker(exchange)
    tracker.reconcile_on_startup()
    assert tracker.get_position("BTCUSDT") is not None

    exchange._positions = []  # đã đóng trong lúc bot offline
    tracker.reconcile_on_startup()

    assert tracker.get_position("BTCUSDT") is None


def test_reconcile_leaves_untouched_symbols_alone() -> None:
    exchange = _FakeExchange(positions=[_position("BTCUSDT"), _position("ETHUSDT", Decimal("1.0"))])
    tracker = PositionTracker(exchange)
    tracker.reconcile_on_startup()

    assert {p.symbol for p in tracker.get_all_positions()} == {"BTCUSDT", "ETHUSDT"}


# ----------------------------------------------------------------------
# poll() — cùng logic reconcile_on_startup(), gọi định kỳ trong lúc chạy
# ----------------------------------------------------------------------


def test_poll_creates_local_position_from_exchange_when_unknown() -> None:
    exchange = _FakeExchange(positions=[_position(qty=Decimal("0.2"))])
    tracker = PositionTracker(exchange)

    tracker.poll()

    pos = tracker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.2")


def test_poll_picks_up_fill_that_happened_since_last_poll() -> None:
    """Không còn on_execution() đẩy trực tiếp — poll() định kỳ là cách
    DUY NHẤT tracker biết một lệnh vừa khớp làm thay đổi qty trên sàn."""
    exchange = _FakeExchange(positions=[_position(qty=Decimal("0.1"))])
    tracker = PositionTracker(exchange)
    tracker.poll()
    assert tracker.get_position("BTCUSDT").qty == Decimal("0.1")  # type: ignore[union-attr]

    exchange._positions = [_position(qty=Decimal("0.15"))]  # lệnh mua khớp thêm 0.05
    tracker.poll()

    assert tracker.get_position("BTCUSDT").qty == Decimal("0.15")  # type: ignore[union-attr]


def test_poll_removes_position_closed_since_last_poll() -> None:
    exchange = _FakeExchange(positions=[_position()])
    tracker = PositionTracker(exchange)
    tracker.poll()
    assert tracker.get_position("BTCUSDT") is not None

    exchange._positions = []
    tracker.poll()

    assert tracker.get_position("BTCUSDT") is None


# ----------------------------------------------------------------------
# get_all_positions
# ----------------------------------------------------------------------


def test_get_all_positions_returns_independent_list() -> None:
    exchange = _FakeExchange(positions=[_position()])
    tracker = PositionTracker(exchange)
    tracker.poll()

    positions = tracker.get_all_positions()
    positions.clear()

    assert len(tracker.get_all_positions()) == 1  # không bị ảnh hưởng bởi sửa bản trả về
