"""Cổng §E — `ops/readiness_gate.py`.

Cổng này tồn tại vì một lời giải thích chỉ có tác dụng với người đã đọc
nó. Nhưng một cổng KHÔNG ĐƯỢC KIỂM CHỨNG còn tệ hơn: nó tạo cảm giác an
toàn mà không có gì đằng sau — đúng chế độ hỏng chủ đạo của dự án này
(CLAUDE.md #16, lỗi xác minh, ba lần đã xảy ra).

Nên mọi test ở đây dựng REPO GIẢ trong `tmp_path` (`git init` thật, commit
thật) thay vì mock `subprocess`. Mock `git diff` nghĩa là kiểm bản mô
phỏng của git trong đầu mình, không phải git.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops.readiness_gate import (
    GATED_PREFIXES,
    RECEIPT_VERSION,
    changed_files,
    check,
    gated_source_digest,
    read_receipt,
    slow_required,
    write_receipt,
)

# ----------------------------------------------------------------------
# Phạm vi cổng — hàm thuần, không cần git
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "core/regime_strategies.py",
        "core/trend_gate.py",
        "core/signal_generator.py",
        "core/hmm_engine.py",
        "backtest/backtester.py",
    ],
)
def test_bon_file_va_backtest_deu_trong_pham_vi(path: str) -> None:
    """Đúng năm mục quy tắc gốc nêu tên. Liệt kê từng cái thay vì tin vào
    tiền tố: nếu ai đó thu hẹp `GATED_PREFIXES`, test này chỉ ra ĐÚNG mục
    nào vừa rơi ra ngoài."""
    assert slow_required([path]) == (path,)


def test_pham_vi_rong_hon_danh_sach_bon_file() -> None:
    """`core/risk_manager.py` KHÔNG nằm trong danh sách bốn file của quy
    tắc gốc, nhưng phép kiểm đi kèm (`grep -E '^(core|backtest)/'`) bắt nó
    — và bản rộng hơn là bản được chọn, có chủ ý.

    Lý do: một danh sách tên file phải cập nhật tay mỗi lần thêm module vào
    `core/`, và lần quên đầu tiên sẽ im lặng. `risk_manager` cũng là một
    tầng trong `min(hmm, trend_gate, risk)` nên nó thuộc về phạm vi này.
    """
    assert slow_required(["core/risk_manager.py"]) == ("core/risk_manager.py",)


@pytest.mark.parametrize(
    "path",
    ["monitoring/health.py", "ops/RUNBOOK.md", "tests/test_health.py", "docs/STATE.md", "main.py"],
)
def test_ngoai_pham_vi_khong_kich_hoat(path: str) -> None:
    """Cổng phải HẸP đúng mức. Bắt buộc chạy slow (137s) cho mọi thay đổi
    tài liệu sẽ làm người ta tìm cách đi vòng, và một cổng bị đi vòng
    không bảo vệ gì cả."""
    assert slow_required([path]) == ()


def test_chi_lay_dung_file_trong_pham_vi() -> None:
    changed = ["docs/STATE.md", "core/trend_gate.py", "main.py", "backtest/metrics.py"]

    assert slow_required(changed) == ("backtest/metrics.py", "core/trend_gate.py")


def test_tien_to_khong_bat_ten_gan_giong() -> None:
    """`core_utils/` hay `backtesting/` KHÔNG phải `core/`/`backtest/`.
    Tiền tố có dấu `/` chính vì điều này."""
    assert slow_required(["core_utils/x.py", "backtesting/y.py"]) == ()


def test_tien_to_dung_nhu_quy_tac() -> None:
    assert GATED_PREFIXES == ("core/", "backtest/")


# ----------------------------------------------------------------------
# Băm nội dung — bằng chứng "đã chạy slow" gắn với MÃ, không với commit
# ----------------------------------------------------------------------


def _fake_repo(root: Path) -> Path:
    (root / "core").mkdir(parents=True)
    (root / "backtest").mkdir(parents=True)
    (root / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "backtest" / "b.py").write_text("y = 2\n", encoding="utf-8")
    return root


def test_bam_on_dinh_giua_hai_lan_goi(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    assert gated_source_digest(repo_root=repo) == gated_source_digest(repo_root=repo)


def test_doi_noi_dung_thi_bam_doi(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    truoc = gated_source_digest(repo_root=repo)

    (repo / "core" / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert gated_source_digest(repo_root=repo) != truoc


def test_doi_ten_file_thi_bam_doi(tmp_path: Path) -> None:
    """Băm cả ĐƯỜNG DẪN, không chỉ nội dung: xoá một file và thêm một file
    khác cùng nội dung là thay đổi thật."""
    repo = _fake_repo(tmp_path)
    truoc = gated_source_digest(repo_root=repo)

    (repo / "core" / "a.py").rename(repo / "core" / "a_doi_ten.py")

    assert gated_source_digest(repo_root=repo) != truoc


def test_file_ngoai_pham_vi_khong_vao_bam(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    truoc = gated_source_digest(repo_root=repo)

    (repo / "monitoring").mkdir()
    (repo / "monitoring" / "c.py").write_text("z = 3\n", encoding="utf-8")

    assert gated_source_digest(repo_root=repo) == truoc


# ----------------------------------------------------------------------
# Biên lai
# ----------------------------------------------------------------------


def test_bien_lai_ghi_roi_doc_lai_duoc(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    path = write_receipt(repo_root=repo, slow_tests=3)
    data = read_receipt(path)

    assert data is not None
    assert data["gated_digest"] == gated_source_digest(repo_root=repo)
    assert data["slow_tests_passed"] == 3


def test_khong_co_bien_lai_thi_none(tmp_path: Path) -> None:
    assert read_receipt(tmp_path / "khong-ton-tai.json") is None


def test_bien_lai_hong_thi_none(tmp_path: Path) -> None:
    """JSON hỏng -> coi như CHƯA chạy. Một cổng nghi ngờ phải nghiêng về
    phía chặn, không phải phía cho qua."""
    bad = tmp_path / "r.json"
    bad.write_text("{ khong phai json", encoding="utf-8")

    assert read_receipt(bad) is None


def test_bien_lai_sai_phien_ban_thi_none(tmp_path: Path) -> None:
    old = tmp_path / "r.json"
    old.write_text(json.dumps({"version": RECEIPT_VERSION + 99, "gated_digest": "x"}), encoding="utf-8")

    assert read_receipt(old) is None


# ----------------------------------------------------------------------
# `check()` đầu-cuối, trên REPO GIT THẬT dựng trong tmp_path
# ----------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo git thật với một commit gốc. `git init` thật thay vì mock
    `subprocess`: mock nghĩa là kiểm bản mô phỏng git trong đầu mình."""
    root = _fake_repo(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "goc")
    return root


def test_diff_khong_cham_thi_pass_du_khong_co_bien_lai(repo: Path) -> None:
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("tai lieu\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chi doi tai lieu")

    result = check("HEAD~1", repo_root=repo, receipt_path=repo / "khong-co.json")

    assert result.ok
    assert not result.slow_required


def test_diff_cham_ma_khong_co_bien_lai_thi_FAIL(repo: Path) -> None:
    """Khẳng định trung tâm của cổng."""
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "doi core")

    result = check("HEAD~1", repo_root=repo, receipt_path=repo / "khong-co.json")

    assert not result.ok
    assert result.slow_required
    assert result.changed == ("core/a.py",)
    assert "pytest -m slow" in result.detail


def test_diff_cham_va_co_bien_lai_dung_thi_PASS(repo: Path) -> None:
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "doi core")
    receipt = repo / "r.json"
    write_receipt(repo_root=repo, path=receipt, slow_tests=2)

    result = check("HEAD~1", repo_root=repo, receipt_path=receipt)

    assert result.ok
    assert result.slow_required


def test_bien_lai_cu_hon_ma_nguon_thi_FAIL(repo: Path) -> None:
    """Chạy slow xong RỒI MỚI sửa `core/` — biên lai vẫn tồn tại nhưng đã
    vô giá trị. Đây là lý do bằng chứng gắn với BĂM NỘI DUNG chứ không với
    commit SHA: một biên lai "khớp HEAD" vẫn có thể mô tả mã đã cũ."""
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "doi core")
    receipt = repo / "r.json"
    write_receipt(repo_root=repo, path=receipt, slow_tests=2)

    (repo / "core" / "a.py").write_text("x = 100  # sua tiep sau khi chay slow\n", encoding="utf-8")

    result = check("HEAD~1", repo_root=repo, receipt_path=receipt)

    assert not result.ok
    assert "ĐÃ CŨ" in result.detail


def test_thay_doi_ngoai_pham_vi_khong_lam_bien_lai_het_han(repo: Path) -> None:
    """Sửa tài liệu sau khi chạy slow KHÔNG được làm biên lai mất hiệu lực
    — nếu có, mọi PR sẽ phải chạy slow lần hai chỉ vì sửa một dòng README,
    và người ta sẽ tìm cách đi vòng."""
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "doi core")
    receipt = repo / "r.json"
    write_receipt(repo_root=repo, path=receipt, slow_tests=2)

    (repo / "README.md").write_text("tai lieu\n", encoding="utf-8")

    assert check("HEAD~1", repo_root=repo, receipt_path=receipt).ok


def test_base_khong_ton_tai_thi_raise_chu_khong_im_lang_cho_qua(repo: Path) -> None:
    """Một cổng không kiểm được phải HỎNG TO, không được trả "pass". Đây
    chính là chế độ hỏng đã xảy ra ba lần trong dự án này (health_check gọi
    nhầm endpoint, health_check hardcode sàn cũ, pipe nuốt exit code)."""
    with pytest.raises(RuntimeError, match="git diff thất bại"):
        check("khong-phai-ref-nao-ca", repo_root=repo, receipt_path=repo / "r.json")


def test_changed_files_doc_dung_diff(repo: Path) -> None:
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    (repo / "README.md").write_text("t\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "hai file")

    assert set(changed_files("HEAD~1", repo_root=repo)) == {"core/a.py", "README.md"}


def test_report_in_ra_ten_file(repo: Path) -> None:
    """Khi cổng FAIL, thứ người đọc cần đầu tiên là "file nào"."""
    (repo / "core" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "doi core")

    report = check("HEAD~1", repo_root=repo, receipt_path=repo / "khong-co.json").report()

    assert "FAIL" in report
    assert "core/a.py" in report


# ----------------------------------------------------------------------
# Nối dây: CI và tài liệu phải dùng đúng phép kiểm này
# ----------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent


def test_ci_dung_dung_bieu_thuc_grep() -> None:
    """CI và `GATED_PREFIXES` phải mô tả CÙNG một phạm vi. Hai nguồn sự
    thật cho một quy tắc sẽ lệch nhau, và bên lỏng hơn âm thầm quyết định
    mức bảo vệ thật."""
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "^(core|backtest)/" in ci
    assert "pytest -m slow" in ci
    assert "ops/readiness_gate.py" in ci


def test_ci_checkout_day_du_lich_su() -> None:
    """`git diff <base>..HEAD` cần `base` có trong repo. Một checkout nông
    làm cổng hỏng theo kiểu "không kiểm được" — và job vẫn xanh."""
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in ci


def test_readiness_gate_co_trong_tai_lieu() -> None:
    doc = (_ROOT / "docs" / "READINESS_GATE.md").read_text(encoding="utf-8")

    assert "ops/readiness_gate.py" in doc
    assert "pytest -m slow" in doc
