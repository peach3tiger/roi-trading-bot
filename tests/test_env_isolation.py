"""Bộ test không được đọc `.env` THẬT, và không được gọi mạng thật.

Hai lớp phòng thủ ở `tests/conftest.py::_cach_ly_moi_truong`, và file này
ghim CẢ HAI. Lớp danh sách đen (`CREDENTIAL_ENV`) có khuyết tật cố hữu —
biến thứ N+1 sẽ không được xoá và không ai biết — nên
`test_khong_bien_moi_truong_nao_bi_bo_quen` đối chiếu nó với code THẬT
mỗi lần chạy.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import CREDENTIAL_ENV, NON_SECRET_ENV

_ROOT = Path(__file__).resolve().parent.parent

# `os.environ.get("X")` / `os.getenv("X")` / `os.environ["X"]`
_ENV_PATTERN = re.compile(
    r'os\.(?:environ\.get|getenv)\(\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']'
    r'|os\.environ\[\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']'
)


def _env_vars_in_production_code() -> set[str]:
    """Mọi biến môi trường đọc trong code KHÔNG phải test.

    Quét bằng regex chứ không AST: ở đây ta tìm CHUỖI hằng làm khoá, và
    `ast` không cho gì thêm — khác `config/validate.py` nơi phải phân biệt
    lời gọi thật với docstring nhắc tới lời gọi.
    """
    files = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    ten: set[str] = set()
    for f in files:
        if f.startswith("tests/"):
            continue
        for m in _ENV_PATTERN.finditer((_ROOT / f).read_text(encoding="utf-8")):
            ten.add(m.group(1) or m.group(2))
    return ten


def test_khong_bien_moi_truong_nao_bi_bo_quen() -> None:
    """Mọi biến đọc trong code phải nằm ở ĐÚNG MỘT trong hai danh sách.

    Đây là phép chống trôi cho một danh sách đen. Biến thứ N+1 xuất hiện
    sẽ buộc một quyết định — bí mật (thêm vào `CREDENTIAL_ENV`) hay không
    (thêm vào `NON_SECRET_ENV`) — và không thể im lặng ở lại ngoài cả hai.

    ĐO khi viết (2026-08-14): 19 biến trong code không phải test. Danh
    sách credential ban đầu có 12 tên và con số đó là PHÁN ĐOÁN, không
    phải phép đo — chính khuyết tật mà test này vá.
    """
    trong_code = _env_vars_in_production_code()
    da_phan_loai = set(CREDENTIAL_ENV) | set(NON_SECRET_ENV)

    bo_quen = trong_code - da_phan_loai

    assert not bo_quen, (
        f"{len(bo_quen)} biến môi trường chưa được phân loại: {sorted(bo_quen)}\n"
        "Thêm vào tests/conftest.py::CREDENTIAL_ENV (nếu là bí mật / đích gửi ra "
        "ngoài) hoặc NON_SECRET_ENV (nếu chỉ là đường dẫn/tham số vận hành)."
    )


def test_hai_danh_sach_khong_giao_nhau() -> None:
    """Một biến vừa "bí mật" vừa "không bí mật" nghĩa là ai đó đã sửa một
    danh sách mà quên danh sách kia."""
    assert not (set(CREDENTIAL_ENV) & set(NON_SECRET_ENV))


def test_danh_sach_khong_chua_ten_da_bien_mat() -> None:
    """Chiều ngược lại: tên trong danh sách mà code không còn đọc là rác.

    Không phải lỗi an toàn, nhưng một danh sách có tên chết sẽ làm người
    đọc sau tưởng biến đó còn được dùng.
    """
    trong_code = _env_vars_in_production_code()
    chet = (set(CREDENTIAL_ENV) | set(NON_SECRET_ENV)) - trong_code

    assert not chet, f"tên trong danh sách nhưng code không còn đọc: {sorted(chet)}"


# ----------------------------------------------------------------------
# Lớp 1 — chặn ĐƯỜNG RÒ RỈ
# ----------------------------------------------------------------------


def test_load_dotenv_mac_dinh_bi_chan_trong_test() -> None:
    """Lớp phòng thủ CHÍNH: đóng cho MỌI biến, không chỉ những biến có tên
    trong danh sách.

    Ném lỗi chứ không trả `[]` im lặng — một lời gọi mặc định trong test
    là lỗi lập trình cần thấy ngay, không phải no-op cần bỏ qua.
    """
    from monitoring.forward_watchdog import load_dotenv

    with pytest.raises(AssertionError, match="đọc"):
        load_dotenv()


def test_load_dotenv_voi_duong_dan_tuong_minh_van_chay(tmp_path: Path) -> None:
    """Chặn phải CHÍNH XÁC: gọi với đường dẫn tạm là cách
    `test_forward_watchdog.py` kiểm chính hàm đó, và nó không được vạ lây."""
    from monitoring.forward_watchdog import load_dotenv

    env = tmp_path / ".env"
    env.write_text("BIEN_TEST_ISOLATION=gia-tri\n", encoding="utf-8")

    assert load_dotenv(env) == ["BIEN_TEST_ISOLATION"]


def test_env_that_khong_bao_gio_vao_os_environ() -> None:
    """Khẳng định KẾT QUẢ, không phải cơ chế: dù `.env` của máy này có
    credential hay không, `os.environ` trong test phải sạch."""
    import os

    co_mat = [ten for ten in CREDENTIAL_ENV if os.environ.get(ten)]

    assert not co_mat, f"credential rò rỉ vào test: {co_mat}"


# ----------------------------------------------------------------------
# ĐO ĐƯỢC: đường gửi thật CÓ bắn ra mạng
# ----------------------------------------------------------------------


def test_spy_alert_manager_di_qua_duong_gui_that(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bằng chứng cho khẳng định trong `docs/DECISIONS.md`: đây là ĐO,
    không phải suy luận.

    `tests/test_main_loop.py::_SpyAlertManager.send()` gọi `super().send()`
    — đường gửi THẬT. `console_enabled=False` chỉ tắt console; Telegram
    đọc từ `os.environ`. Nên khi env có token, mỗi lời gọi `.send()` là
    một `requests.post` tới `api.telegram.org`.

    ~20 chỗ trong `test_main_loop.py` dùng `_SpyAlertManager` và gọi
    `.send()`; cộng `test_alert_channel_health.py` dựng `AlertManager`
    thật rồi `.send()`. Từ ngày `.env` có token thật cho tới bản sửa này,
    mỗi lần chạy bộ test đều POST thật.
    """
    import monitoring.alerts as alerts_mod
    from monitoring.alerts import Alert, AlertManager, AlertType

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "gia-lap")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "gia-lap")

    goi: list[str] = []

    class _Resp:
        status_code = 200

    def _bat(url: str, *a: object, **k: object) -> object:
        goi.append(url)
        return _Resp()

    monkeypatch.setattr(alerts_mod.requests, "post", _bat)
    AlertManager(rate_limit_seconds=0, console_enabled=False).send(
        Alert(AlertType.API_LOST, "test", severity="ERROR")
    )

    assert len(goi) == 1
    assert goi[0].startswith("https://api.telegram.org/")
