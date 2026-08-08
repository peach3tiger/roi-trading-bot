"""Cây exception của `ccxt` không được giao với `_PROGRAMMING_ERRORS`.

`main.py` phân loại lỗi bằng THỨ TỰ `except`: `_PROGRAMMING_ERRORS`
(`TypeError`/`AttributeError`/`KeyError`) bắt trước, `except Exception`
(lỗi hạ tầng -> `DATA_FEED_LOST`/`API_LOST`) bắt sau. Cách này chỉ đúng
khi hai tập KHÔNG giao nhau.

Giả định ngầm: mọi exception của `ccxt` kế thừa `Exception` trực tiếp,
không đi qua `TypeError`/`AttributeError`/`KeyError`. Đúng ở phiên bản
hiện tại — nhưng đó là chi tiết nội bộ của một thư viện ngoài, không phải
hợp đồng nào cả. Nếu một bản `ccxt` sau này cho `BadRequest` kế thừa
`ValueError`, hoặc thêm một lớp kế thừa `KeyError`, thì một lỗi mạng thật
sẽ bị dán nhãn `INTERNAL_ERROR` — và triệu chứng là một alert sai loại
lúc 3 giờ sáng, không phải một test đỏ.

File này biến giả định đó thành phép kiểm chạy mỗi lần `pytest`.

**Chỉ nằm trong `tests/`.** `main.py` CỐ TÌNH không import `ccxt` (sẽ kéo
cả thư viện vào đường import của mọi lệnh con, kể cả `--backtest` vốn
không cần sàn) — xem chú thích ở nhánh `except Exception` của
`_check_spread_and_alert`. Test thì import thoải mái.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import ccxt
import pytest

from main import _PROGRAMMING_ERRORS

# Ở ccxt 4.5.64 cả hai namespace cho cùng 41 lớp; duyệt cả hai để một bản
# sau này thêm lớp CHỈ ở submodule vẫn không lọt.
_NAMESPACES = ("ccxt", "ccxt.base.errors")

# Chặn test tự rỗng nghĩa: nếu `vars(ccxt)` đổi hình dạng (đổi tên
# submodule, chuyển sang lazy import) thì vòng duyệt có thể trả về 0 lớp
# và mọi assert bên dưới xanh mà không kiểm gì. 41 lớp ở 4.5.64 — lấy 20
# làm sàn để không phải sửa test mỗi lần ccxt thêm/bớt vài lớp.
_MIN_EXPECTED_CLASSES = 20


def _ccxt_exception_classes() -> dict[str, type[BaseException]]:
    found: dict[str, type[BaseException]] = {}
    for mod_name in _NAMESPACES:
        try:
            module: Any = importlib.import_module(mod_name)
        except ImportError:
            continue  # submodule bị đổi tên ở bản sau — namespace kia vẫn phủ
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and issubclass(obj, BaseException):
                found[f"{mod_name}.{name}"] = obj
    return found


def test_tim_duoc_du_lop_exception_de_phep_kiem_co_nghia() -> None:
    """Tiền đề của mọi assert bên dưới."""
    classes = _ccxt_exception_classes()

    assert len(classes) >= _MIN_EXPECTED_CLASSES, (
        f"chỉ tìm thấy {len(classes)} lớp exception trong {_NAMESPACES} — "
        f"cách ccxt phơi bày exception đã đổi, vòng duyệt của test này không còn "
        f"phủ được gì. Sửa `_ccxt_exception_classes()`, đừng hạ `_MIN_EXPECTED_CLASSES`."
    )
    assert "ccxt.BaseError" in classes


def test_khong_lop_ccxt_nao_la_loi_lap_trinh() -> None:
    """KHẲNG ĐỊNH TRUNG TÂM.

    Đỏ nghĩa là: một loại lỗi HẠ TẦNG của ccxt giờ rơi vào nhánh
    `_PROGRAMMING_ERRORS` của `main.py` và sẽ bị dán nhãn
    `INTERNAL_ERROR` thay vì `DATA_FEED_LOST`/`API_LOST`.

    KHÔNG sửa bằng cách bỏ lớp đó ra khỏi test. Sửa bằng cách liệt kê nó
    tường minh ở nhánh hạ tầng của `main.py` TRƯỚC nhánh lỗi lập trình,
    hoặc thu hẹp `_PROGRAMMING_ERRORS`.
    """
    offenders = {
        name: [base.__name__ for base in cls.__mro__]
        for name, cls in _ccxt_exception_classes().items()
        if issubclass(cls, _PROGRAMMING_ERRORS)
    }

    assert offenders == {}, (
        f"ccxt (bản {ccxt.__version__}) có lớp exception kế thừa "
        f"{[e.__name__ for e in _PROGRAMMING_ERRORS]}:\n"
        + "\n".join(f"  {n}: {mro}" for n, mro in offenders.items())
        + "\n\nMain.py phân loại bằng thứ tự `except`, nên những lớp này sẽ bị "
        "dán nhãn INTERNAL_ERROR trong khi chúng là sự cố hạ tầng thật."
    )


@pytest.mark.parametrize("name", ["NetworkError", "ExchangeError", "BaseError"])
def test_lop_ccxt_chinh_van_roi_vao_nhanh_ha_tang(name: str) -> None:
    """Chiều ngược lại: ba lớp mà `broker/ccxt_client.py::_call_with_retry`
    dựa vào phải VẪN rơi vào `except Exception` của `main.py`.

    Nếu một bản ccxt cho chúng kế thừa thẳng `BaseException` (không qua
    `Exception`), nhánh rộng sẽ trượt và lỗi thoát ra khỏi
    `_check_spread_and_alert` — phá cam kết "không raise" của hàm đó.
    """
    cls = getattr(ccxt, name)

    assert issubclass(cls, Exception), f"ccxt.{name} không còn kế thừa Exception"
    assert not issubclass(cls, _PROGRAMMING_ERRORS)


def test_moi_lop_ccxt_deu_bat_duoc_bang_except_exception() -> None:
    """`except Exception` là lưới hứng cuối của `_check_spread_and_alert`
    và của vòng lặp chính. Một lớp chỉ kế thừa `BaseException` sẽ lọt qua
    nó và thoát ra ngoài."""
    escapees = sorted(
        name for name, cls in _ccxt_exception_classes().items() if not issubclass(cls, Exception)
    )

    assert escapees == [], (
        f"những lớp này chỉ kế thừa BaseException nên `except Exception` KHÔNG bắt được: "
        f"{escapees} — chúng sẽ thoát ra khỏi `send()`/`_check_spread_and_alert`."
    )
