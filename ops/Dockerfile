# ops/Dockerfile — image cho live loop (Phase 10 / phase-10-main-loop.md,
# CHƯA implement — xem ops/RUNBOOK.md mục "Trạng thái hiện tại"). Build từ
# gốc repo: `docker build -f ops/Dockerfile -t regime-trader-crypto .`
# (docker-compose.yml đã set context đúng, dùng `docker compose` là đủ).
#
# QUAN TRỌNG — KHÔNG chạy forward/logger.py trong container này. Forward
# test chạy qua launchd trên máy HOST với cấu hình đã đóng băng
# (forward/config_frozen.yaml, xem forward/README.md và
# docs/DECISIONS.md "Forward test — tiền đăng ký"). Đưa nó vào container sẽ
# đổi môi trường thực thi (Python version, hệ điều hành, filesystem, cách
# lên lịch) giữa chừng một thí nghiệm 12 tháng đã tiền đăng ký — phá vỡ
# đúng giả định "cùng một hệ thống xuyên suốt" mà thí nghiệm đó cần.
# ops/entrypoint.sh chặn tường minh nếu lệnh chạy nhỡ gọi tới forward.logger.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# build-essential: một số wheel (hmmlearn) không có sẵn cho mọi kiến trúc/
# phiên bản Python — cài để build không tuỳ vào việc PyPI có sẵn wheel hay
# không. Đánh đổi: image nặng hơn vài trăm MB. Có thể chuyển sang multi-stage
# build (builder stage cài build-essential, copy site-packages sang stage
# runtime sạch) nếu cỡ image thành vấn đề — chưa làm ở đây, ưu tiên build
# tin cậy trước, tối ưu sau (chưa có Docker để build-test trong môi trường
# viết file này — kiểm tra `docker build` thật trước khi tin tưởng).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# non-root — không có lý do nào ở đây cần root (không bind port <1024,
# không mount device, không cần setuid).
RUN groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid botuser --shell /bin/bash --create-home botuser

COPY --chown=botuser:botuser . .

# chmod SAU khi copy — không phụ thuộc bit thực thi từ máy host (checkout
# trên Windows, hoặc git đôi khi làm mất +x).
RUN chmod +x ops/entrypoint.sh

USER botuser

# ops/health_check.py tự thoát mã khác 0 nếu bất kỳ kiểm tra nào fail —
# xem file đó để biết danh sách đầy đủ (config, kết nối sàn, model HMM,
# dung lượng đĩa, thư mục log ghi được).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python ops/health_check.py || exit 1

ENTRYPOINT ["ops/entrypoint.sh"]
CMD ["python", "main.py"]
