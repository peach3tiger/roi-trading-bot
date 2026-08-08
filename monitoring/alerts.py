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

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_TIMEOUT_S = 10
_WEBHOOK_TIMEOUT_S = 10
_SMTP_TIMEOUT_S = 10

# Số lần thất bại LIÊN TIẾP của MỘT kênh trước khi hạ trạng thái tổng thể
# xuống "degraded". 3 chứ không phải 1: một lần Telegram 502 hay SMTP
# timeout là chuyện thường ngày và không có nghĩa kênh đã chết — hạ trạng
# thái ngay lần đầu sẽ làm "degraded" thành trạng thái mặc định, và một
# chỉ báo lúc nào cũng đỏ thì không ai đọc nữa.
_DEFAULT_DEGRADED_AFTER = 3

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"

# Khớp `main.py::run_live_loop` và `ops/entrypoint.sh` — cùng một thư mục
# với `state_snapshot.json`/`trading_halted.lock`.
_DEFAULT_STATE_DIR = "state"


def _default_status_path() -> Path:
    """`${STATE_DIR}/status.json`, đọc env ở THỜI ĐIỂM GỌI.

    Không phải hằng số mức module: `STATE_DIR` được đặt lúc chạy
    (`ops/docker-compose.yml` -> `/app/state`), còn module này có thể được
    import trước khi env đó tồn tại. Một hằng số tính lúc import sẽ đóng
    băng giá trị sai và ghi status ra ngoài volume đã mount.

    Trước 2026-08-08 đây là `monitoring/state/status.json` — nằm trong cây
    MÃ NGUỒN. Chuyển sang `STATE_DIR` để mọi state runtime ở cùng một chỗ,
    cùng volume, cùng đường sao lưu.
    """
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR)) / "status.json"


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
    FORWARD_LOG_STALE = "FORWARD_LOG_STALE"
    # LỖI LẬP TRÌNH (TypeError/AttributeError/KeyError), KHÔNG phải sự cố
    # vận hành. Tách riêng khỏi DATA_FEED_LOST/API_LOST vì hai loại này
    # cần hành động khác hẳn nhau: sự cố hạ tầng thì chờ/thử lại, lỗi lập
    # trình thì phải sửa code — và một bug được dán nhãn "mất feed" sẽ
    # được xử lý bằng cách chờ, tức là không bao giờ được xử lý.
    INTERNAL_ERROR = "INTERNAL_ERROR"


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


@dataclass
class ChannelHealth:
    """Sức khoẻ MỘT kênh gửi. KHÔNG `frozen=True`: đây là bộ đếm sống,
    cập nhật tại chỗ sau mỗi lần gửi (khác `Alert`/`EmailConfig` vốn là
    giá trị bất biến).

    `consecutive_failures` reset về 0 khi có một lần gửi THÀNH CÔNG —
    "degraded" phải mô tả tình trạng HIỆN TẠI, không phải lịch sử. Một
    kênh hỏng 50 lần hôm qua rồi khỏi thì không còn degraded; tổng
    `failures` vẫn giữ nguyên để đọc lại được.
    """

    name: str
    attempts: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_success_at: Optional[str] = None

    def record(self, ok: bool, error: Optional[str] = None) -> None:
        self.attempts += 1
        now = datetime.now(timezone.utc).isoformat()
        if ok:
            self.consecutive_failures = 0
            self.last_success_at = now
            return
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = error
        self.last_failure_at = now

    def is_degraded(self, threshold: int) -> bool:
        return self.consecutive_failures >= threshold

    def as_dict(self, threshold: int) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "degraded": self.is_degraded(threshold),
            "last_error": self.last_error,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
        }


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
        status_path: Optional[Path] = None,
        degraded_after: int = _DEFAULT_DEGRADED_AFTER,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.status_path = status_path if status_path is not None else _default_status_path()
        self.degraded_after = degraded_after
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

        # Chỉ theo dõi kênh ĐÃ CẤU HÌNH. Một kênh không bật thì `_send_*`
        # trả về ngay, không phải "thất bại" — đếm nó sẽ làm mọi cài đặt
        # tối thiểu trông như đang degraded vĩnh viễn.
        self._health: dict[str, ChannelHealth] = {}
        if console_enabled:
            self._health["console"] = ChannelHealth("console")
        if self._alert_logger is not None:
            self._health["file"] = ChannelHealth("file")
        if self.telegram_bot_token and self.telegram_chat_id:
            self._health["telegram"] = ChannelHealth("telegram")
        if self.email_config is not None:
            self._health["email"] = ChannelHealth("email")
        if self.webhook_url:
            self._health["webhook"] = ChannelHealth("webhook")

    # ------------------------------------------------------------------
    # Sức khoẻ kênh
    # ------------------------------------------------------------------

    def _record(self, channel: str, ok: bool, error: Optional[str] = None) -> None:
        health = self._health.get(channel)
        if health is None:
            return
        was_degraded = health.is_degraded(self.degraded_after)
        health.record(ok, error)
        now_degraded = health.is_degraded(self.degraded_after)

        # Log ĐÚNG một lần ở mỗi lần đổi trạng thái, không phải mỗi lần
        # thất bại: một kênh chết sẽ thất bại mỗi alert, và log mỗi lần
        # biến chính dòng log đó thành nhiễu.
        if now_degraded and not was_degraded:
            logger.error(
                "Kênh cảnh báo %r DEGRADED — %d lần thất bại liên tiếp. Cảnh báo đang bị mất qua kênh này.",
                channel,
                health.consecutive_failures,
            )
        elif was_degraded and not now_degraded:
            logger.info("Kênh cảnh báo %r đã hồi phục.", channel)

    def status(self) -> str:
        """`"degraded"` nếu BẤT KỲ kênh nào vượt ngưỡng, ngược lại `"ok"`.

        KHÔNG có kênh nào cũng là `"degraded"`: đó là dạng cực đoan nhất
        của chính thứ cơ chế này sinh ra để chặn — 100% cảnh báo đi vào hư
        không, mà chỉ báo vẫn xanh. Một `AlertManager` không kênh nào
        không phải "khoẻ", nó là "câm".
        """
        if not self._health:
            return STATUS_DEGRADED
        if any(h.is_degraded(self.degraded_after) for h in self._health.values()):
            return STATUS_DEGRADED
        return STATUS_OK

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "degraded_after_consecutive_failures": self.degraded_after,
            "written_at_utc": datetime.now(timezone.utc).isoformat(),
            "channels": {name: h.as_dict(self.degraded_after) for name, h in sorted(self._health.items())},
        }

    def write_status(self, path: Optional[Path] = None) -> None:
        """Ghi NGUYÊN TỬ (tmp + rename) — cùng lý do
        `main.py::write_state_snapshot`: crash giữa lúc ghi không được để
        lại JSON nửa vời cho thứ đọc nó ở lần sau.

        KHÔNG BAO GIỜ raise: hàm này chạy bên trong `send()`, vốn cam kết
        tuyệt đối không raise. Một đĩa đầy không được phép làm crash vòng
        lặp giao dịch đang cố báo một sự cố khác.
        """
        target = path if path is not None else self.status_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(self.health_snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
        except Exception as exc:
            logger.warning("Không ghi được %s: %s", target, exc)

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

        Giá trị trả về CỐ TÌNH không phản ánh kênh nào thất bại: nó chỉ
        nói "alert này có bị nén hay không". Thất bại từng kênh nằm ở
        `status()`/`health_snapshot()`/`${STATE_DIR}/status.json` —
        cam kết "không bao giờ raise" bảo vệ vòng lặp giao dịch, nhưng
        KHÔNG được đổi lấy việc mất cảnh báo một cách vô hình.

        **Thứ tự các kênh có ý nghĩa.** Kênh FILE (`logs/alerts.log`) là
        đường cuối cùng — bản ghi bền duy nhất còn lại khi mọi kênh từ xa
        chết — nên nó được thử TRƯỚC và nằm trong try RIÊNG của nó. Đặt
        nó sau, hoặc chung try với Telegram/webhook, nghĩa là một sự cố
        mạng có thể cuốn theo cả bản ghi bền: đúng kịch bản "mất cảnh báo
        vô hình" mà cấu trúc này sinh ra để chặn.
        """
        if self._should_rate_limit(alert.alert_type):
            logger.debug(
                "Alert %s bị rate-limit (< %ss kể từ lần trước), bỏ qua.",
                alert.alert_type.value,
                self.rate_limit_seconds,
            )
            return False
        self._record_sent(alert.alert_type)

        # 1. Kênh cục bộ, mỗi kênh một try riêng.
        self._send_file(alert)
        if self.console_enabled:
            self._send_console(alert)

        # 2. Kênh từ xa — có thể chết vì mạng, không được ảnh hưởng mục 1.
        self._send_telegram(alert)
        self._send_email(alert)
        self._send_webhook(alert)

        self.write_status()
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

    def _send_file(self, alert: Alert) -> None:
        """Bản ghi BỀN — đường cuối cùng khi mọi kênh từ xa chết.

        Try RIÊNG, không dùng chung với Telegram/email/webhook. Bản trước
        gọi `self._alert_logger.info(...)` KHÔNG có try nào: đĩa đầy hoặc
        rotating handler lỗi sẽ ném thẳng ra khỏi `send()` và phá vỡ cam
        kết "không bao giờ raise" — đúng ở kênh quan trọng nhất.
        """
        if self._alert_logger is None:
            return
        try:
            self._alert_logger.info(
                alert.message,
                extra={
                    "event": "alert",
                    "alert_type": alert.alert_type.value,
                    "severity": alert.severity,
                },
            )
            self._record("file", ok=True)
        except Exception as exc:
            # Không dùng `logger.warning` với `exc_info` ở đây: nếu chính
            # hệ thống logging đang hỏng thì ghi thêm cũng vô ích — bộ đếm
            # trong status.json là thứ còn lại đọc được.
            self._record("file", ok=False, error=f"{type(exc).__name__}: {exc}")
            logger.warning("Ghi alert vào file thất bại cho %s: %s", alert.alert_type.value, exc)

    def _send_console(self, alert: Alert) -> None:
        try:
            _console_logger.info("[%s] %s: %s", alert.severity, alert.alert_type.value, alert.message)
            self._record("console", ok=True)
        except Exception as exc:
            self._record("console", ok=False, error=f"{type(exc).__name__}: {exc}")

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
                # HTTP != 200 LÀ thất bại. Bản trước chỉ log rồi đi tiếp,
                # nên một bot_token bị revoke (401 mọi lần) trông y hệt
                # gửi thành công ở mọi chỗ khác trong hệ thống.
                self._record("telegram", ok=False, error=f"HTTP {response.status_code}")
            else:
                self._record("telegram", ok=True)
        except Exception as exc:
            self._record("telegram", ok=False, error=f"{type(exc).__name__}: {exc}")
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
            self._record("email", ok=True)
        except Exception as exc:
            self._record("email", ok=False, error=f"{type(exc).__name__}: {exc}")
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
            response = requests.post(
                self.webhook_url,
                json={
                    "alert_type": alert.alert_type.value,
                    "message": alert.message,
                    "severity": alert.severity,
                },
                timeout=_WEBHOOK_TIMEOUT_S,
            )
            # Cùng lý do `_send_telegram`: một endpoint trả 500 mọi lần LÀ
            # kênh đã chết, không phải "đã gửi".
            if response.status_code >= 400:
                logger.warning(
                    "Gửi webhook thất bại (status=%s) cho alert %s",
                    response.status_code,
                    alert.alert_type.value,
                )
                self._record("webhook", ok=False, error=f"HTTP {response.status_code}")
            else:
                self._record("webhook", ok=True)
        except Exception as exc:
            self._record("webhook", ok=False, error=f"{type(exc).__name__}: {exc}")
            # Bắt RỘNG — cùng lý do `_send_telegram`.
            logger.warning("Gửi webhook thất bại cho alert %s: %s", alert.alert_type.value, exc)
