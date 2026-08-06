"""forward.logger — forward test: GHI LOG, KHÔNG ĐẶT LỆNH.

Chạy pipeline suy luận regime + tính allocation y hệt hệ thống đã kiểm định
(xem docs/VALIDATION_REPORT.md), mỗi bar append MỘT dòng vào `forward/log.csv`
rồi dừng. Không có bất kỳ đường code nào gửi lệnh ra sàn:

    KHÔNG import `broker.*` ở bất cứ đâu trong module này. Không gọi
    ExchangeClient, không gọi Bybit/ccxt trading API. `tests/test_forward_logger.py::
    test_no_broker_import` kiểm tra tĩnh điều này trên chính file nguồn.

ĐÓNG BĂNG CẤU HÌNH — hai khối, coi là MỘT:
  1. `forward/config_frozen.yaml` — bản copy `config/settings.yaml` tại thời
     điểm bắt đầu thí nghiệm. Module này CHỈ đọc file này, không bao giờ đọc
     `config/settings.yaml` (xem `load_frozen_settings`). sha256 của nó được
     kiểm tra lại mỗi lần chạy trước `forward/config_frozen.sha256` — sửa file
     sau khi đóng băng làm chương trình dừng ngay, không âm thầm chạy tiếp.
  2. `FEATURE_SUBSET` bên dưới — bộ 8 cột "pruned-8" đã kiểm định (xem
     docs/DECISIONS.md "Vì sao pruned-8, không phải 14 cột Tầng 1 đầy đủ" và
     docs/VALIDATION_REPORT.md). `settings.yaml` KHÔNG có field này (nó là
     tham số CLI-only của main.py, không phải config field) nên phải đóng
     băng riêng ở đây làm hằng số nguồn — đổi nó giữa chừng phá thí nghiệm
     y hệt đổi config_frozen.yaml.

Muốn đổi bất kỳ thứ gì trong hai khối trên: KẾT THÚC thí nghiệm hiện tại,
bắt đầu thí nghiệm mới, ghi rõ vào docs/DECISIONS.md — không sửa tại chỗ.

RETRAIN THEO LỊCH, không phải mỗi lần chạy — `hmm.retrain_interval_days`
trong config đóng băng (mặc định 7 ngày bar, không phải 7 ngày đồng hồ: lịch
retrain bám theo NGÀY BAR, để backfill nhiều bar một lúc vẫn tái lập đúng y
hệt chạy mỗi ngày). Cửa sổ train MỞ RỘNG (expanding) từ `DATA_START`, không
phải walk-forward rolling `is_bars=365` của backtest — `settings.yaml` không
có tham số độ dài cửa sổ train cho chế độ sống, và bản thân docstring của
`core/hmm_engine.py`/`backtest/backtester.py` đọc `hmm.min_train_bars: 730`
đúng như một sàn an toàn cho training SỐNG (không phải walk-forward), nên
đây là lựa chọn duy nhất khớp với ý nghĩa tham số đó — ghi lại lựa chọn này
ở đây theo đúng tinh thần CLAUDE.md (spec không rõ thì chọn phương án và ghi
lý do).

BACKFILL + IDEMPOTENT — `forward/log.csv` là nguồn trạng thái DUY NHẤT
(ngoài giá thô, vốn tự cache qua `data.history_loader.HistoryLoader`). Không
có file trạng thái ẩn nào khác: cash/qty/pending-target của cả 4 track VÀ
lịch sử retrain HMM đều đọc lại được từ chính log.csv (xem
`pending_bar_dates`, `run_forward_test`). Vì vậy chạy hằng ngày (mỗi lần một
bar) hay hằng tuần (mỗi lần bù 7 bar) cho ra log.csv giống hệt nhau, và chạy
lại cùng ngày không ghi thêm dòng nào (`pending_bar_dates` rỗng).

LẦN CHẠY ĐẦU TIÊN chỉ ghi ĐÚNG MỘT bar — bar gần nhất đã đóng tại thời điểm
chạy — KHÔNG backfill ngược về quá khứ. Xem docs/VALIDATION_REPORT.md mục
3.4: tập dữ liệu lịch sử đã bị nhìn nhiều lần trong quá trình kiểm định,
không còn ngoài mẫu. Forward test chỉ có ý nghĩa với dữ liệu CHƯA từng được
dùng để quyết định bất kỳ điều gì ở trên — lùi log về quá khứ sẽ chỉ là một
backtest nữa đội lốt forward test.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from backtest.backtester import WalkForwardConfig
from backtest.cost_model import CostModel
from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.signal_generator import compose_layer_allocations
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from data.feature_engineering import FeatureConfig, compute_all_features
from data.history_loader import HistoryLoader

logger = logging.getLogger(__name__)

_FORWARD_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _FORWARD_DIR / "config_frozen.yaml"
_HASH_PATH = _FORWARD_DIR / "config_frozen.sha256"
_LOG_PATH = _FORWARD_DIR / "log.csv"

# Cache HIỆU NĂNG THUẦN TUÝ, không phải trạng thái thẩm quyền — lịch retrain
# thật đọc lại được từ cột `hmm_retrained` của log.csv (xem `run_forward_test`).
# File này chỉ tránh phải retrain lại (tốn n_candidates*n_init lần fit EM)
# mỗi khi tiến trình khởi động lại giữa hai bar chưa tới hạn retrain. Mất
# file này không sai gì — lần chạy sau sẽ retrain lại và ghi cache mới.
# `*.pkl` đã có trong .gitignore, không commit.
_MODEL_CACHE_PATH = _FORWARD_DIR / "state" / "hmm_model.pkl"

# Bộ feature "pruned-8" đã kiểm định — xem docstring module ở trên (mục
# ĐÓNG BĂNG CẤU HÌNH, khối 2). Thứ tự không quan trọng, đây là tập hợp.
FEATURE_SUBSET: tuple[str, ...] = (
    "log_return_1",
    "log_return_5",
    "realized_vol_20",
    "vol_ratio_5_20",
    "adx_14",
    "sma50_slope",
    "trade_count_zscore_50",
    "trade_count_sma10_slope",
)

# Ngày bắt đầu TẢI dữ liệu — khớp DEFAULT_DATA_START của main.py, cùng nền
# lịch sử đã dùng để kiểm định (docs/VALIDATION_REPORT.md). Đổi ngày này
# cũng đổi toàn bộ dữ liệu train HMM — đóng băng cùng khối trên.
DATA_START = datetime(2018, 2, 9, tzinfo=timezone.utc)

_SYMBOL = "BTCUSDT"
_CCXT_SYMBOL = "BTC/USDT"
_BAR_OFFSET_HOURS = 0  # ranh giới ngày 00:00 UTC — CLAUDE.md bất biến #10

# target_daily_vol của benchmark static_vol_target — khớp đúng default của
# backtest/performance.py::compare_static_vol_target (bản thân đó cũng là
# magic number ngoài settings.yaml, có sẵn từ trước). Giữ nguyên giá trị để
# 4 đường benchmark forward-test so sánh được với bảng §4.9/VALIDATION_REPORT.
_TARGET_DAILY_VOL = Decimal("0.02")

# Khớp backtest/backtester.py::_STRATEGY_BARS_LOOKBACK — đủ cho EMA50/ATR14
# hội tụ mà không phải tính lại toàn bộ lịch sử mỗi bar.
_STRATEGY_BARS_LOOKBACK = 300

_CSV_FIELDNAMES = [
    "date",
    "run_at_utc",
    "open_price",
    "close_price",
    "hmm_retrained",
    "hmm_train_bars",
    "regime_id",
    "regime_label",
    "regime_probability",
    "regime_is_confirmed",
    "is_flickering",
    "hmm_allocation",
    "trend_gate_state",
    "trend_gate_cap",
    "final_allocation",
    "strategy_cash",
    "strategy_qty",
    "strategy_equity",
    "strategy_target_allocation",
    "bh_cash",
    "bh_qty",
    "bh_equity",
    "bh_target_allocation",
    "sma200_cash",
    "sma200_qty",
    "sma200_equity",
    "sma200_target_allocation",
    "volTarget_cash",
    "volTarget_qty",
    "volTarget_equity",
    "volTarget_target_allocation",
]

# 4 track logic — tên field prefix ↔ cột log. Dùng chung một danh sách để
# vòng lặp ghi/khôi phục trạng thái không lặp lại 4 lần bằng tay.
_TRACK_PREFIXES = ("strategy", "bh", "sma200", "volTarget")


# ----------------------------------------------------------------------
# Đóng băng cấu hình
# ----------------------------------------------------------------------


def sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_settings() -> dict[str, Any]:
    """Đọc `forward/config_frozen.yaml` — KHÔNG BAO GIỜ `config/settings.yaml`.

    Kiểm tra sha256 khớp `forward/config_frozen.sha256` trước khi dùng bất
    kỳ giá trị nào từ nó. Lệch hash nghĩa là file đã bị sửa sau khi đóng
    băng — dừng ngay, không chạy tiếp trên config đã đổi giữa chừng.
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"Thiếu {_CONFIG_PATH} — chưa đóng băng config cho forward test.")
    if not _HASH_PATH.exists():
        raise FileNotFoundError(f"Thiếu {_HASH_PATH} — chưa ghi hash đóng băng.")

    actual_hash = sha256_of_file(_CONFIG_PATH)
    expected_hash = _HASH_PATH.read_text(encoding="utf-8").strip()
    if actual_hash != expected_hash:
        raise RuntimeError(
            "forward/config_frozen.yaml đã bị SỬA sau khi đóng băng — "
            f"sha256 hiện tại {actual_hash} != {expected_hash} đã ghi ở "
            "forward/config_frozen.sha256. Thí nghiệm này coi như hỏng "
            "(xem docs/DECISIONS.md, mục 'Forward test — tiền đăng ký'). "
            "Muốn đổi cấu hình: KẾT THÚC thí nghiệm này, bắt đầu thí nghiệm "
            "mới với config_frozen.yaml/hash mới, ghi rõ lý do."
        )

    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        settings: dict[str, Any] = yaml.safe_load(fh)
    return settings


# ----------------------------------------------------------------------
# Backfill / idempotency — hàm THUẦN, không I/O, dễ test độc lập
# ----------------------------------------------------------------------


def latest_closed_bar_date(now: datetime) -> pd.Timestamp:
    """Bar ngày gần nhất đã ĐÓNG tại thời điểm `now`.

    Bar "hôm nay" (00:00 UTC hôm nay → 00:00 UTC ngày mai) chưa đóng cho
    tới đúng ranh giới đó — CLAUDE.md bất biến #10, không có giờ giao dịch,
    ranh giới ngày LUÔN là 00:00 UTC. Vì vậy bar dùng được gần nhất luôn là
    hôm qua, bất kể `now` là giờ nào trong ngày.
    """
    now_utc = now.astimezone(timezone.utc)
    today = pd.Timestamp(now_utc.date(), tz="UTC")
    return today - pd.Timedelta(days=1)


def pending_bar_dates(
    last_logged_date: date | pd.Timestamp | None,
    available_dates: list[pd.Timestamp],
) -> list[pd.Timestamp]:
    """Danh sách bar CHƯA có trong log, tăng dần theo thời gian.

    Hàm THUẦN: cùng input luôn cho cùng output. Đây là cơ chế backfill —
    chạy hằng ngày (mỗi lần `last_logged_date` là hôm qua, trả về đúng 1
    bar) hay hằng tuần (cách nhau 7 ngày, trả về đúng 7 bar) đều cho ra
    log.csv cuối cùng giống hệt nhau, vì hàm chỉ nhìn khoảng cách ngày,
    không nhìn tần suất gọi.

    `last_logged_date=None` (log.csv chưa tồn tại/rỗng — lần chạy đầu
    tiên) trả về ĐÚNG MỘT bar (gần nhất) — không backfill về quá khứ, xem
    docstring module.
    """
    if not available_dates:
        return []
    if last_logged_date is None:
        return [available_dates[-1]]

    last_date = last_logged_date.date() if hasattr(last_logged_date, "date") else last_logged_date
    return sorted(d for d in available_dates if d.date() > last_date)


def thresholded_target(
    raw_target: Decimal, current_allocation: Decimal, threshold_fraction: Decimal
) -> Decimal:
    """Giữ nguyên allocation hiện tại nếu lệch dưới ngưỡng rebalance.

    Khớp logic `StrategyOrchestrator.generate_signal`
    (core/regime_strategies.py) và `backtest/performance.py::
    _simulate_target_allocation_series` — áp lại thủ công ở đây cho 3
    benchmark (chúng không đi qua StrategyOrchestrator, vốn đã tự làm việc
    này cho track strategy).
    """
    if abs(raw_target - current_allocation) < threshold_fraction:
        return current_allocation
    return raw_target


# ----------------------------------------------------------------------
# Ghi log — CHỈ APPEND
# ----------------------------------------------------------------------


def append_row(row: dict[str, Any], path: Path | None = None) -> None:
    """Append MỘT dòng vào log.csv.

    Luôn mở bằng mode `'a'`, KHÔNG BAO GIỜ `'w'` hay đọc-rồi-ghi-đè toàn bộ
    — đây là cơ chế duy nhất trong module này chạm vào `log.csv`, và nó
    không có khả năng sửa một dòng đã ghi (xem
    tests/test_forward_logger.py::test_append_row_never_rewrites_prior_lines).
    Ghi header đúng một lần, khi file chưa tồn tại.

    `path=None` tra `_LOG_PATH` tại THỜI ĐIỂM GỌI (không phải default
    argument cố định lúc định nghĩa hàm) — để test có thể trỏ sang tmp_path
    bằng monkeypatch mà không phải truyền path qua mọi lớp gọi.
    """
    target = path if path is not None else _LOG_PATH
    is_new = not target.exists()
    with target.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def read_existing_log(path: Path | None = None) -> pd.DataFrame | None:
    target = path if path is not None else _LOG_PATH
    if not target.exists():
        return None
    df = pd.read_csv(target, parse_dates=["date"])
    if df.empty:
        return None
    df["hmm_retrained"] = df["hmm_retrained"].astype(str) == "True"
    return df


# ----------------------------------------------------------------------
# Mô phỏng portfolio giấy (paper) — 4 track song song, cùng cost model
# ----------------------------------------------------------------------


@dataclass
class _Track:
    cash: Decimal
    qty: Decimal

    def equity(self, price: Decimal) -> Decimal:
        return self.cash + self.qty * price

    def allocation(self, price: Decimal) -> Decimal:
        eq = self.equity(price)
        return self.qty * price / eq if eq > 0 else Decimal("0")


def execute_pending(
    track: _Track,
    target_allocation: Decimal,
    open_price: Decimal,
    cost_model: CostModel,
    instrument_rules: Any,
) -> None:
    """Thực thi target đã quyết định ở bar TRƯỚC, dùng giá OPEN của bar
    này — khớp `fill_delay_bars=1` của `WalkForwardBacktester`. Decimal +
    ROUND_DOWN xuyên suốt qua `instrument_rules.round_qty` (CLAUDE.md bất
    biến #3). Không giao dịch thật — track chỉ là sổ sách giấy trong bộ
    nhớ, không có lệnh nào gửi ra ngoài (xem docstring module).
    """
    if open_price <= 0:
        return
    equity = track.equity(open_price)
    target_qty = instrument_rules.round_qty(equity * target_allocation / open_price)
    delta = target_qty - track.qty
    if delta == 0:
        return
    notional = abs(delta) * open_price
    if notional < instrument_rules.min_order_amt:
        return
    cost = cost_model.rebalance_cost(delta, open_price)
    track.cash -= delta * open_price + cost
    track.qty += delta


# ----------------------------------------------------------------------
# Vòng lặp chính
# ----------------------------------------------------------------------


def run_forward_test(now: datetime | None = None) -> dict[str, Any]:
    """Một lần chạy: tải dữ liệu, tìm bar còn thiếu, xử lý từng bar theo
    thứ tự thời gian, append log.csv. Idempotent — không có bar mới thì
    không ghi gì và trả `{"appended": 0, ...}`.
    """
    now = now or datetime.now(timezone.utc)
    settings = load_frozen_settings()

    existing = read_existing_log()
    last_logged_date = existing["date"].max() if existing is not None else None

    latest_bar = latest_closed_bar_date(now)
    loader = HistoryLoader()
    ohlcv = loader.load(
        _CCXT_SYMBOL, "1D", DATA_START, latest_bar.to_pydatetime(), bar_offset_hours=_BAR_OFFSET_HOURS
    )
    available_dates = [ts for ts in ohlcv.index if ts <= latest_bar]

    to_process = pending_bar_dates(last_logged_date, available_dates)
    if not to_process:
        logger.info("Không có bar mới — log đã cập nhật tới %s.", last_logged_date)
        return {"appended": 0, "last_logged_date": _fmt_date(last_logged_date)}

    hmm_cfg = settings["hmm"]
    strategy_cfg = settings["strategy"]
    trend_gate_cfg = settings["trend_gate"]
    costs_cfg = settings["costs"]

    hmm_engine = HMMRegimeEngine(
        n_candidates=list(hmm_cfg["n_candidates"]),
        n_init=hmm_cfg["n_init"],
        covariance_type=hmm_cfg["covariance_type"],
        min_train_bars=hmm_cfg["min_train_bars"],
        stability_bars=hmm_cfg["stability_bars"],
        flicker_window=hmm_cfg["flicker_window"],
        flicker_threshold=hmm_cfg["flicker_threshold"],
    )
    orchestrator = StrategyOrchestrator(
        min_confidence=strategy_cfg["min_confidence"],
        rebalance_threshold_pct=Decimal(str(strategy_cfg["rebalance_threshold_pct"])),
        # "halve" — xem docs/DECISIONS.md, QUYẾT ĐỊNH (2026-08-05): giữ
        # nguyên, cơ chế phòng vệ đuôi thật, không phải lỗi thiết kế.
        uncertainty_mode="halve",
    )
    trend_gate = StructuralTrendGate(
        TrendGateConfig(
            sma_period=trend_gate_cfg["sma_period"],
            slope_lookback=trend_gate_cfg["slope_lookback"],
            buffer_pct=Decimal(str(trend_gate_cfg["buffer_pct"])),
            confirm_bars=trend_gate_cfg["confirm_bars"],
            cap_bull_structure=Decimal(str(trend_gate_cfg["cap_bull_structure"])),
            cap_transition=Decimal(str(trend_gate_cfg["cap_transition"])),
            cap_bear_structure=Decimal(str(trend_gate_cfg["cap_bear_structure"])),
        )
    )
    cost_model = CostModel(
        taker_fee_pct=Decimal(str(costs_cfg["taker_fee_pct"])),
        maker_fee_pct=Decimal(str(costs_cfg["maker_fee_pct"])),
        slippage_pct=Decimal(str(costs_cfg["slippage_pct"])),
        assume_taker=costs_cfg["assume_taker"],
    )
    wf_defaults = WalkForwardConfig()
    instrument_rules = wf_defaults.instrument_rules
    initial_equity = wf_defaults.initial_equity
    threshold_fraction = Decimal(str(strategy_cfg["rebalance_threshold_pct"])) / Decimal("100")

    feature_config = FeatureConfig(
        zscore_lookback=hmm_cfg["zscore_lookback"],
        use_trade_count_not_volume=settings["features"]["use_trade_count_not_volume"],
        tier2_derivatives=False,  # đóng băng cùng FEATURE_SUBSET — pruned-8 không dùng Tầng 2
        tier3_temporal=False,
        feature_subset=FEATURE_SUBSET,
    )
    features = compute_all_features(ohlcv, feature_config)

    # Tính một lần trên toàn bộ lịch sử rồi tra bảng theo bar — không gọi
    # lại get_structure_history() mỗi bar (O(n) mỗi lần, xem lý do trong
    # backtest/backtester.py).
    trend_gate_history = trend_gate.get_structure_history(ohlcv)
    sma200 = ohlcv["close"].rolling(200, min_periods=200).mean()
    realized_vol_20 = ohlcv["close"].pct_change().rolling(20, min_periods=20).std()

    tracks: dict[str, _Track] = {}
    pending: dict[str, Decimal] = {}
    for prefix in _TRACK_PREFIXES:
        if existing is None:
            tracks[prefix] = _Track(cash=initial_equity, qty=Decimal("0"))
            pending[prefix] = Decimal("0")
        else:
            last_row = existing.iloc[-1]
            tracks[prefix] = _Track(
                cash=Decimal(str(last_row[f"{prefix}_cash"])),
                qty=Decimal(str(last_row[f"{prefix}_qty"])),
            )
            pending[prefix] = Decimal(str(last_row[f"{prefix}_target_allocation"]))

    last_retrain_date: date | None = None
    if existing is not None:
        retrained_rows = existing[existing["hmm_retrained"]]
        if not retrained_rows.empty:
            last_retrain_date = retrained_rows["date"].max().date()

    # Nạp cache model NẾU CÓ — thẩm quyền lịch retrain vẫn là log.csv (ở
    # trên), cache này chỉ tránh retrain lại khi tiến trình mới khởi động
    # nhưng chưa tới hạn retrain thật. Nạp lỗi (cache hỏng/thiếu) không sao
    # — model vẫn None, nhánh `hmm_engine.model is None` bên dưới sẽ ép
    # retrain ngay bar đầu tiên bất kể lịch.
    if _MODEL_CACHE_PATH.exists():
        try:
            hmm_engine.load(str(_MODEL_CACHE_PATH))
        except Exception:
            logger.warning("Không nạp được cache model %s — sẽ retrain lại.", _MODEL_CACHE_PATH)

    retrain_interval_days = int(hmm_cfg["retrain_interval_days"])
    appended = 0
    last_processed: pd.Timestamp | None = None

    for ts in to_process:
        if ts not in features.index:
            # Chưa đủ warmup (zscore_lookback) để có feature tại bar này —
            # không thể suy luận regime. Dừng, không ghi dòng nào cho bar
            # này hay các bar sau nó — lần chạy kế tiếp thử lại từ đây.
            logger.warning("Bar %s chưa đủ warmup feature — dừng, chờ lần chạy sau.", ts.date())
            break

        schedule_due = (
            last_retrain_date is None or (ts.date() - last_retrain_date).days >= retrain_interval_days
        )
        # `hmm_engine.model is None` bắt đúng trường hợp tiến trình mới khởi
        # động, chưa tới hạn retrain theo lịch, NHƯNG không có cache model để
        # nạp — không có nhánh này thì predict_regime_filtered() bên dưới sẽ
        # crash thay vì tự phục hồi.
        must_retrain = schedule_due or hmm_engine.model is None
        train_bars_used: int | None = None
        # pandas-stubs không nhận nhãn Timestamp làm biên slice `.loc[:ts]`
        # dù runtime hoàn toàn hợp lệ (label-based slicing chuẩn của pandas)
        # — `# type: ignore[misc]` đúng ba chỗ dùng pattern này bên dưới.
        if must_retrain:
            train_features = features.loc[:ts]  # type: ignore[misc]
            hmm_engine.select_and_train(train_features)  # raise rõ ràng nếu thiếu min_train_bars
            train_bars_used = len(train_features)
            last_retrain_date = ts.date()
            _MODEL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            hmm_engine.save(str(_MODEL_CACHE_PATH))

        features_so_far = features.loc[:ts]  # type: ignore[misc]
        regime_state = hmm_engine.predict_regime_filtered(features_so_far)
        is_flickering = hmm_engine.is_flickering()

        bars_window = ohlcv.loc[:ts].tail(_STRATEGY_BARS_LOOKBACK)  # type: ignore[misc]
        # Tương tự, `.loc[ts, "col"]` (tuple nhãn hàng+cột) không khớp overload
        # của pandas-stubs cho kiểu Timestamp tường minh — `# type: ignore[index]`.
        open_price = Decimal(str(ohlcv.loc[ts, "open"]))  # type: ignore[index]
        close_price = Decimal(str(ohlcv.loc[ts, "close"]))  # type: ignore[index]

        # 1) thực thi quyết định của bar TRƯỚC, tại giá OPEN của bar này —
        # cùng cơ chế cho cả 4 track (cùng dữ liệu, cùng chi phí).
        for prefix in _TRACK_PREFIXES:
            execute_pending(tracks[prefix], pending[prefix], open_price, cost_model, instrument_rules)

        # 2) suy luận + quyết định target MỚI (dùng dữ liệu tới HẾT bar này) —
        # target này trở thành "pending" cho lần chạy/bar kế tiếp.
        strategy_track = tracks["strategy"]
        signal = orchestrator.generate_signal(
            _SYMBOL,
            regime_state,
            hmm_engine.regime_infos,
            bars_window,
            strategy_track.allocation(close_price),
            is_flickering,
        )
        trend_gate_row = trend_gate_history.loc[ts]
        trend_gate_cap = Decimal(str(trend_gate_row["cap"]))
        hmm_allocation = signal.target_allocation_pct
        final_allocation = compose_layer_allocations(hmm_allocation, trend_gate_cap)

        bh_target = thresholded_target(Decimal("1"), tracks["bh"].allocation(close_price), threshold_fraction)

        sma_val = sma200.loc[ts]
        close_raw = float(ohlcv.loc[ts, "close"])  # type: ignore[index, arg-type]
        if pd.isna(sma_val):
            raw_sma_target = Decimal("0")
        else:
            raw_sma_target = Decimal("1") if close_raw > float(sma_val) else Decimal("0")
        sma200_target = thresholded_target(
            raw_sma_target, tracks["sma200"].allocation(close_price), threshold_fraction
        )

        vol_val = realized_vol_20.loc[ts]
        if pd.isna(vol_val) or vol_val <= 0:
            raw_vol_target = Decimal("0")
        else:
            raw_vol_target = min(Decimal("1"), max(Decimal("0"), _TARGET_DAILY_VOL / Decimal(str(vol_val))))
        vol_target = thresholded_target(
            raw_vol_target, tracks["volTarget"].allocation(close_price), threshold_fraction
        )

        new_targets = {
            "strategy": final_allocation,
            "bh": bh_target,
            "sma200": sma200_target,
            "volTarget": vol_target,
        }

        # 3) ghi lại — mark-to-market bằng giá ĐÓNG của bar này.
        row: dict[str, Any] = {
            "date": ts.date().isoformat(),
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            "open_price": str(open_price),
            "close_price": str(close_price),
            "hmm_retrained": must_retrain,
            "hmm_train_bars": train_bars_used if train_bars_used is not None else "",
            "regime_id": regime_state.state_id,
            "regime_label": regime_state.label,
            "regime_probability": regime_state.probability,
            "regime_is_confirmed": regime_state.is_confirmed,
            "is_flickering": is_flickering,
            "hmm_allocation": str(hmm_allocation),
            "trend_gate_state": trend_gate_row["confirmed_state"],
            "trend_gate_cap": str(trend_gate_cap),
            "final_allocation": str(final_allocation),
        }
        for prefix in _TRACK_PREFIXES:
            row[f"{prefix}_cash"] = str(tracks[prefix].cash)
            row[f"{prefix}_qty"] = str(tracks[prefix].qty)
            row[f"{prefix}_equity"] = str(tracks[prefix].equity(close_price))
            row[f"{prefix}_target_allocation"] = str(new_targets[prefix])

        append_row(row)
        appended += 1
        last_processed = ts
        pending = new_targets

    final_date = last_processed if last_processed is not None else last_logged_date
    return {"appended": appended, "last_logged_date": _fmt_date(final_date)}


def _fmt_date(value: pd.Timestamp | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value.isoformat()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_forward_test()
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
