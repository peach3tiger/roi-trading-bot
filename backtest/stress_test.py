"""backtest.stress_test — crash injection, gap risk, regime misclassification, exchange outage."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StressTestReport:
    mean_max_loss_pct: float
    worst_case_loss_pct: float
    circuit_breaker_trigger_rate: float
    details: dict


def crash_injection_test(
    ohlcv: pd.DataFrame, n_injections: int = 10, n_monte_carlo: int = 100
) -> StressTestReport:
    """Chèn gap -15% đến -40% (không phải -5%..-15% như equities) tại
    điểm ngẫu nhiên."""
    raise NotImplementedError


def gap_risk_test(
    ohlcv: pd.DataFrame, gap_multiplier_range: tuple[float, float] = (2.0, 5.0)
) -> StressTestReport:
    """Gap xảy ra trong phiên (flash crash), không phải qua đêm."""
    raise NotImplementedError


def regime_misclassification_test(regime_history: pd.DataFrame) -> StressTestReport:
    """Xáo trộn nhãn regime, kiểm chứng risk management vẫn giới hạn được thiệt hại."""
    raise NotImplementedError


def exchange_outage_test(outage_hours_range: tuple[int, int] = (1, 6)) -> StressTestReport:
    """Mô phỏng sàn không phản hồi giữa lúc cần rebalance — kiểm chứng không đặt trùng lệnh."""
    raise NotImplementedError
