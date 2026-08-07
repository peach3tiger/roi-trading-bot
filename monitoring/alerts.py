"""monitoring.alerts — cảnh báo qua console/log/Telegram/email/webhook.

Telegram là kênh thực tế nhất cho crypto — bot chạy 24/7 nên cần nhận
cảnh báo trên điện thoại. Giới hạn tần suất: 1 cảnh báo mỗi loại sự kiện
mỗi 15 phút.

Rate limit áp dụng cho CẢ ALERT (mọi kênh cùng lúc), không phải riêng
từng kênh — một alert bị rate-limit thì KHÔNG kênh nào gửi, tránh Telegram
im lặng trong khi console vẫn xả liên tục (hoặc ngược lại) cho cùng một
`AlertType`.

Không dùng `print()` ở đây (CLAUDE.md: "không print() trong code
production", và nghiệm thu của phase-11-monitoring.md chạy đúng
`grep -rn "print(" monitoring/`) — kênh console dùng một `logging.Logger`
console-only riêng (`_console_logger`, StreamHandler, không rotating file)
thay vì `print()` trực tiếp.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_TIMEOUT_S = 10
_WEBHOOK_TIMEOUT_S = 10
_SMTP_TIMEOUT_S = 10


class AlertType(Enum):
    REGIME_CHANGE = "REGIME_CHANGE"
    TREND_GATE_CHANGE = "TREND_GATE_CHANGE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    LARGE_PNL = "LARGE_PNL"
    DATA_FEED_LOST = "DATA_FEED_LOST"
    API_LOST = "API_LOST"
    HMM_RETRAINED = "HMM_RETRAINED"
    FLICKER_THRESHOLD_EXCEEDED = "FLICKER_THRESHOLD_EXCEEDED"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"
    ABNORMAL_SPREAD = "ABNORMAL_SPREAD"
    CLOCK_DRIFT = "CLOCK_DRIFT"


@dataclass(frozen=True)
class Alert:
    alert_type: AlertType
    message: str
    severity: str


@dataclass(frozen=True)
class EmailConfig:
    """Tuỳ chọn — `AlertManager.email_config=None` (mặc định) tắt hẳn kênh
    email, `_send_email` no-op ngay từ đầu. Không giá trị mặc định cho
    field nào: một EmailConfig thiếu `smtp_host` phải lỗi ngay lúc dựng,
    không phải lúc gửi alert đầu tiên giữa đêm."""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_addr: str
    to_addr: str


# Console riêng biệt khỏi log file (`monitoring.logger`) — StreamHandler,
# không rotating, dựng MỘT LẦN ở mức module (không phải trong
# AlertManager.__init__) để nhiều instance AlertManager trong cùng tiến
# trình không cộng dồn handler (cùng lý do idempotent của
# `monitoring.logger.get_logger`).
_console_logger = logging.getLogger("monitoring.alerts.console")
if not _console_logger.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter("%(message)s"))
    _console_logger.addHandler(_console_handler)
    _console_logger.propagate = False
    _console_logger.setLevel(logging.INFO)


class AlertManager:
    """Credential Telegram đọc từ env (`TELEGRAM_BOT_TOKEN`/
    `TELEGRAM_CHAT_ID`) nếu không truyền tường minh — cùng quy ước
    `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` (CLAUDE.md bất biến #6:
    không hardcode credentials, không log giá trị kể cả một phần).
    """

    def __init__(
        self,
        rate_limit_seconds: int = 900,
        *,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        email_config: Optional[EmailConfig] = None,
        webhook_url: Optional[str] = None,
        log_dir: Optional[str] = None,
        console_enabled: bool = True,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.telegram_bot_token = (
            telegram_bot_token
            if telegram_bot_token is not None
            else (os.environ.get("TELEGRAM_BOT_TOKEN") or None)
        )
        self.telegram_chat_id = (
            telegram_chat_id
            if telegram_chat_id is not None
            else (os.environ.get("TELEGRAM_CHAT_ID") or None)
        )
        self.email_config = email_config
        self.webhook_url = webhook_url
        self.console_enabled = console_enabled

        self._alert_logger: Optional[logging.Logger] = None
        if log_dir is not None:
            from monitoring.logger import get_logger

            self._alert_logger = get_logger("alerts", log_dir)

        # `time.monotonic()` — KHÔNG dùng `datetime.now()`/wall clock:
        # rate-limit không được phép co giãn theo NTP step hay lệch múi
        # giờ hệ thống, chỉ cần "đã trôi qua bao lâu" (cùng lý do
        # `broker/ccxt_client.py::_call_with_retry` dùng `time.monotonic`
        # cho backoff).
        self._last_sent_monotonic: dict[AlertType, float] = {}

    # ------------------------------------------------------------------

    def send(self, alert: Alert) -> bool:
        """Gửi qua mọi kênh đã cấu hình, có rate limit theo alert_type.

        Trả về True nếu ĐÃ gửi (qua ít nhất kênh console/log), False nếu
        bị rate-limit — caller (main.py) có thể dùng giá trị này để quyết
        định có log thêm "alert bị nén" hay không, không bắt buộc.

        Không bao giờ raise ra ngoài: một kênh cảnh báo hỏng (Telegram
        sập, SMTP timeout, webhook 500) không được phép làm crash vòng
        lặp chính đang cố BÁO rằng có sự cố — mỗi `_send_*` tự bắt lỗi
        riêng của kênh mình.
        """
        if self._should_rate_limit(alert.alert_type):
            logger.debug(
                "Alert %s bị rate-limit (< %ss kể từ lần trước), bỏ qua.",
                alert.alert_type.value,
                self.rate_limit_seconds,
            )
            return False
        self._record_sent(alert.alert_type)

        if self.console_enabled:
            self._send_console(alert)
        if self._alert_logger is not None:
            self._alert_logger.info(
                alert.message,
                extra={
                    "event": "alert",
                    "alert_type": alert.alert_type.value,
                    "severity": alert.severity,
                },
            )
        self._send_telegram(alert)
        self._send_email(alert)
        self._send_webhook(alert)
        return True

    def _should_rate_limit(self, alert_type: AlertType) -> bool:
        last = self._last_sent_monotonic.get(alert_type)
        if last is None:
            return False
        return (time.monotonic() - last) < self.rate_limit_seconds

    def _record_sent(self, alert_type: AlertType) -> None:
        self._last_sent_monotonic[alert_type] = time.monotonic()

    # ------------------------------------------------------------------
    # Kênh gửi — mỗi hàm tự bắt lỗi riêng, không bao giờ để lộ ra send()
    # ------------------------------------------------------------------

    def _send_console(self, alert: Alert) -> None:
        _console_logger.info("[%s] %s: %s", alert.severity, alert.alert_type.value, alert.message)

    def _send_telegram(self, alert: Alert) -> None:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        text = f"[{alert.severity}] {alert.alert_type.value}\n{alert.message}"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.telegram_chat_id, "text": text},
                timeout=_TELEGRAM_TIMEOUT_S,
            )
            if response.status_code != 200:
                # KHÔNG log `url`/token — url chứa bot_token nguyên văn.
                # Chỉ log status + alert_type, không log response.text (có
                # thể phản chiếu lại một phần request tuỳ API Telegram).
                logger.warning(
                    "Gửi Telegram thất bại (status=%s) cho alert %s",
                    response.status_code,
                    alert.alert_type.value,
                )
        except Exception as exc:
            # Bắt RỘNG (không chỉ requests.RequestException): `send()` cam
            # kết "không bao giờ raise ra ngoài" (xem docstring) — một lỗi
            # bất ngờ (DNS, socket lạ, bug thư viện) ở kênh Telegram không
            # được phép làm crash vòng lặp chính đang cố cảnh báo về một
            # sự cố khác. Phát hiện qua test (`test_send_never_raises_when_
            # telegram_network_fails` đỏ khi mock ném OSError thay vì đúng
            # `requests.RequestException`) — không phải suy luận trước.
            logger.warning("Gửi Telegram lỗi cho alert %s: %s", alert.alert_type.value, exc)

    def _send_email(self, alert: Alert) -> None:
        if self.email_config is None:
            return
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(alert.message)
        msg["Subject"] = f"[{alert.severity}] {alert.alert_type.value}"
        msg["From"] = self.email_config.from_addr
        msg["To"] = self.email_config.to_addr
        try:
            with smtplib.SMTP(
                self.email_config.smtp_host, self.email_config.smtp_port, timeout=_SMTP_TIMEOUT_S
            ) as smtp:
                smtp.starttls()
                smtp.login(self.email_config.username, self.email_config.password)
                smtp.send_message(msg)
        except Exception as exc:
            # Bắt RỘNG (không chỉ SMTPException/OSError) — cùng lý do
            # `_send_telegram`/`_send_webhook`: "không bao giờ raise ra
            # ngoài" là cam kết tuyệt đối, không phải "trừ khi thư viện
            # ném một loại exception tôi chưa liệt kê". KHÔNG log
            # self.email_config.password, kể cả một phần.
            logger.warning("Gửi email thất bại cho alert %s: %s", alert.alert_type.value, exc)

    def _send_webhook(self, alert: Alert) -> None:
        if not self.webhook_url:
            return
        try:
            requests.post(
                self.webhook_url,
                json={
                    "alert_type": alert.alert_type.value,
                    "message": alert.message,
                    "severity": alert.severity,
                },
                timeout=_WEBHOOK_TIMEOUT_S,
            )
        except Exception as exc:
            # Bắt RỘNG — cùng lý do `_send_telegram`.
            logger.warning("Gửi webhook thất bại cho alert %s: %s", alert.alert_type.value, exc)
