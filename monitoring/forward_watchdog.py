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
) -> dict[str, Any]:
    freshness = inspect_log(path, today_utc)
    message = build_alert_message(freshness, max_staleness_days)

    from monitoring.alerts import Alert, AlertManager, AlertType

    # Dựng AlertManager NGAY CẢ KHI log khoẻ, chỉ để biết kênh có gửi được
    # không. Nếu chỉ kiểm lúc có sự cố thì phát hiện "Telegram chưa cấu
    # hình" đúng vào hôm cần nó nhất — tức là không phát hiện gì cả.
    # `telegram_configured` xuất hiện trong watchdog.out.log MỖI NGÀY.
    load_dotenv()
    manager = AlertManager()
    telegram_ok = bool(manager.telegram_bot_token and manager.telegram_chat_id)

    result: dict[str, Any] = {
        "stale": message is not None,
        "message": message,
        "alert_sent": False,
        "telegram_configured": telegram_ok,
        **freshness.as_dict(),
    }

    if not telegram_ok:
        logger.warning(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID rỗng hoặc thiếu trong .env — "
            "watchdog chỉ ghi được ra console/log, KHÔNG có cảnh báo nào tới "
            "điện thoại. Đây là điểm mù: thí nghiệm có thể dừng mà không ai hay."
        )

    if message is None or not send:
        return result

    result["alert_sent"] = manager.send(
        Alert(
            alert_type=AlertType.FORWARD_LOG_STALE,
            message=message,
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
    return EXIT_STALE if result["stale"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
