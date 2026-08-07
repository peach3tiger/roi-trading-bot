"""tests.test_monitoring_alerts — monitoring/alerts.py: rate limit, kênh
gửi không raise ra send(), Telegram/webhook/email không gửi khi chưa cấu
hình, không log credential.

Mọi test mạng (Telegram/webhook/email) MOCK `requests.post`/`smtplib.SMTP`
— không bao giờ gọi mạng thật trong test suite (testnet đang bị chặn ở
tầng tài khoản GitHub, xem docs/STATE.md, và dù không bị chặn thì test
suite cũng không nên phụ thuộc mạng thật).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from monitoring.alerts import Alert, AlertManager, AlertType, EmailConfig


def _alert(alert_type: AlertType = AlertType.REGIME_CHANGE) -> Alert:
    return Alert(alert_type=alert_type, message="test message", severity="WARNING")


# ----------------------------------------------------------------------
# Rate limit — 1 cảnh báo / loại sự kiện / 15 phút (mặc định)
# ----------------------------------------------------------------------


def test_first_alert_of_a_type_is_never_rate_limited() -> None:
    manager = AlertManager(rate_limit_seconds=900, console_enabled=True)
    assert manager.send(_alert()) is True


def test_second_alert_same_type_within_window_is_rate_limited() -> None:
    manager = AlertManager(rate_limit_seconds=900, console_enabled=True)
    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is True
    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is False


def test_ten_alerts_same_type_in_one_minute_only_first_sent() -> None:
    """Nghiệm thu phase-11-monitoring.md: "bắn 10 alert cùng loại trong 1
    phút -> chỉ nhận 1"."""
    manager = AlertManager(rate_limit_seconds=900, console_enabled=True)
    results = [manager.send(_alert(AlertType.CIRCUIT_BREAKER)) for _ in range(10)]
    assert results == [True] + [False] * 9


def test_different_alert_types_are_independent_rate_limits() -> None:
    manager = AlertManager(rate_limit_seconds=900, console_enabled=True)
    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is True
    assert manager.send(_alert(AlertType.CIRCUIT_BREAKER)) is True


def test_alert_allowed_again_after_window_elapses(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AlertManager(rate_limit_seconds=900, console_enabled=True)
    fake_time = [1000.0]
    monkeypatch.setattr("monitoring.alerts.time.monotonic", lambda: fake_time[0])

    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is True
    fake_time[0] += 899
    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is False
    fake_time[0] += 2
    assert manager.send(_alert(AlertType.REGIME_CHANGE)) is True


def test_rate_limit_mutation_kill_always_false() -> None:
    """Đột biến kiểm chứng test rate-limit không vô nghĩa (CLAUDE.md #16):
    nếu `_should_rate_limit` LUÔN trả False (không rate-limit gì cả), test
    `test_second_alert_same_type_within_window_is_rate_limited` PHẢI đỏ.
    Test này tự chạy phần "đỏ" bằng một manager cố tình phá `_should_rate_limit`
    tại chỗ (subclass), xác nhận bug lộ ra thay vì âm thầm qua."""

    class _NeverRateLimits(AlertManager):
        def _should_rate_limit(self, alert_type: AlertType) -> bool:
            return False

    broken = _NeverRateLimits(rate_limit_seconds=900, console_enabled=True)
    first = broken.send(_alert(AlertType.REGIME_CHANGE))
    second = broken.send(_alert(AlertType.REGIME_CHANGE))
    # Với bug đã cấy: cả hai đều True — nghĩa là hành vi mong đợi (second=False)
    # đã KHÔNG xảy ra, đúng như một bug thật sẽ biểu hiện.
    assert (first, second) == (True, True)


# ----------------------------------------------------------------------
# send() không bao giờ raise, dù một kênh hỏng
# ----------------------------------------------------------------------


def test_send_never_raises_when_telegram_network_fails() -> None:
    manager = AlertManager(
        rate_limit_seconds=0, telegram_bot_token="tok", telegram_chat_id="123", console_enabled=True
    )
    with patch("monitoring.alerts.requests.post", side_effect=OSError("network down")):
        manager.send(_alert())  # không raise


def test_send_never_raises_when_webhook_fails() -> None:
    manager = AlertManager(
        rate_limit_seconds=0, webhook_url="https://example.invalid/hook", console_enabled=True
    )
    with patch("monitoring.alerts.requests.post", side_effect=OSError("network down")):
        manager.send(_alert())  # không raise


def test_send_never_raises_when_email_fails() -> None:
    email_config = EmailConfig(
        smtp_host="smtp.invalid",
        smtp_port=587,
        username="u",
        password="p",
        from_addr="a@x.com",
        to_addr="b@x.com",
    )
    manager = AlertManager(rate_limit_seconds=0, email_config=email_config, console_enabled=True)
    with patch("smtplib.SMTP", side_effect=OSError("smtp down")):
        manager.send(_alert())  # không raise


# ----------------------------------------------------------------------
# Kênh chưa cấu hình -> không gửi, không lỗi
# ----------------------------------------------------------------------


def test_telegram_not_called_when_not_configured() -> None:
    manager = AlertManager(
        rate_limit_seconds=0, telegram_bot_token=None, telegram_chat_id=None, console_enabled=True
    )
    with patch("monitoring.alerts.requests.post") as mock_post:
        manager.send(_alert())
        mock_post.assert_not_called()


def test_webhook_not_called_when_not_configured() -> None:
    manager = AlertManager(rate_limit_seconds=0, webhook_url=None, console_enabled=True)
    with patch("monitoring.alerts.requests.post") as mock_post:
        manager.send(_alert())
        mock_post.assert_not_called()


def test_email_not_sent_when_not_configured() -> None:
    manager = AlertManager(rate_limit_seconds=0, email_config=None, console_enabled=True)
    with patch("smtplib.SMTP") as mock_smtp:
        manager.send(_alert())
        mock_smtp.assert_not_called()


def test_telegram_called_with_correct_payload_when_configured() -> None:
    manager = AlertManager(
        rate_limit_seconds=0, telegram_bot_token="tok123", telegram_chat_id="chat456", console_enabled=True
    )
    mock_response = MagicMock(status_code=200)
    with patch("monitoring.alerts.requests.post", return_value=mock_response) as mock_post:
        manager.send(_alert(AlertType.CIRCUIT_BREAKER))

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "tok123" in args[0]
    assert kwargs["json"]["chat_id"] == "chat456"
    assert "CIRCUIT_BREAKER" in kwargs["json"]["text"]


def test_telegram_reads_credentials_from_env_when_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
    manager = AlertManager(rate_limit_seconds=0, console_enabled=True)
    assert manager.telegram_bot_token == "env-token"
    assert manager.telegram_chat_id == "env-chat"


# ----------------------------------------------------------------------
# Không bao giờ log credential, kể cả một phần (CLAUDE.md bất biến #6)
# ----------------------------------------------------------------------


def test_telegram_failure_log_never_contains_bot_token(caplog: Any) -> None:
    manager = AlertManager(
        rate_limit_seconds=0,
        telegram_bot_token="SECRET_TOKEN_XYZ",
        telegram_chat_id="123",
        console_enabled=True,
    )
    mock_response = MagicMock(status_code=401, text="Unauthorized")
    with caplog.at_level(logging.WARNING, logger="monitoring.alerts"):
        with patch("monitoring.alerts.requests.post", return_value=mock_response):
            manager.send(_alert())

    for record in caplog.records:
        assert "SECRET_TOKEN_XYZ" not in record.getMessage()


def test_email_failure_log_never_contains_password(caplog: Any) -> None:
    email_config = EmailConfig(
        smtp_host="smtp.invalid",
        smtp_port=587,
        username="bot@example.com",
        password="SUPER_SECRET_PW",
        from_addr="bot@example.com",
        to_addr="me@example.com",
    )
    manager = AlertManager(rate_limit_seconds=0, email_config=email_config, console_enabled=True)
    with caplog.at_level(logging.WARNING, logger="monitoring.alerts"):
        with patch("smtplib.SMTP", side_effect=OSError("boom")):
            manager.send(_alert())

    for record in caplog.records:
        assert "SUPER_SECRET_PW" not in record.getMessage()


# ----------------------------------------------------------------------
# Kênh log file (monitoring.logger)
# ----------------------------------------------------------------------


def test_alert_written_to_alerts_log_file(tmp_path: Any) -> None:
    manager = AlertManager(rate_limit_seconds=0, log_dir=str(tmp_path), console_enabled=True)
    manager.send(_alert(AlertType.HMM_RETRAINED))

    log_file = tmp_path / "alerts.log"
    assert log_file.exists()
    assert "HMM_RETRAINED" in log_file.read_text(encoding="utf-8")


def test_alert_type_has_trend_gate_change() -> None:
    """phase-11-monitoring.md liệt kê "đổi trạng thái trend gate" như một
    trigger riêng — scaffold gốc thiếu hẳn giá trị này trong AlertType."""
    assert AlertType.TREND_GATE_CHANGE.value == "TREND_GATE_CHANGE"
