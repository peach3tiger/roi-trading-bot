"""tests.test_backtester — cash >= 0, equity = cash + qty*price, cost reconciliation.

File MỚI — không có trong scaffold gốc (cùng tình huống với
data/derivatives_loader.py ở Phase 2: cần thiết thật cho nghiệm thu Phase 6,
không phải tuỳ chọn). Dùng dữ liệu tổng hợp + cấu hình HMM nhỏ để chạy
nhanh trong bộ test tự động — bài chạy đầy đủ 2018→nay là nghiệm thu thủ
công riêng (xem output đã dán), không thuộc bộ test này.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from backtest.backtester import BacktestResult, WalkForwardBacktester, WalkForwardConfig
from backtest.cost_model import CostModel
from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.trend_gate import StructuralTrendGate, TrendGateConfig

# EM restart hiếm khi không hội tụ ở một vài trong n_init lần khởi tạo —
# vô hại, xem pyproject.toml [tool.pytest.ini_options] filterwarnings và
# Phase 3 (mọi model được CHỌN đều converged=True).


def _make_synthetic_ohlcv(n_bars: int = 1100, seed: int = 3) -> pd.DataFrame:
    """OHLCV tổng hợp, không phụ thuộc mạng/data/cache. 1100 bar mặc định:
    warmup của compute_tier1_features (SMA200 + z-score lookback 365) một
    mình đã ăn ~565 bar đầu; cộng thêm is_bars=100 cần trước khi window OOS
    đầu tiên có thể bắt đầu."""
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0003, 0.02, n_bars)
    close = 20000 * np.exp(np.cumsum(log_returns))
    high = close * (1 + rng.uniform(0.001, 0.02, n_bars))
    low = close * (1 - rng.uniform(0.001, 0.02, n_bars))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    trade_count = rng.integers(1000, 5000, n_bars)
    index = pd.date_range("2020-01-01", periods=n_bars, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": trade_count * 0.1,
            "trade_count": trade_count,
        },
        index=index,
    )


def _make_backtester() -> WalkForwardBacktester:
    engine = HMMRegimeEngine(
        n_candidates=[3],
        n_init=2,
        covariance_type="full",
        min_train_bars=100,
        stability_bars=3,
        flicker_window=20,
        flicker_threshold=4,
    )
    orchestrator = StrategyOrchestrator(min_confidence=0.55, rebalance_threshold_pct=Decimal("25"))
    gate = StructuralTrendGate(TrendGateConfig())
    cost_model = CostModel(
        taker_fee_pct=Decimal("0.10"),
        maker_fee_pct=Decimal("0.10"),
        slippage_pct=Decimal("0.03"),
        assume_taker=True,
    )
    config = WalkForwardConfig(is_bars=100, oos_bars=30, step_bars=30)
    return WalkForwardBacktester(engine, orchestrator, gate, cost_model, config)


@pytest.fixture(scope="module")
def small_result() -> BacktestResult:
    ohlcv = _make_synthetic_ohlcv()
    return _make_backtester().run(
        "BTCUSDT",
        ohlcv,
        datetime(2022, 5, 1, tzinfo=timezone.utc),
        datetime(2022, 12, 31, tzinfo=timezone.utc),
    )


def test_cash_never_negative(small_result: BacktestResult) -> None:
    assert (small_result.equity_curve["cash"] >= 0).all()


def test_equity_equals_cash_plus_qty_times_price(small_result: BacktestResult) -> None:
    ec = small_result.equity_curve
    computed = ec["cash"] + ec["qty"] * ec["price"]
    diff = (computed - ec["equity"]).abs()
    assert (diff < Decimal("0.01")).all()


def test_cost_report_reconciles_with_trade_log(small_result: BacktestResult) -> None:
    trade_log = small_result.trade_log
    report = small_result.cost_report
    if trade_log.empty:
        assert report.total_fee_usdt == 0
        assert report.total_slippage_usdt == 0
        return
    fee_sum = sum(trade_log["fee"], start=Decimal("0"))
    slippage_sum = sum(trade_log["slippage_cost"], start=Decimal("0"))
    assert report.total_fee_usdt == fee_sum
    assert report.total_slippage_usdt == slippage_sum
    assert report.n_rebalances == len(trade_log)


def test_no_look_ahead_different_end_dates_identical_overlap() -> None:
    """Nghiệm thu Phase 6: chạy 2 lần với --end khác nhau, phần chồng lấn
    của equity curve phải giống hệt nhau."""
    ohlcv = _make_synthetic_ohlcv()
    start = datetime(2022, 5, 1, tzinfo=timezone.utc)

    r1 = _make_backtester().run("BTCUSDT", ohlcv, start, datetime(2022, 9, 30, tzinfo=timezone.utc))
    r2 = _make_backtester().run("BTCUSDT", ohlcv, start, datetime(2022, 12, 31, tzinfo=timezone.utc))

    overlap = r1.equity_curve.index
    diff = (r1.equity_curve.loc[overlap, "equity"] - r2.equity_curve.loc[overlap, "equity"]).abs()
    assert (diff == 0).all()


def test_final_allocation_never_exceeds_trend_gate_cap(small_result: BacktestResult) -> None:
    """So từng phần tử bằng `Decimal`, KHÔNG qua số học của `Series`.

    `Series + Decimal` không có kiểu hợp lệ (mypy: `Value of type variable
    "S2" of "__add__" of "Series" cannot be "Decimal"`) — pandas không hứa
    gì về việc cộng một `Decimal` vào một Series `object`. Nó CHẠY được ở
    runtime, nhưng nó chạy qua đường `object` mà stub không mô tả, và đúng
    ở đây là chỗ CLAUDE.md bất biến #3 quan tâm nhất: ép sang `float` để
    làm vừa lòng kiểu sẽ chấp nhận đúng loại sai lệch mà `Decimal` sinh ra
    để loại bỏ.

    Đổi lại còn được một thứ bản cũ không có: khi đỏ, nó chỉ ra ĐÚNG bar
    nào vi phạm thay vì chỉ nói "có gì đó sai".
    """
    rh = small_result.regime_history
    dung_sai = Decimal("1e-9")

    vi_pham = [
        (ts, cuoi, tran)
        for ts, cuoi, tran in zip(
            rh.index, rh["final_allocation_pct"], rh["trend_gate_cap"]
        )
        if Decimal(str(cuoi)) > Decimal(str(tran)) + dung_sai
    ]

    assert not vi_pham, f"{len(vi_pham)} bar vượt trần trend gate, đầu tiên: {vi_pham[0]}"
