"""tests.test_frozen_files — ghim SHA256 của các file thuộc thí nghiệm
forward test đóng băng, để một thay đổi (vô tình HAY cố ý) không lọt qua
im lặng dù `forward/logger.py`/`forward/config_frozen.yaml` không đi qua
đường compose pipeline mà `tests/test_forward_golden.py`/
`tests/test_wiring_equivalence.py`/`tests/test_bars_window_sensitivity.py`
kiểm tra (ví dụ: sửa `DATA_START`, `_CSV_FIELDNAMES`, comment không ảnh
hưởng hành vi runtime nhưng VẪN là một thay đổi tới file đã tuyên bố đóng
băng — CLAUDE.md không phân biệt "vô hại" hay không ở đây, chỉ phân biệt
"đổi" hay "không đổi").

Hai file:
  - `forward/logger.py` — thí nghiệm forward test 12 tháng, bắt đầu
    2026-08-06 (xem `forward/README.md`, `docs/DECISIONS.md` mục
    "Forward test — tiền đăng ký"). KHÔNG được sửa trong suốt thí nghiệm.
  - `forward/config_frozen.yaml` — cấu hình đóng băng cho thí nghiệm đó.
    ĐÃ có cơ chế hash-kiểm RIÊNG ở tầng RUNTIME (`forward/config_frozen.sha256`,
    kiểm tra mỗi lần `forward/logger.py::load_frozen_settings()` chạy qua
    launchd) — file test này thêm một lớp Ở TẦNG TEST SUITE, độc lập:
    bắt được thay đổi ngay cả khi không ai chạy `run_forward_test()` (vd.
    trong CI/`pytest` thường, trước khi forward test hằng ngày kịp chạy).

**Hash ghim tại 2026-08-07 (`tests/golden/frozen_hashes.json`) phản ánh
`forward/logger.py` SAU khi thêm đoạn docstring "ĐO `bars_window`"** —
lần sửa DUY NHẤT được người yêu cầu tường minh trong phiên viết
`tests/test_bars_window_sensitivity.py` ("Khớp → ghi kết quả + ngày vào
docstring forward/logger.py"), thực hiện TRƯỚC khi file này tồn tại. Kể
từ lúc ghim hash này trở đi, `forward/logger.py` KHÔNG còn đường ngoại lệ
nào — kể cả một dòng comment.

**Test này KHÔNG được sửa hash để cho qua.** Đổi hash trong
`tests/golden/frozen_hashes.json` CHỈ khi CỐ Ý kết thúc thí nghiệm forward
hiện tại và bắt đầu thí nghiệm mới — ghi lý do + ngày vào
`docs/DECISIONS.md` TRƯỚC khi đổi, đúng quy trình đã áp dụng cho
`tests/test_forward_golden.py`/`tests/golden/forward_baseline.json`.

Xem CLAUDE.md bất biến #15 — file này nằm trong danh sách test bắt buộc
không được skip/xfail/comment out.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HASHES_PATH = Path(__file__).resolve().parent / "golden" / "frozen_hashes.json"

_EXPERIMENT_STARTED = "2026-08-06"


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_pinned_hashes() -> dict[str, str]:
    if not _HASHES_PATH.exists():
        raise FileNotFoundError(
            f"Thiếu {_HASHES_PATH} — không có hash nào được ghim. Đây không phải lỗi để tự "
            "sửa bằng cách tạo file mới tuỳ tiện, xem docstring module này."
        )
    with _HASHES_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    files: dict[str, str] = payload["files"]
    return files


def test_frozen_files_have_not_changed() -> None:
    pinned = _load_pinned_hashes()
    assert pinned, f"{_HASHES_PATH} không có file nào được ghim — kiểm tra lại nội dung."

    for relative_path, expected_hash in pinned.items():
        full_path = _REPO_ROOT / relative_path
        assert full_path.exists(), (
            f"{relative_path} đã ghim hash nhưng KHÔNG CÒN TỒN TẠI — bị xoá hay đổi tên? "
            "Đây là file thuộc thí nghiệm forward test đóng băng, không được động vào."
        )
        actual_hash = _sha256_of(full_path)
        assert actual_hash == expected_hash, (
            f"\n\n{relative_path} ĐÃ THAY ĐỔI kể từ lúc ghim hash — SHA256 hiện tại "
            f"({actual_hash}) khác hash đã ghim ({expected_hash}) trong {_HASHES_PATH}.\n\n"
            f"Đây là file thuộc THÍ NGHIỆM FORWARD TEST 12 THÁNG, bắt đầu "
            f"{_EXPERIMENT_STARTED} (xem forward/README.md, docs/DECISIONS.md mục "
            f"'Forward test — tiền đăng ký'). Sửa file này — DÙ VÔ TÌNH HAY CỐ Ý — nghĩa là "
            f"THÍ NGHIỆM HIỆN TẠI ĐÃ KẾT THÚC tại đúng thời điểm này, và một thí nghiệm MỚI "
            f"bắt đầu từ đây.\n\n"
            f"KHÔNG được sửa hash trong {_HASHES_PATH.relative_to(_REPO_ROOT)} để test này xanh "
            f"lại. Nếu thay đổi là VÔ TÌNH: revert file về đúng nội dung cũ. Nếu thay đổi là CỐ "
            f"Ý: ghi lý do + ngày vào docs/DECISIONS.md TRƯỚC, rồi mới cập nhật hash ghim cho thí "
            f"nghiệm MỚI."
        )
