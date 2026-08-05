"""core.trend_gate — StructuralTrendGate: trần tỷ trọng độc lập với HMM.

Giải quyết chế độ thất bại: HMM phân loại theo volatility có thể đọc một
đợt bào mòn dài, vol giảm dần (ví dụ BTC 2022) là "vol thấp" và đặt tỷ
trọng cao trong lúc giá đang xói mòn có cấu trúc. Trend gate không tạo tín
hiệu vào lệnh — nó chỉ đặt TRẦN cho tỷ trọng mà các tầng khác đề xuất.

Cố tình giữ đơn giản: hai đầu vào (price_vs_sma200, sma200_slope_30), ba
trạng thái, ba tham số. Không thêm MA khác, không ngưỡng động — giá trị của
tầng này nằm ở chỗ nó gần như không thể overfit.

Thiết kế THUẦN có chủ đích: mọi phương thức công khai tính lại toàn bộ từ
`bars` được truyền vào mỗi lần gọi, không giữ trạng thái xuyên suốt giữa
các lần gọi. Tính toán ở đây rẻ (một lượt quét tuyến tính, không phải ma
trận như HMM) nên không cần cache tăng dần — đổi lại, tính "không
look-ahead" đúng theo cấu trúc (cùng tiền tố dữ liệu luôn cho cùng kết
quả), không cần chứng minh bằng test riêng như hmm_engine.
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

    def cap_for(self, state: StructureState) -> Decimal:
        return {
            StructureState.BULL_STRUCTURE: self.cap_bull_structure,
            StructureState.TRANSITION: self.cap_transition,
            StructureState.BEAR_STRUCTURE: self.cap_bear_structure,
        }[state]


class StructuralTrendGate:
    """Độc lập hoàn toàn với HMM. Chỉ có quyền GIẢM tỷ trọng.

    Chống nhiễu: buffer 2% quanh SMA200 để tránh whipsaw crossover, xác
    nhận trạng thái mới sau 5 bar (chậm hơn bộ lọc 3 bar của HMM một cách
    có chủ đích — đây là tầng cấu trúc, nó NÊN chậm), và chỉ siết trần
    ngay lập tức nhưng chỉ nới sau khi đã xác nhận đủ (bất đối xứng có
    chủ đích).

    Cách cài đặt bất đối xứng: trần áp dụng ở mỗi bar là
    min(cap(trạng_thái_đã_xác_nhận), cap(trạng_thái_thô_bar_này)). Nếu
    trạng thái thô kéo trần XUỐNG, min() chọn nó ngay lập tức. Nếu trạng
    thái thô muốn kéo trần LÊN nhưng chưa đủ 5 bar để được xác nhận,
    min() vẫn giữ trần của trạng thái đã xác nhận (thấp hơn) — không cần
    cờ trạng thái phụ, chỉ một phép min().
    """

    def __init__(self, config: TrendGateConfig) -> None:
        self.config = config

    def get_structure_state(self, bars: pd.DataFrame) -> StructureState:
        """Trạng thái cấu trúc ĐÃ XÁC NHẬN tại bar cuối cùng của `bars`."""
        history = self.get_structure_history(bars)
        last = history["confirmed_state"].iloc[-1]
        if last is None:
            raise ValueError(
                f"Cần tối thiểu {self.config.sma_period + self.config.slope_lookback} bar "
                f"để xác định trạng thái, chỉ có {len(bars)}."
            )
        return StructureState(last)

    def get_allocation_cap(self, bars: pd.DataFrame) -> Decimal:
        """Trần tỷ trọng tại bar cuối cùng của `bars`.

        Kết hợp với các tầng khác luôn bằng hàm tối thiểu (min), không bao
        giờ tối đa (max) hay trung bình — xem CLAUDE.md bất biến #2.
        """
        history = self.get_structure_history(bars)
        last = history["cap"].iloc[-1]
        if last is None:
            raise ValueError(
                f"Cần tối thiểu {self.config.sma_period + self.config.slope_lookback} bar "
                f"để tính trần, chỉ có {len(bars)}."
            )
        return Decimal(last)

    def get_structure_history(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Tính raw_state/confirmed_state/cap cho MỌI bar trong `bars` —
        dùng để backtest toàn bộ lịch sử và làm nền cho hai phương thức
        `get_structure_state`/`get_allocation_cap` (chỉ lấy dòng cuối).
        """
        price_vs_sma = self._compute_price_vs_sma200(bars)
        slope = self._compute_sma200_slope(bars)
        buffer = float(self.config.buffer_pct)

        n = len(bars)
        price_above_col: list[object] = [None] * n
        raw_col: list[object] = [None] * n
        confirmed_col: list[object] = [None] * n
        cap_col: list[object] = [None] * n

        prev_price_above: bool | None = None
        confirmed: StructureState | None = None
        pending: StructureState | None = None
        pending_bars = 0

        for i in range(n):
            pv = price_vs_sma.iloc[i]
            sl = slope.iloc[i]
            if pd.isna(pv) or pd.isna(sl):
                continue  # chưa đủ warmup SMA200/slope — để trống, không suy đoán

            if pv > buffer:
                price_above = True
            elif pv < -buffer:
                price_above = False
            else:
                # trong dải chết ±2% — giữ nguyên trạng thái trước đó (dead
                # zone chống whipsaw); nếu đây là điểm dữ liệu hợp lệ đầu
                # tiên, dùng dấu thô làm điểm khởi đầu.
                price_above = prev_price_above if prev_price_above is not None else (pv > 0)
            prev_price_above = price_above

            slope_positive = bool(sl > 0)

            if price_above and slope_positive:
                raw = StructureState.BULL_STRUCTURE
            elif not price_above and not slope_positive:
                raw = StructureState.BEAR_STRUCTURE
            else:
                raw = StructureState.TRANSITION

            if confirmed is None:
                confirmed = raw
                pending = None
                pending_bars = 0
            elif raw == confirmed:
                pending = None
                pending_bars = 0
            else:
                if pending == raw:
                    pending_bars += 1
                else:
                    pending = raw
                    pending_bars = 1
                if pending_bars >= self.config.confirm_bars:
                    confirmed = raw
                    pending = None
                    pending_bars = 0

            cap = min(self.config.cap_for(confirmed), self.config.cap_for(raw))

            price_above_col[i] = price_above
            raw_col[i] = raw.value
            confirmed_col[i] = confirmed.value
            cap_col[i] = cap

        return pd.DataFrame(
            {
                "price_above": price_above_col,
                "raw_state": raw_col,
                "confirmed_state": confirmed_col,
                "cap": cap_col,
            },
            index=bars.index,
        )

    def _compute_price_vs_sma200(self, bars: pd.DataFrame) -> pd.Series:
        sma = bars["close"].rolling(window=self.config.sma_period, min_periods=self.config.sma_period).mean()
        return (bars["close"] - sma) / sma * 100.0

    def _compute_sma200_slope(self, bars: pd.DataFrame) -> pd.Series:
        sma = bars["close"].rolling(window=self.config.sma_period, min_periods=self.config.sma_period).mean()
        prior = sma.shift(self.config.slope_lookback)
        return (sma - prior) / prior * 100.0
