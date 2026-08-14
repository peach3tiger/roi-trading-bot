"""Regression harness — backtest hiện tại so với baseline Phase 7.

## QUY TẮC QUAN TRỌNG NHẤT: harness này KHÔNG PHẢI để "cho qua"

Nếu nó fail sau một refactor, câu hỏi là **"thay đổi đó có CỐ Ý ảnh hưởng
kết quả không?"** — không phải **"làm sao cho nó xanh?"**.

- Cố ý (đổi công thức, sửa bug ảnh hưởng số liệu) → ghi lý do vào
  `docs/DECISIONS.md` TRƯỚC, rồi mới sinh lại snapshot, kèm ngày và commit.
- Không cố ý → **revert**. Đừng nới ngưỡng, đừng cập nhật snapshot.

Nới ngưỡng để cho xanh là cách chắc chắn nhất biến harness thành một thứ
tốn 3 phút mỗi lần merge mà không phát hiện được gì.

## Cấu hình được ghim

`reports/pruned8_base` (nguồn của snapshot) sinh bởi — `docs/DECISIONS.md`
mục "Cấu hình hiện tại đang đánh giá: pruned-8" và
`docs/VALIDATION_REPORT.md`:

    python main.py --backtest --start 2018-02-09 --end 2026-08-04 \\
        --feature-subset log_return_1,log_return_5,realized_vol_20,\\
    vol_ratio_5_20,adx_14,sma50_slope,trade_count_zscore_50,\\
    trade_count_sma10_slope

`is_bars=365, oos_bars=182, step_bars=182, covariance_type=full`,
`uncertainty_mode="halve"` (mặc định của `StrategyOrchestrator`).
Không truyền `--data-start` nên `data_start = start` (xem
`main.resolve_data_start`).

## Vì sao tính lại chỉ số từ equity_curve thay vì đọc benchmark_comparison.csv

Cả hai phía — baseline và bản chạy mới — đi qua **CÙNG MỘT** hàm
`_basic_metrics()`. Đọc Sharpe đã ghi sẵn trong CSV thì đang so một con số
tính bằng code CŨ với một con số tính bằng code MỚI; nếu chính hàm tính
metric đổi, harness sẽ báo "chiến lược trôi" trong khi chiến lược không
đổi gì. Tính lại cả hai phía loại bỏ nhầm lẫn đó.

Đánh đổi có chủ ý: harness này KHÔNG bắt được thay đổi trong
`_basic_metrics()`. Đó là việc của `tests/test_cost_model.py` và các test
đơn vị khác, không phải của một phép so hồi quy đầu-cuối.

## Chi phí

~13 window × 50 lần `.fit()` (full covariance) ≈ 3 phút. Vì thế nó mang
`@pytest.mark.slow` và chỉ chạy khi gọi tường minh:

    pytest -m slow

Phần LOGIC SO SÁNH thì được kiểm bằng test NHANH ở cùng file (dữ liệu tổng
hợp) — một bug trong bộ so sánh không được phép ẩn tới tận lần chạy 3 phút.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pytest

import main as main_mod
from tests.fixtures import load_fixture

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots" / "phase7_baseline"

# Ghim từ docs/DECISIONS.md — KHÔNG đọc từ settings.yaml. settings.yaml là
# cấu hình ĐANG DÙNG và có thể đổi; baseline được đo với đúng bộ này, nên
# nó phải nằm cạnh phép so, không phải ở một file có thể trôi.
_FEATURE_SUBSET = (
    "log_return_1",
    "log_return_5",
    "realized_vol_20",
    "vol_ratio_5_20",
    "adx_14",
    "sma50_slope",
    "trade_count_zscore_50",
    "trade_count_sma10_slope",
)
_START = datetime(2018, 2, 9, tzinfo=timezone.utc)
_END = datetime(2026, 8, 4, tzinfo=timezone.utc)
_IS_BARS = 365
_OOS_BARS = 182
_STEP_BARS = 182
_SYMBOL = "BTCUSDT"
_CCXT_SYMBOL = "BTC/USDT"


@dataclass(frozen=True)
class Threshold:
    """`relative=True` -> ngưỡng là % của giá trị baseline."""

    name: str
    tolerance: float
    relative: bool


# Bảng ngưỡng §A.1. Con số ở đây là HỢP ĐỒNG — đổi chúng là đổi định nghĩa
# "hồi quy", nên phải có entry docs/DECISIONS.md đi kèm.
THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("sharpe", 0.001, relative=False),
    Threshold("calmar", 0.001, relative=False),
    Threshold("max_drawdown_pct", 0.001, relative=False),
    Threshold("n_trades", 0.01, relative=True),
    Threshold("regime_transitions", 0.02, relative=True),
    Threshold("total_fee_usdt", 0.005, relative=True),
)


@dataclass(frozen=True)
class Comparison:
    name: str
    baseline: float
    current: float
    tolerance: float
    relative: bool

    @property
    def deviation(self) -> float:
        """Tuyệt đối, hoặc tỷ lệ so với baseline nếu `relative`."""
        raw = abs(self.current - self.baseline)
        if not self.relative:
            return raw
        return raw / abs(self.baseline) if self.baseline else (0.0 if raw == 0 else float("inf"))

    @property
    def ok(self) -> bool:
        return self.deviation <= self.tolerance

    def describe(self) -> str:
        unit = "%" if self.relative else ""
        shown = self.deviation * 100 if self.relative else self.deviation
        limit = self.tolerance * 100 if self.relative else self.tolerance
        flag = "OK " if self.ok else "LỆCH"
        return (
            f"  [{flag}] {self.name:20s} baseline={self.baseline:<20.10g} "
            f"hiện tại={self.current:<20.10g} lệch={shown:.6g}{unit} (trần {limit:g}{unit})"
        )


@dataclass(frozen=True)
class Divergence:
    """Bar ĐẦU TIÊN mà equity curve lệch — nghiệm thu §A.1 đòi "dòng đầu
    tiên lệch, giá trị cũ/mới, và bar nào"."""

    position: int
    bar: str
    column: str
    baseline: float
    current: float


# ----------------------------------------------------------------------
# Trích chỉ số — dùng CHUNG cho baseline và bản chạy mới
# ----------------------------------------------------------------------


def extract_metrics(
    equity_curve: pd.DataFrame,
    trade_log: pd.DataFrame,
    regime_history: pd.DataFrame,
    total_fee_usdt: float,
) -> dict[str, float]:
    """Sáu chỉ số của bảng §A.1. Hàm THUẦN — không đọc file, không chạy
    backtest, nên test nhanh gọi được trực tiếp."""
    from backtest.performance import _basic_metrics

    basic = _basic_metrics(equity_curve["equity"])
    return {
        "sharpe": float(basic["sharpe"]),
        "calmar": float(basic["calmar"]),
        "max_drawdown_pct": float(basic["max_drawdown_pct"]),
        "n_trades": float(len(trade_log)),
        # Số LẦN ĐỔI regime, không phải số bar — hai bar liên tiếp cùng
        # regime không phải một transition.
        "regime_transitions": float((regime_history["regime_id"].diff() != 0).sum() - 1),
        "total_fee_usdt": float(total_fee_usdt),
    }


def load_baseline_metrics(snapshot_dir: Path = _SNAPSHOT_DIR) -> dict[str, float]:
    equity = pd.read_csv(snapshot_dir / "equity_curve.csv")
    trades = pd.read_csv(snapshot_dir / "trade_log.csv")
    regimes = pd.read_csv(snapshot_dir / "regime_history.csv")
    costs = pd.read_csv(snapshot_dir / "cost_report.csv")
    return extract_metrics(equity, trades, regimes, float(costs["total_fee_usdt"].iloc[0]))


def compare(baseline: dict[str, float], current: dict[str, float]) -> list[Comparison]:
    return [
        Comparison(
            name=t.name,
            baseline=baseline[t.name],
            current=current[t.name],
            tolerance=t.tolerance,
            relative=t.relative,
        )
        for t in THRESHOLDS
    ]


def first_equity_divergence(
    baseline: pd.DataFrame, current: pd.DataFrame, *, atol: float = 1e-9
) -> Optional[Divergence]:
    """Bar đầu tiên lệch. `None` nếu trùng khớp hoàn toàn.

    Độ dài khác nhau cũng là một lệch — báo ở đúng vị trí bắt đầu thừa/thiếu
    thay vì để `zip` cắt ngắn im lặng và nói "không lệch gì".
    """
    n = min(len(baseline), len(current))
    for col in ("equity", "allocation_pct", "qty", "cash"):
        if col not in baseline.columns or col not in current.columns:
            continue
        b = pd.to_numeric(baseline[col].iloc[:n], errors="coerce").to_numpy()
        c = pd.to_numeric(current[col].iloc[:n], errors="coerce").to_numpy()
        for i in range(n):
            if abs(b[i] - c[i]) > atol:
                return Divergence(
                    position=i,
                    bar=str(baseline.iloc[i, 0]),
                    column=col,
                    baseline=float(b[i]),
                    current=float(c[i]),
                )

    if len(baseline) != len(current):
        i = n
        which = baseline if len(baseline) > len(current) else current
        return Divergence(
            position=i,
            bar=str(which.iloc[i, 0]) if i < len(which) else "?",
            column="<số dòng>",
            baseline=float(len(baseline)),
            current=float(len(current)),
        )
    return None


def format_report(comparisons: list[Comparison], divergence: Optional[Divergence]) -> str:
    lines = ["", "REGRESSION vs baseline Phase 7 (tests/snapshots/phase7_baseline/):"]
    lines += [c.describe() for c in comparisons]
    if divergence is not None:
        lines += [
            "",
            "  Bar ĐẦU TIÊN lệch trong equity_curve:",
            f"    vị trí dòng : {divergence.position}",
            f"    bar         : {divergence.bar}",
            f"    cột         : {divergence.column}",
            f"    baseline    : {divergence.baseline!r}",
            f"    hiện tại    : {divergence.current!r}",
        ]
    lines += [
        "",
        "  Fail ở đây KHÔNG phải lời mời nới ngưỡng. Hỏi: thay đổi vừa rồi có",
        "  CỐ Ý ảnh hưởng kết quả không? Cố ý -> ghi docs/DECISIONS.md rồi sinh",
        "  lại snapshot. Không cố ý -> revert. Xem docstring file này.",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Chạy lại backtest theo đúng cấu hình đã ghim
# ----------------------------------------------------------------------


def run_pinned_backtest() -> Any:
    from backtest.backtester import WalkForwardBacktester

    settings = main_mod.load_settings()
    wf = replace(
        main_mod.build_walk_forward_config(settings),
        is_bars=_IS_BARS,
        oos_bars=_OOS_BARS,
        step_bars=_STEP_BARS,
    )
    # `data_start = start` — không truyền `--data-start` ở lần chạy gốc.
    # FIXTURE ĐÃ COMMIT. `bar_offset_hours=0` của bản cũ trùng đúng mặc
    # định của `HistoryLoader` (đã kiểm bằng đo), nên fixture tái tạo
    # nguyên vẹn đầu vào baseline Phase 7.
    ohlcv = load_fixture(_END)
    backtester = WalkForwardBacktester(
        hmm_engine=main_mod.build_hmm_engine(settings, min_train_bars=_IS_BARS),
        strategy_orchestrator=main_mod.build_orchestrator(settings),
        trend_gate=main_mod.build_trend_gate(settings, enabled=True),
        cost_model=main_mod.build_cost_model(settings),
        config=wf,
        feature_config=main_mod.build_feature_config(settings, feature_subset=_FEATURE_SUBSET),
    )
    return backtester.run(_SYMBOL, ohlcv, _START, _END)


# ======================================================================
# Test NHANH — kiểm chính bộ so sánh, không chạy backtest
# ======================================================================


def _frame(equity: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(equity), freq="D", tz="UTC")
    return pd.DataFrame(
        {"timestamp": idx, "cash": 0.0, "qty": 0.0, "price": 1.0, "equity": equity, "allocation_pct": 0.0}
    )


def test_lech_duoi_nguong_thi_ok() -> None:
    base = {t.name: 1.0 for t in THRESHOLDS}
    cur = {**base, "sharpe": 1.0005, "n_trades": 1.005}

    assert all(c.ok for c in compare(base, cur))


def test_lech_tren_nguong_thi_bao() -> None:
    base = {t.name: 1.0 for t in THRESHOLDS}
    cur = {**base, "sharpe": 1.002}

    bad = [c for c in compare(base, cur) if not c.ok]
    assert [c.name for c in bad] == ["sharpe"]


def test_nguong_tuyet_doi_va_tuong_doi_khac_nhau() -> None:
    """`sharpe` là ngưỡng TUYỆT ĐỐI (0.001), `n_trades` là TƯƠNG ĐỐI (1%).
    Cùng một mức lệch 0.5% cho hai kết luận khác nhau — nếu không, một
    trong hai ngưỡng đang bị áp sai kiểu."""
    base = {t.name: 100.0 for t in THRESHOLDS}
    cur = {**base, "sharpe": 100.5, "n_trades": 100.5}

    result = {c.name: c.ok for c in compare(base, cur)}
    assert result["sharpe"] is False, "0.5 tuyệt đối > 0.001"
    assert result["n_trades"] is True, "0.5% tương đối <= 1%"


def test_bao_dung_bar_dau_tien_lech() -> None:
    base = _frame([100.0, 101.0, 102.0, 103.0])
    cur = _frame([100.0, 101.0, 999.0, 103.0])

    div = first_equity_divergence(base, cur)

    assert div is not None
    assert div.position == 2
    assert div.column == "equity"
    assert (div.baseline, div.current) == (102.0, 999.0)
    assert "2024-01-03" in div.bar


def test_trung_khop_thi_khong_bao_lech() -> None:
    base = _frame([100.0, 101.0, 102.0])

    assert first_equity_divergence(base, _frame([100.0, 101.0, 102.0])) is None


def test_do_dai_khac_nhau_cung_la_lech() -> None:
    """`zip` cắt ngắn im lặng sẽ nói "không lệch gì" khi bản mới thiếu
    hẳn 500 bar cuối — đúng loại hồi quy nghiêm trọng nhất."""
    div = first_equity_divergence(_frame([100.0, 101.0, 102.0]), _frame([100.0, 101.0]))

    assert div is not None
    assert div.column == "<số dòng>"


def test_bao_cao_noi_ro_khong_duoc_noi_nguong() -> None:
    """Thông điệp fail phải mang theo quy tắc, không chỉ con số. Người đọc
    lúc 2 giờ sáng sau một merge hỏng cần biết PHẢI LÀM GÌ."""
    base = {t.name: 1.0 for t in THRESHOLDS}
    report = format_report(compare(base, {**base, "sharpe": 5.0}), None)

    assert "revert" in report
    assert "docs/DECISIONS.md" in report
    assert "nới ngưỡng" in report


def test_snapshot_da_commit_va_doc_duoc() -> None:
    """Snapshot phải nằm trong repo — một baseline chỉ có trên máy một
    người thì không phải baseline."""
    for name in (
        "equity_curve.csv",
        "regime_history.csv",
        "trade_log.csv",
        "benchmark_comparison.csv",
        "cost_report.csv",
    ):
        assert (_SNAPSHOT_DIR / name).exists(), f"thiếu {name} trong snapshot"

    metrics = load_baseline_metrics()
    assert set(metrics) == {t.name for t in THRESHOLDS}
    # Ghim vài con số từ docs/VALIDATION_REPORT.md — nếu snapshot bị thay
    # bằng dữ liệu khác, phép so hồi quy sẽ so với sai baseline mà không ai
    # biết.
    assert metrics["sharpe"] == pytest.approx(0.9411, abs=0.0005)
    assert metrics["total_fee_usdt"] == pytest.approx(4445.04, abs=0.01)


# ======================================================================
# Test CHẬM — chạy lại backtest đầy đủ
# ======================================================================


@pytest.mark.slow
def test_regression_vs_phase7_baseline() -> None:
    """~3 phút. `pytest -m slow`.

    Đọc QUY TẮC ở đầu file trước khi làm gì với một lần fail.
    """
    result = run_pinned_backtest()
    current = extract_metrics(
        result.equity_curve.reset_index(),
        result.trade_log,
        result.regime_history.reset_index(),
        float(result.cost_report.total_fee_usdt),
    )
    baseline = load_baseline_metrics()

    comparisons = compare(baseline, current)
    divergence = first_equity_divergence(
        pd.read_csv(_SNAPSHOT_DIR / "equity_curve.csv"), result.equity_curve.reset_index()
    )

    assert all(c.ok for c in comparisons), format_report(comparisons, divergence)
