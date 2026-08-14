"""Mọi khẳng định "sạch" phải kèm PHẠM VI đã kiểm. CLAUDE.md #19.

Một công cụ báo "không có vấn đề" không nói được nó đã nhìn vào đâu. Bốn
chế độ hỏng, cả bốn đều làm một cổng RỖNG trông như một cổng XANH:

1. **`grep` trên đường dẫn không tồn tại** → 0 kết quả, và trong một
   checklist thủ công thì "không có kết quả" là ĐẠT. Đây là chỗ nguy hiểm
   nhất còn lại, vì nó không để lại dấu vết nào: `grep -rn "x" dashboard/`
   trả rỗng vì sạch, hay vì không có thư mục `dashboard/`?
2. **`pytest` với `addopts = "-m 'not slow'"`** → "toàn bộ xanh" chỉ đúng
   với phần đã chọn. 691/697 nghe như tất cả cho tới lúc không phải.
3. **`mypy .` dừng ở lỗi phân giải module đầu tiên** → "Found 1 error"
   thay vì kiểm 84 file. Đã xảy ra trong dự án này, phát hiện 2026-08-14;
   trước đó "mypy sạch" nghĩa là "mypy chưa kiểm được gì".
4. **`cmd | tail` nuốt exit code** (CLAUDE.md #17).

Module này TRẢ LỜI câu hỏi "bao nhiêu", không phải "có sạch không". Nó
không thay thế `ruff`/`mypy`/`pytest` — nó nói ba công cụ đó vừa nhìn vào
đâu, để một con số tụt xuống trở thành thứ đọc được.

`python ops/verify_scope.py` in bảng phạm vi và thoát khác 0 nếu bất kỳ
đường dẫn nghiệm thu nào không tồn tại.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ScopeCheck:
    """Một dòng trong bảng phạm vi."""

    tool: str
    scope: str
    ok: bool
    detail: str = ""


# ----------------------------------------------------------------------
# Mốc so: số file .py mà git THEO DÕI
# ----------------------------------------------------------------------


def tracked_python_files(*, repo_root: Path = _REPO_ROOT) -> tuple[str, ...]:
    """`.py` git theo dõi — mốc so cho ruff và mypy.

    Dùng `git ls-files` chứ không `rglob("*.py")`: `rglob` sẽ đếm cả
    `.venv/`, `__pycache__/`, và mọi file rác chưa commit, nên con số của
    nó không so được với thứ công cụ báo. Git là định nghĩa duy nhất của
    "mã nguồn của dự án này".
    """
    proc = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files thất bại: {proc.stderr.strip()}")
    return tuple(sorted(line for line in proc.stdout.splitlines() if line))


def untracked_python_files(*, repo_root: Path = _REPO_ROOT) -> tuple[str, ...]:
    """`.py` CHƯA `git add` nhưng không bị `.gitignore` loại.

    `ruff`/`mypy` nhìn thấy chúng, git thì chưa. Không tách riêng con số
    này thì "86 file kiểm / 84 file git theo dõi" trông như một sai lệch
    thay vì hai file vừa tạo chưa commit.
    """
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files --others thất bại: {proc.stderr.strip()}")
    return tuple(sorted(line for line in proc.stdout.splitlines() if line))


def visible_python_files(*, repo_root: Path = _REPO_ROOT) -> tuple[str, ...]:
    """Mọi `.py` mà một công cụ chạy `.` NHÌN THẤY được — mốc so đúng cho
    `ruff`/`mypy`."""
    return tuple(
        sorted(
            set(tracked_python_files(repo_root=repo_root))
            | set(untracked_python_files(repo_root=repo_root))
        )
    )


# ----------------------------------------------------------------------
# ruff
# ----------------------------------------------------------------------


def ruff_checked_files(*, repo_root: Path = _REPO_ROOT) -> tuple[str, ...]:
    """File `.py` mà `ruff check .` THỰC SỰ nhìn vào.

    `--show-files` liệt kê cả `pyproject.toml` (file cấu hình, không phải
    mã được kiểm) — lọc ra để con số so được với `git ls-files "*.py"`.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--show-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    ra = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.endswith(".py"):
            continue
        p = Path(line)
        ra.append(str(p.relative_to(repo_root)) if p.is_absolute() else line)
    return tuple(sorted(ra))


def check_ruff(*, repo_root: Path = _REPO_ROOT) -> ScopeCheck:
    tracked = set(tracked_python_files(repo_root=repo_root))
    checked = set(ruff_checked_files(repo_root=repo_root))
    thieu = sorted(tracked - checked)
    # File ruff kiểm nhưng git chưa theo dõi (mới tạo, chưa `git add`) —
    # KHÔNG phải lỗi, nhưng phải nói ra: nếu không, "85/84" trông như một
    # con số sai thay vì một file chưa commit.
    chua_theo_doi = len(checked - tracked)
    return ScopeCheck(
        tool="ruff check .",
        scope=f"{len(checked)} file .py (git theo dõi {len(tracked)})",
        ok=not thieu,
        detail=(
            f"BỎ SÓT {len(thieu)}: {', '.join(thieu[:5])}"
            if thieu
            else (f"+{chua_theo_doi} file chưa `git add`" if chua_theo_doi else "")
        ),
    )


# ----------------------------------------------------------------------
# mypy
# ----------------------------------------------------------------------

# HAI dạng thông điệp, tuỳ có lỗi hay không:
#   "Success: no issues found in 85 source files"
#   "Found 3 errors in 2 files (checked 85 source files)"
# Bản đầu của regex chỉ bắt dạng thứ hai, nên khi mypy SẠCH nó báo "KHÔNG
# BÁO SỐ FILE" — tức là công cụ đo phạm vi tự nó có một điểm mù, đúng loại
# lỗi nó sinh ra để bắt. Bắt được vì chạy thử ngay sau khi viết.
_MYPY_COUNT = re.compile(r"(?:checked|found in) (\d+) source files?")


def mypy_checked_count(*, repo_root: Path = _REPO_ROOT) -> Optional[int]:
    """Số file mypy BÁO đã kiểm. `None` khi nó không báo con số nào —
    tức là nó đã dừng sớm (lỗi phân giải module), đúng chế độ hỏng #3."""
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "."],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    m = _MYPY_COUNT.search(proc.stdout)
    return int(m.group(1)) if m else None


def check_mypy(*, repo_root: Path = _REPO_ROOT) -> ScopeCheck:
    visible = visible_python_files(repo_root=repo_root)
    n = mypy_checked_count(repo_root=repo_root)
    if n is None:
        return ScopeCheck(
            tool="mypy .",
            scope="KHÔNG BÁO SỐ FILE",
            ok=False,
            detail=(
                "mypy không in 'checked N source files' — nó đã DỪNG SỚM. "
                "'Found 1 error' ở đây KHÔNG phải 'gần sạch', mà là 'chưa kiểm gì'."
            ),
        )
    return ScopeCheck(
        tool="mypy .",
        scope=f"{n} file .py (thấy được {len(visible)})",
        # Ngưỡng là SỐ FILE GIT THEO DÕI, không phải hằng số 84. Một con số
        # ghim cứng sẽ đỏ mỗi lần thêm file mới (nhiễu) và vẫn xanh nếu
        # repo teo lại đúng bằng số đó (điểm mù). So với git thì cả hai
        # chiều đều đúng và không phải bảo trì.
        # HAI chiều. `>=` một mình không đủ: một hàm đọc số hỏng trả
        # 99999 cũng thoả, và lúc đó công cụ đo phạm vi báo một con số bịa
        # ra với vẻ đầy thẩm quyền. Đo được bằng đột biến, và đã đo.
        ok=n == len(visible),
        detail=(
            ""
            if n == len(visible)
            else (
                f"TỤT {len(visible) - n} file so với thực tế"
                if n < len(visible)
                else f"BÁO THỪA {n - len(visible)} file — con số không đáng tin"
            )
        ),
    )


# ----------------------------------------------------------------------
# pytest
# ----------------------------------------------------------------------

_PYTEST_COUNT = re.compile(r"(\d+)/(\d+) tests collected|(\d+) tests? collected")


def pytest_collected(
    marker_filter: Optional[str] = None, *, repo_root: Path = _REPO_ROOT
) -> Optional[int]:
    """Số test `pytest --collect-only` thu được.

    `marker_filter=""` VÔ HIỆU HOÁ `addopts = "-m 'not slow'"` của
    `pyproject.toml` — đó là cách duy nhất biết TỔNG số test thật.
    """
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if marker_filter is not None:
        cmd += ["-m", marker_filter]
    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    m = _PYTEST_COUNT.search(proc.stdout)
    if not m:
        return None
    return int(m.group(1) or m.group(3))


def check_pytest(*, repo_root: Path = _REPO_ROOT) -> ScopeCheck:
    mac_dinh = pytest_collected(repo_root=repo_root)
    tong = pytest_collected("", repo_root=repo_root)
    if mac_dinh is None or tong is None:
        return ScopeCheck("pytest", "KHÔNG ĐỌC ĐƯỢC", False, "không parse được dòng collected")
    return ScopeCheck(
        tool="pytest (mặc định)",
        scope=f"{mac_dinh}/{tong} test",
        # KHÔNG phải lỗi — đây là thiết kế có chủ ý. Nhưng phải NHÌN THẤY:
        # "toàn bộ xanh" từ lệnh mặc định chỉ đúng với phần đã chọn.
        ok=True,
        detail=(
            ""
            if mac_dinh == tong
            else f"{tong - mac_dinh} test `slow` bị loại — cần `pytest -m slow` riêng"
        ),
    )


# ----------------------------------------------------------------------
# Đường dẫn trong các mục nghiệm thu
# ----------------------------------------------------------------------

# Mọi đường dẫn xuất hiện trong một mục nghiệm thu dạng `grep`. Một mục
# nghiệm thu "không có kết quả" trên đường dẫn KHÔNG TỒN TẠI là mục nghiệm
# thu rỗng — nó ĐẠT mà không kiểm gì.
#
# Ghi cả những đường dẫn CHƯA XÂY (phase sau) kèm cờ `expected_missing`:
# chúng vẫn phải xuất hiện trong bảng, dán nhãn "chưa xây", thay vì im
# lặng vắng mặt.
ACCEPTANCE_PATHS: tuple[tuple[str, str, bool], ...] = (
    # (đường dẫn, mục nghiệm thu, có được phép chưa tồn tại không)
    ("monitoring/", 'grep -rn "print(" monitoring/ core/ broker/', False),
    ("core/", 'grep -rn "print(" monitoring/ core/ broker/', False),
    ("broker/", 'grep -rn "print(" monitoring/ core/ broker/', False),
    ("data/", 'grep -rn "center=True" data/', False),
    ("backtest/", 'grep -rn "252" core/ data/ backtest/', False),
    ("core/risk_manager.py", 'grep -n "import" core/risk_manager.py', False),
    ("core/signal_generator.py", 'grep -rn "max(" core/signal_generator.py', False),
    ("core/regime_strategies.py", 'grep -rn "leverage" core/regime_strategies.py', False),
    ("core/hmm_engine.py", 'grep -rn "\\.predict(|\\.decode(" core/', False),
    ("tests/regression_harness.py", 'grep -rn "forward/" monitoring/ tests/regression_harness.py', False),
    # `forward/` ở mục trên là CHUỖI TÌM KIẾM chứ không phải đối số đường
    # dẫn — nhưng nó vẫn là một đường dẫn thật, và nếu thư mục biến mất thì
    # mục nghiệm thu "chỉ có thao tác đọc" trở nên vô nghĩa. Canh luôn.
    ("forward/", 'grep -rn "forward/" monitoring/ tests/regression_harness.py', False),
    # Phase 12c — CHƯA XÂY. Mục nghiệm thu của nó
    # (`grep -rn "order_executor|submit_order" ops/shadow_runner.py`) hiện
    # ĐẠT một cách RỖNG: grep trên file không tồn tại trả 0 kết quả.
    ("ops/shadow_runner.py", 'grep -rn "order_executor|submit_order" ops/shadow_runner.py', True),
)


def check_paths(*, repo_root: Path = _REPO_ROOT) -> list[ScopeCheck]:
    ra: list[ScopeCheck] = []
    for duong_dan, muc, duoc_phep_thieu in ACCEPTANCE_PATHS:
        ton_tai = (repo_root / duong_dan).exists()
        if ton_tai:
            ra.append(ScopeCheck(f"path {duong_dan}", "tồn tại", True))
        elif duoc_phep_thieu:
            ra.append(
                ScopeCheck(
                    f"path {duong_dan}",
                    "CHƯA XÂY",
                    True,
                    f"mục nghiệm thu `{muc}` hiện ĐẠT một cách RỖNG",
                )
            )
        else:
            ra.append(
                ScopeCheck(
                    f"path {duong_dan}",
                    "KHÔNG TỒN TẠI",
                    False,
                    f"`{muc}` trả rỗng vì không có gì để tìm, không phải vì sạch",
                )
            )
    return ra


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def run_all(*, repo_root: Path = _REPO_ROOT) -> list[ScopeCheck]:
    return [
        check_ruff(repo_root=repo_root),
        check_mypy(repo_root=repo_root),
        check_pytest(repo_root=repo_root),
        *check_paths(repo_root=repo_root),
    ]


def format_report(checks: Sequence[ScopeCheck]) -> str:
    rong_tool = max(len(c.tool) for c in checks)
    rong_scope = max(len(c.scope) for c in checks)
    dong = ["PHẠM VI ĐÃ KIỂM (CLAUDE.md #19)", ""]
    for c in checks:
        dau = "  " if c.ok else "!!"
        dong.append(f"{dau} {c.tool:<{rong_tool}}  {c.scope:<{rong_scope}}  {c.detail}".rstrip())
    hong = [c for c in checks if not c.ok]
    dong += ["", f"{len(checks) - len(hong)}/{len(checks)} mục có phạm vi hợp lệ."]
    if hong:
        dong.append("MỘT SỐ MỤC KHÔNG KIỂM ĐƯỢC GÌ — xem dòng đánh dấu !!")
    return "\n".join(dong)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    checks = run_all()
    print(format_report(checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
