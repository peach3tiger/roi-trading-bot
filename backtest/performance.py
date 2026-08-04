"""backtest.performance — chỉ số hiệu suất, so sánh benchmark, output báo cáo.

Sharpe/Sortino annualize bằng căn bậc hai của 365, KHÔNG dùng quy ước
ngày-giao-dịch-trong-năm của equities — crypto giao dịch 365 ngày/năm,
không có ngày nghỉ thị trường (xem CLAUDE.md bất biến #9).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.backtester import BacktestResult


@dataclass(frozen=True)
class PerformanceMetrics:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float
    max_drawdown_days: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    total_trades: int
    avg_holding_period_bars: float


def compute_performance_metrics(equity_curve: pd.DataFrame) -> PerformanceMetrics:
    """Sharpe/Sortino dùng √365 — xem CLAUDE.md bất biến #9."""
    raise NotImplementedError


def compute_regime_breakdown(equity_curve: pd.DataFrame, regime_history: pd.DataFrame) -> pd.DataFrame:
    """Bảng: Regime | % Time In | Return Contribution | Avg P&L | Win Rate | Sharpe."""
    raise NotImplementedError


def compute_confidence_buckets(equity_curve: pd.DataFrame, regime_history: pd.DataFrame) -> pd.DataFrame:
    """Bucket <50%, 50-60%, 60-70%, 70%+ — nhóm confidence cao phải vượt
    trội nhóm thấp nếu HMM có giá trị thật."""
    raise NotImplementedError


def compare_buy_and_hold(equity_curve: pd.DataFrame, ohlcv: pd.DataFrame) -> dict:
    """Benchmark quan trọng nhất — không đánh bại được sau phí thì dừng."""
    raise NotImplementedError


def compare_sma200_trend(equity_curve: pd.DataFrame, ohlcv: pd.DataFrame) -> dict:
    """Long khi trên SMA200, cash khi dưới."""
    raise NotImplementedError


def compare_random_allocation(equity_curve: pd.DataFrame, ohlcv: pd.DataFrame, n_seeds: int = 100) -> dict:
    """100 seed ngẫu nhiên cùng tần suất, cùng rule sizing. Báo cáo mean/std."""
    raise NotImplementedError


def compare_static_vol_target(equity_curve: pd.DataFrame, ohlcv: pd.DataFrame) -> dict:
    """Benchmark khắt khe nhất — nhắm vol danh mục cố định bằng realized vol, không dùng HMM."""
    raise NotImplementedError


def compute_worst_case_stats(equity_curve: pd.DataFrame) -> dict:
    """Ngày/tuần/tháng tệ nhất, chuỗi thua dài nhất, thời gian dưới nước lâu nhất."""
    raise NotImplementedError


def write_reports(result: BacktestResult, output_dir: str) -> None:
    """equity_curve.csv, trade_log.csv, regime_history.csv,
    benchmark_comparison.csv, cost_report.csv + bảng rich ra terminal."""
    raise NotImplementedError
