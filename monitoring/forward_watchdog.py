"""monitoring.forward_watchdog — canh gác độ tươi file log forward test.

Canh file ĐANG HOẠT ĐỘNG (`forward.runner.ACTIVE_LOG_PATH`, hiện là
`forward/log_v2.csv`), không hardcode tên file — xem `_active_log_path()`.

Thí nghiệm forward chạy 12 tháng không người trông. Chế độ hỏng nguy hiểm
nhất KHÔNG phải là nó chạy sai — mà là nó **dừng im lặng**: launchd job
chết, không ai đọc `forward/launchd.err.log`, và phát hiện ra sau nhiều
tuần thì dữ liệu đã mất vĩnh viễn (không backfill được quá xa vì bar phải
được ghi bằng đúng trạng thái HMM tại thời điểm đó).

Đã xảy ra một lần, 2026-08-06 → 2026-08-08: `warning_count` được thêm vào
`_CSV_FIELDNAMES` sau khi log đã bắt đầu, `append_row()` chỉ ghi header khi
file CHƯA tồn tại nên header cũ 31 cột ở lại vĩnh viễn, dòng mới ghi 32
cột, và `read_existing_log()` chết ở `pd.read_csv` mỗi lần chạy. Job vẫn
được lên lịch đều, vẫn "chạy", chỉ là lần nào cũng exit 1. Không có gì
báo. Xem `docs/DECISIONS.md`.

TÍN HIỆU DÙNG ĐỂ QUYẾT ĐỊNH: `max(date)` trong file log — KHÔNG phải mtime,
KHÔNG phải số dòng.

- `mtime` nói dối: `git checkout`/`git stash`/copy file đều làm nó mới lại
  mà không có bar nào được ghi thêm.
- Số dòng cần state file để so với lần trước, và state file đó lại là một
  thứ nữa có thể mất/hỏng im lặng — đúng chế độ hỏng ta đang cố bắt.
- `max(date)` tự mang trạng thái, đọc trực tiếp từ chính bằng chứng thí
  nghiệm, không cần nhớ gì giữa hai lần chạy.

Cả hai vẫn được ĐO và đưa vào thông điệp cảnh báo làm dữ liệu chẩn đoán —
chỉ là không dùng để ra quyết định.

Bar ngày D được ghi vào ngày D+1 (sau 00:00 UTC), nên `staleness_days = 1`
là TRẠNG THÁI BÌNH THƯỜNG. Ngưỡng mặc định `> 2` chịu được đúng một lần lỡ
lịch (laptop ngủ/tắt qua mốc 08:00) mà không kêu oan, và kêu từ lần lỡ thứ
hai trở đi.

Chạy bằng LaunchAgent riêng, KHÔNG gộp vào job forward test: một watchdog
sống chung tiến trình với thứ nó canh sẽ chết cùng thứ đó, và khi đó
không canh được gì nữa.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_ROOT / ".env"


def _active_log_path() -> Path:
    """File log ĐANG HOẠT ĐỘNG, hỏi `forward.runner` chứ không hardcode.

    Schema log cuộn file khi đổi cột (`forward/SCHEMA.md`) — v1 `log.csv`
    đã đóng vĩnh viễn ở 1 bar, v2 `log_v2.csv` đang chạy. Hardcode tên file
    ở đây nghĩa là lần cuộn sang v3, watchdog sẽ canh một file ĐÃ ĐÓNG:
    file đó không bao giờ tăng dòng nữa nên watchdog kêu mỗi ngày, bị coi
    là báo động giả, rồi bị tắt — đúng lúc nó mất khả năng canh thật.
    """
    from forward.runner import ACTIVE_LOG_PATH

    return ACTIVE_LOG_PATH


# Bar D ghi vào ngày D+1 → staleness 1 = bình thường, 2 = lỡ một lần
# (chấp nhận được), > 2 = lỡ từ hai lần trở lên (bất thường thật sự).
_DEFAULT_MAX_STALENESS_DAYS = 2

EXIT_OK = 0
EXIT_INTERNAL_ERROR = 1
EXIT_STALE = 2
EXIT_CADENCE_DRIFT = 3

# ----------------------------------------------------------------------
# Nhịp retrain
# ----------------------------------------------------------------------

# MỐC BẮT ĐẦU GIÁM SÁT — không phải ngoại lệ hardcode.
#
# Sai lệch #1 (lịch retrain reset khi cuộn schema, xem docs/DECISIONS.md)
# xảy ra ở khoảng 08-05 → 08-08, TRƯỚC mốc này, và đã được ghi nhận riêng
# ở đó. Phép kiểm này KHÔNG cần biết gì về nó.
#
# Sự khác biệt là quan trọng. Một ngoại lệ hardcode nghĩa là "bỏ qua đúng
# lần lệch ngày 08-08" — nó biến phép kiểm thành thứ phải mang theo danh
# sách miễn trừ, và mỗi mục trong danh sách đó là một chỗ phép kiểm nói
# dối. Một mốc bắt đầu giám sát thì khác: nó nói "cơ chế này bắt đầu chạy
# từ đây", đúng như mọi hệ thống giám sát khởi động ở một thời điểm nào
# đó. Trước mốc không có dữ liệu giám sát; sau mốc mọi lệch nhịp đều là
# bất thường thật, vì quy tắc "KHÔNG cuộn schema trong thời gian thí
# nghiệm" (forward/SCHEMA.md) đã loại bỏ nguyên nhân duy nhất đã biết.
#
# Cụ thể: chỉ xét khoảng cách nào có ĐIỂM SAU >= mốc này. Khoảng
# 08-05 → 08-08 có điểm sau là 08-08 < 08-09 nên rơi ngoài phạm vi giám
# sát một cách tự nhiên, không cần nhắc tới nó ở bất kỳ đâu trong code.
_CADENCE_MONITORING_START = date(2026, 8, 9)

# ±1 ngày: bar là mốc UTC nguyên ngày, và runner có thể chạy bù nhiều bar
# trong một lần (backfill sau khi máy ngủ), nên một ngày xê dịch là bình
# thường. Lệch 2 ngày trở lên thì không giải thích được bằng lịch chạy.
_CADENCE_TOLERANCE_DAYS = 1

CADENCE_OK = "ok"
CADENCE_DRIFT = "drift"
CADENCE_NO_DATA = "no_data"  # chưa đủ 2 lần retrain sau mốc giám sát
CADENCE_UNAVAILABLE = "unavailable"  # không đọc được log/config


@dataclass(frozen=True)
class RetrainGap:
    """Khoảng cách giữa HAI lần retrain liên tiếp."""

    previous: date
    current: date
    days: int
    ok: bool


@dataclass(frozen=True)
class TrailingGap:
    """Khoảng HỞ CUỐI: từ lần retrain gần nhất tới HÔM NAY.

    Kiểu RIÊNG, không tái dùng `RetrainGap`: ở đó `current` là một lần
    retrain đã xảy ra, còn ở đây điểm sau là hôm nay — một thời điểm chưa
    có sự kiện nào. Nhồi hai ý nghĩa vào một kiểu là loại nhập nhằng chỉ
    lộ ra khi có người đọc `gaps` rồi tưởng mọi `current` đều là ngày
    retrain.

    Chỉ có TRẦN, không có sàn: khoảng hở ngắn là bình thường (vừa retrain
    xong). Khác `RetrainGap` vốn kiểm cả hai phía.
    """

    last_retrain: date
    today: date
    days: int
    ok: bool


@dataclass(frozen=True)
class RetrainCadence:
    status: str
    detail: str
    interval_days: Optional[int] = None
    gaps: tuple[RetrainGap, ...] = ()
    trailing_gap: Optional[TrailingGap] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "interval_days": self.interval_days,
            "monitoring_since": _CADENCE_MONITORING_START.isoformat(),
            "gaps": [
                {
                    "previous": g.previous.isoformat(),
                    "current": g.current.isoformat(),
                    "days": g.days,
                    "ok": g.ok,
                }
                for g in self.gaps
            ],
            "trailing_gap": (
                None
                if self.trailing_gap is None
                else {
                    "last_retrain": self.trailing_gap.last_retrain.isoformat(),
                    "today": self.trailing_gap.today.isoformat(),
                    "days": self.trailing_gap.days,
                    "ok": self.trailing_gap.ok,
                }
            ),
        }


def check_retrain_cadence(
    monitoring_start: Optional[date] = None,
    tolerance_days: int = _CADENCE_TOLERANCE_DAYS,
    today_utc: Optional[date] = None,
) -> RetrainCadence:
    """Nhịp retrain HMM có đúng `retrain_interval_days` không.

    Kiểm HAI thứ:
      1. Khoảng cách giữa hai lần retrain liên tiếp — `interval ± tolerance`.
      2. Khoảng HỞ CUỐI (`today - lần retrain gần nhất`) — chỉ có trần.
         Không có nó, "retrain ngừng hẳn" là điểm mù: hai lần đúng nhịp rồi
         im lặng 60 ngày cho `gaps` toàn xanh và `status = ok`.

    Đọc log ĐÃ NỐI (`forward.runner.load_all_bars()`) chứ không phải file
    đang hoạt động: chính việc `run_forward_test()` chỉ nhìn file đang
    hoạt động là nguyên nhân của sai lệch #1. Một phép kiểm mắc lại đúng
    điểm mù mà nó sinh ra để canh thì vô nghĩa.

    Đặt ở đây, KHÔNG ở `forward/logger.py` — file đó đóng băng với SHA256
    ghim, sửa nó = kết thúc thí nghiệm (CLAUDE.md bất biến #15). Watchdog
    là chỗ đúng cho mọi phép kiểm thêm vào giữa chừng: nó chỉ ĐỌC, không
    tham gia vào đường sinh dữ liệu, nên thêm bao nhiêu cũng không đụng
    tới tính toàn vẹn của thí nghiệm.

    KHÔNG BAO GIỜ raise — cùng hợp đồng với phần còn lại của watchdog.
    Không đọc được config/log thì trả `CADENCE_UNAVAILABLE`, không phải
    `CADENCE_OK`: "không kiểm được" khác hẳn "đã kiểm, không sao".
    """
    start = monitoring_start if monitoring_start is not None else _CADENCE_MONITORING_START

    try:
        from forward.logger import load_frozen_settings

        interval = int(load_frozen_settings()["hmm"]["retrain_interval_days"])
    except Exception as exc:  # noqa: BLE001 — mọi lỗi đọc config đều là "không kiểm được"
        return RetrainCadence(
            status=CADENCE_UNAVAILABLE,
            detail=f"Không đọc được retrain_interval_days: {type(exc).__name__}: {exc}",
        )

    try:
        from forward.runner import load_all_bars

        df = load_all_bars()
    except Exception as exc:  # noqa: BLE001
        return RetrainCadence(
            status=CADENCE_UNAVAILABLE,
            detail=f"Không đọc được log đã nối: {type(exc).__name__}: {exc}",
            interval_days=interval,
        )

    retrain_dates = sorted(d.date() for d in df.loc[df["hmm_retrained"], "date"])

    gaps = tuple(
        RetrainGap(
            previous=prev,
            current=cur,
            days=(cur - prev).days,
            ok=abs((cur - prev).days - interval) <= tolerance_days,
        )
        for prev, cur in zip(retrain_dates, retrain_dates[1:])
        # ĐIỂM SAU quyết định phạm vi giám sát — xem chú thích
        # `_CADENCE_MONITORING_START`.
        if cur >= start
    )

    # KHOẢNG HỞ CUỐI — từ lần retrain gần nhất tới HÔM NAY.
    #
    # Các khoảng ở trên chỉ nhìn được những lần retrain ĐÃ xảy ra, nên
    # chúng mù hoàn toàn với chế độ hỏng nguy hiểm nhất: retrain NGỪNG HẲN.
    # Hai lần retrain đúng nhịp rồi im lặng 60 ngày cho `gaps` toàn xanh và
    # `status = ok` — đúng lúc cần báo động nhất.
    #
    # Cùng mốc giám sát, cùng quy tắc "ĐIỂM SAU quyết định phạm vi": điểm
    # sau ở đây là hôm nay, nên phép kiểm bật khi `today >= start`.
    #
    # Chỉ có TRẦN (`> interval + tolerance`), không có sàn: khoảng hở ngắn
    # nghĩa là vừa retrain xong — bình thường, không phải bất thường.
    today = today_utc if today_utc is not None else datetime.now(timezone.utc).date()
    trailing: Optional[TrailingGap] = None
    if retrain_dates and today >= start:
        last_retrain = retrain_dates[-1]
        idle_days = (today - last_retrain).days
        trailing = TrailingGap(
            last_retrain=last_retrain,
            today=today,
            days=idle_days,
            ok=idle_days <= interval + tolerance_days,
        )

    if not gaps and trailing is None:
        return RetrainCadence(
            status=CADENCE_NO_DATA,
            detail=(
                f"Chưa đủ dữ liệu: {len(retrain_dates)} lần retrain trong log, "
                f"0 khoảng cách nào có điểm sau >= {start} (mốc bắt đầu giám sát)."
            ),
            interval_days=interval,
        )

    bad = [g for g in gaps if not g.ok]
    problems: list[str] = []
    if bad:
        lines = "; ".join(
            f"{g.previous} → {g.current} cách {g.days} ngày (chờ đợi {interval}±{tolerance_days})"
            for g in bad
        )
        problems.append(f"{len(bad)}/{len(gaps)} khoảng cách retrain lệch nhịp: {lines}")
    if trailing is not None and not trailing.ok:
        # Thông điệp CỐ TÌNH khác hẳn nhánh trên: "chưa retrain N ngày" mô
        # tả một việc KHÔNG xảy ra, còn "nhịp lệch" mô tả một việc đã xảy
        # ra sai thời điểm. Người đọc alert cần phân biệt ngay — hai triệu
        # chứng này dẫn tới hai chỗ điều tra khác nhau.
        problems.append(
            f"CHƯA RETRAIN {trailing.days} ngày — lần cuối {trailing.last_retrain}, "
            f"hôm nay {trailing.today} (trần {interval}+{tolerance_days} ngày)"
        )

    if problems:
        return RetrainCadence(
            status=CADENCE_DRIFT,
            detail=". ".join(problems),
            interval_days=interval,
            gaps=gaps,
            trailing_gap=trailing,
        )

    parts = []
    if gaps:
        parts.append(f"{len(gaps)} khoảng cách retrain, tất cả trong {interval}±{tolerance_days} ngày")
    else:
        parts.append(f"chưa có khoảng cách retrain nào sau {start}")
    if trailing is not None:
        parts.append(f"chưa retrain {trailing.days} ngày (trần {interval}+{tolerance_days})")
    return RetrainCadence(
        status=CADENCE_OK,
        detail=". ".join(parts) + ".",
        interval_days=interval,
        gaps=gaps,
        trailing_gap=trailing,
    )


# ----------------------------------------------------------------------
# Môi trường
# ----------------------------------------------------------------------


def load_dotenv(path: Optional[Path] = None) -> list[str]:
    """Nạp `.env` vào `os.environ`, trả về TÊN các biến đã nạp.

    launchd chạy với môi trường tối thiểu (`PATH=/usr/bin:/bin:...`, không
    có gì từ shell của người dùng), nên `TELEGRAM_BOT_TOKEN`/
    `TELEGRAM_CHAT_ID` trong `.env` sẽ KHÔNG tự có mặt — không nạp ở đây
    thì `AlertManager` im lặng bỏ qua kênh Telegram và watchdog trở thành
    thứ vô dụng nhất có thể: một cảnh báo không ai nhận được.

    KHÔNG dùng `EnvironmentVariables` trong plist: plist là file được
    commit, nhét token vào đó là hardcode credentials (CLAUDE.md bất biến
    #6). `.env` nằm trong `.gitignore` và ở đúng một chỗ.

    Biến đã có sẵn trong `os.environ` được GIỮ NGUYÊN — môi trường thật
    luôn thắng file, để chạy tay với biến tạm không bị `.env` ghi đè.

    Trả về tên biến, KHÔNG BAO GIỜ giá trị (bất biến #6: không log
    credential kể cả một phần).
    """
    target = path if path is not None else _ENV_PATH
    if not target.exists():
        return []
    loaded: list[str] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
        loaded.append(key)
    return loaded


# ----------------------------------------------------------------------
# Đo độ tươi
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class LogFreshness:
    """Ảnh chụp trạng thái một file log forward test tại một thời điểm."""

    path: Path
    exists: bool
    parse_ok: bool
    parse_error: Optional[str]
    row_count: Optional[int]
    mtime_utc: Optional[datetime]
    last_bar_date: Optional[date]
    staleness_days: Optional[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "parse_ok": self.parse_ok,
            "parse_error": self.parse_error,
            "row_count": self.row_count,
            "mtime_utc": self.mtime_utc.isoformat() if self.mtime_utc else None,
            "last_bar_date": self.last_bar_date.isoformat() if self.last_bar_date else None,
            "staleness_days": self.staleness_days,
        }


def inspect_log(path: Optional[Path] = None, today_utc: Optional[date] = None) -> LogFreshness:
    """Đọc file log bằng ĐÚNG hàm forward test dùng (`read_existing_log`).

    Cố tình KHÔNG tự viết một bộ đọc CSV riêng: nếu watchdog parse được
    bằng cách khác trong khi `forward/logger.py` thì không, watchdog sẽ báo
    "khoẻ" đúng lúc thí nghiệm đang chết — chính xác lỗi 2026-08-06. Dùng
    chung hàm đọc nghĩa là watchdog hỏng khi và chỉ khi thí nghiệm hỏng.
    """
    target = path if path is not None else _active_log_path()
    now = today_utc if today_utc is not None else datetime.now(timezone.utc).date()

    def _broken(reason: str, mtime: Optional[datetime]) -> LogFreshness:
        return LogFreshness(
            path=target,
            exists=target.exists(),
            parse_ok=False,
            parse_error=reason,
            row_count=None,
            mtime_utc=mtime,
            last_bar_date=None,
            staleness_days=None,
        )

    if not target.exists():
        return LogFreshness(
            path=target,
            exists=False,
            parse_ok=False,
            parse_error="file không tồn tại",
            row_count=None,
            mtime_utc=None,
            last_bar_date=None,
            staleness_days=None,
        )

    mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)

    try:
        from forward.logger import _CSV_FIELDNAMES, read_existing_log

        df = read_existing_log(target)
    except Exception as exc:  # noqa: BLE001 — mọi lỗi đọc đều là "thí nghiệm đang chết"
        return _broken(f"{type(exc).__name__}: {exc}", mtime)

    # "Đọc được" KHÔNG đồng nghĩa "đọc đúng". Nếu MỌI dòng đều dư đúng một
    # trường so với header, pandas không ném lỗi — nó lặng lẽ lấy cột đầu
    # làm index, và toàn bộ cột dịch đi một ô. Đo được bằng đột biến: bản
    # watchdog đầu tiên báo "khoẻ" trên đúng kiểu hỏng này, `date` khi đó
    # chứa giá trị `run_at_utc` nên `staleness` vẫn trông bình thường.
    # Ghim thẳng danh sách cột vào `_CSV_FIELDNAMES` là cách duy nhất phát
    # hiện được, vì mọi tín hiệu khác (parse ok, số dòng, kiểu dữ liệu) đều
    # trông lành lặn.
    if df is not None:
        actual = list(df.columns)
        if actual != list(_CSV_FIELDNAMES):
            missing = [c for c in _CSV_FIELDNAMES if c not in actual]
            extra = [c for c in actual if c not in _CSV_FIELDNAMES]
            return _broken(
                "lệch schema: cột đọc được không khớp _CSV_FIELDNAMES "
                f"({len(actual)} vs {len(_CSV_FIELDNAMES)}); "
                f"thiếu={missing or '-'}, thừa={extra or '-'}. "
                "Cột có thể đã dịch ô — KHÔNG tin số liệu trong file cho tới khi sửa.",
                mtime,
            )
        if not isinstance(df.index, pd.RangeIndex):
            return _broken(
                f"lệch schema: index không phải RangeIndex ({type(df.index).__name__}) "
                "— pandas đã nuốt một cột làm index, mọi cột sau đó dịch ô.",
                mtime,
            )

    if df is None or df.empty:
        return LogFreshness(
            path=target,
            exists=True,
            parse_ok=True,
            parse_error=None,
            row_count=0,
            mtime_utc=mtime,
            last_bar_date=None,
            staleness_days=None,
        )

    last = df["date"].max().date()
    return LogFreshness(
        path=target,
        exists=True,
        parse_ok=True,
        parse_error=None,
        row_count=int(len(df)),
        mtime_utc=mtime,
        last_bar_date=last,
        staleness_days=(now - last).days,
    )


# ----------------------------------------------------------------------
# Đánh giá
# ----------------------------------------------------------------------


def build_alert_message(
    freshness: LogFreshness, max_staleness_days: int = _DEFAULT_MAX_STALENESS_DAYS
) -> Optional[str]:
    """Trả về thông điệp cảnh báo, hoặc None nếu log còn tươi. Hàm THUẦN.

    Thông điệp luôn kèm mtime + số dòng (chẩn đoán) và lệnh cần gõ tiếp —
    cảnh báo đọc trên điện thoại lúc nửa đêm mà không nói được phải làm gì
    thì chỉ tạo lo lắng, không tạo hành động.
    """
    where = f"{freshness.path}"
    tail = (
        "\n\nKiểm tra:"
        "\n  launchctl print gui/$(id -u)/com.regime-trader-crypto.forward-test"
        "\n  tail -40 forward/launchd.err.log"
    )

    if not freshness.exists:
        return f"forward test ĐÃ DỪNG: {where} không tồn tại.{tail}"

    if not freshness.parse_ok:
        return (
            f"forward test ĐANG CHẾT: không đọc được {where}.\n"
            f"Lỗi: {freshness.parse_error}\n"
            f"Đây là lỗi ĐỌC, nghĩa là MỌI lần chạy sau đều exit 1 và không "
            f"bar nào được ghi thêm cho tới khi sửa.{tail}"
        )

    if freshness.row_count == 0 or freshness.last_bar_date is None:
        return f"forward test CHƯA CÓ DỮ LIỆU: {where} rỗng (0 dòng).{tail}"

    if (freshness.staleness_days or 0) > max_staleness_days:
        return (
            f"forward test IM LẶNG {freshness.staleness_days} ngày.\n"
            f"Bar cuối cùng: {freshness.last_bar_date} "
            f"(ngưỡng: {max_staleness_days} ngày)\n"
            f"Số dòng: {freshness.row_count}\n"
            f"mtime: {freshness.mtime_utc.isoformat() if freshness.mtime_utc else '?'}"
            f"{tail}"
        )

    return None


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def run_watchdog(
    path: Optional[Path] = None,
    max_staleness_days: int = _DEFAULT_MAX_STALENESS_DAYS,
    today_utc: Optional[date] = None,
    send: bool = True,
    env_path: Optional[Path] = None,
) -> dict[str, Any]:
    freshness = inspect_log(path, today_utc)
    message = build_alert_message(freshness, max_staleness_days)

    from monitoring.alerts import Alert, AlertManager, AlertType

    # Dựng AlertManager NGAY CẢ KHI log khoẻ, chỉ để biết kênh có gửi được
    # không. Nếu chỉ kiểm lúc có sự cố thì phát hiện "Telegram chưa cấu
    # hình" đúng vào hôm cần nó nhất — tức là không phát hiện gì cả.
    # `telegram_configured` xuất hiện trong watchdog.out.log MỖI NGÀY.
    # `env_path=None` (mặc định, và là thứ DUY NHẤT vận hành dùng) -> đọc
    # `.env` thật. Tham số tồn tại để TEST truyền đường dẫn tạm: bản cũ
    # gọi `load_dotenv()` cứng, nên mọi test chạm `run_watchdog` đều đọc
    # `.env` THẬT của máy dev và rò credential sang các test sau — xem
    # `tests/conftest.py::_cach_ly_moi_truong`. Một phụ thuộc ngầm vào
    # trạng thái máy là thứ phải làm cho TƯỜNG MINH, không phải thứ để
    # test đi vòng.
    load_dotenv(env_path)
    manager = AlertManager()
    telegram_ok = bool(manager.telegram_bot_token and manager.telegram_chat_id)

    cadence = check_retrain_cadence()

    result: dict[str, Any] = {
        "stale": message is not None,
        "message": message,
        "alert_sent": False,
        "telegram_configured": telegram_ok,
        "retrain_cadence": cadence.as_dict(),
        **freshness.as_dict(),
    }

    if not telegram_ok:
        logger.warning(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID rỗng hoặc thiếu trong .env — "
            "watchdog chỉ ghi được ra console/log, KHÔNG có cảnh báo nào tới "
            "điện thoại. Đây là điểm mù: thí nghiệm có thể dừng mà không ai hay."
        )

    if not send:
        return result

    # Hai cảnh báo ĐỘC LẬP: log có thể vừa tươi vừa lệch nhịp retrain (log
    # tăng dòng đều mỗi ngày, chỉ lịch retrain sai). Gộp chúng vào một
    # nhánh `if message is None: return` như bản trước sẽ làm cảnh báo nhịp
    # retrain không bao giờ phát khi log còn khoẻ — tức là gần như không
    # bao giờ.
    if message is not None:
        result["alert_sent"] = manager.send(
            Alert(
                alert_type=AlertType.FORWARD_LOG_STALE,
                message=message,
                severity="ERROR",
            )
        )

    if cadence.status == CADENCE_DRIFT:
        result["cadence_alert_sent"] = manager.send(
            Alert(
                alert_type=AlertType.RETRAIN_CADENCE_DRIFT,
                message=(
                    f"Nhịp retrain HMM lệch khỏi retrain_interval_days.\n"
                    f"{cadence.detail}\n"
                    f"Giám sát từ {_CADENCE_MONITORING_START} (xem docs/DECISIONS.md, "
                    f"'Sai lệch thí nghiệm #1')."
                ),
                severity="ERROR",
            )
        )
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Canh gác độ tươi forward/log.csv")
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--max-staleness-days", type=int, default=_DEFAULT_MAX_STALENESS_DAYS)
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Chỉ đo và in JSON, không gửi alert — dùng để kiểm tra bằng tay.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        result = run_watchdog(
            path=args.path,
            max_staleness_days=args.max_staleness_days,
            send=not args.no_send,
        )
    except Exception:
        logger.exception("watchdog lỗi nội bộ")
        return EXIT_INTERNAL_ERROR

    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    # Log stale ĐI TRƯỚC nhịp retrain khi cả hai cùng sai: log không tăng
    # dòng nghĩa là thí nghiệm đã dừng, còn lệch nhịp retrain thì thí
    # nghiệm vẫn chạy. Mã thoát chỉ mang được một giá trị nên nó phải mang
    # cái nghiêm trọng hơn; cả hai vẫn xuất hiện đầy đủ trong JSON và mỗi
    # cái có alert riêng.
    if result["stale"]:
        return EXIT_STALE
    if result["retrain_cadence"]["status"] == CADENCE_DRIFT:
        return EXIT_CADENCE_DRIFT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
