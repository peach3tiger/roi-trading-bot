"""Đưa số liệu CI ra NGOÀI log — annotations và step summary.

## Vì sao đây là sửa chữa QUY TRÌNH, không phải tiện ích

Phiên 2026-08-15/16 bị chặn ít nhất năm lần vì cùng một lý do: một phép
đo chạy trên CI, kết quả nằm trong log, và log chỉ đọc được bằng cách
người dùng mở trang GitHub rồi chép tay từng bước. Mỗi lần chặn lại đẻ ra
một vòng đoán mò — và ba trong số các vòng đó kết luận sai (H1, H2, H3
đều bị bác bằng dữ liệu sau đó).

Máy làm việc không có `gh`. Đó là ràng buộc cố định, không phải sự cố tạm
thời, nên cách sửa phải là đưa số liệu tới chỗ đọc được **mà không cần
đăng nhập, không cần công cụ**:

- `::notice title=X::...` -> mục **Annotations** ngay trang run
- `$GITHUB_STEP_SUMMARY`  -> trang **Summary**, hỗ trợ markdown

## Ràng buộc của kênh annotation

GitHub hiện **tối đa 10** annotation mỗi run và cắt mỗi cái ở **~4000 ký
tự**. Nên kênh này chỉ dành cho **KẾT LUẬN ĐÃ RÚT GỌN** — không phải log
thô. Đổ log vào đây sẽ làm chính nó vô dụng: 10 annotation đầy chữ mà
không cái nào trả lời được câu hỏi nào.

Bảng chi tiết đi vào `$GITHUB_STEP_SUMMARY`, nơi không có giới hạn đó.

## Vì sao in cả khi chạy ở local

Không có nhánh `if GITHUB_ACTIONS`. Chạy ở local in ra ĐÚNG những dòng CI
sẽ nhận, nên "annotation sẽ hiện gì" là thứ kiểm được trước khi push thay
vì thứ phải push mới biết.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

#: GitHub cắt mỗi annotation ở khoảng 4096 ký tự; chừa biên cho tiền tố.
MAX_ANNOTATION = 4000

#: GitHub chỉ HIỆN 10 annotation mỗi run — phần vượt bị nuốt IM LẶNG.
MAX_ANNOTATIONS = 10

_TITLE_HOP_LE = re.compile(r"^[A-Za-z0-9 _.:/#-]+$")


def _escape(gia_tri: str) -> str:
    """Escape theo đúng thứ tự GitHub yêu cầu.

    `%` phải đi TRƯỚC, nếu không `%0A` do escape xuống dòng sinh ra sẽ bị
    escape lần hai thành `%250A` và annotation hiện ra chuỗi rác.
    """
    return (
        gia_tri.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    )


def _cat(noi_dung: str) -> str:
    if len(noi_dung) <= MAX_ANNOTATION:
        return noi_dung
    # Cắt ở ĐẦU chứ không ở cuối: kết luận thường nằm cuối một danh sách,
    # và một annotation bị cắt mất kết luận thì không khác gì không có.
    bo = len(noi_dung) - MAX_ANNOTATION + 40
    return f"[đã cắt {bo} ký tự đầu] ..." + noi_dung[bo:]


def _phat(muc: str, title: str, noi_dung: str) -> str:
    if not _TITLE_HOP_LE.match(title):
        raise ValueError(
            f"title {title!r} chứa ký tự GitHub không hiện được. Dùng ASCII "
            f"không dấu — tiêu đề annotation KHÔNG phải chỗ viết tiếng Việt "
            f"có dấu, nội dung mới là."
        )
    dong = f"::{muc} title={title}::{_escape(_cat(noi_dung))}"
    print(dong, flush=True)
    return dong


def notice(title: str, **truong: object) -> str:
    """Một kết luận, dạng `k=v k=v`. Giá trị `None` -> `?`, không bỏ đi.

    Bỏ đi một trường rỗng làm hai lần chạy khác nhau in ra hai bộ trường
    khác nhau, và so hai bản ghi lệch trường là việc thủ công dễ sai —
    cùng bài học với `ops/kiem_tat_dinh.dau_van_tay()`.
    """
    ra = " ".join(f"{k}={'?' if v is None else v}" for k, v in truong.items())
    return _phat("notice", title, ra)


def error(title: str, noi_dung: str) -> str:
    return _phat("error", title, noi_dung)


def them_summary(markdown: str) -> bool:
    """Ghi vào `$GITHUB_STEP_SUMMARY`. `False` khi không chạy trên CI."""
    duong = os.environ.get("GITHUB_STEP_SUMMARY")
    if not duong:
        return False
    with open(duong, "a", encoding="utf-8") as f:
        f.write(markdown.rstrip() + "\n\n")
    return True


# ----------------------------------------------------------------------
# Đọc kết quả pytest -> annotation + summary
# ----------------------------------------------------------------------

_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
_TONG = re.compile(r"^(?:=+ )?(\d+ (?:passed|failed).*?)(?: =+)?$", re.M)


def ten_test_that_bai(dau_ra: str) -> list[str]:
    """Tên test FAILED/ERROR, không kèm traceback.

    Traceback KHÔNG đi vào annotation: nó dài, và mười annotation đầy
    traceback che mất chín kết luận khác. Tên test là thứ đủ để biết đi
    đọc log ở đâu.

    Tên hàm KHÔNG bắt đầu bằng `test_`: `pyproject.toml` mở rộng
    `python_files`, và một hàm sản xuất tên `test_*` được import vào file
    test sẽ bị pytest THU THẬP thành test — nó đỏ ngay với "fixture
    'dau_ra' not found". Đã xảy ra lúc viết file này.
    """
    return sorted({m.group(1) for m in _FAILED.finditer(dau_ra)})


def bao_cao_pytest(dau_ra: str, *, nhan: str) -> list[str]:
    """Phát annotation cho một lần chạy pytest. Trả các dòng đã phát."""
    that_bai = ten_test_that_bai(dau_ra)
    tong = _TONG.findall(dau_ra)
    dong_tong = tong[-1].strip() if tong else "(không đọc được dòng tổng kết)"

    if not that_bai:
        them_summary(f"### pytest — {nhan}\n\n`{dong_tong}`")
        return [notice(f"PYTEST OK {nhan}", ket_qua=dong_tong)]

    ra = [
        error(
            f"PYTEST FAILED {nhan}",
            f"{len(that_bai)} test đỏ ({dong_tong}):\n" + "\n".join(that_bai),
        )
    ]
    bang = "\n".join(f"| `{t}` |" for t in that_bai)
    them_summary(
        f"### pytest — {nhan}: **{len(that_bai)} ĐỎ**\n\n"
        f"`{dong_tong}`\n\n| test |\n|---|\n{bang}"
    )
    return ra


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tu-pytest", type=Path, help="file chứa output pytest")
    ap.add_argument("--nhan", help="nhãn ASCII phân biệt job/matrix")
    ap.add_argument("--notice", metavar="TITLE", help="phát một ::notice; các cặp k=v theo sau")
    ap.add_argument("--summary", metavar="TIEU_DE", help="thêm một mục vào step summary")
    ap.add_argument("--tu-file", type=Path, help="nội dung cho --summary (khối code)")
    ap.add_argument("truong", nargs="*", help="cặp k=v cho --notice")
    args = ap.parse_args(argv)

    if args.notice:
        cap: dict[str, object] = {}
        for t in args.truong:
            k, _, v = t.partition("=")
            cap[k.strip()] = v.strip()
        notice(args.notice, **cap)
        return 0

    if args.summary:
        noi = ""
        if args.tu_file and args.tu_file.exists():
            noi = args.tu_file.read_text(encoding="utf-8", errors="replace")
        them_summary(f"### {args.summary}\n\n```\n{noi.strip()}\n```")
        return 0

    if not args.tu_pytest or not args.nhan:
        ap.error("cần --tu-pytest + --nhan, hoặc --notice, hoặc --summary")

    if not args.tu_pytest.exists():
        error("PYTEST LOG THIEU", f"không có {args.tu_pytest} — không báo cáo được gì")
        return 1
    bao_cao_pytest(args.tu_pytest.read_text(encoding="utf-8", errors="replace"), nhan=args.nhan)
    # LUÔN trả 0: bước này BÁO CÁO, không phán xét. Exit code của pytest do
    # chính bước pytest giữ (CLAUDE.md #17) — một bộ báo cáo tự làm job đỏ
    # sẽ che mất nguyên nhân thật.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
