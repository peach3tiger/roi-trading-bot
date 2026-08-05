"""tests.test_strategies — LowVolBull/MidVolCautious/HighVolDefensive, StrategyOrchestrator.

Implement ở Phase 4 cùng core/regime_strategies.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from core.hmm_engine import RegimeInfo, RegimeState
from core.regime_strategies import (
    Direction,
    HighVolDefensiveStrategy,
    LowVolBullStrategy,
    MidVolCautiousStrategy,
    StrategyOrchestrator,
)


def _make_bars(n: int = 100, start_price: float = 100.0, trend: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """OHLC tổng hợp đủ dài để EMA50/ATR14 khỏi NaN ở bar cuối. `trend` là
    độ trôi giá mỗi bar (0 = đi ngang, dương = tăng, âm = giảm)."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.5, n)
    close = start_price + np.cumsum(trend + noise)
    close = np.maximum(close, 1.0)  # giữ giá dương
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close - rng.uniform(-0.5, 0.5, n)
    index = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def _make_regime_state(state_id: int = 0, probability: float = 0.9, label: str = "TEST") -> RegimeState:
    return RegimeState(
        label=label,
        state_id=state_id,
        probability=probability,
        state_probabilities=np.array([probability, 1 - probability]),
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        is_confirmed=True,
        consecutive_bars=5,
    )


def _make_regime_info(regime_id: int, name: str, volatility: float, ret: float = 0.0) -> RegimeInfo:
    return RegimeInfo(
        regime_id=regime_id,
        regime_name=name,
        expected_return=ret,
        expected_volatility=volatility,
        recommended_strategy_type="placeholder",
        max_allocation_pct=1.0,
        min_confidence_to_act=0.55,
    )


def test_vol_rank_mapping_to_strategy() -> None:
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))

    assert orchestrator.select_strategy(0.0) is LowVolBullStrategy
    assert orchestrator.select_strategy(0.33) is LowVolBullStrategy
    assert orchestrator.select_strategy(0.5) is MidVolCautiousStrategy
    assert orchestrator.select_strategy(0.67) is HighVolDefensiveStrategy
    assert orchestrator.select_strategy(1.0) is HighVolDefensiveStrategy

    regime_infos = [
        _make_regime_info(0, "A", volatility=0.1),
        _make_regime_info(1, "B", volatility=0.5),
        _make_regime_info(2, "C", volatility=0.9),
    ]
    ranks = orchestrator.rank_regimes_by_volatility(regime_infos)
    assert ranks[0] == 0.0
    assert ranks[1] == 0.5
    assert ranks[2] == 1.0


def test_never_shorts() -> None:
    bars = _make_bars(n=100, trend=0.1)
    regime_state = _make_regime_state()
    for strategy_cls in (LowVolBullStrategy, MidVolCautiousStrategy, HighVolDefensiveStrategy):
        signal = strategy_cls().generate_signal("BTCUSDT", bars, regime_state)
        assert signal is not None
        assert signal.direction is Direction.LONG
        # Direction chỉ có LONG|FLAT — không có SHORT trong toàn bộ enum.
        assert {d.value for d in Direction} == {"LONG", "FLAT"}


def test_allocation_never_exceeds_100_pct() -> None:
    bars = _make_bars(n=100, trend=0.1)
    regime_state = _make_regime_state()
    for strategy_cls in (LowVolBullStrategy, MidVolCautiousStrategy, HighVolDefensiveStrategy):
        signal = strategy_cls().generate_signal("BTCUSDT", bars, regime_state)
        assert signal is not None
        assert signal.target_allocation_pct <= Decimal("1.0")

    # Đường qua orchestrator (uncertainty/rebalance) cũng không được vượt 1.0.
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))
    regime_infos = [_make_regime_info(0, "A", volatility=0.1)]
    signal = orchestrator.generate_signal(
        "BTCUSDT",
        _make_regime_state(state_id=0, probability=0.9),
        regime_infos,
        bars,
        current_allocation=Decimal("0.0"),
        is_flickering=False,
    )
    assert signal.target_allocation_pct <= Decimal("1.0")


def test_uncertainty_halves_allocation() -> None:
    bars = _make_bars(n=100, trend=0.1)
    regime_infos = [_make_regime_info(0, "A", volatility=0.1)]  # vol_rank=0 -> LowVolBull (95%)
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))

    # confidence cao, không flicker -> full 95%, không có nhánh uncertainty
    confident_signal = orchestrator.generate_signal(
        "BTCUSDT",
        _make_regime_state(state_id=0, probability=0.90),
        regime_infos,
        bars,
        current_allocation=Decimal("0.95"),  # trùng target -> tránh bị rebalance-threshold che khuất
        is_flickering=False,
    )
    assert confident_signal.target_allocation_pct == Decimal("0.95")
    assert "UNCERTAINTY" not in confident_signal.reasoning

    # confidence dưới ngưỡng 0.55 -> uncertainty -> giảm đúng một nửa
    uncertain_signal = orchestrator.generate_signal(
        "BTCUSDT",
        _make_regime_state(state_id=0, probability=0.30),
        regime_infos,
        bars,
        current_allocation=Decimal("0.475"),  # = 0.95/2 -> trùng target sau khi giảm nửa
        is_flickering=False,
    )
    assert uncertain_signal.target_allocation_pct == Decimal("0.475")
    assert uncertain_signal.target_allocation_pct == confident_signal.target_allocation_pct / Decimal("2")
    assert "[UNCERTAINTY — size halved]" in uncertain_signal.reasoning

    # is_flickering=True cũng bật uncertainty dù confidence cao
    flicker_signal = orchestrator.generate_signal(
        "BTCUSDT",
        _make_regime_state(state_id=0, probability=0.90),
        regime_infos,
        bars,
        current_allocation=Decimal("0.475"),
        is_flickering=True,
    )
    assert flicker_signal.target_allocation_pct == Decimal("0.475")
    assert "[UNCERTAINTY — size halved]" in flicker_signal.reasoning


def test_rebalance_threshold_25_pct() -> None:
    bars = _make_bars(n=100, trend=0.1)
    regime_infos = [_make_regime_info(0, "A", volatility=0.1)]  # -> LowVolBull, target 95%
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))
    regime_state = _make_regime_state(state_id=0, probability=0.90)

    # lệch 10% (< 25%) -> KHÔNG rebalance, allocation giữ nguyên current
    small_delta_signal = orchestrator.generate_signal(
        "BTCUSDT", regime_state, regime_infos, bars, current_allocation=Decimal("0.85"), is_flickering=False
    )
    assert small_delta_signal.target_allocation_pct == Decimal("0.85")

    # lệch 45% (> 25%) -> CÓ rebalance, allocation đổi sang target mới (0.95)
    large_delta_signal = orchestrator.generate_signal(
        "BTCUSDT", regime_state, regime_infos, bars, current_allocation=Decimal("0.50"), is_flickering=False
    )
    assert large_delta_signal.target_allocation_pct == Decimal("0.95")


def test_orchestrator_independent_of_return_based_labels() -> None:
    """Nhãn `BULL` (return-based, gán ở hmm_engine) không được dẫn dắt lựa
    chọn strategy — chỉ vol_rank mới quyết định. Dựng một regime tên "BULL"
    nhưng có volatility CAO NHẤT trong nhóm: orchestrator phải chọn
    HighVolDefensiveStrategy, không phải LowVolBullStrategy — nếu để nhãn
    dẫn dắt, bot sẽ all-in đúng đỉnh (đúng kịch bản EUPHORIA của crypto).
    """
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))
    regime_infos = [
        _make_regime_info(0, "BEAR", volatility=0.05, ret=-0.2),
        _make_regime_info(1, "NEUTRAL", volatility=0.15, ret=0.0),
        _make_regime_info(2, "BULL", volatility=0.90, ret=+0.3),  # tên "BULL" nhưng vol cao nhất
    ]
    ranks = orchestrator.rank_regimes_by_volatility(regime_infos)
    bull_rank = ranks[2]
    assert bull_rank == 1.0  # cao nhất trong nhóm

    strategy_for_bull = orchestrator.select_strategy(bull_rank)
    assert strategy_for_bull is HighVolDefensiveStrategy
    assert strategy_for_bull is not LowVolBullStrategy


def test_stop_loss_below_entry_for_all_strategies() -> None:
    """Không bắt buộc trong nghiệm thu, nhưng là điều kiện tối thiểu để
    stop loss có ý nghĩa — LONG-only nên stop phải luôn dưới giá vào lệnh."""
    bars = _make_bars(n=100, trend=0.1)
    entry_price = Decimal(str(bars["close"].iloc[-1]))
    for strategy_cls in (LowVolBullStrategy, MidVolCautiousStrategy, HighVolDefensiveStrategy):
        stop = strategy_cls().compute_stop_loss(bars, entry_price)
        assert stop < entry_price


def test_generate_signal_returns_none_when_indicators_not_ready() -> None:
    """Chưa đủ bar để ATR14 hết NaN (chỉ 3 bar) -> None, không bịa signal."""
    bars = _make_bars(n=3)
    regime_state = _make_regime_state()
    for strategy_cls in (LowVolBullStrategy, MidVolCautiousStrategy, HighVolDefensiveStrategy):
        assert strategy_cls().generate_signal("BTCUSDT", bars, regime_state) is None


def test_orchestrator_falls_back_gracefully_on_insufficient_data() -> None:
    bars = _make_bars(n=3)
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))
    regime_infos = [_make_regime_info(0, "A", volatility=0.1)]
    signal = orchestrator.generate_signal(
        "BTCUSDT",
        _make_regime_state(state_id=0),
        regime_infos,
        bars,
        current_allocation=Decimal("0.4"),
        is_flickering=False,
    )
    assert signal.direction is Direction.FLAT
    assert signal.target_allocation_pct == Decimal("0.4")
