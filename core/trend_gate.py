"""core.trend_gate — StructuralTrendGate: trần tỷ trọng độc lập với HMM.

Giải quyết chế độ thất bại: HMM phân loại theo volatility có thể đọc một
đợt bào mòn dài, vol giảm dần (ví dụ BTC 2022) là "vol thấp" và đặt tỷ
trọng cao trong lúc giá đang xói mòn có cấu trúc. Trend gate không tạo tín
hiệu vào lệnh — nó chỉ đặt TRẦN cho tỷ trọng mà các tầng khác đề xuất.

Cố tình giữ đơn giản: hai đầu vào (price_vs_sma200, sma200_slope_30), ba
trạng thái, ba tham số. Không thêm MA khác, không ngưỡng động — giá trị của
tầng này nằm ở chỗ nó gần như không thể overfit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

import pandas as pd


class StructureState(Enum):
    BULL_STRUCTURE = "BULL_STRUCTURE"
    TRANSITION = "TRANSITION"
    BEAR_STRUCTURE = "BEAR_STRUCTURE"


@dataclass(frozen=True)
class TrendGateConfig:
    sma_period: int = 200
    slope_lookback: int = 30
    buffer_pct: Decimal = Decimal("2.0")
    confirm_bars: int = 5
    cap_bull_structure: Decimal = Decimal("1.00")
    cap_transition: Decimal = Decimal("0.60")
    cap_bear_structure: Decimal = Decimal("0.30")


class StructuralTrendGate:
    """Độc lập hoàn toàn với HMM. Chỉ có quyền GIẢM tỷ trọng.

    Chống nhiễu: buffer 2% quanh SMA200 để tránh whipsaw crossover, xác
    nhận trạng thái mới sau 5 bar (chậm hơn bộ lọc 3 bar của HMM một cách
    có chủ đích — đây là tầng cấu trúc, nó NÊN chậm), và chỉ siết trần
    ngay lập tức nhưng chỉ nới sau khi đã xác nhận đủ (bất đối xứng có
    chủ đích).
    """

    def __init__(self, config: TrendGateConfig) -> None:
        ...

    def get_structure_state(self, bars: pd.DataFrame) -> StructureState:
        """Xác định trạng thái cấu trúc từ price_vs_sma200 và sma200_slope_30, có buffer + xác nhận N bar."""
        raise NotImplementedError

    def get_allocation_cap(self, bars: pd.DataFrame) -> Decimal:
        """Trần tỷ trọng tương ứng trạng thái hiện tại.

        Kết hợp với các tầng khác luôn bằng min(), không bao giờ max() hay
        trung bình — xem CLAUDE.md bất biến #2.
        """
        raise NotImplementedError

    def _compute_price_vs_sma200(self, bars: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def _compute_sma200_slope(self, bars: pd.DataFrame) -> pd.Series:
        raise NotImplementedError
