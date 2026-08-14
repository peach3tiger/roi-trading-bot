"""Giám sát chất lượng dữ liệu — tiến trình RIÊNG. Phase 12d §B.

## Điểm quan trọng nhất của file này: KHÔNG tự khoá khi thị trường sập

Ngưỡng "giá nhảy > 30%/bar" là ngưỡng hợp lý cho **lỗi dữ liệu**. Nhưng
BTC đã từng giảm ~40% trong một ngày (12/03/2020), và một cú sập thật là
lúc **cần bot hoạt động nhất** — đó là lúc trend gate hạ trần, HMM chuyển
sang CRASH, risk manager cắt size. Khoá bot đúng lúc đó là cách chắc chắn
để bỏ lỡ chính hành vi phòng vệ đã xây bảy phase để có.

Nên `|Δ| > 30%` KHÔNG phải kết luận, nó là CÂU HỎI. Câu trả lời đến từ
nguồn thứ hai (xem `classify_price_move`).

## Nhịp kiểm khớp nhịp bar, không phải nhịp đồng hồ

Bot chạy bar 1D: mỗi ngày đúng một bar mới. Kiểm tính đúng đắn mỗi 30
giây là chạy cùng một phép trên cùng dữ liệu 2880 lần/ngày. Tách hai:
đủ bộ khi CÓ BAR MỚI, chỉ độ tươi mỗi 15 phút.

## Lock phải xoá THỦ CÔNG

Giống `trading_halted.lock`. Một lock tự hết hạn nghĩa là nguyên nhân
không bao giờ bị điều tra — bot tự chạy lại trên dữ liệu vẫn còn hỏng.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "state"
LOCK_FILENAME = "data_quality.lock"

# §B.3 — dưới ngưỡng này thì không cần hỏi nguồn thứ hai.
LARGE_MOVE_PCT = Decimal("30")
# Hai nguồn chênh nhau quá chừng này thì coi là KHÔNG khớp. 2%: đủ rộng
# cho chênh lệch thật giữa hai sàn (thanh khoản, giờ chốt bar lệch vài
# giây), đủ hẹp để một bar hỏng -35% không thể trốn qua.
SOURCE_AGREE_PCT = Decimal("2")
# §B.1 — bar mới nhất cũ hơn chừng này lần chu kỳ bar thì coi là mất tươi.
STALE_BAR_MULTIPLE = 1.5
FRESHNESS_POLL_SECONDS = 15 * 60

MOVE_REAL = "bien_dong_that"
MOVE_BAD_DATA = "loi_du_lieu"
MOVE_UNVERIFIED = "khong_xac_minh_duoc"


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR))


def lock_path() -> Path:
    return state_dir() / LOCK_FILENAME


@dataclass(frozen=True)
class Violation:
    """Một vi phạm cụ thể, kèm ĐỦ dữ kiện để tái lập.

    `bar`/`values` bắt buộc, không phải tuỳ chọn: một dòng "dữ liệu sai"
    không nói bar nào và giá trị bao nhiêu buộc người vận hành phải tự đi
    tìm lại — trong lúc bot đang dừng.
    """

    check: str
    bar: str
    detail: str
    values: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# §B.2 — tính đúng đắn. Hàm THUẦN trên DataFrame.
# ----------------------------------------------------------------------


def check_integrity(bars: pd.DataFrame, *, bar_period: timedelta = timedelta(days=1)) -> list[Violation]:
    """Toàn bộ kiểm §B.2. Trả về MỌI vi phạm, không dừng ở cái đầu tiên —
    khi dữ liệu hỏng, biết nó hỏng ở ba chỗ hay một chỗ là thông tin khác
    nhau về nguyên nhân."""
    vi_pham: list[Violation] = []
    if bars.empty:
        return vi_pham

    def _ts(i: Any) -> str:
        return str(i)

    for idx, row in bars.iterrows():
        o, h, low, c = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
        gia = {"open": o, "high": h, "low": low, "close": c}
        if low > h:
            vi_pham.append(Violation("low <= high", _ts(idx), f"low {low} > high {h}", gia))
        if not (low <= c <= h):
            vi_pham.append(Violation("low <= close <= high", _ts(idx), f"close {c} ngoài [{low}, {h}]", gia))
        if not (low <= o <= h):
            vi_pham.append(Violation("low <= open <= high", _ts(idx), f"open {o} ngoài [{low}, {h}]", gia))

        vol = float(row["volume"])
        if vol < 0:
            vi_pham.append(Violation("volume >= 0", _ts(idx), f"volume {vol}", {"volume": vol}))
        elif vol == 0:
            # Với BTC/USDT bar NGÀY, volume 0 không phải "thị trường
            # trầm lắng" — nó chắc chắn là lỗi dữ liệu.
            vi_pham.append(
                Violation("volume != 0", _ts(idx), "volume == 0 trên bar ngày BTC/USDT", {"volume": vol})
            )

        if "trade_count" in bars.columns:
            tc = float(row["trade_count"])
            if tc < 0:
                vi_pham.append(
                    Violation("trade_count >= 0", _ts(idx), f"trade_count {tc}", {"trade_count": tc})
                )

    vi_pham.extend(_check_timestamps(bars, bar_period))
    return vi_pham


def _check_timestamps(bars: pd.DataFrame, bar_period: timedelta) -> list[Violation]:
    """Trùng lặp và thiếu bar. Tách riêng vì cả hai là tính chất của CHUỖI,
    không của từng dòng."""
    ra: list[Violation] = []
    index = pd.DatetimeIndex(bars.index)

    trung = index[index.duplicated()]
    for ts_trung in trung.unique():
        ra.append(Violation("timestamp không trùng", str(ts_trung), "timestamp xuất hiện nhiều lần"))

    if len(index) < 2:
        return ra
    khoang = index.to_series().diff().dropna()
    for ts, delta in khoang.items():
        if delta > bar_period:
            thieu = int(delta / bar_period) - 1
            ra.append(
                Violation(
                    "không thiếu bar",
                    str(ts),
                    f"khoảng {delta} so với bar trước — thiếu {thieu} bar",
                    {"gap": str(delta)},
                )
            )
    return ra


# ----------------------------------------------------------------------
# §B.1 — độ tươi
# ----------------------------------------------------------------------


def check_freshness(
    latest_bar: Optional[datetime],
    *,
    now: datetime,
    bar_period: timedelta = timedelta(days=1),
    multiple: float = STALE_BAR_MULTIPLE,
) -> Optional[Violation]:
    """`None` = tươi. Không có bar nào -> vi phạm (KHÔNG phải "chưa biết"):
    một nguồn dữ liệu rỗng ở tầng giám sát nghĩa là không có gì để giao
    dịch trên đó."""
    if latest_bar is None:
        return Violation("có dữ liệu", "—", "không có bar nào")
    tuoi = now - latest_bar
    han = bar_period * multiple
    if tuoi > han:
        return Violation(
            "độ tươi",
            latest_bar.isoformat(),
            f"bar mới nhất cũ {tuoi} (> {multiple}× chu kỳ {bar_period})",
            {"age_seconds": tuoi.total_seconds()},
        )
    return None


# ----------------------------------------------------------------------
# §B.3 — giá nhảy lớn: HỎI, không kết luận
# ----------------------------------------------------------------------


def pct_change(truoc: Decimal, sau: Decimal) -> Decimal:
    if truoc == 0:
        return Decimal("0")
    return (sau - truoc) / truoc * Decimal("100")


def classify_price_move(
    move_pct: Decimal,
    primary_close: Decimal,
    secondary_close: Optional[Decimal],
    *,
    threshold_pct: Decimal = LARGE_MOVE_PCT,
    agree_pct: Decimal = SOURCE_AGREE_PCT,
) -> str:
    """Bốn nhánh §B.3. THUẦN — nguồn thứ hai đã được lấy sẵn và truyền vào.

    Trả `""` khi biến động chưa đủ lớn để phải hỏi.

    `secondary_close=None` -> `MOVE_UNVERIFIED` -> caller GHI LOCK. Thận
    trọng có chủ ý: không xác minh được thì không được phép coi là bình
    thường. Nhưng nó khác `MOVE_BAD_DATA` ở nhãn, vì hai tình huống cần
    hai hành động khác nhau (sửa kết nối nguồn phụ vs điều tra dữ liệu).
    """
    if abs(move_pct) <= threshold_pct:
        return ""
    if secondary_close is None:
        return MOVE_UNVERIFIED
    lech = abs(pct_change(primary_close, secondary_close))
    return MOVE_REAL if lech < agree_pct else MOVE_BAD_DATA


# ----------------------------------------------------------------------
# Lock
# ----------------------------------------------------------------------


def write_lock(violations: Sequence[Violation], *, path: Optional[Path] = None, reason: str = "") -> Path:
    """Ghi `data_quality.lock`. Bot đọc file này và DỪNG SINH SIGNAL MỚI,
    giữ nguyên vị thế và stop.

    Xoá THỦ CÔNG (§B.4) — một lock tự hết hạn nghĩa là nguyên nhân không
    bao giờ bị điều tra.
    """
    target = path or lock_path()
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": reason or "kiểm tra chất lượng dữ liệu thất bại",
        "violations": [
            {"check": v.check, "bar": v.bar, "detail": v.detail, "values": v.values} for v in violations
        ],
        "cach_xoa": (
            f"Điều tra trước, rồi `rm {target}` bằng tay. KHÔNG tự hết hạn — "
            "xem ops/RUNBOOK.md mục DATA_QUALITY."
        ),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.error("Không ghi được %s: %s — bot sẽ KHÔNG dừng signal!", target, exc)
    return target


def lock_active(path: Optional[Path] = None) -> bool:
    return (path or lock_path()).exists()


# ----------------------------------------------------------------------
# Chạy một lượt
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessResult:
    violations: tuple[Violation, ...]
    move_verdict: str
    lock_written: bool


def run_integrity_check(
    bars: pd.DataFrame,
    *,
    secondary_close: Optional[Decimal] = None,
    fetch_secondary: Optional[Callable[[], Optional[Decimal]]] = None,
    bar_period: timedelta = timedelta(days=1),
    lock_file: Optional[Path] = None,
    alert_manager: Any = None,
) -> HarnessResult:
    """Đủ bộ §B.2 + §B.3 trên `bars`. Chạy khi CÓ BAR MỚI.

    `fetch_secondary` chỉ được gọi khi biến động vượt ngưỡng — không tốn
    một round-trip mạng mỗi bar cho một nhánh gần như không bao giờ chạy.
    """
    vi_pham = check_integrity(bars, bar_period=bar_period)

    move_verdict = ""
    if len(bars) >= 2:
        truoc = Decimal(str(bars["close"].iloc[-2]))
        sau = Decimal(str(bars["close"].iloc[-1]))
        thay_doi = pct_change(truoc, sau)
        if abs(thay_doi) > LARGE_MOVE_PCT:
            nguon_hai = secondary_close
            if nguon_hai is None and fetch_secondary is not None:
                try:
                    nguon_hai = fetch_secondary()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Không lấy được nguồn thứ hai: %s", exc)
                    nguon_hai = None
            move_verdict = classify_price_move(thay_doi, sau, nguon_hai)
            vi_pham.extend(_move_violations(move_verdict, thay_doi, sau, nguon_hai, str(bars.index[-1])))
            _alert_move(alert_manager, move_verdict, thay_doi, nguon_hai)

    lock_written = False
    if vi_pham:
        write_lock(vi_pham, path=lock_file)
        lock_written = True
        _alert_violations(alert_manager, vi_pham)

    return HarnessResult(tuple(vi_pham), move_verdict, lock_written)


def _move_violations(
    verdict: str, move_pct: Decimal, close: Decimal, secondary: Optional[Decimal], bar: str
) -> list[Violation]:
    """`MOVE_REAL` KHÔNG sinh vi phạm — đó là toàn bộ điểm của §B.3."""
    if verdict == MOVE_BAD_DATA:
        return [
            Violation(
                "hai nguồn khớp nhau",
                bar,
                f"nhảy {move_pct:.1f}% nhưng nguồn phụ báo {secondary} vs {close}",
                {"move_pct": str(move_pct), "primary": str(close), "secondary": str(secondary)},
            )
        ]
    if verdict == MOVE_UNVERIFIED:
        return [
            Violation(
                "xác minh được bằng nguồn thứ hai",
                bar,
                f"nhảy {move_pct:.1f}% và KHÔNG lấy được nguồn thứ hai — khoá vì thận trọng",
                {"move_pct": str(move_pct)},
            )
        ]
    return []


def _alert_move(alert_manager: Any, verdict: str, move_pct: Decimal, secondary: Optional[Decimal]) -> None:
    if alert_manager is None or verdict != MOVE_REAL:
        return
    from monitoring.alerts import Alert, AlertType

    alert_manager.send(
        Alert(
            AlertType.LARGE_PRICE_MOVE,
            f"Giá nhảy {move_pct:.1f}% trong một bar, ĐÃ xác nhận bằng nguồn thứ hai "
            f"({secondary}). Biến động THẬT — bot chạy tiếp, KHÔNG khoá.",
            severity="WARNING",
        )
    )


def _alert_violations(alert_manager: Any, violations: Sequence[Violation]) -> None:
    if alert_manager is None:
        return
    from monitoring.alerts import Alert, AlertType

    dau = violations[0]
    them = f" (+{len(violations) - 1} vi phạm khác)" if len(violations) > 1 else ""
    alert_manager.send(
        Alert(
            AlertType.DATA_QUALITY_FAILED,
            f"Dữ liệu hỏng: [{dau.check}] bar {dau.bar} — {dau.detail}{them}. "
            "Đã ghi data_quality.lock, bot dừng sinh signal mới (giữ vị thế và stop).",
            severity="CRITICAL",
        )
    )


def main() -> int:
    """Vòng lặp độ tươi mỗi 15 phút; đủ bộ khi thấy bar mới."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    import main as main_mod
    from data.history_loader import HistoryLoader

    settings = main_mod.load_settings()
    symbol = settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
    alert_manager = main_mod.build_alert_manager(settings)
    loader = HistoryLoader()
    bar_cuoi_da_kiem: Optional[Any] = None

    while True:
        try:
            now = datetime.now(timezone.utc)
            bars = loader.load(ccxt_symbol, "1D", now - timedelta(days=60), now)
            latest = bars.index[-1].to_pydatetime() if len(bars) else None

            tuoi = check_freshness(latest, now=now)
            if tuoi is not None:
                write_lock([tuoi])
                _alert_violations(alert_manager, [tuoi])
            elif latest != bar_cuoi_da_kiem:
                run_integrity_check(bars, alert_manager=alert_manager)
                bar_cuoi_da_kiem = latest
        except Exception:
            logger.error("Lỗi trong data harness — tiếp tục vòng sau.", exc_info=True)
        time.sleep(FRESHNESS_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
