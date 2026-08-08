"""Điểm vào của forward test — chọn FILE LOG ĐANG HOẠT ĐỘNG theo schema.

Đây là module mà LaunchAgent chạy (`python -m forward.runner`), KHÔNG
phải `forward.logger` nữa. Lý do tồn tại: schema `log.csv` đã cuộn sang
v2 ngày 2026-08-08 (xem `forward/SCHEMA.md`), và việc cuộn file phải làm
được mà KHÔNG sửa `forward/logger.py`.

## Tại sao không sửa thẳng `_LOG_PATH` trong logger.py

`forward/logger.py` đóng băng, SHA256 ghim trong
`tests/golden/frozen_hashes.json` (CLAUDE.md kỷ luật #15). Sửa nó — kể cả
đúng một dòng gán đường dẫn — buộc phải đổi hash, và theo chính bất biến
đó thì đổi hash CHỈ hợp lệ khi CỐ Ý KẾT THÚC thí nghiệm hiện tại. Cuộn
file log không phải kết thúc thí nghiệm: cấu hình đóng băng
(`config_frozen.yaml`, `FEATURE_SUBSET`) không đổi, chuỗi bar không đứt,
`hmm_retrained` đọc tiếp được qua cả hai file. Trả giá bằng việc vô hiệu
hoá một bất biến để thoả một thay đổi không cần tới nó là lỗ hổng đúng
kiểu đã gây ra sự cố 2026-08-06 ngay từ đầu.

`append_row()`/`read_existing_log()` tra `_LOG_PATH` ở **thời điểm gọi**
chứ không phải lúc định nghĩa hàm — thiết kế có chủ đích, docstring
`append_row` nói rõ. Gán lại biến module trước khi gọi `run_forward_test()`
là dùng đúng cánh cửa đã chừa sẵn, không phải lách.

## Hệ quả với `log.csv` (v1)

Sau khi module này gán `_LOG_PATH`, `forward/log.csv` KHÔNG còn được ghi
bởi bất kỳ đường nào. Nó đóng vĩnh viễn ở 1 bar (2026-08-05) và được ghim
SHA256 trong `tests/golden/frozen_hashes.json` — sửa nó bây giờ là làm
sai lệch bằng chứng thí nghiệm, không phải sửa lỗi.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from forward import logger as _logger

_FORWARD_DIR = Path(__file__).resolve().parent

# File log ĐANG HOẠT ĐỘNG. Đổi giá trị này = cuộn schema sang v3 — đọc
# forward/SCHEMA.md trước, và ghi entry DECISIONS.md TẠI THỜI ĐIỂM đổi
# (bài học 2026-08-08: thêm cột vào forward/ mà không có entry là cách sự
# cố lần trước xảy ra).
ACTIVE_LOG_PATH = _FORWARD_DIR / "log_v2.csv"

# Các file log đã đóng, theo thứ tự thời gian. Code phân tích ở mốc 3/6/12
# tháng phải đọc TẤT CẢ rồi mới nối — xem `forward/SCHEMA.md`.
CLOSED_LOG_PATHS = (_FORWARD_DIR / "log.csv",)


def activate_current_schema() -> Path:
    """Trỏ `forward.logger` sang file log đang hoạt động. Trả về path đó.

    Idempotent — gọi nhiều lần cho cùng kết quả, không tích luỹ trạng thái.
    """
    _logger._LOG_PATH = ACTIVE_LOG_PATH
    return ACTIVE_LOG_PATH


def load_all_bars() -> pd.DataFrame:
    """Nối MỌI file log (đã đóng + đang hoạt động) theo thứ tự thời gian.

    Đây là đường DUY NHẤT mà code phân tích ở mốc 3/6/12 tháng được dùng.
    Đọc thẳng `log_v2.csv` sẽ mất bar 2026-08-05 — một bar trên tổng số
    365 thì vô hại về thống kê, nhưng "vô hại" không phải thứ nên để mỗi
    chỗ gọi tự phán đoán lại.

    `warning_count` của v1 là `NaN`, KHÔNG phải `0`: bản chạy v1 không có
    cơ chế đếm warning, nên `0` sẽ là khẳng định sai ("đã đo, không có")
    thay vì ô trống trung thực ("không biết"). Mọi phép thống kê trên cột
    này phải bỏ qua NaN thay vì cộng chúng vào như số không.

    Cột trả về luôn là `_CSV_FIELDNAMES` của schema MỚI NHẤT — file cũ
    thiếu cột nào thì cột đó thành NaN.
    """
    frames: list[pd.DataFrame] = []
    for path in (*CLOSED_LOG_PATHS, ACTIVE_LOG_PATH):
        if not path.exists():
            continue
        df = _logger.read_existing_log(path)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["source_log"] = path.name
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=[*_logger._CSV_FIELDNAMES, "source_log"])

    out = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    return out.reindex(columns=[*_logger._CSV_FIELDNAMES, "source_log"])


def run() -> dict[str, Any]:
    activate_current_schema()
    return _logger.run_forward_test()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run()
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
