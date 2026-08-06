"""monitoring.watchdog — giám sát bot còn sống qua mốc thời gian status file.

KHÔNG dùng `threading.Timer` trong cùng process với bot: nếu process chính
treo (deadlock, C extension kẹt, event loop nghẽn), một timer thread trong
CHÍNH process đó cũng treo theo — không bao giờ kích hoạt được. Watchdog
phải là quan sát viên bên ngoài, độc lập hoàn toàn.

Hai cách triển khai:
  1. systemd `Type=notify` + `WatchdogSec=60` — để hệ điều hành làm việc
     này, không tự viết lại logic đã có sẵn trong systemd.
  2. Process riêng (module này) — dùng khi không có systemd (launchd trên
     macOS lúc dev, hoặc container không có systemd). Đọc mtime của
     `status.json`; bot phải tự cập nhật file này mỗi chu kỳ (main loop,
     Phase 10, chưa implement). Cũ hơn `timeout_sec` → coi là treo, gửi
     SIGTERM tới PID đọc từ `bot.pid`.

Cả hai file (`status.json`, `bot.pid`) sống trong `STATE_DIR`, cùng chỗ với
`trading_halted.lock`/`state_snapshot.json` (xem `ops/RUNBOOK.md`) — không
hardcode `data/bot.pid` như draft đầu: watchdog và bot phải thống nhất một
thư mục trạng thái duy nhất, đọc từ cùng biến môi trường `STATE_DIR` mà
`ops/entrypoint.sh` đã dùng.

TODO: khi `monitoring/logger.py::get_logger()` được implement (còn
NotImplementedError), chuyển log ở đây sang dùng nó thay vì
`logging.basicConfig` tự cấu hình — để watchdog ghi vào cùng rotating file
JSON với các thành phần khác thay vì log riêng một kiểu.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _log_event(event: str, **fields: object) -> None:
    """JSON có cấu trúc trên một dòng — CLAUDE.md cấm print() trong code
    production. Tạm dùng logging.Logger trực tiếp (xem TODO ở module
    docstring) thay vì monitoring.logger.get_logger() vì hàm đó còn stub."""
    logger.warning(json.dumps({"event": event, **fields}, default=str))


def _kill_bot(pid_file: Path) -> None:
    """Đọc PID rồi gửi SIGTERM. Bắt cả hai lỗi có thể xảy ra khi bot đã
    chết hoặc đang khởi động lại giữa lúc đọc file — không để watchdog tự
    sập vì đúng cái nó đang cố xử lý (process không còn tồn tại)."""
    try:
        pid = int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError) as exc:
        _log_event("watchdog_kill_skipped", reason=f"pid_file không đọc được: {exc}")
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _log_event("watchdog_kill_skipped", pid=pid, reason="process đã không còn tồn tại")
        return

    _log_event("watchdog_killed_bot", pid=pid)


def watchdog_process(status_file: Path, pid_file: Path, timeout_sec: int = 90, poll_sec: int = 30) -> None:
    """Chạy như process riêng, vòng lặp vô hạn — không tự thoát, để
    supervisor (launchd/systemd/docker restart policy) là nơi duy nhất
    quyết định khi nào watchdog dừng.

    Sau một lần SIGTERM, KHÔNG break: nếu supervisor khởi động lại bot với
    PID mới, `status_file` sẽ được cập nhật lại và watchdog tự động thôi
    kích hoạt ở vòng lặp kế tiếp — đúng hành vi giám sát liên tục, không
    phải hành động một lần.
    """
    while True:
        time.sleep(poll_sec)
        if not status_file.exists():
            continue

        age_sec = time.time() - status_file.stat().st_mtime
        if age_sec > timeout_sec:
            _log_event("watchdog_stale_status", age_sec=round(age_sec, 1), timeout_sec=timeout_sec)
            _kill_bot(pid_file)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    state_dir = Path(os.environ.get("STATE_DIR", "state"))
    status_path = state_dir / "status.json"
    pid_path = state_dir / "bot.pid"
    timeout = int(os.environ.get("WATCHDOG_TIMEOUT_SEC", "90"))
    poll = int(os.environ.get("WATCHDOG_POLL_SEC", "30"))

    if poll >= timeout:
        # Nếu poll >= timeout, có thể bỏ lỡ cả một chu kỳ trước khi phát
        # hiện — timeout phải luôn có biên so với tần suất kiểm tra.
        sys.exit(f"WATCHDOG_POLL_SEC ({poll}) phải nhỏ hơn WATCHDOG_TIMEOUT_SEC ({timeout})")

    _log_event("watchdog_started", status_file=str(status_path), pid_file=str(pid_path), timeout_sec=timeout)
    watchdog_process(status_path, pid_path, timeout_sec=timeout, poll_sec=poll)
