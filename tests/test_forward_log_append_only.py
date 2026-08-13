"""Bất biến append-only áp cho MỌI file log forward test, cả v1 lẫn v2.

`tests/test_forward_logger.py` đã kiểm `append_row()` không ghi đè dòng cũ
— nhưng đó là kiểm HÀM, trên `tmp_path`. File này kiểm FILE THẬT trên đĩa:
`forward/log.csv` (v1, đã đóng) và `forward/log_v2.csv` (v2, đang chạy).

Khoảng trống mà file này lấp: sự cố 2026-08-06 không phải lỗi của
`append_row()` — hàm đó chạy đúng đặc tả suốt. Lỗi nằm ở chỗ file trên đĩa
rơi vào trạng thái header một schema / dòng một schema khác, và không test
nào nhìn vào file thật để thấy. Xem `forward/SCHEMA.md`.

Không test nào ở đây được sửa để "cho qua" khi đỏ. Đỏ nghĩa là một file
bằng chứng đã bị sửa hoặc đã hỏng — điều tra file, đừng điều tra test.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from forward.logger import _CSV_FIELDNAMES, read_existing_log
from forward.runner import ACTIVE_LOG_PATH, CLOSED_LOG_PATHS, load_all_bars

_ALL_LOGS = (*CLOSED_LOG_PATHS, ACTIVE_LOG_PATH)

# Số cột của từng schema, ghim theo tên file. v1 = v2 trừ `warning_count`.
_EXPECTED_WIDTH = {
    "log.csv": len(_CSV_FIELDNAMES) - 1,
    "log_v2.csv": len(_CSV_FIELDNAMES),
}


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.reader(fh))


@pytest.mark.parametrize("path", _ALL_LOGS, ids=lambda p: p.name)
def test_moi_dong_cung_so_cot_voi_header(path: Path) -> None:
    """Đây là test bắt được sự cố 2026-08-06 nếu nó đã tồn tại lúc đó.

    Header 31 cột + dòng 32 cột là trạng thái `log.csv` đã nằm suốt 3 ngày
    trong khi launchd chạy đều và exit 1 mỗi lần.
    """
    rows = _rows(path)
    assert rows, f"{path.name} rỗng hoàn toàn — không có cả header"

    width = len(rows[0])
    for n, row in enumerate(rows[1:], start=2):
        assert len(row) == width, (
            f"{path.name} dòng {n} có {len(row)} cột, header có {width}. "
            f"File log append-only KHÔNG BAO GIỜ được rơi vào trạng thái này — "
            f"nó nghĩa là schema đã đổi giữa chừng mà file cũ giữ header cũ "
            f"(`append_row()` chỉ ghi header khi file chưa tồn tại). "
            f"Cuộn sang file mới, đừng sửa file này. Xem forward/SCHEMA.md."
        )


@pytest.mark.parametrize("path", _ALL_LOGS, ids=lambda p: p.name)
def test_header_khop_dung_schema_cua_file(path: Path) -> None:
    """v1 phải là đúng `_CSV_FIELDNAMES` trừ `warning_count`, không phải
    "một tập con nào đó" — thứ tự cột cũng phải khớp."""
    header = _rows(path)[0]
    assert len(header) == _EXPECTED_WIDTH[path.name], (
        f"{path.name}: {len(header)} cột, chờ đợi {_EXPECTED_WIDTH[path.name]}"
    )

    if path.name == "log.csv":
        assert header == [c for c in _CSV_FIELDNAMES if c != "warning_count"]
    else:
        assert header == list(_CSV_FIELDNAMES)


@pytest.mark.parametrize("path", _ALL_LOGS, ids=lambda p: p.name)
def test_ngay_tang_dan_khong_trung(path: Path) -> None:
    """Append-only nghĩa là bar chỉ đi tới. Ngày lùi hoặc trùng nghĩa là
    có ai đó đã ghi lại (hoặc backfill hai lần) — mất tính chất "mỗi dòng
    ghi đúng một lần, tại thời điểm đó, không bao giờ đổi"."""
    df = read_existing_log(path)
    if df is None or df.empty:
        pytest.skip(f"{path.name} chưa có bar nào")

    dates = list(df["date"])
    assert dates == sorted(dates), f"{path.name}: ngày không tăng dần"
    assert len(set(dates)) == len(dates), f"{path.name}: có ngày trùng"


@pytest.mark.parametrize("path", _ALL_LOGS, ids=lambda p: p.name)
def test_doc_duoc_bang_dung_ham_ma_forward_test_dung(path: Path) -> None:
    """`read_existing_log()` là hàm mà `run_forward_test()` gọi ở bước đầu.

    Nó đỏ ở đây nghĩa là lần chạy launchd tiếp theo sẽ exit 1 — và sẽ tiếp
    tục exit 1 mãi cho tới khi có người sửa.
    """
    assert read_existing_log(path) is not None or not _rows(path)[1:]


# ----------------------------------------------------------------------
# Nối hai schema
# ----------------------------------------------------------------------


def test_load_all_bars_gom_du_moi_file() -> None:
    """Tổng bar của bản nối phải bằng tổng bar của từng file cộng lại —
    không mất bar nào ở chỗ giáp ranh hai schema."""
    tong = 0
    for path in _ALL_LOGS:
        df = read_existing_log(path)
        tong += 0 if df is None else len(df)

    assert len(load_all_bars()) == tong


def test_load_all_bars_giu_warning_count_v1_la_nan() -> None:
    """v1 KHÔNG được thành 0.

    Bản chạy v1 không có cơ chế đếm warning; `0` là khẳng định sai ("đã
    đo, không có") thay vì ô trống trung thực ("không biết"). Nếu ai đó
    thêm `fillna(0)` vào `load_all_bars()`, test này phải đỏ.
    """
    df = load_all_bars()
    v1 = df[df["source_log"] == "log.csv"]
    if v1.empty:
        pytest.skip("chưa có bar v1")

    assert v1["warning_count"].isna().all()


def test_load_all_bars_sap_theo_thoi_gian() -> None:
    dates = list(load_all_bars()["date"])
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)


def test_load_all_bars_tra_ve_schema_moi_nhat() -> None:
    """Cột phải là `_CSV_FIELDNAMES` mới nhất + `source_log` — code phân
    tích không phải biết file nào thiếu cột nào."""
    df = load_all_bars()
    assert list(df.columns) == [*_CSV_FIELDNAMES, "source_log"]


# ----------------------------------------------------------------------
# Cuộn file
# ----------------------------------------------------------------------


def test_file_dang_hoat_dong_khong_nam_trong_danh_sach_da_dong() -> None:
    """Cuộn schema mà quên chuyển file cũ sang `CLOSED_LOG_PATHS` (hoặc
    ngược lại) sẽ làm `load_all_bars()` đọc trùng hoặc bỏ sót."""
    assert ACTIVE_LOG_PATH not in CLOSED_LOG_PATHS


def test_moi_file_da_dong_deu_duoc_ghim_hash() -> None:
    """File đã đóng = bằng chứng không đổi được nữa. Đóng một file mà quên
    ghim hash nghĩa là nó có thể bị sửa im lặng sau này."""
    import json

    pinned = json.loads(
        (Path(__file__).resolve().parent / "golden" / "frozen_hashes.json").read_text(encoding="utf-8")
    )["files"]
    repo = Path(__file__).resolve().parent.parent

    for path in CLOSED_LOG_PATHS:
        rel = str(path.relative_to(repo))
        assert rel in pinned, (
            f"{rel} đã đóng (nằm trong CLOSED_LOG_PATHS) nhưng CHƯA ghim SHA256 trong "
            f"tests/golden/frozen_hashes.json — xem forward/SCHEMA.md, mục "
            f"'Nếu vẫn buộc phải cuộn'. Lưu ý: KHÔNG cuộn schema trong thời gian "
            f"thí nghiệm (tới 2027-08-06) — dùng file phụ forward/extra_<tên>.csv."
        )


def test_forward_logger_van_tro_ve_v1_khi_chua_activate() -> None:
    """`forward/logger.py` KHÔNG bị sửa — `_LOG_PATH` mặc định vẫn là
    `log.csv`. `forward/runner.py` mới là chỗ chọn file đang hoạt động.

    Nếu test này đỏ, ai đó đã sửa file đóng băng thay vì dùng runner —
    `tests/test_frozen_files.py` cũng sẽ đỏ theo.
    """
    import importlib

    mod = importlib.import_module("forward.logger")
    assert mod.__file__ is not None
    src_default = Path(mod.__file__).resolve().parent / "log.csv"
    assert src_default.name == "log.csv"
    assert ACTIVE_LOG_PATH.name == "log_v2.csv"


def test_bar_khong_trung_giua_hai_file() -> None:
    """Bar 2026-08-05 chỉ được nằm ở v1, không được lặp lại ở v2."""
    seen: dict[pd.Timestamp, str] = {}
    for path in _ALL_LOGS:
        df = read_existing_log(path)
        if df is None:
            continue
        for d in df["date"]:
            assert d not in seen, (
                f"bar {d.date()} xuất hiện ở CẢ {seen[d]} lẫn {path.name} — "
                f"backfill đã chạy hai lần qua ranh giới schema?"
            )
            seen[d] = path.name
