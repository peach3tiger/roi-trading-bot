"""So sánh ngoại tuyến hai git ref trên cùng dữ liệu. Phase 12c §A.

**Đây là cổng mạnh nhất và rẻ nhất.** Chạy trước mọi thứ khác: hàng nghìn
bar thay vì vài chục, tái lập được, mất vài chục giây. Shadow mode (§B)
KHÔNG dùng để kiểm logic — nó chỉ trả lời những câu backtest không trả lời
được (phản hồi API thật, lệch đồng hồ, `instrumentRules` thật).

## KHÔNG có ngưỡng dung sai cho bốn trường quyết định

`regime_id`, `hmm_allocation`, `trend_gate_cap`, `final_allocation` phải
khớp **100%**. Nếu một refactor được cho là thuần tuý mà output lệch, thì
**giả định sai** — điều tra, đừng nới ngưỡng. `equity` có dung sai 1e-9,
và đó là dung sai của phép cộng dồn `Decimal`, không phải của logic.

## Vì sao chạy ref qua `git worktree`, không phải `git stash`

`worktree` checkout song song vào một thư mục riêng: cây làm việc hiện tại
**không bị đụng tới**. `stash`/`checkout` sẽ sửa file ngay dưới chân người
đang chạy lệnh — và nếu tiến trình chết giữa chừng, họ mất trạng thái.
Cùng bài học với CLAUDE.md #16 (commit hoặc stash TRƯỚC khi chạy đột
biến): thao tác sửa cây làm việc phải luôn có đường lùi.

## Dữ liệu đến từ FIXTURE, đường dẫn TUYỆT ĐỐI

Script chạy trong worktree được sinh ra tại thời điểm chạy (không lấy từ
ref), nên nó đọc `tests/fixtures/...` của cây HIỆN TẠI bằng đường dẫn
tuyệt đối. Hai hệ quả:

1. So sánh không gọi mạng, kể cả khi ref cũ chưa có fixture.
2. Hai ref chắc chắn nhận **cùng một byte dữ liệu** — nếu để mỗi ref tự
   tải, một khác biệt dữ liệu sẽ bị đọc nhầm thành khác biệt logic.

## Ghi CSV chứ không parquet

`regime_history` chứa `Decimal`. `str(Decimal)` là biểu diễn CHÍNH XÁC;
parquet sẽ ép về float hoặc từ chối cột `object`. Toàn bộ điểm của phép so
này là bit-for-bit, nên không được đi qua `float` ở bất kỳ đâu.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Bốn trường QUYẾT ĐỊNH — khớp 100%, không dung sai.
EXACT_FIELDS = ("regime_id", "hmm_allocation", "trend_gate_cap", "final_allocation")
# Tên cột thật trong `regime_history` của backtester.
_COLUMN_MAP = {
    "regime_id": "regime_id",
    "hmm_allocation": "strategy_target_allocation_pct",
    "trend_gate_cap": "trend_gate_cap",
    "final_allocation": "final_allocation_pct",
}
EQUITY_TOLERANCE = Decimal("1e-9")
# Số bar in quanh chỗ lệch đầu tiên. Một dòng lệch không có bối cảnh
# thường không đủ để biết lệch bắt đầu từ đâu — regime chuyển ở bar N-3
# có thể là nguyên nhân của allocation lệch ở bar N.
CONTEXT_BARS = 10

DEFAULT_START = date(2018, 2, 9)
DEFAULT_END = date(2026, 8, 4)

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "btcusdt_1d_2018_2026.parquet"

# Script chạy BÊN TRONG worktree. Sinh lúc chạy, không lấy từ ref — nên nó
# luôn biết cách đọc fixture của cây hiện tại dù ref cũ chưa có khái niệm
# fixture.
_RUNNER = '''
import sys
sys.path.insert(0, {worktree!r})
import pandas as pd
import main as main_mod
from backtest.backtester import WalkForwardBacktester

settings = main_mod.load_settings()
wf = main_mod.build_walk_forward_config(settings)
bars = pd.read_parquet({fixture!r})
# `tz="UTC"` BẮT BUỘC: index của fixture là datetime64[ns, UTC], và
# pandas TỪ CHỐI so tz-naive với tz-aware (TypeError, không phải kết quả
# sai âm thầm — may). Đã gặp ở lần chạy đầu.
_end = pd.Timestamp({end!r}, tz="UTC")
_start = pd.Timestamp({start!r}, tz="UTC")
bars = bars.loc[bars.index <= _end]

backtester = WalkForwardBacktester(
    hmm_engine=main_mod.build_hmm_engine(settings, min_train_bars=wf.is_bars),
    strategy_orchestrator=main_mod.build_orchestrator(settings),
    trend_gate=main_mod.build_trend_gate(settings, enabled=True),
    cost_model=main_mod.build_cost_model(settings),
    config=wf,
    feature_config=main_mod.build_feature_config(settings),
)
result = backtester.run({symbol!r}, bars, _start, _end)
result.regime_history.to_csv({out!r} + "/regime.csv")
result.equity_curve.to_csv({out!r} + "/equity.csv")
'''


@dataclass(frozen=True)
class Divergence:
    """MỘT bar lệch, ở MỘT trường."""

    bar: str
    field_name: str
    value_a: str
    value_b: str


@dataclass(frozen=True)
class ComparisonReport:
    ref_a: str
    ref_b: str
    n_bars_a: int
    n_bars_b: int
    divergences: tuple[Divergence, ...] = ()
    context: tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.divergences

    def render(self) -> str:
        dau = "KHỚP 100%" if self.ok else "LỆCH"
        dong = [
            "=" * 70,
            f"SO SÁNH NGOẠI TUYẾN — {dau}",
            "=" * 70,
            f"ref A: {self.ref_a}   ({self.n_bars_a} bar)",
            f"ref B: {self.ref_b}   ({self.n_bars_b} bar)",
            "",
        ]
        if self.error:
            dong += [f"LỖI: {self.error}"]
            return "\n".join(dong)

        if self.ok:
            dong += [
                f"Bốn trường quyết định khớp 100% trên {self.n_bars_a} bar:",
                "  " + ", ".join(EXACT_FIELDS),
                f"equity chênh <= {EQUITY_TOLERANCE}",
            ]
            return "\n".join(dong)

        dau_tien = self.divergences[0]
        dong += [
            f"{len(self.divergences)} khác biệt. BAR ĐẦU TIÊN LỆCH:",
            "",
            f"  bar     : {dau_tien.bar}",
            f"  trường  : {dau_tien.field_name}",
            f"  ref A   : {dau_tien.value_a}",
            f"  ref B   : {dau_tien.value_b}",
            "",
        ]
        if self.context:
            dong += [f"Bối cảnh {CONTEXT_BARS} bar quanh đó:", ""]
            dong += [f"  {d}" for d in self.context]
            dong += [""]
        dong += [
            "KHÔNG nới ngưỡng. Nếu thay đổi được cho là thuần tuý mà output lệch",
            "thì GIẢ ĐỊNH SAI — điều tra. Nếu lệch là CỐ Ý, ghi docs/DECISIONS.md",
            "kèm bảng hiệu năng cũ/mới rồi mới cập nhật baseline.",
        ]
        return "\n".join(dong)


# ----------------------------------------------------------------------
# Chạy backtest ở một ref
# ----------------------------------------------------------------------


def run_at_ref(
    ref: str,
    *,
    start: date,
    end: date,
    out_dir: Path,
    repo_root: Path = _REPO_ROOT,
    fixture: Path = _FIXTURE,
    symbol: str = "BTCUSDT",
    timeout: float = 900.0,
) -> Path:
    """Checkout `ref` vào một worktree tạm, chạy backtest, trả thư mục kết quả.

    Worktree được gỡ trong `finally` — kể cả khi backtest ném lỗi. Một
    worktree bỏ quên làm `git worktree list` bẩn dần và `git gc` không dọn
    được commit mà nó giữ.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    wt = Path(tempfile.mkdtemp(prefix="cmpver-"))
    # `git worktree add` đòi thư mục CHƯA tồn tại.
    wt_path = wt / "tree"
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), ref],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        script = _RUNNER.format(
            worktree=str(wt_path),
            fixture=str(fixture),
            start=start.isoformat(),
            end=end.isoformat(),
            symbol=symbol,
            out=str(out_dir),
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=wt_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"backtest ở ref {ref} thất bại:\n{proc.stderr[-3000:]}")
        return out_dir
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=repo_root,
            capture_output=True,
        )
        shutil.rmtree(wt, ignore_errors=True)


# ----------------------------------------------------------------------
# So sánh — THUẦN trên hai DataFrame
# ----------------------------------------------------------------------


def _as_str(value: object) -> str:
    """Chuỗi hoá KHÔNG qua float. `str(Decimal)` là biểu diễn chính xác;
    toàn bộ điểm của phép so này là bit-for-bit."""
    return "" if value is None else str(value)


def compare_frames(
    regime_a: pd.DataFrame,
    regime_b: pd.DataFrame,
    equity_a: pd.DataFrame,
    equity_b: pd.DataFrame,
) -> tuple[list[Divergence], list[str]]:
    """Trả `(khác biệt, bối cảnh)`. Dừng thu bối cảnh sau chỗ lệch ĐẦU TIÊN.

    Thu HẾT khác biệt (không return sớm) vì "lệch một bar" và "lệch từ bar
    đó trở đi" là hai chẩn đoán khác nhau, và số lượng nói ngay điều đó.
    """
    ra, rb = regime_a, regime_b
    ea, eb = equity_a, equity_b

    khac: list[Divergence] = []
    n = min(len(ra), len(rb))
    if len(ra) != len(rb):
        khac.append(
            Divergence(
                bar="(số bar)",
                field_name="n_bars",
                value_a=str(len(ra)),
                value_b=str(len(rb)),
            )
        )

    idx_dau_tien: Optional[int] = None
    for i in range(n):
        bar = _as_str(ra.index[i] if ra.index.name else ra.iloc[i, 0])
        for ten, cot in _COLUMN_MAP.items():
            if cot not in ra.columns or cot not in rb.columns:
                continue
            va, vb = _as_str(ra[cot].iloc[i]), _as_str(rb[cot].iloc[i])
            if va != vb:
                khac.append(Divergence(bar=bar, field_name=ten, value_a=va, value_b=vb))
                if idx_dau_tien is None:
                    idx_dau_tien = i

    m = min(len(ea), len(eb))
    for i in range(m):
        if "equity" not in ea.columns or "equity" not in eb.columns:
            break
        eq_a = Decimal(_as_str(ea["equity"].iloc[i]))
        eq_b = Decimal(_as_str(eb["equity"].iloc[i]))
        if abs(eq_a - eq_b) > EQUITY_TOLERANCE:
            bar = _as_str(ea.index[i] if ea.index.name else ea.iloc[i, 0])
            khac.append(
                Divergence(bar=bar, field_name="equity", value_a=str(eq_a), value_b=str(eq_b))
            )
            if idx_dau_tien is None:
                idx_dau_tien = i

    boi_canh: list[str] = []
    if idx_dau_tien is not None:
        lo = max(0, idx_dau_tien - CONTEXT_BARS // 2)
        hi = min(n, idx_dau_tien + CONTEXT_BARS // 2 + 1)
        boi_canh.append(f"{'bar':26} {'trường':18} {'ref A':>18} {'ref B':>18}")
        for i in range(lo, hi):
            bar = _as_str(ra.index[i] if ra.index.name else ra.iloc[i, 0])
            danh_dau = "->" if i == idx_dau_tien else "  "
            for ten, cot in _COLUMN_MAP.items():
                if cot not in ra.columns or cot not in rb.columns:
                    continue
                va, vb = _as_str(ra[cot].iloc[i]), _as_str(rb[cot].iloc[i])
                lech = "*" if va != vb else " "
                boi_canh.append(f"{danh_dau}{lech}{bar:24} {ten:18} {va:>18} {vb:>18}")
    return khac, boi_canh


# ----------------------------------------------------------------------
# Đầu-cuối
# ----------------------------------------------------------------------


@dataclass
class _RefResult:
    regime: pd.DataFrame
    equity: pd.DataFrame
    n_bars: int = field(default=0)


def _load(out_dir: Path) -> _RefResult:
    regime = pd.read_csv(out_dir / "regime.csv")
    equity = pd.read_csv(out_dir / "equity.csv")
    return _RefResult(regime=regime, equity=equity, n_bars=len(regime))


def compare_versions(
    ref_a: str,
    ref_b: str,
    start: date = DEFAULT_START,
    end: date = DEFAULT_END,
    *,
    runner: Optional[Callable[..., Path]] = None,
    repo_root: Path = _REPO_ROOT,
) -> ComparisonReport:
    """Chạy backtest ở hai ref trên CÙNG dữ liệu, so từng bar.

    `runner` tiêm được để test kiểm logic so sánh mà không phải chạy hai
    backtest thật (mỗi lần vài chục giây) — nhưng có một test đầu-cuối
    THẬT dùng git worktree thật, vì một cổng chỉ được kiểm qua mock là một
    cổng chưa bao giờ mở worktree nào.
    """
    chay = runner or run_at_ref
    tmp = Path(tempfile.mkdtemp(prefix="cmpver-out-"))
    try:
        a = _load(chay(ref_a, start=start, end=end, out_dir=tmp / "a", repo_root=repo_root))
        b = _load(chay(ref_b, start=start, end=end, out_dir=tmp / "b", repo_root=repo_root))
    except Exception as exc:  # noqa: BLE001
        return ComparisonReport(ref_a=ref_a, ref_b=ref_b, n_bars_a=0, n_bars_b=0, error=str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    khac, boi_canh = compare_frames(a.regime, b.regime, a.equity, b.equity)
    return ComparisonReport(
        ref_a=ref_a,
        ref_b=ref_b,
        n_bars_a=a.n_bars,
        n_bars_b=b.n_bars,
        divergences=tuple(khac),
        context=tuple(boi_canh),
    )


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref-a", required=True)
    parser.add_argument("--ref-b", required=True)
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=DEFAULT_END)
    args = parser.parse_args(argv)

    bao_cao = compare_versions(args.ref_a, args.ref_b, args.start, args.end)
    print(bao_cao.render())
    return 0 if bao_cao.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
