"""CLAUDE.md #19 — `ops/verify_scope.py`.

Một công cụ đo phạm vi mà bản thân nó không được kiểm chứng là lớp giả an
toàn dày thêm một tầng: nó in ra những con số trông có thẩm quyền, và
không ai biết chúng có đúng không.

Điều đó đã suýt xảy ra ngay khi viết file này: regex đọc số file của mypy
chỉ bắt dạng `"checked N source files"` (chỉ xuất hiện khi CÓ lỗi), nên
lúc mypy SẠCH nó báo "KHÔNG BÁO SỐ FILE" — công cụ đo điểm mù tự có điểm
mù. `test_doc_duoc_ca_hai_dang_thong_diep_mypy` giữ chỗ đó.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ops.verify_scope import (
    ACCEPTANCE_PATHS,
    ScopeCheck,
    check_mypy,
    check_paths,
    check_pytest,
    check_ruff,
    format_report,
    mypy_checked_count,
    pytest_collected,
    ruff_checked_files,
    tracked_python_files,
)

_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# ruff — kiểm ĐỦ mọi file .py git theo dõi
# ----------------------------------------------------------------------


def test_ruff_kiem_moi_file_git_theo_doi() -> None:
    """Khẳng định trung tâm cho ruff: không file nào git theo dõi mà ruff
    bỏ qua. Một mục `extend-exclude` thêm vào `pyproject.toml` sẽ đỏ ở đây
    — đó là mục đích, vì loại một thư mục khỏi lint là một quyết định phải
    thấy được, không phải một dòng config lặng lẽ."""
    bo_sot = set(tracked_python_files()) - set(ruff_checked_files())

    assert not bo_sot, f"ruff bỏ qua {len(bo_sot)} file git theo dõi: {sorted(bo_sot)[:5]}"


def test_ruff_khong_dem_pyproject_la_file_ma_nguon() -> None:
    """`--show-files` in cả `pyproject.toml`. Đếm nó vào sẽ làm con số
    lệch đúng 1 so với `git ls-files "*.py"` và che mất một file bị bỏ
    sót thật."""
    assert not any(f.endswith(".toml") for f in ruff_checked_files())


def test_check_ruff_bao_ok_tren_repo_hien_tai() -> None:
    assert check_ruff().ok


# ----------------------------------------------------------------------
# mypy — GHIM số file, tụt xuống là đỏ
# ----------------------------------------------------------------------


def test_mypy_kiem_khong_it_hon_so_file_git_theo_doi() -> None:
    """Ghim theo `git ls-files`, KHÔNG theo hằng số 84.

    Một con số ghim cứng sẽ đỏ mỗi lần thêm file mới (nhiễu, rồi người ta
    sẽ sửa con số theo phản xạ) và vẫn XANH nếu repo teo lại đúng bằng nó
    (điểm mù). So với git thì cả hai chiều đều đúng và không phải bảo trì.
    """
    n = mypy_checked_count()

    assert n is not None, "mypy không báo số file — nó đã DỪNG SỚM, xem CLAUDE.md #19"
    assert n >= len(tracked_python_files())


def test_mypy_khong_dung_som() -> None:
    """Chế độ hỏng đã XẢY RA (2026-08-14): thiếu `tests/__init__.py` làm
    mypy dừng ở "Source file found twice under different module names" sau
    khi kiểm 0 file. Lúc đó "Found 1 error" trông như "gần sạch"."""
    assert mypy_checked_count() is not None


def test_doc_duoc_ca_hai_dang_thong_diep_mypy() -> None:
    """mypy in HAI dạng tuỳ có lỗi hay không. Bản đầu của regex chỉ bắt
    dạng có lỗi, nên khi mypy sạch thì công cụ đo báo "KHÔNG BÁO SỐ FILE"
    — công cụ đo điểm mù tự có điểm mù."""
    from ops.verify_scope import _MYPY_COUNT

    sach = _MYPY_COUNT.search("Success: no issues found in 85 source files")
    co_loi = _MYPY_COUNT.search("Found 3 errors in 2 files (checked 85 source files)")

    assert sach is not None and sach.group(1) == "85"
    assert co_loi is not None and co_loi.group(1) == "85"


def test_mypy_dung_som_thi_bao_KHONG_OK(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm chính nhánh cảnh báo, không chỉ nhánh vui."""
    import ops.verify_scope as vs

    monkeypatch.setattr(vs, "mypy_checked_count", lambda **_: None)
    ket_qua = check_mypy()

    assert not ket_qua.ok
    assert "DỪNG SỚM" in ket_qua.detail


def test_mypy_tut_so_file_thi_bao_KHONG_OK(monkeypatch: pytest.MonkeyPatch) -> None:
    import ops.verify_scope as vs

    monkeypatch.setattr(vs, "mypy_checked_count", lambda **_: 3)
    ket_qua = check_mypy()

    assert not ket_qua.ok
    assert "TỤT" in ket_qua.detail


# ----------------------------------------------------------------------
# pytest — "toàn bộ xanh" chỉ đúng một nửa
# ----------------------------------------------------------------------


def test_bo_mac_dinh_KHONG_phai_toan_bo() -> None:
    """`addopts = "-m 'not slow'"` là đánh đổi có chủ ý, nhưng phải NHÌN
    THẤY: một dòng "691 passed" không nói được rằng 6 test khác chưa chạy."""
    mac_dinh = pytest_collected()
    tong = pytest_collected("")

    assert mac_dinh is not None and tong is not None
    assert mac_dinh < tong, "không còn test `slow` nào — kiểm tra lại marker"


def test_bao_cao_pytest_noi_ro_phan_bi_loai() -> None:
    ket_qua = check_pytest()

    assert "/" in ket_qua.scope
    assert "slow" in ket_qua.detail


def test_doc_duoc_ca_hai_dang_thong_diep_pytest() -> None:
    """pytest in "N/M tests collected (K deselected)" khi có lọc marker, và
    "M tests collected" khi không."""
    from ops.verify_scope import _PYTEST_COUNT

    co_loc = _PYTEST_COUNT.search("691/697 tests collected (6 deselected) in 1.78s")
    khong_loc = _PYTEST_COUNT.search("697 tests collected in 1.29s")

    assert co_loc is not None and (co_loc.group(1) or co_loc.group(3)) == "691"
    assert khong_loc is not None and (khong_loc.group(1) or khong_loc.group(3)) == "697"


# ----------------------------------------------------------------------
# Đường dẫn nghiệm thu — chỗ nguy hiểm nhất
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "duong_dan,muc",
    [(p, m) for p, m, duoc_phep_thieu in ACCEPTANCE_PATHS if not duoc_phep_thieu],
)
def test_duong_dan_nghiem_thu_ton_tai(duong_dan: str, muc: str) -> None:
    """`grep -rn "..." duong/dan/` trên thư mục KHÔNG TỒN TẠI trả 0 kết
    quả, và trong một checklist thủ công thì "không có kết quả" là ĐẠT.

    Mục nghiệm thu đó không kiểm gì cả — nó chỉ chứng minh thư mục không
    tồn tại. Đây là chế độ hỏng không để lại dấu vết nào.
    """
    assert (_ROOT / duong_dan).exists(), f"`{muc}` sẽ trả rỗng vì {duong_dan} không tồn tại"


def test_danh_dau_CHUA_XAY_phai_that_su_chua_xay() -> None:
    """Cờ `expected_missing=True` là một lời khai "mục nghiệm thu này hiện
    RỖNG". Khi file được xây xong mà cờ còn nguyên, lời khai thành SAI —
    và nó sai theo hướng nguy hiểm: một phép kiểm thật bị dán nhãn "chưa
    kiểm được gì", nên không ai đọc kết quả của nó.

    Đã xảy ra 2026-08-14: `ops/shadow_runner.py` được xây ở Phase 12c và
    cờ vẫn là `True`.
    """
    con_thieu = [
        p for p, _muc, duoc_phep in ACCEPTANCE_PATHS if duoc_phep and (_ROOT / p).exists()
    ]

    assert not con_thieu, (
        f"đánh dấu CHƯA XÂY nhưng file ĐÃ TỒN TẠI: {con_thieu}\n"
        "Đổi cờ sang False trong ops/verify_scope.py::ACCEPTANCE_PATHS — "
        "mục nghiệm thu đó giờ kiểm thật."
    )


def test_khong_con_muc_nghiem_thu_rong() -> None:
    """Trạng thái MONG MUỐN: không mục nghiệm thu nào ĐẠT một cách rỗng.

    Test này được phép xanh với danh sách rỗng — khác
    `test_danh_dau_CHUA_XAY_phai_that_su_chua_xay`, vốn bắt cờ nói dối.
    Nếu Phase sau thêm một mục chưa xây, nó sẽ hiện ở đây và bảng
    `verify_scope` in ra nhãn CHƯA XÂY.
    """
    rong = [c for c in check_paths() if c.scope == "CHƯA XÂY"]

    assert all("RỖNG" in c.detail for c in rong), "mục chưa xây phải nói rõ nó rỗng"


def test_duong_dan_bien_mat_thi_bao_KHONG_OK(tmp_path: Path) -> None:
    """Kiểm nhánh cảnh báo: một repo giả không có `core/` phải cho FAIL,
    không phải "sạch"."""
    (tmp_path / "monitoring").mkdir()

    ket_qua = check_paths(repo_root=tmp_path)
    hong = [c for c in ket_qua if not c.ok]

    assert hong, "repo thiếu gần hết thư mục mà vẫn báo ok"
    assert any("KHÔNG TỒN TẠI" in c.scope for c in hong)
    assert any("không phải vì sạch" in c.detail for c in hong)


def test_moi_lenh_grep_trong_nghiem_thu_co_trong_ACCEPTANCE_PATHS() -> None:
    """Chống trôi lệch giữa tài liệu và công cụ: mọi đường dẫn xuất hiện
    trong một dòng `grep` của `prompts/*/Nghiệm thu` phải có mặt ở
    `ACCEPTANCE_PATHS`, nếu không nó không được ai canh.

    Quét thô (tách token trông giống đường dẫn) — cố tình thô: bỏ sót một
    đường dẫn thật còn tệ hơn báo thừa một token vô hại.
    """
    import re

    da_dang_ky = {p for p, _, _ in ACCEPTANCE_PATHS}
    thieu: set[str] = set()
    for f in (_ROOT / "prompts").glob("*.md"):
        for dong in f.read_text(encoding="utf-8").splitlines():
            if "grep -" not in dong:
                continue
            for token in re.findall(r"(?<![\w/.])((?:\w+/)+\w*(?:\.py)?)", dong):
                if token.startswith(("http", "www")) or token in da_dang_ky:
                    continue
                # Chỉ quan tâm token trỏ vào cây mã nguồn thật hoặc kết
                # thúc bằng `/` (thư mục) — phần còn lại là chú thích.
                if token.endswith("/") or token.endswith(".py"):
                    thieu.add(token)

    assert not thieu, (
        f"đường dẫn trong lệnh grep nghiệm thu chưa được canh: {sorted(thieu)}\n"
        "Thêm vào ops/verify_scope.py::ACCEPTANCE_PATHS."
    )


# ----------------------------------------------------------------------
# Báo cáo
# ----------------------------------------------------------------------


def test_bao_cao_danh_dau_muc_hong() -> None:
    ra = format_report([ScopeCheck("x", "KHÔNG TỒN TẠI", False, "lý do"), ScopeCheck("y", "ok", True)])

    assert "!!" in ra
    assert "1/2 mục có phạm vi hợp lệ" in ra


def test_cli_thoat_khac_khong_khi_co_muc_hong(tmp_path: Path) -> None:
    """Không đọc exit code sau pipe (CLAUDE.md #17) — chạy trực tiếp."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pathlib; sys.path.insert(0, '.');"
            " from ops.verify_scope import check_paths;"
            f" ket_qua = check_paths(repo_root=pathlib.Path({str(tmp_path)!r}));"
            " sys.exit(0 if all(c.ok for c in ket_qua) else 1)",
        ],
        cwd=_ROOT,
        capture_output=True,
    )

    assert proc.returncode == 1


def test_cli_tren_repo_that_thoat_khong() -> None:
    from ops.verify_scope import run_all

    assert all(c.ok for c in run_all()), format_report(run_all())


def test_mypy_bao_DUNG_so_file_khong_phai_chi_khong_it_hon() -> None:
    """Hai chiều, không chỉ `>=`.

    Đo bằng đột biến: thay `mypy_checked_count()` bằng `return 99999` thì
    một khẳng định `n >= len(tracked)` vẫn XANH — và lúc đó công cụ đo
    phạm vi đang in một con số bịa ra với vẻ đầy thẩm quyền. Đúng loại giả
    an toàn mà CLAUDE.md #19 sinh ra để chặn.
    """
    from ops.verify_scope import visible_python_files

    assert mypy_checked_count() == len(visible_python_files())


def test_mypy_bao_thua_so_file_thi_bao_KHONG_OK(monkeypatch: pytest.MonkeyPatch) -> None:
    import ops.verify_scope as vs

    monkeypatch.setattr(vs, "mypy_checked_count", lambda **_: 99999)
    ket_qua = check_mypy()

    assert not ket_qua.ok
    assert "BÁO THỪA" in ket_qua.detail


def test_moc_so_phu_TOAN_REPO_khong_phai_mot_goc() -> None:
    """`tracked_python_files()` là mốc so cho cả ruff lẫn mypy. Thu hẹp nó
    (ví dụ `git ls-files "core/*.py"`) làm CẢ HAI phép kiểm luôn "đủ" —
    một điểm mù duy nhất vô hiệu hoá hai cổng cùng lúc.

    Đo bằng đột biến: thu hẹp mốc so, mọi test khác vẫn xanh.
    """
    tracked = set(tracked_python_files())
    thu_muc_goc = {f.split("/")[0] for f in tracked if "/" in f}

    # Mọi package thật của dự án phải có mặt.
    assert {"core", "monitoring", "broker", "data", "backtest", "ops", "tests"} <= thu_muc_goc
    assert "main.py" in tracked, "mốc so bỏ sót file ở gốc repo"
    assert len(tracked) > 50, f"mốc so chỉ có {len(tracked)} file — có vẻ đã bị thu hẹp"
