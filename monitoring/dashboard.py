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

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

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
    # HỆ THỐNG — REST polling (không WebSocket, xem docs/DECISIONS.md "Đổi
    # sàn Bybit -> Binance"). `ws_connected`/`ws_last_message_seconds_ago`
    # đã bị bỏ (2026-08-07): không có kết nối bền để "connected"/"disconnected",
    # không có message đẩy tới để đo "bao lâu kể từ tin nhắn cuối" — hai
    # field đó mô tả một kiến trúc không còn tồn tại trong hệ thống này.
    # Thay bằng:
    # round-trip của lần fetch OHLCV gần nhất (history_loader.load()).
    poll_latency_ms: float
    # ISO UTC — lúc lần fetch OHLCV gần nhất XẢY RA. KHÔNG phải mỗi lần
    # lặp vòng poll: main.py::run_live_loop chỉ gọi mạng khi phát hiện có
    # bar mới (so sánh ngày cục bộ trước, không tốn round-trip nào ở phần
    # lớn chu kỳ 60s không có gì mới) — xem docstring run_live_loop.
    last_poll_at: str
    # Bar đã đóng - bar đã xử lý xong gần nhất; 0 = đồng bộ. TÍNH LẠI mỗi
    # lần dựng DashboardState (xem main.py::compute_bars_behind), KHÔNG
    # lưu trong state_snapshot.json: một giá trị lưu sẵn sẽ đứng yên "0"
    # ngay lúc tiến trình chính đã chết — đúng lúc field này cần báo động
    # nhất, một con số đông cứng sẽ nói dối đúng lúc quan trọng nhất.
    bars_behind: int
    api_latency_ms: float
    clock_drift_ms: float
    hmm_last_trained_days_ago: int
    is_testnet: bool


# ----------------------------------------------------------------------
# Panel SO SÁNH BASELINE — CHỈ ĐỌC `drift.json`, không tính lại gì
# ----------------------------------------------------------------------

# `monitoring/drift.py` (Phase 12b §C.1) sinh file này.
#
# Đường dẫn ĐÃ ĐỔI 2026-08-14: `monitoring/state/drift.json` (như §C.1
# viết) -> `${STATE_DIR}/drift.json`. `monitoring/state/` nằm trong cây MÃ
# NGUỒN, và `status.json` đã phải chuyển khỏi đúng chỗ ấy ngày 2026-08-08
# (xem `alerts.py::_default_status_path`). Bên GHI (`drift.py`) và bên ĐỌC
# (file này) phải đổi TRONG CÙNG MỘT commit — tách ra thì panel sẽ hiện
# "chưa có dữ liệu drift" dù `drift.py` đã chạy xong, và người debug đi
# tìm nhầm chỗ.
#
# Hàm chứ không phải hằng số mức module: `STATE_DIR` được đặt lúc chạy, và
# một hằng số tính lúc import sẽ đóng băng giá trị sai.
def _default_drift_path() -> Path:
    from monitoring.drift import default_drift_path

    return default_drift_path()

# Bốn trạng thái, CỐ TÌNH tách rời nhau. Gộp "file hỏng" vào "chưa có dữ
# liệu" là đúng cái bẫy mà panel này sinh ra để tránh: người vận hành đọc
# "chưa có dữ liệu", cho rằng Phase 12b chưa xây xong, và một file JSON
# hỏng nằm im vô thời hạn.
DRIFT_OK = "ok"
DRIFT_MISSING = "missing"  # chưa có file — TRẠNG THÁI MONG ĐỢI trước Phase 12b
DRIFT_UNREADABLE = "unreadable"  # có file nhưng không đọc/không hiểu được — SỰ CỐ
DRIFT_EMPTY = "empty"  # đọc được, đúng schema, nhưng không có chỉ số nào


@dataclass(frozen=True)
class DriftMetric:
    """MỘT dòng so sánh. `current`/`baseline` là chuỗi ĐÃ ĐỊNH DẠNG sẵn
    bởi bên ghi — panel không làm phép tính nào trên chúng.

    Cố ý không dùng `Decimal`/`float`: mỗi chỉ số trong bảng Phase 12b §C.1
    có đơn vị khác nhau (điểm %, tỷ lệ, lần, số đếm). Ép về một kiểu số ở
    đây nghĩa là panel phải biết đơn vị của từng chỉ số — tức là biết logic
    drift, đúng thứ nó KHÔNG được biết.
    """

    name: str
    current: str
    baseline: str
    alert: bool


@dataclass(frozen=True)
class DriftPanelData:
    """Thứ panel cần để vẽ. `status` luôn là một trong bốn hằng số trên."""

    status: str
    detail: str
    path: Path
    generated_at_utc: Optional[str] = None
    metrics: tuple[DriftMetric, ...] = ()


def _coerce_metric(raw: Any) -> Optional[DriftMetric]:
    """`None` nếu phần tử không đúng hình dạng — bỏ qua phần tử hỏng còn
    hơn làm hỏng cả panel, nhưng caller đếm lại để không im lặng."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    return DriftMetric(
        name=name,
        current=str(raw.get("current", "?")),
        baseline=str(raw.get("baseline", "?")),
        alert=bool(raw.get("alert", False)),
    )


def load_drift_panel_data(path: Optional[Path] = None) -> DriftPanelData:
    """Đọc `drift.json`. KHÔNG BAO GIỜ raise, KHÔNG BAO GIỜ tính lại drift.

    Đây là bên ĐỌC. Toàn bộ logic so sánh baseline thuộc về
    `monitoring/drift.py` (Phase 12b §C.1) — nếu panel tự tính khi thiếu
    file, dashboard sẽ hiển thị một con số KHÁC với con số mà cơ chế cảnh
    báo dùng, và hai nguồn sự thật cho cùng một chỉ số là cách chắc chắn
    nhất để không ai tin cái nào.

    **Hợp đồng schema tối thiểu** — đây là thứ Phase 12b phải ghi ra:

        {
          "generated_at_utc": "2026-08-08T00:05:00+00:00",   // tuỳ chọn
          "metrics": [
            {"name": "Phân bố allocation",
             "current": "28.1 / 19.0 / 15.2 / 37.7 %",
             "baseline": "30.6 / 18.1 / 16.5 / 34.8 %",
             "alert": false}
          ]
        }

    `current`/`baseline` là CHUỖI đã định dạng, `alert` là bool đã quyết
    định bởi bên ghi. Panel chỉ tô màu theo `alert`.

    Sai hợp đồng -> `DRIFT_UNREADABLE` kèm thông điệp nói rõ chờ đợi gì,
    KHÔNG phải `DRIFT_MISSING`.
    """
    target = path if path is not None else _default_drift_path()

    if not target.exists():
        return DriftPanelData(
            status=DRIFT_MISSING,
            detail=(
                f"Chưa có dữ liệu drift — {target} chưa tồn tại.\n"
                "File này do monitoring/drift.py sinh ra (Phase 12b §C.1), chưa xây."
            ),
            path=target,
        )

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — mọi lỗi đọc/parse đều là "không dùng được"
        return DriftPanelData(
            status=DRIFT_UNREADABLE,
            detail=(
                f"{target} TỒN TẠI nhưng không đọc được: {type(exc).__name__}: {exc}\n"
                "Đây KHÔNG phải 'chưa có dữ liệu' — có thứ gì đó đã ghi hỏng file."
            ),
            path=target,
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), list):
        return DriftPanelData(
            status=DRIFT_UNREADABLE,
            detail=(
                f"{target} sai hợp đồng schema — chờ đợi một object có khoá "
                '"metrics" là list. Xem docstring load_drift_panel_data().'
            ),
            path=target,
        )

    raw_metrics = payload["metrics"]
    metrics = tuple(m for m in (_coerce_metric(r) for r in raw_metrics) if m is not None)
    generated_at = payload.get("generated_at_utc")
    generated_at_str = generated_at if isinstance(generated_at, str) else None

    if not metrics:
        return DriftPanelData(
            status=DRIFT_EMPTY,
            detail=(
                f"{target} đọc được nhưng không có chỉ số nào dùng được "
                f"({len(raw_metrics)} phần tử thô, 0 phần tử đúng hình dạng)."
            ),
            path=target,
            generated_at_utc=generated_at_str,
        )

    skipped = len(raw_metrics) - len(metrics)
    detail = f"{len(metrics)} chỉ số"
    if skipped:
        # Không im lặng bỏ qua: một phần tử sai hình dạng nghĩa là bên ghi
        # và bên đọc đã lệch hợp đồng, phải nhìn thấy được.
        detail += f" ({skipped} phần tử sai hình dạng, đã bỏ qua)"
    return DriftPanelData(
        status=DRIFT_OK,
        detail=detail,
        path=target,
        generated_at_utc=generated_at_str,
        metrics=metrics,
    )


def drift_metric_style(metric: DriftMetric) -> str:
    """Tô màu THEO CỜ `alert` của bên ghi — KHÔNG so sánh
    `current` với `baseline`.

    Tách thành hàm riêng (thay vì một biểu thức inline trong panel) vì
    `render_text()` lược bỏ màu, nên một lỗi ở đây KHÔNG quan sát được từ
    text đã render — đo bằng đột biến: đổi thành
    `metric.current != metric.baseline` và toàn bộ test panel vẫn xanh.
    Hàm thuần thì kiểm trực tiếp được.

    Vì sao không được tự so sánh: ngưỡng cảnh báo của từng chỉ số nằm ở
    bảng Phase 12b §C.1 (lệch > 15 điểm %, > 10 điểm %, > 20%, cao hơn
    2×...) — mỗi chỉ số một quy tắc khác nhau. Panel so sánh chuỗi bằng
    `!=` sẽ báo động ở mọi khác biệt dù nhỏ, tức là tự bịa ra một logic
    drift thứ hai, khác với logic mà cơ chế cảnh báo thật đang dùng.
    """
    return _STYLE_DANGER if metric.alert else _STYLE_OK


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
    def __init__(self, refresh_interval_seconds: int = 5, *, drift_path: Optional[Path] = None) -> None:
        self.refresh_interval_seconds = refresh_interval_seconds
        self.console = Console()
        # `drift.json` do một TIẾN TRÌNH KHÁC ghi (Phase 12b §C.1), nên
        # panel đọc lại ở mỗi lần vẽ thay vì nhận qua `DashboardState`.
        # Hệ quả có chủ ý: `DashboardState` KHÔNG phải thêm field thứ 27 và
        # `main.py` không phải luồn dữ liệu drift qua toàn bộ vòng lặp chỉ
        # để hiển thị. Đọc file nhỏ mỗi 5s là chi phí chấp nhận được, và
        # `load_drift_panel_data()` không bao giờ raise nên I/O ở đây không
        # làm vỡ `render()`.
        self._drift_path = drift_path

    # ------------------------------------------------------------------

    def _build_renderable(self, state: DashboardState) -> RenderableType:
        return Group(
            self._regime_panel(state),
            self._portfolio_panel(state),
            self._position_panel(state),
            self._signals_panel(state),
            self._risk_panel(state),
            self._drift_panel(),
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

    def _drift_panel(self) -> Panel:
        """SO SÁNH BASELINE. Luôn vẽ MỘT panel có nội dung đọc được —
        không bao giờ để trống, không bao giờ raise.

        "Để trống im lặng" là chế độ hỏng tệ nhất ở đây: một ô rỗng trông
        y hệt "không có gì đáng lo", trong khi nó có thể nghĩa là cơ chế
        phát hiện trôi lệch chưa từng chạy. Bốn trạng thái, bốn thông điệp
        khác nhau, không cái nào rỗng.
        """
        data = load_drift_panel_data(self._drift_path)

        if data.status == DRIFT_OK:
            grid = Table.grid(padding=(0, 3))
            grid.add_column()
            grid.add_column()
            grid.add_column()
            grid.add_column()
            grid.add_row("Chỉ số", "Hiện tại", "Baseline", "")
            for metric in data.metrics:
                style = drift_metric_style(metric)
                grid.add_row(
                    Text(metric.name, style=style),
                    Text(metric.current, style=style),
                    Text(metric.baseline, style=style),
                    Text(_bool_icon(not metric.alert), style=style),
                )
            stamp = data.generated_at_utc or "không rõ thời điểm"
            grid.add_row(Text(f"Cập nhật: {stamp} — {data.detail}"), "", "", "")
            body: RenderableType = grid
        elif data.status == DRIFT_MISSING:
            # VÀNG, không đỏ: trước Phase 12b đây là trạng thái ĐÚNG của hệ
            # thống, không phải sự cố. Đỏ ở đây sẽ làm dashboard lúc nào
            # cũng có một ô đỏ và người xem quen mắt bỏ qua nó.
            body = Text(data.detail, style=_STYLE_WARN)
        elif data.status == DRIFT_UNREADABLE:
            # ĐỎ: có file mà không dùng được là sự cố thật.
            body = Text(data.detail, style=_STYLE_DANGER)
        else:  # DRIFT_EMPTY
            body = Text(data.detail, style=_STYLE_WARN)

        return Panel(body, title="SO SÁNH BASELINE", title_align="left")

    def _system_panel(self, state: DashboardState) -> Panel:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column()
        grid.add_column()

        bars_behind_ok = state.bars_behind == 0
        if bars_behind_ok:
            bars_behind_style = _STYLE_OK
        elif state.bars_behind == 1:
            bars_behind_style = _STYLE_WARN
        else:
            bars_behind_style = _STYLE_DANGER
        bars_behind_text = Text(
            f"Trễ: {state.bars_behind} bar {_bool_icon(bars_behind_ok)}", style=bars_behind_style
        )
        drift_style = _STYLE_OK if abs(state.clock_drift_ms) <= 1000 else _STYLE_DANGER
        grid.add_row(
            f"Poll: {state.poll_latency_ms:.0f}ms lúc {state.last_poll_at}",
            f"API: {state.api_latency_ms:.0f}ms",
            Text(f"Lệch giờ: {state.clock_drift_ms:.0f}ms", style=drift_style),
        )
        env_label = "TESTNET" if state.is_testnet else "MAINNET"
        env_style = _STYLE_OK if state.is_testnet else _STYLE_DANGER
        grid.add_row(
            bars_behind_text,
            f"HMM: {state.hmm_last_trained_days_ago} ngày trước",
            Text(env_label, style=env_style),
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
