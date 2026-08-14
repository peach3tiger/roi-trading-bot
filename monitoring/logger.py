"""monitoring.logger — log JSON có cấu trúc, file xoay vòng.

Không bao giờ dùng print() trong code production (xem CLAUDE.md phong
cách code). main.log, trades.log, alerts.log, regime.log — mỗi file 10MB,
giữ 30 ngày.

**"10MB, giữ 30 ngày" là hai trục xoay vòng khác nhau** (dung lượng vs.
lịch), và `RotatingFileHandler` chuẩn của thư viện chỉ xoay theo MỘT trục
(dung lượng). `_BACKUP_COUNT = 30` là proxy — "giữ tối đa 30 file đã xoay,
mỗi file tới 10MB" — KHÔNG phải "giữ đúng 30 ngày lịch". Ở tần suất ghi
của bot này (tối đa vài dòng mỗi bar 1D + mỗi lần alert), 30 file 10MB
chắc chắn phủ quá 30 ngày thật; ghi chú lại để không ai đọc nhầm đây là
`TimedRotatingFileHandler`.

Mỗi lời gọi `get_logger(name, log_dir)` với CÙNG (name, log_dir) trả về
CÙNG một `logging.Logger` đã cấu hình — idempotent có chủ đích, tránh bug
kinh điển "gọi getLogger + addHandler bên trong vòng lặp" làm handler cộng
dồn và mỗi dòng log bị ghi lặp N lần.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Xoay theo dung lượng — xem docstring module.
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 30

# Tập thuộc tính CHUẨN của logging.LogRecord — dùng để lọc ra đúng những gì
# caller truyền qua `extra=` khi ghép vào payload JSON. Dựng ĐỘNG từ một
# LogRecord mẫu thay vì liệt kê tay: bền hơn qua các bản Python khác nhau
# (vd. "taskName" chỉ có từ 3.12) mà không cần tự đoán danh sách.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord("_probe", logging.INFO, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}

# Cache logger đã dựng, khoá theo (name, log_dir) — nền tảng cho tính chất
# idempotent mô tả ở docstring module.
_loggers: dict[tuple[str, str], logging.Logger] = {}


class _JsonFormatter(logging.Formatter):
    """Mỗi dòng log là MỘT object JSON hợp lệ (JSONL) — không phải
    "timestamp + message dạng JSON" như quy ước tạm của
    `monitoring/watchdog.py::_log_event` (module đó có TODO tự ghi rõ sẽ
    chuyển sang dùng `get_logger()` một khi hàm này hết là stub)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def get_logger(name: str, log_dir: str) -> logging.Logger:
    """Trả về logger JSON structured, rotating file handler 10MB / giữ 30 ngày.

    Ghi vào `{log_dir}/{name}.log` (vd. name="main" -> "logs/main.log",
    khớp bốn tên file trong spec: main/trades/alerts/regime). `propagate =
    False` — không đẩy record lên root logger, tránh việc `main.py` gọi
    `logging.basicConfig()` (console handler ở root) làm mỗi dòng log JSON
    bị in trùng ra console dưới dạng thô.
    """
    key = (name, str(log_dir))
    cached = _loggers.get(key)
    if cached is not None:
        return cached

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # `logging.Logger(...)` dựng TRỰC TIẾP, KHÔNG qua `logging.getLogger()`
    # — `getLogger()` tra cứu/tạo trong registry TOÀN CỤC theo TÊN, không
    # theo `(name, log_dir)`. Hai lần gọi `get_logger("main", dirA)` rồi
    # `get_logger("main", dirB)` (hai thư mục log KHÁC nhau, cùng `name`)
    # qua `getLogger()` sẽ trả về CÙNG MỘT đối tượng logger toàn cục và
    # cộng dồn handler — đúng bug idempotent này cố tránh, chỉ trồi ra khi
    # `log_dir` khác nhau (registry không phân biệt được). Dựng trực tiếp
    # loại bỏ hẳn khả năng va chạm registry: mỗi khoá `(name, log_dir)`
    # trong `_loggers` sở hữu một `logging.Logger` độc lập hoàn toàn.
    logger = logging.Logger(f"monitoring.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.handlers.RotatingFileHandler(
        log_path / f"{name}.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)

    _loggers[key] = logger
    return logger


def _to_jsonable(value: Any) -> Any:
    """`positions` thường là `dict[str, broker.base.Position]` (frozen
    dataclass, chứa `Decimal`) — không tự JSON-hoá được. Đệ quy qua dict và
    dataclass; `Decimal`/`Enum` còn lại được `json.dumps(..., default=str)`
    xử lý ở `get_logger()`'s formatter lẫn ở đây."""
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    return value


def log_state(
    logger: logging.Logger,
    regime: str,
    probability: float,
    equity: Decimal,
    positions: dict,
    daily_pnl: Decimal,
    cumulative_fees_paid: Decimal,
    *,
    hmm_allocation: Decimal | None = None,
    trend_gate_cap: Decimal | None = None,
    risk_manager_cap: Decimal | None = None,
    drawdown_pct: Decimal | None = None,
) -> None:
    """Mỗi entry gồm timestamp UTC, regime, probability, equity, positions,
    daily_pnl, cumulative_fees_paid.

    BỐN trường sau là tuỳ chọn (Phase 12b §C.2, thêm 2026-08-14), mặc định
    `None` để 23 test đã có trước đó không phải đổi: ba trần allocation +
    drawdown. `monitoring/daily_digest.py` cần chúng để trả lời "tầng nào
    giới hạn allocation bao nhiêu bar trong ngày" — không có chúng thì mục
    đó của §C.2 luôn rỗng. Ghi `None` khi caller không truyền, KHÔNG bỏ
    khoá: một khoá vắng mặt và một giá trị null cần phản ứng khác nhau khi
    đọc lại log cũ.

    `Decimal` truyền vào luôn được ghi dưới dạng `str()` (không phải
    `float()`) — giữ đúng biểu diễn thập phân chính xác trong log, khớp
    CLAUDE.md bất biến #3 dù đây không phải đường thực thi (log là nơi
    audit lại quyết định thực thi, lệch số ở đây gây hiểu sai khi điều
    tra sự cố).
    """
    logger.info(
        "regime_state",
        extra={
            "event": "regime_state",
            "regime": regime,
            "probability": probability,
            "equity": str(equity),
            "positions": _to_jsonable(positions),
            "daily_pnl": str(daily_pnl),
            "cumulative_fees_paid": str(cumulative_fees_paid),
            "hmm_allocation": None if hmm_allocation is None else str(hmm_allocation),
            "trend_gate_cap": None if trend_gate_cap is None else str(trend_gate_cap),
            "risk_manager_cap": None if risk_manager_cap is None else str(risk_manager_cap),
            "drawdown_pct": None if drawdown_pct is None else str(drawdown_pct),
        },
    )
