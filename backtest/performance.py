"""backtest.performance — chỉ số hiệu suất, so sánh benchmark, output báo cáo.

Sharpe/Sortino annualize bằng căn bậc hai của 365, KHÔNG dùng quy ước
ngày-giao-dịch-trong-năm của equities — crypto giao dịch 365 ngày/năm,
không có ngày nghỉ thị trường (xem CLAUDE.md bất biến #9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from backtest.backtester import BacktestResult
from backtest.cost_model import CostModel
from broker.instrument_rules import InstrumentRules

_ANNUALIZATION = math.sqrt(365)
_CONFIDENCE_BUCKETS = [(0.0, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]
_CONFIDENCE_BUCKET_LABELS = ["<50%", "50-60%", "60-70%", "70%+"]


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


def _basic_metrics(equity: pd.Series) -> dict:
    """Return/CAGR/Sharpe/Sortino/Calmar/max_drawdown từ một chuỗi equity —
    dùng chung cho cả báo cáo chính lẫn 4 benchmark, không cần trade_log.
    """
    equity = equity.astype(float)
    returns = equity.pct_change().dropna()
    n_bars = len(equity)

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1) if equity.iloc[0] != 0 else 0.0
    n_years = n_bars / 365.0
    cagr = (
        float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0)
        if n_years > 0 and equity.iloc[0] > 0
        else 0.0
    )

    sharpe = float(returns.mean() / returns.std() * _ANNUALIZATION) if returns.std() > 0 else 0.0
    downside = returns[returns < 0]
    sortino = (
        float(returns.mean() / downside.std() * _ANNUALIZATION)
        if len(downside) > 1 and downside.std() > 0
        else 0.0
    )

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max.replace(0, np.nan)
    max_dd_pct = float(drawdown.min()) if not drawdown.empty else 0.0
    max_dd_days = _longest_true_streak(drawdown < 0)

    calmar = float(cagr / abs(max_dd_pct)) if max_dd_pct != 0 else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_days": max_dd_days,
    }


def _longest_true_streak(mask: pd.Series) -> int:
    longest = 0
    current = 0
    for value in mask:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _segment_pnl(equity_curve: pd.DataFrame, trade_log: pd.DataFrame) -> list[dict]:
    """P&L của từng đoạn giữ vị thế giữa hai lần rebalance liên tiếp — cơ sở
    cho win_rate/avg_win/avg_loss/profit_factor. Backtest này theo allocation
    chứ không theo lệnh vào/ra riêng lẻ, nên "một giao dịch" ở đây là một
    đoạn thời gian giữa hai lần thay đổi allocation.
    """
    if trade_log.empty:
        return []

    boundaries = list(trade_log.index) + [equity_curve.index[-1]]
    segments = []
    for i in range(len(boundaries) - 1):
        t0, t1 = boundaries[i], boundaries[i + 1]
        if t0 not in equity_curve.index or t1 not in equity_curve.index:
            continue
        qty_held = float(equity_curve.loc[t0, "qty"])
        price0 = float(equity_curve.loc[t0, "price"])
        price1 = float(equity_curve.loc[t1, "price"])
        pnl = qty_held * (price1 - price0)
        holding_bars = cast(int, equity_curve.index.get_loc(t1)) - cast(int, equity_curve.index.get_loc(t0))
        segments.append({"pnl": pnl, "holding_bars": holding_bars})
    return segments


def compute_performance_metrics(equity_curve: pd.DataFrame, trade_log: pd.DataFrame) -> PerformanceMetrics:
    """Sharpe/Sortino dùng √365 — xem CLAUDE.md bất biến #9.

    Nhận thêm `trade_log` so với chữ ký gốc của stub: win_rate/avg_win/
    avg_loss/profit_factor/total_trades/avg_holding_period cần dữ liệu giao
    dịch, không suy ra được chỉ từ equity_curve.
    """
    basic = _basic_metrics(equity_curve["equity"])
    segments = _segment_pnl(equity_curve, trade_log)

    wins = [s["pnl"] for s in segments if s["pnl"] > 0]
    losses = [s["pnl"] for s in segments if s["pnl"] < 0]
    win_rate = len(wins) / len(segments) if segments else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0.0
    avg_holding = float(np.mean([s["holding_bars"] for s in segments])) if segments else 0.0

    return PerformanceMetrics(
        total_return=basic["total_return"],
        cagr=basic["cagr"],
        sharpe=basic["sharpe"],
        sortino=basic["sortino"],
        calmar=basic["calmar"],
        max_drawdown_pct=basic["max_drawdown_pct"],
        max_drawdown_days=basic["max_drawdown_days"],
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        total_trades=len(trade_log),
        avg_holding_period_bars=avg_holding,
    )


def compute_regime_breakdown(equity_curve: pd.DataFrame, regime_history: pd.DataFrame) -> pd.DataFrame:
    """Bảng: Regime | % Time In | Return Contribution | Avg P&L | Win Rate | Sharpe."""
    returns = equity_curve["equity"].astype(float).pct_change().rename("daily_return")
    joined = regime_history.join(returns, how="inner").dropna(subset=["daily_return"])
    total_bars = len(joined)

    rows = []
    for label, group in joined.groupby("regime_label"):
        daily = group["daily_return"]
        std = daily.std()
        rows.append(
            {
                "regime": label,
                "pct_time_in": len(group) / total_bars * 100 if total_bars else 0.0,
                "return_contribution_pct": daily.sum() * 100,
                "avg_pnl_pct": daily.mean() * 100 if len(daily) else 0.0,
                "win_rate_pct": (daily > 0).mean() * 100 if len(daily) else 0.0,
                "sharpe": float(daily.mean() / std * _ANNUALIZATION) if std and std > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows).set_index("regime").sort_values("pct_time_in", ascending=False)


def compute_confidence_buckets(equity_curve: pd.DataFrame, regime_history: pd.DataFrame) -> pd.DataFrame:
    """Bucket <50%, 50-60%, 60-70%, 70%+ — nhóm confidence cao phải vượt
    trội nhóm thấp nếu HMM có giá trị thật."""
    returns = equity_curve["equity"].astype(float).pct_change().rename("daily_return")
    joined = regime_history.join(returns, how="inner").dropna(subset=["daily_return"])

    rows = []
    for (lo, hi), label in zip(_CONFIDENCE_BUCKETS, _CONFIDENCE_BUCKET_LABELS):
        mask = (joined["regime_probability"] >= lo) & (joined["regime_probability"] < hi)
        bucket = joined.loc[mask, "daily_return"]
        std = bucket.std()
        rows.append(
            {
                "confidence_bucket": label,
                "n_bars": len(bucket),
                "avg_pnl_pct": bucket.mean() * 100 if len(bucket) else 0.0,
                "win_rate_pct": (bucket > 0).mean() * 100 if len(bucket) else 0.0,
                "sharpe": float(bucket.mean() / std * _ANNUALIZATION) if std and std > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows).set_index("confidence_bucket")


def _simulate_target_allocation_series(
    ohlcv: pd.DataFrame,
    target_series: pd.Series,
    cost_model: CostModel,
    instrument_rules: InstrumentRules,
    rebalance_threshold_pct: Decimal,
    initial_equity: Decimal,
    fill_delay_bars: int = 1,
) -> pd.DataFrame:
    """Mô phỏng một chuỗi allocation mục tiêu bất kỳ (không qua HMM) bằng
    ĐÚNG cơ chế thực thi của WalkForwardBacktester — Decimal/ROUND_DOWN,
    cùng cost model, cùng fill delay, cùng ngưỡng rebalance — để 4 benchmark
    so sánh công bằng với chiến lược thật, không phải so với một mô phỏng
    thực thi dễ dãi hơn.
    """
    threshold_fraction = rebalance_threshold_pct / Decimal("100")
    cash = initial_equity
    qty = Decimal("0")
    current_allocation = Decimal("0")
    pending: tuple[pd.Timestamp, Decimal] | None = None
    rows = []

    index = target_series.index
    for i, ts in enumerate(index):
        open_price = Decimal(str(ohlcv.loc[ts, "open"]))
        close_price = Decimal(str(ohlcv.loc[ts, "close"]))

        if pending is not None and pending[0] == ts:
            target_allocation = pending[1]
            equity = cash + qty * open_price
            target_notional = equity * target_allocation
            target_qty = instrument_rules.round_qty(target_notional / open_price)
            delta = target_qty - qty
            notional = abs(delta) * open_price
            if delta != 0 and notional >= instrument_rules.min_order_amt:
                cost = cost_model.rebalance_cost(delta, open_price)
                cash -= delta * open_price + cost
                qty += delta
            pending = None

        equity_now = cash + qty * close_price
        current_allocation = (qty * close_price / equity_now) if equity_now > 0 else Decimal("0")

        raw_target = Decimal(str(target_series.loc[ts])).quantize(Decimal("0.0001"))
        if abs(raw_target - current_allocation) >= threshold_fraction:
            execute_idx = i + fill_delay_bars
            if execute_idx < len(index):
                pending = (index[execute_idx], raw_target)

        rows.append({"timestamp": ts, "cash": cash, "qty": qty, "price": close_price, "equity": equity_now})

    return pd.DataFrame(rows).set_index("timestamp")


def compare_buy_and_hold(
    equity_curve: pd.DataFrame,
    ohlcv: pd.DataFrame,
    cost_model: CostModel,
    initial_equity: Decimal = Decimal("10000"),
) -> dict:
    """Benchmark quan trọng nhất — không đánh bại được sau phí thì dừng."""
    prices = ohlcv.loc[equity_curve.index, "close"]
    entry_price = Decimal(str(prices.iloc[0]))
    qty = initial_equity / entry_price
    cost = cost_model.rebalance_cost(qty, entry_price)
    cash = initial_equity - qty * entry_price - cost
    equity = float(cash) + float(qty) * prices.astype(float)
    return _basic_metrics(equity)


def compare_sma200_trend(
    equity_curve: pd.DataFrame,
    ohlcv: pd.DataFrame,
    cost_model: CostModel,
    instrument_rules: InstrumentRules,
    rebalance_threshold_pct: Decimal = Decimal("25"),
    initial_equity: Decimal = Decimal("10000"),
) -> dict:
    """Long khi trên SMA200, cash khi dưới."""
    closes = ohlcv.loc[equity_curve.index, "close"]
    sma200 = ohlcv["close"].rolling(200, min_periods=200).mean().loc[equity_curve.index]
    target = (closes > sma200).astype(float).fillna(0.0)
    sim = _simulate_target_allocation_series(
        ohlcv, target, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
    )
    return _basic_metrics(sim["equity"])


def compare_random_allocation(
    equity_curve: pd.DataFrame,
    ohlcv: pd.DataFrame,
    cost_model: CostModel,
    instrument_rules: InstrumentRules,
    rebalance_threshold_pct: Decimal = Decimal("25"),
    initial_equity: Decimal = Decimal("10000"),
    n_seeds: int = 100,
) -> dict:
    """100 seed ngẫu nhiên cùng tần suất, cùng rule sizing. Báo cáo mean/std."""
    choices = [0.50, 0.60, 0.95]
    metrics_per_seed = []
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        target = pd.Series(rng.choice(choices, size=len(equity_curve)), index=equity_curve.index)
        sim = _simulate_target_allocation_series(
            ohlcv, target, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
        )
        metrics_per_seed.append(_basic_metrics(sim["equity"]))

    df = pd.DataFrame(metrics_per_seed)
    result: dict = {}
    for col in df.columns:
        result[f"{col}_mean"] = float(df[col].mean())
        result[f"{col}_std"] = float(df[col].std())
    return result


def compare_static_vol_target(
    equity_curve: pd.DataFrame,
    ohlcv: pd.DataFrame,
    cost_model: CostModel,
    instrument_rules: InstrumentRules,
    rebalance_threshold_pct: Decimal = Decimal("25"),
    initial_equity: Decimal = Decimal("10000"),
    target_daily_vol: float = 0.02,
) -> dict:
    """Benchmark khắt khe nhất — nhắm vol danh mục cố định bằng realized vol,
    không dùng HMM. `target_daily_vol=0.02` (≈38%/năm) là mặc định hợp lý,
    không phải hiệu chỉnh chính xác — bản thân sự tồn tại của benchmark này
    mới là bài kiểm tra thật (spec: nếu HMM không đánh bại nổi, cả tầng HMM
    là phức tạp thừa).
    """
    returns = ohlcv["close"].pct_change()
    realized_vol = returns.rolling(20, min_periods=20).std()
    target = (target_daily_vol / realized_vol).clip(upper=1.0).clip(lower=0.0)
    target = target.loc[equity_curve.index].fillna(0.0)
    sim = _simulate_target_allocation_series(
        ohlcv, target, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
    )
    return _basic_metrics(sim["equity"])


def compute_worst_case_stats(equity_curve: pd.DataFrame) -> dict:
    """Ngày/tuần/tháng tệ nhất, chuỗi thua dài nhất, thời gian dưới nước lâu nhất."""
    equity = equity_curve["equity"].astype(float)
    daily_returns = equity.pct_change().dropna()
    weekly_returns = equity.pct_change(7).dropna()
    monthly_returns = equity.pct_change(30).dropna()

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max.replace(0, np.nan)

    return {
        "worst_day_pct": float(daily_returns.min()) * 100 if len(daily_returns) else 0.0,
        "worst_week_pct": float(weekly_returns.min()) * 100 if len(weekly_returns) else 0.0,
        "worst_month_pct": float(monthly_returns.min()) * 100 if len(monthly_returns) else 0.0,
        "longest_losing_streak_days": _longest_true_streak(daily_returns < 0),
        "longest_underwater_days": _longest_true_streak(drawdown < 0),
    }


def write_reports(
    result: BacktestResult,
    ohlcv: pd.DataFrame,
    cost_model: CostModel,
    instrument_rules: InstrumentRules,
    output_dir: str,
) -> dict:
    """equity_curve.csv, trade_log.csv, regime_history.csv,
    benchmark_comparison.csv, cost_report.csv + bảng rich ra terminal.

    Nhận thêm `ohlcv`/`cost_model`/`instrument_rules` so với chữ ký gốc của
    stub: tính 4 benchmark cần dữ liệu giá thô và cùng cost model với
    chiến lược thật, `BacktestResult` một mình không đủ. Trả về dict kết
    quả benchmark để caller in/kiểm tra thêm nếu cần.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    result.equity_curve.to_csv(out / "equity_curve.csv")
    result.trade_log.to_csv(out / "trade_log.csv")
    result.regime_history.to_csv(out / "regime_history.csv")
    pd.DataFrame([result.cost_report.as_dict()]).to_csv(out / "cost_report.csv", index=False)

    initial_equity = result.metadata["config"].initial_equity
    rebalance_threshold_pct = result.metadata["config"].rebalance_threshold_pct

    strategy_metrics = _basic_metrics(result.equity_curve["equity"])
    benchmarks = {
        "strategy": strategy_metrics,
        "buy_and_hold": compare_buy_and_hold(result.equity_curve, ohlcv, cost_model, initial_equity),
        "sma200_trend": compare_sma200_trend(
            result.equity_curve, ohlcv, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
        ),
        "random_allocation": compare_random_allocation(
            result.equity_curve, ohlcv, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
        ),
        "static_vol_target": compare_static_vol_target(
            result.equity_curve, ohlcv, cost_model, instrument_rules, rebalance_threshold_pct, initial_equity
        ),
    }
    pd.DataFrame(benchmarks).T.to_csv(out / "benchmark_comparison.csv")

    console = Console()
    table = Table(title="So sánh benchmark")
    table.add_column("Chiến lược")
    for metric in ["total_return", "cagr", "sharpe", "sortino", "calmar", "max_drawdown_pct"]:
        table.add_column(metric)
    for name, metrics in benchmarks.items():
        row = [name]
        for metric in ["total_return", "cagr", "sharpe", "sortino", "calmar", "max_drawdown_pct"]:
            key = f"{metric}_mean" if f"{metric}_mean" in metrics else metric
            value = metrics.get(key)
            row.append(f"{value:.4f}" if value is not None else "-")
        table.add_row(*row)
    console.print(table)

    cost_table = Table(title="Chi phí")
    cost_table.add_column("Khoản mục")
    cost_table.add_column("Giá trị")
    for key, value in result.cost_report.as_dict().items():
        cost_table.add_row(key, str(value))
    console.print(cost_table)

    return benchmarks
