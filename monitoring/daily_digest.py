"""Báo cáo tổng hợp hằng ngày. §C.2 — chạy 00:05 UTC.

Ghi `logs/digest/YYYY-MM-DD.md`, gửi Telegram nếu đã cấu hình (Phase 11).

## Vì sao 00:05 chứ không 00:00

Ranh giới ngày là 00:00 UTC (CLAUDE.md bất biến #10) và bar cuối của ngày
đóng đúng lúc đó. Chạy digest tại 00:00 là chạy đua với chính vòng lặp
đang xử lý bar cuối cùng — năm phút đệm đủ để bar đó đã ghi xong vào log.

## Nguồn dữ liệu, và cái giá của việc thiếu chúng

| Mục §C.2 | Nguồn |
|---|---|
| Bar xử lý, phân bố regime, đổi regime | `logs/regime.log` (JSON lines) |
| Lệnh, lệnh bị từ chối kèm lý do | `logs/trades.log` |
| Tầng giới hạn (HMM / trend gate / risk) | ba trần trong `health.json` + `regime.log` |
| P&L, phí, drawdown | `logs/regime.log` + `state_snapshot.json` |
| Cảnh báo drift đang bật | `${STATE_DIR}/drift.json` (CHỈ ĐỌC, không tính lại) |
| Số warning hmmlearn | `forward/log_v2.csv` (CHỈ ĐỌC) |

Nguồn thiếu -> mục đó ghi rõ **"không có dữ liệu"** kèm đường dẫn đã tìm,
KHÔNG bị bỏ khỏi báo cáo. Một mục biến mất trông giống hệt một mục bằng 0,
và hai điều đó cần phản ứng khác hẳn nhau — cùng nguyên tắc với panel drift
(`dashboard.py::load_drift_panel_data`).

## Không tính lại drift

Digest ĐỌC `drift.json`. `monitoring/drift.py` là bên duy nhất quyết định
cờ `alert`. Hai nguồn sự thật cho cùng một chỉ số là cách chắc chắn nhất
để không ai tin cái nào.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "state"
_DEFAULT_LOG_DIR = "logs"

# Bốn nhãn "tầng nào đang giới hạn". Xem `limiting_layer()` về vì sao cần
# nhãn thứ tư.
LAYER_HMM = "HMM"
LAYER_TREND = "trend gate"
LAYER_RISK = "risk manager"
LAYER_TIE = "đồng hạng"

_KHONG_CO_DU_LIEU = "*không có dữ liệu*"


def _state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR))


def digest_path(day: date, log_dir: str | Path = _DEFAULT_LOG_DIR) -> Path:
    return Path(log_dir) / "digest" / f"{day.isoformat()}.md"


# ----------------------------------------------------------------------
# Tầng nào đang giới hạn
# ----------------------------------------------------------------------


def limiting_layer(
    hmm: Optional[Decimal], trend: Optional[Decimal], risk: Optional[Decimal]
) -> Optional[str]:
    """Tầng nào ĐANG giới hạn allocation. `None` khi thiếu dữ liệu.

    KHÔNG dùng `min()` rồi xem trần nào bằng nó. Lý do: trong đường dây
    hiện tại `risk_manager_cap == final_allocation == min(ba trần)` (xem
    `core/signal_generator.py`), nên risk manager LUÔN bằng giá trị nhỏ
    nhất và cách làm ngây thơ sẽ báo "risk manager giới hạn" ở 100% số
    bar — một thống kê đúng về mặt số học và vô dụng về mặt vận hành.

    Định nghĩa đúng: risk manager giới hạn khi nó cắt SÂU HƠN cả hai tầng
    trước. Giữa HMM và trend gate thì bên nào THẤP HƠN là bên giới hạn.
    Bằng nhau -> `LAYER_TIE`: cả hai cùng ràng buộc, và gán bừa cho một
    bên sẽ làm thống kê nghiêng vĩnh viễn về bên đó.
    """
    if hmm is None or trend is None or risk is None:
        return None
    if risk < min(hmm, trend):
        return LAYER_RISK
    if trend < hmm:
        return LAYER_TREND
    if hmm < trend:
        return LAYER_HMM
    return LAYER_TIE


# ----------------------------------------------------------------------
# Đọc log JSON lines
# ----------------------------------------------------------------------


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    """Đọc log JSON-lines. Dòng hỏng -> BỎ QUA dòng đó, không hỏng cả file.

    Log có thể bị cắt giữa chừng nếu tiến trình chết đúng lúc ghi. Mất một
    dòng cuối còn hơn mất cả báo cáo — nhưng số dòng bỏ qua được đếm và in
    ra (`ban_ghi_hong`), vì im lặng bỏ qua sẽ giấu mất việc log đang hỏng.
    """
    if not path.exists():
        return []
    ket_qua: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            ket_qua.append({"_hong": True})
            continue
        if isinstance(obj, dict):
            ket_qua.append(obj)
    return ket_qua


def _on_day(events: Iterable[dict[str, Any]], day: date, key: str = "timestamp") -> list[dict]:
    """Lọc theo NGÀY UTC. Bản ghi không có/không parse được dấu thời gian
    bị loại — một bản ghi không biết thuộc ngày nào không được phép làm
    lệch thống kê của một ngày cụ thể."""
    ra = []
    for e in events:
        raw = e.get(key)
        if not isinstance(raw, str):
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.astimezone(timezone.utc).date() == day:
            ra.append(e)
    return ra


def _dec(raw: Any) -> Optional[Decimal]:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


# ----------------------------------------------------------------------
# Thu thập
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class DigestData:
    """Mọi thứ báo cáo cần, đã tách khỏi I/O.

    `render()` là hàm THUẦN trên kiểu này — đó là điều làm nội dung báo
    cáo test được mà không phải dựng một ngày vận hành giả.
    """

    day: date
    n_bars: int = 0
    regime_counts: dict[str, int] = field(default_factory=dict)
    regime_changes: int = 0
    n_orders: int = 0
    rejections: tuple[tuple[str, int], ...] = ()  # (lý do, số lần)
    layer_counts: dict[str, int] = field(default_factory=dict)
    equity: Optional[Decimal] = None
    daily_pnl: Optional[Decimal] = None
    cumulative_fees: Optional[Decimal] = None
    drawdown_pct: Optional[Decimal] = None
    drift_alerts: Optional[tuple[str, ...]] = None  # None = không đọc được drift.json
    drift_path: Optional[Path] = None
    hmmlearn_warnings: Optional[int] = None
    corrupt_log_lines: int = 0
    missing_sources: tuple[str, ...] = ()


def collect(
    day: date,
    *,
    log_dir: str | Path = _DEFAULT_LOG_DIR,
    state_dir: Optional[Path] = None,
    drift_path: Optional[Path] = None,
) -> DigestData:
    """Gom dữ liệu từ file. Nguồn thiếu -> ghi vào `missing_sources`, KHÔNG
    raise: một ngày không có lệnh nào vẫn phải ra được báo cáo."""
    log_dir = Path(log_dir)
    state = state_dir or _state_dir()
    thieu: list[str] = []

    regime_file = log_dir / "regime.log"
    trades_file = log_dir / "trades.log"
    if not regime_file.exists():
        thieu.append(str(regime_file))
    if not trades_file.exists():
        thieu.append(str(trades_file))

    regime_events_all = read_json_lines(regime_file)
    trade_events_all = read_json_lines(trades_file)
    hong = sum(1 for e in regime_events_all + trade_events_all if e.get("_hong"))

    regime_events = _on_day(regime_events_all, day)
    trade_events = _on_day(trade_events_all, day)

    labels = [str(e["regime"]) for e in regime_events if e.get("regime") is not None]
    changes = sum(1 for a, b in zip(labels, labels[1:]) if a != b)

    layers: Counter[str] = Counter()
    for e in regime_events:
        tang = limiting_layer(
            _dec(e.get("hmm_allocation")),
            _dec(e.get("trend_gate_cap")),
            _dec(e.get("risk_manager_cap")),
        )
        if tang is not None:
            layers[tang] += 1

    orders = [e for e in trade_events if e.get("event") in ("order_submitted", "order_filled")]
    tu_choi = Counter(
        str(e.get("rejection_reason") or "không ghi lý do")
        for e in trade_events
        if e.get("event") == "signal_rejected"
    )

    cuoi = regime_events[-1] if regime_events else {}

    drift_file = drift_path or (state / "drift.json")
    drift_alerts = _read_drift_alerts(drift_file)
    if drift_alerts is None:
        thieu.append(str(drift_file))

    return DigestData(
        day=day,
        n_bars=len(regime_events),
        regime_counts=dict(Counter(labels)),
        regime_changes=changes,
        n_orders=len(orders),
        rejections=tuple(sorted(tu_choi.items(), key=lambda kv: -kv[1])),
        layer_counts=dict(layers),
        equity=_dec(cuoi.get("equity")),
        daily_pnl=_dec(cuoi.get("daily_pnl")),
        cumulative_fees=_dec(cuoi.get("cumulative_fees_paid")),
        drawdown_pct=_dec(cuoi.get("drawdown_pct")),
        drift_alerts=drift_alerts,
        drift_path=drift_file,
        hmmlearn_warnings=_read_hmmlearn_warnings(day),
        corrupt_log_lines=hong,
        missing_sources=tuple(thieu),
    )


def _read_drift_alerts(path: Path) -> Optional[tuple[str, ...]]:
    """Tên các chỉ số ĐANG bật cảnh báo. `None` = không đọc được file.

    KHÔNG tính lại drift — chỉ đọc cờ `alert` mà `monitoring/drift.py` đã
    quyết định. Phân biệt `None` (không đọc được) với `()` (đọc được, không
    có cảnh báo nào): hai trạng thái đó cần phản ứng khác hẳn nhau.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metrics = payload["metrics"]
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return None
    if not isinstance(metrics, list):
        return None
    return tuple(str(m.get("name")) for m in metrics if isinstance(m, dict) and m.get("alert"))


def _read_hmmlearn_warnings(day: date) -> Optional[int]:
    """`warning_count` của lần retrain trong ngày, đọc từ log forward test.

    CHỈ ĐỌC. `None` khi không có retrain nào trong ngày hoặc không đọc
    được — `0` nghĩa là "có train và không warning nào", khác hẳn.
    """
    try:
        from forward.runner import load_all_bars

        bars = load_all_bars()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được log forward cho digest: %s", exc)
        return None
    if bars is None or bars.empty or "warning_count" not in bars.columns:
        return None

    trong_ngay = bars[bars["date"].astype(str) == day.isoformat()]
    if trong_ngay.empty:
        return None
    import pandas as pd

    gia_tri = pd.to_numeric(trong_ngay["warning_count"], errors="coerce").dropna()
    return int(gia_tri.sum()) if not gia_tri.empty else None


# ----------------------------------------------------------------------
# Kết xuất Markdown — hàm THUẦN
# ----------------------------------------------------------------------


def _fmt(value: Optional[Decimal], hau_to: str = "") -> str:
    return _KHONG_CO_DU_LIEU if value is None else f"{value}{hau_to}"


def _bang(tieu_de: Sequence[str], hang: Sequence[Sequence[str]]) -> list[str]:
    ra = ["| " + " | ".join(tieu_de) + " |", "|" + "---|" * len(tieu_de)]
    ra += ["| " + " | ".join(c) + " |" for c in hang]
    return ra


def render(data: DigestData) -> str:
    """Markdown. THUẦN — không đọc file, không đọc đồng hồ.

    Mọi mục §C.2 LUÔN xuất hiện, kể cả khi rỗng. Một mục biến mất trông
    giống hệt một mục bằng 0.
    """
    d = data
    out: list[str] = [f"# Digest {d.day.isoformat()}", ""]

    if d.missing_sources:
        out += [
            "> **Thiếu nguồn dữ liệu** — các mục liên quan bên dưới ghi "
            "*không có dữ liệu*, không phải 0:",
            "",
        ]
        out += [f"> - `{p}`" for p in d.missing_sources]
        out += [""]
    if d.corrupt_log_lines:
        out += [
            f"> **{d.corrupt_log_lines} dòng log hỏng** đã bị bỏ qua — kiểm tra "
            "tiến trình ghi log.",
            "",
        ]

    out += ["## Hoạt động", ""]
    out += _bang(
        ["Chỉ số", "Giá trị"],
        [
            ["Bar đã xử lý", str(d.n_bars)],
            ["Lệnh đã đặt", str(d.n_orders)],
            ["Lệnh bị risk manager từ chối", str(sum(n for _, n in d.rejections))],
        ],
    )
    out += [""]

    out += ["### Lý do từ chối", ""]
    if d.rejections:
        out += _bang(["Lý do", "Số lần"], [[ly_do, str(n)] for ly_do, n in d.rejections])
    else:
        out += ["Không có lệnh nào bị từ chối." if d.n_bars else _KHONG_CO_DU_LIEU]
    out += [""]

    out += ["## Regime", ""]
    if d.regime_counts:
        out += _bang(
            ["Regime", "Số bar"],
            [[k, str(v)] for k, v in sorted(d.regime_counts.items(), key=lambda kv: -kv[1])],
        )
        out += ["", f"Số lần đổi regime: **{d.regime_changes}**"]
    else:
        out += [_KHONG_CO_DU_LIEU]
    out += [""]

    out += [
        "## Tầng nào giới hạn allocation",
        "",
        "Đếm theo bar. `risk manager` chỉ tính khi nó cắt SÂU HƠN cả HMM lẫn",
        "trend gate — xem `limiting_layer()`.",
        "",
    ]
    if d.layer_counts:
        out += _bang(
            ["Tầng", "Số bar"],
            [[k, str(v)] for k, v in sorted(d.layer_counts.items(), key=lambda kv: -kv[1])],
        )
    else:
        out += [_KHONG_CO_DU_LIEU]
    out += [""]

    out += ["## Tài chính", ""]
    out += _bang(
        ["Chỉ số", "Giá trị"],
        [
            ["Equity", _fmt(d.equity)],
            ["P&L ngày", _fmt(d.daily_pnl)],
            ["Phí luỹ kế", _fmt(d.cumulative_fees)],
            ["Drawdown", _fmt(d.drawdown_pct, " %")],
        ],
    )
    out += [""]

    out += ["## Cảnh báo drift đang bật", ""]
    if d.drift_alerts is None:
        out += [
            f"{_KHONG_CO_DU_LIEU} — `{d.drift_path}` chưa có hoặc không đọc được.",
            "",
            "Đây KHÔNG phải \"không có cảnh báo\": `monitoring/drift.py` có thể "
            "chưa chạy lần nào.",
        ]
    elif d.drift_alerts:
        out += [f"- **{ten}**" for ten in d.drift_alerts]
    else:
        out += ["Không có chỉ số nào vượt ngưỡng."]
    out += [""]

    out += ["## hmmlearn", ""]
    if d.hmmlearn_warnings is None:
        out += ["Không có lần retrain nào trong ngày (hoặc không đọc được log forward)."]
    else:
        out += [f"Số warning khi train: **{d.hmmlearn_warnings}**"]
    out += [""]

    return "\n".join(out)


def summary_line(data: DigestData) -> str:
    """Một dòng cho Telegram. Tin nhắn dài bị cắt trên điện thoại, và phần
    bị cắt luôn là phần cuối — nên thứ quan trọng nhất (cảnh báo drift)
    phải nằm ở đầu."""
    canh_bao = (
        "drift: không đọc được"
        if data.drift_alerts is None
        else (f"drift: {len(data.drift_alerts)} cảnh báo" if data.drift_alerts else "drift: sạch")
    )
    return (
        f"Digest {data.day.isoformat()} — {canh_bao} | {data.n_bars} bar, "
        f"{data.n_orders} lệnh, {sum(n for _, n in data.rejections)} bị từ chối, "
        f"{data.regime_changes} lần đổi regime"
    )


# ----------------------------------------------------------------------
# Chạy
# ----------------------------------------------------------------------


def write_digest(data: DigestData, *, log_dir: str | Path = _DEFAULT_LOG_DIR) -> Path:
    """Ghi NGUYÊN TỬ. KHÔNG raise `OSError` — cùng hợp đồng với
    `health.py::write_health`."""
    target = digest_path(data.day, log_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(render(data), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("Không ghi được %s: %s", target, exc)
    return target


def run(
    day: Optional[date] = None,
    *,
    log_dir: str | Path = _DEFAULT_LOG_DIR,
    alert_manager: Any = None,
    state_dir: Optional[Path] = None,
) -> DigestData:
    """`day=None` -> NGÀY HÔM QUA.

    Digest chạy 00:05 UTC, tức là năm phút SAU khi ngày mới bắt đầu — ngày
    cần tổng kết là ngày vừa kết thúc, không phải ngày đang chạy được năm
    phút. Mặc định `date.today()` ở đây sẽ cho một báo cáo trống mỗi sáng,
    và không có gì đỏ.
    """
    from datetime import timedelta

    muc_tieu = day or (datetime.now(timezone.utc).date() - timedelta(days=1))
    data = collect(muc_tieu, log_dir=log_dir, state_dir=state_dir)
    write_digest(data, log_dir=log_dir)

    if alert_manager is not None:
        from monitoring.alerts import Alert, AlertType

        alert_manager.send(Alert(AlertType.DAILY_DIGEST, summary_line(data), severity="INFO"))

    return data


def main() -> int:
    import main as main_mod

    settings = main_mod.load_settings()
    log_dir = settings.get("monitoring", {}).get("log_dir", _DEFAULT_LOG_DIR)
    data = run(log_dir=log_dir, alert_manager=main_mod.build_alert_manager(settings))
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
