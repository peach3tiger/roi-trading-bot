"""core.risk_manager — RiskManager với quyền phủ quyết tuyệt đối.

Hoạt động HOÀN TOÀN ĐỘC LẬP với HMM: module này không được import
core.hmm_engine hay bất kỳ thứ gì từ core.regime_strategies (xem CLAUDE.md
bất biến #4). Nó ra quyết định dựa trên P&L thực tế và trạng thái danh
mục — sự độc lập này là lý do nó vẫn bảo vệ được khi HMM sai hoàn toàn.

Vì lý do đó, `Signal` được mô tả ở đây bằng một structural type
(`SignalLike`) thay vì import trực tiếp `core.regime_strategies.Signal`.
Việc "capping" allocation dùng `dataclasses.replace()` — hoạt động trên
BẤT KỲ dataclass instance nào lúc runtime (kiểm tra qua `__dataclass_fields__`,
không cần biết trước lớp cụ thể), nên vẫn tạo được bản sao đã sửa mà không
phải import `Signal` để gọi constructor của nó.

Mọi lệnh đi qua validate_signal(). Không có đường vòng, không có cờ bypass,
không có "chế độ khẩn cấp" bỏ qua nó.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

# File lock dừng toàn bộ giao dịch khi peak drawdown > ngưỡng — PHẢI xoá thủ
# công mới chạy lại (Brain-Crypto-Bybit.md §5.2). Là FILE (không phải cờ
# trong bộ nhớ) có chủ đích: sống sót qua restart tiến trình — một crash-
# restart không được phép âm thầm xoá bỏ một lần dừng khẩn cấp.
_DEFAULT_HALT_LOCK_PATH = Path("trading_halted.lock")

# 100 → phần trăm sang phân số, cùng quy ước _PCT_TO_RATE của
# backtest/cost_model.py — mọi tham số *_pct trong settings.yaml nhận theo
# ĐƠN VỊ PHẦN TRĂM.
_PCT_TO_RATE = Decimal("100")


class SignalLike(Protocol):
    """Hình dạng tối thiểu mà risk_manager cần từ một signal — tránh phụ
    thuộc trực tiếp vào core.regime_strategies.Signal (bất biến #4).

    Khai báo bằng `@property` (chỉ đọc) thay vì thuộc tính thường: Signal
    thật là frozen dataclass (thuộc tính chỉ đọc), còn Protocol với thuộc
    tính thường ngầm định có thể ghi — khai báo sai khiến mypy coi
    Signal KHÔNG khớp cấu trúc dù dữ liệu hoàn toàn tương thích.
    """

    @property
    def symbol(self) -> str: ...
    @property
    def direction(self) -> str: ...
    @property
    def target_allocation_pct(self) -> Decimal: ...
    @property
    def stop_loss(self) -> Optional[Decimal]: ...
    @property
    def timestamp(self) -> datetime: ...


class BreakerLevel(Enum):
    NONE = "NONE"
    DAILY_REDUCE = "DAILY_REDUCE"
    DAILY_HALT = "DAILY_HALT"
    WEEKLY_REDUCE = "WEEKLY_REDUCE"
    WEEKLY_HALT = "WEEKLY_HALT"
    PEAK_HALT = "PEAK_HALT"


# size_multiplier áp dụng cho target_allocation_pct khi breaker đang active —
# HALT luôn 0 (đóng hết), REDUCE luôn một nửa (§5.2), NONE giữ nguyên (1).
_SIZE_MULTIPLIER: dict[BreakerLevel, Decimal] = {
    BreakerLevel.NONE: Decimal("1"),
    BreakerLevel.DAILY_REDUCE: Decimal("0.5"),
    BreakerLevel.DAILY_HALT: Decimal("0"),
    BreakerLevel.WEEKLY_REDUCE: Decimal("0.5"),
    BreakerLevel.WEEKLY_HALT: Decimal("0"),
    BreakerLevel.PEAK_HALT: Decimal("0"),
}

# Thứ tự ưu tiên khi NHIỀU điều kiện cùng đúng một lúc — số nhỏ hơn thắng.
# PEAK_HALT luôn thắng tuyệt đối (dừng vô thời hạn, cần can thiệp thủ công).
# Trong hai loại HALT còn lại, WEEKLY nghiêm trọng hơn DAILY (hiệu lực dài
# hơn); tương tự giữa hai REDUCE. HALT luôn thắng REDUCE bất kể ngày/tuần.
_LEVEL_PRIORITY: dict[BreakerLevel, int] = {
    BreakerLevel.PEAK_HALT: 0,
    BreakerLevel.WEEKLY_HALT: 1,
    BreakerLevel.DAILY_HALT: 2,
    BreakerLevel.WEEKLY_REDUCE: 3,
    BreakerLevel.DAILY_REDUCE: 4,
    BreakerLevel.NONE: 5,
}


@dataclass(frozen=True)
class BreakerStatus:
    level: BreakerLevel
    triggered_at: Optional[datetime]
    daily_dd: Decimal
    weekly_dd: Decimal
    peak_dd: Decimal
    size_multiplier: Decimal


@dataclass(frozen=True)
class PortfolioState:
    equity: Decimal
    cash: Decimal
    available_balance: Decimal
    positions: dict
    daily_pnl: Decimal
    weekly_pnl: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    circuit_breaker_status: dict
    flicker_rate: float


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    modified_signal: Optional[SignalLike]
    rejection_reason: Optional[str]
    modifications: list[str] = field(default_factory=list)


class CircuitBreaker:
    """Kích hoạt theo P&L thực tế, độc lập với regime. Ranh giới ngày 00:00 UTC.

    THUẦN có chủ đích — không tự đọc đồng hồ hệ thống để phát hiện qua
    ngày/qua tuần. `reset_daily()`/`reset_weekly()` phải được BÊN NGOÀI
    (main loop, đúng CLAUDE.md bất biến #10: ranh giới ngày 00:00 UTC,
    ranh giới tuần Thứ Hai 00:00 UTC) gọi đúng lúc — thiết kế này giữ
    CircuitBreaker dễ test (feed một chuỗi equity + mốc reset giả lập được,
    không phải chờ đồng hồ thật hay mock `datetime.now`).

    `update(pnl)` — tham số tên `pnl` khớp đúng chữ ký spec §5.7, nhưng giá
    trị truyền vào là EQUITY TOÀN DANH MỤC hiện tại (không phải một khoản
    lãi/lỗ riêng lẻ) — đây là cách duy nhất để tính daily/weekly/peak
    drawdown độc lập mà không cần biết gì về position/trade riêng lẻ.
    """

    def __init__(
        self,
        daily_dd_reduce_pct: Decimal,
        daily_dd_halt_pct: Decimal,
        weekly_dd_reduce_pct: Decimal,
        weekly_dd_halt_pct: Decimal,
        peak_dd_halt_pct: Decimal,
    ) -> None:
        self.daily_dd_reduce_pct = daily_dd_reduce_pct
        self.daily_dd_halt_pct = daily_dd_halt_pct
        self.weekly_dd_reduce_pct = weekly_dd_reduce_pct
        self.weekly_dd_halt_pct = weekly_dd_halt_pct
        self.peak_dd_halt_pct = peak_dd_halt_pct

        self._current_equity: Optional[Decimal] = None
        self._day_start_equity: Optional[Decimal] = None
        self._week_start_equity: Optional[Decimal] = None
        self._peak_equity: Optional[Decimal] = None
        self._last_update_at: Optional[datetime] = None
        self._history: list[BreakerStatus] = []

    def update(self, pnl: Decimal, *, timestamp: Optional[datetime] = None) -> None:
        """Ghi nhận equity hiện tại. `timestamp` chỉ dùng để gắn nhãn audit
        cho `BreakerStatus.triggered_at` ở lần `check()` kế tiếp — không
        điều khiển logic reset (xem docstring lớp). Mặc định giờ thật, có
        thể truyền tường minh để test tái lập được."""
        self._current_equity = pnl
        self._last_update_at = timestamp or datetime.now(timezone.utc)

        if self._day_start_equity is None:
            self._day_start_equity = pnl
        if self._week_start_equity is None:
            self._week_start_equity = pnl
        if self._peak_equity is None or pnl > self._peak_equity:
            self._peak_equity = pnl

    def reset_daily(self) -> None:
        """00:00 UTC — caller (main loop) gọi đúng ranh giới này."""
        if self._current_equity is not None:
            self._day_start_equity = self._current_equity

    def reset_weekly(self) -> None:
        """Thứ Hai 00:00 UTC — caller (main loop) gọi đúng ranh giới này."""
        if self._current_equity is not None:
            self._week_start_equity = self._current_equity

    def check(self) -> BreakerStatus:
        """Đánh giá trạng thái hiện tại theo ngưỡng đã cấu hình. Ghi mỗi
        lần gọi vào `_history` (kể cả level NONE) — dấu vết đầy đủ, caller
        muốn chỉ xem lần kích hoạt thật thì tự lọc `level != NONE`."""
        daily_dd = self._drawdown_pct(self._day_start_equity)
        weekly_dd = self._drawdown_pct(self._week_start_equity)
        peak_dd = self._drawdown_pct(self._peak_equity)

        level = self._evaluate(daily_dd, weekly_dd, peak_dd)
        status = BreakerStatus(
            level=level,
            triggered_at=self._last_update_at,
            daily_dd=daily_dd,
            weekly_dd=weekly_dd,
            peak_dd=peak_dd,
            size_multiplier=_SIZE_MULTIPLIER[level],
        )
        self._history.append(status)
        if level is not BreakerLevel.NONE:
            logger.warning(
                "CircuitBreaker %s: daily_dd=%.2f%% weekly_dd=%.2f%% peak_dd=%.2f%% equity=%s",
                level.value,
                daily_dd,
                weekly_dd,
                peak_dd,
                self._current_equity,
            )
        return status

    def get_history(self) -> list[BreakerStatus]:
        return list(self._history)

    def _drawdown_pct(self, reference: Optional[Decimal]) -> Decimal:
        """% sụt so với `reference` (dương = đang lỗ). `reference` None
        (chưa update() lần nào) hoặc <= 0 → 0, không chia cho 0/số âm."""
        if reference is None or reference <= 0 or self._current_equity is None:
            return Decimal("0")
        drop = reference - self._current_equity
        if drop <= 0:
            return Decimal("0")
        return drop / reference * _PCT_TO_RATE

    def _evaluate(self, daily_dd: Decimal, weekly_dd: Decimal, peak_dd: Decimal) -> BreakerLevel:
        candidates: list[BreakerLevel] = [BreakerLevel.NONE]
        if peak_dd > self.peak_dd_halt_pct:
            candidates.append(BreakerLevel.PEAK_HALT)
        if weekly_dd > self.weekly_dd_halt_pct:
            candidates.append(BreakerLevel.WEEKLY_HALT)
        if daily_dd > self.daily_dd_halt_pct:
            candidates.append(BreakerLevel.DAILY_HALT)
        if weekly_dd > self.weekly_dd_reduce_pct:
            candidates.append(BreakerLevel.WEEKLY_REDUCE)
        if daily_dd > self.daily_dd_reduce_pct:
            candidates.append(BreakerLevel.DAILY_REDUCE)
        return min(candidates, key=lambda lvl: _LEVEL_PRIORITY[lvl])


class RiskManager:
    """Quyền phủ quyết tuyệt đối với mọi signal — kiểm tra size, stop loss
    bắt buộc, circuit breaker, spread, peg stablecoin, lệnh trùng.

    `config` là chính `settings["risk"]` (dict thô từ settings.yaml) —
    khác quy ước "named Decimal params" của các builder khác trong dự án
    (CostModel, TrendGateConfig, ...) vì đây là chữ ký spec §5.7
    (`RiskManager(config: dict)`). Giữ nguyên để khớp interface đã định
    nghĩa, không tự ý đổi.
    """

    def __init__(self, config: dict, halt_lock_path: Optional[Path] = None) -> None:
        self.max_position_pct = self._pct(config["max_position_pct"])
        self.max_risk_per_trade_pct = self._pct(config["max_risk_per_trade_pct"])
        self.min_order_value_usdt = Decimal(str(config["min_order_value_usdt"]))
        self.min_cash_buffer_pct = self._pct(config["min_cash_buffer_pct"])
        self.max_trades_per_day = int(config["max_trades_per_day"])
        self.max_leverage = Decimal(str(config["max_leverage"]))
        # KHÔNG qua self._pct() — hai ngưỡng này so trực tiếp với spread_pct/
        # deviation_pct tính trong check_spread/check_stablecoin_peg, vốn
        # cũng ở ĐƠN VỊ PHẦN TRĂM (vd. 0.10 nghĩa là 0.10%), không phải phân
        # số. Trộn hai quy ước (giống bẫy đã ghi trong backtest/cost_model.py)
        # sẽ làm spread 0.017% bị so với 0.001 (đã chia 100 nhầm) thay vì 0.10.
        self.spread_max_pct = Decimal(str(config["spread_max_pct"]))
        self.usdt_depeg_threshold_pct = Decimal(str(config["usdt_depeg_threshold_pct"]))
        self.duplicate_order_window_seconds = int(config["duplicate_order_window_seconds"])

        # Trần allocation THẬT SỰ áp dụng = min(max_position_pct, 1 - cash
        # buffer tối thiểu) — cash buffer có thể khắt khe hơn max_position_pct
        # (100%), nên phải lấy min của cả hai, không chỉ dùng max_position_pct.
        self._effective_max_allocation = min(
            self.max_position_pct, Decimal("1") - self.min_cash_buffer_pct
        )

        cb = config["circuit_breaker"]
        self.circuit_breaker = CircuitBreaker(
            daily_dd_reduce_pct=Decimal(str(cb["daily_dd_reduce_pct"])),
            daily_dd_halt_pct=Decimal(str(cb["daily_dd_halt_pct"])),
            weekly_dd_reduce_pct=Decimal(str(cb["weekly_dd_reduce_pct"])),
            weekly_dd_halt_pct=Decimal(str(cb["weekly_dd_halt_pct"])),
            peak_dd_halt_pct=Decimal(str(cb["peak_dd_halt_pct"])),
        )

        self._halt_lock_path = halt_lock_path if halt_lock_path is not None else _DEFAULT_HALT_LOCK_PATH
        self._recent_orders: dict[tuple[str, str], datetime] = {}
        self._daily_trade_count = 0

    @staticmethod
    def _pct(raw: object) -> Decimal:
        return Decimal(str(raw)) / _PCT_TO_RATE

    # ------------------------------------------------------------------
    # Điểm phủ quyết chính
    # ------------------------------------------------------------------

    def validate_signal(self, signal: SignalLike, portfolio_state: PortfolioState) -> RiskDecision:
        """Điểm phủ quyết duy nhất — mọi lệnh phải đi qua đây, không ngoại lệ.

        Thứ tự kiểm tra (dừng ở REJECT đầu tiên gặp phải):
        1. `trading_halted.lock` đã tồn tại → từ chối tuyệt đối.
        2. Cập nhật circuit breaker bằng equity hiện tại, đọc trạng thái —
           HALT (daily/weekly/peak) → từ chối; peak → ghi lock file.
        3. Thiếu stop loss → từ chối, KHÔNG NGOẠI LỆ (CLAUDE.md bất biến #5).
        4. Cap allocation theo `_effective_max_allocation`.
        5. Áp `size_multiplier` của circuit breaker (REDUCE, nếu có).
        6. Vượt `max_trades_per_day` → từ chối.
        7. Lệnh trùng (symbol+hướng trong `duplicate_order_window_seconds`)
           → từ chối; nếu qua được, ghi nhận đã gửi.

        **NGOẠI LỆ DUY NHẤT — `target_allocation_pct <= 0` LUÔN được duyệt**
        (kiểm tra TRƯỚC mọi bước trên). Xem `_approve_exit()` để biết lý do.
        """
        # Lệnh THOÁT đi trước mọi cổng từ chối. Một lệnh giảm về 0 bị chặn
        # vì max_daily_trades/circuit breaker/halt lock nghĩa là GIỮ NGUYÊN
        # một vị thế đang lỗ — hậu quả tệ hơn hẳn thứ mà các cổng đó đang
        # bảo vệ. Không phá bất biến "mỗi tầng chỉ được GIẢM" (#2): lệnh này
        # chỉ giảm exposure, không tầng nào nâng tỷ trọng ở đây.
        if signal.target_allocation_pct <= 0:
            return self._approve_exit(signal, portfolio_state)

        if self._is_halted():
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=(
                    f"{self._halt_lock_path} tồn tại — peak drawdown đã kích hoạt dừng toàn bộ, "
                    "cần xoá thủ công mới chạy lại (Brain-Crypto-Bybit.md §5.2)"
                ),
                modifications=[],
            )

        self.circuit_breaker.update(portfolio_state.equity, timestamp=signal.timestamp)
        breaker = self.circuit_breaker.check()

        if breaker.level is BreakerLevel.PEAK_HALT:
            self._write_halt_lock(breaker)
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=(
                    f"PEAK_HALT — peak drawdown {breaker.peak_dd:.2f}% > "
                    f"{self.circuit_breaker.peak_dd_halt_pct}%, đã ghi {self._halt_lock_path}"
                ),
                modifications=[],
            )
        if breaker.level in (BreakerLevel.DAILY_HALT, BreakerLevel.WEEKLY_HALT):
            dd = breaker.daily_dd if breaker.level is BreakerLevel.DAILY_HALT else breaker.weekly_dd
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=f"{breaker.level.value} — drawdown {dd:.2f}%, dừng tới lần reset kế tiếp",
                modifications=[],
            )

        if signal.stop_loss is None:
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=(
                    "Thiếu stop loss — mọi vị thế bắt buộc có stop, không ngoại lệ (bất biến #5)"
                ),
                modifications=[],
            )

        modifications: list[str] = []
        working_signal = signal
        target = signal.target_allocation_pct

        if target > self._effective_max_allocation:
            modifications.append(
                f"allocation capped từ {target} xuống {self._effective_max_allocation} "
                f"(max_position_pct={self.max_position_pct}, cash_buffer={self.min_cash_buffer_pct})"
            )
            target = self._effective_max_allocation

        multiplier = breaker.size_multiplier
        if multiplier != Decimal("1"):
            new_target = target * multiplier
            modifications.append(
                f"circuit breaker {breaker.level.value} — allocation nhân {multiplier} "
                f"({target} -> {new_target})"
            )
            target = new_target

        if modifications:
            working_signal = dataclasses.replace(signal, target_allocation_pct=target)  # type: ignore[type-var]

        if self._daily_trade_count >= self.max_trades_per_day:
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=(
                    f"Đã đạt max_trades_per_day={self.max_trades_per_day} — từ chối, chờ reset ngày kế tiếp"
                ),
                modifications=[],
            )

        if self.check_duplicate_order(signal.symbol, signal.direction, now=signal.timestamp):
            return RiskDecision(
                approved=False,
                modified_signal=None,
                rejection_reason=(
                    f"Lệnh trùng {signal.symbol}/{signal.direction} trong "
                    f"{self.duplicate_order_window_seconds}s — bị chặn"
                ),
                modifications=[],
            )

        self._daily_trade_count += 1
        return RiskDecision(
            approved=True, modified_signal=working_signal, rejection_reason=None, modifications=modifications
        )

    def _approve_exit(self, signal: SignalLike, portfolio_state: PortfolioState) -> RiskDecision:
        """Lệnh giảm exposure về 0 — LUÔN duyệt. Ghi nhận và đếm, không chặn.

        Vì sao không có cổng nào áp cho nhánh này:

        - **halt lock / circuit breaker**: cả hai tồn tại để NGĂN MỞ THÊM
          rủi ro sau khi đã lỗ. Dùng chúng để chặn một lệnh thoát là quay
          ngược mục đích của chính chúng — bot thủng stop giữa lúc circuit
          breaker đang HALT sẽ ngồi im với vị thế đang lỗ cho tới lần reset
          ngày kế tiếp.
        - **`max_trades_per_day`**: hạn mức chống giao dịch quá tay. Một
          lệnh thoát bắt buộc không phải giao dịch quá tay.
        - **lệnh trùng**: gửi lệnh đóng hai lần vô hại — lần thứ hai thấy
          qty = 0 và không làm gì (`OrderExecutor.close_position`), và
          `order_link_id` deterministic khiến sàn tự từ chối bản sao.
        - **`stop_loss is None`**: bất biến #5 đòi mọi VỊ THẾ phải có stop.
          Sau lệnh này không còn vị thế nào, nên không có gì để bảo vệ.

        Vẫn `circuit_breaker.update()` và vẫn tăng `_daily_trade_count`:
        "không chặn" không có nghĩa "không ghi nhận". Drawdown và số lệnh
        trong ngày phải phản ánh đúng cả những lệnh thoát, nếu không thì
        hạn mức của ngày hôm sau tính trên số liệu thiếu.

        Lệnh vẫn đi qua `validate_signal()` chứ không đi vòng — thoả
        CLAUDE.md bất biến #4 (mọi lệnh qua risk manager, không có cờ
        bypass, không có "chế độ khẩn cấp").
        """
        self.circuit_breaker.update(portfolio_state.equity, timestamp=signal.timestamp)
        breaker = self.circuit_breaker.check()
        if breaker.level is BreakerLevel.PEAK_HALT:
            # Vẫn ghi lock để chặn MỌI lệnh vào sau đó — chỉ riêng lệnh
            # thoát này được đi tiếp.
            self._write_halt_lock(breaker)

        # Tiêu một suất trong cửa sổ chống trùng như mọi lệnh khác, để lần
        # gọi sau nhìn thấy đúng mốc thời gian. KHÔNG dùng kết quả để chặn.
        self.check_duplicate_order(signal.symbol, signal.direction, now=signal.timestamp)
        self._daily_trade_count += 1

        return RiskDecision(
            approved=True,
            modified_signal=signal,
            rejection_reason=None,
            modifications=[
                f"lệnh THOÁT (target={signal.target_allocation_pct}) — duyệt vô điều kiện, "
                f"circuit_breaker={breaker.level.value}"
            ],
        )

    # ------------------------------------------------------------------
    # Correlation — giữ interface, bỏ logic ở v1 (một tài sản, §5.6)
    # ------------------------------------------------------------------

    def check_correlation(self, signal: SignalLike, positions: dict) -> RiskDecision:
        """Bỏ ở v1 (một tài sản) — luôn approved. Giữ nguyên interface để
        mở rộng đa tài sản không phải sửa kiến trúc."""
        return RiskDecision(approved=True, modified_signal=signal, rejection_reason=None, modifications=[])

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def compute_position_size(
        self, equity: Decimal, entry: Decimal, stop_loss: Decimal, max_allocation: Decimal
    ) -> Decimal:
        """(equity * max_risk_per_trade_pct) / abs(entry - stop_loss), cap
        theo `max_allocation` (trần của regime, tham số truyền vào — vd.
        `signal.target_allocation_pct`) rồi cap theo `max_position_pct` của
        danh mục (CLAUDE.md bất biến #2: chỉ min, không bao giờ max).

        Trả về SỐ LƯỢNG (đơn vị tài sản cơ sở, vd. BTC) — không phải %.
        `entry <= 0` hoặc `entry == stop_loss` (risk-per-unit vô nghĩa/chia
        0) trả về 0, không đoán bừa.
        """
        if entry <= 0 or entry == stop_loss:
            return Decimal("0")

        price_risk = abs(entry - stop_loss)
        risk_based_qty = (equity * self.max_risk_per_trade_pct) / price_risk
        regime_cap_qty = max_allocation * equity / entry
        portfolio_cap_qty = self.max_position_pct * equity / entry

        return min(risk_based_qty, regime_cap_qty, portfolio_cap_qty)

    # ------------------------------------------------------------------
    # Rule đặc thù crypto (§5.4)
    # ------------------------------------------------------------------

    def check_spread(self, bid: Decimal, ask: Decimal) -> bool:
        """True nếu spread CHẤP NHẬN ĐƯỢC (<= spread_max_pct). False = từ
        chối — spread rộng bất thường là dấu hiệu thanh khoản đang hỏng."""
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        mid = (bid + ask) / Decimal("2")
        spread_pct = (ask - bid) / mid * _PCT_TO_RATE
        return spread_pct <= self.spread_max_pct

    def check_stablecoin_peg(self, usdt_price_usd: Decimal) -> bool:
        """True nếu USDT còn TRONG peg (lệch <= usdt_depeg_threshold_pct).
        False = tạm dừng giao dịch mới, cảnh báo (§5.4)."""
        deviation_pct = abs(usdt_price_usd - Decimal("1")) * _PCT_TO_RATE
        in_peg = deviation_pct <= self.usdt_depeg_threshold_pct
        if not in_peg:
            logger.warning(
                "USDT lệch peg %.3f%% (giá %s) — tạm dừng lệnh mới (§5.4)", deviation_pct, usdt_price_usd
            )
        return in_peg

    def log_exchange_concentration(self, total_balance_on_exchange: Decimal) -> None:
        """Log tổng số dư đang giữ trên sàn — rủi ro đối tác của sàn có
        thật và không được mô hình hoá ở đâu khác trong hệ thống (§5.4)."""
        logger.info(
            "Tổng số dư trên sàn: %s USDT — rủi ro đối tác không mô hình hoá được",
            total_balance_on_exchange,
        )

    # ------------------------------------------------------------------
    # Lệnh trùng (§5.5)
    # ------------------------------------------------------------------

    def check_duplicate_order(
        self, symbol: str, direction: str, *, now: Optional[datetime] = None
    ) -> bool:
        """True = ĐÂY LÀ LỆNH TRÙNG, phải chặn. False = không trùng — hàm
        này CŨNG ghi nhận (symbol, direction, now) làm mốc mới cho lần gọi
        sau, nên chỉ gọi khi thật sự sắp gửi lệnh (validate_signal gọi nó
        SAU CÙNG, sau khi mọi kiểm tra khác đã qua — một lệnh bị từ chối vì
        lý do khác không được phép "tiêu" mất cửa sổ chống trùng).

        `now` mặc định giờ thật cho caller trực tiếp; `validate_signal`
        truyền tường minh `signal.timestamp` để không phụ thuộc đồng hồ hệ
        thống (tái lập được, khớp mọi nơi khác trong hệ thống dùng
        timestamp của chính dữ liệu thay vì `datetime.now()`).
        """
        now = now or datetime.now(timezone.utc)
        key = (symbol, direction)
        last_seen = self._recent_orders.get(key)
        if last_seen is not None and (now - last_seen).total_seconds() < self.duplicate_order_window_seconds:
            return True
        self._recent_orders[key] = now
        return False

    # ------------------------------------------------------------------
    # Reset theo ranh giới ngày/tuần (CLAUDE.md bất biến #10) — caller
    # (main loop) gọi đúng lúc, cùng lý do CircuitBreaker.reset_daily().
    # ------------------------------------------------------------------

    def reset_daily(self) -> None:
        self._daily_trade_count = 0
        self.circuit_breaker.reset_daily()

    def reset_weekly(self) -> None:
        self.circuit_breaker.reset_weekly()

    # ------------------------------------------------------------------
    # Halt lock (§5.2 — peak DD > 20%)
    # ------------------------------------------------------------------

    def _is_halted(self) -> bool:
        return self._halt_lock_path.exists()

    def _write_halt_lock(self, breaker: BreakerStatus) -> None:
        if self._is_halted():
            return
        content = (
            f"PEAK_HALT tại {breaker.triggered_at}\n"
            f"peak_dd={breaker.peak_dd:.2f}% > ngưỡng {self.circuit_breaker.peak_dd_halt_pct}%\n"
            "Xoá file này thủ công sau khi đã xem xét trước khi chạy lại "
            "(Brain-Crypto-Bybit.md §5.2).\n"
        )
        self._halt_lock_path.write_text(content, encoding="utf-8")
        logger.warning("Đã ghi %s — dừng toàn bộ giao dịch, cần xoá thủ công.", self._halt_lock_path)
