"""Bộ test không được đọc `.env` THẬT, và không được gọi mạng thật.

Hai lớp phòng thủ ở `tests/conftest.py::_cach_ly_moi_truong`, và file này
ghim CẢ HAI. Lớp danh sách đen (`CREDENTIAL_ENV`) có khuyết tật cố hữu —
biến thứ N+1 sẽ không được xoá và không ai biết — nên
`test_khong_bien_moi_truong_nao_bi_bo_quen` đối chiếu nó với code THẬT
mỗi lần chạy.
"""

from __future__ import annotations

import ast
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


def _ten_qua_bien_lap(nguon: str) -> set[str]:
    """Tên biến môi trường đọc QUA MỘT BIẾN, không qua chuỗi hằng.

    `ops/kiem_tat_dinh.py` làm đúng thế: `for b in _BIEN_THREAD:
    os.environ.get(b, ...)`. Regex chuỗi hằng không thấy gì, nên năm biến
    `*_NUM_THREADS` vô hình với phép chống trôi — và chúng vô hình đúng
    lúc `ci.yml` bắt đầu đặt chúng ở tầng job.

    Điểm mù này lộ ra khi ĐO, không khi đọc: giả thuyết ban đầu cho rằng
    chính năm biến đó làm CI đỏ. Chúng không — bộ dò chưa từng thấy chúng.
    Đó là một khẳng định "đã phân loại hết" hẹp hơn nó tự nhận
    (CLAUDE.md #19).

    Cần AST ở đây, không regex: phải nối một `ast.Name` với giá trị tuple
    của nó ở tầng module.
    """
    try:
        cay = ast.parse(nguon)
    except SyntaxError:
        return set()

    hang: dict[str, list[str]] = {}
    for node in cay.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        dich = node.targets[0]
        if not isinstance(dich, ast.Name) or not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        gia_tri = [
            e.value
            for e in node.value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if len(gia_tri) == len(node.value.elts):
            hang[dich.id] = gia_tri

    # Nối biến LẶP với tuple nó duyệt: `for b in _BIEN_THREAD:` làm `b`
    # mang mọi giá trị của `_BIEN_THREAD`. Không có bước này thì
    # `os.environ.get(b)` chỉ là "đọc một biến tên b" và vô nghĩa.
    for nut in ast.walk(cay):
        if (
            isinstance(nut, ast.For)
            and isinstance(nut.target, ast.Name)
            and isinstance(nut.iter, ast.Name)
            and nut.iter.id in hang
        ):
            hang[nut.target.id] = hang[nut.iter.id]

    ra: set[str] = set()
    for goi in ast.walk(cay):
        if not isinstance(goi, ast.Call) or not goi.args:
            continue
        f = goi.func
        la_env = (
            isinstance(f, ast.Attribute)
            and f.attr in ("get", "getenv")
            and "environ" in ast.dump(f.value) + f.attr
        ) or (isinstance(f, ast.Attribute) and f.attr == "getenv")
        if la_env and isinstance(goi.args[0], ast.Name):
            ra.update(hang.get(goi.args[0].id, []))
    for chi_muc in ast.walk(cay):
        if (
            isinstance(chi_muc, ast.Subscript)
            and isinstance(chi_muc.slice, ast.Name)
            and "environ" in ast.dump(chi_muc.value)
        ):
            ra.update(hang.get(chi_muc.slice.id, []))
    return ra


def _env_vars_in_production_code() -> set[str]:
    """Mọi biến môi trường đọc trong code KHÔNG phải test.

    HAI đường quét, vì có hai cách đọc:

    - chuỗi hằng (`os.environ.get("X")`) — regex là đủ, `ast` không cho
      gì thêm; khác `config/validate.py` nơi phải phân biệt lời gọi thật
      với docstring nhắc tới lời gọi.
    - qua biến lặp (`for b in _BIEN_THREAD: os.environ.get(b)`) — regex
      mù hoàn toàn, phải dùng AST. Xem `_ten_qua_bien_lap`.
    """
    files = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    ten: set[str] = set()
    for f in files:
        if f.startswith("tests/"):
            continue
        nguon = (_ROOT / f).read_text(encoding="utf-8")
        for m in _ENV_PATTERN.finditer(nguon):
            ten.add(m.group(1) or m.group(2))
        ten |= _ten_qua_bien_lap(nguon)
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


def test_bien_doc_QUA_BIEN_LAP_cung_bi_dem() -> None:
    """Điểm mù đã ĐO, giờ được ghim.

    `ops/kiem_tat_dinh.py` đọc năm biến `*_NUM_THREADS` qua một vòng lặp
    trên `_BIEN_THREAD`. Regex chuỗi hằng mù hoàn toàn với cách đó, nên
    năm biến ấy nằm ngoài phép chống trôi suốt từ lúc file đó ra đời —
    kể cả sau khi `ci.yml` bắt đầu đặt chúng ở tầng job.

    Chúng KHÔNG phải nguyên nhân làm CI đỏ (nguyên nhân là
    `GITHUB_STEP_SUMMARY`, một chuỗi hằng). Nhưng đi tìm nguyên nhân đó
    mới lộ ra rằng "đã phân loại hết" hẹp hơn nó tự nhận — CLAUDE.md #19.
    """
    trong_code = _env_vars_in_production_code()

    assert {
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    } <= trong_code, "bộ dò lại mù với biến đọc qua biến lặp"


def test_bo_do_khong_dem_bua_tuple_khong_lien_quan() -> None:
    """Chiều còn lại: nối biến-lặp với tuple chỉ được áp khi tuple đó
    THẬT SỰ đi vào `os.environ`. Một bộ dò đếm bừa sẽ buộc phân loại
    những tên không phải biến môi trường, và danh sách mất nghĩa."""
    nguon = (
        "KHONG_LIEN_QUAN = ('MOT', 'HAI')\n"
        "CO_LIEN_QUAN = ('BA',)\n"
        "for x in KHONG_LIEN_QUAN:\n    print(x)\n"
        "for y in CO_LIEN_QUAN:\n    import os; os.environ.get(y)\n"
    )

    assert _ten_qua_bien_lap(nguon) == {"BA"}
