"""broker.order_executor — submit/cancel/modify qua ExchangeClient, idempotent.

orderLinkId sinh deterministic từ (symbol, bar_timestamp, target_allocation)
là khoá idempotency bắt buộc: thị trường 24/7 nên bot SẼ crash-restart
giữa lúc có lệnh đang chờ; không có khoá này một lần restart có thể nhân
đôi vị thế — xem CLAUDE.md bất biến #8.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Protocol

from broker.base import ExchangeClient, OrderRequest, OrderResult, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)

# Bybit v5 giới hạn orderLinkId tối đa 36 ký tự.
_ORDER_LINK_ID_MAX_LEN = 36

_POLL_INTERVAL_SECONDS = 2.0


class SignalLike(Protocol):
    """Hình dạng tối thiểu cần từ một signal — tránh phụ thuộc trực tiếp
    vào core.regime_strategies.Signal, giữ tầng broker độc lập với tầng
    strategy (đối xứng với CLAUDE.md bất biến #4 áp cho risk_manager).

    `@property` chỉ đọc, không phải thuộc tính thường — Signal thật là
    frozen dataclass, xem ghi chú tương tự ở core/risk_manager.py.

    `entry_price` — Signal thật đã tính giá đóng cửa hiện tại lúc sinh
    signal (`core/regime_strategies.py`, `bars["close"].iloc[-1]`); dùng
    lại đúng giá đó làm tâm cho lệnh LIMIT ±0.05% thay vì gọi thêm một API
    riêng chỉ để lấy giá — tránh một round-trip network không cần thiết
    và tránh lệch giá giữa lúc sinh signal và lúc đặt lệnh.
    """

    @property
    def symbol(self) -> str: ...
    @property
    def target_allocation_pct(self) -> Decimal: ...
    @property
    def timestamp(self) -> datetime: ...
    @property
    def entry_price(self) -> Decimal: ...


class OrderExecutor:
    def __init__(
        self, exchange_client: ExchangeClient, limit_offset_pct: Decimal, timeout_seconds: int
    ) -> None:
        self.exchange_client = exchange_client
        self.limit_offset_pct = limit_offset_pct
        self.timeout_seconds = timeout_seconds

        # Trạng thái cục bộ tối thiểu cần để idempotency/partial-fill/
        # modify_stop hoạt động đúng — KHÔNG phải nguồn sự thật thay cho
        # sàn (đối soát với sàn là việc của broker/position_tracker.py).
        self._requested_qty: dict[str, Decimal] = {}  # order_link_id -> qty đã yêu cầu
        self._current_stops: dict[str, Decimal] = {}  # symbol -> stop hiện tại đã xác nhận

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def generate_order_link_id(self, symbol: str, bar_timestamp: datetime, target_allocation: Decimal) -> str:
        """Deterministic — cùng bộ ba input luôn cho cùng id, để sàn từ
        chối lệnh trùng thay vì đặt hai lần khi bot restart giữa chừng.

        Hash thay vì nối chuỗi thô: giữ trong giới hạn 36 ký tự của Bybit
        bất kể độ dài symbol/timestamp/allocation, và tránh ký tự
        timestamp ISO (dấu `:`, `+`) mà Bybit có thể không chấp nhận trong
        orderLinkId.

        `normalize()` trước khi format: `Decimal("0.30")`,
        `Decimal("0.3")` và `Decimal("0.300")` BẰNG NHAU về giá trị nhưng
        `str()` ra ba chuỗi khác nhau → ba hash khác nhau → ba
        orderLinkId khác nhau cho CÙNG một quyết định phân bổ. Khi đó lớp
        chống trùng im lặng mất tác dụng đúng lúc cần nhất: bot restart,
        tính lại target bằng một đường số học khác (vd. `Decimal("0.3")`
        vs `equity * pct / equity`), sinh id mới, và sàn không còn nhận ra
        đây là lệnh đã đặt.

        `:f` (không phải `str()`) để chặn ký hiệu mũ — `normalize()` biến
        `Decimal("0.00000")` thành `Decimal("0E-5")`, và `str()` giữ
        nguyên dạng đó, lại sinh ra một chuỗi khác cho cùng giá trị 0.
        """
        target_str = f"{target_allocation.normalize():f}"
        raw = f"{symbol}|{bar_timestamp.isoformat()}|{target_str}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return digest[:_ORDER_LINK_ID_MAX_LEN]

    # ------------------------------------------------------------------
    # Đặt lệnh
    # ------------------------------------------------------------------

    def submit_order(self, signal: SignalLike) -> OrderResult:
        """LIMIT mặc định, đặt ±0.05% quanh giá hiện tại; huỷ sau
        `timeout_seconds` nếu chưa khớp.

        Tính qty cần giao dịch từ `target_allocation_pct` (%) chứ không
        nhận thẳng qty — đọc equity/qty hiện tại từ chính sàn
        (`get_balance`/`get_positions`) mỗi lần gọi, không tự giữ state
        song song dễ lệch với sàn thật (đúng tinh thần "đối soát, tin
        sàn" của broker/position_tracker.py).
        """
        symbol = signal.symbol
        order_link_id = self.generate_order_link_id(symbol, signal.timestamp, signal.target_allocation_pct)

        rules = self.exchange_client.get_instrument_rules(symbol)
        balance = self.exchange_client.get_balance()
        current_qty = self._current_qty(symbol)

        equity = balance.total + current_qty * signal.entry_price
        target_notional = equity * signal.target_allocation_pct
        target_qty = rules.round_qty(target_notional / signal.entry_price)
        delta = target_qty - current_qty

        if delta == 0:
            logger.info("submit_order(%s): delta=0, không đặt lệnh (đã ở target).", symbol)
            return OrderResult(
                order_id="",
                order_link_id=order_link_id,
                status=OrderStatus.FILLED,
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                raw_response={"note": "no-op: delta qty = 0"},
            )

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        offset_fraction = self.limit_offset_pct / Decimal("100")
        # Mua: đặt giá THẤP hơn giá hiện tại một chút để có ưu thế làm
        # maker; bán: đặt CAO hơn. Vẫn nằm trong ±0.05% nên gần như chắc
        # khớp trong 30s nếu thị trường không đứng yên hoàn toàn.
        if side is OrderSide.BUY:
            limit_price = signal.entry_price * (Decimal("1") - offset_fraction)
        else:
            limit_price = signal.entry_price * (Decimal("1") + offset_fraction)
        # Hướng làm tròn theo CHIỀU lệnh, không phải luôn xuống: mua làm
        # tròn xuống (không vượt số dư), bán rebalance làm tròn lên (bán là
        # NHẬN tiền — làm tròn xuống là tự nguyện nhận ít hơn). CLAUDE.md
        # bất biến #3 quy định ROUND_DOWN cho SỐ LƯỢNG, không phải giá —
        # xem docstring `InstrumentRules.round_price`.
        limit_price = rules.round_price(limit_price, ROUND_DOWN if side is OrderSide.BUY else ROUND_UP)

        qty = rules.round_qty(abs(delta))

        # Pre-flight số dư KHẢ DỤNG — `equity` ở trên dùng `balance.total`,
        # vốn gồm cả phần đang bị khoá trong lệnh chờ/ký quỹ. Một lệnh mua
        # tính từ `total` có thể vượt `available` và bị sàn từ chối sau một
        # round-trip mạng; tệ hơn, nó khớp MỘT PHẦN rồi để lại trạng thái
        # nửa vời mà `handle_partial_fill` phải dọn. Chặn tại đây rẻ hơn.
        # Chỉ áp cho chiều MUA: bán làm GIẢM exposure và không tiêu số dư.
        if side is OrderSide.BUY:
            required = qty * limit_price
            if required > balance.available:
                logger.warning(
                    "submit_order(%s): cần %s USDT nhưng chỉ có %s khả dụng "
                    "(total=%s — phần chênh đang bị khoá) — TỪ CHỐI, không gửi lệnh.",
                    symbol,
                    required,
                    balance.available,
                    balance.total,
                )
                return OrderResult(
                    order_id="",
                    order_link_id=order_link_id,
                    status=OrderStatus.REJECTED,
                    filled_qty=Decimal("0"),
                    avg_fill_price=None,
                    raw_response={
                        "rejection_reason": (f"notional {required} > available {balance.available}")
                    },
                )

        ok, reason = rules.is_valid_order(qty, limit_price)
        if not ok:
            logger.warning("submit_order(%s): lệnh không hợp lệ (%s) — không gửi.", symbol, reason)
            return OrderResult(
                order_id="",
                order_link_id=order_link_id,
                status=OrderStatus.REJECTED,
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                raw_response={"rejection_reason": reason},
            )

        request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            qty=qty,
            price=limit_price,
            order_link_id=order_link_id,
        )
        self._requested_qty[order_link_id] = qty

        result = self.exchange_client.submit_order(request)
        result = self._wait_for_fill_or_cancel(result)
        return result

    def _current_qty(self, symbol: str) -> Decimal:
        for position in self.exchange_client.get_positions():
            if position.symbol == symbol:
                return position.qty
        return Decimal("0")

    def _wait_for_fill_or_cancel(self, result: OrderResult) -> OrderResult:
        """Poll `get_open_orders()` tới khi khớp hoặc hết `timeout_seconds`
        thì huỷ — không có callback WebSocket đồng bộ ở tầng này, polling
        đơn giản đủ dùng cho tần suất đặt lệnh của hệ thống (tối đa vài
        lần/ngày, không phải market making)."""
        elapsed = 0.0
        while elapsed < self.timeout_seconds:
            time.sleep(min(_POLL_INTERVAL_SECONDS, self.timeout_seconds - elapsed))
            elapsed += _POLL_INTERVAL_SECONDS

            still_open = any(o.order_id == result.order_id for o in self.exchange_client.get_open_orders())
            if not still_open:
                # Không còn trong danh sách mở nữa — coi như đã khớp (hoặc
                # bị huỷ/từ chối ở phía sàn); broker/position_tracker.py
                # tự đối soát filled_qty thật từ sàn ở vòng poll() kế tiếp
                # (REST polling, không còn luồng execution đẩy trực tiếp —
                # xem docs/DECISIONS.md), không suy đoán ở đây.
                return result

        logger.warning(
            "Lệnh %s (%s) chưa khớp sau %ss — huỷ.",
            result.order_id,
            result.order_link_id,
            self.timeout_seconds,
        )
        self.cancel_order(result.order_id)
        return result

    # ------------------------------------------------------------------
    # Khớp một phần
    # ------------------------------------------------------------------

    def handle_partial_fill(self, order_result: OrderResult) -> Decimal:
        """Phần còn thiếu cần rebalance tiếp = qty đã yêu cầu lúc đặt lệnh
        (`generate_order_link_id`/`submit_order` đã ghi lại) trừ
        `filled_qty` của kết quả mới nhất. Không suy ra "đã yêu cầu bao
        nhiêu" từ chính `order_result` — nó không mang thông tin đó, chỉ
        `submit_order` (nơi quyết định qty) mới biết.
        """
        requested = self._requested_qty.get(order_result.order_link_id)
        if requested is None:
            # `_requested_qty` sống trong bộ nhớ và MẤT khi tiến trình
            # restart — đúng tình huống nó cần nhất (crash giữa lúc có lệnh
            # khớp một phần). Hỏi SÀN thay vì persist ra file: sàn là nguồn
            # sự thật, không mất khi restart, và nhất quán với nguyên tắc
            # "đối soát, tin sàn" đã có ở broker/position_tracker.py. Thêm
            # một file trạng thái nữa chỉ là thêm một thứ có thể lệch.
            remaining_on_exchange = self._remaining_qty_from_exchange(order_result.order_link_id)
            if remaining_on_exchange is not None:
                logger.info(
                    "handle_partial_fill: không có qty trong bộ nhớ cho order_link_id=%s "
                    "(restart?) — đọc từ sàn: còn lại %s.",
                    order_result.order_link_id,
                    remaining_on_exchange,
                )
                return remaining_on_exchange
            logger.warning(
                "handle_partial_fill: không có qty đã yêu cầu cho order_link_id=%s "
                "trong bộ nhớ LẪN trên sàn (lệnh đã đóng hoàn toàn, hoặc không phải "
                "lệnh do OrderExecutor này đặt) — trả 0.",
                order_result.order_link_id,
            )
            return Decimal("0")
        remaining = requested - order_result.filled_qty
        return remaining if remaining > 0 else Decimal("0")

    def _remaining_qty_from_exchange(self, order_link_id: str) -> Decimal | None:
        """Phần chưa khớp của lệnh còn mở, đọc từ sàn: `qty - filled_qty`.

        `None` = không tìm thấy lệnh nào còn mở mang id này — khác hẳn
        `Decimal("0")` ("tìm thấy, đã khớp hết"). Caller cần phân biệt hai
        trường hợp để không log nhầm.
        """
        for order in self.exchange_client.get_open_orders():
            if order.order_link_id != order_link_id:
                continue
            remaining = order.qty - order.filled_qty
            return remaining if remaining > 0 else Decimal("0")
        return None

    # ------------------------------------------------------------------
    # Stop loss — chỉ siết, không bao giờ nới (CLAUDE.md bất biến #5)
    # ------------------------------------------------------------------

    def modify_stop(self, symbol: str, new_stop: Decimal) -> bool:
        """True nếu đã áp dụng — chỉ khi CHƯA có stop cho symbol này, hoặc
        `new_stop` SIẾT CHẶT hơn (cao hơn, hệ thống long-only) stop hiện
        tại. Nới rộng bị từ chối tại đây, không đẩy quyết định lên caller."""
        current = self._current_stops.get(symbol)
        if current is not None and new_stop <= current:
            logger.warning(
                "modify_stop(%s): %s không siết chặt hơn stop hiện tại %s — từ chối, không áp dụng.",
                symbol,
                new_stop,
                current,
            )
            return False
        self._current_stops[symbol] = new_stop
        return True

    def restore_known_stop(self, symbol: str, known_stop: Decimal) -> None:
        """Khôi phục stop đã biết sau khi tiến trình restart (đọc từ
        `state_snapshot.json`, xem main.py) — PHẢI gọi TRƯỚC lần
        `modify_stop()` đầu tiên sau restart.

        `_current_stops` sống trong bộ nhớ, mất khi tiến trình chết.
        Không gọi hàm này thì `modify_stop()` đầu tiên sau restart sẽ coi
        `current = None` (chưa từng có stop) và chấp nhận BẤT KỲ giá trị
        nào — kể cả rộng hơn stop thật đã đặt trước khi crash — âm thầm vi
        phạm CLAUDE.md bất biến #5 (chỉ siết, không bao giờ nới) ngay sau
        một lần khởi động lại. Không qua `modify_stop()` (không kiểm tra
        siết/nới ở đây) vì đây là NẠP LẠI trạng thái đã biết, không phải
        một quyết định sửa stop mới."""
        self._current_stops[symbol] = known_stop

    # ------------------------------------------------------------------
    # Đóng vị thế / huỷ lệnh
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        return self.exchange_client.cancel_order(order_id)

    def close_position(self, symbol: str, bar_timestamp: datetime) -> OrderResult:
        """`bar_timestamp` BẮT BUỘC — cố tình KHÔNG có mặc định `None` với
        fallback `datetime.now()`.

        `datetime.now()` làm `order_link_id` khác nhau ở mỗi lần gọi, tức
        là chính cái nó phải chống: bot crash sau khi gửi lệnh đóng nhưng
        trước khi ghi nhận, restart, đóng lại — sàn thấy hai id khác nhau
        và khớp cả hai. Một tham số mặc định "tiện" ở đây sẽ tái tạo lại
        đúng bug đang sửa, im lặng, và không caller nào lộ ra. Bắt buộc để
        type checker chỉ ra mọi chỗ gọi thiếu.

        Truyền timestamp của BAR đang xử lý (không phải giờ hiện tại): hai
        lần chạy cùng một bar phải cho cùng một id.
        """
        qty = self._current_qty(symbol)
        order_link_id = self.generate_order_link_id(symbol, bar_timestamp, Decimal("0"))
        if qty <= 0:
            return OrderResult(
                order_id="",
                order_link_id=order_link_id,
                status=OrderStatus.FILLED,
                filled_qty=Decimal("0"),
                avg_fill_price=None,
                raw_response={"note": "không có gì để đóng"},
            )
        request = OrderRequest(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            qty=qty,
            price=None,
            order_link_id=order_link_id,
        )
        return self.exchange_client.submit_order(request)

    def close_all_positions(self, bar_timestamp: datetime) -> list[OrderResult]:
        """`bar_timestamp` bắt buộc, cùng lý do `close_position` — xem đó."""
        return [self.close_position(p.symbol, bar_timestamp) for p in self.exchange_client.get_positions()]
