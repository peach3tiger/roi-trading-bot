"""`ops/ci_bao_cao.py` + cú pháp shell của `ci.yml`.

Hai thứ ở đây có cùng một lý do tồn tại: **một kênh báo cáo hỏng im lặng
thì tệ hơn không có kênh nào**. Không có kênh, người ta biết mình mù. Có
một kênh không phát gì, người ta tưởng mình thấy.

Cả hai chế độ hỏng đã xảy ra trong dự án này ở dạng khác: `grep` trên thư
mục không tồn tại trả rỗng và được đọc thành "sạch"; cổng §E in "Bỏ qua"
và được đọc thành "đã kiểm".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ops.ci_bao_cao import (
    MAX_ANNOTATION,
    _escape,
    bao_cao_pytest,
    error,
    notice,
    ten_test_that_bai,
    them_summary,
)

_ROOT = Path(__file__).resolve().parent.parent
_CI = _ROOT / ".github" / "workflows" / "ci.yml"


# ----------------------------------------------------------------------
# Escape — sai thứ tự là hỏng im lặng
# ----------------------------------------------------------------------


def test_escape_phan_tram_TRUOC_xuong_dong() -> None:
    """`%` phải escape TRƯỚC. Ngược lại, `%0A` do escape `\\n` sinh ra bị
    escape lần hai thành `%250A` và annotation hiện ra chuỗi rác."""
    assert _escape("a\nb") == "a%0Ab"
    assert _escape("100%\n") == "100%25%0A"


def test_escape_carriage_return() -> None:
    assert _escape("a\r\nb") == "a%0D%0Ab"


# ----------------------------------------------------------------------
# Annotation
# ----------------------------------------------------------------------


def test_notice_dinh_dang_dung(capsys: pytest.CaptureFixture[str]) -> None:
    notice("TAT DINH NOI MAY", run1="abc", giong="yes")

    assert capsys.readouterr().out.strip() == "::notice title=TAT DINH NOI MAY::run1=abc giong=yes"


def test_truong_None_thanh_hoi_cham_khong_bi_bo_di() -> None:
    """Bỏ một trường rỗng làm hai lần chạy in ra hai bộ trường khác nhau,
    và so hai bản ghi lệch trường là việc thủ công dễ sai."""
    assert "blas=?" in notice("X", blas=None)


def test_tieu_de_co_dau_tieng_viet_thi_RAISE() -> None:
    """GitHub không hiện được, và nó hỏng IM LẶNG — annotation đơn giản
    không xuất hiện. Raise ở đây biến một lỗi vô hình thành lỗi thấy được
    ngay lúc viết."""
    with pytest.raises(ValueError, match="ASCII"):
        notice("TẤT ĐỊNH")


def test_cat_o_DAU_giu_lai_ket_luan(capsys: pytest.CaptureFixture[str]) -> None:
    """Kết luận nằm CUỐI một danh sách. Cắt đuôi = mất đúng thứ cần đọc."""
    error("X", "RAC" * 3000 + "KET-LUAN-QUAN-TRONG")
    ra = capsys.readouterr().out

    assert "KET-LUAN-QUAN-TRONG" in ra
    assert "đã cắt" in ra
    assert len(ra) < MAX_ANNOTATION + 200


# ----------------------------------------------------------------------
# Đọc kết quả pytest
# ----------------------------------------------------------------------

_MAU_DO = """\
FAILED tests/test_a.py::test_mot - AssertionError: chi tiet dai dong
FAILED tests/test_b.py::test_hai - ValueError
ERROR tests/test_c.py::test_ba
=========================== 2 failed, 5 passed in 1.20s ===========================
"""

_MAU_XANH = "1056 passed, 9 deselected, 1 warning in 115.06s (0:01:55)\n"


def test_doc_dung_ten_test_do() -> None:
    assert ten_test_that_bai(_MAU_DO) == [
        "tests/test_a.py::test_mot",
        "tests/test_b.py::test_hai",
        "tests/test_c.py::test_ba",
    ]


def test_KHONG_kem_traceback() -> None:
    """Mười annotation đầy traceback che mất chín kết luận khác. Tên test
    là thứ đủ để biết đi đọc log ở đâu."""
    assert all("AssertionError" not in t for t in ten_test_that_bai(_MAU_DO))


def test_xanh_thi_khong_co_ten_test() -> None:
    assert ten_test_that_bai(_MAU_XANH) == []


def test_bao_cao_do_phat_error(capsys: pytest.CaptureFixture[str]) -> None:
    bao_cao_pytest(_MAU_DO, nhan="py3.11")
    ra = capsys.readouterr().out

    assert ra.startswith("::error title=PYTEST FAILED py3.11::")
    assert "test_mot" in ra


def test_bao_cao_xanh_phat_notice_kem_dong_tong(capsys: pytest.CaptureFixture[str]) -> None:
    bao_cao_pytest(_MAU_XANH, nhan="py3.9")
    ra = capsys.readouterr().out

    assert "::notice title=PYTEST OK py3.9::" in ra
    assert "1056 passed" in ra


def test_khong_doc_duoc_dong_tong_van_bao_cao(capsys: pytest.CaptureFixture[str]) -> None:
    """Một bộ báo cáo im lặng vì không parse được là đúng chế độ hỏng nó
    sinh ra để chống."""
    bao_cao_pytest("pytest chet giua chung, khong co dong tong ket", nhan="x")

    assert "::notice" in capsys.readouterr().out


# ----------------------------------------------------------------------
# Step summary
# ----------------------------------------------------------------------


def test_summary_ghi_noi_tiep_khong_ghi_de(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(f))

    them_summary("### A")
    them_summary("### B")

    noi = f.read_text(encoding="utf-8")
    assert "### A" in noi and "### B" in noi


def test_summary_ngoai_CI_tra_False(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert them_summary("### X") is False


# ----------------------------------------------------------------------
# ci.yml — cú pháp shell của MỌI bước
# ----------------------------------------------------------------------


def _cac_buoc_run() -> list[tuple[str, str, str]]:
    d = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    return [
        (job, st.get("name") or "(run)", st["run"])
        for job, j in d["jobs"].items()
        for st in j["steps"]
        if st.get("run")
    ]


@pytest.mark.parametrize("job,ten,kich_ban", _cac_buoc_run())
def test_moi_buoc_run_dung_cu_phap_bash(job: str, ten: str, kich_ban: str) -> None:
    """`bash -n` từng bước.

    Viết ra vì đã mất một vòng vì nó: một lần sửa `ci.yml` bằng script tự
    động làm hỏng thụt lề của hai khối `{ ... }`, và YAML vẫn parse được —
    lỗi chỉ lộ ra khi runner chạy. Một phép kiểm 200ms ở local thay cho
    một vòng push-chờ-đọc-log.

    `${{ }}` được thay bằng chuỗi giả: đó là cú pháp GitHub, không phải
    bash, và runner đã thay nó trước khi shell nhìn thấy.
    """
    kb = re.sub(r"\$\{\{[^}]*\}\}", "XX", kich_ban)
    p = subprocess.run(["bash", "-n"], input=kb, capture_output=True, text=True)

    assert p.returncode == 0, f"{job} / {ten}:\n{p.stderr}"


def test_moi_phep_do_quan_trong_deu_ra_NGOAI_log() -> None:
    """Khẳng định trung tâm của sửa chữa quy trình này.

    Ba phép đo đã từng chặn cả phiên vì kết quả chỉ nằm trong log. Nếu ai
    đó gỡ `ci_bao_cao` khỏi một trong ba, nó lại thành vô hình — và sự vô
    hình đó không tự báo.
    """
    lenh = " ".join(k for _, _, k in _cac_buoc_run())

    assert "--tu-pytest" in lenh, "kết quả pytest không ra ngoài log"
    assert '--notice "CHAN DOAN E"' in lenh, "chẩn đoán §E không ra ngoài log"
    assert "--summary" in lenh, "không có gì vào step summary"


_LOG_PYTEST = re.compile(r"^\s*pytest\b[^\n]*>\s*(\S+\.log)\b", re.M)


def test_MOI_lan_chay_pytest_that_deu_duoc_bao_cao() -> None:
    """Ràng buộc CẤU TRÚC, không phải "có ít nhất một".

    Bản đầu chỉ kiểm `"--tu-pytest" in lenh`, và đột biến "gỡ báo cáo
    pytest khỏi job fast" SỐNG SÓT — vì job slow-gate vẫn còn một cái. Một
    trong hai job mù mà cổng vẫn xanh, đúng chế độ hỏng file này gác.

    Quy tắc: mỗi `pytest ... > X.log` phải đi kèm `--tu-pytest X.log`
    TRONG CÙNG bước. `pytest --collect-only` và `pytest -q -rs` ở bước
    PHẠM VI không tính — chúng đếm test, không phán xét test.
    """
    cap = [
        (job, ten, m.group(1))
        for job, ten, kb in _cac_buoc_run()
        for m in _LOG_PYTEST.finditer(kb)
    ]

    assert len(cap) >= 2, (
        f"chỉ thấy {len(cap)} lần chạy pytest ghi ra .log — phải có ít nhất "
        "hai (bộ mặc định của job fast, và bộ slow của slow-gate)"
    )

    theo_buoc = {(job, ten): kb for job, ten, kb in _cac_buoc_run()}
    for job, ten, tep_log in cap:
        kb = theo_buoc[(job, ten)]
        assert f"--tu-pytest {tep_log}" in kb, (
            f"{job} / {ten}: chạy pytest ghi ra {tep_log} nhưng KHÔNG báo cáo "
            f"kết quả ra ngoài log. Job này sẽ đỏ mà không ai biết test nào đỏ."
        )


def test_ca_HAI_job_deu_bao_cao_pytest() -> None:
    """Bất đối xứng py3.9/py3.11 chỉ chẩn đoán được nếu CẢ HAI job phát
    ra tên test đỏ. Một job im lặng biến một bất đối xứng đo được thành
    một vòng đoán mò."""
    job_co_bao_cao = {job for job, _, kb in _cac_buoc_run() if "--tu-pytest" in kb}

    assert job_co_bao_cao == {"fast", "slow-gate"}, f"chỉ {job_co_bao_cao} báo cáo"


def test_tat_dinh_noi_may_tu_phat_annotation() -> None:
    """Bước này gọi thẳng `ops/kiem_tat_dinh.py`, nên annotation phải do
    chính công cụ phát — không có chỗ nào trong `ci.yml` phát hộ."""
    from ops import kiem_tat_dinh

    src = Path(kiem_tat_dinh.__file__).read_text(encoding="utf-8")

    assert "TAT DINH NOI MAY" in src
    assert "DAU VAN TAY" in src


# ----------------------------------------------------------------------
# ĐẦU-CUỐI: chạy ĐÚNG kịch bản của bước CI với một `pytest` giả ĐỎ
# ----------------------------------------------------------------------
#
# Câu hỏi mà mọi test ở trên KHÔNG trả lời được: khi pytest đỏ thật, bước
# CI có phát `::error title=PYTEST FAILED` không? Các test trên gọi
# `bao_cao_pytest()` trực tiếp — chúng chứng minh HÀM đúng, không chứng
# minh BƯỚC gọi hàm đó. Cùng lỗ hổng đã làm đột biến "bỏ khẳng định trong
# select_and_train" sống sót, và cùng mẫu hỏng của cổng §E: một cơ chế
# đúng nhưng không được nối vào đường thật.
#
# Trang run của `2f1c961` hiện CHAN DOAN E nhưng KHÔNG hiện PYTEST FAILED
# dù job py3.11 đỏ. Đây là phép kiểm để phân biệt "kênh hỏng" với "pytest
# chưa từng chạy vì một bước trước đó đã đỏ".

_PYTEST_GIA = """#!/bin/bash
# `--collect-only` chỉ đếm; đỏ ở đó không phải chuyện file này kiểm.
for a in "$@"; do [ "$a" = "--collect-only" ] && { echo "3 tests collected"; exit 0; }; done
cat <<'EOF'
FAILED tests/test_gia.py::test_mot - AssertionError: chi tiet
FAILED tests/test_gia.py::test_hai - ValueError
=========================== 2 failed, 1 passed in 0.10s ===========================
EOF
exit 1
"""


def _buoc_chay_pytest_that() -> list[tuple[str, str, str]]:
    return [
        (job, ten, kb)
        for job, ten, kb in _cac_buoc_run()
        if _LOG_PYTEST.search(kb)
    ]


@pytest.mark.parametrize("job,ten,kich_ban", _buoc_chay_pytest_that())
def test_buoc_CI_phat_annotation_khi_pytest_DO(
    job: str, ten: str, kich_ban: str, tmp_path: Path
) -> None:
    """Thi hành kịch bản thật của bước, với `pytest` giả luôn đỏ.

    Không mock `bao_cao_pytest`, không đọc `ci.yml` bằng chuỗi: chạy đúng
    những dòng runner sẽ chạy, rồi đọc stdout và mã thoát.
    """
    bin_gia = tmp_path / "bin"
    bin_gia.mkdir()
    (bin_gia / "pytest").write_text(_PYTEST_GIA)
    (bin_gia / "pytest").chmod(0o755)
    # `python` có thể không tồn tại trên máy dev (chỉ có `python3`); runner
    # Ubuntu thì có. Cầu nối để kịch bản chạy được ở cả hai nơi.
    (bin_gia / "python").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    (bin_gia / "python").chmod(0o755)
    (tmp_path / "ops").symlink_to(_ROOT / "ops")

    kb = re.sub(r"\$\{\{[^}]*\}\}", "3.11", kich_ban)
    p = subprocess.run(
        ["bash", "-e", "-c", kb],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_gia}:{os.environ['PATH']}"},
    )

    assert "::error title=PYTEST FAILED" in p.stdout, (
        f"{job} / {ten}: pytest ĐỎ nhưng bước KHÔNG phát annotation.\n"
        f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
    )
    assert "test_mot" in p.stdout, "annotation không kèm tên test đỏ"
    assert p.returncode == 1, (
        f"{job} / {ten}: bước phải GIỮ mã thoát của pytest. Một bộ báo cáo "
        f"nuốt mã thoát biến job đỏ thành job xanh — thấy {p.returncode}."
    )


@pytest.mark.parametrize("job,ten,kich_ban", _buoc_chay_pytest_that())
def test_buoc_CI_van_bao_cao_khi_pytest_XANH(
    job: str, ten: str, kich_ban: str, tmp_path: Path
) -> None:
    """Chiều còn lại. Một kênh chỉ phát khi đỏ không phân biệt được
    "xanh" với "bước chưa từng chạy" — và phân biệt đúng hai thứ đó là
    câu hỏi đang mở về `2f1c961`."""
    bin_gia = tmp_path / "bin"
    bin_gia.mkdir()
    (bin_gia / "pytest").write_text(
        '#!/bin/bash\necho "1088 passed, 9 deselected in 110.57s"\nexit 0\n'
    )
    (bin_gia / "pytest").chmod(0o755)
    (bin_gia / "python").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    (bin_gia / "python").chmod(0o755)
    (tmp_path / "ops").symlink_to(_ROOT / "ops")

    kb = re.sub(r"\$\{\{[^}]*\}\}", "3.11", kich_ban)
    p = subprocess.run(
        ["bash", "-e", "-c", kb], cwd=tmp_path, capture_output=True, text=True,
        timeout=60, env={**os.environ, "PATH": f"{bin_gia}:{os.environ['PATH']}"},
    )

    assert "::notice title=PYTEST OK" in p.stdout, p.stdout
    assert p.returncode == 0


# ----------------------------------------------------------------------
# ruff / mypy — bước đỏ TRƯỚC pytest phải tự báo tên nó
# ----------------------------------------------------------------------

_TOOL_LOG = re.compile(r"^\s*(ruff|mypy)\b[^\n]*>\s*(\S+\.log)\b", re.M)


def _buoc_cong_cu() -> list[tuple[str, str, str, str]]:
    return [
        (job, ten, m.group(1), kb)
        for job, ten, kb in _cac_buoc_run()
        for m in _TOOL_LOG.finditer(kb)
    ]


def test_ruff_va_mypy_deu_co_buoc_bao_cao() -> None:
    """`ruff` và `mypy` chạy TRƯỚC `pytest`. Một job đỏ ở mypy thì pytest
    KHÔNG BAO GIỜ CHẠY — trang run không có annotation nào, và trông y hệt
    trường hợp kênh báo cáo hỏng.

    Đó là lần thứ tư của mẫu "lỗi bị che bởi lỗi đứng trước" trong dự án
    này; ba lần trước: `cmd | tail` nuốt mã thoát, bước PHẠM VI không chạy
    vì pytest đỏ trước, và `test_snapshot` gọi mạng mà job không đỏ vì
    mypy chết trước.
    """
    cong_cu = {t for _, _, t, _ in _buoc_cong_cu()}

    assert cong_cu == {"ruff", "mypy"}, f"thiếu báo cáo cho: {{'ruff','mypy'}} - {cong_cu}"


@pytest.mark.parametrize("job,ten,cong_cu,kich_ban", _buoc_cong_cu())
def test_buoc_cong_cu_phat_annotation_khi_DO(
    job: str, ten: str, cong_cu: str, kich_ban: str, tmp_path: Path
) -> None:
    """Chạy thật kịch bản bước, với `ruff`/`mypy` giả luôn đỏ."""
    bin_gia = tmp_path / "bin"
    bin_gia.mkdir()
    for t in ("ruff", "mypy"):
        (bin_gia / t).write_text(
            f'#!/bin/bash\necho "{t}: dong loi dau tien"\necho "{t}: dong hai"\nexit 1\n'
        )
        (bin_gia / t).chmod(0o755)
    (bin_gia / "python").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    (bin_gia / "python").chmod(0o755)
    (tmp_path / "ops").symlink_to(_ROOT / "ops")

    kb = re.sub(r"\$\{\{[^}]*\}\}", "3.11", kich_ban)
    p = subprocess.run(
        ["bash", "-e", "-c", kb], cwd=tmp_path, capture_output=True, text=True,
        timeout=60, env={**os.environ, "PATH": f"{bin_gia}:{os.environ['PATH']}"},
    )

    assert "::error title=" in p.stdout and "FAILED" in p.stdout, (
        f"{job} / {ten}: {cong_cu} ĐỎ nhưng bước KHÔNG phát annotation.\n{p.stdout}"
    )
    assert "dong loi dau tien" in p.stdout, "không kèm dòng lỗi đầu tiên"
    assert p.returncode == 1, f"bước phải giữ mã thoát, thấy {p.returncode}"


def test_cong_cu_cat_o_DAU_khong_o_cuoi(capsys: pytest.CaptureFixture[str]) -> None:
    """Ngược với pytest: output của ruff/mypy có phần dùng được ở ĐẦU
    (dòng lỗi đầu tiên), pytest có kết luận ở CUỐI. Cắt nhầm chiều là mất
    đúng thứ cần đọc."""
    from ops.ci_bao_cao import bao_cao_cong_cu

    bao_cao_cong_cu(
        "\n".join(["DONG-DAU-TIEN"] + [f"rac {i}" for i in range(500)]),
        ten="MYPY", ma_thoat=1,
    )
    ra = capsys.readouterr().out

    assert "DONG-DAU-TIEN" in ra
    assert "còn 48" in ra or "còn " in ra


def test_dau_van_tay_chay_o_job_fast_ca_hai_phien_ban() -> None:
    """Dấu vân tay tốn ~0 giây và là dữ liệu NỀN. Để nó trong slow-gate
    nghĩa là nó vắng mặt ở đúng những lần chạy cần nó nhất — mọi lần diff
    không chạm `core/`.

    Job `fast` chạy matrix 3.9 + 3.11, nên đặt ở đó cũng cho hai bản ghi
    so được với nhau: bất đối xứng giữa hai phiên bản có thể nằm ngay
    trong bảng này.
    """
    d = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    fast = d["jobs"]["fast"]

    lenh = " ".join(s.get("run", "") for s in fast["steps"])
    assert "kiem_tat_dinh.py --runs 0" in lenh, "job fast không in dấu vân tay"

    ver = fast["strategy"]["matrix"]["python-version"]
    assert set(ver) == {"3.9", "3.11"}, f"matrix đổi thành {ver} — cập nhật test này"


def test_do_tat_dinh_2_lan_van_o_slow_gate() -> None:
    """~4 phút. Đúng chỗ của nó — không kéo job fast dài ra."""
    d = yaml.safe_load(_CI.read_text(encoding="utf-8"))

    fast = " ".join(s.get("run", "") for s in d["jobs"]["fast"]["steps"])
    slow = " ".join(s.get("run", "") for s in d["jobs"]["slow-gate"]["steps"])

    assert "--runs 2" in slow
    assert "--runs 2" not in fast, "phép đo 4 phút lọt vào job chạy mọi commit"
