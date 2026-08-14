"""Phase 12d §C — `config/validate.py`.

Nghiệm thu 12d đòi: với MỖI bất biến, tiêm vi phạm, xác nhận validator từ
chối, revert. Ở đây các vi phạm được tiêm vào một REPO GIẢ trong
`tmp_path` chứ không vào cây làm việc thật — an toàn hơn kịch bản đột biến
(CLAUDE.md #16 §"commit hoặc git stash trước"), vì không có đường nào để
lại một dòng vi phạm trong repo nếu tiến trình bị giết giữa chừng.

Một phép kiểm bổ sung, và là lý do §C bắt dùng AST:
`test_khong_bao_loi_khi_docstring_nhac_predict`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from config.validate import (
    INVARIANT_CHECKS,
    MAINNET_CONFIRMATION_ENV,
    REQUIRED_SECTIONS,
    Problem,
    check_compose_uses_min,
    check_env,
    check_frozen_hash,
    check_no_252,
    check_no_center_rolling,
    check_no_viterbi,
    check_risk_manager_independent,
    check_settings_sections,
    check_shadow_runner_no_executor,
    check_testnet,
    format_report,
    validate,
)

_THAT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# Repo THẬT phải sạch — cổng này chạy trước mỗi lần khởi động
# ----------------------------------------------------------------------


@pytest.mark.parametrize("kiem", INVARIANT_CHECKS, ids=[c.__name__ for c in INVARIANT_CHECKS])
def test_repo_that_khong_vi_pham_bat_bien(kiem: Callable[..., list[Problem]]) -> None:
    assert kiem(repo_root=_THAT) == []


def test_repo_that_du_section_va_hash() -> None:
    import main as main_mod

    assert check_settings_sections(main_mod.load_settings()) == []
    assert check_frozen_hash(repo_root=_THAT) == []


def test_sau_kiem_bat_bien_dung_nhu_C2() -> None:
    assert len(INVARIANT_CHECKS) == 6


# ----------------------------------------------------------------------
# AST, KHÔNG grep — lý do tồn tại của §C.2
# ----------------------------------------------------------------------


def _repo_gia(root: Path) -> Path:
    """Repo tối thiểu, mọi bất biến đều SẠCH. Mỗi test bên dưới làm bẩn
    đúng một chỗ."""
    (root / "core").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "backtest").mkdir()
    (root / "core" / "hmm_engine.py").write_text("x = 1\n", encoding="utf-8")
    (root / "core" / "risk_manager.py").write_text("import json\n", encoding="utf-8")
    (root / "core" / "signal_generator.py").write_text(
        "def compose_layer_allocations(*caps):\n    return min(caps)\n", encoding="utf-8"
    )
    return root


def test_khong_bao_loi_khi_docstring_nhac_predict(tmp_path: Path) -> None:
    """NGHIỆM THU 12d #7, và là lý do §C.2 bắt dùng AST.

    Bản grep của phép kiểm này bắt nhầm chính docstring đang GIẢI THÍCH
    tại sao không được dùng `predict()`, và cách sửa duy nhất là viết lại
    docstring cho vừa công cụ — xoá đúng lời giải thích mà người đọc tiếp
    theo cần nhất.
    """
    repo = _repo_gia(tmp_path)
    (repo / "core" / "hmm_engine.py").write_text(
        '"""KHÔNG BAO GIỜ dùng model.predict() — nó chạy Viterbi trên toàn\n'
        "chuỗi và sửa lại quá khứ bằng dữ liệu tương lai. Cũng không\n"
        'model.decode(). Dùng predict_regime_filtered().\n"""\n'
        "# .predict( trong comment cũng không được tính\n"
        'BAO_LOI = "gọi .decode() là look-ahead bias"\n'
        "def predict_regime_filtered(x):\n"
        "    return x\n",
        encoding="utf-8",
    )

    assert check_no_viterbi(repo_root=repo) == []


def test_grep_se_bao_loi_o_dung_file_do(tmp_path: Path) -> None:
    """Mặt kia của phép đo: chứng minh khác biệt AST-vs-grep là THẬT, chứ
    không phải một lời khẳng định trong docstring."""
    repo = _repo_gia(tmp_path)
    noi_dung = (
        '"""KHÔNG dùng model.predict() — look-ahead bias."""\n'
        "def predict_regime_filtered(x):\n    return x\n"
    )
    (repo / "core" / "hmm_engine.py").write_text(noi_dung, encoding="utf-8")

    assert ".predict(" in noi_dung  # grep SẼ khớp
    assert check_no_viterbi(repo_root=repo) == []  # AST thì không


def test_252_trong_comment_va_chuoi_khong_bi_tinh(tmp_path: Path) -> None:
    """Cùng nguyên tắc: `# KHÔNG phải 252` là lời nhắc đúng đắn, không
    phải vi phạm. `settings.yaml` của dự án có đúng dòng đó."""
    repo = _repo_gia(tmp_path)
    (repo / "core" / "x.py").write_text(
        "# zscore_lookback 365, KHÔNG phải 252\n"
        'LOI = "năm có 365 ngày, không phải 252"\n'
        "NAM = 365\n",
        encoding="utf-8",
    )

    assert check_no_252(repo_root=repo) == []


# ----------------------------------------------------------------------
# Tiêm vi phạm cho TỪNG bất biến — nghiệm thu 12d #6
# ----------------------------------------------------------------------


@pytest.mark.parametrize("ten_goi", ["predict", "decode"])
def test_tiem_viterbi_thi_tu_choi(tmp_path: Path, ten_goi: str) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "core" / "hmm_engine.py").write_text(
        f"def f(model, X):\n    return model.{ten_goi}(X)\n", encoding="utf-8"
    )

    van_de = check_no_viterbi(repo_root=repo)

    assert len(van_de) == 1
    assert f".{ten_goi}()" in van_de[0].detail
    assert "hmm_engine.py:2" in van_de[0].where


def test_tiem_center_rolling_thi_tu_choi(tmp_path: Path) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "data" / "fe.py").write_text(
        "def f(s):\n    return s.rolling(20, center=True).mean()\n", encoding="utf-8"
    )

    assert len(check_no_center_rolling(repo_root=repo)) == 1


def test_center_False_van_hop_le(tmp_path: Path) -> None:
    """`center=False` là mặc định viết tường minh — không phải vi phạm."""
    repo = _repo_gia(tmp_path)
    (repo / "data" / "fe.py").write_text(
        "def f(s):\n    return s.rolling(20, center=False).mean()\n", encoding="utf-8"
    )

    assert check_no_center_rolling(repo_root=repo) == []


def test_tiem_max_vao_compose_thi_tu_choi(tmp_path: Path) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "core" / "signal_generator.py").write_text(
        "def compose_layer_allocations(*caps):\n    return max(caps)\n", encoding="utf-8"
    )

    van_de = check_compose_uses_min(repo_root=repo)

    assert any("max()" in p.detail for p in van_de)
    assert any("KHÔNG gọi `min()`" in p.detail for p in van_de)


def test_max_o_HAM_KHAC_khong_bi_tinh(tmp_path: Path) -> None:
    """Kiểm ngay trong thân `compose_layer_allocations`, không quét cả
    file: `max()` ở chỗ khác có thể hoàn toàn hợp lệ, và một phép kiểm cấm
    `max` toàn file sẽ buộc code phải né công cụ."""
    repo = _repo_gia(tmp_path)
    (repo / "core" / "signal_generator.py").write_text(
        "def compose_layer_allocations(*caps):\n    return min(caps)\n\n"
        "def do_dai_nhat(xs):\n    return max(len(x) for x in xs)\n",
        encoding="utf-8",
    )

    assert check_compose_uses_min(repo_root=repo) == []


@pytest.mark.parametrize(
    "dong",
    [
        "from core.hmm_engine import HMMRegimeEngine",
        "import core.hmm_engine",
        "from core.regime_strategies import Signal",
    ],
)
def test_tiem_import_hmm_vao_risk_manager_thi_tu_choi(tmp_path: Path, dong: str) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "core" / "risk_manager.py").write_text(dong + "\n", encoding="utf-8")

    assert len(check_risk_manager_independent(repo_root=repo)) == 1


def test_tiem_order_executor_vao_shadow_runner_thi_tu_choi(tmp_path: Path) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "ops").mkdir()
    (repo / "ops" / "shadow_runner.py").write_text(
        "from broker.order_executor import OrderExecutor\n", encoding="utf-8"
    )

    assert len(check_shadow_runner_no_executor(repo_root=repo)) == 1


def test_shadow_runner_chua_ton_tai_thi_KHONG_vi_pham(tmp_path: Path) -> None:
    """"Chưa kiểm được" khác "sạch". Ở đây trả rỗng là đúng hành vi, và
    `ops/verify_scope.py` là chỗ nói ra rằng file chưa tồn tại."""
    assert check_shadow_runner_no_executor(repo_root=_repo_gia(tmp_path)) == []


def test_tiem_252_thi_tu_choi(tmp_path: Path) -> None:
    repo = _repo_gia(tmp_path)
    (repo / "backtest" / "m.py").write_text("SHARPE_DAYS = 252\n", encoding="utf-8")

    van_de = check_no_252(repo_root=repo)

    assert len(van_de) == 1
    assert "m.py:1" in van_de[0].where


# ----------------------------------------------------------------------
# §C.1 — cấu hình
# ----------------------------------------------------------------------


def test_thieu_section_thi_bao_dung_ten() -> None:
    van_de = check_settings_sections({"exchange": {}})

    assert len(van_de) == len(REQUIRED_SECTIONS) - 1
    assert any("`risk`" in p.detail for p in van_de)


def test_env_rong_LA_thieu() -> None:
    """`.env` của dự án đã từng có `TELEGRAM_BOT_TOKEN=` với giá trị rỗng
    — biến TỒN TẠI, mọi phép kiểm "có key không" đều qua, và không gửi
    được cảnh báo nào."""
    van_de = check_env({"EXCHANGE_API_KEY": "", "EXCHANGE_API_SECRET": "   "})

    assert len(van_de) == 2
    assert all("EXCHANGE_API" in p.detail for p in van_de)


def test_env_du_thi_khong_bao() -> None:
    assert check_env({"EXCHANGE_API_KEY": "k", "EXCHANGE_API_SECRET": "s"}) == []


def test_testnet_true_thi_qua() -> None:
    assert check_testnet({"exchange": {"testnet": True}}, {}) == []


def test_testnet_false_khong_xac_nhan_thi_tu_choi() -> None:
    van_de = check_testnet({"exchange": {"testnet": False}}, {})

    assert len(van_de) == 1
    assert MAINNET_CONFIRMATION_ENV in van_de[0].detail


def test_testnet_false_co_xac_nhan_thi_qua() -> None:
    assert check_testnet({"exchange": {"testnet": False}}, {MAINNET_CONFIRMATION_ENV: "yes"}) == []


def test_testnet_thieu_han_cung_bi_tu_choi() -> None:
    """Thiếu key KHÁC `testnet: true`. Mặc định phải là TỪ CHỐI."""
    assert len(check_testnet({"exchange": {}}, {})) == 1


def test_hash_doi_thi_tu_choi(tmp_path: Path) -> None:
    (tmp_path / "forward").mkdir()
    (tmp_path / "forward" / "config_frozen.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "tests" / "golden").mkdir(parents=True)
    (tmp_path / "tests" / "golden" / "frozen_hashes.json").write_text(
        json.dumps({"files": {"forward/config_frozen.yaml": "sai" * 20}}), encoding="utf-8"
    )

    van_de = check_frozen_hash(repo_root=tmp_path)

    assert len(van_de) == 1
    assert "ĐÃ ĐỔI" in van_de[0].detail


def test_doc_hash_dung_cau_truc_files() -> None:
    """Hash nằm dưới khoá `files`. Đọc sai cấu trúc cho một validator LUÔN
    báo lỗi — và một cổng luôn đỏ bị vô hiệu hoá trong tuần đầu tiên."""
    ghim = json.loads((_THAT / "tests" / "golden" / "frozen_hashes.json").read_text(encoding="utf-8"))

    assert "forward/config_frozen.yaml" in ghim["files"]
    assert check_frozen_hash(repo_root=_THAT) == []


# ----------------------------------------------------------------------
# Gộp
# ----------------------------------------------------------------------


def test_validate_thu_HET_van_de_khong_dung_o_cai_dau(tmp_path: Path) -> None:
    """Một validator dừng ở lỗi đầu tiên bắt người vận hành
    sửa-chạy-lại-sửa nhiều vòng, mỗi vòng một lỗi."""
    repo = _repo_gia(tmp_path)
    (repo / "core" / "hmm_engine.py").write_text("def f(m, X):\n    return m.predict(X)\n", encoding="utf-8")
    (repo / "backtest" / "m.py").write_text("N = 252\n", encoding="utf-8")

    van_de = validate({}, repo_root=repo, env={}, check_env_vars=False)

    loai = {p.check for p in van_de}
    assert "không dùng Viterbi" in loai
    assert "không có 252" in loai
    assert "section bắt buộc" in loai


def test_bao_cao_sach_noi_ro_da_kiem_gi() -> None:
    """CLAUDE.md #19 — mọi khẳng định "sạch" phải kèm phạm vi."""
    ra = format_report([])

    assert "6 bất biến" in ra


def test_bao_cao_in_du_moi_van_de() -> None:
    ra = format_report([Problem("a", "x"), Problem("b", "y", "f.py:1")])

    assert "2 VẤN ĐỀ" in ra
    assert "f.py:1" in ra


def test_repo_that_qua_toan_bo_validate() -> None:
    """Cổng này chạy TRƯỚC MỖI LẦN BOT KHỞI ĐỘNG — nó phải xanh trên chính
    repo này, nếu không bot không khởi động được."""
    van_de = validate(repo_root=_THAT, env={}, check_env_vars=False)

    assert van_de == [], format_report(van_de)


def test_validate_khong_ghi_gi_vao_repo(tmp_path: Path) -> None:
    """Ràng buộc #3 của Phase 12d: phát hiện và báo cáo, không tự sửa."""
    src = (_THAT / "config" / "validate.py").read_text(encoding="utf-8")

    for cam in ("write_text", "open(", "unlink", "mkdir", "rename"):
        assert cam not in src, f"validator có vẻ ghi vào đĩa qua {cam!r}"


def test_validate_khong_dung_grep() -> None:
    """Ràng buộc #5: AST cho MỌI kiểm bất biến."""
    src = (_THAT / "config" / "validate.py").read_text(encoding="utf-8")

    assert "import ast" in src
    for cam in ("subprocess", "re.search", "re.findall", '"grep"'):
        assert cam not in src, f"validator có vẻ dùng {cam!r} thay vì AST"


def test_khong_ghi_vao_forward() -> None:
    """Ràng buộc #4."""
    src = (_THAT / "config" / "validate.py").read_text(encoding="utf-8")

    assert "read_bytes" in src  # đọc để băm
    assert "write" not in src
