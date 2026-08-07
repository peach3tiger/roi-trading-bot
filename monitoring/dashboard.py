"""monitoring.dashboard — dashboard terminal dùng thư viện `rich`, refresh mỗi 5 giây.

Ô "Phí" là bổ sung so với bản gốc và cần nhìn thấy thường xuyên — nó là
chỉ báo sớm cho việc giao dịch quá nhiều (xem CLAUDE.md bất biến #7).

Layout theo `docs/Brain-Crypto-Bybit.md` §8.2:
REGIME / PORTFOLIO / VỊ THẾ / SIGNAL GẦN ĐÂY / RISK / HỆ THỐNG.

`render()` in trực tiếp ra `self.console` (dùng khi gọi một lần, vd.
`main.py --dashboard` không có `--watch`). `render_text()` KHÔNG in ra
đâu cả — dựng lại renderable trên một `Console(record=True)` riêng và trả
về text đã "chụp màn hình", dùng cho test (không cần TTY thật) và cho
nghiệm thu "Dashboard chạy được... chụp lại màn hình dạng text".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_STYLE_OK = "bold green"
_STYLE_WARN = "bold yellow"
_STYLE_DANGER = "bold red"

# "FLAT" — không có vị thế mở, xem DashboardState.position_direction. Không
# phải một trong hai giá trị OrderSide (BUY/SELL) của broker/base.py vì
# đây là NHÃN HIỂN THỊ (LONG/FLAT), không phải hướng lệnh.
_FLAT = "FLAT"


@dataclass(frozen=True)
class RecentSignal:
    """Một dòng trong panel "SIGNAL GẦN ĐÂY" — vd. spec §8.2:
    "00:00 UTC │ Rebalance 60%→95% │ Vol thấp, xu hướng OK"."""

    timestamp_utc: str
    action: str
    reasoning: str


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
    # VỊ THẾ — position_direction == "FLAT" nghĩa là không có vị thế mở;
    # các field còn lại của nhóm này là None khi FLAT (không có entry/stop/
    # số ngày giữ để hiển thị).
    position_direction: str
    position_entry_price: Optional[Decimal]
    position_unrealized_pnl_pct: Optional[Decimal]
    position_stop_loss: Optional[Decimal]
    position_days_held: Optional[int]
    # SIGNAL GẦN ĐÂY — mới nhất trước, tối đa vài dòng gần nhất (caller
    # quyết định cắt bao nhiêu, panel chỉ vẽ những gì được truyền vào).
    recent_signals: tuple[RecentSignal, ...]
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


def _risk_style(value: Decimal, limit: Decimal) -> str:
    """Xanh dưới 50% giới hạn, vàng 50-100%, đỏ khi CHẠM/VƯỢT giới hạn.
    `limit <= 0` coi như không có ngưỡng thật (tránh chia 0) -> OK."""
    if limit <= 0:
        return _STYLE_OK
    ratio = value / limit
    if ratio >= Decimal("1"):
        return _STYLE_DANGER
    if ratio >= Decimal("0.5"):
        return _STYLE_WARN
    return _STYLE_OK


def _bool_style(ok: bool) -> str:
    return _STYLE_OK if ok else _STYLE_DANGER


def _bool_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


class Dashboard:
    def __init__(self, refresh_interval_seconds: int = 5) -> None:
        self.refresh_interval_seconds = refresh_interval_seconds
        self.console = Console()

    # ------------------------------------------------------------------

    def _build_renderable(self, state: DashboardState) -> RenderableType:
        return Group(
            self._regime_panel(state),
            self._portfolio_panel(state),
            self._position_panel(state),
            self._signals_panel(state),
            self._risk_panel(state),
            self._system_panel(state),
        )

    def _regime_panel(self, state: DashboardState) -> Panel:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        grid.add_column()
        grid.add_row(
            f"[bold]{state.regime_label}[/bold] ({state.regime_probability:.0%})",
            f"Vol rank: {state.vol_rank}",
            f"Ổn định: {state.stability_bars} bar",
        )
        confirm_text = Text(
            f"Xác nhận: {_bool_icon(state.is_confirmed)}", style=_bool_style(state.is_confirmed)
        )
        grid.add_row(f"Flicker: {state.flicker_count}/{state.flicker_window}", confirm_text, "")
        return Panel(grid, title="REGIME", title_align="left")

    def _portfolio_panel(self, state: DashboardState) -> Panel:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        grid.add_column()
        pnl_style = _STYLE_OK if state.daily_pnl >= 0 else _STYLE_DANGER
        pnl_text = Text(
            f"Ngày: {state.daily_pnl:+.2f} ({state.daily_pnl_pct:+.2f}%)", style=pnl_style
        )
        grid.add_row(f"Equity: {state.equity:,.2f} USDT", pnl_text, "")
        grid.add_row(
            f"Allocation: {state.allocation_pct:.0%}",
            f"BTC: {state.position_qty}",
            f"Cash: {state.cash:,.2f} USDT",
        )
        return Panel(grid, title="PORTFOLIO", title_align="left")

    def _position_panel(self, state: DashboardState) -> Panel:
        if state.position_direction == _FLAT or state.position_entry_price is None:
            body: RenderableType = Text("Không có vị thế mở (FLAT)")
        else:
            pnl_pct = state.position_unrealized_pnl_pct or Decimal("0")
            pnl_style = _STYLE_OK if pnl_pct >= 0 else _STYLE_DANGER
            stop_label = f"{state.position_stop_loss:,.0f}" if state.position_stop_loss is not None else "—"
            grid = Table.grid(padding=(0, 3))
            for _ in range(6):
                grid.add_column()
            grid.add_row(
                "BTCUSDT",
                state.position_direction,
                f"{state.position_entry_price:,.0f}",
                Text(f"{pnl_pct:+.1f}%", style=pnl_style),
                f"Stop: {stop_label}",
                f"{state.position_days_held}d",
            )
            body = grid
        return Panel(body, title="VỊ THẾ", title_align="left")

    def _signals_panel(self, state: DashboardState) -> Panel:
        if not state.recent_signals:
            body: RenderableType = Text("Chưa có signal nào được ghi nhận.")
        else:
            grid = Table.grid(padding=(0, 3))
            grid.add_column()
            grid.add_column()
            grid.add_column()
            for sig in state.recent_signals:
                grid.add_row(sig.timestamp_utc, sig.action, sig.reasoning)
            body = grid
        return Panel(body, title="SIGNAL GẦN ĐÂY", title_align="left")

    def _risk_panel(self, state: DashboardState) -> Panel:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        daily_style = _risk_style(state.daily_dd_pct, state.daily_dd_limit_pct)
        peak_style = _risk_style(state.peak_dd_pct, state.peak_dd_limit_pct)
        daily_ok = state.daily_dd_pct < state.daily_dd_limit_pct
        peak_ok = state.peak_dd_pct < state.peak_dd_limit_pct
        grid.add_row(
            Text(
                f"DD ngày: {state.daily_dd_pct:.1f}%/{state.daily_dd_limit_pct:.1f}% {_bool_icon(daily_ok)}",
                style=daily_style,
            ),
            Text(
                f"Từ đỉnh: {state.peak_dd_pct:.1f}%/{state.peak_dd_limit_pct:.1f}% {_bool_icon(peak_ok)}",
                style=peak_style,
            ),
        )
        # "Phí tháng này" LUÔN hiển thị, ở panel RISK, không được rút gọn
        # hay ẩn có điều kiện — xem docstring module + phase-11-monitoring.md.
        grid.add_row(
            f"Phí tháng này: {state.monthly_fees_paid:,.2f} USDT "
            f"({state.monthly_fees_pct_of_gross:.1f}% lợi nhuận gộp)",
            "",
        )
        return Panel(grid, title="RISK", title_align="left")

    def _system_panel(self, state: DashboardState) -> Panel:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        grid.add_column()
        ws_text = Text(
            f"WS: {_bool_icon(state.ws_connected)} {state.ws_last_message_seconds_ago:.0f}s trước",
            style=_bool_style(state.ws_connected),
        )
        drift_style = _STYLE_OK if abs(state.clock_drift_ms) <= 1000 else _STYLE_DANGER
        grid.add_row(
            ws_text,
            f"API: ✅ {state.api_latency_ms:.0f}ms",
            Text(f"Lệch giờ: {state.clock_drift_ms:.0f}ms", style=drift_style),
        )
        env_label = "TESTNET" if state.is_testnet else "MAINNET"
        env_style = _STYLE_OK if state.is_testnet else _STYLE_DANGER
        grid.add_row(
            f"HMM: {state.hmm_last_trained_days_ago} ngày trước",
            Text(env_label, style=env_style),
            "",
        )
        return Panel(grid, title="HỆ THỐNG", title_align="left")

    # ------------------------------------------------------------------

    def render(self, state: DashboardState) -> None:
        """Vẽ layout REGIME / PORTFOLIO / VỊ THẾ / SIGNAL GẦN ĐÂY / RISK / HỆ THỐNG."""
        self.console.print(self._build_renderable(state))

    def render_text(self, state: DashboardState, *, width: int = 100) -> str:
        """Không in ra đâu — trả về text đã render, dùng cho test và cho
        "chụp lại màn hình dạng text" (nghiệm thu phase-11-monitoring.md)."""
        capture = Console(record=True, width=width, force_terminal=False)
        capture.print(self._build_renderable(state))
        return capture.export_text()

    def run(self, state_provider: Callable[[], DashboardState]) -> None:
        """Vòng lặp refresh mỗi refresh_interval_seconds.

        `state_provider` raise `StopIteration` để dừng vòng lặp một cách
        sạch sẽ — dùng cho test (fake provider hữu hạn) mà không cần patch
        `time.sleep`/`KeyboardInterrupt` giả. Ctrl+C thật (`KeyboardInterrupt`)
        cũng dừng sạch, không in traceback — đây là dashboard cho thao tác
        viên xem, không phải một process cần giữ sống bằng mọi giá.
        """
        from rich.live import Live

        try:
            with Live(console=self.console, auto_refresh=False, screen=True) as live:
                while True:
                    state = state_provider()
                    live.update(self._build_renderable(state))
                    live.refresh()
                    time.sleep(self.refresh_interval_seconds)
        except (KeyboardInterrupt, StopIteration):
            return
