"""Đối soát sau một lần dừng bất thường. Phase 12d §E.

**CHỈ ĐỌC VÀ BÁO CÁO.** Không tự sửa gì, không tự chạy lệnh nào, không
khởi động lại bot. Ràng buộc #2 và #3 của Phase 12d.

## Mục quan trọng nhất: vị thế còn stop bảo vệ không

Sau một lần crash, kịch bản tệ nhất KHÔNG phải mất đồng bộ trạng thái —
mất đồng bộ thì đối soát lại được. Tệ nhất là **có vị thế mà không có
stop**, vì không có gì tự động phát hiện được điều đó.

Trong hệ thống này mục đó còn nặng hơn §E mô tả: `modify_stop()` chỉ ghi
stop vào BỘ NHỚ tiến trình bot, không gửi lệnh nào lên sàn (đã kiểm: không
có `stopPrice`/`STOP_LOSS` ở đâu trong `broker/`). Nên khi bot đã chết,
một vị thế "có stop trong snapshot" vẫn đang **hoàn toàn không được
canh** — snapshot chỉ nói bot ĐỊNH dùng mức nào, không nói sàn đang giữ gì.

## TIN SÀN

Khi snapshot và sàn lệch nhau, sàn đúng. Snapshot là thứ bot NGHĨ; sàn là
thứ ĐANG CÓ. Script in ra lệnh cần chạy để đồng bộ lại nhưng **không tự
chạy** — một script tự "sửa" trạng thái sau crash là script có thể biến
một sự cố đọc hiểu thành một sự cố giao dịch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = "state"

SEV_CRITICAL = "NGHIÊM TRỌNG"
SEV_WARN = "CẦN XEM"
SEV_INFO = "thông tin"


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR))


@dataclass(frozen=True)
class Finding:
    severity: str
    title: str
    detail: str
    action: str = ""


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]
    snapshot: dict[str, Any] = field(default_factory=dict)
    watchdog_kill: Optional[dict[str, Any]] = None
    exchange_positions: tuple[str, ...] = ()
    exchange_orders: tuple[str, ...] = ()
    balance: Optional[str] = None

    @property
    def critical(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == SEV_CRITICAL)

    def render(self) -> str:
        dong = ["=" * 68, "CHECKLIST KHÔI PHỤC — chỉ đọc, KHÔNG tự sửa gì", "=" * 68, ""]

        if self.watchdog_kill:
            dong += [
                "## Lần dừng gần nhất (watchdog_kill.json)",
                f"  thời điểm : {self.watchdog_kill.get('killed_at_utc')}",
                f"  lý do     : {self.watchdog_kill.get('reason')} — {self.watchdog_kill.get('detail')}",
                f"  tín hiệu  : {self.watchdog_kill.get('signal_used')}",
                "",
            ]

        dong += ["## Đối soát snapshot ↔ sàn (TIN SÀN khi lệch)", ""]
        dong += _bang(
            ("Mục", "snapshot", "sàn"),
            [
                (
                    "vị thế",
                    str(self.snapshot.get("current_allocation_pct", "—")),
                    ", ".join(self.exchange_positions) or "(không có)",
                ),
                (
                    "lệnh mở",
                    "(snapshot không lưu)",
                    ", ".join(self.exchange_orders) or "(không có)",
                ),
                ("số dư", "(snapshot không lưu)", self.balance or "—"),
                ("stop đã biết", str(self.snapshot.get("current_stop_loss", "—")), "(sàn KHÔNG giữ stop)"),
            ],
        )
        dong += [""]

        for muc in (SEV_CRITICAL, SEV_WARN, SEV_INFO):
            nhom = [f for f in self.findings if f.severity == muc]
            if not nhom:
                continue
            dong += [f"## {muc} ({len(nhom)})", ""]
            for f in nhom:
                dong += [f"  [{f.title}] {f.detail}"]
                if f.action:
                    dong += [f"      -> {f.action}"]
            dong += [""]

        dong += [
            "## Trước khi khởi động lại — xác nhận THỦ CÔNG từng mục",
            "",
            "  [ ] Đã đọc lý do dừng ở trên và hiểu nguyên nhân",
            "  [ ] Mọi mục NGHIÊM TRỌNG đã được xử lý",
            "  [ ] Vị thế trên sàn khớp với thứ mình muốn giữ",
            "  [ ] Không còn lệnh mồ côi nào trên sàn",
            "  [ ] Mọi file lock đã được điều tra rồi xoá TAY",
            "  [ ] Nếu còn vị thế: đã có stop THỦ CÔNG trên sàn, hoặc chấp nhận",
            "      chạy không stop tới khi bot lên lại",
            "",
            "Script này KHÔNG khởi động lại bot. Đó là quyết định của bạn.",
        ]
        return "\n".join(dong)


def _bang(tieu_de: Sequence[str], hang: Sequence[Sequence[str]]) -> list[str]:
    rong = [max(len(str(h[i])) for h in [tieu_de, *hang]) for i in range(len(tieu_de))]
    ra = ["  " + " | ".join(str(t).ljust(rong[i]) for i, t in enumerate(tieu_de))]
    ra.append("  " + "-+-".join("-" * r for r in rong))
    ra += ["  " + " | ".join(str(c).ljust(rong[i]) for i, c in enumerate(h)) for h in hang]
    return ra


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _dec(raw: Any) -> Optional[Decimal]:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


# ----------------------------------------------------------------------
# Các phép kiểm — THUẦN trên dữ liệu đã đọc
# ----------------------------------------------------------------------


def check_stop_protection(snapshot: dict[str, Any], positions: Sequence[Any]) -> list[Finding]:
    """§E.5 — mục ƯU TIÊN CAO NHẤT.

    Ba trạng thái, ba mức khác nhau. Gộp chúng lại là bỏ mất chính thông
    tin cần nhất: "không có vị thế" và "có vị thế không stop" đều cho
    `stop is None`.
    """
    co_vi_the = any(_dec(getattr(p, "qty", 0)) not in (None, Decimal("0")) for p in positions)
    stop = _dec(snapshot.get("current_stop_loss"))

    if not co_vi_the:
        return [Finding(SEV_INFO, "bảo vệ vị thế", "Không có vị thế nào trên sàn — không cần stop.")]

    if stop is None:
        return [
            Finding(
                SEV_CRITICAL,
                "VỊ THẾ KHÔNG CÓ STOP",
                "Sàn CÓ vị thế nhưng snapshot không ghi `current_stop_loss`. "
                "Đây là kịch bản tệ nhất sau một lần crash và không có gì tự động phát hiện được nó.",
                "Đặt stop THỦ CÔNG trên sàn NGAY, hoặc đóng vị thế bằng tay nếu không theo dõi được.",
            )
        ]

    return [
        Finding(
            SEV_CRITICAL,
            "STOP CHỈ CÓ TRONG SNAPSHOT",
            f"snapshot ghi stop {stop}, nhưng hệ thống này KHÔNG đặt stop lên sàn — "
            "`modify_stop()` chỉ ghi vào bộ nhớ tiến trình, enforce do vòng lặp bot làm mỗi bar. "
            "Bot đang không chạy, nên vị thế HIỆN KHÔNG được canh bởi bất cứ thứ gì.",
            "Đặt stop thủ công trên sàn, hoặc khởi động lại bot sớm và theo dõi tay tới lúc đó.",
        )
    ]


def check_orphan_orders(snapshot: dict[str, Any], orders: Sequence[Any]) -> list[Finding]:
    """§E.6 — lệnh trên sàn mà bot không biết.

    `state_snapshot.json` không lưu danh sách lệnh, nên "mồ côi" ở đây
    nghĩa là: có lệnh đang mở trong khi snapshot nói bot đã xử lý xong bar
    cuối. Không kết luận chắc chắn được — nên mức là CẦN XEM, không phải
    NGHIÊM TRỌNG; báo sai lên NGHIÊM TRỌNG sẽ làm mục đó mất giá.
    """
    if not orders:
        return []
    mo_ta = ", ".join(str(getattr(o, "order_id", o)) for o in orders)
    return [
        Finding(
            SEV_WARN,
            "lệnh còn mở trên sàn",
            f"{len(orders)} lệnh: {mo_ta}. snapshot ghi bar cuối = "
            f"{snapshot.get('last_processed_bar', '—')}.",
            "Đối chiếu với logs/trades.log quanh mốc đó; huỷ tay nếu là lệnh mồ côi.",
        )
    ]


def check_locks(*, base: Optional[Path] = None) -> list[Finding]:
    """§E.7 — in NỘI DUNG lock, không chỉ báo có/không. Nội dung chính là
    thứ nói vì sao nó tồn tại."""
    thu_muc = base or state_dir()
    ra: list[Finding] = []
    for ten, muc in (("trading_halted.lock", SEV_CRITICAL), ("data_quality.lock", SEV_CRITICAL)):
        f = thu_muc / ten
        if not f.exists():
            continue
        try:
            noi_dung = f.read_text(encoding="utf-8").strip()
        except OSError as exc:
            noi_dung = f"(không đọc được: {exc})"
        ra.append(
            Finding(
                muc,
                f"{ten} TỒN TẠI",
                noi_dung,
                f"Điều tra nguyên nhân rồi `rm {f}` bằng tay. Bot sẽ không khởi động khi file còn.",
            )
        )
    return ra


def check_allocation_match(snapshot: dict[str, Any], positions: Sequence[Any]) -> list[Finding]:
    """§E.4 — lệch thì TIN SÀN, in lệnh cần chạy nhưng KHÔNG chạy."""
    snap = _dec(snapshot.get("current_allocation_pct"))
    co_vi_the = any(_dec(getattr(p, "qty", 0)) not in (None, Decimal("0")) for p in positions)

    if snap is None:
        return []
    if snap > 0 and not co_vi_the:
        return [
            Finding(
                SEV_WARN,
                "snapshot nói CÓ vị thế, sàn nói KHÔNG",
                f"snapshot: allocation={snap}; sàn: không có vị thế nào.",
                "TIN SÀN. Sửa `current_allocation_pct` về 0 trong state_snapshot.json "
                "trước khi khởi động lại (KHÔNG tự sửa từ script này).",
            )
        ]
    if snap == 0 and co_vi_the:
        return [
            Finding(
                SEV_CRITICAL,
                "sàn CÓ vị thế, snapshot nói KHÔNG",
                f"snapshot: allocation=0; sàn: {len(positions)} vị thế.",
                "TIN SÀN. Bot khởi động lại sẽ coi như đang flat và có thể mua thêm chồng lên "
                "vị thế đang có. Đồng bộ snapshot TRƯỚC khi chạy lại.",
            )
        ]
    return []


# ----------------------------------------------------------------------
# Gom
# ----------------------------------------------------------------------


def build_report(
    *,
    snapshot: Optional[dict[str, Any]] = None,
    watchdog_kill: Optional[dict[str, Any]] = None,
    positions: Sequence[Any] = (),
    orders: Sequence[Any] = (),
    balance: Optional[str] = None,
    lock_dir: Optional[Path] = None,
) -> Report:
    snap = snapshot or {}
    findings: list[Finding] = []
    # Thứ tự CỐ Ý: bảo vệ vị thế trước tiên. Người đọc một báo cáo sự cố
    # đọc từ trên xuống và dừng khi thấy đủ.
    findings += check_stop_protection(snap, positions)
    findings += check_allocation_match(snap, positions)
    findings += check_orphan_orders(snap, orders)
    findings += check_locks(base=lock_dir)

    return Report(
        findings=tuple(findings),
        snapshot=snap,
        watchdog_kill=watchdog_kill,
        exchange_positions=tuple(
            f"{getattr(p, 'symbol', '?')} qty={getattr(p, 'qty', '?')}" for p in positions
        ),
        exchange_orders=tuple(str(getattr(o, "order_id", o)) for o in orders),
        balance=balance,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    argparse.ArgumentParser(description="Đối soát sau dừng bất thường — chỉ đọc.").parse_args(argv)

    import main as main_mod

    settings = main_mod.load_settings()
    sd = state_dir()
    snapshot = _read_json(sd / "state_snapshot.json") or {}
    kill = _read_json(sd / "watchdog_kill.json")

    positions: list[Any] = []
    orders: list[Any] = []
    balance: Optional[str] = None
    try:
        client = main_mod.build_exchange_client(settings, testnet=settings["exchange"]["testnet"])
        positions = list(client.get_positions())
        orders = list(client.get_open_orders())
        bal = client.get_balance()
        balance = f"{bal.total} {bal.asset} (khả dụng {bal.available})"
    except Exception as exc:  # noqa: BLE001
        logger.error("Không đọc được trạng thái sàn: %s", exc)
        balance = f"(không đọc được: {exc})"

    bao_cao = build_report(
        snapshot=snapshot, watchdog_kill=kill, positions=positions, orders=orders, balance=balance
    )
    print(bao_cao.render())
    # Thoát khác 0 khi có mục NGHIÊM TRỌNG — để một wrapper (hoặc con
    # người đọc `echo $?`) biết mà không phải đọc hết bản in.
    return 1 if bao_cao.critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
