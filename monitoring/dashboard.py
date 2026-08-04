"""monitoring.dashboard — dashboard terminal dùng thư viện `rich`, refresh mỗi 5 giây.

Ô "Phí" là bổ sung so với bản gốc và cần nhìn thấy thường xuyên — nó là
chỉ báo sớm cho việc giao dịch quá nhiều (xem CLAUDE.md bất biến #7).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable


@dataclass(frozen=True)
class DashboardState:
    regime_label: str
    regime_probability: float
    vol_rank: str
    stability_bars: int
    flicker_count: int
    flicker_window: int
    is_confirmed: bool
    equity: Decimal
    daily_pnl: Decimal
    daily_pnl_pct: Decimal
    allocation_pct: Decimal
    position_qty: Decimal
    cash: Decimal
    daily_dd_pct: Decimal
    daily_dd_limit_pct: Decimal
    peak_dd_pct: Decimal
    peak_dd_limit_pct: Decimal
    monthly_fees_paid: Decimal
    monthly_fees_pct_of_gross: Decimal
    ws_connected: bool
    ws_last_message_seconds_ago: float
    api_latency_ms: float
    clock_drift_ms: float
    hmm_last_trained_days_ago: int
    is_testnet: bool


class Dashboard:
    def __init__(self, refresh_interval_seconds: int = 5) -> None:
        ...

    def render(self, state: DashboardState) -> None:
        """Vẽ layout REGIME / PORTFOLIO / VỊ THẾ / SIGNAL GẦN ĐÂY / RISK / HỆ THỐNG."""
        raise NotImplementedError

    def run(self, state_provider: Callable[[], DashboardState]) -> None:
        """Vòng lặp refresh mỗi refresh_interval_seconds."""
        raise NotImplementedError
