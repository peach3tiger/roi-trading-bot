"""Dừng khẩn cấp. Phase 12d §D.

## Điểm dễ sai nhất và hậu quả nặng nhất: KHÔNG huỷ lệnh bảo vệ

"Huỷ lệnh chờ" nghe như MỘT hành động, nhưng lệnh stop-loss **cũng là lệnh
chờ**. Huỷ hết nghĩa là để lại vị thế trần trụi trong đúng tình huống khẩn
cấp — chính lúc cần bảo vệ nhất.

## SỰ THẬT VỀ HỆ THỐNG NÀY, đọc trước khi tin phần trên

Ở trạng thái hiện tại, **stop-loss KHÔNG tồn tại trên sàn**.
`broker/order_executor.py::modify_stop()` chỉ ghi vào `_current_stops`
trong bộ nhớ; enforce do chính vòng lặp bot làm, mỗi bar, bằng cách so giá
với stop rồi gọi `close_position()`. Không có lệnh `STOP_LOSS_LIMIT` nào
được gửi đi (đã kiểm: không có `stopPrice`/`STOP_LOSS` ở đâu trong
`broker/`).

Hệ quả ĐẢO NGƯỢC rủi ro mà §D.2 dự đoán:

- Rủi ro "huỷ nhầm stop" hiện là **giả định** — không có gì để huỷ nhầm.
  Phân loại vẫn được cài đặt, vì nó phải ĐÚNG SẴN vào ngày stop được đẩy
  lên sàn, và ngày đó sẽ không ai nhớ quay lại sửa file này.
- Rủi ro THẬT ngược lại: **giết bot chính là gỡ bỏ toàn bộ stop**. Sau khi
  script này chạy, vị thế còn nguyên và không còn gì canh nó.

Script in cảnh báo đó ra ở cuối. Đừng bỏ qua nó.

## Không đóng vị thế spot

Đóng trong hoảng loạn là hiện thực hoá khoản lỗ ở đúng thời điểm tệ nhất,
và nó mâu thuẫn với luận điểm của hệ thống — giảm tỷ trọng theo biến động,
không thoát sạch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "state"
SIGTERM_GRACE_SECONDS = 30.0


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR))


@dataclass(frozen=True)
class OrderPlan:
    """Kết quả PHÂN LOẠI, tách hẳn khỏi việc thực thi.

    Tách ra để `test_chi_huy_lenh_vao_giu_nguyen_stop` kiểm được quyết
    định mà không cần một sàn giả biết huỷ lệnh — và để quyết định đó đọc
    được bằng mắt trong bản in trước khi có gì bị huỷ.
    """

    to_cancel: tuple[Any, ...]
    protective: tuple[Any, ...]


def is_protective(order: Any) -> bool:
    """Lệnh này BẢO VỆ vị thế (stop-loss / take-profit / reduce-only)?

    Nhận diện theo NHIỀU dấu hiệu vì `broker/base.py::Order` chưa có
    trường `reduceOnly`, và ccxt trả cờ đó trong `raw`/`info` tuỳ sàn.
    Thiếu thông tin -> **coi là bảo vệ** (giữ lại).

    Hướng nghiêng có chủ ý: bỏ sót một lệnh vào (nó vẫn còn chờ, huỷ tay
    được) rẻ hơn vô hạn so với huỷ nhầm một lệnh stop (vị thế trần trụi
    ngay giây tiếp theo).
    """
    for ten in ("reduce_only", "reduceOnly"):
        if bool(getattr(order, ten, False)):
            return True

    loai = str(getattr(getattr(order, "order_type", None), "value", getattr(order, "order_type", "")))
    if "STOP" in loai.upper() or "TAKE_PROFIT" in loai.upper():
        return True

    raw = getattr(order, "raw", None) or getattr(order, "info", None) or {}
    if isinstance(raw, dict):
        if raw.get("reduceOnly") or raw.get("reduce_only"):
            return True
        raw_type = str(raw.get("type") or raw.get("orderType") or "").upper()
        if "STOP" in raw_type or "TAKE_PROFIT" in raw_type:
            return True
        if raw.get("stopPrice") not in (None, "", 0, "0"):
            return True

    # KHÔNG xác định được loại lệnh (không `order_type`, không `raw`/`info`)
    # -> COI LÀ BẢO VỆ. Đây là nhánh mà docstring nói tới, và bản đầu của
    # hàm này trả `False` — tức là ngược hẳn với điều nó tự khai. Test bắt
    # được ngay.
    #
    # Một `Order` bình thường của `broker/base.py` LUÔN có `order_type`
    # (LIMIT/MARKET), nên nhánh này chỉ chạm tới thứ thật sự lạ — và với
    # thứ lạ, giữ lại là hướng đúng.
    if not loai and not raw:
        return True
    return False


def classify_orders(orders: Sequence[Any]) -> OrderPlan:
    """Chia lệnh mở thành "huỷ" và "GIỮ NGUYÊN". THUẦN."""
    bao_ve = tuple(o for o in orders if is_protective(o))
    huy = tuple(o for o in orders if not is_protective(o))
    return OrderPlan(to_cancel=huy, protective=bao_ve)


def write_halt_lock(reason: str, *, path: Optional[Path] = None) -> Path:
    """`trading_halted.lock` — cùng file mà `core/risk_manager.py` và
    `ops/entrypoint.sh` đã kiểm. Xoá THỦ CÔNG."""
    target = path or (state_dir() / "trading_halted.lock")
    noi_dung = (
        f"DỪNG KHẨN CẤP\n"
        f"thời điểm : {datetime.now(timezone.utc).isoformat()}\n"
        f"lý do     : {reason}\n"
        f"bởi       : scripts/emergency_kill.py\n\n"
        "Vị thế spot KHÔNG bị đóng (§D.3). Lệnh bảo vệ KHÔNG bị huỷ (§D.2).\n"
        "Chạy scripts/recovery_checklist.py trước khi xoá file này.\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(noi_dung, encoding="utf-8")
    return target


def stop_bot(
    pid: Optional[int],
    *,
    grace_seconds: float = SIGTERM_GRACE_SECONDS,
    send: Callable[[int, int], None] = os.kill,
    alive: Optional[Callable[[int], bool]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """SIGTERM → chờ → SIGKILL. Dùng lại `monitoring/watchdog.py::terminate`
    thay vì viết bản thứ hai: hai bản của cùng một thứ tự kết thúc sẽ trôi
    lệch, và bản nào cũng có thể là bản đang chạy lúc khẩn cấp."""
    if pid is None:
        return "none"
    from monitoring.watchdog import WatchdogConfig, process_alive, terminate

    return terminate(
        pid,
        WatchdogConfig(sigterm_grace_seconds=grace_seconds),
        send=send,
        alive=alive or process_alive,
        sleep=sleep,
    )


def read_bot_pid(*, heartbeat: Optional[Path] = None) -> Optional[int]:
    from monitoring.watchdog import read_heartbeat

    hb = read_heartbeat(heartbeat or (state_dir() / "heartbeat.json"))
    return hb.pid if hb else None


@dataclass(frozen=True)
class KillSummary:
    reason: str
    cancelled: tuple[str, ...]
    cancel_failed: tuple[str, ...]
    protective_kept: tuple[str, ...]
    positions: tuple[str, ...]
    known_stops: dict[str, str]
    signal_used: str
    halt_lock: Path

    def render(self) -> str:
        dong = [
            "=" * 64,
            "DỪNG KHẨN CẤP — TÓM TẮT",
            "=" * 64,
            f"Lý do        : {self.reason}",
            f"Halt lock    : {self.halt_lock}",
            f"Tín hiệu bot : {self.signal_used}",
            "",
            f"Lệnh ĐÃ HUỶ ({len(self.cancelled)}):",
        ]
        dong += [f"  - {o}" for o in self.cancelled] or ["  (không có)"]
        if self.cancel_failed:
            dong += ["", f"Lệnh huỷ THẤT BẠI ({len(self.cancel_failed)}) — KIỂM TRA TAY TRÊN SÀN:"]
            dong += [f"  !! {o}" for o in self.cancel_failed]
        dong += ["", f"Lệnh BẢO VỆ giữ nguyên ({len(self.protective_kept)}):"]
        dong += [f"  - {o}" for o in self.protective_kept] or ["  (không có)"]
        dong += ["", f"Vị thế còn lại ({len(self.positions)}) — KHÔNG đóng (§D.3):"]
        dong += [f"  - {p}" for p in self.positions] or ["  (không có)"]
        dong += ["", "Stop đã biết (từ state_snapshot.json):"]
        dong += [f"  - {s}: {v}" for s, v in self.known_stops.items()] or ["  (không có)"]

        if self.positions:
            dong += [
                "",
                "!" * 64,
                "CẢNH BÁO: stop-loss của hệ thống này KHÔNG nằm trên sàn.",
                "`modify_stop()` chỉ ghi vào bộ nhớ tiến trình; enforce do chính vòng",
                "lặp bot làm mỗi bar. Bot vừa bị dừng, nên vị thế trên đang KHÔNG",
                "được canh bởi bất cứ thứ gì.",
                "",
                "Hoặc đặt stop THỦ CÔNG trên sàn ngay, hoặc theo dõi tay tới khi",
                "khởi động lại. Xem ops/RUNBOOK.md mục EMERGENCY_KILL.",
                "!" * 64,
            ]
        return "\n".join(dong)


def run(
    reason: str,
    *,
    exchange_client: Any,
    pid: Optional[int] = None,
    halt_lock: Optional[Path] = None,
    snapshot: Optional[Path] = None,
    stop_bot_fn: Callable[..., str] = stop_bot,
) -> KillSummary:
    """Sáu bước §D.1, theo đúng thứ tự.

    Ghi lock TRƯỚC khi huỷ lệnh: nếu script chết giữa chừng, thứ còn lại
    phải là "đã cấm giao dịch" chứ không phải "đã huỷ vài lệnh rồi thôi".
    """
    lock = write_halt_lock(reason, path=halt_lock)

    orders = list(exchange_client.get_open_orders())
    plan = classify_orders(orders)

    da_huy: list[str] = []
    that_bai: list[str] = []
    for o in plan.to_cancel:
        oid = str(getattr(o, "order_id", o))
        try:
            ok = exchange_client.cancel_order(oid)
        except Exception as exc:  # noqa: BLE001
            logger.error("Huỷ lệnh %s thất bại: %s", oid, exc)
            that_bai.append(f"{oid} ({exc})")
            continue
        (da_huy if ok else that_bai).append(oid)

    try:
        positions = [f"{p.symbol} qty={p.qty}" for p in exchange_client.get_positions()]
    except Exception as exc:  # noqa: BLE001
        logger.error("Không đọc được vị thế: %s", exc)
        positions = [f"(không đọc được: {exc})"]

    known_stops = _read_known_stops(snapshot)
    signal_used = stop_bot_fn(pid if pid is not None else read_bot_pid())

    return KillSummary(
        reason=reason,
        cancelled=tuple(da_huy),
        cancel_failed=tuple(that_bai),
        protective_kept=tuple(str(getattr(o, "order_id", o)) for o in plan.protective),
        positions=tuple(positions),
        known_stops=known_stops,
        signal_used=signal_used,
        halt_lock=lock,
    )


def _read_known_stops(snapshot: Optional[Path] = None) -> dict[str, str]:
    """Stop từ `state_snapshot.json`. KHÔNG ghi lại file — script này chỉ
    đọc trạng thái đã có; bot ghi snapshot mỗi bar và một lần nữa khi nhận
    SIGTERM, nên ghi đè từ đây chỉ có thể làm mất dữ liệu mới hơn."""
    target = snapshot or (state_dir() / "state_snapshot.json")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stop = data.get("current_stop_loss")
    if stop in (None, ""):
        return {}
    return {"(symbol từ settings)": str(Decimal(str(stop)))}


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description="Dừng khẩn cấp: cấm giao dịch, huỷ lệnh vào, dừng bot.")
    parser.add_argument("--reason", required=True, help="Lý do — ghi vào trading_halted.lock.")
    parser.add_argument("--pid", type=int, default=None, help="PID bot (mặc định: đọc heartbeat.json).")
    args = parser.parse_args(argv)

    import main as main_mod

    settings = main_mod.load_settings()
    client = main_mod.build_exchange_client(settings, testnet=settings["exchange"]["testnet"])

    tom_tat = run(args.reason, exchange_client=client, pid=args.pid)
    print(tom_tat.render())
    return 1 if tom_tat.cancel_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
