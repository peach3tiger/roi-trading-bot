"""core.regime_strategies — ánh xạ vol rank sang chiến lược phân bổ.

LUÔN LONG. KHÔNG BAO GIỜ SHORT — phục hồi hình chữ V của crypto nhanh và
bạo lực hơn equities, HMM luôn chậm vài bar; phản ứng đúng với vol cao là
giảm tỷ trọng, không phải đảo chiều.

Ánh xạ vol_rank → strategy ĐỘC LẬP HOÀN TOÀN với nhãn regime (BULL/BEAR/...).
Nhãn sắp theo return, vol_rank sắp theo volatility — hai phép sắp không liên
quan. Với crypto, EUPHORIA thường là regime vol CAO NHẤT; để nhãn dẫn dắt
strategy sẽ khiến bot all-in đúng đỉnh.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

import pandas as pd

from core.hmm_engine import RegimeInfo, RegimeState


class Direction(str, Enum):
    LONG = "LONG"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: Direction
    confidence: float
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    target_allocation_pct: Decimal
    leverage: Decimal
    regime_id: int
    regime_name: str
    regime_probability: float
    timestamp: datetime
    reasoning: str
    strategy_name: str
    metadata: dict = field(default_factory=dict)


class Strategy(ABC):
    """Interface chung cho mọi lớp chiến lược theo vol rank."""

    @abstractmethod
    def generate_signal(
        self,
        regime_state: RegimeState,
        regime_info: RegimeInfo,
        bars: pd.DataFrame,
        current_allocation: Decimal,
    ) -> Signal: ...

    @abstractmethod
    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal: ...


class LowVolBullStrategy(Strategy):
    """1/3 regime vol thấp nhất. Allocation 95%, leverage 1.0x (spot).

    Stop: max(price - 3*ATR, EMA50 - 0.5*ATR).
    """

    def generate_signal(
        self,
        regime_state: RegimeState,
        regime_info: RegimeInfo,
        bars: pd.DataFrame,
        current_allocation: Decimal,
    ) -> Signal:
        raise NotImplementedError

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        raise NotImplementedError


class MidVolCautiousStrategy(Strategy):
    """1/3 giữa. Allocation 95% nếu price > EMA50 (xu hướng còn nguyên),
    60% nếu price < EMA50 (xu hướng gãy). Stop: EMA50 - 0.5*ATR.
    """

    def generate_signal(
        self,
        regime_state: RegimeState,
        regime_info: RegimeInfo,
        bars: pd.DataFrame,
        current_allocation: Decimal,
    ) -> Signal:
        raise NotImplementedError

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        raise NotImplementedError


class HighVolDefensiveStrategy(Strategy):
    """1/3 vol cao nhất. LONG, KHÔNG short. Allocation 50% — thấp hơn mức
    60% của equities vì đuôi phân phối crypto dày hơn nhiều.
    Stop: EMA50 - 1.0*ATR (rộng hơn cho điều kiện biến động).
    """

    def generate_signal(
        self,
        regime_state: RegimeState,
        regime_info: RegimeInfo,
        bars: pd.DataFrame,
        current_allocation: Decimal,
    ) -> Signal:
        raise NotImplementedError

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        raise NotImplementedError


# Alias tương thích ngược — giữ để không phá vỡ import cũ tham chiếu tên
# theo nhãn return-based thay vì vol rank.
CrashDefensiveStrategy = HighVolDefensiveStrategy
BearTrendStrategy = HighVolDefensiveStrategy
MeanReversionStrategy = MidVolCautiousStrategy
BullTrendStrategy = LowVolBullStrategy
EuphoriaCautiousStrategy = LowVolBullStrategy

LABEL_TO_STRATEGY: dict[str, type[Strategy]] = {
    "CRASH": CrashDefensiveStrategy,
    "STRONG_BEAR": BearTrendStrategy,
    "BEAR": BearTrendStrategy,
    "WEAK_BEAR": MeanReversionStrategy,
    "NEUTRAL": MeanReversionStrategy,
    "WEAK_BULL": MeanReversionStrategy,
    "BULL": BullTrendStrategy,
    "STRONG_BULL": BullTrendStrategy,
    "EUPHORIA": EuphoriaCautiousStrategy,
}


class StrategyOrchestrator:
    """Ánh xạ regime_id → vol_rank → strategy class, quản lý confidence/uncertainty và rebalance threshold."""

    def __init__(self, min_confidence: float, rebalance_threshold_pct: Decimal) -> None:
        ...

    def rank_regimes_by_volatility(self, regime_infos: list[RegimeInfo]) -> dict[int, float]:
        """Sắp regime_infos theo expected_volatility tăng dần, trả position trong [0,1].

        Phép sắp này ĐỘC LẬP hoàn toàn với phép sắp theo return dùng để gán
        nhãn ở hmm_engine — orchestrator bỏ qua nhãn khi ra quyết định.
        """
        raise NotImplementedError

    def select_strategy(self, vol_rank_position: float) -> type[Strategy]:
        """position <= 0.33 → LowVolBull; >= 0.67 → HighVolDefensive; else MidVolCautious."""
        raise NotImplementedError

    def generate_signal(
        self,
        regime_state: RegimeState,
        regime_infos: list[RegimeInfo],
        bars: pd.DataFrame,
        current_allocation: Decimal,
        is_flickering: bool,
    ) -> Signal:
        """Sinh signal cuối cùng, áp dụng uncertainty (giảm nửa allocation
        khi confidence < ngưỡng hoặc đang flicker) và rebalance threshold.
        """
        raise NotImplementedError
