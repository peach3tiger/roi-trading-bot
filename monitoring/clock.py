"""monitoring.clock — đo lệch đồng hồ cục bộ so với sàn, có hiệu chỉnh
round-trip.

**Công thức ngây thơ `drift = server_time - local_now()` SAI.** Giữa lúc
gửi request và lúc nhận phản hồi trôi qua một khoảng round-trip THẬT
(network latency), và `server_time` được sàn đọc tại MỘT ĐIỂM ở giữa
khoảng đó — so nó với `local_now()` đo SAU KHI round-trip đã xong sẽ cộng
gộp gần như toàn bộ độ trễ một chiều vào kết quả, báo lệch giả. Ví dụ:
round-trip 400ms, đồng hồ thật ra khớp hoàn hảo — công thức ngây thơ vẫn
báo lệch ~200-400ms tuỳ lúc server đọc giờ nằm ở đâu trong khoảng đó.

**Công thức đúng** — kiểu NTP, giả định độ trễ đi/về đối xứng — chọn ĐIỂM
GIỮA của round-trip làm mốc so sánh:

    t0 = local_ms()              # trước khi gửi request
    server = get_server_time()   # sàn đọc đồng hồ tại một điểm nào đó GIỮA t0 và t1
    t1 = local_ms()               # sau khi nhận phản hồi
    round_trip = t1 - t0
    local_at_server_response = t0 + round_trip / 2   # ước lượng "lúc sàn đọc giờ" theo đồng hồ cục bộ
    drift_ms = server - local_at_server_response

Lấy trung vị (median) của nhiều lần đo liên tiếp (mặc định 3, xem
`measure_clock_drift()`) để loại nhiễu mạng — một lần đo đơn lẻ có thể
trúng đúng lúc round-trip bất thường (nghẽn mạng tạm thời, GC pause phía
sàn) và cho kết quả sai lệch dù công thức đúng.

**TUYỆT ĐỐI KHÔNG bật ccxt `options={'adjustForTimeDifference': True}` ở
bất cứ đâu dùng module này** (hay bất cứ đâu trong `broker/ccxt_client.py`).
Cờ đó tự động cộng bù offset đo được vào timestamp của MỌI request ký sau
đó — request đi lọt, nhưng đó là CHE TRIỆU CHỨNG, không sửa nguyên nhân.
Đồng hồ máy sai (NTP tắt, máy vừa ngủ dậy, đồng hồ CMOS trôi) là một VẤN ĐỀ
HỆ THỐNG cần được người vận hành SỬA Ở ĐÓ (xem `ops/RUNBOOK.md` mục
CLOCK_DRIFT), không phải giấu đi ở tầng client:
  - Giấu offset khiến operator không bao giờ biết đồng hồ vẫn đang sai,
    cho tới khi nó trôi đủ xa mà offset tự động cũng không cứu nổi nữa
    (Binance `recvWindow` mặc định 5000ms — quá nửa cửa sổ đó, request ký
    bắt đầu bị từ chối với `-1021`, và lỗi đó TRÔNG HỆT như key sai).
  - Một hệ thống KHÁC đọc cùng chiếc máy đó (log timestamp, cron job khác,
    một tiến trình giám sát) sẽ tin vào giờ sai của nó — `adjustForTimeDifference`
    chỉ sửa cho ccxt, không sửa đồng hồ hệ điều hành.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

_DEFAULT_SAMPLES = 3


@dataclass(frozen=True)
class ClockCheck:
    drift_ms: float
    round_trip_ms: float
    measured_at: datetime


def _local_ms() -> int:
    return int(time.time() * 1000)


def measure_clock_drift(
    get_server_time: Callable[[], int],
    *,
    samples: int = _DEFAULT_SAMPLES,
    local_ms_fn: Callable[[], int] = _local_ms,
) -> ClockCheck:
    """`samples` lần đo LIÊN TIẾP (mặc định 3) — kết quả cuối là lần đo có
    `drift_ms` Ở GIỮA (median theo drift), giữ nguyên CẶP `(drift_ms,
    round_trip_ms)` từ ĐÚNG lần đo đó.

    KHÔNG lấy trung vị độc lập từng trường (median riêng của tất cả
    `drift_ms`, median riêng của tất cả `round_trip_ms`): trộn `drift` của
    lần đo này với `round_trip` của lần đo khác tạo ra một cặp số không
    tương ứng với bất kỳ lần đo thật nào — vô nghĩa để audit lại sau này
    ("lần đo nào cho ra con số bất thường?").

    `samples` chẵn: lấy phần tử ở chỉ số `samples // 2` sau khi sắp theo
    `drift_ms` (phần tử "giữa-trên"), KHÔNG trung bình cộng hai phần tử
    giữa — cùng lý do ở trên, tránh tạo ra một cặp số không phải từ một
    lần đo thật.

    `get_server_time` nhận thẳng `ExchangeClient.get_server_time` (đã bind
    method) — không phải instance, để test truyền được một callable giả
    tuỳ ý (kể cả stateful, trả offset khác nhau mỗi lần gọi) mà không cần
    dựng một `ExchangeClient` giả đầy đủ.
    """
    if samples < 1:
        raise ValueError(f"samples phải >= 1, nhận {samples}")

    raw: list[tuple[float, float]] = []
    for _ in range(samples):
        t0 = local_ms_fn()
        server = get_server_time()
        t1 = local_ms_fn()
        round_trip = t1 - t0
        local_at_server_response = t0 + round_trip / 2
        drift = server - local_at_server_response
        raw.append((float(drift), float(round_trip)))

    raw.sort(key=lambda pair: pair[0])
    median_drift, median_round_trip = raw[len(raw) // 2]

    return ClockCheck(
        drift_ms=median_drift,
        round_trip_ms=median_round_trip,
        measured_at=datetime.now(timezone.utc),
    )
