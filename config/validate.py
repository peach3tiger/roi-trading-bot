"""Kiểm cấu hình + bất biến TRƯỚC khi bot khởi động, và trong CI. §C.

## Vì sao AST chứ không grep

Dự án này đã gặp đúng vấn đề đó: `grep -rn "\\.predict("` bắt nhầm một
docstring đang *giải thích tại sao không được dùng* `predict()`, và cách
sửa duy nhất là viết lại docstring cho vừa công cụ. Đó là công cụ sai bắt
code phải chiều nó — và nó xoá đúng lời giải thích mà người đọc tiếp theo
cần nhất.

`ast` nhìn thấy CẤU TRÚC: một lời gọi `.predict()` là một `ast.Call` với
`func` là `ast.Attribute`. Comment và docstring không phải `ast.Call`, nên
chúng không tồn tại với công cụ này. `test_khong_bao_loi_khi_docstring_nhac_predict`
ghim điều đó.

## Mọi kiểm trả về DANH SÁCH, không raise

Một validator dừng ở lỗi đầu tiên bắt người vận hành sửa-chạy-lại-sửa
nhiều vòng, mỗi vòng một lỗi. Thu hết rồi in một lần.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_SECTIONS = (
    "exchange",
    "execution",
    "costs",
    "hmm",
    "features",
    "trend_gate",
    "strategy",
    "risk",
    "backtest",
    "monitoring",
)

# Biến môi trường bắt buộc để CHẠY THẬT. Không gồm `STATE_DIR`/`MODEL_PATH`
# (có mặc định hợp lý trong code) — chỉ những thứ không đoán được.
REQUIRED_ENV = ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET")

MAINNET_CONFIRMATION_ENV = "I_UNDERSTAND_THIS_IS_REAL_MONEY"


@dataclass(frozen=True)
class Problem:
    check: str
    detail: str
    where: str = ""

    def __str__(self) -> str:
        vi_tri = f" [{self.where}]" if self.where else ""
        return f"[{self.check}]{vi_tri} {self.detail}"


# ----------------------------------------------------------------------
# §C.1 — cấu hình
# ----------------------------------------------------------------------


def check_settings_sections(settings: dict) -> list[Problem]:
    return [
        Problem("section bắt buộc", f"thiếu section `{s}` trong settings.yaml")
        for s in REQUIRED_SECTIONS
        if s not in settings
    ]


def check_env(
    env: Optional[dict[str, str]] = None, *, required: Sequence[str] = REQUIRED_ENV
) -> list[Problem]:
    """Rỗng cũng là THIẾU.

    `.env` của dự án này đã từng có `TELEGRAM_BOT_TOKEN=` với giá trị rỗng
    — biến TỒN TẠI, `os.environ.get()` trả `""`, và mọi phép kiểm "có key
    không" đều qua trong khi không gửi được cảnh báo nào. Báo ĐÚNG TÊN
    biến còn thiếu, không phải "thiếu cấu hình".
    """
    moi_truong = os.environ if env is None else env
    return [
        Problem("biến môi trường", f"`{ten}` không tồn tại hoặc rỗng")
        for ten in required
        if not (moi_truong.get(ten) or "").strip()
    ]


def check_frozen_hash(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """`forward/config_frozen.yaml` phải khớp hash đã ghim.

    Trùng vai trò với `tests/test_frozen_files.py` một cách CÓ CHỦ Ý: file
    test chỉ chạy khi có ai chạy test, còn hàm này chạy TRƯỚC MỖI LẦN BOT
    KHỞI ĐỘNG. Một thí nghiệm 12 tháng không nên phụ thuộc vào việc ai đó
    nhớ chạy pytest.
    """
    frozen = repo_root / "forward" / "config_frozen.yaml"
    pinned = repo_root / "tests" / "golden" / "frozen_hashes.json"
    if not frozen.exists():
        return [Problem("hash đóng băng", f"{frozen} không tồn tại")]
    if not pinned.exists():
        return [Problem("hash đóng băng", f"{pinned} không tồn tại")]

    try:
        ghim = json.loads(pinned.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Problem("hash đóng băng", f"{pinned} không đọc được: {exc}")]

    # Hash nằm dưới khoá `files`, không phải ở gốc — `tests/test_frozen_files.py`
    # là nguồn sự thật cho định dạng này; đọc sai cấu trúc sẽ cho một
    # validator luôn báo lỗi, và một cổng luôn đỏ bị vô hiệu hoá trong
    # tuần đầu tiên.
    khoa = "forward/config_frozen.yaml"
    mong_doi = (ghim.get("files") or {}).get(khoa)
    if not isinstance(mong_doi, str):
        return [Problem("hash đóng băng", f"{pinned} không có khoá `{khoa}`")]

    thuc_te = hashlib.sha256(frozen.read_bytes()).hexdigest()
    if thuc_te != mong_doi:
        return [
            Problem(
                "hash đóng băng",
                f"{khoa} ĐÃ ĐỔI — ghim {mong_doi[:12]}…, thực tế {thuc_te[:12]}…. "
                "Đổi file này = kết thúc thí nghiệm forward hiện tại (CLAUDE.md #15).",
            )
        ]
    return []


def check_testnet(settings: dict, env: Optional[dict[str, str]] = None) -> list[Problem]:
    """`testnet: false` chỉ hợp lệ khi có xác nhận mainnet TƯỜNG MINH.

    Cổng này KHÔNG thay thế CLAUDE.md #12 (mainnet khoá tới 2027-08-06) —
    nó chỉ chặn một lần `testnet: false` gõ nhầm không đi thẳng ra tiền
    thật.
    """
    moi_truong = os.environ if env is None else env
    testnet = (settings.get("exchange") or {}).get("testnet")
    if testnet is True:
        return []
    if (moi_truong.get(MAINNET_CONFIRMATION_ENV) or "").strip():
        return []
    return [
        Problem(
            "testnet",
            f"`exchange.testnet` là {testnet!r} chứ không phải True, và "
            f"`{MAINNET_CONFIRMATION_ENV}` chưa được đặt. Xem CLAUDE.md #6 và #12.",
        )
    ]


# ----------------------------------------------------------------------
# §C.2 — bất biến, bằng AST
# ----------------------------------------------------------------------


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _python_files(root: Path) -> Iterable[Path]:
    return sorted(root.rglob("*.py")) if root.is_dir() else []


def _method_calls(tree: ast.Module) -> Iterable[tuple[str, ast.Call]]:
    """Mọi lời gọi dạng `<gì đó>.<tên>(...)`, kèm tên method."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node.func.attr, node


def check_no_viterbi(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """CLAUDE.md #1 — `predict()`/`decode()` chạy Viterbi trên TOÀN chuỗi
    và sửa lại trạng thái quá khứ bằng dữ liệu tương lai. Look-ahead bias,
    bug nghiêm trọng nhất có thể có trong dự án này."""
    ra: list[Problem] = []
    f = repo_root / "core" / "hmm_engine.py"
    tree = _parse(f)
    if tree is None:
        return [Problem("không dùng Viterbi", f"không parse được {f}")]
    for ten, node in _method_calls(tree):
        if ten in ("predict", "decode"):
            ra.append(
                Problem(
                    "không dùng Viterbi",
                    f"lời gọi `.{ten}()` — look-ahead bias (CLAUDE.md #1). "
                    "Dùng `predict_regime_filtered()`.",
                    f"core/hmm_engine.py:{node.lineno}",
                )
            )
    return ra


def check_no_center_rolling(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """CLAUDE.md #11 — `center=True` làm cửa sổ rolling nhìn về TƯƠNG LAI."""
    ra: list[Problem] = []
    for thu_muc in ("core", "data"):
        for f in _python_files(repo_root / thu_muc):
            tree = _parse(f)
            if tree is None:
                continue
            for ten, node in _method_calls(tree):
                if ten != "rolling":
                    continue
                for kw in node.keywords:
                    if kw.arg == "center" and not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is False
                    ):
                        ra.append(
                            Problem(
                                "rolling không center",
                                "`.rolling(center=...)` khác False — cửa sổ nhìn về tương lai",
                                f"{f.relative_to(repo_root)}:{node.lineno}",
                            )
                        )
    return ra


def check_compose_uses_min(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """CLAUDE.md #2 — `compose_layer_allocations` phải là hàm TỐI THIỂU.

    Kiểm ngay trong thân hàm đó chứ không quét cả file: `max()` ở chỗ khác
    trong `signal_generator.py` có thể hoàn toàn hợp lệ, và một phép kiểm
    cấm `max` toàn file sẽ buộc code phải né công cụ.
    """
    f = repo_root / "core" / "signal_generator.py"
    tree = _parse(f)
    if tree is None:
        return [Problem("kết hợp tầng bằng min", f"không parse được {f}")]

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "compose_layer_allocations"):
            continue
        ten_goi = {
            n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        loi: list[Problem] = []
        if "max" in ten_goi:
            loi.append(
                Problem(
                    "kết hợp tầng bằng min",
                    "`compose_layer_allocations` gọi `max()` — CLAUDE.md #2 cấm tuyệt đối",
                    f"core/signal_generator.py:{node.lineno}",
                )
            )
        if "min" not in ten_goi:
            loi.append(
                Problem(
                    "kết hợp tầng bằng min",
                    "`compose_layer_allocations` KHÔNG gọi `min()` — không còn là hàm tối thiểu",
                    f"core/signal_generator.py:{node.lineno}",
                )
            )
        return loi
    return [Problem("kết hợp tầng bằng min", "không tìm thấy `compose_layer_allocations`")]


def _imported_modules(tree: ast.Module) -> set[str]:
    ra: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            ra.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            ra.add(node.module)
            ra.update(f"{node.module}.{a.name}" for a in node.names)
    return ra


def check_risk_manager_independent(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """CLAUDE.md #4 — risk manager phải ĐỘC LẬP với HMM. Sự độc lập đó là
    lý do nó vẫn bảo vệ được khi HMM sai hoàn toàn."""
    f = repo_root / "core" / "risk_manager.py"
    tree = _parse(f)
    if tree is None:
        return [Problem("risk manager độc lập", f"không parse được {f}")]
    # Một Problem cho mỗi MODULE BỊ CẤM, không phải cho mỗi chuỗi import:
    # `from core.hmm_engine import X` sinh cả `core.hmm_engine` lẫn
    # `core.hmm_engine.X` trong `_imported_modules`, và báo hai lần cùng
    # một vi phạm làm người đọc tưởng có hai chỗ phải sửa.
    da_import = _imported_modules(tree)
    return [
        Problem(
            "risk manager độc lập",
            f"import `{cam}` — CLAUDE.md #4 cấm; risk manager phải quyết định "
            "dựa trên P&L thực tế và trạng thái danh mục",
            "core/risk_manager.py",
        )
        for cam in ("hmm_engine", "regime_strategies")
        if any(cam in m for m in da_import)
    ]


def check_shadow_runner_no_executor(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """`ops/shadow_runner.py` (Phase 12c, CHƯA XÂY) không được import
    `order_executor` — shadow mode không đặt lệnh.

    File chưa tồn tại -> KHÔNG có vi phạm, nhưng đó là "chưa kiểm được"
    chứ không phải "sạch". `ops/verify_scope.py` là chỗ nói ra điều đó.
    """
    f = repo_root / "ops" / "shadow_runner.py"
    if not f.exists():
        return []
    tree = _parse(f)
    if tree is None:
        return [Problem("shadow không đặt lệnh", f"không parse được {f}")]
    if any("order_executor" in m for m in _imported_modules(tree)):
        return [
            Problem(
                "shadow không đặt lệnh",
                "import `order_executor` — shadow mode chỉ quan sát, không đặt lệnh",
                "ops/shadow_runner.py",
            )
        ]
    return []


def check_no_252(*, repo_root: Path = _REPO_ROOT) -> list[Problem]:
    """CLAUDE.md #9 — năm có 365 ngày. `252` là tàn dư từ spec equity gốc.

    Chỉ bắt literal SỐ (`ast.Constant`), nên `# KHÔNG phải 252` trong
    comment và `"252"` trong một thông điệp lỗi không bị tính. Đó là toàn
    bộ điểm của việc dùng AST.
    """
    ra: list[Problem] = []
    for thu_muc in ("core", "data", "backtest"):
        for f in _python_files(repo_root / thu_muc):
            tree = _parse(f)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == 252 and not isinstance(node.value, bool):
                    ra.append(
                        Problem(
                            "không có 252",
                            "literal `252` — năm có 365 ngày (CLAUDE.md #9)",
                            f"{f.relative_to(repo_root)}:{node.lineno}",
                        )
                    )
    return ra


INVARIANT_CHECKS = (
    check_no_viterbi,
    check_no_center_rolling,
    check_compose_uses_min,
    check_risk_manager_independent,
    check_shadow_runner_no_executor,
    check_no_252,
)


# ----------------------------------------------------------------------
# Chạy
# ----------------------------------------------------------------------


def validate(
    settings: Optional[dict] = None,
    *,
    repo_root: Path = _REPO_ROOT,
    env: Optional[dict[str, str]] = None,
    check_env_vars: bool = True,
) -> list[Problem]:
    """Thu HẾT vấn đề rồi mới trả về.

    `check_env_vars=False` cho CI, nơi không có credential thật và cũng
    không nên có — nhưng mọi kiểm bất biến vẫn chạy đủ.
    """
    if settings is None:
        # `config/` không nằm trên `sys.path` khi chạy
        # `python config/validate.py` trực tiếp — và chạy trực tiếp CHÍNH
        # LÀ cách `ops/entrypoint.sh` gọi nó, trước khi bất kỳ thứ gì của
        # dự án được import. Thêm gốc repo vào path ở đây thay vì bắt
        # người vận hành nhớ `PYTHONPATH=.`.
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        import main as main_mod

        settings = main_mod.load_settings()

    van_de: list[Problem] = []
    van_de += check_settings_sections(settings)
    if check_env_vars:
        van_de += check_env(env)
    van_de += check_frozen_hash(repo_root=repo_root)
    van_de += check_testnet(settings, env)
    for kiem in INVARIANT_CHECKS:
        van_de += kiem(repo_root=repo_root)
    return van_de


def format_report(problems: Sequence[Problem]) -> str:
    if not problems:
        return "config/validate.py: OK — cấu hình đủ, 6 bất biến không bị vi phạm."
    dong = [f"config/validate.py: {len(problems)} VẤN ĐỀ", ""]
    dong += [f"  {p}" for p in problems]
    return "\n".join(dong)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Kiểm cấu hình + bất biến trước khi khởi động.")
    parser.add_argument(
        "--skip-env",
        action="store_true",
        help="Bỏ kiểm biến môi trường (dùng trong CI — không có credential thật).",
    )
    args = parser.parse_args(argv)

    van_de = validate(check_env_vars=not args.skip_env)
    print(format_report(van_de))
    return 1 if van_de else 0


if __name__ == "__main__":
    sys.exit(main())
