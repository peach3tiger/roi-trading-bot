"""So log shadow với log production trên cùng khoảng thời gian. §B.

## MỘT parser, hai nguồn

Cả `ops/shadow_runner.py` lẫn `main.py` phát cùng định dạng trace
(`monitoring/trace.py::log_layer`), nên file này có đúng **một** hàm đọc
dùng cho cả hai. Đó là toàn bộ lý do §C.4 đòi đồng bộ định dạng: một
parser riêng cho từng nguồn sẽ trôi lệch, và lúc đó "hai bên khớp" chỉ
nghĩa là hai parser đồng ý với nhau.

## Bốn trường là TIÊU CHÍ, hai trường còn lại chỉ GHI NHẬN

Khớp 100%, nếu không thì dừng và điều tra:
`regime_id`, `hmm_allocation`, `trend_gate_cap`, `final_allocation`.

Cộng `instrument_rules` lấy từ sàn — phải khớp. Đây là một trong bốn câu
hỏi duy nhất shadow mode tồn tại để trả lời (backtest chỉ giả định quy
tắc làm tròn).

`clock_skew_ms` và `api_latency_ms` **chỉ ghi nhận, không phải tiêu chí**.
Hai tiến trình gọi mạng ở hai thời điểm khác nhau thì độ trễ khác nhau là
đương nhiên; biến chúng thành tiêu chí sẽ tạo một cổng đỏ ngẫu nhiên, và
một cổng đỏ ngẫu nhiên sẽ bị vô hiệu hoá trong vòng một tuần.

## Bar nào không có ở cả hai bên thì BỎ QUA, và NÓI RA

Shadow khởi động sau production vài phút là chuyện bình thường. Nhưng
"bỏ qua" mà không đếm sẽ biến một shadow chạy 20 phút thành "khớp 100%
trên 0 bar" — báo cáo phải in số bar CHUNG, và `enough_coverage()` là thứ
biến con số đó thành một quyết định.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Bốn trường quyết định — ĐỌC TỪ `ops/compare_versions.py`, không gõ lại.
# Hai chỗ gõ tay cùng một danh sách sẽ lệch nhau, và bên thiếu hơn âm thầm
# quyết định mức bảo vệ thật.
from ops.compare_versions import EXACT_FIELDS  # noqa: E402

# §B: tối thiểu 24 giờ, khuyến nghị 48. Bar 1D nên đơn vị là BAR.
MIN_BARS = 1
MIN_HOURS = 24
RECOMMENDED_HOURS = 48

# Chỉ ghi nhận, KHÔNG phải tiêu chí.
OBSERVED_ONLY = ("clock_skew_ms", "api_latency_ms")


@dataclass(frozen=True)
class BarRecord:
    """Một bar, gộp từ các dòng trace cùng `trace_id`."""

    trace: str
    regime_id: Optional[str] = None
    hmm_allocation: Optional[str] = None
    trend_gate_cap: Optional[str] = None
    final_allocation: Optional[str] = None
    capped_by: Optional[str] = None
    instrument_rules: Optional[dict[str, str]] = None
    clock_skew_ms: Optional[float] = None
    api_latency_ms: Optional[float] = None


@dataclass(frozen=True)
class FieldDiff:
    trace: str
    field_name: str
    shadow: str
    production: str


@dataclass
class DiffReport:
    n_shadow: int = 0
    n_production: int = 0
    n_common: int = 0
    diffs: list[FieldDiff] = field(default_factory=list)
    observed: dict[str, list[tuple[str, Any, Any]]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """`n_common == 0` KHÔNG phải "ok". Không có bar chung nghĩa là
        chưa so được gì — và một cổng chưa so gì mà báo xanh là đúng loại
        cổng rỗng mà `CLAUDE.md` #19 sinh ra để chặn."""
        return self.n_common > 0 and not self.diffs

    def render(self) -> str:
        dong = [
            "=" * 68,
            f"SHADOW DIFF — {'KHỚP' if self.ok else 'LỆCH' if self.diffs else 'CHƯA ĐỦ DỮ LIỆU'}",
            "=" * 68,
            f"bar shadow     : {self.n_shadow}",
            f"bar production : {self.n_production}",
            f"bar CHUNG      : {self.n_common}   <- phép so chỉ có nghĩa trên số này",
            "",
        ]
        if self.n_common == 0:
            dong += [
                "Không có bar nào chung — chưa so được gì.",
                "Shadow khởi động sau production vài phút là bình thường; chờ thêm.",
            ]
            return "\n".join(dong)

        if self.diffs:
            dong += [f"{len(self.diffs)} khác biệt ở trường TIÊU CHÍ:", ""]
            for d in self.diffs[:20]:
                dong += [
                    f"  {d.trace}",
                    f"    {d.field_name:18} shadow={d.shadow!r}  production={d.production!r}",
                ]
            if len(self.diffs) > 20:
                dong += [f"  ... còn {len(self.diffs) - 20} khác biệt nữa"]
            dong += [
                "",
                "DỪNG, ĐIỀU TRA. Đây là khác biệt TẦNG SÀN mà backtest không thấy được",
                "(§A đã xanh trên cùng logic, nên chênh lệch ở đây đến từ dữ liệu/API",
                "thật, không từ công thức).",
            ]
        else:
            dong += [f"Bốn trường tiêu chí + instrument_rules khớp 100% trên {self.n_common} bar."]

        if self.observed:
            dong += ["", "CHỈ GHI NHẬN (không phải tiêu chí):"]
            for ten, muc in self.observed.items():
                if not muc:
                    continue
                lech = [abs(float(a) - float(b)) for _, a, b in muc if a is not None and b is not None]
                if lech:
                    dong.append(
                        f"  {ten:16} n={len(lech)}  chênh trung bình {sum(lech) / len(lech):.1f}"
                        f"  lớn nhất {max(lech):.1f}"
                    )
        return "\n".join(dong)


# ----------------------------------------------------------------------
# Parser DUY NHẤT — dùng cho cả shadow lẫn production
# ----------------------------------------------------------------------


def parse_trace_log(lines: Iterable[str]) -> dict[str, BarRecord]:
    """Gộp các dòng trace theo `trace_id` thành một `BarRecord` mỗi bar.

    Bỏ qua dòng không phải JSON và dòng không có `trace` — cả hai file đều
    có thể lẫn dòng log thường, và một parser chết vì một dòng lạ sẽ biến
    "có khác biệt" thành "công cụ hỏng".
    """
    from monitoring.trace import (
        LAYER_COMPOSE,
        LAYER_HMM,
        LAYER_TREND_GATE,
        NO_TRACE,
    )

    tho: dict[str, dict[str, Any]] = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        tid = d.get("trace")
        if not isinstance(tid, str) or tid == NO_TRACE:
            continue

        muc = tho.setdefault(tid, {})
        tang = d.get("layer")
        if tang == LAYER_HMM:
            muc["hmm_allocation"] = _s(d.get("alloc_out"))
            if d.get("regime_id") is not None:
                muc["regime_id"] = _s(d.get("regime_id"))
        elif tang == LAYER_TREND_GATE:
            muc["trend_gate_cap"] = _s(d.get("cap"))
        elif tang == LAYER_COMPOSE:
            muc["final_allocation"] = _s(d.get("final"))
            muc["capped_by"] = _s(d.get("capped_by"))
            if d.get("instrument_rules") is not None:
                muc["instrument_rules"] = d["instrument_rules"]
            for ten in OBSERVED_ONLY:
                if d.get(ten) is not None:
                    muc[ten] = d[ten]

    return {tid: BarRecord(trace=tid, **v) for tid, v in tho.items()}


def _s(value: Any) -> Optional[str]:
    """Chuỗi hoá KHÔNG qua float — cùng lý do `compare_versions._as_str`:
    phép so phải bit-for-bit trên `Decimal`."""
    return None if value is None else str(value)


def load_log(path: Path) -> dict[str, BarRecord]:
    if not path.exists():
        return {}
    return parse_trace_log(path.read_text(encoding="utf-8").splitlines())


def load_dir(directory: Path, pattern: str = "*.jsonl") -> dict[str, BarRecord]:
    """Gộp nhiều ngày. Shadow chạy 24–48 giờ nên nó luôn nằm trên ≥2 file."""
    gop: dict[str, BarRecord] = {}
    for f in sorted(directory.glob(pattern)) if directory.exists() else []:
        gop.update(load_log(f))
    return gop


# ----------------------------------------------------------------------
# So sánh
# ----------------------------------------------------------------------


def diff(shadow: dict[str, BarRecord], production: dict[str, BarRecord]) -> DiffReport:
    bao_cao = DiffReport(n_shadow=len(shadow), n_production=len(production))
    chung = sorted(set(shadow) & set(production))
    bao_cao.n_common = len(chung)

    bao_cao.observed = {ten: [] for ten in OBSERVED_ONLY}
    for tid in chung:
        s, p = shadow[tid], production[tid]
        for ten in EXACT_FIELDS:
            gs, gp = getattr(s, ten), getattr(p, ten)
            # Thiếu ở MỘT bên là khác biệt thật; thiếu ở CẢ HAI thì tầng đó
            # không được ghi và không nói lên gì.
            if gs is None and gp is None:
                continue
            if gs != gp:
                bao_cao.diffs.append(FieldDiff(tid, ten, _txt(gs), _txt(gp)))
        if s.instrument_rules is not None and p.instrument_rules is not None:
            if s.instrument_rules != p.instrument_rules:
                bao_cao.diffs.append(
                    FieldDiff(tid, "instrument_rules", _txt(s.instrument_rules), _txt(p.instrument_rules))
                )
        for ten in OBSERVED_ONLY:
            bao_cao.observed[ten].append((tid, getattr(s, ten), getattr(p, ten)))
    return bao_cao


def _txt(value: Any) -> str:
    return "<thiếu>" if value is None else str(value)


def enough_coverage(n_common_bars: int, *, bar_hours: int = 24) -> tuple[bool, str]:
    """§B đòi tối thiểu 24 giờ, khuyến nghị 48. Bar 1D -> 1 bar = 24 giờ.

    Trả `(đủ, lời giải thích)` chứ không chỉ `bool`: "chưa đủ" và "đủ
    nhưng dưới mức khuyến nghị" là hai tình huống khác nhau, và người vận
    hành cần biết mình đang ở đâu chứ không chỉ được/không.
    """
    gio = n_common_bars * bar_hours
    if gio < MIN_HOURS:
        return False, f"mới {gio}h chung (< {MIN_HOURS}h tối thiểu) — chờ thêm"
    if gio < RECOMMENDED_HOURS:
        return True, f"{gio}h chung — đạt tối thiểu, dưới mức khuyến nghị {RECOMMENDED_HOURS}h"
    return True, f"{gio}h chung — đạt mức khuyến nghị {RECOMMENDED_HOURS}h"


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ops.shadow_runner import SHADOW_DIR

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-dir", type=Path, default=SHADOW_DIR)
    parser.add_argument(
        "--production-log",
        type=Path,
        default=_REPO_ROOT / "logs" / "regime.log",
        help="log trace của instance production (main.py ghi regime.log)",
    )
    args = parser.parse_args(argv)

    bao_cao = diff(load_dir(args.shadow_dir), load_log(args.production_log))
    print(bao_cao.render())
    du, ly_do = enough_coverage(bao_cao.n_common)
    print(f"\nĐộ phủ: {ly_do}")
    return 0 if (bao_cao.ok and du) else 1


if __name__ == "__main__":
    raise SystemExit(main())
