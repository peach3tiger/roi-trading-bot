"""Cổng §E — `pytest -m slow` là BẮT BUỘC khi diff chạm tầng quyết định.

## Vì sao cần cổng, khi docstring đã giải thích rồi

`tests/test_snapshot.py` đã ghi rõ, kèm bảng đo: smoke test (~8s) KHÔNG
bắt được đột biến `_EMA_PERIOD` 50 -> 40 trên đường allocation, trong khi
`tests/regression_harness.py` (~137s, `-m slow`) bắt ngay. Đo trên hai cửa
sổ khác nhau, cùng kết quả.

Docstring đó giữ nguyên và vẫn là chỗ giải thích TẠI SAO. Nhưng một lời
giải thích chỉ có tác dụng với người đã đọc nó, vào đúng lúc cần. Cổng này
lo phần BẮT BUỘC: nó không thuyết phục ai, nó chặn.

`pyproject.toml` đặt `addopts = "-m 'not slow'"`, nghĩa là `pytest` trần
KHÔNG còn là "chạy tất cả". Đó là đánh đổi có chủ ý (vòng lặp phát triển
nhanh), và cái giá của nó là: quên `-m slow` không hề báo lỗi — bộ test
vẫn xanh, vẫn in "553 passed". Cổng này là thứ trả cái giá đó.

## Phạm vi: `^(core|backtest)/`, RỘNG HƠN danh sách bốn file

Quy tắc gốc nêu tên bốn file (`regime_strategies.py`, `trend_gate.py`,
`signal_generator.py`, `hmm_engine.py`) và thư mục `backtest/`, còn phép
kiểm đi kèm là `grep -E '^(core|backtest)/'` — rộng hơn, vì nó cũng bắt
`core/risk_manager.py` và mọi file `core/` sinh sau này.

Lấy bản RỘNG HƠN, có chủ ý. Một danh sách tên file phải được cập nhật tay
mỗi lần thêm module vào `core/`, và lần quên đầu tiên sẽ im lặng — đúng
kiểu hỏng mà cổng này sinh ra để chặn. `risk_manager` cũng là một tầng
trong `min(hmm, trend_gate, risk)`, nên nó thuộc về phạm vi này chứ không
phải ngoại lệ.

## Bằng chứng "đã chạy slow" = BĂM NỘI DUNG, không phải commit SHA

Biên lai (`.slow_receipt.json`) ghi SHA256 của toàn bộ `core/**/*.py` +
`backtest/**/*.py` tại thời điểm chạy slow. Cổng băm lại và so.

Vì sao không dùng commit SHA: chạy slow xong rồi sửa tiếp `core/` mà chưa
commit sẽ cho một biên lai "khớp HEAD" nhưng vô giá trị. Băm nội dung
không quan tâm tới commit, chỉ quan tâm tới câu hỏi đúng — *mã đang được
gác đã đổi kể từ lần chạy slow chưa?*
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Tiền tố đường dẫn (so với gốc repo) thuộc phạm vi cổng. Xem docstring
# module về việc vì sao rộng hơn danh sách bốn file trong quy tắc gốc.
GATED_PREFIXES: tuple[str, ...] = ("core/", "backtest/")

RECEIPT_PATH = _REPO_ROOT / ".slow_receipt.json"

# Phiên bản schema biên lai. Đổi cấu trúc mà giữ nguyên số này sẽ làm cổng
# đọc một biên lai cũ theo cách mới và kết luận sai — an toàn hơn là coi
# mọi biên lai khác phiên bản là KHÔNG có biên lai.
RECEIPT_VERSION = 1


@dataclass(frozen=True)
class GateResult:
    ok: bool
    slow_required: bool
    changed: tuple[str, ...]
    detail: str

    def report(self) -> str:
        lines = [f"[{'PASS' if self.ok else 'FAIL'}] Cổng §E — test chậm", self.detail]
        if self.changed:
            lines.append("")
            lines.append(f"File trong phạm vi cổng ({len(self.changed)}):")
            lines.extend(f"  - {p}" for p in self.changed)
        return "\n".join(lines)


def changed_files(base: str, *, repo_root: Path = _REPO_ROOT) -> tuple[str, ...]:
    """`git diff --name-only <base>..HEAD`.

    `..` (two-dot) chứ không phải `...`: ta hỏi "cây làm việc hiện tại khác
    `base` ở đâu", không phải "nhánh này thêm gì kể từ điểm rẽ". Với một
    nhánh đã rebase lên base mới, hai câu hỏi đó cho kết quả khác nhau và
    câu đầu mới là câu đúng cho một cổng chất lượng.
    """
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff thất bại ({base}): {proc.stderr.strip()}")
    return tuple(line for line in proc.stdout.splitlines() if line)


def slow_required(paths: Iterable[str]) -> tuple[str, ...]:
    """Các file trong `paths` thuộc phạm vi cổng. Rỗng = không bắt buộc.

    Trả về DANH SÁCH chứ không phải `bool`: khi cổng FAIL, thứ người đọc
    cần đầu tiên là "file nào", không phải "có hay không".
    """
    return tuple(sorted(p for p in paths if p.startswith(GATED_PREFIXES)))


def gated_source_digest(*, repo_root: Path = _REPO_ROOT) -> str:
    """SHA256 của mọi `.py` dưới `core/` và `backtest/`, theo thứ tự đường dẫn.

    Băm cả ĐƯỜNG DẪN lẫn nội dung: xoá một file và thêm một file khác có
    cùng nội dung là một thay đổi thật, và một phép băm chỉ gộp nội dung
    sẽ không thấy nó.
    """
    h = hashlib.sha256()
    for prefix in GATED_PREFIXES:
        base = repo_root / prefix
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            h.update(str(path.relative_to(repo_root)).encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def write_receipt(
    *, repo_root: Path = _REPO_ROOT, path: Optional[Path] = None, slow_tests: int
) -> Path:
    """Ghi biên lai sau một lần `pytest -m slow` XANH HOÀN TOÀN.

    Gọi từ `tests/conftest.py::pytest_sessionfinish`. Không gọi tay —
    một biên lai viết tay là một lời khai không có ai kiểm chứng.
    """
    target = path or (repo_root / RECEIPT_PATH.name)
    target.write_text(
        json.dumps(
            {
                "version": RECEIPT_VERSION,
                "gated_digest": gated_source_digest(repo_root=repo_root),
                "ran_at_utc": datetime.now(timezone.utc).isoformat(),
                "slow_tests_passed": slow_tests,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def read_receipt(path: Path) -> Optional[dict]:
    """`None` khi không có/không đọc được/sai phiên bản — mọi trường hợp
    "không chắc" đều quy về "coi như chưa chạy". Một cổng nghi ngờ phải
    nghiêng về phía chặn."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != RECEIPT_VERSION:
        return None
    return data


def check(
    base: str, *, repo_root: Path = _REPO_ROOT, receipt_path: Optional[Path] = None
) -> GateResult:
    receipt_file = receipt_path or (repo_root / RECEIPT_PATH.name)
    changed = slow_required(changed_files(base, repo_root=repo_root))

    if not changed:
        return GateResult(
            ok=True,
            slow_required=False,
            changed=(),
            detail=f"Diff {base}..HEAD không chạm {'/'.join(GATED_PREFIXES)} — không bắt buộc chạy slow.",
        )

    receipt = read_receipt(receipt_file)
    if receipt is None:
        return GateResult(
            ok=False,
            slow_required=True,
            changed=changed,
            detail=(
                f"Diff chạm tầng quyết định nhưng KHÔNG có biên lai chạy slow ({receipt_file.name}).\n"
                "Chạy: pytest -m slow"
            ),
        )

    hien_tai = gated_source_digest(repo_root=repo_root)
    if receipt["gated_digest"] != hien_tai:
        return GateResult(
            ok=False,
            slow_required=True,
            changed=changed,
            detail=(
                f"Biên lai slow có nhưng ĐÃ CŨ: `core/`+`backtest/` đã đổi kể từ lần chạy "
                f"lúc {receipt['ran_at_utc']}.\n"
                "Chạy lại: pytest -m slow"
            ),
        )

    return GateResult(
        ok=True,
        slow_required=True,
        changed=changed,
        detail=(
            f"Diff chạm tầng quyết định, và slow đã chạy lúc {receipt['ran_at_utc']} "
            f"({receipt['slow_tests_passed']} test) trên đúng mã hiện tại."
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Mốc so sánh cho `git diff --name-only <base>..HEAD` (mặc định origin/main).",
    )
    args = parser.parse_args(argv)

    result = check(args.base)
    print(result.report())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
