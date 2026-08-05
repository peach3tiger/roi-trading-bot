"""core.risk_manager — RiskManager với quyền phủ quyết tuyệt đối.

Hoạt động HOÀN TOÀN ĐỘC LẬP với HMM: module này không được import
core.hmm_engine hay bất kỳ thứ gì từ core.regime_strategies (xem CLAUDE.md
bất biến #4). Nó ra quyết định dựa trên P&L thực tế và trạng thái danh
mục — sự độc lập này là lý do nó vẫn bảo vệ được khi HMM sai hoàn toàn.

Vì lý do đó, `Signal` được mô tả ở đây bằng một structural type
(`SignalLike`) thay vì import trực tiếp `core.regime_strategies.Signal`.

Mọi lệnh đi qua validate_signal(). Không có đường vòng, không có cờ bypass,
không có "chế độ khẩn cấp" bỏ qua nó.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Protocol


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


class BreakerLevel(Enum):
    NONE = "NONE"
    DAILY_REDUCE = "DAILY_REDUCE"
    DAILY_HALT = "DAILY_HALT"
    WEEKLY_REDUCE = "WEEKLY_REDUCE"
    WEEKLY_HALT = "WEEKLY_HALT"
    PEAK_HALT = "PEAK_HALT"


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
    """Kích hoạt theo P&L thực tế, độc lập với regime. Ranh giới ngày 00:00 UTC."""

    def __init__(
        self,
        daily_dd_reduce_pct: Decimal,
        daily_dd_halt_pct: Decimal,
        weekly_dd_reduce_pct: Decimal,
        weekly_dd_halt_pct: Decimal,
        peak_dd_halt_pct: Decimal,
    ) -> None:
        ...

    def check(self) -> BreakerStatus:
        raise NotImplementedError

    def update(self, pnl: Decimal) -> None:
        raise NotImplementedError

    def reset_daily(self) -> None:
        """00:00 UTC."""
        raise NotImplementedError

    def reset_weekly(self) -> None:
        """Thứ Hai 00:00 UTC."""
        raise NotImplementedError

    def get_history(self) -> list[BreakerStatus]:
        raise NotImplementedError


class RiskManager:
    """Quyền phủ quyết tuyệt đối với mọi signal — kiểm tra size, stop loss
    bắt buộc, circuit breaker, spread, peg stablecoin, lệnh trùng.
    """

    def __init__(self, config: dict) -> None:
        ...

    def validate_signal(self, signal: SignalLike, portfolio_state: PortfolioState) -> RiskDecision:
        """Điểm phủ quyết duy nhất — mọi lệnh phải đi qua đây, không ngoại lệ."""
        raise NotImplementedError

    def check_correlation(self, signal: SignalLike, positions: dict) -> RiskDecision:
        """Bỏ ở v1 (một tài sản) — luôn approved. Giữ nguyên interface để
        mở rộng đa tài sản không phải sửa kiến trúc."""
        raise NotImplementedError

    def compute_position_size(
        self, equity: Decimal, entry: Decimal, stop_loss: Decimal, max_allocation: Decimal
    ) -> Decimal:
        """(equity * 0.01) / abs(entry - stop_loss), cap theo max của regime rồi cap theo max danh mục."""
        raise NotImplementedError

    def check_spread(self, bid: Decimal, ask: Decimal) -> bool:
        """Từ chối nếu spread > 0.10%."""
        raise NotImplementedError

    def check_stablecoin_peg(self, usdt_price_usd: Decimal) -> bool:
        """Tạm dừng giao dịch mới nếu USDT lệch peg quá 0.5%."""
        raise NotImplementedError

    def check_duplicate_order(self, symbol: str, direction: str) -> bool:
        """Chặn lệnh trùng cùng symbol + hướng trong 60 giây."""
        raise NotImplementedError
