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
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional, cast

import pandas as pd
from ta.volatility import AverageTrueRange

from core.hmm_engine import RegimeInfo, RegimeState

_EMA_PERIOD = 40  # MUTANT
_ATR_PERIOD = 14
_UNCERTAINTY_SUFFIX = " [UNCERTAINTY — size halved]"


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


def _ema(close: pd.Series, period: int) -> pd.Series:
    """EMA — không cần min_periods, well-defined ngay từ bar đầu tiên."""
    return close.ewm(span=period, adjust=False).mean()


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    """ATR(period), thang giá thô — dùng cho stop loss, KHÔNG dùng bản đã
    chuẩn hoá của data/feature_engineering.py (bản đó chia cho close, hợp
    cho HMM chứ không cho phép tính `price - k*ATR`)."""
    return cast(
        pd.Series,
        AverageTrueRange(
            high=bars["high"], low=bars["low"], close=bars["close"], window=period, fillna=False
        ).average_true_range(),
    )


class Strategy(ABC):
    """Interface chung cho mọi lớp chiến lược theo vol rank."""

    @abstractmethod
    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, regime_state: RegimeState
    ) -> Optional[Signal]:
        """None nếu chưa đủ dữ liệu để tính chỉ báo (EMA50/ATR14 còn NaN ở
        bar cuối) — orchestrator xử lý fallback, strategy không tự bịa
        signal từ dữ liệu thiếu."""
        ...

    @abstractmethod
    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal: ...

    def _indicators_ready(self, bars: pd.DataFrame) -> bool:
        # ta.volatility.AverageTrueRange indexes atr[window - 1] không kiểm
        # tra biên — dưới window bar nó ném IndexError thay vì trả NaN, nên
        # phải chặn ở đây trước khi gọi, không dựa vào NaN-check sau đó.
        if len(bars) < _ATR_PERIOD:
            return False
        atr = _atr(bars, _ATR_PERIOD)
        return bool(pd.notna(atr.iloc[-1]))


class LowVolBullStrategy(Strategy):
    """1/3 regime vol thấp nhất. Allocation 95%, leverage 1.0x (spot).

    Stop: max(price - 3*ATR, EMA50 - 0.5*ATR).
    """

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, regime_state: RegimeState
    ) -> Optional[Signal]:
        if not self._indicators_ready(bars):
            return None

        entry_price = Decimal(str(bars["close"].iloc[-1]))
        return Signal(
            symbol=symbol,
            direction=Direction.LONG,
            confidence=regime_state.probability,
            entry_price=entry_price,
            stop_loss=self.compute_stop_loss(bars, entry_price),
            take_profit=None,
            target_allocation_pct=Decimal("0.95"),
            leverage=Decimal("1.0"),
            regime_id=regime_state.state_id,
            regime_name=regime_state.label,
            regime_probability=regime_state.probability,
            timestamp=regime_state.timestamp,
            reasoning="Vol thấp — allocation 95%.",
            strategy_name=self.__class__.__name__,
        )

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        ema_val = Decimal(str(_ema(bars["close"], _EMA_PERIOD).iloc[-1]))
        atr_val = Decimal(str(_atr(bars, _ATR_PERIOD).iloc[-1]))
        candidate_price = entry_price - Decimal("3") * atr_val
        candidate_ema = ema_val - Decimal("0.5") * atr_val
        return max(candidate_price, candidate_ema)


class MidVolCautiousStrategy(Strategy):
    """1/3 giữa. Allocation 95% nếu price > EMA50 (xu hướng còn nguyên),
    60% nếu price < EMA50 (xu hướng gãy). Stop: EMA50 - 0.5*ATR.
    """

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, regime_state: RegimeState
    ) -> Optional[Signal]:
        if not self._indicators_ready(bars):
            return None

        close = bars["close"]
        entry_price = Decimal(str(close.iloc[-1]))
        ema_last = _ema(close, _EMA_PERIOD).iloc[-1]

        if close.iloc[-1] > ema_last:
            target_allocation = Decimal("0.95")
            reasoning = "Vol trung bình, giá trên EMA50 — xu hướng còn nguyên, allocation 95%."
        else:
            target_allocation = Decimal("0.60")
            reasoning = "Vol trung bình, giá dưới EMA50 — xu hướng gãy, allocation giảm còn 60%."

        return Signal(
            symbol=symbol,
            direction=Direction.LONG,
            confidence=regime_state.probability,
            entry_price=entry_price,
            stop_loss=self.compute_stop_loss(bars, entry_price),
            take_profit=None,
            target_allocation_pct=target_allocation,
            leverage=Decimal("1.0"),
            regime_id=regime_state.state_id,
            regime_name=regime_state.label,
            regime_probability=regime_state.probability,
            timestamp=regime_state.timestamp,
            reasoning=reasoning,
            strategy_name=self.__class__.__name__,
        )

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        ema_val = Decimal(str(_ema(bars["close"], _EMA_PERIOD).iloc[-1]))
        atr_val = Decimal(str(_atr(bars, _ATR_PERIOD).iloc[-1]))
        return ema_val - Decimal("0.5") * atr_val


class HighVolDefensiveStrategy(Strategy):
    """1/3 vol cao nhất. LONG, KHÔNG short. Allocation 50% — thấp hơn mức
    60% của equities vì đuôi phân phối crypto dày hơn nhiều.
    Stop: EMA50 - 1.0*ATR (rộng hơn cho điều kiện biến động).
    """

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, regime_state: RegimeState
    ) -> Optional[Signal]:
        if not self._indicators_ready(bars):
            return None

        entry_price = Decimal(str(bars["close"].iloc[-1]))
        return Signal(
            symbol=symbol,
            direction=Direction.LONG,
            confidence=regime_state.probability,
            entry_price=entry_price,
            stop_loss=self.compute_stop_loss(bars, entry_price),
            take_profit=None,
            target_allocation_pct=Decimal("0.50"),
            leverage=Decimal("1.0"),
            regime_id=regime_state.state_id,
            regime_name=regime_state.label,
            regime_probability=regime_state.probability,
            timestamp=regime_state.timestamp,
            reasoning="Vol cao — phòng thủ, allocation 50%, KHÔNG đảo chiều short.",
            strategy_name=self.__class__.__name__,
        )

    def compute_stop_loss(self, bars: pd.DataFrame, entry_price: Decimal) -> Decimal:
        ema_val = Decimal(str(_ema(bars["close"], _EMA_PERIOD).iloc[-1]))
        atr_val = Decimal(str(_atr(bars, _ATR_PERIOD).iloc[-1]))
        return ema_val - atr_val


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
    """Ánh xạ regime_id → vol_rank → strategy class, quản lý confidence/uncertainty và rebalance threshold.

    `rebalance_threshold_pct` nhận giá trị THÔ theo đơn vị settings.yaml
    (vd. `Decimal("25")` cho 25%), không phải phân số — tự chia 100 nội bộ
    khi so sánh với `target_allocation_pct` (vốn ở dạng phân số 0..1).
    """

    def __init__(
        self,
        min_confidence: float,
        rebalance_threshold_pct: Decimal,
        uncertainty_mode: Literal["halve", "hold_previous", "none"] = "halve",
    ) -> None:
        self.min_confidence = min_confidence
        self.rebalance_threshold_pct = rebalance_threshold_pct
        # "halve" la hanh vi mac dinh/lich su (khong doi hanh vi cho moi caller
        # cu). "hold_previous" va "none" chi ton tai de kiem chung mot gia
        # thuyet cu the (xem docs/DECISIONS.md, thi nghiem uncertainty-mode
        # 2026-08-05) — khong phai tham so du dinh dua vao settings.yaml de
        # quet.
        self.uncertainty_mode = uncertainty_mode

    def rank_regimes_by_volatility(self, regime_infos: list[RegimeInfo]) -> dict[int, float]:
        """Sắp regime_infos theo expected_volatility tăng dần, trả position trong [0,1].

        Phép sắp này ĐỘC LẬP hoàn toàn với phép sắp theo return dùng để gán
        nhãn ở hmm_engine — orchestrator bỏ qua nhãn khi ra quyết định.
        """
        ordered = sorted(regime_infos, key=lambda info: info.expected_volatility)
        n = len(ordered)
        if n <= 1:
            return {info.regime_id: 0.0 for info in ordered}
        return {info.regime_id: rank / (n - 1) for rank, info in enumerate(ordered)}

    def select_strategy(self, vol_rank_position: float) -> type[Strategy]:
        """position <= 0.33 → LowVolBull; >= 0.67 → HighVolDefensive; else MidVolCautious."""
        if vol_rank_position <= 0.33:
            return LowVolBullStrategy
        if vol_rank_position >= 0.67:
            return HighVolDefensiveStrategy
        return MidVolCautiousStrategy

    def generate_signal(
        self,
        symbol: str,
        regime_state: RegimeState,
        regime_infos: list[RegimeInfo],
        bars: pd.DataFrame,
        current_allocation: Decimal,
        is_flickering: bool,
    ) -> Signal:
        """Sinh signal cuối cùng, áp dụng uncertainty (giảm nửa allocation
        khi confidence < ngưỡng hoặc đang flicker) và rebalance threshold.
        """
        vol_ranks = self.rank_regimes_by_volatility(regime_infos)
        # Không tìm thấy regime_id trong bảng vol_rank (không nên xảy ra khi
        # regime_infos khớp với model đang chạy) — coi như giữa dải, phòng
        # thủ thay vì crash.
        position = vol_ranks.get(regime_state.state_id, 0.5)
        strategy = self.select_strategy(position)()

        signal = strategy.generate_signal(symbol, bars, regime_state)
        if signal is None:
            return Signal(
                symbol=symbol,
                direction=Direction.FLAT,
                confidence=regime_state.probability,
                entry_price=Decimal(str(bars["close"].iloc[-1])) if not bars.empty else Decimal("0"),
                stop_loss=Decimal(str(bars["close"].iloc[-1])) if not bars.empty else Decimal("0"),
                take_profit=None,
                target_allocation_pct=current_allocation,
                leverage=Decimal("1.0"),
                regime_id=regime_state.state_id,
                regime_name=regime_state.label,
                regime_probability=regime_state.probability,
                timestamp=regime_state.timestamp,
                reasoning="Không đủ dữ liệu để tính chỉ báo (EMA50/ATR14) — giữ nguyên allocation.",
                strategy_name="StrategyOrchestrator(insufficient_data)",
            )

        is_uncertain = regime_state.probability < self.min_confidence or is_flickering
        if is_uncertain and self.uncertainty_mode == "halve":
            signal = replace(
                signal,
                target_allocation_pct=signal.target_allocation_pct / Decimal("2"),
                reasoning=signal.reasoning + _UNCERTAINTY_SUFFIX,
            )
        elif is_uncertain and self.uncertainty_mode == "hold_previous":
            signal = replace(
                signal,
                target_allocation_pct=current_allocation,
                reasoning=signal.reasoning + " [UNCERTAINTY — held previous bar's allocation]",
            )
        # "none": bo hoan toan buoc nay, target_allocation_pct giu nguyen gia
        # tri strategy con lai da tinh, khong phan biet uncertain/confident.

        threshold_fraction = self.rebalance_threshold_pct / Decimal("100")
        delta = abs(signal.target_allocation_pct - current_allocation)
        if delta < threshold_fraction:
            signal = replace(
                signal,
                target_allocation_pct=current_allocation,
                reasoning=(
                    f"{signal.reasoning} [lệch {delta:.4f} dưới ngưỡng rebalance "
                    f"{threshold_fraction:.4f} — giữ nguyên allocation]"
                ),
            )

        return signal
