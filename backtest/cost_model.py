"""backtest.cost_model — phí + slippage tách riêng để unit test và quét tham số.

Mọi backtest bắt buộc báo cáo tổng phí đã trả theo USDT và theo % lợi
nhuận gộp — xem CLAUDE.md bất biến #7. Một backtest không tính phí không
phải kết quả xấu, nó không phải kết quả gì cả.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

# `settings.yaml` ghi phí theo ĐƠN VỊ PHẦN TRĂM (`taker_fee_pct: 0.10` nghĩa là
# 0.10%), trong khi mã giả ở spec §4.2 nhân thẳng `notional * taker_fee_pct` —
# tức giả định phân số (0.001). Trộn hai quy ước này làm phí sai 100 lần mà
# backtest vẫn chạy bình thường. Quy ước ở đây: mọi tham số `*_pct` nhận vào
# theo phần trăm, và chỉ đổi sang phân số qua `_PCT_TO_RATE`.
_PCT_TO_RATE = Decimal("100")

# Ngưỡng cảnh báo của spec §4.3: phí ăn hơn 30% lợi nhuận gộp nghĩa là chiến
# lược đang giao dịch quá nhiều.
_FEE_DRAG_WARNING_THRESHOLD_PCT = Decimal("30")

# Cột chi phí bắt buộc của `trade_log`. Khai báo ở đây để `backtester.py` và
# module này không trôi khỏi nhau khi Phase 6 viết tiếp.
TRADE_LOG_COST_COLUMNS = ("fee", "slippage_cost")


@dataclass(frozen=True)
class CostReport:
    """Kết quả của `total_cost_report`.

    Dataclass thay vì dict trần để mypy bắt được lỗi sai tên khoá, thay vì để
    một `.get("total_fee")` gõ nhầm lặng lẽ trả None vào báo cáo cuối.
    """

    total_fee_usdt: Decimal
    total_slippage_usdt: Decimal
    total_cost_usdt: Decimal
    n_rebalances: int
    gross_profit_usdt: Decimal | None
    cost_pct_of_gross_profit: Decimal | None
    exceeds_fee_drag_threshold: bool

    def as_dict(self) -> dict[str, object]:
        """Cho `cost_report.csv` và các chỗ cần dict thật (spec §4.3)."""
        return {
            "total_fee_usdt": self.total_fee_usdt,
            "total_slippage_usdt": self.total_slippage_usdt,
            "total_cost_usdt": self.total_cost_usdt,
            "n_rebalances": self.n_rebalances,
            "gross_profit_usdt": self.gross_profit_usdt,
            "cost_pct_of_gross_profit": self.cost_pct_of_gross_profit,
            "exceeds_fee_drag_threshold": self.exceeds_fee_drag_threshold,
        }


class CostModel:
    def __init__(
        self,
        taker_fee_pct: Decimal,
        maker_fee_pct: Decimal,
        slippage_pct: Decimal,
        assume_taker: bool = True,
    ) -> None:
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct
        self.slippage_pct = slippage_pct
        self.assume_taker = assume_taker

    @property
    def effective_fee_pct(self) -> Decimal:
        """`assume_taker: true` là lựa chọn bảo thủ — giả định không bao giờ
        được hưởng phí maker, kể cả khi lệnh thực tế có thể khớp maker."""
        return self.taker_fee_pct if self.assume_taker else self.maker_fee_pct

    def fee_cost(self, delta_qty: Decimal, price: Decimal) -> Decimal:
        return abs(delta_qty) * price * self.effective_fee_pct / _PCT_TO_RATE

    def slippage_cost(self, delta_qty: Decimal, price: Decimal) -> Decimal:
        return abs(delta_qty) * price * self.slippage_pct / _PCT_TO_RATE

    def rebalance_cost(self, delta_qty: Decimal, price: Decimal) -> Decimal:
        """Phí + slippage cho một lần rebalance.

        Luôn dương bất kể mua hay bán, nhờ `abs(delta_qty)`: chi phí không bao
        giờ được hoàn, và một dấu âm lọt qua đây sẽ biến chi phí thành lợi nhuận
        — sai lệch có lợi cho kết quả nên rất khó nhận ra khi đọc equity curve.
        """
        return self.fee_cost(delta_qty, price) + self.slippage_cost(delta_qty, price)

    def total_cost_report(
        self,
        trade_log: pd.DataFrame,
        gross_profit: Decimal | None = None,
    ) -> CostReport:
        """Tổng phí đã trả, % lợi nhuận gộp bị phí ăn mất.

        Nếu phí ăn hơn 30% lợi nhuận gộp, chiến lược đang giao dịch quá
        nhiều — tăng ngưỡng rebalance hoặc chuyển sang khung thời gian dài hơn.

        `gross_profit` không suy ra được từ `trade_log`: trade log ghi từng lần
        allocation thay đổi, không ghi P&L đã thực hiện. Spec §4.3 chỉ ghi chữ ký
        một tham số nên tham số thứ hai để mặc định None — gọi được đúng như
        spec, và khi thiếu thì `cost_pct_of_gross_profit` trả None thay vì một
        con số bịa (CLAUDE.md: thiếu dữ liệu thì nói ra, không đoán).
        """
        missing = [c for c in TRADE_LOG_COST_COLUMNS if c not in trade_log.columns]
        if missing:
            raise ValueError(f"trade_log thiếu cột chi phí bắt buộc: {missing}")

        total_fee = _sum_decimal(trade_log["fee"])
        total_slippage = _sum_decimal(trade_log["slippage_cost"])
        total_cost = total_fee + total_slippage

        cost_pct: Decimal | None = None
        exceeds = False
        if gross_profit is not None and gross_profit > 0:
            cost_pct = total_cost / gross_profit * _PCT_TO_RATE
            exceeds = cost_pct > _FEE_DRAG_WARNING_THRESHOLD_PCT
        elif gross_profit is not None:
            # Lợi nhuận gộp <= 0: "phí ăn bao nhiêu % lợi nhuận" không có nghĩa.
            # Trả None chứ không phải 0 hay vô cực, nhưng vẫn bật cờ cảnh báo —
            # lỗ gộp mà vẫn trả phí là trường hợp xấu nhất, không phải trường
            # hợp không xác định.
            exceeds = total_cost > 0

        return CostReport(
            total_fee_usdt=total_fee,
            total_slippage_usdt=total_slippage,
            total_cost_usdt=total_cost,
            n_rebalances=int(len(trade_log)),
            gross_profit_usdt=gross_profit,
            cost_pct_of_gross_profit=cost_pct,
            exceeds_fee_drag_threshold=exceeds,
        )


def _sum_decimal(column: pd.Series) -> Decimal:
    """Cộng trong không gian Decimal.

    `column.sum()` của pandas đi qua float64 và tích luỹ sai số trên hàng nghìn
    lần rebalance — đúng loại sai lệch mà tiêu chí nghiệm thu "tổng phí trong
    cost_report khớp tổng phí trong trade_log" tồn tại để bắt.
    """
    total = Decimal("0")
    for value in column:
        total += value if isinstance(value, Decimal) else Decimal(str(value))
    return total
