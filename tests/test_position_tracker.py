"""tests.test_position_tracker — đối soát khi khởi động, cập nhật khi khớp lệnh.

File MỚI — không nằm trong bốn/năm file bắt buộc ở CLAUDE.md bất biến #15,
nhưng logic đối soát ("tin sàn khi lệch") là đúng loại bug im lặng nguy
hiểm nếu sai (Brain-Crypto-Bybit.md §6.5), nên viết test thật thay vì chỉ
đọc code.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable

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

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        raise NotImplementedError

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        raise NotImplementedError


def _position(symbol: str = "BTCUSDT", qty: Decimal = Decimal("0.1")) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        unrealized_pnl=Decimal("100"),
    )


def _fill(
    symbol: str = "BTCUSDT",
    side: str = "Buy",
    qty: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("64000"),
    link_id: str = "abc",
) -> OrderResult:
    return OrderResult(
        order_id="1",
        order_link_id=link_id,
        status=OrderStatus.FILLED,
        filled_qty=qty,
        avg_fill_price=price,
        raw_response={"symbol": symbol, "side": side},
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
# on_execution
# ----------------------------------------------------------------------


def test_on_execution_buy_creates_new_position() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill(side="Buy", qty=Decimal("0.1"), price=Decimal("64000")))

    pos = tracker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.1")
    assert pos.entry_price == Decimal("64000")


def test_on_execution_buy_increases_existing_position() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill(side="Buy", qty=Decimal("0.1")))
    tracker.on_execution(_fill(side="Buy", qty=Decimal("0.05"), price=Decimal("65000")))

    pos = tracker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.15")
    assert pos.current_price == Decimal("65000")  # cập nhật theo giá khớp gần nhất


def test_on_execution_sell_reduces_position() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill(side="Buy", qty=Decimal("0.1")))
    tracker.on_execution(_fill(side="Sell", qty=Decimal("0.04")))

    pos = tracker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.06")


def test_on_execution_sell_closes_position_when_qty_reaches_zero() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill(side="Buy", qty=Decimal("0.1")))
    tracker.on_execution(_fill(side="Sell", qty=Decimal("0.1")))

    assert tracker.get_position("BTCUSDT") is None


def test_on_execution_sell_without_existing_position_does_not_create_negative() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill(side="Sell", qty=Decimal("0.1")))

    assert tracker.get_position("BTCUSDT") is None


def test_on_execution_missing_symbol_or_side_is_skipped_gracefully() -> None:
    tracker = PositionTracker(_FakeExchange())
    incomplete = OrderResult(
        order_id="1",
        order_link_id="x",
        status=OrderStatus.FILLED,
        filled_qty=Decimal("0.1"),
        avg_fill_price=Decimal("64000"),
        raw_response={},  # thiếu symbol/side
    )
    tracker.on_execution(incomplete)  # không raise

    assert tracker.get_all_positions() == []


def test_get_all_positions_returns_independent_list() -> None:
    tracker = PositionTracker(_FakeExchange())
    tracker.on_execution(_fill())
    positions = tracker.get_all_positions()
    positions.clear()

    assert len(tracker.get_all_positions()) == 1  # không bị ảnh hưởng bởi sửa bản trả về
