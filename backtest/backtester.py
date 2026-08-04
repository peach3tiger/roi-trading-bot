"""backtest.backtester — walk-forward, theo allocation chứ không theo từng lệnh.

Mỗi bar đặt một tỷ trọng danh mục mục tiêu dựa trên regime vol phát hiện
được, rebalance khi lệch đủ nhiều. Dùng Decimal cho toàn bộ số lượng/giá
trong đường thực thi — int() sẽ làm tròn vị thế BTC dưới một đơn vị về 0
(xem CLAUDE.md bất biến #3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd

from backtest.cost_model import CostModel
from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.trend_gate import StructuralTrendGate


@dataclass(frozen=True)
class WalkForwardConfig:
    is_bars: int = 365
    oos_bars: int = 182
    step_bars: int = 182
    fill_delay_bars: int = 1
    rebalance_threshold_pct: Decimal = Decimal("25")
    base_precision: Decimal = Decimal("0.000001")
    min_order_value_usdt: Decimal = Decimal("5")


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trade_log: pd.DataFrame
    regime_history: pd.DataFrame
    cost_report: dict
    metadata: dict = field(default_factory=dict)


class WalkForwardBacktester:
    def __init__(
        self,
        hmm_engine: HMMRegimeEngine,
        strategy_orchestrator: StrategyOrchestrator,
        trend_gate: StructuralTrendGate | None,
        cost_model: CostModel,
        config: WalkForwardConfig,
    ) -> None:
        ...

    def run(self, ohlcv: pd.DataFrame, start: datetime, end: datetime) -> BacktestResult:
        """Cửa sổ trượt IS/OOS: train HMM trên IS, đi qua OOS từng bar bằng
        filtered inference, mark-to-market bằng Decimal, rebalance khi lệch
        > ngưỡng, ghi một 'trade' mỗi lần allocation thay đổi.
        """
        raise NotImplementedError

    def _run_single_window(self, is_bars: pd.DataFrame, oos_bars: pd.DataFrame) -> BacktestResult:
        raise NotImplementedError

    def _compute_target_qty(
        self,
        equity: Decimal,
        target_allocation: Decimal,
        current_price: Decimal,
        base_precision: Decimal,
    ) -> Decimal:
        """Làm tròn XUỐNG theo base_precision — không bao giờ int()."""
        raise NotImplementedError
