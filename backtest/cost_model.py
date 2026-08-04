"""backtest.cost_model — phí + slippage tách riêng để unit test và quét tham số.

Mọi backtest bắt buộc báo cáo tổng phí đã trả theo USDT và theo % lợi
nhuận gộp — xem CLAUDE.md bất biến #7. Một backtest không tính phí không
phải kết quả xấu, nó không phải kết quả gì cả.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd


class CostModel:
    def __init__(
        self,
        taker_fee_pct: Decimal,
        maker_fee_pct: Decimal,
        slippage_pct: Decimal,
        assume_taker: bool = True,
    ) -> None:
        ...

    def rebalance_cost(self, delta_qty: Decimal, price: Decimal) -> Decimal:
        """Phí + slippage cho một lần rebalance."""
        raise NotImplementedError

    def total_cost_report(self, trade_log: pd.DataFrame) -> dict:
        """Tổng phí đã trả, % lợi nhuận gộp bị phí ăn mất.

        Nếu phí ăn hơn 30% lợi nhuận gộp, chiến lược đang giao dịch quá
        nhiều — tăng ngưỡng rebalance hoặc chuyển sang khung thời gian dài hơn.
        """
        raise NotImplementedError
