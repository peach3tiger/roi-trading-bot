"""Ảnh chụp trạng thái LÚC CHẠY, ghi ra JSON. Phase 12b §B.1/§B.3.

## Khác `ops/health_check.py` như thế nào — đừng gộp hai file

| | `ops/health_check.py` | `monitoring/health.py` (file này) |
|---|---|---|
| Câu hỏi trả lời | "khởi động được không?" | "đang chạy có ổn không?" |
| Đầu ra | exit code 0/1 | file JSON |
| Ai đọc | Docker `HEALTHCHECK`, `ops/entrypoint.sh` | người vận hành, dashboard, digest |
| Khi chạy | trước khi vào vòng lặp | mỗi chu kỳ vòng lặp |

Một probe liveness trả 0/1 không nói được **tầng nào** đang giới hạn
allocation hay model HMM đã cũ bao nhiêu ngày. Một file JSON không dùng
được làm `HEALTHCHECK` của Docker. Hai vai trò khác nhau, hai file.

**KHÔNG mở port.** Trên máy cá nhân, một cổng HTTP để xem trạng thái là
thêm bề mặt tấn công cho một thứ mà `cat` đọc được.

## Vì sao `evaluate()` là hàm THUẦN

`evaluate(HealthInputs) -> HealthReport` không đọc file, không gọi mạng,
không đọc đồng hồ. Mọi thứ nó cần đi vào qua `HealthInputs`. Đó là điều
làm cho quy tắc `ok`/`degraded`/`down` kiểm được bằng test thường thay vì
phải dựng một sàn giả và chờ thời gian trôi.

Chế độ hỏng chủ đạo của dự án này là **lỗi xác minh** (CLAUDE.md #16) —
ba lần đã xảy ra. Một hàm sức khoẻ mà bản thân nó không test được sẽ là
lần thứ tư: nó báo "ok" và không ai biết nó có bao giờ báo khác không.

## Vì sao mặc định ghi `${STATE_DIR}/health.json`, không phải `monitoring/state/`

Lệch khỏi đường dẫn trong prompt §B.1 một cách CÓ CHỦ Ý. `monitoring/state/`
nằm trong cây MÃ NGUỒN. `status.json` từng nằm đúng chỗ đó và đã phải
chuyển đi ngày 2026-08-08 (xem `monitoring/alerts.py::_default_status_path`)
— state runtime cần ở cùng một thư mục, cùng volume Docker, cùng đường sao
lưu, và nằm ngoài `git status`. Đặt file mới vào đúng chỗ vừa dọn xong là
tự tạo lại vấn đề đã sửa.

Caller vẫn truyền được `path` tuỳ ý; chỉ MẶC ĐỊNH là khác prompt.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from core.risk_manager import _SIZE_MULTIPLIER, BreakerLevel

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"

# Khớp `monitoring/alerts.py::_default_status_path` và `main.py::run_live_loop`.
_DEFAULT_STATE_DIR = "state"

# "Circuit breaker đang halt" = level nào ép size về 0. Suy ra TỪ
# `_SIZE_MULTIPLIER` chứ không chép tay danh sách tên: thêm một level HALT
# mới ở `core/risk_manager.py` mà quên cập nhật ở đây sẽ làm health.json
# báo "ok" trong lúc bot đã đóng hết vị thế — đúng loại lệch âm thầm mà
# một danh sách chép tay sinh ra.
_HALT_LEVELS: frozenset[str] = frozenset(
    level.value for level, mult in _SIZE_MULTIPLIER.items() if mult == 0
)

# Nhãn hiển thị cho `BreakerLevel.NONE` — prompt §B.1 dùng "normal" trong
# schema mẫu, enum dùng "NONE". Đổi ở ĐÂY (tầng trình bày) chứ không đổi
# enum: `BreakerLevel` là hợp đồng của `core/risk_manager.py` với phần còn
# lại của hệ thống, không phải của file JSON này.
_BREAKER_NORMAL_LABEL = "normal"

# §B.3 — 60 giây sau khởi động. Không nằm trong settings.yaml vì đây không
# phải tham số vận hành cần chỉnh: nó là "đủ lâu để vòng lặp chạy xong bar
# đầu tiên, đủ ngắn để người vận hành còn đang nhìn màn hình".
_STARTUP_CHECK_DELAY_S = 60.0


def default_health_path() -> Path:
    """`${STATE_DIR}/health.json`, đọc env ở THỜI ĐIỂM GỌI — cùng lý do
    với `alerts.py::_default_status_path`: module này có thể được import
    trước khi `STATE_DIR` tồn tại, nên một hằng số mức module sẽ đóng băng
    giá trị sai và ghi ra ngoài volume đã mount."""
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR)) / "health.json"


@dataclass(frozen=True)
class HealthThresholds:
    """Ngưỡng quyết định `ok`/`degraded`/`down`.

    Tách khỏi `HealthInputs` vì chúng thay đổi theo CẤU HÌNH, không theo
    từng bar — và vì test cần đổi ngưỡng mà không phải dựng lại toàn bộ
    đầu vào.
    """

    # "Mất data feed > 2 chu kỳ bar" (§B.1). Với bar 1D thì 2 chu kỳ = 2
    # ngày; `bars_behind` đã đếm bằng đơn vị bar nên so trực tiếp được.
    bars_behind_down: int = 2
    clock_skew_degraded_ms: float = 1000.0
    unfilled_order_degraded_seconds: float = 300.0
    # "Model HMM cũ hơn 2× chu kỳ retrain" (§B.1).
    hmm_model_age_multiplier: float = 2.0
    retrain_interval_days: int = 7

    @property
    def hmm_model_age_degraded_days(self) -> float:
        return self.hmm_model_age_multiplier * self.retrain_interval_days

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "HealthThresholds":
        """Đọc từ `config/settings.yaml` (CLAUDE.md bất biến #14 — không
        magic number trong code).

        `clock_skew_degraded_ms` dùng lại `monitoring.clock_drift_alert_ms`
        thay vì thêm một key riêng: hai con số này mô tả CÙNG một ngưỡng
        ("lệch đồng hồ tới mức đáng lo"), và hai key trùng nghĩa sẽ lệch
        nhau đúng lúc cần chúng khớp nhau.
        """
        monitoring = settings.get("monitoring", {}) or {}
        hmm = settings.get("hmm", {}) or {}
        defaults = cls()
        return cls(
            bars_behind_down=defaults.bars_behind_down,
            clock_skew_degraded_ms=float(
                monitoring.get("clock_drift_alert_ms", defaults.clock_skew_degraded_ms)
            ),
            unfilled_order_degraded_seconds=float(
                monitoring.get(
                    "unfilled_order_degraded_seconds",
                    defaults.unfilled_order_degraded_seconds,
                )
            ),
            hmm_model_age_multiplier=defaults.hmm_model_age_multiplier,
            retrain_interval_days=int(
                hmm.get("retrain_interval_days", defaults.retrain_interval_days)
            ),
        )


@dataclass(frozen=True)
class HealthInputs:
    """Toàn bộ thứ `evaluate()` cần — giá trị thuần, không đối tượng sống.

    Cố tình KHÔNG nhận `signal_generator`/`exchange_client`/`hmm_engine`:
    nhận đối tượng sống nghĩa là test phải giả lập chúng, và một hàm chỉ
    test được qua mock thì phần lớn thứ được test là mock.
    """

    updated_at: datetime
    last_bar_time: Optional[datetime]
    bars_behind: int
    # `False` = lần gọi sàn gần nhất thất bại (§B.1 "API không phản hồi").
    api_ok: bool
    api_latency_ms: Optional[float]
    poll_latency_ms: Optional[float]
    clock_skew_ms: Optional[float]
    hmm_regime: Optional[str]
    hmm_confidence: Optional[float]
    hmm_model_age_days: Optional[float]
    trend_gate: Optional[str]
    # BỐN trường allocation, không phải một. Chỉ nhìn `final_allocation`
    # thì không biết TẦNG NÀO đang giới hạn — đó là thông tin chẩn đoán
    # quan trọng nhất khi có gì bất thường (prompt §B.1).
    # `Decimal` chứ không `float`: xem `_alloc_to_json` bên dưới.
    hmm_allocation: Optional[Decimal]
    trend_gate_cap: Optional[Decimal]
    risk_manager_cap: Optional[Decimal]
    final_allocation: Optional[Decimal]
    position_delta_pct: Optional[Decimal]
    unfilled_orders: int
    unfilled_value_usdt: Optional[Decimal]
    # Tuổi (giây) của lệnh chưa khớp LÂU NHẤT. `None` = không có lệnh nào
    # đang chờ.
    oldest_unfilled_age_seconds: Optional[float]
    circuit_breaker: str
    cumulative_fees_usdt: Optional[Decimal]
    fees_pct_of_gross: Optional[float]
    last_alert_minutes_ago: Optional[float]
    uptime_seconds: float
    testnet: bool


@dataclass(frozen=True)
class HealthReport:
    status: str
    # Vì sao KHÔNG phải `ok` — rỗng khi `ok`. Một trạng thái "degraded"
    # không nói được lý do thì người vận hành phải đi dò lại chính những
    # con số mà file này vừa đọc xong.
    reasons: tuple[str, ...]
    # Vi phạm BẤT BIẾN, tách riêng khỏi `reasons`. Xem `_check_invariants`.
    invariant_violations: tuple[str, ...]
    payload: dict[str, Any]


def _alloc_to_json(value: Optional[Decimal]) -> Optional[str]:
    """Allocation ghi ra JSON dưới dạng CHUỖI, không phải số.

    Prompt §B.1 vẽ schema mẫu bằng số (`"hmm_allocation": 0.95`). Lệch có
    chủ ý, hai lý do:

    1. Cùng quy ước với `state_snapshot.json` (`current_allocation_pct` là
       `str(Decimal)`) — hai file mô tả cùng đại lượng, đọc chéo nhau được.
    2. Điều đáng kiểm nhất ở bốn trường này là `final == min(ba trường
       kia)` (CLAUDE.md bất biến #2). Ghi bằng `float` thì chính file bằng
       chứng mất khả năng chứng minh điều đó — `0.95` in ra từ một `float`
       không phân biệt được với `0.9500000000000001`.

    Độ trễ / lệch đồng hồ / confidence vẫn là `float`: chúng là PHÉP ĐO,
    không phải tiền hay tỷ trọng (CLAUDE.md bất biến #3 cho phép).
    """
    return None if value is None else str(value)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_invariants(inputs: HealthInputs) -> tuple[str, ...]:
    """Bất biến #2: `final_allocation == min(hmm, trend_gate, risk)`.

    CỐ TÌNH KHÔNG đưa vào `status`. Prompt §B.1 liệt kê quy tắc `status`
    một cách vét cạn ("ok — còn lại"), và quan trọng hơn: một vi phạm ở
    đây KHÔNG phải trạng thái vận hành. `degraded` nghĩa là "hạ tầng đang
    có vấn đề, chờ hoặc thử lại"; vi phạm bất biến nghĩa là "code sai,
    phải sửa". Trộn hai thứ lại thì bug được xử lý bằng cách chờ — tức là
    không bao giờ được xử lý. Cùng lý do `AlertType.INTERNAL_ERROR` tồn
    tại tách khỏi `DATA_FEED_LOST` (xem `monitoring/alerts.py`).

    `assert_healthy_or_alert()` phát INTERNAL_ERROR cho những gì hàm này
    trả về.
    """
    caps = (inputs.hmm_allocation, inputs.trend_gate_cap, inputs.risk_manager_cap)
    if inputs.final_allocation is None or any(c is None for c in caps):
        return ()

    expected = min(c for c in caps if c is not None)
    if inputs.final_allocation != expected:
        return (
            f"BẤT BIẾN #2 VI PHẠM: final_allocation={inputs.final_allocation} "
            f"!= min(hmm={inputs.hmm_allocation}, trend_gate={inputs.trend_gate_cap}, "
            f"risk={inputs.risk_manager_cap})={expected}",
        )
    return ()


def evaluate(
    inputs: HealthInputs, thresholds: Optional[HealthThresholds] = None
) -> HealthReport:
    """Quy tắc §B.1, theo đúng thứ tự ưu tiên: `down` thắng `degraded`.

    Thu thập TẤT CẢ lý do rồi mới quyết định status, không return sớm ở lý
    do đầu tiên: khi ba thứ cùng hỏng, người vận hành cần thấy cả ba —
    sửa một cái rồi phát hiện còn hai cái nữa là cách tốn thời gian nhất
    để xử lý một sự cố.
    """
    th = thresholds or HealthThresholds()

    down: list[str] = []
    degraded: list[str] = []

    if not inputs.api_ok:
        down.append("API không phản hồi ở lần gọi gần nhất")
    if inputs.bars_behind > th.bars_behind_down:
        down.append(
            f"Chậm {inputs.bars_behind} bar (> {th.bars_behind_down}) — coi như mất data feed"
        )
    if inputs.circuit_breaker in _HALT_LEVELS:
        down.append(f"Circuit breaker đang halt ({inputs.circuit_breaker})")

    if 0 < inputs.bars_behind <= th.bars_behind_down:
        degraded.append(f"Chậm {inputs.bars_behind} bar")
    if inputs.clock_skew_ms is not None and abs(inputs.clock_skew_ms) > th.clock_skew_degraded_ms:
        degraded.append(
            f"Lệch đồng hồ {inputs.clock_skew_ms:.0f}ms (> {th.clock_skew_degraded_ms:.0f}ms)"
        )
    if (
        inputs.oldest_unfilled_age_seconds is not None
        and inputs.oldest_unfilled_age_seconds > th.unfilled_order_degraded_seconds
    ):
        degraded.append(
            f"Có lệnh chưa khớp {inputs.oldest_unfilled_age_seconds:.0f}s "
            f"(> {th.unfilled_order_degraded_seconds:.0f}s)"
        )
    if (
        inputs.hmm_model_age_days is not None
        and inputs.hmm_model_age_days > th.hmm_model_age_degraded_days
    ):
        degraded.append(
            f"Model HMM cũ {inputs.hmm_model_age_days:.0f} ngày "
            f"(> {th.hmm_model_age_degraded_days:.0f} = {th.hmm_model_age_multiplier:g}× "
            f"chu kỳ retrain {th.retrain_interval_days}d)"
        )

    if down:
        status = STATUS_DOWN
    elif degraded:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK

    breaker_label = (
        _BREAKER_NORMAL_LABEL
        if inputs.circuit_breaker == BreakerLevel.NONE.value
        else inputs.circuit_breaker
    )

    payload: dict[str, Any] = {
        "status": status,
        "reasons": list(down + degraded),
        "updated_at": _iso(inputs.updated_at),
        "last_bar_time": _iso(inputs.last_bar_time),
        "bars_behind": inputs.bars_behind,
        "api_ok": inputs.api_ok,
        "api_latency_ms": inputs.api_latency_ms,
        "poll_latency_ms": inputs.poll_latency_ms,
        "clock_skew_ms": inputs.clock_skew_ms,
        "hmm_regime": inputs.hmm_regime,
        "hmm_confidence": inputs.hmm_confidence,
        "hmm_model_age_days": inputs.hmm_model_age_days,
        "trend_gate": inputs.trend_gate,
        "hmm_allocation": _alloc_to_json(inputs.hmm_allocation),
        "trend_gate_cap": _alloc_to_json(inputs.trend_gate_cap),
        "risk_manager_cap": _alloc_to_json(inputs.risk_manager_cap),
        "final_allocation": _alloc_to_json(inputs.final_allocation),
        "position_delta_pct": _alloc_to_json(inputs.position_delta_pct),
        "unfilled_orders": inputs.unfilled_orders,
        "unfilled_value_usdt": _alloc_to_json(inputs.unfilled_value_usdt),
        "oldest_unfilled_age_seconds": inputs.oldest_unfilled_age_seconds,
        "circuit_breaker": breaker_label,
        "cumulative_fees_usdt": _alloc_to_json(inputs.cumulative_fees_usdt),
        "fees_pct_of_gross": inputs.fees_pct_of_gross,
        "last_alert_minutes_ago": inputs.last_alert_minutes_ago,
        "uptime_seconds": inputs.uptime_seconds,
        "testnet": inputs.testnet,
    }

    violations = _check_invariants(inputs)
    if violations:
        payload["invariant_violations"] = list(violations)

    return HealthReport(
        status=status,
        reasons=tuple(down + degraded),
        invariant_violations=violations,
        payload=payload,
    )


def write_health(report: HealthReport, path: Optional[Path] = None) -> Path:
    """Ghi NGUYÊN TỬ (tmp + rename) — cùng lý do với `write_state_snapshot`:
    một tiến trình crash đúng giữa lúc ghi không được để lại `health.json`
    nửa vời, vì thứ đọc nó tiếp theo là người đang xử lý sự cố.

    KHÔNG raise: đây là đường quan sát, không phải đường giao dịch. Một
    volume hết chỗ không được phép giết vòng lặp chính. Nhưng lỗi lập
    trình thì VẪN raise — xem nhánh `except` bên dưới.
    """
    target = path or default_health_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(report.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        # Hạ tầng: hết chỗ, quyền, volume chưa mount. Ghi log rồi đi tiếp.
        # KHÔNG bắt `Exception`: `TypeError` từ `json.dumps` (payload chứa
        # thứ không serialise được) là LỖI LẬP TRÌNH và phải nổ ra ngay ở
        # test, không được biến thành một dòng WARNING mà không ai đọc.
        logger.warning("Không ghi được %s: %s", target, exc)
    return target


def assert_healthy_or_alert(
    report_provider: Callable[[], HealthReport],
    alert_manager: Any,
    *,
    delay_seconds: float = _STARTUP_CHECK_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> HealthReport:
    """§B.3 — chờ `delay_seconds` sau khởi động rồi kiểm một lần.

    Vì sao cần: chu kỳ kiểm định kỳ đầu tiên có thể còn xa (bar 1D). Một
    bot khởi động sai cấu hình sẽ nằm im ở trạng thái hỏng suốt quãng đó
    mà không ai biết — đúng chế độ hỏng đã xảy ra ngày 2026-08-06..08 với
    forward test (dừng im lặng 3 ngày, xem `docs/DECISIONS.md`).

    `sleep` tiêm được để test không phải chờ 60 giây thật. `report_provider`
    là callable chứ không phải `HealthReport` dựng sẵn: phải đo SAU khi
    ngủ dậy, không phải trước khi ngủ — chụp trước rồi ngủ thì hàm này chỉ
    xác nhận bot khởi động được, đúng việc `ops/health_check.py` đã làm.
    """
    from monitoring.alerts import Alert, AlertType

    sleep(delay_seconds)
    report = report_provider()

    if report.invariant_violations:
        # Lỗi lập trình, KHÔNG phải sự cố vận hành — xem `_check_invariants`.
        for violation in report.invariant_violations:
            alert_manager.send(
                Alert(
                    AlertType.INTERNAL_ERROR,
                    f"Kiểm tra sau khởi động ({delay_seconds:.0f}s): {violation}",
                    severity="CRITICAL",
                )
            )

    if report.status != STATUS_OK:
        # `HEALTH_CHECK_FAILED`, KHÔNG phải `API_LOST`/`DATA_FEED_LOST`:
        # status có thể là `down` vì circuit breaker đang halt, hoặc
        # `degraded` vì model HMM quá cũ — không liên quan gì tới feed hay
        # API. Chọn đại một trong hai nhãn có sẵn sẽ gửi người vận hành đi
        # kiểm tra mạng trong lúc vấn đề nằm ở chỗ khác. Lý do thật nằm
        # trong `report.reasons`, đính kèm nguyên văn.
        alert_manager.send(
            Alert(
                AlertType.HEALTH_CHECK_FAILED,
                f"Sức khoẻ sau khởi động ({delay_seconds:.0f}s): {report.status.upper()} — "
                + "; ".join(report.reasons),
                severity="CRITICAL" if report.status == STATUS_DOWN else "WARNING",
            )
        )

    return report
