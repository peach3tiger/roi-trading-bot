"""Trace context — truy được toàn bộ chuỗi quyết định của MỘT bar. §C.

## Vì sao `contextvars`, không truyền `trace_id` qua chữ ký hàm

Nếu phải truyền tay qua mọi hàm thì sớm muộn một nhánh bị quên, và chuỗi
**đứt im lặng** — chỉ phát hiện đúng lúc đang cần truy vết, tức là đúng
lúc không sửa được nữa. `contextvars` chạy xuyên cả call stack đồng bộ lẫn
async mà không chạm một chữ ký nào.

## Vì sao `trace_id` TẤT ĐỊNH, không phải UUID

Cùng lý do với `orderLinkId` (CLAUDE.md #8): chạy lại cùng một bar phải
cho cùng một id, để log backtest / shadow / forward / live so sánh TRỰC
TIẾP được mà không cần parser riêng cho từng nguồn. Một UUID ngẫu nhiên
làm mọi phép so chéo trở thành bài toán khớp mờ.

## Phạm vi là CHU KỲ BAR, không phải lệnh

`trace_id` sinh ở đầu chu kỳ bar, TRƯỚC cả khi tính feature. Khác
`trade_id` (Phase 9) vốn chỉ tồn tại khi có lệnh — mà thứ khó truy nhất
lại là những gì **không** thành lệnh: signal bị risk manager từ chối, bar
bị trend gate chặn, bar không làm gì.

Quan hệ: một bar → một `trace_id` → N signal → N `trade_id`.

## `capped_by` — trường có giá trị nhất ở đây

Kiến trúc là `min()` của ba tầng (CLAUDE.md #2). Khi có gì bất thường,
câu hỏi ĐẦU TIÊN luôn là *tầng nào đang giới hạn*. Không có trường này thì
phải đọc bốn file log rồi tự suy ra. Tính ở tầng `compose`, đừng bắt người
đọc log tự suy.

## KHÔNG đụng `forward/logger.py`

§C.4 muốn ba nguồn (`forward/logger.py`, `ops/shadow_runner.py`,
`main.py`) phát cùng định dạng trace. Hai nguồn sau làm được; nguồn đầu
thì KHÔNG — `forward/logger.py` ĐÓNG BĂNG, ghim SHA256
(`tests/golden/frozen_hashes.json`), và sửa nó là **kết thúc thí nghiệm
forward 12 tháng** (CLAUDE.md #15). Chính prompt §C.4 đã viết: "thí nghiệm
quan trọng hơn tiện lợi khi debug".

Hệ quả cụ thể: `ops/shadow_diff.py` không được đòi `trace` ở log forward.
Nó so theo `bar_date`, và `trace_id` tất định nghĩa là hai bên vẫn khớp
được — đó chính là thứ tính tất định mua cho ta.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

# "-" chứ không phải "" hay None: một dòng log ngoài phạm vi bar vẫn phải
# có trường `trace`, để `grep trace=` không bao giờ bỏ sót dòng nào vì
# thiếu khoá.
NO_TRACE = "-"

trace_id: ContextVar[str] = ContextVar("trace_id", default=NO_TRACE)

# Tên tầng — hằng số chứ không phải chuỗi rời rạc. `ops/shadow_diff.py` và
# mọi phép grep về sau đối chiếu theo chúng; hai chỗ gõ tay cùng một chuỗi
# sẽ lệch nhau đúng lúc cần khớp.
LAYER_FEATURES = "features"
LAYER_HMM = "hmm"
LAYER_TREND_GATE = "trend_gate"
LAYER_RISK = "risk"
LAYER_COMPOSE = "compose"
LAYER_REBALANCE = "rebalance"

# Giá trị của `capped_by`.
CAPPED_BY_HMM = "hmm"
CAPPED_BY_TREND_GATE = "trend_gate"
CAPPED_BY_RISK = "risk"
CAPPED_BY_NONE = "none"


def new_bar_trace(bar_timestamp: datetime, symbol: str) -> str:
    """`f"{bar_timestamp.isoformat()}:{symbol}"` — TẤT ĐỊNH.

    Không sinh ngẫu nhiên, không đọc đồng hồ. Chạy lại cùng bar cho cùng
    id; đó là điều kiện để so log giữa backtest/shadow/forward/live.
    """
    return f"{bar_timestamp.isoformat()}:{symbol}"


def set_bar_trace(bar_timestamp: datetime, symbol: str) -> str:
    """Sinh + đặt vào context. Trả về id để caller log/khẳng định.

    KHÔNG trả `Token` để reset: phạm vi là cả chu kỳ bar, và mỗi bar mới
    ghi đè bar cũ. Một API `reset()` chỉ mời gọi người ta lồng phạm vi —
    lúc đó "một bar một trace" không còn đúng.
    """
    tid = new_bar_trace(bar_timestamp, symbol)
    trace_id.set(tid)
    return tid


def current_trace() -> str:
    return trace_id.get()


class TraceFilter(logging.Filter):
    """Chèn `trace` vào MỌI bản ghi. Không module nào phải tự nhớ ghi nó.

    Là `Filter` chứ không phải `Formatter`: filter chạy trước, nên trường
    `trace` có mặt trong `record.__dict__` và được
    `monitoring/logger.py::_JsonFormatter` gói vào JSON như mọi trường
    extra khác — không phải sửa formatter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace"):
            record.trace = trace_id.get()
        return True


def install(logger: logging.Logger) -> logging.Logger:
    """Gắn `TraceFilter` một lần. Idempotent — gọi hai lần không nhân đôi
    filter (cùng lý do `get_logger()` idempotent với handler)."""
    if not any(isinstance(f, TraceFilter) for f in logger.filters):
        logger.addFilter(TraceFilter())
    return logger


def capped_by(
    hmm: Optional[Decimal], trend_gate: Optional[Decimal], risk: Optional[Decimal]
) -> str:
    """Tầng NÀO đang giới hạn allocation.

    KHÔNG dùng `min()` rồi xem trần nào bằng nó. Trong đường dây hiện tại
    `risk_manager_cap == final_allocation == min(ba trần)` (xem
    `core/signal_generator.py`), nên risk LUÔN bằng giá trị nhỏ nhất và
    cách ngây thơ sẽ báo "risk giới hạn" ở 100% số bar — đúng số học, vô
    dụng vận hành.

    Cùng định nghĩa với `monitoring/daily_digest.py::limiting_layer()`;
    hai bản sẽ trôi lệch, nên bản này ỦY QUYỀN cho bản đó.
    """
    from monitoring.daily_digest import LAYER_HMM as _DIGEST_HMM
    from monitoring.daily_digest import LAYER_RISK as _DIGEST_RISK
    from monitoring.daily_digest import LAYER_TIE as _DIGEST_TIE
    from monitoring.daily_digest import LAYER_TREND as _DIGEST_TREND
    from monitoring.daily_digest import limiting_layer

    tang = limiting_layer(hmm, trend_gate, risk)
    return {
        _DIGEST_HMM: CAPPED_BY_HMM,
        _DIGEST_TREND: CAPPED_BY_TREND_GATE,
        _DIGEST_RISK: CAPPED_BY_RISK,
        # "đồng hạng" = HMM và trend gate ràng buộc BẰNG NHAU. Ở tầng log
        # một dòng thì `none` sai (có ràng buộc thật) và chọn bừa một bên
        # cũng sai — nên giữ nhãn riêng.
        _DIGEST_TIE: "tie",
        None: CAPPED_BY_NONE,
    }[tang]


def log_layer(logger: logging.Logger, layer: str, **fields: Any) -> None:
    """Một dòng cho một tầng. `trace` do `TraceFilter` chèn.

    Định dạng CHUNG cho `main.py` và `ops/shadow_runner.py` — đó là điều
    kiện để `ops/shadow_diff.py` không cần parser riêng cho từng nguồn.
    """
    logger.info(layer, extra={"event": "trace_layer", "layer": layer, **fields})
