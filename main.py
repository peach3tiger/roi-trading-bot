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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

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


_VALID_TIER2_FEATURES = frozenset(
    {
        "funding_rate",
        "funding_zscore_90",
        "oi_change_24h",
        "perp_spot_basis",
        "taker_buy_ratio",
    }
)


def parse_feature_subset(raw: str | None) -> tuple[str, ...] | None:
    """`None` = giữ nguyên cả 14 cột Tầng 1 (mặc định). Dùng cho ablation/
    feature-pruning thủ công — xem ghi chú ở `FeatureConfig.feature_subset`.
    Chấp nhận cả tên cột Tầng 2 (xem `needs_tier2_derivatives`) — không tự
    lấy toàn bộ 14+5 cột khi `raw` rỗng, chỉ Tầng 1 là mặc định như trước."""
    if not raw:
        return None
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    valid = _VALID_TIER1_FEATURES | _VALID_TIER2_FEATURES
    invalid = [n for n in names if n not in valid]
    if invalid:
        raise ValueError(f"--feature-subset có tên cột không hợp lệ: {invalid}")
    return names


def needs_tier2_derivatives(subset: tuple[str, ...] | None) -> bool:
    """True nếu subset yêu cầu ít nhất một cột Tầng 2 — kích hoạt tải
    derivatives và bật `FeatureConfig.tier2_derivatives`, KHÔNG đọc từ
    `settings.yaml` (subset là nguồn sự thật duy nhất cho thí nghiệm này,
    tránh phải sửa settings.yaml — một tham số — cho mỗi lần thử)."""
    return subset is not None and any(name in _VALID_TIER2_FEATURES for name in subset)


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


def build_risk_manager(settings: dict[str, Any], *, halt_lock_path: Path | None = None) -> Any:
    """`RiskManager(config: dict)` nhận thẳng `settings["risk"]` — khác quy
    ước "named Decimal params" của các builder khác, vì đó là chữ ký spec
    §5.7 (xem docstring core/risk_manager.py::RiskManager).

    `halt_lock_path=None` giữ mặc định của `RiskManager` (`trading_halted.lock`
    tại CWD) — `run_live_loop` truyền `${STATE_DIR}/trading_halted.lock`
    tường minh (khớp `ops/RUNBOOK.md`), test truyền `tmp_path/...`.
    """
    from core.risk_manager import RiskManager

    return RiskManager(settings["risk"], halt_lock_path=halt_lock_path)


def build_exchange_client(settings: dict[str, Any], *, testnet: bool) -> Any:
    """Credential đọc từ env `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` — KHÔNG
    BAO GIỜ từ settings.yaml/CLI (CLAUDE.md bất biến #6: không hardcode
    credentials, không log kể cả một phần). Đọc đúng tên biến hiện tại của
    `ops/health_check.py` sau khi bỏ fallback `BYBIT_*` (xem docs/DECISIONS.md)
    — dùng chung một quy ước tên biến giữa hai điểm vào."""
    import os

    from broker.ccxt_client import CCXTClient

    exch = settings["exchange"]
    return CCXTClient(
        exchange_id=exch["name"],
        symbol=exch["symbol"],
        api_key=os.environ.get("EXCHANGE_API_KEY") or None,
        api_secret=os.environ.get("EXCHANGE_API_SECRET") or None,
        testnet=testnet,
        quote_asset=exch["quote_asset"],
    )


def build_market_data_service(settings: dict[str, Any], exchange_client: Any) -> Any:
    from data.market_data import MarketDataService

    exch = settings["exchange"]
    return MarketDataService(exchange_client, exch["symbol"], exch["timeframe"])


def build_order_executor(settings: dict[str, Any], exchange_client: Any) -> Any:
    from broker.order_executor import OrderExecutor

    ex = settings["execution"]
    return OrderExecutor(
        exchange_client,
        limit_offset_pct=Decimal(str(ex["limit_offset_pct"])),
        timeout_seconds=int(ex["order_timeout_seconds"]),
    )


def build_signal_generator(
    settings: dict[str, Any], hmm_engine: Any, *, halt_lock_path: Path | None = None
) -> Any:
    from core.signal_generator import SignalGenerator

    return SignalGenerator(
        hmm_engine,
        build_trend_gate(settings),
        build_orchestrator(settings),
        build_risk_manager(settings, halt_lock_path=halt_lock_path),
    )


def build_alert_manager(settings: dict[str, Any]) -> Any:
    """Credential đọc từ env, KHÔNG từ settings.yaml (CLAUDE.md bất biến
    #6, cùng quy ước `build_exchange_client`). Kênh email chỉ bật khi
    `MONITORING_SMTP_HOST` có giá trị — thiếu MỘT trong các biến
    `MONITORING_SMTP_*`/`MONITORING_EMAIL_*` còn lại sẽ để trống ("")
    trong `EmailConfig`, KHÔNG raise ở đây (SMTP thật sự thất bại lúc gửi
    sẽ tự lộ ra qua log warning của `_send_email`, không cần validate hai
    lần); webhook tương tự, chỉ cần `MONITORING_WEBHOOK_URL`."""
    import os

    from monitoring.alerts import AlertManager, EmailConfig

    mon = settings["monitoring"]
    email_config = None
    smtp_host = os.environ.get("MONITORING_SMTP_HOST")
    if smtp_host:
        email_config = EmailConfig(
            smtp_host=smtp_host,
            smtp_port=int(os.environ.get("MONITORING_SMTP_PORT", "587")),
            username=os.environ.get("MONITORING_SMTP_USERNAME", ""),
            password=os.environ.get("MONITORING_SMTP_PASSWORD", ""),
            from_addr=os.environ.get("MONITORING_EMAIL_FROM", ""),
            to_addr=os.environ.get("MONITORING_EMAIL_TO", ""),
        )

    return AlertManager(
        rate_limit_seconds=int(mon["alert_rate_limit_seconds"]),
        email_config=email_config,
        webhook_url=os.environ.get("MONITORING_WEBHOOK_URL") or None,
        log_dir=mon["log_dir"],
    )


# ----------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------


def run_backtest(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    """Một lần chạy walk-forward cho mỗi bar-offset, xuất báo cáo mỗi lần.

    Nhiều offset ghi vào thư mục con riêng: tiêu chí 6 của §4.9 so sánh Sharpe
    GIỮA các offset, nên ghi đè lên nhau sẽ xoá mất chính thứ cần đo.
    """
    from dataclasses import replace as _replace

    from backtest.backtester import WalkForwardBacktester
    from backtest.performance import write_reports
    from data.history_loader import HistoryLoader

    start, end = resolve_date_range(args)
    offsets = parse_bar_offsets(args.bar_offset)
    symbol = args.symbol or settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"

    subset = parse_feature_subset(args.feature_subset)
    wf_config = build_walk_forward_config(settings)
    feature_config = build_feature_config(settings, feature_subset=subset)
    use_tier2 = needs_tier2_derivatives(subset)
    if use_tier2:
        # settings.yaml tier2_derivatives có thể vẫn là False (mặc định) —
        # subset là nguồn sự thật cho thí nghiệm này, xem needs_tier2_derivatives.
        feature_config = _replace(feature_config, tier2_derivatives=True)
        if offsets != [0]:
            raise ValueError(
                "Tầng 2 (funding/OI/perp) chỉ hỗ trợ bar_offset_hours=0 — "
                "derivatives_loader.py không có tham số timeZone như Binance klines."
            )

    data_start = resolve_data_start(args, start, settings, wf_config.is_bars)

    loader = HistoryLoader()
    results: dict[str, Any] = {}

    derivatives = None
    if use_tier2:
        from data.derivatives_loader import DerivativesLoader

        bybit_symbol = symbol if "/" not in symbol else symbol.replace("/", "")
        derivatives = DerivativesLoader().load_tier2_bundle(bybit_symbol, data_start, end)

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
        result = backtester.run(symbol, ohlcv, start, end, derivatives=derivatives)

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
    derivatives: Any = None,
) -> dict[str, Any]:
    """Một lần walk-forward với đúng `subset` feature, trả về metric + BIC trung bình.

    `derivatives` truyền từ caller (đã tải một lần, dùng chung cho mọi cấu
    hình ablation) — mỗi lần gọi tự quyết định có cần Tầng 2 hay không dựa
    trên `subset` CỦA CHÍNH NÓ (vd. khi ablation bỏ đúng cột Tầng 2 cuối
    cùng, `subset` đó không còn cần derivatives nữa dù baseline có cần).
    """
    from dataclasses import replace as _replace

    from backtest.backtester import WalkForwardBacktester
    from backtest.performance import write_reports

    wf_config = build_walk_forward_config(settings)
    feature_config = build_feature_config(settings, feature_subset=subset)
    use_tier2 = needs_tier2_derivatives(subset)
    if use_tier2:
        feature_config = _replace(feature_config, tier2_derivatives=True)
    backtester = WalkForwardBacktester(
        hmm_engine=build_hmm_engine(settings, min_train_bars=wf_config.is_bars),
        strategy_orchestrator=build_orchestrator(settings),
        trend_gate=build_trend_gate(settings, enabled=not args.no_trend_gate),
        cost_model=build_cost_model(settings),
        config=wf_config,
        feature_config=feature_config,
    )
    result = backtester.run(symbol, ohlcv, start, end, derivatives=derivatives if use_tier2 else None)
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
    if needs_tier2_derivatives(subset) and offsets != [0]:
        raise ValueError(
            "Tầng 2 (funding/OI/perp) chỉ hỗ trợ bar_offset_hours=0 — "
            "derivatives_loader.py không có tham số timeZone như Binance klines."
        )

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

    derivatives = None
    if needs_tier2_derivatives(subset):
        from data.derivatives_loader import DerivativesLoader

        bybit_symbol = symbol if "/" not in symbol else symbol.replace("/", "")
        derivatives = DerivativesLoader().load_tier2_bundle(bybit_symbol, data_start, end)

    out_root = Path(args.output_dir)

    logger = logging.getLogger(__name__)
    logger.info("Ablation: %d feature -> %d lần chạy walk-forward", len(subset), len(subset) + 1)

    baseline = _run_one_config(
        args, settings, subset, ohlcv, symbol, start, end, out_root / "ablation_baseline", derivatives
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
            args,
            settings,
            reduced,
            ohlcv,
            symbol,
            start,
            end,
            out_root / f"ablation_drop_{feature}",
            derivatives,
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


# ----------------------------------------------------------------------
# Live loop — Phase 10 (prompts/phase-10-main-loop.md)
# ----------------------------------------------------------------------

# Đủ cho EMA50/ATR14 hội tụ mà không phải tính lại toàn bộ lịch sử mỗi
# bar — khớp forward/logger.py::_STRATEGY_BARS_LOOKBACK (cùng lý do, hai
# module không dùng chung hằng số có chủ đích, xem _latest_closed_bar_date
# dưới đây).
_STRATEGY_BARS_LOOKBACK = 300


def _latest_closed_bar_date(now: datetime) -> Any:
    """Bar ngày gần nhất đã ĐÓNG tại thời điểm `now` — CLAUDE.md bất biến
    #10, ranh giới ngày LUÔN 00:00 UTC, không có khái niệm giờ giao dịch.

    Trùng logic `forward/logger.py::latest_closed_bar_date` — CỐ TÌNH
    không import từ đó: `forward/` tự cô lập hoàn toàn khỏi phần còn lại
    của hệ thống (docstring `forward/logger.py`: "KHÔNG import broker.* ở
    bất cứ đâu trong module này"), và live loop không nên phụ thuộc NGƯỢC
    vào forward/ dù chỉ một hàm thuần vô hại — trùng lặp 4 dòng còn rẻ hơn
    phá vỡ ranh giới đó (forward test là thí nghiệm tiền đăng ký 12 tháng,
    xem docs/DECISIONS.md).
    """
    import pandas as pd

    now_utc = now.astimezone(timezone.utc)
    today = pd.Timestamp(now_utc.date(), tz="UTC")
    return today - pd.Timedelta(days=1)


def compute_bars_behind(last_processed_bar: str | None, now: datetime) -> int:
    """Số bar giữa bar ĐÃ ĐÓNG mới nhất (tính tới `now`) và
    `last_processed_bar` đã lưu — 0 nghĩa là đồng bộ.

    THUẦN, tính lại mỗi lần gọi — KHÔNG lưu kết quả vào `state_snapshot.json`
    (xem `monitoring/dashboard.py::DashboardState.bars_behind`): một giá
    trị lưu sẵn từ lần `process_one_bar()` thành công gần nhất sẽ đứng yên
    ở "0" ngay cả khi tiến trình chính đã chết từ lâu — đúng lúc field này
    tồn tại để báo động. `--dashboard` (khi wire xong, xem docs/STATE.md)
    gọi hàm này tại THỜI ĐIỂM RENDER, dùng đồng hồ thật của tiến trình đọc
    dashboard, không phải đồng hồ của tiến trình `run_live_loop` lúc ghi
    snapshot lần cuối.
    """
    import pandas as pd

    if last_processed_bar is None:
        return 0

    latest = _latest_closed_bar_date(now)
    last = pd.Timestamp(last_processed_bar, tz="UTC")
    return max(0, int((latest - last).days))


@dataclass
class LiveLoopState:
    """Ghi vào `state_snapshot.json` MỖI BAR (spec Phase 7 — không chỉ lúc
    thoát): thị trường 24/7, tiến trình CÓ THỂ crash bất cứ lúc nào giữa
    hai bar, và khôi phục sai `current_stop_loss` sau restart có thể âm
    thầm vi phạm CLAUDE.md bất biến #5 (xem
    broker/order_executor.py::restore_known_stop).
    """

    last_processed_bar: str | None  # ISO date "2026-08-06", None = chưa xử lý bar nào
    current_stop_loss: str | None  # str(Decimal), None = đang flat
    current_allocation_pct: str | None  # str(Decimal)
    current_regime_id: int | None
    current_regime_label: str | None
    session_started_at_utc: str  # đặt lại mỗi lần run_live_loop() khởi động, KHÔNG khôi phục từ snapshot cũ
    written_at_utc: str
    # str(Decimal), cộng dồn TOÀN BỘ phiên (không reset theo ngày/tháng —
    # dashboard tự trừ để ra "phí tháng này", xem build_dashboard_state()).
    # default="0" (không phải field bắt buộc) để snapshot cũ (trước khi
    # field này tồn tại) vẫn load được qua load_state_snapshot() — thiếu
    # field mới trong JSON cũ dùng default, KHÔNG bị coi là "hỏng" (xem
    # docstring load_state_snapshot bên dưới).
    cumulative_fees_paid: str = "0"
    # StructureState.value ("BULL_STRUCTURE"/"TRANSITION"/"BEAR_STRUCTURE")
    # của lần tính gần nhất — None = chưa tính lần nào (phiên mới/snapshot
    # cũ trước field này tồn tại). Dùng để phát AlertType.TREND_GATE_CHANGE
    # (so với giá trị MỚI tính trong cùng bar, xem _fire_bar_alerts) mà
    # không cần một biến rời sống ngoài LiveLoopState — cùng kỹ thuật đã
    # dùng cho current_regime_id (so state.current_regime_id CŨ với
    # regime_id MỚI ngay trong process_one_bar()).
    current_trend_structure: str | None = None
    # Poll telemetry (2026-08-07) — REST polling thay WebSocket (xem
    # docs/DECISIONS.md "Đổi sàn Bybit -> Binance"), nên KHÔNG có khái
    # niệm "đang kết nối"/"bao lâu kể từ tin nhắn cuối" như trước. Hai
    # field dưới đây thay thế đúng vai trò "cho biết feed dữ liệu còn
    # sống không" bằng ngôn ngữ đúng kiến trúc: thời điểm VÀ độ trễ của
    # lần fetch OHLCV thật gần nhất (`history_loader.load()` trong
    # `run_live_loop()`), KHÔNG phải mỗi lần lặp vòng poll — phần lớn chu
    # kỳ 60s không có bar mới nên không gọi mạng, xem docstring
    # run_live_loop. None = chưa poll lần nào (phiên mới/snapshot cũ
    # trước hai field này tồn tại).
    last_poll_at: str | None = None
    poll_latency_ms: float | None = None
    # Lệch đồng hồ (2026-08-07) — monitoring/clock.py::measure_clock_drift(),
    # đo mỗi bar trong process_one_bar() khi alert_manager được truyền.
    # Persist để một tiến trình --dashboard riêng (đọc snapshot, không
    # chạy chung process với run_live_loop) có dữ liệu thật cho
    # DashboardState.clock_drift_ms — KHÁC bars_behind (main.py::
    # compute_bars_behind, cố tình KHÔNG persist): drift đồng hồ không tự
    # "trôi thêm" giữa hai lần đo như độ trễ bar so với hiện tại, nên một
    # giá trị đo gần nhất vẫn còn ý nghĩa tham khảo dù hơi cũ, không nói
    # dối theo kiểu "đứng yên ở 0" mà bars_behind mắc phải.
    last_clock_drift_ms: float | None = None
    last_clock_round_trip_ms: float | None = None
    last_clock_check_at: str | None = None


def write_state_snapshot(path: Path, state: LiveLoopState) -> None:
    """Ghi NGUYÊN TỬ (tmp file + rename) — tiến trình crash đúng giữa lúc
    ghi không được để lại `state_snapshot.json` nửa vời (JSON hỏng khiến
    lần khởi động sau không đọc được, mất hết trạng thái đã biết)."""
    from dataclasses import asdict

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_state_snapshot(path: Path) -> LiveLoopState | None:
    """`None` nếu chưa tồn tại HOẶC hỏng — coi như phiên mới, không raise
    (một `state_snapshot.json` hỏng không được phép chặn khởi động lại,
    chỉ mất khả năng khôi phục allocation/stop đã biết)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LiveLoopState(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("%s hỏng/không đọc được (%s) — bỏ qua, coi như phiên mới.", path, exc)
        return None


def _extract_fee_paid(order_result: Any) -> Decimal:
    """Phí THẬT trả cho lệnh vừa khớp, đọc từ `OrderResult.raw_response`
    (dict thô ccxt trả về nguyên văn từ `create_order`) — KHÔNG ước lượng
    bằng `costs.taker_fee_pct` (đó là con số cho BACKTEST, nơi không có phí
    thật để đọc; ở live, luôn ưu tiên số sàn thật sự tính, xem CLAUDE.md
    bất biến #7 "mọi báo cáo phải in phí đã trả THEO USDT").

    ccxt chuẩn hoá cấu trúc order trả về, phí nằm ở MỘT trong hai chỗ tuỳ
    sàn/loại lệnh:
      - `fee`: {"cost": ..., "currency": ...} — một khoản phí duy nhất.
      - `fees`: [{"cost": ..., "currency": ...}, ...] — nhiều khoản (lệnh
        khớp từng phần qua nhiều lần khớp, mỗi lần một dòng phí).
    Trả `Decimal("0")` nếu không có field nào (lệnh NEW chưa khớp — market
    order Binance spot trả `fee` ngay khi khớp đồng bộ, nhưng LIMIT có thể
    chưa khớp lúc response trả về) — KHÔNG raise, "chưa biết phí" khác
    "phí bằng 0 thật", nhưng caller không có cách phân biệt hai điều đó từ
    mỗi lần gọi riêng lẻ; ghi nhận giới hạn này, không bịa số.
    """
    raw = getattr(order_result, "raw_response", None) or {}
    fee = raw.get("fee")
    if isinstance(fee, dict) and fee.get("cost") is not None:
        return Decimal(str(fee["cost"]))

    fees = raw.get("fees")
    if isinstance(fees, list) and fees:
        total = Decimal("0")
        for entry in fees:
            if isinstance(entry, dict) and entry.get("cost") is not None:
                total += Decimal(str(entry["cost"]))
        return total

    return Decimal("0")


def _fire_bar_alerts(
    *,
    alert_manager: Any,
    signal_generator: Any,
    state: LiveLoopState,
    result: Any,
    regime_id: int,
    regime_label: str,
    new_trend_structure: str,
    large_pnl_alert_pct: Decimal,
) -> None:
    """Phát các cảnh báo suy được TRỰC TIẾP từ một lần gọi
    `signal_generator.generate()` — REGIME_CHANGE, TREND_GATE_CHANGE,
    FLICKER_THRESHOLD_EXCEEDED, CIRCUIT_BREAKER, LARGE_PNL (Phase 11,
    prompts/phase-11-monitoring.md). `AlertManager.send()` tự đảm bảo
    không bao giờ raise (xem monitoring/alerts.py), nên hàm này không cần
    try/except riêng.

    So sánh "đổi" dựa trên `state` (giá trị CŨ, đầu vào của bar này) với
    giá trị MỚI vừa tính trong CHÍNH bar này — không cần biến rời sống
    ngoài `process_one_bar()`, cùng kỹ thuật `current_regime_id` đã dùng
    từ Phase 10.

    KHÔNG bao gồm ABNORMAL_SPREAD/DATA_FEED_LOST/API_LOST/HMM_RETRAINED —
    những cái cần đọc mạng ngoài phạm vi một lần generate(), xem
    run_live_loop()/process_one_bar() gọi riêng. STABLECOIN_DEPEG/
    CLOCK_DRIFT: `AlertType` đã có sẵn giá trị (kích hoạt thủ công được),
    nhưng CHƯA wire liên tục ở đây — thiếu nguồn giá tham chiếu USDT/USD
    đáng tin (depeg) và cần `ExchangeClient.get_server_time()` chưa tồn
    tại (clock drift); ghi nhận là khoảng trống đã biết ở docs/STATE.md,
    không âm thầm giả định.
    """
    from monitoring.alerts import Alert, AlertType

    if state.current_regime_id is not None and state.current_regime_id != regime_id:
        alert_manager.send(
            Alert(
                AlertType.REGIME_CHANGE,
                f"{state.current_regime_label} -> {regime_label} "
                f"(id {state.current_regime_id} -> {regime_id})",
                severity="INFO",
            )
        )

    if state.current_trend_structure is not None and state.current_trend_structure != new_trend_structure:
        alert_manager.send(
            Alert(
                AlertType.TREND_GATE_CHANGE,
                f"{state.current_trend_structure} -> {new_trend_structure}",
                severity="INFO",
            )
        )

    if result.is_flickering:
        alert_manager.send(
            Alert(
                AlertType.FLICKER_THRESHOLD_EXCEEDED,
                f"Regime flicker rate vượt ngưỡng (regime hiện tại: {regime_label}).",
                severity="WARNING",
            )
        )

    breaker_history = signal_generator.risk_manager.circuit_breaker.get_history()
    if breaker_history:
        breaker = breaker_history[-1]
        if breaker.level.value != "NONE":
            alert_manager.send(
                Alert(
                    AlertType.CIRCUIT_BREAKER,
                    f"{breaker.level.value} — daily_dd={breaker.daily_dd:.2f}% "
                    f"weekly_dd={breaker.weekly_dd:.2f}% peak_dd={breaker.peak_dd:.2f}%",
                    severity="ERROR" if "HALT" in breaker.level.value else "WARNING",
                )
            )
        if breaker.daily_dd >= large_pnl_alert_pct:
            # Chiều LỖ — xem chú thích monitoring.large_pnl_alert_pct ở
            # config/settings.yaml cho lý do chưa phát hiện chiều LÃI lớn.
            alert_manager.send(
                Alert(
                    AlertType.LARGE_PNL,
                    f"Daily drawdown {breaker.daily_dd:.2f}% >= ngưỡng cảnh báo {large_pnl_alert_pct}%.",
                    severity="WARNING",
                )
            )


def _check_spread_and_alert(
    *, alert_manager: Any, risk_manager: Any, exchange_client: Any, symbol: str
) -> None:
    """ABNORMAL_SPREAD (spread rộng bất thường, đọc `RiskManager.check_spread()`
    đã có sẵn từ §5.4 nhưng CHƯA từng được `validate_signal()` gọi tới —
    xem ghi chú docs/STATE.md) và DATA_FEED_LOST (không lấy được orderbook)
    — hai cảnh báo duy nhất cần một lượt gọi mạng NGOÀI những gì
    `signal_generator.generate()` đã tự làm, nên tách khỏi `_fire_bar_alerts()`.

    Không raise ra ngoài: một lần fetch orderbook lỗi (mất feed) tự nó LÀ
    điều cần cảnh báo (DATA_FEED_LOST), không phải lý do làm crash bar
    đang xử lý — bar vẫn tiếp tục dùng OHLCV/feature đã tải được.
    """
    from monitoring.alerts import Alert, AlertType

    try:
        orderbook = exchange_client.get_orderbook(symbol)
    except Exception as exc:
        alert_manager.send(
            Alert(AlertType.DATA_FEED_LOST, f"Không lấy được orderbook {symbol}: {exc}", severity="ERROR")
        )
        return

    if not risk_manager.check_spread(orderbook.best_bid, orderbook.best_ask):
        alert_manager.send(
            Alert(
                AlertType.ABNORMAL_SPREAD,
                f"Spread {symbol} bất thường: bid={orderbook.best_bid} ask={orderbook.best_ask} "
                f"(trần {risk_manager.spread_max_pct}%)",
                severity="WARNING",
            )
        )


def _check_clock_drift(
    *,
    alert_manager: Any,
    exchange_client: Any,
    regime_state_logger: Any | None,
    clock_drift_alert_ms: Decimal,
    clock_drift_halt_ms: Decimal,
) -> tuple[bool, Any]:
    """Đo lệch đồng hồ qua `monitoring.clock.measure_clock_drift()`, ghi
    log MỖI LẦN gọi (kể cả không vượt ngưỡng nào — spec: "mỗi bar, phát
    ClockCheck vào log"), cảnh báo `AlertType.CLOCK_DRIFT` nếu vượt
    `clock_drift_alert_ms`.

    Trả `(halted, clock_check)` — `halted=True` nếu vượt
    `clock_drift_halt_ms` (caller dừng gửi lệnh bar này); `clock_check` là
    `monitoring.clock.ClockCheck` đo được, hoặc `None` nếu bản thân phép
    đo lỗi — caller dùng nó để cập nhật telemetry vào `LiveLoopState`
    (`last_clock_drift_ms`/`last_clock_round_trip_ms`/`last_clock_check_at`).

    Không raise ra ngoài: nếu `exchange_client.get_server_time()` raise
    (mất mạng, sàn lỗi), trả `(False, None)` — KHÔNG halt. Cố ý không coi
    "đo lệch giờ thất bại" là lý do để dừng giao dịch — đó là việc của
    DATA_FEED_LOST/API_LOST (`_check_spread_and_alert`, catch-all của
    `run_live_loop`), không phải CLOCK_DRIFT: một giá trị đo được RÕ RÀNG
    khác về bản chất với một phép đo không thực hiện được.
    """
    from monitoring.alerts import Alert, AlertType
    from monitoring.clock import measure_clock_drift

    try:
        check = measure_clock_drift(exchange_client.get_server_time)
    except Exception as exc:
        logger.warning("Không đo được lệch đồng hồ bar này: %s", exc)
        return False, None

    if regime_state_logger is not None:
        regime_state_logger.info(
            "clock_check",
            extra={
                "event": "clock_check",
                "drift_ms": check.drift_ms,
                "round_trip_ms": check.round_trip_ms,
                "measured_at": check.measured_at.isoformat(),
            },
        )

    abs_drift = Decimal(str(abs(check.drift_ms)))
    halted = abs_drift > clock_drift_halt_ms
    if abs_drift > clock_drift_alert_ms:
        alert_manager.send(
            Alert(
                AlertType.CLOCK_DRIFT,
                f"Lệch đồng hồ {check.drift_ms:.0f}ms (round-trip {check.round_trip_ms:.0f}ms)"
                + (" — ĐÃ DỪNG gửi lệnh mới" if halted else ""),
                severity="ERROR" if halted else "WARNING",
            )
        )

    return halted, check


def process_one_bar(
    *,
    symbol: str,
    signal_generator: Any,
    order_executor: Any,
    position_tracker: Any,
    ohlcv: Any,
    features: Any,
    bar_ts: Any,
    state: LiveLoopState,
    dry_run: bool,
    alert_manager: Any | None = None,
    regime_state_logger: Any | None = None,
    large_pnl_alert_pct: Decimal = Decimal("2.0"),
    clock_drift_alert_ms: Decimal = Decimal("1000"),
    clock_drift_halt_ms: Decimal = Decimal("2500"),
) -> LiveLoopState:
    """Xử lý ĐÚNG MỘT bar đã đóng. Tách khỏi vòng lặp poll để test được
    bằng fake, không cần `time.sleep`/vòng lặp vô hạn thật — xem
    tests/test_main_loop.py.

    Thứ tự (spec Phase 7 §Vòng lặp chính, điều chỉnh cho REST polling —
    không có bước "nhận bar qua WebSocket", xem docs/DECISIONS.md mục
    "Đổi sàn Bybit -> Binance (ccxt)"):
    1. Reset ranh giới ngày/tuần của risk_manager (CLAUDE.md bất biến #10)
       — mỗi bar LÀ một ngày mới (timeframe 1D) nên reset_daily() mỗi
       bar; reset_weekly() khi bar rơi đúng Thứ Hai.
    2. Kiểm tra stop loss đã biết có bị breach chưa — spot KHÔNG có stop
       order native trên sàn (§ ghi chú broker/order_executor.py), bot
       phải tự theo dõi mỗi bar bằng giá đóng.
    3. Breach -> đóng vị thế, DỪNG ở đây, không sinh signal mới bar này.
    4. Không breach -> SignalGenerator.generate() -> RiskDecision.
    5. approved -> submit_order (trừ dry_run) + modify_stop.
       rejected -> chỉ log lý do, KHÔNG đổi allocation/stop đang có.
    6. position_tracker.poll() đối soát lại (trừ dry_run — không có lệnh
       thật nào vừa gửi để đối soát).

    KHÔNG tự ghi `state_snapshot.json` — caller (`run_live_loop`) làm việc
    đó, để hàm này test được thuần bằng cách so state trả về, không phải
    đọc lại filesystem.

    Lỗi HMM ("giữ nguyên regime hiện tại", spec §Xử lý lỗi): KHÔNG bắt
    riêng ở đây — nếu `signal_generator.generate()` raise, hàm này raise
    theo, và caller (`run_live_loop`) bắt ở vòng ngoài rồi GIỮ NGUYÊN
    `state` đã trả về ở lần gọi trước (không ghi đè) — hiệu quả giống hệt
    "giữ nguyên regime" mà không cần hai lớp try/except lồng nhau cho
    cùng một kết quả.

    `alert_manager`/`regime_state_logger` (Phase 11, monitoring/) mặc định
    `None` — KHÔNG đổi hành vi hàm này với 23 test đã có ở
    tests/test_main_loop.py trước Phase 11 (không truyền hai tham số này).
    Khi được truyền (chỉ `run_live_loop()` truyền trong vận hành thật):
      - `regime_state_logger`: ghi MỘT dòng JSON (`monitoring.logger.log_state`)
        mỗi bar KHÔNG breach — bỏ qua nhánh breach (regime/probability lúc
        đó không được tính lại, xem code, thà thiếu một dòng log còn hơn
        log lại giá trị regime CŨ dán nhãn nhầm là "vừa tính").
      - `alert_manager`: xem `_fire_bar_alerts()` cho danh sách đầy đủ
        loại cảnh báo được phát từ bar này.

    Lệch đồng hồ (`clock_drift_halt_ms`, mặc định 2500ms): kiểm tra TRƯỚC
    CẢ bước 2 (stop-loss breach) — nếu vượt ngưỡng, hàm DỪNG NGAY ở đây,
    KHÔNG đóng vị thế dù đang breach, KHÔNG sinh signal mới. Lý do: mọi
    request ký (kể cả `close_position()`) sẽ bị sàn từ chối với `-1021`
    khi đồng hồ lệch đủ xa recvWindow — cố gắng đóng vị thế lúc đó chỉ tốn
    một lệnh gọi mạng thất bại chắc chắn, và giữ nguyên vị thế/stop hiện
    có (không thử, không đổi) an toàn hơn một nỗ lực thất bại giữa chừng.
    Đánh đổi CÓ CHỦ Ý: một breach stop-loss THẬT trong đúng bar này sẽ
    KHÔNG được enforce cho tới khi đồng hồ đồng bộ lại — ghi lại rõ ràng ở
    đây vì đây là hạn chế thật của thiết kế, không phải sơ suất.
    """
    risk_manager = signal_generator.risk_manager
    risk_manager.reset_daily()
    if bar_ts.weekday() == 0:  # Thứ Hai — CLAUDE.md bất biến #10
        risk_manager.reset_weekly()

    close_price = Decimal(str(ohlcv.loc[bar_ts, "close"]))
    now_iso = datetime.now(timezone.utc).isoformat()
    cumulative_fees = Decimal(state.cumulative_fees_paid) if state.cumulative_fees_paid else Decimal("0")

    clock_drift_ms = state.last_clock_drift_ms
    clock_round_trip_ms = state.last_clock_round_trip_ms
    clock_checked_at = state.last_clock_check_at
    if alert_manager is not None:
        halted, clock_check = _check_clock_drift(
            alert_manager=alert_manager,
            exchange_client=order_executor.exchange_client,
            regime_state_logger=regime_state_logger,
            clock_drift_alert_ms=clock_drift_alert_ms,
            clock_drift_halt_ms=clock_drift_halt_ms,
        )
        if clock_check is not None:
            clock_drift_ms = clock_check.drift_ms
            clock_round_trip_ms = clock_check.round_trip_ms
            clock_checked_at = clock_check.measured_at.isoformat()
        if halted:
            logger.error(
                "Lệch đồng hồ vượt ngưỡng dừng lệnh (%sms) — GIỮ NGUYÊN vị thế/stop bar này, "
                "KHÔNG gửi lệnh mới.",
                clock_drift_halt_ms,
            )
            return replace(
                state,
                last_processed_bar=bar_ts.date().isoformat(),
                written_at_utc=now_iso,
                cumulative_fees_paid=str(cumulative_fees),
                last_clock_drift_ms=clock_drift_ms,
                last_clock_round_trip_ms=clock_round_trip_ms,
                last_clock_check_at=clock_checked_at,
            )

    current_stop = Decimal(state.current_stop_loss) if state.current_stop_loss else None
    if current_stop is not None and close_price <= current_stop:
        logger.warning(
            "STOP LOSS BREACH %s: giá đóng %s <= stop %s — đóng vị thế.", symbol, close_price, current_stop
        )
        if dry_run:
            logger.info("[DRY-RUN] sẽ đóng vị thế %s (stop breach) — không đặt lệnh thật.", symbol)
        else:
            result = order_executor.close_position(symbol)
            logger.info(
                "close_position(%s) -> order_id=%s status=%s", symbol, result.order_id, result.status
            )
            cumulative_fees += _extract_fee_paid(result)
            position_tracker.poll()
        return replace(
            state,
            last_processed_bar=bar_ts.date().isoformat(),
            current_stop_loss=None,
            current_allocation_pct="0",
            cumulative_fees_paid=str(cumulative_fees),
            written_at_utc=now_iso,
            last_clock_drift_ms=clock_drift_ms,
            last_clock_round_trip_ms=clock_round_trip_ms,
            last_clock_check_at=clock_checked_at,
        )

    current_allocation = (
        Decimal(state.current_allocation_pct) if state.current_allocation_pct else Decimal("0")
    )
    bars_window = ohlcv.loc[:bar_ts].tail(_STRATEGY_BARS_LOOKBACK)
    features_so_far = features.loc[:bar_ts]

    if alert_manager is not None:
        _check_spread_and_alert(
            alert_manager=alert_manager,
            risk_manager=risk_manager,
            exchange_client=order_executor.exchange_client,
            symbol=symbol,
        )

    balance = order_executor.exchange_client.get_balance()
    positions = {p.symbol: p for p in order_executor.exchange_client.get_positions()}
    qty = positions[symbol].qty if symbol in positions else Decimal("0")
    equity = balance.total + qty * close_price
    # daily_pnl/weekly_pnl/peak_equity/drawdown: KHÔNG được risk_manager.
    # validate_signal() đọc (nó tự theo dõi drawdown NỘI BỘ qua
    # circuit_breaker.update(portfolio_state.equity, ...) — chỉ trường
    # .equity thật sự ảnh hưởng quyết định, xác nhận bằng đọc lại
    # core/risk_manager.py). Điền cho mục đích log/dashboard tương lai
    # (Phase 8), không phải vì validate_signal cần chúng.
    from core.risk_manager import PortfolioState

    portfolio_state = PortfolioState(
        equity=equity,
        cash=balance.available,
        available_balance=balance.available,
        positions=positions,
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        peak_equity=equity,
        drawdown=Decimal("0"),
        circuit_breaker_status={},
        flicker_rate=signal_generator.hmm_engine.get_regime_flicker_rate(),
    )

    result = signal_generator.generate(
        symbol, features_so_far, bars_window, current_allocation, portfolio_state
    )
    decision = result.decision
    regime_id = result.regime_state.state_id
    regime_label = result.regime_state.label
    new_trend_structure = signal_generator.trend_gate.get_structure_state(bars_window).value

    if alert_manager is not None:
        _fire_bar_alerts(
            alert_manager=alert_manager,
            signal_generator=signal_generator,
            state=state,
            result=result,
            regime_id=regime_id,
            regime_label=regime_label,
            new_trend_structure=new_trend_structure,
            large_pnl_alert_pct=large_pnl_alert_pct,
        )

    if decision.approved:
        signal = decision.modified_signal
        assert signal is not None
        if decision.modifications:
            logger.info("Signal sửa bởi risk_manager: %s", "; ".join(decision.modifications))
        if dry_run:
            logger.info(
                "[DRY-RUN] signal %s %s allocation=%s stop=%s regime=%s(%s) — KHÔNG đặt lệnh thật.",
                signal.symbol,
                signal.direction,
                signal.target_allocation_pct,
                signal.stop_loss,
                regime_id,
                regime_label,
            )
        else:
            order_result = order_executor.submit_order(signal)
            logger.info(
                "submit_order -> order_id=%s order_link_id=%s status=%s filled_qty=%s",
                order_result.order_id,
                order_result.order_link_id,
                order_result.status,
                order_result.filled_qty,
            )
            cumulative_fees += _extract_fee_paid(order_result)
            applied = order_executor.modify_stop(signal.symbol, signal.stop_loss)
            if not applied:
                logger.info("modify_stop(%s): không siết chặt hơn stop hiện tại — giữ nguyên.", signal.symbol)
            position_tracker.poll()
        new_stop = signal.stop_loss
        new_allocation = signal.target_allocation_pct
    else:
        logger.warning("Signal bị risk_manager TỪ CHỐI: %s", decision.rejection_reason)
        new_stop = current_stop
        new_allocation = current_allocation

    if regime_state_logger is not None:
        # Ghi SAU khi cumulative_fees đã cộng phí lệnh của CHÍNH bar này
        # (nếu có submit_order/close_position ở trên) — log ĐÚNG lúc mọi
        # phép cộng phí của bar đã xong, không phải giá trị "trước khi vào
        # bar", tránh entry log trễ một bar so với hành động thật.
        from monitoring.logger import log_state

        log_state(
            regime_state_logger,
            regime=regime_label,
            probability=result.regime_state.probability,
            equity=equity,
            positions=positions,
            daily_pnl=portfolio_state.daily_pnl,
            cumulative_fees_paid=cumulative_fees,
        )

    return LiveLoopState(
        last_processed_bar=bar_ts.date().isoformat(),
        current_stop_loss=str(new_stop) if new_stop is not None else None,
        current_allocation_pct=str(new_allocation),
        current_regime_id=regime_id,
        current_regime_label=regime_label,
        session_started_at_utc=state.session_started_at_utc,
        written_at_utc=now_iso,
        cumulative_fees_paid=str(cumulative_fees),
        current_trend_structure=new_trend_structure,
        last_clock_drift_ms=clock_drift_ms,
        last_clock_round_trip_ms=clock_round_trip_ms,
        last_clock_check_at=clock_checked_at,
    )


def run_live_loop(args: argparse.Namespace, settings: dict[str, Any]) -> None:
    """Khởi động (spec Phase 7 §Khởi động, 10 bước) rồi vòng lặp poll REST
    vĩnh viễn (không có "chờ thị trường mở" — CLAUDE.md bất biến #10).
    """
    import os
    import signal as os_signal
    import time

    import pandas as pd

    dry_run: bool = args.dry_run
    testnet = not args.live
    symbol = settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
    poll_interval = int(settings["execution"]["poll_interval_seconds"])
    retrain_interval_days = int(settings["hmm"]["retrain_interval_days"])
    clock_drift_alert_ms = Decimal(str(settings["monitoring"]["clock_drift_alert_ms"]))
    clock_drift_halt_ms = Decimal(str(settings["monitoring"]["clock_drift_halt_ms"]))

    state_dir = Path(os.environ.get("STATE_DIR", "state"))
    model_path = Path(os.environ.get("MODEL_PATH", "models/hmm_model.pkl"))
    snapshot_path = state_dir / "state_snapshot.json"
    halt_lock_path = state_dir / "trading_halted.lock"

    logger.info("=" * 60)
    logger.info(
        "regime-trader-crypto — khởi động (%s, dry_run=%s)", "testnet" if testnet else "MAINNET", dry_run
    )

    # Bước 8 (spec) — kiểm tra TRƯỚC KHI làm gì khác cần mạng/tiền. Risk
    # manager cũng tự kiểm tra lại mỗi validate_signal() (core/risk_manager.py::
    # _is_halted) — cổng ở đây chặn SỚM HƠN, trước khi tốn một round-trip
    # mạng nào, đúng tinh thần "fail loud" của spec.
    if halt_lock_path.exists():
        print(f"TỪ CHỐI KHỞI ĐỘNG: {halt_lock_path} tồn tại.")
        print(halt_lock_path.read_text(encoding="utf-8"))
        print("Xoá file này thủ công sau khi đã xem xét trước khi chạy lại (Brain-Crypto-Bybit.md §5.2).")
        sys.exit(1)

    # Bước 1-2 — kết nối + xác minh tài khoản + đồng bộ thời gian. Uỷ
    # quyền cho ops/health_check.py (đã kiểm chứng bằng mạng thật, xem
    # docs/STATE.md) thay vì viết lại cùng logic lần hai.
    from ops.health_check import check_exchange_authenticated, check_exchange_reachable

    reachable = check_exchange_reachable(Path(args.config))
    logger.info("[%s] exchange_reachable: %s", reachable.status, reachable.detail)
    if reachable.status == "FAIL":
        print(f"TỪ CHỐI KHỞI ĐỘNG: không kết nối được sàn — {reachable.detail}")
        sys.exit(1)

    if not dry_run:
        authenticated = check_exchange_authenticated(Path(args.config))
        logger.info("[%s] exchange_authenticated: %s", authenticated.status, authenticated.detail)
        if authenticated.status == "FAIL":
            print(f"TỪ CHỐI KHỞI ĐỘNG: xác thực sàn thất bại — {authenticated.detail}")
            sys.exit(1)
    else:
        logger.info("--dry-run: bỏ qua exchange_authenticated (không cần đặt lệnh thật).")

    exchange_client = build_exchange_client(settings, testnet=testnet)

    # Lệch đồng hồ (2026-08-07) — kiểm tra CHÍNH XÁC hơn WARN-only của
    # `check_exchange_reachable` ở trên (công thức ngây thơ, không hiệu
    # chỉnh round-trip — xem docstring monitoring/clock.py). Đây là cổng
    # QUYẾT ĐỊNH THẬT ở ngưỡng clock_drift_halt_ms; check ở trên chỉ là
    # heads-up sớm, không có ngưỡng FAIL. Dùng exchange_client THẬT (đã
    # dựng xong) qua ExchangeClient.get_server_time(), không phải một
    # instance ccxt trần như check_exchange_reachable.
    from monitoring.clock import measure_clock_drift

    startup_clock_check = measure_clock_drift(exchange_client.get_server_time)
    logger.info(
        "Lệch đồng hồ khởi động: %.0fms (round-trip %.0fms)",
        startup_clock_check.drift_ms,
        startup_clock_check.round_trip_ms,
    )
    if abs(Decimal(str(startup_clock_check.drift_ms))) > clock_drift_halt_ms:
        print(
            f"TỪ CHỐI KHỞI ĐỘNG: lệch đồng hồ {startup_clock_check.drift_ms:.0f}ms "
            f"(round-trip {startup_clock_check.round_trip_ms:.0f}ms) vượt ngưỡng "
            f"{clock_drift_halt_ms}ms — sửa đồng hồ hệ thống trước khi chạy lại "
            "(xem ops/RUNBOOK.md mục CLOCK_DRIFT)."
        )
        sys.exit(1)

    # Bước 3
    instrument_rules = exchange_client.get_instrument_rules(symbol)
    logger.info("InstrumentRules(%s): %s", symbol, instrument_rules)

    # Bước 4 — load hoặc train (retrain nếu model cũ hơn retrain_interval_days
    # hoặc không tồn tại/không nạp được).
    from data.feature_engineering import compute_all_features
    from data.history_loader import HistoryLoader

    hmm_engine = build_hmm_engine(settings)
    need_retrain = True
    if model_path.exists():
        try:
            hmm_engine.load(str(model_path))
            age_days = (datetime.now(timezone.utc) - hmm_engine.training_date).days
            need_retrain = age_days >= retrain_interval_days
            logger.info(
                "Model HMM nạp từ %s — train lúc %s (%d ngày trước).",
                model_path,
                hmm_engine.training_date,
                age_days,
            )
        except Exception:
            logger.warning("Không nạp được model %s — sẽ train mới.", model_path, exc_info=True)
            need_retrain = True

    history_loader = HistoryLoader()
    data_start = datetime.strptime(DEFAULT_DATA_START, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    latest_bar = _latest_closed_bar_date(now)
    ohlcv = history_loader.load(ccxt_symbol, "1D", data_start, latest_bar.to_pydatetime())
    feature_config = build_feature_config(settings)
    features = compute_all_features(ohlcv, feature_config)

    if need_retrain:
        logger.info("Training HMM (%d bar)...", len(features))
        hmm_engine.select_and_train(features)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        hmm_engine.save(str(model_path))
        logger.info("Đã train + lưu %s.", model_path)

    # Bước 5-6 — risk manager (qua signal_generator), position tracker, đối soát.
    from broker.position_tracker import PositionTracker

    balance = exchange_client.get_balance()
    logger.info("Số dư %s: total=%s available=%s", balance.asset, balance.total, balance.available)

    order_executor = build_order_executor(settings, exchange_client)
    position_tracker = PositionTracker(exchange_client)
    position_tracker.reconcile_on_startup()

    signal_generator = build_signal_generator(settings, hmm_engine, halt_lock_path=halt_lock_path)

    # Phase 11 (monitoring/) — log JSON có cấu trúc + cảnh báo. Đặt SAU
    # khi mọi builder khác đã xong (không cần cho tới đây), TRƯỚC vòng
    # lặp poll để cả state phục hồi lẫn bar đầu tiên đều được log/cảnh báo.
    from monitoring.logger import get_logger

    log_dir = settings["monitoring"]["log_dir"]
    regime_state_logger = get_logger("regime", log_dir)
    alert_manager = build_alert_manager(settings)
    large_pnl_alert_pct = Decimal(str(settings["monitoring"]["large_pnl_alert_pct"]))

    # Bước 7 — khôi phục state_snapshot.json.
    restored = load_state_snapshot(snapshot_path)
    if restored is not None:
        logger.info(
            "Khôi phục state: last_processed_bar=%s allocation=%s stop=%s regime=%s",
            restored.last_processed_bar,
            restored.current_allocation_pct,
            restored.current_stop_loss,
            restored.current_regime_label,
        )
        if restored.current_stop_loss is not None:
            # BẮT BUỘC trước modify_stop() đầu tiên sau restart — xem
            # broker/order_executor.py::restore_known_stop.
            order_executor.restore_known_stop(symbol, Decimal(restored.current_stop_loss))
        state = replace(restored, session_started_at_utc=datetime.now(timezone.utc).isoformat())
    else:
        logger.info("Không có %s — bắt đầu phiên mới.", snapshot_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        state = LiveLoopState(
            last_processed_bar=None,
            current_stop_loss=None,
            current_allocation_pct="0",
            current_regime_id=None,
            current_regime_label=None,
            session_started_at_utc=now_iso,
            written_at_utc=now_iso,
        )

    # Bước 9-10 — không có WebSocket để mở (REST polling, xem
    # docs/DECISIONS.md); "System online".
    logger.info("REST polling active — poll mỗi %ss, không WebSocket.", poll_interval)
    logger.info("System online. symbol=%s testnet=%s dry_run=%s", symbol, testnet, dry_run)

    stop_requested = False

    def _handle_shutdown_signal(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        name = os_signal.Signals(signum).name
        logger.info(
            "Nhận tín hiệu %s — dừng sau vòng poll hiện tại. KHÔNG đóng vị thế "
            "(stop đã đặt vẫn còn hiệu lực phía bot ở lần khởi động lại kế tiếp).",
            name,
        )
        stop_requested = True

    os_signal.signal(os_signal.SIGINT, _handle_shutdown_signal)
    os_signal.signal(os_signal.SIGTERM, _handle_shutdown_signal)

    while not stop_requested:
        try:
            now = datetime.now(timezone.utc)
            latest_bar = _latest_closed_bar_date(now)
            last_processed = (
                pd.Timestamp(state.last_processed_bar, tz="UTC") if state.last_processed_bar else None
            )

            if last_processed is not None and latest_bar <= last_processed:
                time.sleep(poll_interval)
                continue

            poll_started_at = time.monotonic()
            ohlcv = history_loader.load(ccxt_symbol, "1D", data_start, latest_bar.to_pydatetime())
            poll_latency_ms = (time.monotonic() - poll_started_at) * 1000
            last_poll_at = datetime.now(timezone.utc).isoformat()
            # Gắn ngay vào `state` — cả hai nhánh dưới đây (warmup chưa đủ
            # HOẶC xử lý bar bình thường) đều phải mang theo, không chỉ
            # nhánh thành công. `process_one_bar()` dựng `LiveLoopState`
            # MỚI qua constructor (không phải `replace()`), nên field này
            # PHẢI được gắn lại SAU khi nó trả về — xem bên dưới.
            state = replace(state, last_poll_at=last_poll_at, poll_latency_ms=poll_latency_ms)

            features = compute_all_features(ohlcv, feature_config)

            if latest_bar not in features.index:
                logger.warning("Bar %s chưa đủ warmup feature — chờ vòng poll sau.", latest_bar.date())
                write_state_snapshot(snapshot_path, state)
                time.sleep(poll_interval)
                continue

            # Retrain theo lịch — lỗi ở đây KHÔNG được dừng vòng lặp, GIỮ
            # NGUYÊN model cũ (spec §Xử lý lỗi: "Lỗi HMM: giữ nguyên regime
            # hiện tại").
            try:
                if (datetime.now(timezone.utc) - hmm_engine.training_date).days >= retrain_interval_days:
                    logger.info("Đến hạn retrain HMM (bar %s)...", latest_bar.date())
                    hmm_engine.select_and_train(features)
                    hmm_engine.save(str(model_path))
                    logger.info("Retrain xong, đã lưu %s.", model_path)
                    from monitoring.alerts import Alert, AlertType

                    alert_manager.send(
                        Alert(
                            AlertType.HMM_RETRAINED,
                            f"Retrain xong tại bar {latest_bar.date()}, đã lưu {model_path}.",
                            severity="INFO",
                        )
                    )
            except Exception:
                logger.error(
                    "Lỗi retrain HMM — GIỮ NGUYÊN model cũ, không dừng vòng lặp.", exc_info=True
                )

            state = process_one_bar(
                symbol=symbol,
                signal_generator=signal_generator,
                order_executor=order_executor,
                position_tracker=position_tracker,
                ohlcv=ohlcv,
                features=features,
                bar_ts=latest_bar,
                state=state,
                dry_run=dry_run,
                alert_manager=alert_manager,
                regime_state_logger=regime_state_logger,
                large_pnl_alert_pct=large_pnl_alert_pct,
                clock_drift_alert_ms=clock_drift_alert_ms,
                clock_drift_halt_ms=clock_drift_halt_ms,
            )
            # process_one_bar() dựng LiveLoopState MỚI qua constructor —
            # gắn lại poll telemetry đã đo ở trên (KHÔNG sống sót qua
            # constructor mới, khác `replace()` vốn giữ nguyên field không
            # được chỉ định tường minh).
            state = replace(state, last_poll_at=last_poll_at, poll_latency_ms=poll_latency_ms)
            write_state_snapshot(snapshot_path, state)

        except Exception as exc:
            # "Lỗi không bắt được: log traceback, ghi trạng thái, cảnh
            # báo" (spec §Xử lý lỗi) — `state` ở đây vẫn là kết quả của
            # lần process_one_bar() THÀNH CÔNG gần nhất (chưa bị ghi đè
            # bởi lần gọi lỗi dở dang), nên ghi lại nó = "giữ nguyên trạng
            # thái đã biết", đúng tinh thần "lỗi HMM giữ nguyên regime cũ"
            # mà không cần bắt riêng exception HMM ở process_one_bar.
            logger.error("Lỗi không bắt được trong vòng lặp chính — ghi trạng thái, tiếp tục.", exc_info=True)
            write_state_snapshot(snapshot_path, state)
            # API_LOST — cố tình RỘNG (không phân loại theo loại exception):
            # đây là điểm bắt "mọi thứ khác" của toàn vòng lặp (REST tải
            # OHLCV, tính feature, generate signal, submit order, ...),
            # không có cách phân biệt rẻ "lỗi mạng" khỏi "lỗi logic" ở tầng
            # này. DATA_FEED_LOST (hẹp hơn, riêng cho orderbook) đã tách
            # riêng ở _check_spread_and_alert() — cái này là lưới hứng còn
            # lại, ưu tiên báo CÓ sự cố hơn là chính xác tuyệt đối loại sự cố.
            from monitoring.alerts import Alert, AlertType

            alert_manager.send(
                Alert(AlertType.API_LOST, f"Lỗi không bắt được trong vòng lặp chính: {exc}", severity="ERROR")
            )

        time.sleep(poll_interval)

    write_state_snapshot(snapshot_path, state)
    logger.info(
        "Đã dừng. Tổng kết phiên: bar cuối=%s allocation=%s regime=%s",
        state.last_processed_bar,
        state.current_allocation_pct,
        state.current_regime_label,
    )


def run_train_only(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    """`--train-only` — train HMM rồi thoát, KHÔNG kết nối sàn.

    Chỉ cần dữ liệu lịch sử công khai (`HistoryLoader`, không cần API key)
    — hữu ích để kiểm chứng riêng bước train mà không phụ thuộc testnet
    (xem docs/STATE.md, mục "Testnet đang bị chặn"). Không dùng model
    cache có sẵn — `--train-only` nghĩa là ép train lại, khác nhánh
    "load nếu còn mới" trong `run_live_loop`.
    """
    import os

    from data.feature_engineering import compute_all_features
    from data.history_loader import HistoryLoader

    symbol = settings["exchange"]["symbol"]
    ccxt_symbol = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}"
    model_path = Path(os.environ.get("MODEL_PATH", "models/hmm_model.pkl"))

    data_start = datetime.strptime(DEFAULT_DATA_START, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    latest_bar = _latest_closed_bar_date(now)
    ohlcv = HistoryLoader().load(ccxt_symbol, "1D", data_start, latest_bar.to_pydatetime())
    features = compute_all_features(ohlcv, build_feature_config(settings))

    hmm_engine = build_hmm_engine(settings)
    logger.info("Training HMM (%d bar)...", len(features))
    hmm_engine.select_and_train(features)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    hmm_engine.save(str(model_path))
    logger.info("Đã train + lưu %s.", model_path)

    return {
        "model_path": str(model_path),
        "training_date": str(hmm_engine.training_date),
        "n_bars": len(features),
        "bic_results": [
            {"n_components": r.n_components, "bic": r.bic, "converged": r.converged}
            for r in hmm_engine.bic_results
        ],
        "regimes": [
            {"regime_id": r.regime_id, "regime_name": r.regime_name} for r in hmm_engine.regime_infos
        ],
    }


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

    if args.dashboard:
        raise NotImplementedError("--dashboard: Phase 8 (monitoring), chưa implement.")

    if args.train_only:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        settings = load_settings(args.config)
        results = run_train_only(args, settings)
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    # Live loop — Phase 10.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    run_live_loop(args, settings)


if __name__ == "__main__":
    main()
