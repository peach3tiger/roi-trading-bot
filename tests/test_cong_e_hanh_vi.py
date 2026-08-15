"""Cổng §E — chạy THẬT kịch bản shell, không đọc văn bản YAML.

## Vì sao file này tồn tại tách khỏi `test_readiness_gate.py`

Mọi phép kiểm cổng §E cho tới nay đều đọc `ci.yml` như một TÀI LIỆU: có
chuỗi này không, hai bước có cùng `if` không, `fetch-depth` bằng mấy. Cả
bộ đó xanh suốt trong khi cổng **chưa từng gác gì** — nó luôn kết luận
"diff không chạm tầng quyết định", trên mọi commit.

Một test ghim văn bản không thể bắt được điều đó, vì văn bản KHÔNG SAI:
từng dòng của bản cũ đều đúng cú pháp và đúng ý định. Sai nằm ở HÀNH VI
khi ghép chúng lại và cho chạy.

Nên file này làm đúng một việc: lấy `run:` của bước dò ra khỏi `ci.yml`,
thay các biểu thức `${{ }}` mà GitHub sẽ thay, rồi **thi hành nó bằng
bash trong một repo git dựng tạm** và đọc `$GITHUB_OUTPUT` + exit code.
Đó là thứ runner thật làm.

Hệ quả cho thiết kế: các test ở đây KHÔNG đỏ khi ai đó đổi định dạng,
đổi tên bước, thêm comment, hay viết lại logic bằng cách khác. Chúng chỉ
đỏ khi cổng KẾT LUẬN SAI.

## Ranh giới — thứ file này KHÔNG kiểm được

`if:` của các bước sau là do GitHub đánh giá, không phải bash; ở đó VĂN
BẢN chính là hành vi, và nó thuộc `test_readiness_gate.py`. Cũng không
kiểm được `actions/checkout` fetch bao nhiêu lịch sử — repo tạm ở đây
luôn đầy đủ. CLAUDE.md #19: đây là phạm vi đã kiểm, không phải "sạch".

Và một điểm mù ĐÃ ĐO, không suy: đột biến "`git diff` đi vào pipe, lỗi bị
nuốt" SỐNG SÓT qua toàn bộ file này. Nguyên nhân không phải test yếu —
`resolve()` đã xác nhận `$BASE` là commit thật TRƯỚC khi diff chạy, nên
trong mọi kịch bản dựng được ở đây `git diff` không có đường thất bại.
Lớp `if ! CHANGED=$(...)` là phòng thủ chiều sâu cho một tình huống mà
chuỗi ba nguồn mốc đã loại trừ. Nó được ghim bằng test CẤU TRÚC
`test_git_diff_khong_bao_gio_di_thang_vao_pipe` (đã nghiệm bằng đột biến,
ĐỎ), không bằng hành vi — và đó là chỗ đúng cho nó.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_ZERO = "0" * 40


def _kich_ban_do_pham_vi() -> str:
    """`run:` của bước `id: scope`, lấy từ chính `ci.yml`.

    Trích từ nguồn thật chứ không chép lại: một bản sao sẽ trôi lệch, và
    lúc đó test xanh chỉ nghĩa là bản sao đồng ý với chính nó.
    """
    wf = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    buoc = [s for s in wf["jobs"]["slow-gate"]["steps"] if s.get("id") == "scope"]
    assert len(buoc) == 1, f"cần đúng 1 bước id=scope trong slow-gate, thấy {len(buoc)}"
    return buoc[0]["run"]


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return r.stdout.strip()


def _dung_repo(tmp: Path, *, co_origin: bool = True) -> Path:
    """Repo có `main` trên một remote thật.

    `origin` phải là remote THẬT: kịch bản chạy `git fetch origin main`
    trước khi thử `merge-base`, nên một `refs/remotes/origin/main` dựng
    bằng `update-ref` sẽ không đi tới nhánh đó — chuỗi `&&` đứt ở fetch.
    """
    goc = tmp / "origin.git"
    lam = tmp / "work"
    lam.mkdir()
    _git(lam, "init", "-q", "-b", "main")
    (lam / "core").mkdir()
    (lam / "docs").mkdir()
    (lam / "core" / "a.py").write_text("x = 1\n")
    (lam / "docs" / "a.md").write_text("tài liệu\n")
    _git(lam, "add", "-A")
    _git(lam, "commit", "-qm", "nền")
    if co_origin:
        subprocess.run(["git", "init", "-q", "--bare", str(goc)], check=True)
        _git(lam, "remote", "add", "origin", str(goc))
        _git(lam, "push", "-q", "origin", "main")
        _git(lam, "fetch", "-q", "origin")
    return lam


def _them_commit(repo: Path, duong_dan: str, noi_dung: str) -> str:
    p = repo / duong_dan
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(noi_dung)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"đổi {duong_dan}")
    return _git(repo, "rev-parse", "HEAD")


class _KetQua:
    def __init__(self, ma: int, ra: str, loi: str, touched: Optional[str]) -> None:
        self.exit_code = ma
        self.stdout = ra
        self.stderr = loi
        self.touched = touched

    def __repr__(self) -> str:  # pragma: no cover — chỉ để đọc lúc test đỏ
        return f"<exit={self.exit_code} touched={self.touched!r}\n{self.stdout}\n{self.stderr}>"


def _chay_cong(repo: Path, tmp: Path, *, before: str) -> _KetQua:
    """Thi hành bước dò y như runner: bash, `$GITHUB_OUTPUT`, cùng cwd."""
    kb = _kich_ban_do_pham_vi().replace("${{ github.event.before }}", before)
    out = tmp / "gh_output"
    out.write_text("")
    r = subprocess.run(
        ["bash", "-c", kb], cwd=repo, capture_output=True, text=True, timeout=60,
        env={**os.environ, "GITHUB_OUTPUT": str(out)},
    )
    kv = dict(
        d.split("=", 1) for d in out.read_text().splitlines() if "=" in d
    )
    return _KetQua(r.returncode, r.stdout, r.stderr, kv.get("touched"))


# ----------------------------------------------------------------------
# Hai chiều — cổng phải PHÂN BIỆT ĐƯỢC, không chỉ "chạy"
# ----------------------------------------------------------------------


def test_diff_cham_core_thi_touched_true(tmp_path: Path) -> None:
    """Chiều (a). Đây chính xác là điều bản cũ KHÔNG làm được: nó trả
    `false` ở đây, trên mọi commit, kể từ khi được viết."""
    repo = _dung_repo(tmp_path)
    truoc = _git(repo, "rev-parse", "HEAD")
    _them_commit(repo, "core/a.py", "x = 2\n")

    kq = _chay_cong(repo, tmp_path, before=truoc)

    assert kq.exit_code == 0, kq
    assert kq.touched == "true", kq


def test_diff_cham_backtest_cung_tinh(tmp_path: Path) -> None:
    """`GATED_PREFIXES` có hai tiền tố; một test chỉ kiểm `core/` sẽ để
    `backtest/` trôi ra ngoài mà vẫn xanh."""
    repo = _dung_repo(tmp_path)
    truoc = _git(repo, "rev-parse", "HEAD")
    _them_commit(repo, "backtest/engine.py", "y = 1\n")

    assert _chay_cong(repo, tmp_path, before=truoc).touched == "true"


def test_diff_chi_cham_docs_thi_touched_false(tmp_path: Path) -> None:
    """Chiều (b). Một cổng LUÔN chạy cũng vô dụng như một cổng không bao
    giờ chạy — nó sẽ bị tắt trong tuần đầu vì mọi PR sửa README đều phải
    chờ ba phút test chậm."""
    repo = _dung_repo(tmp_path)
    truoc = _git(repo, "rev-parse", "HEAD")
    _them_commit(repo, "docs/a.md", "tài liệu mới\n")

    kq = _chay_cong(repo, tmp_path, before=truoc)

    assert kq.exit_code == 0, kq
    assert kq.touched == "false", kq


def test_ten_file_chi_CHUA_core_khong_tinh(tmp_path: Path) -> None:
    """Biểu thức phải neo đầu chuỗi. `monitoring/core_utils.py` không nằm
    trong tầng quyết định; một cổng bắt nhầm nó sẽ chạy test chậm vô cớ và
    mất uy tín nhanh hơn cả một cổng lỏng."""
    repo = _dung_repo(tmp_path)
    truoc = _git(repo, "rev-parse", "HEAD")
    _them_commit(repo, "monitoring/core_utils.py", "z = 1\n")

    assert _chay_cong(repo, tmp_path, before=truoc).touched == "false"


# ----------------------------------------------------------------------
# Ba nguồn mốc so — mỗi cái là một chế độ hỏng đã gặp thật
# ----------------------------------------------------------------------


def test_before_bon_muoi_so_khong_thi_lui_ve_merge_base(tmp_path: Path) -> None:
    """Push ĐẦU TIÊN vào một nhánh: GitHub gửi `before` = 40 số 0. Không
    có lớp lùi thì cổng mù đúng lúc nhánh mới ra đời — tức là đúng lúc
    người ta hay sửa `core/` nhất."""
    repo = _dung_repo(tmp_path)
    _git(repo, "checkout", "-qb", "nhanh-moi")
    _them_commit(repo, "core/a.py", "x = 3\n")

    kq = _chay_cong(repo, tmp_path, before=_ZERO)

    assert kq.exit_code == 0, kq
    assert kq.touched == "true", kq


def test_before_mo_coi_sau_force_push_thi_lui_ve_merge_base(tmp_path: Path) -> None:
    """Sau `push -f`, `before` trỏ tới commit đã MỒ CÔI. `fetch-depth: 0`
    chỉ lấy lịch sử REACHABLE, nên SHA đó không có trong clone của runner
    dù nó từng tồn tại — `git diff` chết, và bản cũ đọc cái chết đó thành
    "không chạm"."""
    repo = _dung_repo(tmp_path)
    _git(repo, "checkout", "-qb", "nhanh-moi")
    _them_commit(repo, "core/a.py", "x = 4\n")
    mo_coi = "d" * 40  # SHA đúng định dạng, không tồn tại trong repo

    kq = _chay_cong(repo, tmp_path, before=mo_coi)

    assert kq.exit_code == 0, kq
    assert kq.touched == "true", kq


def test_merge_base_PHAN_BIET_duoc_voi_HEAD_truoc(tmp_path: Path) -> None:
    """Lớp `merge-base` phải làm được thứ `HEAD~1` KHÔNG làm được.

    Nhánh có HAI commit: cái đầu chạm `core/`, cái sau chỉ chạm `docs/`.
        HEAD~1     -> so với commit đầu -> chỉ thấy docs/  -> "không chạm"
        merge-base -> so với gốc nhánh  -> thấy cả core/   -> "có chạm"
    Chỉ câu trả lời thứ hai đúng với thứ cổng bảo vệ: **hợp nhất nhánh
    này vào main SẼ đổi `core/`.**

    Không có kịch bản này thì đột biến "bỏ lớp merge-base" SỐNG SÓT — đã
    đo, vòng đột biến đầu tiên. Mọi kịch bản khác đều là nhánh một commit,
    ở đó `merge-base` và `HEAD~1` tình cờ cho cùng đáp án.
    """
    repo = _dung_repo(tmp_path)
    _git(repo, "checkout", "-qb", "nhanh-hai-commit")
    _them_commit(repo, "core/a.py", "x = 9\n")
    _them_commit(repo, "docs/a.md", "chỉ tài liệu\n")

    kq = _chay_cong(repo, tmp_path, before=_ZERO)

    assert kq.exit_code == 0, kq
    assert kq.touched == "true", (
        "cổng so với HEAD~1 thay vì gốc nhánh — thay đổi core/ ở commit trước "
        f"trở nên vô hình:\n{kq}"
    )


def test_khong_co_origin_thi_lui_ve_HEAD_truoc(tmp_path: Path) -> None:
    """Lớp lùi cuối. Chưa gặp trên CI, nhưng nó là thứ giữ cho cổng không
    rơi thẳng từ "merge-base hỏng" xuống "exit 1" ở mọi lần chạy."""
    repo = _dung_repo(tmp_path, co_origin=False)
    _them_commit(repo, "core/a.py", "x = 5\n")

    kq = _chay_cong(repo, tmp_path, before=_ZERO)

    assert kq.exit_code == 0, kq
    assert kq.touched == "true", kq


# ----------------------------------------------------------------------
# KHÔNG XÁC ĐỊNH ĐƯỢC phải HỎNG TO — chế độ hỏng CLAUDE.md #19
# ----------------------------------------------------------------------


def test_khong_co_moc_so_nao_thi_EXIT_1_khong_phai_false(tmp_path: Path) -> None:
    """Khẳng định quan trọng nhất của file này.

    Repo một commit, không remote, `before` rỗng: không có mốc nào xác
    định được. Cổng phải ĐỎ. Nếu nó trả `touched=false` thì nó vừa cho
    một thay đổi `core/` đi qua mà không ai chạy `pytest -m slow`, và
    log sẽ ghi "Bỏ qua (diff không chạm tầng quyết định)" — một câu SAI
    trông y hệt một câu đúng.

    Đây là khiếm khuyết đã làm cổng §E vô hình suốt từ lúc viết.
    """
    repo = _dung_repo(tmp_path, co_origin=False)

    kq = _chay_cong(repo, tmp_path, before="")

    assert kq.exit_code == 1, kq
    assert kq.touched != "false", "cổng không kiểm được mà tự nhận là 'sạch'"


@pytest.mark.parametrize("before", ["", _ZERO])
def test_moc_so_hong_khong_bao_gio_thanh_touched_false(tmp_path: Path, before: str) -> None:
    """Tổng quát hoá: với MỌI dạng mốc hỏng, kết luận "không chạm" bị cấm.
    `false` là kết luận duy nhất cho phép bỏ qua test chậm, nên nó phải
    đến từ một phép so THẬT, không từ một lệnh chết."""
    repo = _dung_repo(tmp_path, co_origin=False)

    kq = _chay_cong(repo, tmp_path, before=before)

    assert not (kq.exit_code == 0 and kq.touched == "false"), kq


def test_bao_cao_moc_so_da_dung_ra_stdout(tmp_path: Path) -> None:
    """CLAUDE.md #19 ở mức vận hành: người đọc log phải biết cổng đã so
    với ĐÂU. "Bỏ qua" mà không kèm mốc là một khẳng định sạch không có
    phạm vi — không kiểm chứng được sau này."""
    repo = _dung_repo(tmp_path)
    truoc = _git(repo, "rev-parse", "HEAD")
    _them_commit(repo, "docs/a.md", "x\n")

    ra = _chay_cong(repo, tmp_path, before=truoc).stdout

    assert truoc in ra, "không in ra mốc so sánh đã dùng"
    assert "nguồn:" in ra, "không nói mốc đến từ nguồn nào trong ba nguồn"
