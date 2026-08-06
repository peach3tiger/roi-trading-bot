#!/usr/bin/env bash
# ops/entrypoint.sh — chạy TRƯỚC lệnh chính mỗi lần container khởi động:
# chặn forward/logger.py, kiểm tra trading_halted.lock, đọc state_snapshot.json,
# chạy health check — rồi mới exec vào lệnh thật (mặc định `python main.py`).
#
# `exec "$@"` ở cuối (không phải chạy nền/wait) — thay thế hẳn tiến trình
# shell bằng tiến trình Python, để SIGTERM/SIGINT từ `docker stop` tới
# thẳng Python thay vì bị shell (PID 1) nuốt mất. Không có exec ở đây,
# "Tắt (SIGINT/SIGTERM)" ở docs/Brain-Crypto-Bybit.md §Phase 7 (ghi
# state_snapshot.json, không đóng vị thế) không có cơ hội chạy.

set -euo pipefail

STATE_DIR="${STATE_DIR:-/app/state}"
LOG_DIR="${LOG_DIR:-/app/logs}"
HALT_LOCK_PATH="${STATE_DIR}/trading_halted.lock"
STATE_SNAPSHOT_PATH="${STATE_DIR}/state_snapshot.json"

log() {
    printf '%s [entrypoint] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

# ----------------------------------------------------------------------
# 0) QUAN TRỌNG — chặn cứng nếu lệnh được truyền vào nhỡ gọi forward.logger.
# Không phải chỉ tài liệu suông: kiểm tra thật trên "$@" trước khi làm bất
# cứ điều gì khác. Xem đầu ops/Dockerfile và ops/docker-compose.yml.
# ----------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        *forward.logger*|*forward/logger.py*)
            log "TỪ CHỐI: lệnh chứa '${arg}' — container này KHÔNG được chạy"
            log "forward/logger.py. Forward test chạy qua launchd trên máy host"
            log "với cấu hình đã đóng băng (forward/README.md, docs/DECISIONS.md"
            log "'Forward test — tiền đăng ký'). Sửa lại lệnh, không phải file này."
            exit 1
            ;;
    esac
done

# ----------------------------------------------------------------------
# 1) trading_halted.lock — nếu tồn tại, KHÔNG được im lặng khởi động lại.
# Đúng tinh thần §5.2 (Brain-Crypto-Bybit.md): "phải xoá thủ công mới chạy
# lại". Restart container (kể cả restart: unless-stopped) không được phép
# tự ý coi là "đã xử lý xong".
# ----------------------------------------------------------------------
mkdir -p "$STATE_DIR" "$LOG_DIR"

if [ -f "$HALT_LOCK_PATH" ]; then
    log "DỪNG: ${HALT_LOCK_PATH} tồn tại — circuit breaker đã dừng giao dịch trước đó."
    log "Nội dung file:"
    cat "$HALT_LOCK_PATH" >&2 || true
    log "Xem ops/RUNBOOK.md, mục 'Circuit breaker kích hoạt' TRƯỚC khi xoá file này."
    exit 1
fi

# ----------------------------------------------------------------------
# 2) state_snapshot.json — không bắt buộc (lần đầu chạy sẽ chưa có), chỉ
# log để biết bot có đang hồi phục từ phiên trước hay khởi động sạch.
# ----------------------------------------------------------------------
if [ -f "$STATE_SNAPSHOT_PATH" ]; then
    log "Tìm thấy ${STATE_SNAPSHOT_PATH} — sẽ hồi phục trạng thái phiên trước."
else
    log "Không có ${STATE_SNAPSHOT_PATH} — khởi động sạch (lần đầu, hoặc đã xoá state)."
fi

# ----------------------------------------------------------------------
# 3) Health check — fail loud trước khi vào lệnh chính, không để main.py tự
# khám phá config sai/model thiếu/đĩa đầy giữa chừng vòng lặp sống.
# ----------------------------------------------------------------------
log "Chạy health check..."
if ! python ops/health_check.py; then
    log "Health check FAIL — xem output ở trên, dừng lại, không exec lệnh chính."
    exit 1
fi
log "Health check OK."

log "exec: $*"
exec "$@"
