"""Lỗi LẬP TRÌNH không được nguỵ trang thành sự cố vận hành.

`TypeError`/`AttributeError`/`KeyError` nghĩa là giả định của chính chúng
ta về hợp đồng dữ liệu đã sai — không phải mạng chập, không phải sàn 5xx.
Gộp chúng vào `DATA_FEED_LOST`/`API_LOST` tạo ra chế độ hỏng tệ nhất:
người vận hành đọc "mất feed", quyết định CHỜ, và bug nằm im vô thời hạn.

Đã xảy ra thật trong chính test của dự án này (2026-08-08): fake
`OrderBook` dựng bằng `best_bid=`/`best_ask=` (vốn là `@property`, không
phải field) ném `TypeError`, `_check_spread_and_alert` nuốt thành
`DATA_FEED_LOST`, và phép kiểm spread im lặng không chạy lần nào — phát
hiện ra bằng đột biến, không phải bằng test đỏ. Nếu một field ở tầng
broker đổi tên khi chạy thật, triệu chứng sẽ y hệt.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.risk_manager import RiskManager
from main import _PROGRAMMING_ERRORS, _check_clock_drift, _check_spread_and_alert
from monitoring.alerts import AlertType
from tests.test_main_loop import _FakeOrderBook, _risk_manager_config, _SpyAlertManager

_SYMBOL = "BTCUSDT"


class _RaisingExchange:
    """Sàn giả ném đúng exception được tiêm vào."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get_orderbook(self, symbol: str) -> Any:
        raise self._exc

    def get_server_time(self) -> int:
        raise self._exc


def _run_spread(exc: BaseException, tmp_path: Path) -> _SpyAlertManager:
    manager = _SpyAlertManager()
    _check_spread_and_alert(
        alert_manager=manager,
        risk_manager=RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "halt.lock"),
        exchange_client=_RaisingExchange(exc),
        symbol=_SYMBOL,
    )
    return manager


# ======================================================================
# Đường spread — yêu cầu chính
# ======================================================================


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("__init__() got an unexpected keyword argument 'best_bid'"),
        AttributeError("'NoneType' object has no attribute 'best_bid'"),
        KeyError("bids"),
    ],
    ids=["TypeError", "AttributeError", "KeyError"],
)
def test_loi_lap_trinh_khong_sinh_data_feed_lost(exc: BaseException, tmp_path: Path) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM của file này."""
    manager = _run_spread(exc, tmp_path)

    types = [a.alert_type for a in manager.sent]
    assert AlertType.DATA_FEED_LOST not in types, (
        f"{type(exc).__name__} bị dán nhãn DATA_FEED_LOST — người vận hành sẽ đi "
        "kiểm tra mạng trong khi vấn đề nằm ở code"
    )
    assert types == [AlertType.INTERNAL_ERROR]


def test_alert_loi_lap_trinh_noi_ro_la_loi_code(tmp_path: Path) -> None:
    """Thông điệp phải nói được PHẢI LÀM GÌ. "TypeError" trần không đủ —
    người đọc alert lúc nửa đêm cần biết ngay đây không phải sự cố chờ
    được."""
    manager = _run_spread(TypeError("boom"), tmp_path)

    (alert,) = manager.sent
    assert alert.severity == "ERROR"
    assert "TypeError" in alert.message
    assert "không phải mất feed" in alert.message
    assert "_check_spread_and_alert" in alert.message


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("connection reset by peer"),
        TimeoutError("read timed out"),
        OSError("network unreachable"),
        RuntimeError("ccxt.NetworkError stand-in"),
    ],
    ids=["ConnectionError", "TimeoutError", "OSError", "RuntimeError"],
)
def test_loi_ha_tang_van_sinh_data_feed_lost(exc: BaseException, tmp_path: Path) -> None:
    """Đột biến ngược: việc thu hẹp KHÔNG được làm mất cảnh báo thật.

    `RuntimeError` đứng thay cho `ccxt.NetworkError`/`ccxt.ExchangeError`
    — cả hai kế thừa `Exception` chứ không phải `TypeError`, nên rơi đúng
    vào nhánh rộng như mọi lỗi hạ tầng khác. Dùng lớp chuẩn thay vì import
    `ccxt` để test không phụ thuộc cây exception của thư viện ngoài.
    """
    manager = _run_spread(exc, tmp_path)

    assert [a.alert_type for a in manager.sent] == [AlertType.DATA_FEED_LOST]


def test_spread_binh_thuong_van_khong_alert(tmp_path: Path) -> None:
    """Đường thành công không bị thu hẹp làm ảnh hưởng."""

    class _OK:
        def get_orderbook(self, symbol: str) -> _FakeOrderBook:
            return _FakeOrderBook("50000", "50005")

    manager = _SpyAlertManager()
    _check_spread_and_alert(
        alert_manager=manager,
        risk_manager=RiskManager(_risk_manager_config(), halt_lock_path=tmp_path / "halt.lock"),
        exchange_client=_OK(),
        symbol=_SYMBOL,
    )

    assert manager.sent == []


# ======================================================================
# Đường lệch đồng hồ — cùng chế độ hỏng
# ======================================================================


def test_clock_drift_loi_lap_trinh_khong_bi_ha_xuong_warning(tmp_path: Path) -> None:
    """Bản cũ hạ MỌI lỗi xuống `warning` — kể cả `AttributeError` khi một
    `ExchangeClient` chưa override `get_server_time()`. Khi đó cổng lệch
    đồng hồ tắt HOÀN TOÀN và chỉ để lại một dòng WARNING mỗi bar."""
    manager = _SpyAlertManager()

    halted, check = _check_clock_drift(
        alert_manager=manager,
        exchange_client=_RaisingExchange(AttributeError("get_server_time")),
        regime_state_logger=None,
        clock_drift_alert_ms=Decimal("1000"),
        clock_drift_halt_ms=Decimal("2500"),
    )

    assert (halted, check) == (False, None)  # hành vi không đổi: không halt
    assert [a.alert_type for a in manager.sent] == [AlertType.INTERNAL_ERROR]


def test_clock_drift_loi_mang_van_chi_la_warning(tmp_path: Path) -> None:
    """Đột biến ngược: mất mạng lúc đo giờ KHÔNG được thành INTERNAL_ERROR
    — nó là sự cố vận hành bình thường, và bắn alert mỗi bar cho nó sẽ làm
    kênh alert vô dụng."""
    manager = _SpyAlertManager()

    halted, check = _check_clock_drift(
        alert_manager=manager,
        exchange_client=_RaisingExchange(ConnectionError("down")),
        regime_state_logger=None,
        clock_drift_alert_ms=Decimal("1000"),
        clock_drift_halt_ms=Decimal("2500"),
    )

    assert (halted, check) == (False, None)
    assert manager.sent == []


# ======================================================================
# Danh sách phân loại
# ======================================================================


def test_danh_sach_loi_lap_trinh_dung_ba_loai() -> None:
    """Ghim danh sách để việc nới nó thành một quyết định CÓ Ý THỨC.

    `ValueError` cố tình KHÔNG có mặt: nó vừa là lỗi lập trình vừa là cách
    hợp lệ để báo dữ liệu đầu vào xấu (`Decimal("abc")`), nên không phân
    loại được nếu chỉ nhìn kiểu. `IndexError` cũng không:
    `response["list"][0]` trên phản hồi rỗng của sàn LÀ sự cố dữ liệu
    thật, không phải bug của ta.
    """
    assert _PROGRAMMING_ERRORS == (TypeError, AttributeError, KeyError)
    assert ValueError not in _PROGRAMMING_ERRORS
    assert IndexError not in _PROGRAMMING_ERRORS
