"""Test tái hiện cho 9 bug sửa ngày 2026-08-08.

Mỗi test ở đây tái hiện ĐÚNG một lỗi và đã được kiểm chứng bằng đột biến
(CLAUDE.md kỷ luật #16): revert phần sửa tương ứng, xác nhận test đỏ,
apply lại. Không có test tái hiện thì không tính là đã sửa.

Tên test mang số bug để lần đỏ sau còn tra ngược được về mô tả gốc.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from broker.base import Order, OrderResult, OrderSide, OrderStatus, OrderType
from broker.instrument_rules import InstrumentRules
from broker.order_executor import OrderExecutor
from core.hmm_engine import HMMRegimeEngine
from core.risk_manager import PortfolioState, RiskManager
from main import FeatureCache, LiveLoopState, process_one_bar
from tests.test_main_loop import (  # fixtures đã có, không dựng lại bản thứ hai
    _bars,
    _FakeOrderExecutor,
    _FakePosition,
    _FakePositionTracker,
    _risk_manager_config,
    _signal_generator,
    _synthetic_features,
)

_SYMBOL = "BTCUSDT"

_RULES = InstrumentRules(
    symbol=_SYMBOL,
    base_precision=Decimal("0.000001"),
    quote_precision=Decimal("0.01"),
    tick_size=Decimal("0.01"),
    min_order_qty=Decimal("0.000001"),
    min_order_amt=Decimal("5"),
    max_order_qty=Decimal("100"),
)


# ----------------------------------------------------------------------
# Fake sàn cho tầng OrderExecutor
# ----------------------------------------------------------------------


class _Balance:
    def __init__(self, total: Decimal, available: Decimal) -> None:
        self.asset = "USDT"
        self.total = total
        self.available = available


class _Signal:
    def __init__(self, target: Decimal, price: Decimal = Decimal("100")) -> None:
        self.symbol = _SYMBOL
        self.target_allocation_pct = target
        self.timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
        self.entry_price = price


class _ExchangeStub:
    def __init__(
        self,
        *,
        total: Decimal = Decimal("10000"),
        available: Decimal = Decimal("10000"),
        open_orders: list[Order] | None = None,
    ) -> None:
        self._balance = _Balance(total, available)
        self._open_orders = open_orders or []
        self.submitted: list[Any] = []

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        return _RULES

    def get_balance(self) -> _Balance:
        return self._balance

    def get_positions(self) -> list[Any]:
        return []

    def get_open_orders(self) -> list[Order]:
        return list(self._open_orders)

    def submit_order(self, request: Any) -> OrderResult:
        self.submitted.append(request)
        return OrderResult(
            order_id="1",
            order_link_id=request.order_link_id,
            status=OrderStatus.FILLED,
            filled_qty=request.qty,
            avg_fill_price=request.price,
        )

    def cancel_order(self, order_id: str) -> bool:
        return True


def _executor(exchange: _ExchangeStub) -> OrderExecutor:
    # `_ExchangeStub` khớp CẤU TRÚC `ExchangeClient` (đủ method test cần)
    # nhưng không kế thừa nó — cố ý: stub kế thừa ABC sẽ phải cài mọi
    # method, kể cả những method test này không dùng tới.
    return OrderExecutor(exchange, limit_offset_pct=Decimal("0.05"), timeout_seconds=0)  # type: ignore[arg-type]


# ======================================================================
# BUG 1 — bar bị lỡ: phải tua trạng thái, tuyệt đối không đặt lệnh
# ======================================================================


def test_bug1_bar_bi_lo_khong_dat_lenh_nhung_van_tua_trang_thai(tmp_path: Path) -> None:
    """`execute=False` — signal của bar cũ KHÔNG được thành lệnh.

    Đây là nửa an toàn của BUG 1: lặp qua bar bị lỡ mà vẫn đặt lệnh theo
    chúng sẽ khớp quyết định của ba ngày trước ở giá hôm nay — tệ hơn hẳn
    bug bỏ qua bar mà nó định sửa.
    """
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]

    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=None,
        current_allocation_pct="0.5",
        current_regime_id=None,
        current_regime_label=None,
        session_started_at_utc=datetime.now(timezone.utc).isoformat(),
        written_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    new_state = process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=state,
        dry_run=False,
        execute=False,
    )

    assert order_executor.submit_order_calls == [], "bar bị lỡ KHÔNG được đặt lệnh"
    assert order_executor.close_position_calls == []
    assert order_executor.modify_stop_calls == []

    # ...nhưng TRẠNG THÁI phải tiến — đó là toàn bộ lý do gọi hàm.
    assert new_state.last_processed_bar == bar_ts.date().isoformat()
    assert new_state.current_regime_id is not None
    assert new_state.current_trend_structure is not None

    # Vị thế thật không đổi (không lệnh nào chạy) -> allocation/stop giữ nguyên.
    assert new_state.current_allocation_pct == "0.5"
    assert new_state.current_stop_loss is None


def test_bug1_chuoi_bat_kip_chi_bar_cuoi_dat_lenh(tmp_path: Path) -> None:
    """Tua 3 bar bị lỡ + 1 bar mới nhất -> ĐÚNG MỘT lệnh, ở bar cuối."""
    generator = _signal_generator(tmp_path)
    order_executor = _FakeOrderExecutor()
    ohlcv = _bars(declining=False)
    pending = list(ohlcv.index[-4:])

    state = LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=None,
        current_allocation_pct="0",
        current_regime_id=None,
        current_regime_label=None,
        session_started_at_utc=datetime.now(timezone.utc).isoformat(),
        written_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    for i, bar in enumerate(pending):
        state = process_one_bar(
            symbol=_SYMBOL,
            signal_generator=generator,
            order_executor=order_executor,
            position_tracker=_FakePositionTracker(),
            ohlcv=ohlcv,
            features=_synthetic_features(),
            bar_ts=bar,
            state=state,
            dry_run=False,
            execute=(i == len(pending) - 1),
        )

    assert len(order_executor.submit_order_calls) == 1, "chỉ bar cuối được đặt lệnh"
    assert state.last_processed_bar == pending[-1].date().isoformat()


def test_bug1_khop_voi_ban_forward() -> None:
    """`main._pending_bar_dates` phải cho kết quả GIỐNG HỆT
    `forward.logger.pending_bar_dates` trên mọi đầu vào thử.

    Hai bản tồn tại song song CÓ CHỦ ĐÍCH: `main.py` không được import
    ngược vào `forward/` (thí nghiệm tiền đăng ký tự cô lập — xem docstring
    `_latest_closed_bar_date`), và từ 2026-08-08 `forward/logger.py` còn
    ĐÓNG BĂNG với SHA256 ghim, nên nối live loop vào nó sẽ ép mọi nhu cầu
    đổi hành vi sau này lên đúng file không được sửa.

    Test này là thứ khiến việc nhân bản chấp nhận được thay vì chỉ là sao
    chép: nó bắt được lúc hai bản bắt đầu trôi lệch.
    """
    from forward.logger import pending_bar_dates
    from main import _pending_bar_dates

    dates = list(pd.date_range("2026-08-01", periods=10, freq="D", tz="UTC"))
    cases: list[Any] = [
        (None, dates),
        (None, []),
        (dates[0], dates),
        (dates[4], dates),
        (dates[-1], dates),  # đã đồng bộ -> rỗng
        (dates[-1] + pd.Timedelta(days=5), dates),  # state đi trước dữ liệu
        (pd.Timestamp("2026-07-01", tz="UTC"), dates),  # tụt lại rất xa
    ]

    for last, available in cases:
        assert _pending_bar_dates(last, available) == pending_bar_dates(last, available), (
            f"hai bản đã trôi lệch tại last={last}"
        )


def test_bug1_process_one_bar_co_tham_so_execute() -> None:
    """Ghim sự tồn tại của tham số — nếu ai đó bỏ nó đi, mọi test trên
    đây sẽ đỏ vì TypeError chứ không phải vì hành vi sai, và thông điệp
    đó khó lần ra hơn nhiều."""
    params = inspect.signature(process_one_bar).parameters
    assert "execute" in params
    assert params["execute"].default is True  # mặc định = hành vi live cũ


# ======================================================================
# BUG 2 — close_position phải nhận bar_timestamp, không có mặc định
# ======================================================================


def test_bug2_close_position_bat_buoc_bar_timestamp() -> None:
    """Không được có mặc định `None` với fallback `datetime.now()`.

    Một mặc định "tiện" ở đây tái tạo đúng bug đang sửa và không caller
    nào lộ ra — chính vì thế test này kiểm CHỮ KÝ, không chỉ hành vi.
    """
    params = inspect.signature(OrderExecutor.close_position).parameters
    assert "bar_timestamp" in params
    assert params["bar_timestamp"].default is inspect.Parameter.empty, (
        "bar_timestamp KHÔNG được có giá trị mặc định — xem docstring close_position"
    )


def test_bug2_cung_bar_cho_cung_order_link_id() -> None:
    """Hai lần đóng cùng một bar (crash-restart) -> cùng id -> sàn từ chối
    bản sao thay vì khớp hai lần."""
    exchange = _ExchangeStub()
    executor = _executor(exchange)
    bar = datetime(2026, 8, 7, tzinfo=timezone.utc)

    id_a = executor.generate_order_link_id(_SYMBOL, bar, Decimal("0"))
    id_b = executor.generate_order_link_id(_SYMBOL, bar, Decimal("0"))
    assert id_a == id_b

    # ...và khác bar thì phải khác id (nếu không, mọi lệnh đóng trùng nhau).
    assert executor.generate_order_link_id(_SYMBOL, bar + timedelta(days=1), Decimal("0")) != id_a


# ======================================================================
# BUG 3 — normalize Decimal
# ======================================================================


@pytest.mark.parametrize(
    "a,b",
    [
        (Decimal("0.30"), Decimal("0.3")),
        (Decimal("0.300"), Decimal("0.3")),
        (Decimal("0.30"), Decimal("0.300")),
        (Decimal("1"), Decimal("1.00")),
    ],
)
def test_bug3_decimal_bang_nhau_cho_cung_order_link_id(a: Decimal, b: Decimal) -> None:
    """`Decimal("0.30") == Decimal("0.3")` là TRUE, nên chúng PHẢI cho
    cùng orderLinkId.

    Không normalize thì `str()` ra hai chuỗi khác nhau -> hai hash khác
    nhau -> lớp chống trùng im lặng mất tác dụng đúng lúc cần nhất: bot
    restart, tính lại target bằng một đường số học khác, sinh id mới, sàn
    không nhận ra đây là lệnh đã đặt.
    """
    assert a == b  # tiền đề: hai giá trị này BẰNG NHAU
    executor = _executor(_ExchangeStub())
    bar = datetime(2026, 8, 7, tzinfo=timezone.utc)

    assert executor.generate_order_link_id(_SYMBOL, bar, a) == executor.generate_order_link_id(
        _SYMBOL, bar, b
    )


def test_bug3_khong_sinh_ky_hieu_mu_cho_so_khong() -> None:
    """`Decimal("0.00000").normalize()` ra `Decimal("0E-5")` — `str()`
    giữ nguyên dạng mũ và lại sinh chuỗi khác cho cùng giá trị 0. `:f`
    trong bản sửa chặn đúng ca này."""
    executor = _executor(_ExchangeStub())
    bar = datetime(2026, 8, 7, tzinfo=timezone.utc)

    assert executor.generate_order_link_id(
        _SYMBOL, bar, Decimal("0.00000")
    ) == executor.generate_order_link_id(_SYMBOL, bar, Decimal("0"))


def test_bug3_gia_tri_khac_nhau_van_cho_id_khac_nhau() -> None:
    """Đột biến ngược: normalize KHÔNG được gộp hai allocation THẬT SỰ
    khác nhau về cùng một id."""
    executor = _executor(_ExchangeStub())
    bar = datetime(2026, 8, 7, tzinfo=timezone.utc)

    assert executor.generate_order_link_id(_SYMBOL, bar, Decimal("0.3")) != executor.generate_order_link_id(
        _SYMBOL, bar, Decimal("0.31")
    )


# ======================================================================
# BUG 4 — stop-loss breach phải đi qua risk manager
# ======================================================================


class _SpyRiskManager(RiskManager):
    """Ghi lại mọi lần `validate_signal` được gọi, vẫn chạy logic thật."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.validated: list[Any] = []

    def validate_signal(self, signal: Any, portfolio_state: Any) -> Any:
        self.validated.append(signal)
        return super().validate_signal(signal, portfolio_state)


def _breach_state(close_price: Decimal) -> LiveLoopState:
    return LiveLoopState(
        last_processed_bar=None,
        current_stop_loss=str(close_price + Decimal("1")),  # stop CAO hơn giá -> breach
        current_allocation_pct="0.5",
        current_regime_id=1,
        current_regime_label="BULL",
        session_started_at_utc=datetime.now(timezone.utc).isoformat(),
        written_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def test_bug4_lenh_thoat_di_qua_validate_signal(tmp_path: Path) -> None:
    """Bản cũ gọi thẳng `close_position()`, KHÔNG qua điểm phủ quyết —
    vi phạm CLAUDE.md bất biến #4 (mọi lệnh qua risk_manager, không
    đường vòng, không cờ bypass)."""
    generator = _signal_generator(tmp_path)
    spy = _SpyRiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "trading_halted.lock")
    generator.risk_manager = spy

    order_executor = _FakeOrderExecutor()
    ohlcv = _bars(declining=True)
    bar_ts = ohlcv.index[-1]
    close_price = Decimal(str(ohlcv.loc[bar_ts, "close"]))

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=bar_ts,
        state=_breach_state(close_price),
        dry_run=False,
    )

    assert len(order_executor.close_position_calls) == 1, "vị thế phải được đóng"
    assert spy.validated, "lệnh THOÁT phải đi qua validate_signal (bất biến #4)"
    assert spy.validated[-1].target_allocation_pct == Decimal("0")


def test_bug4_validate_signal_luon_duyet_lenh_ve_khong_du_het_han_muc(tmp_path: Path) -> None:
    """Đã dùng hết `max_trades_per_day` -> lệnh thoát VẪN được duyệt.

    Chặn lệnh thoát vì hạn mức nghĩa là GIỮ NGUYÊN vị thế đang lỗ — hậu
    quả tệ hơn hẳn thứ hạn mức đó đang bảo vệ.
    """
    cfg = dict(_risk_manager_config())
    cfg["max_trades_per_day"] = 0  # không còn suất nào
    rm = RiskManager(cfg, halt_lock_path=tmp_path / "trading_halted.lock")

    exit_signal = _exit_signal_for(rm)
    decision = rm.validate_signal(exit_signal, _portfolio_state())

    assert decision.approved, "lệnh giảm về 0 phải luôn được duyệt"


def test_bug4_validate_signal_luon_duyet_lenh_ve_khong_khi_da_halt(tmp_path: Path) -> None:
    """`trading_halted.lock` tồn tại -> lệnh VÀO bị chặn, lệnh THOÁT thì không."""
    lock = tmp_path / "trading_halted.lock"
    lock.write_text("halted", encoding="utf-8")
    rm = RiskManager(_risk_manager_config(), halt_lock_path=lock)

    assert rm.validate_signal(_exit_signal_for(rm), _portfolio_state()).approved

    entry = _exit_signal_for(rm, target=Decimal("0.5"))
    assert not rm.validate_signal(entry, _portfolio_state()).approved, "lệnh VÀO vẫn phải bị chặn khi đã halt"


def test_bug4_lenh_thoat_van_duoc_dem_vao_so_lenh_trong_ngay(tmp_path: Path) -> None:
    """ "Không chặn" KHÔNG có nghĩa "không ghi nhận" — hạn mức ngày hôm
    sau tính trên số liệu này."""
    rm = RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "trading_halted.lock")
    before = rm._daily_trade_count

    rm.validate_signal(_exit_signal_for(rm), _portfolio_state())

    assert rm._daily_trade_count == before + 1


def test_bug4_halt_lock_duoc_kiem_moi_bar(tmp_path: Path) -> None:
    """Lock tạo GIỮA phiên (không phải lúc khởi động) phải chặn được lệnh
    vào ngay bar kế tiếp."""
    generator = _signal_generator(tmp_path)
    lock = tmp_path / "trading_halted.lock"
    generator.risk_manager = RiskManager(_risk_manager_config(), halt_lock_path=lock)
    order_executor = _FakeOrderExecutor()
    ohlcv = _bars(declining=False)

    lock.write_text("halted giữa phiên", encoding="utf-8")

    process_one_bar(
        symbol=_SYMBOL,
        signal_generator=generator,
        order_executor=order_executor,
        position_tracker=_FakePositionTracker(),
        ohlcv=ohlcv,
        features=_synthetic_features(),
        bar_ts=ohlcv.index[-1],
        state=LiveLoopState(
            last_processed_bar=None,
            current_stop_loss=None,
            current_allocation_pct="0",
            current_regime_id=None,
            current_regime_label=None,
            session_started_at_utc=datetime.now(timezone.utc).isoformat(),
            written_at_utc=datetime.now(timezone.utc).isoformat(),
        ),
        dry_run=False,
    )

    assert order_executor.submit_order_calls == []


def _exit_signal_for(rm: RiskManager, target: Decimal = Decimal("0")) -> Any:
    from core.regime_strategies import Direction, Signal

    return Signal(
        symbol=_SYMBOL,
        direction=Direction.FLAT if target == 0 else Direction.LONG,
        confidence=1.0,
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=None,
        target_allocation_pct=target,
        leverage=Decimal("1"),
        regime_id=1,
        regime_name="BULL",
        regime_probability=0.9,
        timestamp=datetime(2026, 8, 7, tzinfo=timezone.utc),
        reasoning="test",
        strategy_name="test",
    )


def _portfolio_state() -> PortfolioState:
    return PortfolioState(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        available_balance=Decimal("10000"),
        positions={},
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        peak_equity=Decimal("10000"),
        drawdown=Decimal("0"),
        circuit_breaker_status={},
        flicker_rate=0.0,
    )


# ======================================================================
# BUG 5 — _requested_qty mất khi restart: đọc từ SÀN
# ======================================================================


def _open_order(order_link_id: str, qty: str, filled: str) -> Order:
    return Order(
        order_id="1",
        order_link_id=order_link_id,
        symbol=_SYMBOL,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal(qty),
        price=Decimal("100"),
        status=OrderStatus.PARTIALLY_FILLED,
        created_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        filled_qty=Decimal(filled),
    )


def test_bug5_doc_phan_con_lai_tu_san_khi_mat_state() -> None:
    """Sau restart `_requested_qty` rỗng — phải hỏi SÀN, không trả 0.

    Trả 0 nghĩa là phần chưa khớp bị bỏ quên: bot tưởng đã rebalance xong
    trong khi vị thế thật còn thiếu.
    """
    exchange = _ExchangeStub(open_orders=[_open_order("abc", "1.0", "0.4")])
    executor = _executor(exchange)
    assert executor._requested_qty == {}  # mô phỏng tiến trình vừa restart

    result = OrderResult(
        order_id="1",
        order_link_id="abc",
        status=OrderStatus.PARTIALLY_FILLED,
        filled_qty=Decimal("0.4"),
        avg_fill_price=Decimal("100"),
    )

    assert executor.handle_partial_fill(result) == Decimal("0.6")


def test_bug5_khong_tim_thay_tren_san_thi_tra_khong() -> None:
    """Không còn lệnh mở mang id đó (đã khớp hết/đã huỷ) -> 0, không nổ."""
    executor = _executor(_ExchangeStub(open_orders=[]))
    result = OrderResult(
        order_id="1",
        order_link_id="khong-ton-tai",
        status=OrderStatus.FILLED,
        filled_qty=Decimal("1.0"),
        avg_fill_price=Decimal("100"),
    )

    assert executor.handle_partial_fill(result) == Decimal("0")


def test_bug5_bo_nho_van_duoc_uu_tien_hon_san() -> None:
    """Còn state trong bộ nhớ thì dùng nó — không thêm một round-trip
    mạng cho trường hợp thường gặp nhất."""
    exchange = _ExchangeStub(open_orders=[_open_order("abc", "9.0", "0.0")])
    executor = _executor(exchange)
    executor._requested_qty["abc"] = Decimal("1.0")

    result = OrderResult(
        order_id="1",
        order_link_id="abc",
        status=OrderStatus.PARTIALLY_FILLED,
        filled_qty=Decimal("0.4"),
        avg_fill_price=Decimal("100"),
    )

    assert executor.handle_partial_fill(result) == Decimal("0.6")  # 1.0-0.4, KHÔNG phải 9.0


def test_bug5_order_mang_truong_filled_qty() -> None:
    """`Order.filled_qty` phải tồn tại — không có nó thì `qty - filled`
    không tính được và cả cơ chế trên vô nghĩa."""
    assert "filled_qty" in Order.__dataclass_fields__


# ======================================================================
# BUG 6 — pre-flight số dư khả dụng
# ======================================================================


def test_bug6_tu_choi_khi_vuot_so_du_kha_dung() -> None:
    """`equity` tính từ `balance.total` (gồm phần đang bị khoá), nên một
    lệnh mua hợp lệ theo `total` vẫn có thể vượt `available`."""
    exchange = _ExchangeStub(total=Decimal("10000"), available=Decimal("100"))
    executor = _executor(exchange)

    result = executor.submit_order(_Signal(Decimal("0.5")))  # ~5000 USDT

    assert result.status is OrderStatus.REJECTED
    assert "available" in result.raw_response["rejection_reason"]
    assert exchange.submitted == [], "KHÔNG được gửi lệnh ra sàn"


def test_bug6_van_gui_khi_du_so_du() -> None:
    """Đột biến ngược: đủ số dư thì lệnh phải đi bình thường."""
    exchange = _ExchangeStub(total=Decimal("10000"), available=Decimal("10000"))
    executor = _executor(exchange)

    result = executor.submit_order(_Signal(Decimal("0.5")))

    assert result.status is not OrderStatus.REJECTED
    assert len(exchange.submitted) == 1


def test_bug6_khong_ap_cho_chieu_ban() -> None:
    """Bán làm GIẢM exposure và không tiêu số dư — pre-flight không được
    chặn nó, kể cả khi `available` bằng 0."""
    exchange = _ExchangeStub(total=Decimal("10000"), available=Decimal("0"))
    exchange.get_positions = lambda: [_FakePosition(_SYMBOL, Decimal("1.0"))]  # type: ignore[method-assign]
    executor = _executor(exchange)

    result = executor.submit_order(_Signal(Decimal("0")))

    assert result.status is not OrderStatus.REJECTED


# ======================================================================
# BUG 7 — round_price có hướng
# ======================================================================


def test_bug7_mua_lam_tron_xuong_ban_lam_tron_len() -> None:
    price = Decimal("100000.017")

    assert _RULES.round_price(price, ROUND_DOWN) == Decimal("100000.01")
    assert _RULES.round_price(price, ROUND_UP) == Decimal("100000.02")


def test_bug7_mac_dinh_van_la_round_down() -> None:
    """Giữ nguyên hành vi cũ cho caller chưa chỉ định — hướng an toàn khi
    không biết chiều lệnh. `tests/test_precision.py` dựa vào điều này."""
    assert _RULES.round_price(Decimal("100000.017")) == Decimal("100000.01")


def test_bug7_lenh_ban_dat_gia_lam_tron_len() -> None:
    """Kiểm ở tầng `submit_order`, không chỉ ở `InstrumentRules`: sửa
    `round_price` mà quên truyền hướng ở chỗ gọi thì bug vẫn còn nguyên."""
    exchange = _ExchangeStub()
    exchange.get_positions = lambda: [_FakePosition(_SYMBOL, Decimal("1.0"))]  # type: ignore[method-assign]
    executor = _executor(exchange)

    # entry_price chọn sao cho giá LIMIT bán rơi vào giữa hai tick.
    executor.submit_order(_Signal(Decimal("0"), price=Decimal("100000.007")))

    assert exchange.submitted, "phải có lệnh bán"
    sent = exchange.submitted[0]
    assert sent.side is OrderSide.SELL
    # bán -> +0.05% -> 100000.007 * 1.0005 = 100050.0070035
    # ROUND_UP theo tick 0.01 -> 100050.01 (bản cũ ROUND_DOWN -> 100050.00,
    # tức tự nguyện nhận ít hơn ở mỗi lệnh bán).
    assert sent.price == Decimal("100050.01")
    assert sent.price != Decimal("100050.00"), "đây là giá trị của bản ROUND_DOWN cũ"


# ======================================================================
# BUG 8 — thiếu log_return_1 phải nổ sớm, thông điệp nêu rõ
# ======================================================================


def _engine() -> HMMRegimeEngine:
    return HMMRegimeEngine(
        n_candidates=[3],
        n_init=1,
        covariance_type="diag",
        min_train_bars=200,
        stability_bars=1,
        flicker_window=20,
        flicker_threshold=4,
    )


def test_bug8_thieu_log_return_1_bao_loi_ro_rang() -> None:
    """Bản cũ nổ bằng `ValueError: 'log_return_1' is not in list` từ
    `.index()` — SAU toàn bộ chi phí train, và không nói được feature nào
    thiếu hay có những feature nào."""
    index = pd.date_range("2024-01-01", periods=250, freq="D", tz="UTC")
    rng = np.random.default_rng(3)
    features = pd.DataFrame(
        {"volatility_20": rng.normal(0, 1, 250), "atr_14": rng.normal(0, 1, 250)}, index=index
    )

    with pytest.raises(ValueError) as exc:
        _engine().select_and_train(features)

    message = str(exc.value)
    assert "log_return_1" in message
    assert "volatility_20" in message, "phải liệt kê feature ĐANG có để lần ra cấu hình sai"
    assert "settings.yaml" in message


def test_bug8_no_truoc_khi_train(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm phải chạy TRƯỚC `scan_bic()` — nếu nó nổ sau, người dùng chờ
    hết chi phí train rồi mới biết cấu hình sai."""
    engine = _engine()
    called = {"scan": False}

    def _spy_scan(features: Any) -> Any:
        called["scan"] = True
        raise AssertionError("scan_bic KHÔNG được chạy khi thiếu feature bắt buộc")

    monkeypatch.setattr(engine, "scan_bic", _spy_scan)

    index = pd.date_range("2024-01-01", periods=250, freq="D", tz="UTC")
    features = pd.DataFrame({"volatility_20": np.zeros(250)}, index=index)

    with pytest.raises(ValueError):
        engine.select_and_train(features)

    assert not called["scan"]


def test_bug8_du_feature_thi_train_binh_thuong() -> None:
    """Đột biến ngược: phép kiểm không được chặn bộ feature hợp lệ."""
    _engine().select_and_train(_synthetic_features())


# ======================================================================
# BUG 9 — cache compute_all_features (KHÔNG tính tăng dần)
# ======================================================================


def _ohlcv(n: int = 300) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(np.linspace(100.0, 200.0, n), index=index)
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1.0,
            # `FeatureConfig.use_trade_count_not_volume=True` mặc định —
            # thiếu cột này thì compute_tier1_features ném KeyError.
            "trade_count": rng.integers(1000, 2000, size=n).astype(float),
        },
        index=index,
    )


def _feature_config() -> Any:
    from main import build_feature_config, load_settings

    return build_feature_config(load_settings())


def test_bug9_cung_ohlcv_chi_tinh_mot_lan() -> None:
    """Vòng poll 60s tính lại toàn bộ feature matrix mỗi vòng ở nhánh
    chưa đủ warmup và mỗi lần `process_one_bar` ném exception."""
    cache = FeatureCache(_feature_config())
    ohlcv = _ohlcv()

    cache.get(ohlcv)
    cache.get(ohlcv)
    cache.get(ohlcv)

    assert cache.misses == 1, "chỉ được tính một lần cho cùng dữ liệu"
    assert cache.hits == 2


def test_bug9_bar_moi_thi_tinh_lai_toan_bo() -> None:
    cache = FeatureCache(_feature_config())
    ohlcv = _ohlcv()

    cache.get(ohlcv)
    cache.get(
        pd.concat(
            [ohlcv, ohlcv.iloc[[-1]].rename(index={ohlcv.index[-1]: ohlcv.index[-1] + pd.Timedelta(days=1)})]
        )
    )

    assert cache.misses == 2


def test_bug9_gia_lich_su_doi_thi_tinh_lai() -> None:
    """Sàn có thể sửa lại nến lịch sử. Cache chỉ nhìn ĐỘ DÀI sẽ trả
    feature cũ cho dữ liệu đã đổi — hash giá trị chặn ca này."""
    cache = FeatureCache(_feature_config())
    ohlcv = _ohlcv()
    cache.get(ohlcv)

    revised = ohlcv.copy()
    # stub pandas không biết `get_loc` trả `int` cho Index thường.
    revised.iloc[10, revised.columns.get_loc("close")] = 999.0  # type: ignore[index]
    cache.get(revised)

    assert cache.misses == 2


def test_bug9_ket_qua_giong_het_ban_khong_cache() -> None:
    """Bất biến quan trọng nhất của BUG 9: cache KHÔNG được đổi một chữ
    số nào so với `compute_all_features()` gọi thẳng.

    Nếu ai đó thay cache bằng bản tính TĂNG DẦN, test này phải đỏ —
    z-score 365 bar/SMA200/ATR đều phụ thuộc cửa sổ nên bản tăng dần gần
    như chắc chắn lệch nhẹ, và lệch nhẹ là thứ làm
    `test_wiring_equivalence`/`test_forward_golden` đỏ, hoặc tệ hơn là
    lệch âm thầm giữa đường live và đường golden.
    """
    from data.feature_engineering import compute_all_features

    config = _feature_config()
    ohlcv = _ohlcv()

    direct = compute_all_features(ohlcv, config)
    cached = FeatureCache(config).get(ohlcv)

    pd.testing.assert_frame_equal(direct, cached)
