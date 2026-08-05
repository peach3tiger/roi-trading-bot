"""backtest.stress_test — crash injection, gap risk, regime misclassification, exchange outage.

Không nằm trong checklist nghiệm thu của phase này (chỉ liệt kê ở "Việc
cần làm"), và một phần phụ thuộc vào `RiskManager`/`OrderExecutor` —
Phase 5 (`core/risk_manager.py`) và Phase 6 broker (`phase-09-bybit-broker.md`
trong đánh số của dự án) — chưa được implement. Các hàm ở đây đo được
những gì ĐO ĐƯỢC NGAY BÂY GIỜ (tác động giá thô lên một danh mục full
allocation) một cách trung thực; chỗ nào cần RiskManager/OrderExecutor để
xác nhận đầy đủ thì ghi rõ trong `details`, không giả vờ đã kiểm chứng.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from ta.volatility import AverageTrueRange

_RISK_MANAGER_NOTE = (
    "circuit_breaker_trigger_rate là placeholder (0.0) — RiskManager (Phase 5, "
    "core/risk_manager.py) chưa được implement nên không thể đo thật."
)
_N_MONTE_CARLO_DEFAULT = 100


@dataclass(frozen=True)
class StressTestReport:
    mean_max_loss_pct: float
    worst_case_loss_pct: float
    circuit_breaker_trigger_rate: float
    details: dict = field(default_factory=dict)


def _max_drawdown_pct(prices: np.ndarray) -> float:
    running_max = np.maximum.accumulate(prices)
    drawdown = (prices - running_max) / running_max
    return float(drawdown.min()) * 100.0


def crash_injection_test(
    ohlcv: pd.DataFrame, n_injections: int = 10, n_monte_carlo: int = 100
) -> StressTestReport:
    """Chèn gap -15% đến -40% (không phải -5%..-15% như equities) tại
    điểm ngẫu nhiên, đo max drawdown của một danh mục full allocation
    (100%) khi hứng chịu chuỗi giá đã bị sập — mô phỏng kịch bản xấu
    nhất trước khi có risk management can thiệp.
    """
    closes = ohlcv["close"].to_numpy(dtype=float)
    n = len(closes)
    max_losses = []

    for seed in range(n_monte_carlo):
        rng = np.random.default_rng(seed)
        injection_points = rng.choice(n, size=min(n_injections, n), replace=False)
        shocked = closes.copy()
        for idx in sorted(injection_points):
            gap = rng.uniform(-0.40, -0.15)
            shocked[idx:] *= 1.0 + gap
        max_losses.append(_max_drawdown_pct(shocked))

    return StressTestReport(
        mean_max_loss_pct=float(np.mean(max_losses)),
        worst_case_loss_pct=float(np.min(max_losses)),
        circuit_breaker_trigger_rate=0.0,
        details={"n_injections": n_injections, "n_monte_carlo": n_monte_carlo, "note": _RISK_MANAGER_NOTE},
    )


def gap_risk_test(
    ohlcv: pd.DataFrame, gap_multiplier_range: tuple[float, float] = (2.0, 5.0)
) -> StressTestReport:
    """Gap xảy ra trong phiên (flash crash), không phải qua đêm — kích
    thước gap tính theo bội số ATR(14) tại điểm chèn, không phải %
    tuyệt đối, để phản ánh đúng vol cục bộ tại thời điểm đó."""
    atr = AverageTrueRange(
        high=ohlcv["high"], low=ohlcv["low"], close=ohlcv["close"], window=14, fillna=False
    ).average_true_range()
    closes = ohlcv["close"].to_numpy(dtype=float)
    atr_vals = atr.to_numpy(dtype=float)
    n = len(closes)
    warmup = 14
    max_losses = []

    for seed in range(_N_MONTE_CARLO_DEFAULT):
        rng = np.random.default_rng(seed)
        idx = int(rng.integers(warmup, n))
        if np.isnan(atr_vals[idx]):
            continue
        multiplier = rng.uniform(*gap_multiplier_range)
        shocked = closes.copy()
        shocked[idx:] = np.maximum(shocked[idx:] - multiplier * atr_vals[idx], 1.0)
        max_losses.append(_max_drawdown_pct(shocked))

    if not max_losses:
        return StressTestReport(0.0, 0.0, 0.0, {"note": "không có bar nào đủ warmup ATR để chèn gap"})

    return StressTestReport(
        mean_max_loss_pct=float(np.mean(max_losses)),
        worst_case_loss_pct=float(np.min(max_losses)),
        circuit_breaker_trigger_rate=0.0,
        details={
            "gap_multiplier_range": gap_multiplier_range,
            "n_trials": len(max_losses),
            "note": _RISK_MANAGER_NOTE,
        },
    )


def regime_misclassification_test(regime_history: pd.DataFrame) -> StressTestReport:
    """Xáo trộn nhãn regime, kiểm chứng risk management vẫn giới hạn được
    thiệt hại dù regime sai hoàn toàn.

    Chưa thể kiểm chứng đầy đủ như spec mô tả — cần RiskManager (Phase 5)
    để xác nhận thiệt hại thực sự bị chặn. Ở đây đo được phần đo được:
    allocation mục tiêu thay đổi bao nhiêu khi nhãn regime bị xáo trộn
    hoàn toàn ngẫu nhiên so với thực tế — một hệ thống mà allocation phụ
    thuộc quá nhạy vào nhãn cụ thể (thay vì vol_rank) sẽ cho thấy chênh
    lệch lớn ở đây.
    """
    if "final_allocation_pct" not in regime_history.columns:
        raise ValueError("regime_history cần cột 'final_allocation_pct' (xem WalkForwardBacktester.run).")

    real_allocation = regime_history["final_allocation_pct"].astype(float)
    shuffled_allocation = real_allocation.sample(frac=1.0, random_state=0).reset_index(drop=True)
    shuffled_allocation.index = real_allocation.index

    diff_pct = (shuffled_allocation - real_allocation).abs() * 100.0

    # mean_max_loss_pct/worst_case_loss_pct không có nghĩa P&L ở test này —
    # không mô phỏng lại P&L, chỉ đo độ nhạy allocation. Để 0.0, số thật
    # nằm trong `details`, tránh nhét một con số "shift" vào field tên
    # "loss" khiến người đọc report hiểu nhầm đó là thiệt hại thực.
    return StressTestReport(
        mean_max_loss_pct=0.0,
        worst_case_loss_pct=0.0,
        circuit_breaker_trigger_rate=0.0,
        details={
            "mean_allocation_shift_pct": float(diff_pct.mean()),
            "max_allocation_shift_pct": float(diff_pct.max()),
            "note": (
                "Đo độ nhạy allocation với việc xáo trộn nhãn regime, KHÔNG PHẢI xác nhận "
                "risk management chặn thiệt hại — cần RiskManager (Phase 5) cho việc đó. "
                + _RISK_MANAGER_NOTE
            ),
        },
    )


def exchange_outage_test(outage_hours_range: tuple[int, int] = (1, 6)) -> StressTestReport:
    """Mô phỏng sàn không phản hồi giữa lúc cần rebalance — kiểm chứng
    không đặt trùng lệnh.

    Bar của hệ thống là 1D — một outage 1-6 giờ nằm dưới độ phân giải bar,
    nên không mô phỏng được ý nghĩa ở tầng backtester. Idempotency thật sự
    (orderLinkId sinh deterministic từ symbol+bar_timestamp+target_allocation)
    thuộc về `broker/order_executor.py`, chưa được implement (phase-09
    trong đánh số của dự án). Trả report placeholder, ghi rõ giới hạn thay
    vì giả vờ đã kiểm chứng.
    """
    return StressTestReport(
        mean_max_loss_pct=0.0,
        worst_case_loss_pct=0.0,
        circuit_breaker_trigger_rate=0.0,
        details={
            "outage_hours_range": outage_hours_range,
            "note": (
                "Chưa kiểm chứng được ở tầng backtester (bar 1D, dưới độ phân giải outage giờ). "
                "Idempotency thật cần broker/order_executor.py (chưa implement)."
            ),
        },
    )
