"""Sức khoẻ kênh cảnh báo — "không bao giờ raise" không được đổi lấy
việc mất cảnh báo một cách VÔ HÌNH.

Cam kết "không bao giờ raise" của `AlertManager.send()` bảo vệ vòng lặp
giao dịch: một kênh alert hỏng không được làm crash bot đang cố báo một
sự cố khác. Nhưng bản trước trả giá bằng sự im lặng hoàn toàn — Telegram
trả 401 mọi lần (token bị revoke) trông y hệt gửi thành công ở mọi chỗ
khác trong hệ thống.

Ba thứ file này khoá lại:
  1. Kênh FILE luôn được thử, kể cả khi kênh từ xa nổ — và nằm trong try
     RIÊNG, không chung với kênh từ xa.
  2. Thất bại được ĐẾM theo từng kênh, ghi ra `status.json`.
  3. N lần thất bại liên tiếp -> `status = "degraded"`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from monitoring.alerts import (
    STATUS_DEGRADED,
    STATUS_OK,
    Alert,
    AlertManager,
    AlertType,
)

_ALERT = Alert(AlertType.API_LOST, "sự cố thử nghiệm", severity="ERROR")


def _manager(tmp_path: Path, **kwargs: Any) -> AlertManager:
    """AlertManager với status.json trong tmp — KHÔNG dùng đường mặc định
    (`monitoring/state/status.json` trong cây nguồn) để test không ghi đè
    trạng thái thật của máy đang chạy."""
    kwargs.setdefault("rate_limit_seconds", 0)
    kwargs.setdefault("console_enabled", False)
    kwargs.setdefault("log_dir", str(tmp_path / "logs"))
    return AlertManager(status_path=tmp_path / "status.json", **kwargs)


def _read_status(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))


# ======================================================================
# 1. Kênh file là đường cuối cùng
# ======================================================================


def test_kenh_file_van_ghi_khi_telegram_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM: kênh từ xa chết không được cuốn theo bản ghi bền."""
    import monitoring.alerts as alerts_mod

    def _boom(*a: Any, **k: Any) -> Any:
        raise ConnectionError("telegram down")

    monkeypatch.setattr(alerts_mod.requests, "post", _boom)

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c")
    manager.send(_ALERT)

    log_text = (tmp_path / "logs" / "alerts.log").read_text(encoding="utf-8")
    assert "sự cố thử nghiệm" in log_text, "kênh file phải ghi được dù Telegram nổ"

    status = _read_status(tmp_path)
    assert status["channels"]["file"]["failures"] == 0
    assert status["channels"]["telegram"]["failures"] == 1


def test_kenh_file_hong_khong_lam_send_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản trước gọi `self._alert_logger.info(...)` KHÔNG có try nào —
    đĩa đầy hay rotating handler lỗi sẽ ném thẳng ra khỏi `send()`, phá
    cam kết "không bao giờ raise" ở đúng kênh quan trọng nhất."""
    manager = _manager(tmp_path)
    assert manager._alert_logger is not None
    monkeypatch.setattr(
        manager._alert_logger,
        "info",
        lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")),
    )

    manager.send(_ALERT)  # không được raise

    assert _read_status(tmp_path)["channels"]["file"]["failures"] == 1


def test_kenh_file_hong_khong_chan_kenh_tu_xa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chiều ngược lại: file hỏng cũng không được nuốt mất kênh từ xa.

    Đây là điều mà "cùng một try" sẽ phá — và là lý do mỗi kênh phải có
    try riêng, không chỉ tách cục bộ khỏi từ xa.
    """
    import monitoring.alerts as alerts_mod

    posted: list[str] = []

    class _Resp:
        status_code = 200

    monkeypatch.setattr(alerts_mod.requests, "post", lambda *a, **k: (posted.append("sent"), _Resp())[1])

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c")
    assert manager._alert_logger is not None
    monkeypatch.setattr(manager._alert_logger, "info", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))

    manager.send(_ALERT)

    assert posted == ["sent"], "Telegram vẫn phải được thử khi kênh file hỏng"


# ======================================================================
# 2. Đếm thất bại theo từng kênh
# ======================================================================


def test_status_json_duoc_ghi_moi_lan_send(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert not (tmp_path / "status.json").exists()

    manager.send(_ALERT)

    status = _read_status(tmp_path)
    assert status["status"] == STATUS_OK
    assert status["channels"]["file"]["attempts"] == 1
    assert "written_at_utc" in status


def test_http_khac_200_duoc_tinh_la_that_bai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản trước chỉ `logger.warning` rồi đi tiếp, nên một `bot_token` bị
    revoke (401 mọi lần) trông y hệt gửi thành công."""
    import monitoring.alerts as alerts_mod

    class _Unauthorized:
        status_code = 401

    monkeypatch.setattr(alerts_mod.requests, "post", lambda *a, **k: _Unauthorized())

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c")
    manager.send(_ALERT)

    telegram = _read_status(tmp_path)["channels"]["telegram"]
    assert telegram["failures"] == 1
    assert telegram["last_error"] == "HTTP 401"


def test_kenh_chua_cau_hinh_khong_bi_dem(tmp_path: Path) -> None:
    """Telegram/email/webhook không bật thì `_send_*` trả về ngay — đó
    KHÔNG phải thất bại. Đếm chúng sẽ làm mọi cài đặt tối thiểu trông như
    degraded vĩnh viễn."""
    manager = _manager(tmp_path)
    manager.send(_ALERT)

    channels = _read_status(tmp_path)["channels"]
    assert set(channels) == {"file"}


def test_thanh_cong_reset_chuoi_that_bai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ "degraded" mô tả tình trạng HIỆN TẠI, không phải lịch sử. Tổng
    `failures` vẫn giữ để đọc lại được."""
    import monitoring.alerts as alerts_mod

    class _Resp:
        status_code = 200

    state = {"fail": True}

    def _post(*a: Any, **k: Any) -> Any:
        if state["fail"]:
            raise ConnectionError("down")
        return _Resp()

    monkeypatch.setattr(alerts_mod.requests, "post", _post)
    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c")

    manager.send(_ALERT)
    manager.send(_ALERT)
    state["fail"] = False
    manager.send(_ALERT)

    telegram = _read_status(tmp_path)["channels"]["telegram"]
    assert telegram["consecutive_failures"] == 0
    assert telegram["failures"] == 2  # lịch sử KHÔNG bị xoá
    assert telegram["degraded"] is False


# ======================================================================
# 3. Ngưỡng degraded
# ======================================================================


@pytest.mark.parametrize(
    "n_fails,expected",
    [(1, STATUS_OK), (2, STATUS_OK), (3, STATUS_DEGRADED), (7, STATUS_DEGRADED)],
)
def test_nguong_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n_fails: int, expected: str
) -> None:
    """Ngưỡng 3 chứ không phải 1: một lần Telegram 502 là chuyện thường
    ngày. Hạ trạng thái ngay lần đầu biến "degraded" thành mặc định, và
    một chỉ báo lúc nào cũng đỏ thì không ai đọc nữa."""
    import monitoring.alerts as alerts_mod

    monkeypatch.setattr(
        alerts_mod.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
    )

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c", degraded_after=3)
    for _ in range(n_fails):
        manager.send(_ALERT)

    assert manager.status() == expected
    assert _read_status(tmp_path)["status"] == expected


def test_mot_kenh_degraded_lam_ca_he_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kênh file vẫn khoẻ, nhưng Telegram chết = cảnh báo KHÔNG tới điện
    thoại. Trạng thái tổng thể phải phản ánh điều đó."""
    import monitoring.alerts as alerts_mod

    monkeypatch.setattr(
        alerts_mod.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
    )

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c", degraded_after=2)
    manager.send(_ALERT)
    manager.send(_ALERT)

    status = _read_status(tmp_path)
    assert status["status"] == STATUS_DEGRADED
    assert status["channels"]["file"]["degraded"] is False
    assert status["channels"]["telegram"]["degraded"] is True


def test_khong_co_kenh_nao_la_degraded(tmp_path: Path) -> None:
    """Dạng cực đoan nhất của "mất cảnh báo vô hình": không kênh nào được
    cấu hình, 100% alert đi vào hư không, mà chỉ báo vẫn xanh.

    Không xảy ra ở vận hành thật (`build_alert_manager` luôn truyền
    `log_dir`, console mặc định bật) — nhưng một `AlertManager` không kênh
    nào KHÔNG phải "khoẻ", nó là "câm", và trạng thái phải nói ra.
    """
    manager = AlertManager(
        rate_limit_seconds=0,
        console_enabled=False,
        log_dir=None,
        status_path=tmp_path / "status.json",
    )

    assert manager.status() == STATUS_DEGRADED

    manager.send(_ALERT)
    status = _read_status(tmp_path)
    assert status["status"] == STATUS_DEGRADED
    assert status["channels"] == {}


def test_status_ghi_nguyen_tu_khong_de_lai_tmp(tmp_path: Path) -> None:
    """tmp + rename — crash giữa lúc ghi không được để lại JSON nửa vời."""
    manager = _manager(tmp_path)
    manager.send(_ALERT)

    assert (tmp_path / "status.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []
    _read_status(tmp_path)  # parse được = không nửa vời


def test_write_status_khong_bao_gio_raise(tmp_path: Path) -> None:
    """Đường ghi status nằm TRONG `send()`, nên nó cũng phải tuân cam kết
    "không bao giờ raise" — một đĩa đầy không được làm crash vòng lặp
    giao dịch đang cố báo một sự cố khác."""
    manager = _manager(tmp_path)
    # Thư mục không ghi được: `status.json` là FILE, giờ dùng nó làm cha.
    blocker = tmp_path / "blocked"
    blocker.write_text("không phải thư mục", encoding="utf-8")
    manager.status_path = blocker / "status.json"

    manager.send(_ALERT)  # không được raise


def test_send_van_tra_true_khi_kenh_that_bai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hợp đồng cũ của `send()` KHÔNG đổi: giá trị trả về chỉ nói alert có
    bị rate-limit hay không. Thất bại từng kênh nằm ở `status()` —
    `_SpyAlertManager` trong test suite dựa vào hành vi này."""
    import monitoring.alerts as alerts_mod

    monkeypatch.setattr(
        alerts_mod.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
    )

    manager = _manager(tmp_path, telegram_bot_token="t", telegram_chat_id="c")

    assert manager.send(_ALERT) is True
    assert manager.status() == STATUS_OK  # 1 lần chưa đủ ngưỡng
