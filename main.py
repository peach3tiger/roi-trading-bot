"""main.py — điểm vào CLI: live loop, backtest, train-only, stress-test.

Không có bước "chờ thị trường mở" — thị trường crypto 24/7, toàn bộ logic
giờ giao dịch bị loại bỏ khỏi hệ thống này (xem CLAUDE.md bất biến #10).

**Nguồn sự thật của tham số.** Trước file này chưa có chỗ nào đọc
`config/settings.yaml`: mọi giá trị sống dưới dạng default trong dataclass,
tức là hai nguồn sự thật song song và không có gì bắt chúng khớp nhau
(CLAUDE.md bất biến #14). `load_settings()` + các `build_*` dưới đây là chỗ
duy nhất dựng component từ YAML, để `--sweep` ở Phase 7 chỉ phải sửa một nơi.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")

# Ngày bắt đầu TẢI dữ liệu mặc định. 2018-02-09 chứ không phải 2018-01-01: chuỗi
# daily kline timeZone=-6:00 của Binance thiếu hẳn 2018-02-08, nên mọi lần chạy
# bắt đầu từ ngày kế tiếp để bốn bar-offset dùng chung một khoảng thời gian.
# Ghi nhận, không vá — không bịa ra một bar không tồn tại ở nguồn.
DEFAULT_DATA_START = "2018-02-09"

# `_REGIME_LABELS` trong core/hmm_engine.py chỉ định nghĩa nhãn cho 3–7 state.
# `select_and_train` sẽ raise ngoài dải này, nên CLI chặn sớm với thông báo rõ
# ràng thay vì để nổ giữa window đầu tiên của một lần chạy dài.
_MAX_LABELLED_STATES = 7


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regime-trader-crypto")
    parser.add_argument("--dry-run", action="store_true", help="Chạy full pipeline, không đặt lệnh")
    parser.add_argument("--backtest", action="store_true", help="Walk-forward backtester")
    parser.add_argument("--train-only", action="store_true", help="Train HMM rồi thoát")
    parser.add_argument("--stress-test", action="store_true", help="Chạy stress test")
    parser.add_argument("--compare", action="store_true", help="So sánh benchmark")
    parser.add_argument("--dashboard", action="store_true", help="Xem dashboard của instance đang chạy")
    parser.add_argument("--testnet", action="store_true", default=True, help="Ép dùng testnet (mặc định)")
    parser.add_argument("--live", action="store_true", help="Ép dùng mainnet (yêu cầu xác nhận gõ tay)")
    parser.add_argument("--start", type=str, help="Ngày bắt đầu backtest, YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="Ngày kết thúc backtest, YYYY-MM-DD")
    parser.add_argument("--period", type=str, help="Kiểm tra riêng một giai đoạn, vd. 2022")
    parser.add_argument("--symbol", type=str, help="Symbol kiểm định ngoài mẫu, vd. ETHUSDT")
    parser.add_argument("--sweep", type=str, help="Tên tham số cần quét")
    parser.add_argument("--range", type=str, help="min,max,step cho --sweep")
    parser.add_argument("--no-trend-gate", action="store_true", help="So sánh có/không Phase 3.5")
    parser.add_argument(
        "--bar-offset", type=str, help="Danh sách offset giờ UTC để kiểm tra độ nhạy mốc đóng bar"
    )
    parser.add_argument("--ablation", action="store_true", help="Quét feature từng cái một")
    parser.add_argument(
        "--feature-subset",
        type=str,
        default=None,
        help="Danh sách tên cột Tầng 1 dùng thay vì cả 14 cột mặc định (ablation/pruning thủ công)",
    )
    parser.add_argument("--config", type=str, default=str(DEFAULT_SETTINGS_PATH), help="Đường dẫn settings")
    parser.add_argument("--output-dir", type=str, default="reports", help="Thư mục xuất báo cáo")
    parser.add_argument(
        "--data-start",
        type=str,
        default=None,
        help=(
            "Ngày bắt đầu TẢI dữ liệu — khác --start (ngày bắt đầu ĐÁNH GIÁ). "
            "Mặc định: --period tải từ "
            f"{DEFAULT_DATA_START}; các trường hợp khác tải từ chính --start."
        ),
    )
    return parser


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def load_settings(path: str | Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        settings: dict[str, Any] = yaml.safe_load(fh)
    return settings


def _parse_date(value: str, *, field: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"{field} phải theo định dạng YYYY-MM-DD, nhận: {value!r}") from exc


def resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    """`--period 2022` là lối tắt cho `--start 2022-01-01 --end 2022-12-31`.

    Ưu tiên `--period` khi có cả hai, và nói ra thay vì âm thầm chọn một bên —
    một lần chạy kiểm định chạy sai khoảng thời gian là kết quả sai mà trông
    vẫn hợp lệ.
    """
    if args.period:
        if args.start or args.end:
            raise ValueError("Dùng --period HOẶC --start/--end, không dùng cả hai")
        year = int(args.period)
        return (
            datetime(year, 1, 1, tzinfo=timezone.utc),
            datetime(year, 12, 31, tzinfo=timezone.utc),
        )

    default_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
    start = _parse_date(args.start, field="--start") if args.start else default_start
    end = _parse_date(args.end, field="--end") if args.end else datetime.now(timezone.utc)
    if end <= start:
        raise ValueError(f"--end ({end:%Y-%m-%d}) phải sau --start ({start:%Y-%m-%d})")
    return start, end


def resolve_data_start(
    args: argparse.Namespace,
    eval_start: datetime,
    settings: dict[str, Any],
    is_bars: int,
) -> datetime:
    """Ngày TẢI dữ liệu — khác ngày ĐÁNH GIÁ, và hai cái này từng bị gộp làm một.

    Feature cần `zscore_lookback` bar warmup, rồi window IS đầu tiên cần thêm
    `is_bars` bar nữa, TRƯỚC bar đánh giá đầu tiên. Nếu chỉ tải đúng khoảng cần
    đánh giá thì `features` rỗng và `_plan_windows` trả về [] — đó chính là lý
    do `--period 2022` crash.

    Ba trường hợp, cố ý khác nhau:

    * `--data-start` chỉ định rõ → dùng đúng thế, có kiểm tra đủ warmup.
    * `--period` → tải từ `DEFAULT_DATA_START`. "Đánh giá trên 2022" chỉ có
      nghĩa khi model được train trên dữ liệu TRƯỚC 2022.
    * còn lại (`--start`/mặc định) → tải TỪ CHÍNH `--start`. Giữ nguyên ngữ
      nghĩa cũ cho quét độ nhạy start-date: ở đó việc dịch cả lưới cửa sổ
      CHÍNH LÀ biến cần đo, nên tự động lùi ngày tải sẽ phá mất phép đo.
    """
    required_warmup = settings["hmm"]["zscore_lookback"] + is_bars

    if args.data_start:
        data_start = _parse_date(args.data_start, field="--data-start")
    elif args.period:
        data_start = _parse_date(DEFAULT_DATA_START, field="--data-start")
    else:
        return eval_start

    if data_start > eval_start:
        raise ValueError(
            f"--data-start ({data_start:%Y-%m-%d}) phải trước --start ({eval_start:%Y-%m-%d})"
        )

    gap_days = (eval_start - data_start).days
    if gap_days < required_warmup:
        raise ValueError(
            f"--data-start {data_start:%Y-%m-%d} chỉ cách ngày đánh giá "
            f"{eval_start:%Y-%m-%d} {gap_days} ngày, cần ít nhất {required_warmup} "
            f"(zscore_lookback {settings['hmm']['zscore_lookback']} + is_bars {is_bars})."
        )
    return data_start


def parse_bar_offsets(raw: str | None) -> list[int]:
    if not raw:
        return [0]
    offsets = [int(part.strip()) for part in raw.split(",") if part.strip()]
    invalid = [o for o in offsets if o not in (0, 6, 12, 18)]
    if invalid:
        raise ValueError(f"--bar-offset chỉ nhận 0/6/12/18, nhận: {invalid}")
    return offsets


# Tên cột Tầng 1 thật — trùng với những gì compute_tier1_features() tạo ra.
# Khai báo lại ở đây (thay vì import từ data.feature_engineering) để
# --feature-subset báo lỗi ngay lúc parse argv, không phải giữa chừng
# window đầu tiên của một lần chạy dài.
_VALID_TIER1_FEATURES = frozenset(
    {
        "log_return_1",
        "log_return_5",
        "log_return_20",
        "realized_vol_20",
        "vol_ratio_5_20",
        "adx_14",
        "sma50_slope",
        "rsi_zscore_14",
        "distance_to_sma200_pct",
        "roc_10",
        "roc_20",
        "atr_norm_14",
        "trade_count_zscore_50",
        "trade_count_sma10_slope",
    }
)


def parse_feature_subset(raw: str | None) -> tuple[str, ...] | None:
    """`None` = giữ nguyên cả 14 cột (mặc định). Dùng cho ablation/feature-pruning
    thủ công — xem ghi chú ở `FeatureConfig.feature_subset`."""
    if not raw:
        return None
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    invalid = [n for n in names if n not in _VALID_TIER1_FEATURES]
    if invalid:
        raise ValueError(f"--feature-subset có tên cột không hợp lệ: {invalid}")
    return names


# ----------------------------------------------------------------------
# Component builders — chỗ DUY NHẤT dịch settings.yaml thành object
# ----------------------------------------------------------------------


def build_hmm_engine(settings: dict[str, Any], *, min_train_bars: int | None = None) -> Any:
    """Dựng HMMRegimeEngine từ settings.

    `min_train_bars` override được là có chủ đích: `settings.yaml` đặt 730 như
    ngưỡng an toàn cho training LIVE (2 năm dữ liệu thật trước khi tin một
    model), nhưng §4.1 định nghĩa IS = 365 bar cho walk-forward. Truyền thẳng
    730 vào backtester sẽ raise ngay window đầu tiên — xem cảnh báo đầu
    backtest/backtester.py.
    """
    # Kiểm tra dải TRƯỚC khi import: config sai phải báo lỗi được kể cả trong
    # môi trường chưa cài hmmlearn, và không phải chờ tới giữa window đầu tiên.
    hmm = settings["hmm"]
    n_candidates = list(hmm["n_candidates"])
    too_many = [n for n in n_candidates if n > _MAX_LABELLED_STATES]
    if too_many:
        raise ValueError(
            f"n_candidates {too_many} vượt dải có nhãn (3–{_MAX_LABELLED_STATES}) trong "
            "_REGIME_LABELS. Muốn quét rộng hơn để khảo sát BIC thì dùng "
            "HMMRegimeEngine.scan_bic() trực tiếp — nó không cần nhãn."
        )

    from core.hmm_engine import HMMRegimeEngine

    return HMMRegimeEngine(
        n_candidates=n_candidates,
        n_init=hmm["n_init"],
        covariance_type=hmm["covariance_type"],
        min_train_bars=min_train_bars if min_train_bars is not None else hmm["min_train_bars"],
        stability_bars=hmm["stability_bars"],
        flicker_window=hmm["flicker_window"],
        flicker_threshold=hmm["flicker_threshold"],
    )


def build_feature_config(settings: dict[str, Any], *, feature_subset: tuple[str, ...] | None = None) -> Any:
    """Trước bản này, `WalkForwardBacktester.run()` tự dựng `FeatureConfig()`
    mặc định bên trong — hàm này tồn tại nhưng chưa từng được `run_backtest`
    gọi tới, nên `use_trade_count_not_volume`/`tier2_derivatives`/
    `tier3_temporal` trong settings.yaml chưa từng có tác dụng thật.
    """
    from data.feature_engineering import FeatureConfig

    return FeatureConfig(
        zscore_lookback=settings["hmm"]["zscore_lookback"],
        use_trade_count_not_volume=settings["features"]["use_trade_count_not_volume"],
        tier2_derivatives=settings["features"]["tier2_derivatives"],
        tier3_temporal=settings["features"]["tier3_temporal"],
        feature_subset=feature_subset,
    )


def build_trend_gate(settings: dict[str, Any], *, enabled: bool = True) -> Any:
    """`--no-trend-gate` dựng gate với mọi cap = 1.00 thay vì trả None.

    Tầng chỉ được GIẢM tỷ trọng (bất biến #2); một gate trần 1.00 là phần tử
    trung hoà của min(), nên tắt gate không cần nhánh `if` nào trong vòng lặp
    mô phỏng — và không có nhánh nào thì không có nhánh nào bị sai.
    """
    from core.trend_gate import StructuralTrendGate, TrendGateConfig

    tg = settings["trend_gate"]
    config = TrendGateConfig(
        sma_period=tg["sma_period"],
        slope_lookback=tg["slope_lookback"],
        buffer_pct=Decimal(str(tg["buffer_pct"])),
        confirm_bars=tg["confirm_bars"],
        cap_bull_structure=Decimal(str(tg["cap_bull_structure"])),
        cap_transition=Decimal(str(tg["cap_transition"])),
        cap_bear_structure=Decimal(str(tg["cap_bear_structure"])),
    )
    if not enabled:
        config = replace(
            config,
            cap_bull_structure=Decimal("1.00"),
            cap_transition=Decimal("1.00"),
            cap_bear_structure=Decimal("1.00"),
        )
    return StructuralTrendGate(config)


def build_cost_model(settings: dict[str, Any]) -> Any:
    from backtest.cost_model import CostModel

    costs = settings["costs"]
    return CostModel(
        taker_fee_pct=Decimal(str(costs["taker_fee_pct"])),
        maker_fee_pct=Decimal(str(costs["maker_fee_pct"])),
        slippage_pct=Decimal(str(costs["slippage_pct"])),
        assume_taker=costs["assume_taker"],
    )


def build_walk_forward_config(settings: dict[str, Any]) -> Any:
    from backtest.backtester import WalkForwardConfig

    bt = settings["backtest"]
    return WalkForwardConfig(
        is_bars=bt["is_bars"],
        oos_bars=bt["oos_bars"],
        step_bars=bt["step_bars"],
        fill_delay_bars=bt["fill_delay_bars"],
        rebalance_threshold_pct=Decimal(str(settings["strategy"]["rebalance_threshold_pct"])),
    )


def build_orchestrator(settings: dict[str, Any]) -> Any:
    from core.regime_strategies import StrategyOrchestrator

    return StrategyOrchestrator(
        min_confidence=settings["strategy"]["min_confidence"],
        rebalance_threshold_pct=Decimal(str(settings["strategy"]["rebalance_threshold_pct"])),
    )


# ----------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------


def run_backtest(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    """Một lần chạy walk-forward cho mỗi bar-offset, xuất báo cáo mỗi lần.

    Nhiều offset ghi vào thư mục con riêng: tiêu chí 6 của §4.9 so sánh Sharpe
    GIỮA các offset, nên ghi đè lên nhau sẽ xoá mất chính thứ cần đo.
    """
    from backtest.backtester import WalkForwardBacktester
    from backtest.performance import write_reports
    from data.history_loader import HistoryLoader

    start, end = resolve_date_range(args)
    offsets = parse_bar_offsets(args.bar_offset)
    symbol = args.symbol or settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"

    wf_config = build_walk_forward_config(settings)
    feature_config = build_feature_config(settings, feature_subset=parse_feature_subset(args.feature_subset))

    data_start = resolve_data_start(args, start, settings, wf_config.is_bars)

    loader = HistoryLoader()
    results: dict[str, Any] = {}

    for offset in offsets:
        ohlcv = loader.load(ccxt_symbol, "1D", data_start, end, bar_offset_hours=offset)

        backtester = WalkForwardBacktester(
            hmm_engine=build_hmm_engine(settings, min_train_bars=wf_config.is_bars),
            strategy_orchestrator=build_orchestrator(settings),
            trend_gate=build_trend_gate(settings, enabled=not args.no_trend_gate),
            cost_model=build_cost_model(settings),
            config=wf_config,
            feature_config=feature_config,
        )
        result = backtester.run(symbol, ohlcv, start, end)

        out_dir = Path(args.output_dir)
        if len(offsets) > 1:
            out_dir = out_dir / f"offset{offset}"
        benchmarks = write_reports(
            result, ohlcv, build_cost_model(settings), wf_config.instrument_rules, str(out_dir)
        )
        results[f"offset{offset}"] = benchmarks

    return results


def _run_one_config(
    args: argparse.Namespace,
    settings: dict[str, Any],
    subset: tuple[str, ...],
    ohlcv: Any,
    symbol: str,
    start: datetime,
    end: datetime,
    out_dir: Path,
) -> dict[str, Any]:
    """Một lần walk-forward với đúng `subset` feature, trả về metric + BIC trung bình."""
    from backtest.backtester import WalkForwardBacktester
    from backtest.performance import write_reports

    wf_config = build_walk_forward_config(settings)
    backtester = WalkForwardBacktester(
        hmm_engine=build_hmm_engine(settings, min_train_bars=wf_config.is_bars),
        strategy_orchestrator=build_orchestrator(settings),
        trend_gate=build_trend_gate(settings, enabled=not args.no_trend_gate),
        cost_model=build_cost_model(settings),
        config=wf_config,
        feature_config=build_feature_config(settings, feature_subset=subset),
    )
    result = backtester.run(symbol, ohlcv, start, end)
    benchmarks = write_reports(
        result, ohlcv, build_cost_model(settings), wf_config.instrument_rules, str(out_dir)
    )
    ms = result.model_selection
    return {
        "metrics": benchmarks["strategy"],
        "mean_bic": float(ms["selected_bic"].mean()) if not ms.empty else None,
        "mean_samples_per_param": float(ms["samples_per_param"].mean()) if not ms.empty else None,
        "n_windows": len(ms),
    }


def run_ablation(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    """Bỏ từng feature một, đo tác động lên Sharpe OOS. Xuất `feature_ablation.csv`.

    Tiêu chí giữ lại của CLAUDE.md #13 phát biểu theo chiều THÊM feature: giữ
    nếu "cải thiện Sharpe OOS >= 0.1 và không làm xấu BIC". Ablation là chiều
    ngược lại, nên đóng góp của một feature = Sharpe(đủ bộ) - Sharpe(bỏ nó ra).
    Đóng góp >= 0.1 thì feature xứng đáng ở lại.

    **Cảnh báo đọc cột BIC.** BIC của hai model có SỐ FEATURE khác nhau không so
    sánh trực tiếp được: log-likelihood tính trên không gian dữ liệu khác chiều,
    nên giá trị tuyệt đối trôi theo số chiều chứ không chỉ theo độ khớp. Cột
    `delta_mean_bic` để tham khảo xu hướng, KHÔNG phải để phán quyết. Cột phán
    quyết là `delta_sharpe` — Sharpe OOS đo trên cùng một chuỗi equity USDT nên
    so sánh được giữa mọi cấu hình. `samples_per_param` cũng so sánh được và là
    chỉ số phụ đáng tin hơn BIC ở đây.
    """
    # Toàn bộ kiểm tra đầu vào chạy TRƯỚC mọi import nặng: ablation là 9–15 lần
    # walk-forward liên tiếp, hỏng đầu vào mà báo lỗi sau khi đã tải dữ liệu là
    # lãng phí hàng chục phút cho một lỗi gõ sai tên cột.
    subset = parse_feature_subset(args.feature_subset) or tuple(sorted(_VALID_TIER1_FEATURES))
    if len(subset) < 3:
        raise ValueError(f"Ablation cần ít nhất 3 feature, bộ hiện tại có {len(subset)}")

    offsets = parse_bar_offsets(args.bar_offset)
    if len(offsets) > 1:
        raise ValueError("--ablation chạy một bar-offset duy nhất; bỏ bớt --bar-offset")

    # core/hmm_engine.py cần cột này để gán nhãn regime (_build_regime_infos) —
    # xem chú thích chi tiết ở nhánh SKIPPED_STRUCTURAL_REQUIRED bên dưới.
    from core.hmm_engine import _RETURN_FEATURE_NAME

    start, end = resolve_date_range(args)
    symbol = args.symbol or settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
    wf_config = build_walk_forward_config(settings)
    data_start = resolve_data_start(args, start, settings, wf_config.is_bars)

    from data.history_loader import HistoryLoader

    ohlcv = HistoryLoader().load(ccxt_symbol, "1D", data_start, end, bar_offset_hours=offsets[0])
    out_root = Path(args.output_dir)

    logger = logging.getLogger(__name__)
    logger.info("Ablation: %d feature -> %d lần chạy walk-forward", len(subset), len(subset) + 1)

    baseline = _run_one_config(
        args, settings, subset, ohlcv, symbol, start, end, out_root / "ablation_baseline"
    )
    base_sharpe = baseline["metrics"]["sharpe"]

    rows: list[dict[str, Any]] = [
        {
            "dropped_feature": "(none - baseline)",
            "n_features": len(subset),
            "sharpe": base_sharpe,
            "delta_sharpe": 0.0,
            "calmar": baseline["metrics"]["calmar"],
            "total_return": baseline["metrics"]["total_return"],
            "max_drawdown_pct": baseline["metrics"]["max_drawdown_pct"],
            "mean_bic": baseline["mean_bic"],
            "delta_mean_bic": 0.0,
            "mean_samples_per_param": baseline["mean_samples_per_param"],
            "verdict": "baseline",
        }
    ]

    for i, feature in enumerate(subset, 1):
        if feature == _RETURN_FEATURE_NAME:
            # core/hmm_engine.py::_build_regime_infos đọc means_[:, return_idx]
            # của đúng cột này để XẾP HẠNG các state theo mean return va gan
            # nhãn bull/bear -- day khong phai "mot feature trong nhieu feature"
            # theo nghia HMM hoc duoc, ma la truc ma toan bo so do gan nhan
            # dua vao. Bo no ra khong do duoc "tin hieu return co giup Sharpe
            # khong" -- no chi lam _build_regime_infos crash (ValueError: not
            # in list), vi khong con cot nao de tinh return_idx. Ghi nhan
            # SKIPPED thay vi chay mot cau hinh vo nghia hoac dung crash de
            # bao "DROP_CANDIDATE" (sai — day la feature bat buoc ve cau truc).
            logger.info(
                "Ablation %d/%d: BO QUA %s (bat buoc cho HMMRegimeEngine gan nhan regime, "
                "khong the ablate)",
                i,
                len(subset),
                feature,
            )
            rows.append(
                {
                    "dropped_feature": feature,
                    "n_features": len(subset) - 1,
                    "sharpe": None,
                    "delta_sharpe": None,
                    "calmar": None,
                    "total_return": None,
                    "max_drawdown_pct": None,
                    "mean_bic": None,
                    "delta_mean_bic": None,
                    "mean_samples_per_param": None,
                    "verdict": "SKIPPED_STRUCTURAL_REQUIRED",
                }
            )
            continue

        reduced = tuple(f for f in subset if f != feature)
        logger.info("Ablation %d/%d: bỏ %s", i, len(subset), feature)
        run = _run_one_config(
            args, settings, reduced, ohlcv, symbol, start, end, out_root / f"ablation_drop_{feature}"
        )
        contribution = base_sharpe - run["metrics"]["sharpe"]
        rows.append(
            {
                "dropped_feature": feature,
                "n_features": len(reduced),
                "sharpe": run["metrics"]["sharpe"],
                "delta_sharpe": contribution,
                "calmar": run["metrics"]["calmar"],
                "total_return": run["metrics"]["total_return"],
                "max_drawdown_pct": run["metrics"]["max_drawdown_pct"],
                "mean_bic": run["mean_bic"],
                "delta_mean_bic": (
                    run["mean_bic"] - baseline["mean_bic"]
                    if run["mean_bic"] is not None and baseline["mean_bic"] is not None
                    else None
                ),
                "mean_samples_per_param": run["mean_samples_per_param"],
                # >= 0.1: bỏ ra làm Sharpe tụt đủ nhiều -> feature có đóng góp thật.
                "verdict": "KEEP" if contribution >= 0.1 else "DROP_CANDIDATE",
            }
        )

    import pandas as pd

    table = pd.DataFrame(rows)
    out_root.mkdir(parents=True, exist_ok=True)
    csv_path = out_root / "feature_ablation.csv"
    table.to_csv(csv_path, index=False)
    logger.info("Đã ghi %s", csv_path)

    return {"csv": str(csv_path), "rows": rows}


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.sweep:
        raise NotImplementedError("--sweep chưa được implement (quét tham số, §4.7).")

    if args.ablation:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        settings = load_settings(args.config)
        results = run_ablation(args, settings)
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    if args.backtest or args.compare:
        settings = load_settings(args.config)
        results = run_backtest(args, settings)
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    if args.stress_test:
        raise NotImplementedError("--stress-test: dùng backtest/stress_test.py trực tiếp cho tới Phase 8")

    # Live loop: Phase 10 trong đánh số prompts/.
    raise NotImplementedError("Live loop chưa được implement — dùng --backtest")


if __name__ == "__main__":
    main()
