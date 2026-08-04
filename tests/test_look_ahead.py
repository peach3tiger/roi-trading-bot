"""tests.test_look_ahead — BẮT BUỘC. Chứng minh predict_regime_filtered
không có look-ahead bias, và Viterbi (`model.predict()`) CÓ (để chứng
minh test có tác dụng).

Đây là chi tiết kỹ thuật quan trọng nhất của toàn bộ dự án — xem CLAUDE.md
bất biến #1. File này KHÔNG được skip, không được xfail, không được
comment out (bất biến #15).

Dữ liệu tổng hợp, không phụ thuộc mạng/`data/cache/` (thư mục đó nằm
trong .gitignore — một clone mới sẽ không có sẵn dữ liệu thật). Chuỗi
được dựng cố ý xen kẽ hai khối "chế độ" tương phản rõ để Viterbi có động
cơ thực sự sửa lại quá khứ khi thấy thêm dữ liệu tương lai — một chuỗi
phẳng/ngẫu nhiên thuần sẽ khiến hai phương pháp tình cờ trùng nhau và test
không chứng minh được gì.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.hmm_engine import HMMRegimeEngine

_REGIME_BLOCK_LEN = 30
_N_BARS = 400
_CUT_AT = _REGIME_BLOCK_LEN  # đúng tại biên chuyển chế độ — nơi bằng chứng nhân
# quả (chỉ nhìn quá khứ) còn mơ hồ nhất, và Viterbi có nhiều động cơ nhất để
# sửa lại phán đoán một khi thấy được 50 bar tương lai xác nhận chế độ mới.
_EXTRA_BARS = 50


def _make_synthetic_features(n_bars: int = _N_BARS, seed: int = 7) -> pd.DataFrame:
    """Hai chiều, hai chế độ xen kẽ theo khối 30 bar, tương phản mờ có chủ
    đích (mean ±0.5, scale 1.0) — đủ để HMM học được cấu trúc, nhưng đủ mơ
    hồ ở gần biên chuyển chế độ để Viterbi thực sự sửa lại quá khứ khi thấy
    thêm dữ liệu tương lai (tương phản quá rõ khiến cả hai phương pháp luôn
    đồng thuận, và test thứ hai không chứng minh được gì).

    Cột đầu đặt tên `log_return_1` để khớp quy ước của
    `data/feature_engineering.py` — `_build_regime_infos()` của
    `HMMRegimeEngine` cần cột này để tính expected_return/expected_volatility.
    """
    rng = np.random.default_rng(seed)
    blocks = []
    state = 0
    for start in range(0, n_bars, _REGIME_BLOCK_LEN):
        length = min(_REGIME_BLOCK_LEN, n_bars - start)
        mean = np.array([0.5, 0.35]) if state == 0 else np.array([-0.5, -0.35])
        blocks.append(rng.normal(loc=mean, scale=1.0, size=(length, 2)))
        state = 1 - state
    X = np.vstack(blocks)
    index = pd.date_range("2020-01-01", periods=n_bars, freq="D", tz="UTC")
    return pd.DataFrame(X, index=index, columns=["log_return_1", "adx_14"])


@pytest.fixture(scope="module")
def trained_engine() -> tuple[HMMRegimeEngine, pd.DataFrame]:
    features = _make_synthetic_features()
    engine = HMMRegimeEngine(
        n_candidates=[3],
        n_init=3,
        covariance_type="full",
        min_train_bars=100,
        stability_bars=3,
        flicker_window=20,
        flicker_threshold=4,
    )
    engine.select_and_train(features)
    return engine, features


def test_no_look_ahead_bias(trained_engine: tuple[HMMRegimeEngine, pd.DataFrame]) -> None:
    """Chạy predict_regime_filtered trên dữ liệu tới bar N. Chạy lại trên
    dữ liệu tới bar N+50, cắt lấy kết quả tại bar N. Hai kết quả PHẢI
    giống hệt nhau. Nếu khác → có look-ahead bias → dừng lại, sửa trước
    khi đi tiếp.
    """
    engine, features = trained_engine
    n = _CUT_AT

    short_history = engine.predict_regime_filtered_history(features.iloc[: n + 1])
    long_history = engine.predict_regime_filtered_history(features.iloc[: n + 1 + _EXTRA_BARS])

    result_short = short_history.iloc[-1]
    result_long_at_n = long_history.iloc[n]

    proba_cols = [c for c in short_history.columns if c.startswith("state_")]
    np.testing.assert_allclose(
        result_short[proba_cols].to_numpy(dtype=float),
        result_long_at_n[proba_cols].to_numpy(dtype=float),
        atol=1e-10,
        err_msg="P(state_N | obs_1:N) đổi khi thêm dữ liệu sau bar N — có look-ahead bias.",
    )
    assert result_short["state_id"] == result_long_at_n["state_id"]
    assert result_short["label"] == result_long_at_n["label"]


def test_viterbi_does_have_look_ahead(trained_engine: tuple[HMMRegimeEngine, pd.DataFrame]) -> None:
    """Cùng phép so sánh nhưng dùng `model.predict()` (Viterbi) trực tiếp
    — PHẢI khác ở ít nhất một bar trong đoạn đã có ở cả hai lần chạy,
    chứng minh test ở trên thật sự có tác dụng: nếu Viterbi cũng luôn cho
    cùng kết quả bất kể có thêm dữ liệu tương lai hay không, phép so sánh
    "giống nhau = không look-ahead" sẽ vô nghĩa vì mọi phương pháp đều
    "giống nhau" trên chuỗi này.

    Đây là chỗ DUY NHẤT trong toàn bộ codebase được phép gọi
    `model.predict()` — cố tình dùng để chứng minh nó SAI, không phải để
    suy luận thật (xem CLAUDE.md bất biến #1).
    """
    engine, features = trained_engine
    n = _CUT_AT
    assert engine.model is not None

    short_states = engine.model.predict(features.iloc[: n + 1].to_numpy())
    long_states = engine.model.predict(features.iloc[: n + 1 + _EXTRA_BARS].to_numpy())

    # Không bắt buộc mismatch phải nằm đúng tại bar N — chỉ cần Viterbi SỬA LẠI
    # một phần nào đó của đoạn [0, N] khi thấy thêm dữ liệu tương lai là đủ để
    # phản chứng "Viterbi không có look-ahead bias".
    changed_anywhere = not np.array_equal(short_states, long_states[: n + 1])

    assert changed_anywhere, (
        "Viterbi cho kết quả giống hệt filtered ở MỌI bar khi thêm 50 bar tương lai — "
        "chuỗi test chưa đủ tương phản để phân biệt hai phương pháp; cần tăng độ tương "
        "phản giữa các regime hoặc chọn lại điểm cắt N."
    )
