"""Watchdog — tiến trình RIÊNG, phát hiện bot treo/chết. Phase 12d §A.

## Vì sao không phải một thread trong chính bot

Nếu vòng lặp chính deadlock, giữ GIL, hoặc kẹt trong một lệnh gọi mạng
không timeout, thì một `threading.Timer` trong CÙNG tiến trình hoặc kẹt
theo, hoặc vẫn chạy và báo "khoẻ" trong khi bot đã chết. Cả hai đều vô
dụng. **Một tiến trình bị treo không tự phát hiện được rằng nó đang
treo.**

## Vì sao phải tự viết trên macOS

`launchd` có `KeepAlive` — khởi động lại khi tiến trình **thoát**, nhưng
KHÔNG phát hiện được tiến trình **treo**. Không có tương đương
`systemd Type=notify` + `WatchdogSec`. Trên Linux thì dùng systemd, tốt
hơn vì kernel giám sát; module này là đường cho macOS. Cả hai ghi ở
`ops/RUNBOOK.md`.

## Ba quy tắc phát hiện, KHÔNG phải một

1. `mtime` của `heartbeat.json` cũ hơn `stale_after_seconds` → treo.
2. `loop_seq` không tăng qua `stuck_checks` lần kiểm liên tiếp → treo.
   Quy tắc 1 một mình BỎ LỌT trường hợp file vẫn được ghi lại (mtime tươi)
   nhưng vòng lặp đứng — ví dụ một luồng phụ còn sống ghi file trong khi
   luồng chính kẹt.
3. PID trong file không còn tồn tại → bot đã chết.

## KHÔNG tự khởi động lại bot

Watchdog giết bot rồi supervisor khởi động lại ngay sẽ tạo một vòng lặp
crash mà không ai để ý — bot chết và sống lại cả ngàn lần, mỗi lần để lại
một trạng thái dở dang, và biểu đồ uptime trông hoàn hảo. Khởi động lại là
quyết định của CON NGƯỜI sau khi chạy `scripts/recovery_checklist.py`.

## SIGTERM trước, SIGKILL sau — không bao giờ SIGKILL thẳng

Bot có thể đang ở giữa lúc gửi lệnh. `SIGKILL` ngay để lại lệnh mồ côi
trên sàn mà `state_snapshot.json` không kịp ghi. SIGTERM cho nó cơ hội
chạy shutdown handler: đóng kết nối, ghi lại mình đang làm gì.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "state"

HEARTBEAT_FILENAME = "heartbeat.json"
KILL_REPORT_FILENAME = "watchdog_kill.json"

# Lý do kết luận bot không còn sống. Hằng số chứ không phải chuỗi rời rạc:
# `watchdog_kill.json` được đọc lại bởi `recovery_checklist.py`, và hai
# bên gõ tay cùng một chuỗi sẽ lệch nhau đúng lúc cần khớp.
REASON_STALE = "heartbeat_stale"
REASON_STUCK = "loop_seq_stuck"
REASON_PID_GONE = "pid_gone"


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR))


def heartbeat_path() -> Path:
    return state_dir() / HEARTBEAT_FILENAME


def kill_report_path() -> Path:
    return state_dir() / KILL_REPORT_FILENAME


@dataclass(frozen=True)
class WatchdogConfig:
    stale_after_seconds: float = 90.0
    poll_seconds: float = 30.0
    stuck_checks: int = 3
    sigterm_grace_seconds: float = 30.0

    def __post_init__(self) -> None:
        # `poll >= stale` nghĩa là có thể bỏ lỡ trọn một chu kỳ trước khi
        # phát hiện — ngưỡng phát hiện phải luôn có biên so với tần suất
        # kiểm tra, nếu không con số 90s chỉ là trang trí.
        if self.poll_seconds >= self.stale_after_seconds:
            raise ValueError(
                f"poll_seconds ({self.poll_seconds}) phải NHỎ HƠN "
                f"stale_after_seconds ({self.stale_after_seconds})"
            )
        if self.stuck_checks < 2:
            raise ValueError("stuck_checks phải >= 2 — một lần kiểm không nói được gì về 'đứng yên'")


@dataclass(frozen=True)
class Heartbeat:
    pid: int
    updated_at: Optional[datetime]
    bar_ts: Optional[str]
    loop_seq: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class Verdict:
    """`alive=False` -> phải kết thúc bot. `reason` là một trong các hằng
    `REASON_*`; `detail` là câu người đọc."""

    alive: bool
    reason: Optional[str] = None
    detail: str = ""


def write_heartbeat(
    path: Optional[Path] = None,
    *,
    loop_seq: int,
    bar_ts: Optional[str] = None,
    pid: Optional[int] = None,
) -> Path:
    """Bot gọi mỗi vòng lặp. Ghi NGUYÊN TỬ (tmp + rename).

    KHÔNG raise `OSError`: heartbeat là đường quan sát, một volume đầy
    không được phép giết vòng lặp giao dịch. Đánh đổi phải biết — lúc đó
    watchdog sẽ thấy file cũ dần và kết luận bot treo, tức là hệ thống
    nghiêng về phía DỪNG khi không chắc. Đó là hướng nghiêng đúng.
    """
    target = path or heartbeat_path()
    payload = {
        "pid": os.getpid() if pid is None else pid,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "bar_ts": bar_ts,
        "loop_seq": loop_seq,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("Không ghi được %s: %s", target, exc)
    return target


def read_heartbeat(path: Optional[Path] = None) -> Optional[Heartbeat]:
    """`None` khi không có/không đọc được/thiếu `pid` hoặc `loop_seq`.

    Thiếu hai trường đó thì không quyết định được gì — và "không quyết
    định được" phải khác "bot khoẻ", nên caller xử `None` riêng chứ không
    coi là bình thường.
    """
    target = path or heartbeat_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data["pid"])
        loop_seq = int(data["loop_seq"])
    except (KeyError, TypeError, ValueError):
        return None

    updated_at: Optional[datetime] = None
    raw_ts = data.get("updated_at")
    if isinstance(raw_ts, str):
        try:
            updated_at = datetime.fromisoformat(raw_ts)
        except ValueError:
            updated_at = None

    bar_ts = data.get("bar_ts")
    return Heartbeat(
        pid=pid,
        updated_at=updated_at,
        bar_ts=bar_ts if isinstance(bar_ts, str) else None,
        loop_seq=loop_seq,
        raw=data,
    )


def process_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` — không gửi tín hiệu nào, chỉ hỏi kernel.

    `PermissionError` = tiến trình TỒN TẠI nhưng thuộc user khác. Coi nó
    là "còn sống" chứ không phải "đã chết": kết luận sai theo hướng "đã
    chết" sẽ làm watchdog bỏ qua một bot đang thật sự chạy.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def evaluate(
    heartbeat: Optional[Heartbeat],
    *,
    age_seconds: Optional[float],
    same_seq_count: int,
    config: WatchdogConfig,
    alive: Callable[[int], bool] = process_alive,
) -> Verdict:
    """Ba quy tắc §A.3. THUẦN — không đọc file, không đọc đồng hồ, không
    gửi tín hiệu. Mọi đầu vào đi qua tham số.

    Không có heartbeat -> `alive=True` (không kết luận). Đây là lựa chọn
    có chủ ý và ngược với hướng nghiêng ở chỗ khác: bot có thể CHƯA khởi
    động, và một watchdog giết tiến trình dựa trên một file chưa từng tồn
    tại là watchdog tự tạo ra sự cố. Trường hợp "bot chạy rồi file biến
    mất" đã được quy tắc `mtime` bắt trước đó.
    """
    if heartbeat is None:
        return Verdict(alive=True, detail="chưa có heartbeat đọc được — không kết luận")

    if not alive(heartbeat.pid):
        return Verdict(
            alive=False,
            reason=REASON_PID_GONE,
            detail=f"PID {heartbeat.pid} trong heartbeat không còn tồn tại",
        )

    if age_seconds is not None and age_seconds > config.stale_after_seconds:
        return Verdict(
            alive=False,
            reason=REASON_STALE,
            detail=(
                f"heartbeat cũ {age_seconds:.0f}s (> {config.stale_after_seconds:.0f}s), "
                f"PID {heartbeat.pid} vẫn tồn tại — tiến trình sống nhưng không tiến"
            ),
        )

    if same_seq_count >= config.stuck_checks:
        return Verdict(
            alive=False,
            reason=REASON_STUCK,
            detail=(
                f"loop_seq đứng ở {heartbeat.loop_seq} qua {same_seq_count} lần kiểm liên tiếp "
                f"(>= {config.stuck_checks}) dù heartbeat vẫn được ghi lại"
            ),
        )

    return Verdict(alive=True)


def terminate(
    pid: int,
    config: WatchdogConfig,
    *,
    send: Callable[[int, int], None] = os.kill,
    alive: Callable[[int], bool] = process_alive,
    sleep: Callable[[float], None] = time.sleep,
    step_seconds: float = 1.0,
) -> str:
    """SIGTERM → chờ tối đa `sigterm_grace_seconds` → SIGKILL.

    Trả về tín hiệu CUỐI CÙNG đã dùng ("SIGTERM"/"SIGKILL"/"none").

    Poll từng `step_seconds` thay vì ngủ trọn 30 giây: một bot thoát sạch
    trong 2 giây không có lý do gì bắt watchdog chờ thêm 28 giây nữa, và
    trong 28 giây đó supervisor có thể đã khởi động lại một PID mới trùng
    số — lúc đó SIGKILL sẽ bắn nhầm tiến trình.
    """
    try:
        send(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "none"

    da_cho = 0.0
    while da_cho < config.sigterm_grace_seconds:
        sleep(step_seconds)
        da_cho += step_seconds
        if not alive(pid):
            return "SIGTERM"

    try:
        send(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "SIGTERM"
    return "SIGKILL"


def write_kill_report(
    verdict: Verdict,
    heartbeat: Optional[Heartbeat],
    signal_used: str,
    *,
    path: Optional[Path] = None,
) -> Path:
    """Ghi `watchdog_kill.json`. `recovery_checklist.py` đọc lại file này.

    Chứa heartbeat CUỐI CÙNG đọc được nguyên văn — đó là thứ duy nhất còn
    lại nói bot đang xử lý bar nào lúc bị giết.
    """
    target = path or kill_report_path()
    payload = {
        "killed_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": verdict.reason,
        "detail": verdict.detail,
        "signal_used": signal_used,
        "last_heartbeat": heartbeat.raw if heartbeat else None,
        "restarted": False,
        "ghi_chu": (
            "Watchdog KHÔNG khởi động lại bot. Chạy scripts/recovery_checklist.py "
            "trước khi khởi động lại bằng tay."
        ),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.error("Không ghi được %s: %s", target, exc)
    return target


class Watchdog:
    """Trạng thái giữa các lần kiểm (chỉ có `loop_seq` lần trước và bộ
    đếm) — tách thành class thay vì biến toàn cục để test chạy song song
    không giẫm lên nhau."""

    def __init__(
        self,
        config: Optional[WatchdogConfig] = None,
        *,
        heartbeat_file: Optional[Path] = None,
        kill_report_file: Optional[Path] = None,
        alert_manager: Any = None,
    ) -> None:
        self.config = config or WatchdogConfig()
        self._heartbeat_file = heartbeat_file
        self._kill_report_file = kill_report_file
        self._alert_manager = alert_manager
        self._last_seq: Optional[int] = None
        self._same_seq_count = 0

    @property
    def heartbeat_file(self) -> Path:
        return self._heartbeat_file or heartbeat_path()

    def _track_seq(self, loop_seq: int) -> int:
        """Số lần kiểm LIÊN TIẾP thấy cùng một `loop_seq`, tính cả lần này.

        Lần đầu thấy một giá trị trả về 1, không phải 0: "đã thấy giá trị
        này một lần" là thông tin thật, và đếm từ 0 sẽ làm ngưỡng
        `stuck_checks=3` thực tế cần 4 lần.
        """
        if loop_seq == self._last_seq:
            self._same_seq_count += 1
        else:
            self._last_seq = loop_seq
            self._same_seq_count = 1
        return self._same_seq_count

    def check_once(self, *, now: Optional[float] = None) -> Verdict:
        """Một lượt kiểm. KHÔNG kết thúc bot — chỉ trả phán quyết."""
        path = self.heartbeat_file
        heartbeat = read_heartbeat(path)
        if heartbeat is None:
            return evaluate(None, age_seconds=None, same_seq_count=0, config=self.config)

        try:
            age = (time.time() if now is None else now) - path.stat().st_mtime
        except OSError:
            age = None

        return evaluate(
            heartbeat,
            age_seconds=age,
            same_seq_count=self._track_seq(heartbeat.loop_seq),
            config=self.config,
        )

    def handle_dead(self, verdict: Verdict) -> str:
        """Kết thúc bot theo đúng thứ tự §A.4, ghi báo cáo, cảnh báo.
        KHÔNG khởi động lại."""
        heartbeat = read_heartbeat(self.heartbeat_file)
        signal_used = "none"
        if heartbeat is not None and verdict.reason != REASON_PID_GONE:
            signal_used = terminate(heartbeat.pid, self.config)

        write_kill_report(verdict, heartbeat, signal_used, path=self._kill_report_file)
        logger.error(
            "WATCHDOG KILL: %s (%s) — tín hiệu %s. KHÔNG khởi động lại.",
            verdict.reason,
            verdict.detail,
            signal_used,
        )

        if self._alert_manager is not None:
            from monitoring.alerts import Alert, AlertType

            self._alert_manager.send(
                Alert(
                    AlertType.WATCHDOG_KILL,
                    f"Watchdog kết thúc bot: {verdict.detail} (tín hiệu {signal_used}). "
                    "KHÔNG tự khởi động lại — chạy scripts/recovery_checklist.py.",
                    severity="CRITICAL",
                )
            )
        return signal_used

    def run_forever(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_checks: Optional[int] = None,
    ) -> None:
        """`max_checks` CHỈ để test — vòng lặp vô hạn không chạy được trong
        test suite. `None` (mặc định, và là thứ duy nhất vận hành dùng)
        chạy tới khi supervisor dừng.

        Sau một lần kill, KHÔNG break: nếu con người khởi động lại bot,
        heartbeat sẽ tươi trở lại và watchdog tự thôi kích hoạt ở vòng kế
        tiếp — giám sát liên tục, không phải hành động một lần.
        """
        checks = 0
        while max_checks is None or checks < max_checks:
            checks += 1
            sleep(self.config.poll_seconds)
            verdict = self.check_once()
            if not verdict.alive:
                self.handle_dead(verdict)
                # Đặt lại bộ đếm: PID cũ đã chết, chuỗi `loop_seq` cũ
                # không còn nghĩa gì với tiến trình kế tiếp.
                self._last_seq = None
                self._same_seq_count = 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = WatchdogConfig(
        stale_after_seconds=float(os.environ.get("WATCHDOG_STALE_SEC", "90")),
        poll_seconds=float(os.environ.get("WATCHDOG_POLL_SEC", "30")),
    )
    alert_manager = None
    try:
        import main as main_mod

        alert_manager = main_mod.build_alert_manager(main_mod.load_settings())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không dựng được AlertManager (%s) — watchdog vẫn chạy, chỉ log.", exc)

    wd = Watchdog(config, alert_manager=alert_manager)
    logger.info("Watchdog bắt đầu: %s, %s", wd.heartbeat_file, config)
    wd.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
