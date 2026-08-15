"""Tất định NỘI MÁY và dấu vân tay môi trường số học.

## Câu hỏi công cụ này trả lời

`tests/regression_harness.py` đỏ trên Ubuntu runner trong khi xanh trên
macOS, với `max_drawdown_pct` giống nhau tới 9 chữ số nhưng đường equity
lệch từ một bar ở giữa. Trước khi đổ cho khác máy, phải loại khả năng
**cùng máy chạy hai lần đã khác nhau** — nếu vậy vấn đề nặng hơn nhiều và
mọi so sánh liên máy đều vô nghĩa.

Nên công cụ này làm hai việc, theo đúng thứ tự đó:

1. `--runs N` — chạy backtest ghim N lần TRONG CÙNG một tiến trình, băm
   đường equity bằng `repr()` của từng float (không phải `round()`, không
   phải so sai số) và so bit-for-bit. Khác nhau ở đây = bất định nội máy.
2. In dấu vân tay số học: BLAS nào, mấy thread, kiến trúc CPU, phiên bản
   numpy/scipy/hmmlearn. Đây là thứ phải kèm theo MỌI khẳng định "tất
   định" (CLAUDE.md #19) — một con hash không nói được nó sinh ra ở đâu.

## Vì sao băm `repr()` chứ không so `np.allclose`

Ngưỡng 0.001 của harness là ngưỡng cho câu hỏi "chiến lược có trôi
không". Câu hỏi ở đây khác hẳn: "hai lần chạy có CHO RA ĐÚNG cùng những
bit không". Một sai khác ở chữ số thứ 15 vẫn là bất định, và nó là thứ EM
khuếch đại thành model khác qua vài chục vòng lặp.

## Biến môi trường thread

Số thread BLAS đổi thứ tự cộng dồn trong `matmul`, và cộng dồn số thực
KHÔNG kết hợp được. Đặt `OMP_NUM_THREADS=1` và các biến anh em TRƯỚC khi
numpy được import là cách rẻ nhất loại nguồn bất định phổ biến nhất —
nhưng phải đặt ở tầng tiến trình (shell hoặc `env:` của CI), không đặt
được từ trong Python sau khi numpy đã nạp.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Các biến thread phải đọc TRƯỚC khi in, và phải đặt TRƯỚC khi numpy nạp.
_BIEN_THREAD = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

_COT_EQUITY = ("equity", "allocation_pct", "qty", "cash")


def dau_van_tay() -> dict[str, str]:
    """Mọi thứ có thể làm hai máy cho hai kết quả khác nhau.

    Gom vào một hàm để CI và local in ra CÙNG một bộ trường — so hai bản
    ghi có trường khác nhau là công việc thủ công dễ sai.
    """
    import numpy as np

    ra: dict[str, str] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "(trống)",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }
    for ten, mod in (("scipy", "scipy"), ("sklearn", "sklearn"), ("hmmlearn", "hmmlearn")):
        try:
            ra[ten] = __import__(mod).__version__
        except Exception as exc:  # noqa: BLE001 — thiếu module cũng là dữ liệu
            ra[ten] = f"(không đọc được: {type(exc).__name__})"

    for b in _BIEN_THREAD:
        ra[b] = os.environ.get(b, "(chưa đặt)")

    # `show_config` in ra stdout thay vì trả chuỗi ở phần lớn phiên bản —
    # lấy dạng dict nếu có, vì đó là thứ so sánh được giữa hai máy.
    try:
        # numpy >= 2 trả dict; các bản cũ in ra stdout rồi trả `None` —
        # nên phải kiểm kiểu, không tin chữ ký hàm.
        cfg: Any = np.show_config(mode="dicts")  # type: ignore[call-arg,func-returns-value]
        blas = (cfg or {}).get("Build Dependencies", {}).get("blas", {})
        ra["blas_name"] = str(blas.get("name", "(không rõ)"))
        ra["blas_version"] = str(blas.get("version", "(không rõ)"))
    except Exception:  # noqa: BLE001 — numpy cũ không có mode="dicts"
        ra["blas_name"] = "(numpy quá cũ cho show_config(mode='dicts'))"
        ra["blas_version"] = "-"

    try:
        from threadpoolctl import threadpool_info

        ra["threadpool"] = "; ".join(
            f"{i.get('internal_api')}={i.get('num_threads')}" for i in threadpool_info()
        )
    except Exception:  # noqa: BLE001 — threadpoolctl là tuỳ chọn
        ra["threadpool"] = "(không có threadpoolctl)"

    return ra


def bam_equity(equity_curve: Any) -> str:
    """SHA256 của `repr()` từng float — KHÔNG làm tròn.

    `repr()` của float Python là biểu diễn ngắn nhất khứ hồi đúng, nên hai
    giá trị cho cùng chuỗi khi và chỉ khi chúng cùng bit.
    """
    h = hashlib.sha256()
    for cot in _COT_EQUITY:
        if cot not in equity_curve.columns:
            continue
        h.update(cot.encode())
        for v in equity_curve[cot]:
            h.update(repr(float(v)).encode())
    return h.hexdigest()


def chay_nhieu_lan(so_lan: int) -> list[str]:
    """Chạy backtest ghim `so_lan` lần trong CÙNG tiến trình, trả các hash."""
    from tests.regression_harness import run_pinned_backtest

    hashes: list[str] = []
    for i in range(so_lan):
        print(f"  lần {i + 1}/{so_lan} ...", flush=True)
        kq = run_pinned_backtest()
        hashes.append(bam_equity(kq.equity_curve))
        print(f"    sha256(equity) = {hashes[-1]}", flush=True)
    return hashes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=0, help="số lần chạy backtest ghim (0 = chỉ in môi trường)")
    args = ap.parse_args(argv)

    from ops.ci_bao_cao import notice, them_summary

    print("=" * 72)
    print("DẤU VÂN TAY MÔI TRƯỜNG SỐ HỌC")
    print("=" * 72)
    vt = dau_van_tay()
    for k, v in vt.items():
        print(f"  {k:24} {v}")

    # Ra NGOÀI log: đây là bộ trường phải kèm MỌI khẳng định tất định
    # (CLAUDE.md #19), và nó vô dụng nếu chỉ đọc được bằng cách chép tay.
    notice(
        "DAU VAN TAY",
        blas=vt["blas_name"],
        arch=vt["machine"],
        python=vt["python"],
        numpy=vt["numpy"],
        scipy=vt["scipy"],
        hmmlearn=vt["hmmlearn"],
        threads=vt["OMP_NUM_THREADS"],
        threadpool=vt["threadpool"],
    )
    them_summary(
        "### Dấu vân tay môi trường số học\n\n| trường | giá trị |\n|---|---|\n"
        + "\n".join(f"| `{k}` | `{v}` |" for k, v in vt.items())
    )

    if args.runs <= 0:
        print("\n(--runs 0: không chạy backtest)")
        return 0

    print("\n" + "=" * 72)
    print(f"TẤT ĐỊNH NỘI MÁY — chạy backtest ghim {args.runs} lần trong cùng tiến trình")
    print("=" * 72)
    hashes = chay_nhieu_lan(args.runs)

    giong = len(set(hashes)) == 1
    notice(
        "TAT DINH NOI MAY",
        **{f"run{i}": h for i, h in enumerate(hashes, 1)},
        giong="yes" if giong else "no",
        so_hash_khac_nhau=len(set(hashes)),
    )
    them_summary(
        f"### Tất định nội máy — {args.runs} lần chạy\n\n"
        f"**{'ĐẠT' if giong else 'TRƯỢT'}** — {len(set(hashes))} hash khác nhau.\n\n"
        "| lần | sha256(equity) |\n|---|---|\n"
        + "\n".join(f"| {i} | `{h}` |" for i, h in enumerate(hashes, 1))
        + "\n\nPHẠM VI: chỉ nói về máy/cấu hình ở bảng dấu vân tay trên."
    )

    if giong:
        print(f"\nĐẠT — {args.runs}/{args.runs} lần cho CÙNG hash. Tất định NỘI MÁY.")
        print("     PHẠM VI: chỉ nói về máy này, cấu hình này. KHÔNG nói gì về máy khác.")
        return 0

    print(f"\nTRƯỢT — {len(set(hashes))} hash khác nhau trong {args.runs} lần chạy.")
    print("        Bất định NỘI MÁY. Mọi so sánh liên máy đều vô nghĩa cho tới khi sửa.")
    for i, h in enumerate(hashes, 1):
        print(f"        lần {i}: {h}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
