"""tests.test_forward_golden — khoá output pipeline forward bằng golden file.

Chạy TOÀN BỘ chuỗi feature → HMM → strategy → trend gate →
`compose_layer_allocations` trên một tập dữ liệu tổng hợp CỐ ĐỊNH (seed cố
định, không đổi), so từng trường output với `tests/golden/forward_baseline.json`
đã commit.

**Test này KHÔNG được sửa để cho qua.** Nếu nó FAIL:

- Thay đổi VÔ TÌNH ảnh hưởng tới output forward (bug mới trong `core/`) →
  **revert thay đổi đó**, không sửa golden file.
- Thay đổi CỐ Ý ảnh hưởng tới output forward (sửa logic có chủ đích trong
  `core/hmm_engine.py`/`core/regime_strategies.py`/`core/trend_gate.py`/
  `core/signal_generator.py`) → **thí nghiệm forward test hiện tại
  (`forward/log.csv`, xem docs/DECISIONS.md "Forward test — tiền đăng ký")
  coi như kết thúc tại đúng thời điểm này** — dù `forward/config_frozen.yaml`
  không đổi, hệ thống chạy trên nó ĐÃ đổi, phá vỡ giả định "cùng một hệ
  thống xuyên suốt" của thí nghiệm đó. Ghi lại vào `docs/DECISIONS.md` (lý
  do thay đổi, ngày, thí nghiệm cũ kết thúc ở đâu), rồi mới regenerate golden
  file cho một thí nghiệm MỚI bằng `python tests/test_forward_golden.py
  --regenerate` (xem docstring `_regenerate` bên dưới) — không bao giờ chạy
  regenerate "tiện tay" chỉ để test xanh lại.

Xem CLAUDE.md bất biến #15 — file này nằm trong danh sách test bắt buộc
không được skip/xfail/comment out.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.signal_generator import compose_layer_allocations
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from data.feature_engineering import FeatureConfig, compute_all_features
from forward.logger import FEATURE_SUBSET

_GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "forward_baseline.json"

# ĐÓNG BĂNG cùng ý nghĩa với forward/config_frozen.yaml — đổi bất kỳ hằng số
# nào dưới đây (seed, số bar, tham số HMM/trend-gate/strategy) tạo ra một
# golden file KHÁC, tức một "thí nghiệm golden" khác. Không đổi tuỳ tiện.
_SEED = 12345
_N_BARS = 400
_TRAIN_BARS = 150  # < hmm.min_train_bars thật (730) — golden test khoá WIRING,
# không khoá độ chính xác production; xem docstring _run_golden_pipeline.
_N_PREDICT_BARS = 60  # đủ dài để có cơ hội thấy đổi regime/trend-gate state,
# không chỉ một giá trị lặp lại suốt — golden test có ích hơn khi bắt được
# nhiều nhánh code khác nhau (LowVol/MidVol/HighVol, rebalance giữ/đổi,
# BULL/TRANSITION/BEAR structure), không chỉ một nhánh duy nhất.
_SYMBOL = "BTCUSDT"

_HMM_N_CANDIDATES = [3, 4, 5]
_HMM_N_INIT = 3
_ZSCORE_LOOKBACK = 60

# `regime_probability` đi qua BLAS (matmul trong hmmlearn/logsumexp) — có
# thể lệch vài ULP giữa các backend BLAS khác nhau (Accelerate/OpenBLAS/MKL)
# dù cùng seed. So bằng dung sai, không so chuỗi. Mọi trường còn lại
# (id/label/bool/Decimal-as-string) phải khớp TUYỆT ĐỐI.
_PROBABILITY_TOLERANCE = 1e-6


def _make_synthetic_ohlcv(n_bars: int, seed: int) -> pd.DataFrame:
    """Cùng công thức với tests/test_backtester.py::_make_synthetic_ohlcv —
    không import chéo giữa file test để mỗi file tự chứa, nhưng cố ý giữ
    công thức giống nhau cho dễ đối chiếu.
    """
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


def _run_golden_pipeline() -> list[dict[str, Any]]:
    """Chạy đúng chuỗi feature → HMM → strategy → trend gate →
    `compose_layer_allocations`, trả về danh sách bản ghi (một bar một dict).

    Dùng tham số NHỎ hơn production (`_TRAIN_BARS=150` thay vì
    `hmm.min_train_bars=730`, `_HMM_N_CANDIDATES` chỉ 3 ứng viên thay vì
    5) để test chạy nhanh trong bộ CI — mục tiêu của golden test này là
    khoá đúng WIRING/logic của pipeline (core/ import gì, gọi hàm nào theo
    thứ tự nào, công thức kết hợp tầng nào), không phải tái lập số liệu
    production thật (đó là việc của reports/pruned8_base, xem
    docs/VALIDATION_REPORT.md). Mọi component ở đây là component THẬT của
    `core/` — không mock, không giả lập rút gọn.
    """
    ohlcv = _make_synthetic_ohlcv(_N_BARS, _SEED)

    feature_config = FeatureConfig(
        zscore_lookback=_ZSCORE_LOOKBACK,
        use_trade_count_not_volume=True,
        tier2_derivatives=False,
        tier3_temporal=False,
        feature_subset=FEATURE_SUBSET,  # bộ pruned-8 thật, xem forward/logger.py
    )
    features = compute_all_features(ohlcv, feature_config)
    if len(features) < _TRAIN_BARS + _N_PREDICT_BARS:
        raise RuntimeError(
            f"_N_BARS={_N_BARS} không đủ tạo {_TRAIN_BARS + _N_PREDICT_BARS} feature row "
            f"(chỉ có {len(features)}) — tăng _N_BARS, KHÔNG giảm _TRAIN_BARS/_N_PREDICT_BARS "
            "tuỳ tiện (đó là một phần khối tham số đóng băng của golden test)."
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
    orchestrator = StrategyOrchestrator(
        min_confidence=0.55,
        rebalance_threshold_pct=Decimal("25"),
        uncertainty_mode="halve",  # mặc định production — docs/DECISIONS.md 2026-08-05
    )
    trend_gate = StructuralTrendGate(TrendGateConfig())  # tham số mặc định thật (SMA200/30/...)
    trend_gate_history = trend_gate.get_structure_history(ohlcv)

    train_features = features.iloc[:_TRAIN_BARS]
    engine.select_and_train(train_features)

    records: list[dict[str, Any]] = []
    current_allocation = Decimal("0")

    for offset in range(_N_PREDICT_BARS):
        i = _TRAIN_BARS + offset
        ts = features.index[i]
        features_so_far = features.iloc[: i + 1]

        regime_state = engine.predict_regime_filtered(features_so_far)
        is_flickering = engine.is_flickering()

        bars_window = ohlcv.loc[:ts]
        signal = orchestrator.generate_signal(
            _SYMBOL, regime_state, engine.regime_infos, bars_window, current_allocation, is_flickering
        )

        trend_gate_row = trend_gate_history.loc[ts]
        trend_gate_cap = Decimal(str(trend_gate_row["cap"]))
        hmm_allocation = signal.target_allocation_pct
        final_allocation = compose_layer_allocations(hmm_allocation, trend_gate_cap)

        records.append(
            {
                "bar_index": i,
                "regime_id": regime_state.state_id,
                "regime_label": regime_state.label,
                "regime_probability": float(regime_state.probability),
                "regime_is_confirmed": bool(regime_state.is_confirmed),
                "is_flickering": bool(is_flickering),
                "hmm_allocation": str(hmm_allocation),
                "trend_gate_state": str(trend_gate_row["confirmed_state"]),
                "trend_gate_cap": str(trend_gate_cap),
                "final_allocation": str(final_allocation),
            }
        )

        current_allocation = final_allocation

    return records


def _load_golden() -> dict[str, Any]:
    if not _GOLDEN_PATH.exists():
        raise FileNotFoundError(
            f"Thiếu {_GOLDEN_PATH}. Đây KHÔNG phải lỗi cần tự sửa bằng cách tạo file mới tuỳ "
            "tiện — xem docstring module này. Nếu đây thật sự là lần đầu tạo golden (thí "
            "nghiệm mới), chạy: python tests/test_forward_golden.py --regenerate"
        )
    with _GOLDEN_PATH.open(encoding="utf-8") as fh:
        golden: dict[str, Any] = json.load(fh)
    return golden


def test_forward_pipeline_matches_golden_baseline() -> None:
    golden = _load_golden()
    actual = _run_golden_pipeline()
    expected = golden["bars"]

    assert len(actual) == len(expected), (
        f"Số bar output đổi ({len(actual)} != {len(expected)}) — pipeline forward đã đổi "
        "hành vi (số bar dự đoán được, hoặc lỗi giữa chừng). Xem docstring module."
    )

    for i, (a, e) in enumerate(zip(actual, expected)):
        for field in [
            "bar_index",
            "regime_id",
            "regime_label",
            "regime_is_confirmed",
            "is_flickering",
            "hmm_allocation",
            "trend_gate_state",
            "trend_gate_cap",
            "final_allocation",
        ]:
            assert a[field] == e[field], (
                f"Bar {i} (bar_index={a['bar_index']}), trường {field!r} lệch golden: "
                f"actual={a[field]!r} != golden={e[field]!r}. Xem docstring module test này "
                "trước khi sửa gì — KHÔNG tự regenerate golden để test xanh lại."
            )

        prob_diff = abs(a["regime_probability"] - e["regime_probability"])
        assert prob_diff < _PROBABILITY_TOLERANCE, (
            f"Bar {i} (bar_index={a['bar_index']}), regime_probability lệch golden quá dung "
            f"sai: actual={a['regime_probability']!r} golden={e['regime_probability']!r} "
            f"(chênh {prob_diff:.3e} >= {_PROBABILITY_TOLERANCE:.0e})."
        )


def _regenerate() -> None:
    """CHỈ chạy thủ công (`python tests/test_forward_golden.py --regenerate`),
    không bao giờ tự động, không bao giờ gọi từ pytest. Ghi đè
    `tests/golden/forward_baseline.json` bằng output hiện tại của pipeline.

    Trước khi chạy lệnh này: đọc docstring module — nếu golden test đang
    FAIL vì một thay đổi CỐ Ý trong core/, phải ghi vào docs/DECISIONS.md
    (thí nghiệm forward cũ kết thúc ở đâu, vì sao) TRƯỚC, không phải sau.
    """
    records = _run_golden_pipeline()
    payload = {
        "meta": {
            "seed": _SEED,
            "n_bars": _N_BARS,
            "train_bars": _TRAIN_BARS,
            "n_predict_bars": _N_PREDICT_BARS,
            "feature_subset": list(FEATURE_SUBSET),
            "hmm_n_candidates": _HMM_N_CANDIDATES,
            "hmm_n_init": _HMM_N_INIT,
            "note": (
                "Golden baseline cho tests/test_forward_golden.py — regenerate CHỈ khi đã "
                "ghi lý do vào docs/DECISIONS.md. Xem docstring module test."
            ),
        },
        "bars": records,
    }
    _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GOLDEN_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Đã ghi {_GOLDEN_PATH} ({len(records)} bar). Nhớ commit kèm lý do trong DECISIONS.md.")


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        _regenerate()
    else:
        print(__doc__)
        print("Chạy `python tests/test_forward_golden.py --regenerate` để tạo/ghi đè golden file.")
        sys.exit(1)
