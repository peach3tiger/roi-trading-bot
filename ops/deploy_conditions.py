"""Điều kiện THỜI ĐIỂM để deploy — đo được, không theo lịch. §E.

## Vì sao không có luật "không deploy tối Thứ Sáu"

Crypto chạy 24/7. Luật đó là luật của thị trường CÓ GIỜ ĐÓNG CỬA: nó tồn
tại vì cuối tuần không có ai trực và thị trường không mở để sửa sai — chứ
không phải vì Thứ Sáu nguy hiểm. Mang nó vào đây là mang theo một giả
định đã chết, và tệ hơn: nó cho cảm giác an toàn vào Thứ Ba lúc thị
trường đang sập.

Thay bằng ba điều kiện ĐO ĐƯỢC. Hai cái đầu tự động (module này); cái thứ
ba là câu hỏi cho con người trong `ops/RUNBOOK.md`, vì nó KHÔNG đo được:

    §E.3 — "Bạn có mặt được ít nhất 2 giờ tới?"

Đó chính là điều kiện thật đằng sau luật Thứ Sáu. Một hàm không trả lời
được nó, và giả vờ trả lời được sẽ tệ hơn không hỏi.

## §E.1 — ngưỡng đến từ PHÂN VỊ, đã đo (CLAUDE.md #18)

Đo trên `tests/fixtures/btcusdt_1d_2018_2026.parquet`, 3137 bar
(2018-01-02 → 2026-08-04). `|log return|` 24 giờ, đơn vị %:

    p50   1.457
    p70   2.548
    p80   3.561   <- ngưỡng
    p90   5.285
    p95   7.197

**Tỷ lệ ngày bị chặn deploy đo được: 20.0% (628/3137).** Đó là con số bắt
buộc phải báo cáo, và ở đây nó bằng đúng định nghĩa phân vị — p80 chặn
20% theo cấu trúc. Ý nghĩa vận hành: trung bình **chờ 5 ngày** là có một
ngày deploy được.

Đánh đổi nếu muốn đổi ngưỡng: p70 chặn 30%, p90 chặn 10%. Giữ p80 theo
§E; nới lên p90 nghĩa là chấp nhận deploy vào những ngày biến động gấp
1.5 lần.

**So CÙNG KÍCH THƯỚC CỬA SỔ** (bước 1 của #18, bước hay bị bỏ nhất): giá
trị hiện tại là `|log return|` của MỘT bar, và phân phối nền cũng là
`|log return|` của TỪNG bar. So một cửa sổ 24h với độ lệch chuẩn toàn kỳ
là so hai thứ có phương sai khác nhau hàng chục lần.

## §E.2 — không lệnh chờ, không circuit breaker

Deploy giữa lúc có lệnh chưa khớp nghĩa là instance mới khởi động với một
lệnh nó không biết mình đã đặt. Circuit breaker đang hoạt động nghĩa là hệ
thống đang tự bảo vệ — thêm một biến số vào đúng lúc đó là chồng rủi ro.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# §E.1. Phân vị, KHÔNG phải một con số tuyệt đối: ngưỡng tuyệt đối sẽ sai
# dần khi chế độ biến động của thị trường đổi, còn phân vị tự hiệu chỉnh.
MAX_VOL_PERCENTILE = 80.0

# Số bar tối thiểu để phân phối nền có nghĩa. Dưới mức này, "phân vị 80"
# tính trên vài chục điểm là một con số trông có thẩm quyền mà không có.
MIN_HISTORY_BARS = 365


@dataclass(frozen=True)
class Condition:
    """Một điều kiện. `ok=None` = KHÔNG XÁC ĐỊNH ĐƯỢC — khác hẳn `False`.

    Trộn hai cái làm một là sai theo cả hai chiều: coi "không đo được" là
    "đạt" thì cổng rỗng; coi nó là "trượt" thì một sự cố đo lường chặn
    deploy vĩnh viễn. Người vận hành cần biết mình đang ở đâu.
    """

    name: str
    ok: Optional[bool]
    detail: str


@dataclass(frozen=True)
class DeployReadiness:
    conditions: tuple[Condition, ...]

    @property
    def ok(self) -> bool:
        """Chỉ `True` khi MỌI điều kiện `ok is True`. `None` KHÔNG được
        tính là đạt."""
        return all(c.ok is True for c in self.conditions)

    def render(self) -> str:
        dong = ["=" * 64, "ĐIỀU KIỆN THỜI ĐIỂM DEPLOY (§E)", "=" * 64]
        for c in self.conditions:
            dau = {True: " ĐẠT ", False: "TRƯỢT", None: " ??? "}[c.ok]
            dong.append(f"[{dau}] {c.name}")
            dong.append(f"         {c.detail}")
        dong += [
            "",
            f"KẾT LUẬN TỰ ĐỘNG: {'đủ điều kiện' if self.ok else 'CHƯA đủ điều kiện'}",
            "",
            "§E.3 KHÔNG đo được bằng máy — tự trả lời trước khi deploy:",
            "   Bạn có mặt được ít nhất 2 GIỜ tới không?",
            "   (Đây là điều kiện thật đằng sau luật 'không deploy tối Thứ Sáu'.)",
        ]
        return "\n".join(dong)


# ----------------------------------------------------------------------
# §E.1 — biến động 24h
# ----------------------------------------------------------------------


def realized_vol_24h(closes: Sequence[float]) -> Optional[float]:
    """`|log return|` của bar cuối, đơn vị %.

    `None` khi chưa đủ hai giá — "không tính được" phải khác "bằng 0", vì
    0 là giá trị BÌNH THƯỜNG NHẤT có thể và sẽ lặng lẽ cho qua cổng.
    """
    if len(closes) < 2:
        return None
    truoc, sau = float(closes[-2]), float(closes[-1])
    if truoc <= 0 or sau <= 0:
        return None
    return abs(math.log(sau / truoc)) * 100.0


def vol_history(closes: Sequence[float]) -> list[float]:
    """`|log return|` của TỪNG bar — cùng đại lượng, cùng kích thước cửa
    sổ với `realized_vol_24h()`. Đây là bước 1 của CLAUDE.md #18."""
    ra: list[float] = []
    for i in range(1, len(closes)):
        truoc, sau = float(closes[i - 1]), float(closes[i])
        if truoc > 0 and sau > 0:
            ra.append(abs(math.log(sau / truoc)) * 100.0)
    return ra


def percentile_of(value: float, history: Sequence[float]) -> float:
    """Phần trăm giá trị lịch sử NHỎ HƠN `value`."""
    if not history:
        return 0.0
    return 100.0 * sum(1 for h in history if h < value) / len(history)


def check_volatility(
    closes: Sequence[float], *, max_percentile: float = MAX_VOL_PERCENTILE
) -> Condition:
    """§E.1. Deploy giữa lúc thị trường động là tự chồng thêm biến số."""
    ten = f"Biến động 24h dưới phân vị {max_percentile:.0f}"
    lich_su = vol_history(closes)
    if len(lich_su) < MIN_HISTORY_BARS:
        return Condition(
            ten,
            None,
            f"chỉ có {len(lich_su)} bar lịch sử (< {MIN_HISTORY_BARS}) — phân vị chưa có nghĩa",
        )

    hien_tai = realized_vol_24h(closes)
    if hien_tai is None:
        return Condition(ten, None, "không tính được biến động bar cuối")

    pv = percentile_of(hien_tai, lich_su)
    nguong = sorted(lich_su)[min(int(len(lich_su) * max_percentile / 100), len(lich_su) - 1)]
    chi_tiet = (
        f"hiện tại {hien_tai:.3f}% = phân vị {pv:.1f} "
        f"(ngưỡng p{max_percentile:.0f} = {nguong:.3f}%, n={len(lich_su)} bar)"
    )
    return Condition(ten, pv <= max_percentile, chi_tiet)


# ----------------------------------------------------------------------
# §E.2 — không lệnh chờ, không circuit breaker
# ----------------------------------------------------------------------


def check_no_pending_orders(exchange_client: Any) -> Condition:
    """Deploy giữa lúc có lệnh chưa khớp nghĩa là instance MỚI khởi động
    với một lệnh nó không biết mình đã đặt.

    Lỗi mạng -> `None`, KHÔNG phải `True`. "Không hỏi được sàn" và "sàn
    trả lời không có lệnh nào" là hai chuyện khác nhau, và chỉ một trong
    hai cho phép deploy.
    """
    ten = "Không có lệnh đang chờ"
    try:
        lenh = list(exchange_client.get_open_orders())
    except Exception as exc:  # noqa: BLE001 — mọi lỗi đều là "không biết"
        return Condition(ten, None, f"không hỏi được sàn: {type(exc).__name__}: {exc}")
    if lenh:
        mo_ta = ", ".join(f"{o.order_id}({o.side})" for o in lenh[:5])
        return Condition(ten, False, f"{len(lenh)} lệnh đang chờ: {mo_ta}")
    return Condition(ten, True, "sàn báo 0 lệnh mở")


def check_no_active_breaker(risk_manager: Any) -> Condition:
    """Circuit breaker đang hoạt động nghĩa là hệ thống đang TỰ BẢO VỆ.
    Thêm một biến số vào đúng lúc đó là chồng rủi ro lên rủi ro."""
    import main as main_mod
    from core.risk_manager import BreakerLevel

    ten = "Circuit breaker không hoạt động"
    try:
        muc = main_mod.read_breaker_level(risk_manager)
    except Exception as exc:  # noqa: BLE001
        return Condition(ten, None, f"không đọc được trạng thái breaker: {exc}")
    if muc != BreakerLevel.NONE.value:
        return Condition(ten, False, f"breaker đang ở mức {muc}")
    return Condition(ten, True, "breaker ở mức NONE")


def check_no_halt_lock(risk_manager: Any) -> Condition:
    """`trading_halted.lock` tồn tại = hệ thống đã dừng vô thời hạn và
    đang chờ con người xem xét (`CLAUDE.md` §5.2). Deploy đè lên đó là bỏ
    qua đúng cơ chế đã chặn một chuỗi thua lỗ."""
    ten = "Không có trading_halted.lock"
    duong_dan = getattr(risk_manager, "_halt_lock_path", None)
    if duong_dan is None:
        return Condition(ten, None, "risk_manager không lộ đường dẫn halt lock")
    if Path(duong_dan).exists():
        return Condition(ten, False, f"{duong_dan} TỒN TẠI — đọc nội dung trước khi làm gì")
    return Condition(ten, True, f"{duong_dan} không tồn tại")


# ----------------------------------------------------------------------
# Tổng hợp
# ----------------------------------------------------------------------


def evaluate(
    *,
    closes: Sequence[float],
    exchange_client: Any = None,
    risk_manager: Any = None,
    max_percentile: float = MAX_VOL_PERCENTILE,
) -> DeployReadiness:
    dieu_kien = [check_volatility(closes, max_percentile=max_percentile)]
    if exchange_client is not None:
        dieu_kien.append(check_no_pending_orders(exchange_client))
    if risk_manager is not None:
        dieu_kien.append(check_no_active_breaker(risk_manager))
        dieu_kien.append(check_no_halt_lock(risk_manager))
    return DeployReadiness(conditions=tuple(dieu_kien))


def main(argv: Optional[Sequence[str]] = None) -> int:
    import pandas as pd

    import main as main_mod

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="đọc giá từ fixture thay vì gọi sàn (dùng khi mạng bị chặn)",
    )
    parser.add_argument("--max-percentile", type=float, default=MAX_VOL_PERCENTILE)
    args = parser.parse_args(argv)

    settings = main_mod.load_settings(args.config)
    symbol = settings["exchange"]["symbol"]

    if args.fixture is not None:
        bars = pd.read_parquet(args.fixture)
        exchange_client = None
        risk_manager = None
    else:
        from datetime import datetime, timezone

        from data.history_loader import HistoryLoader

        ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
        wf = main_mod.build_walk_forward_config(settings)
        now = datetime.now(timezone.utc)
        bars = HistoryLoader().load(
            ccxt_symbol, "1D", main_mod.resolve_data_start(args, now, settings, wf.is_bars), now
        )
        exchange_client = main_mod.build_exchange_client(settings, testnet=not args.live)
        risk_manager = main_mod.build_risk_manager(settings)

    ket_qua = evaluate(
        closes=list(bars["close"]),
        exchange_client=exchange_client,
        risk_manager=risk_manager,
        max_percentile=args.max_percentile,
    )
    print(ket_qua.render())
    return 0 if ket_qua.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
