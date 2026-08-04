"""monitoring.alerts — cảnh báo qua console/log/Telegram/email/webhook.

Telegram là kênh thực tế nhất cho crypto — bot chạy 24/7 nên cần nhận
cảnh báo trên điện thoại. Giới hạn tần suất: 1 cảnh báo mỗi loại sự kiện
mỗi 15 phút.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlertType(Enum):
    REGIME_CHANGE = "REGIME_CHANGE"
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


class AlertManager:
    def __init__(self, rate_limit_seconds: int = 900) -> None:
        ...

    def send(self, alert: Alert) -> None:
        """Gửi qua mọi kênh đã cấu hình, có rate limit theo alert_type."""
        raise NotImplementedError

    def _should_rate_limit(self, alert_type: AlertType) -> bool:
        raise NotImplementedError

    def _send_console(self, alert: Alert) -> None:
        raise NotImplementedError

    def _send_telegram(self, alert: Alert) -> None:
        raise NotImplementedError

    def _send_email(self, alert: Alert) -> None:
        raise NotImplementedError

    def _send_webhook(self, alert: Alert) -> None:
        raise NotImplementedError
