"""tests.test_bars_window_sensitivity — đo (không suy luận) khác biệt giữa
hai quy ước `bars_window` đang cùng tồn tại trong repo:

  - `forward/logger.py:558` (thí nghiệm ĐÓNG BĂNG, quy ước THẬT đang chạy
    sản xuất): `ohlcv.loc[:ts].tail(_STRATEGY_BARS_LOOKBACK)` (300 bar).
  - `tests/test_forward_golden.py:160` (và `tests/test_wiring_equivalence.py`):
    `ohlcv.loc[:ts]` (KHÔNG giới hạn).

Ở phiên trước, kết luận "vô hại" chỉ dựa trên đọc code (EMA50/ATR14 hội
tụ trong vài chục bar) — CHƯA ĐO. File này đo thật: chạy đúng công thức
wiring của `forward/logger.py` (HMM → `StrategyOrchestrator.generate_signal()`
→ `StructuralTrendGate.get_allocation_cap()` → `compose_layer_allocations()`)
HAI LẦN ĐỘC LẬP trên CÙNG một chuỗi bar — một lần với `bars_window` cắt
300 bar, một lần không giới hạn — mỗi lần tự theo dõi `current_allocation`
RIÊNG (không reset về nhau giữa hai lần chạy), để một khác biệt nhỏ có cơ
hội TÍCH LUỸ qua ngưỡng rebalance nếu nó thật sự tồn tại, thay vì bị che
bởi phép so sánh "mỗi bar độc lập, state luôn đồng bộ lại".

`_N_BARS`/`_N_PREDICT_BARS` chọn đủ lớn để dải bar kiểm tra ĐI QUA đúng
ranh giới nơi `.tail(300)` bắt đầu cắt thật (ohlcv position 300) — xác
nhận bằng `assert` trong chính test, không phải chọn số rồi hy vọng.

**KHÔNG sửa `forward/logger.py`** — chỉ đọc hằng số `FEATURE_SUBSET`
(đã đóng băng) và tái tạo đúng công thức wiring bằng component thật của
`core/`, giống `tests/test_forward_golden.py`/`tests/test_wiring_equivalence.py`.

**Kết quả đo được (2026-08-07, xem `docs/DECISIONS.md`):** KHỚP 100% —
0/300 bar lệch, kể cả sau khi cho hai lần chạy tích luỹ độc lập qua toàn
bộ dải (bao gồm cả trước LẪN sau ranh giới cắt 300 bar). Kết quả này được
ghi lại vào docstring `forward/logger.py` (mục "Đo `bars_window`") —
đóng câu hỏi, không phải giả định.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.signal_generator import compose_layer_allocations
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from data.feature_engineering import FeatureConfig, compute_all_features
from forward.logger import FEATURE_SUBSET  # hằng số đã đóng băng, CHỈ ĐỌC

_SYMBOL = "BTCUSDT"
_SEED = 12345
_N_BARS = 700  # đủ lớn để dải dự đoán đi qua ranh giới cắt 300 bar, xem assert dưới
_TRAIN_BARS = 150
_N_PREDICT_BARS = 300
_ZSCORE_LOOKBACK = 60
_HMM_N_CANDIDATES = [3, 4, 5]
_HMM_N_INIT = 3
_TAIL_LOOKBACK = 300  # khớp forward/logger.py::_STRATEGY_BARS_LOOKBACK


def _make_synthetic_ohlcv(n_bars: int, seed: int) -> pd.DataFrame:
    """Cùng công thức với tests/test_forward_golden.py — không import
    chéo giữa file test (quy ước đã có từ trước)."""
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


def test_tail_300_matches_unbounded_bars_window_across_the_truncation_boundary() -> None:
    ohlcv = _make_synthetic_ohlcv(_N_BARS, _SEED)
    feature_config = FeatureConfig(
        zscore_lookback=_ZSCORE_LOOKBACK,
        use_trade_count_not_volume=True,
        tier2_derivatives=False,
        tier3_temporal=False,
        feature_subset=FEATURE_SUBSET,
    )
    features = compute_all_features(ohlcv, feature_config)
    assert len(features) >= _TRAIN_BARS + _N_PREDICT_BARS, (
        f"_N_BARS={_N_BARS} không đủ tạo {_TRAIN_BARS + _N_PREDICT_BARS} feature row "
        f"(chỉ có {len(features)})."
    )

    engine = HMMRegimeEngine(
        n_candidates=_HMM_N_CANDIDATES,
        n_init=_HMM_N_INIT,
        covariance_type="full",
        min_train_bars=_TRAIN_BARS,
        stability_bars=3,
        flicker_window=20,
        flicker_threshold=4,
    )
    engine.select_and_train(features.iloc[:_TRAIN_BARS])

    orchestrator = StrategyOrchestrator(
        min_confidence=0.55, rebalance_threshold_pct=Decimal("25"), uncertainty_mode="halve"
    )
    trend_gate = StructuralTrendGate(TrendGateConfig())

    current_allocation_tail = Decimal("0")
    current_allocation_unbounded = Decimal("0")
    crossed_truncation_boundary = False
    checked_bars = 0

    for offset in range(_N_PREDICT_BARS):
        i = _TRAIN_BARS + offset
        ts = features.index[i]
        features_so_far = features.iloc[: i + 1]

        # bars_window KHÔNG ảnh hưởng HMM (predict_regime_filtered chỉ
        # đọc `features`, không đọc `ohlcv`) — dùng CHUNG một regime_state
        # cho cả hai lần chạy composition, khớp đúng thứ đang muốn đo
        # (độ nhạy của bars_window, không phải độ nhạy của HMM).
        regime_state = engine.predict_regime_filtered(features_so_far)
        is_flickering = engine.is_flickering()

        ohlcv_pos = ohlcv.index.get_loc(ts)
        assert isinstance(ohlcv_pos, int)
        if ohlcv_pos >= _TAIL_LOOKBACK:
            crossed_truncation_boundary = True

        bars_tail = ohlcv.loc[:ts].tail(_TAIL_LOOKBACK)
        bars_unbounded = ohlcv.loc[:ts]

        signal_tail = orchestrator.generate_signal(
            _SYMBOL, regime_state, engine.regime_infos, bars_tail, current_allocation_tail, is_flickering
        )
        signal_unbounded = orchestrator.generate_signal(
            _SYMBOL,
            regime_state,
            engine.regime_infos,
            bars_unbounded,
            current_allocation_unbounded,
            is_flickering,
        )

        cap_tail = trend_gate.get_allocation_cap(bars_tail)
        cap_unbounded = trend_gate.get_allocation_cap(bars_unbounded)

        final_tail = compose_layer_allocations(signal_tail.target_allocation_pct, cap_tail)
        final_unbounded = compose_layer_allocations(signal_unbounded.target_allocation_pct, cap_unbounded)

        assert signal_tail.target_allocation_pct == signal_unbounded.target_allocation_pct, (
            f"Bar {i} (ts={ts.date()}, ohlcv_pos={ohlcv_pos}): hmm_allocation LỆCH giữa "
            f"bars_window cắt 300 ({signal_tail.target_allocation_pct}) và không giới hạn "
            f"({signal_unbounded.target_allocation_pct})."
        )
        assert cap_tail == cap_unbounded, (
            f"Bar {i} (ts={ts.date()}, ohlcv_pos={ohlcv_pos}): trend_gate_cap LỆCH giữa "
            f"bars_window cắt 300 ({cap_tail}) và không giới hạn ({cap_unbounded})."
        )
        assert final_tail == final_unbounded, (
            f"Bar {i} (ts={ts.date()}, ohlcv_pos={ohlcv_pos}): final_allocation LỆCH — "
            f"tail(300)={final_tail} không_giới_hạn={final_unbounded}. current_allocation lúc "
            f"vào bar này: tail={current_allocation_tail} unbounded={current_allocation_unbounded}."
        )

        current_allocation_tail = final_tail
        current_allocation_unbounded = final_unbounded
        checked_bars += 1

    assert checked_bars == _N_PREDICT_BARS, "Vòng lặp dừng sớm — không phủ hết dải bar dự kiến."
    assert crossed_truncation_boundary, (
        f"Dải bar kiểm tra chưa từng chạm ohlcv position >= {_TAIL_LOOKBACK} — "
        "tail(300) chưa từng thật sự cắt gì trong lần chạy này, phép đo không có ý nghĩa. "
        "Tăng _N_BARS/_N_PREDICT_BARS."
    )
    # Đối chứng cuối: hai chuỗi current_allocation ĐỘC LẬP (tích luỹ suốt
    # 300 bar) vẫn hội tụ về đúng CÙNG một giá trị — không chỉ khớp bar-by-bar
    # mà không có phân kỳ tích luỹ nào lọt qua được phép so sánh trên.
    assert current_allocation_tail == current_allocation_unbounded
