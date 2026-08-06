"""tests.test_hmm — HMMRegimeEngine: BIC model selection, gán nhãn regime,
bộ lọc ổn định, flicker rate.

Bốn test này tồn tại dưới dạng `pytest.skip("TODO: Phase 2")` từ lúc
`core/hmm_engine.py` còn là stub — module đó đã implement đầy đủ từ lâu,
nhưng test chưa bao giờ được viết lại. `test_look_ahead.py` xanh chỉ
chứng minh forward algorithm không nhìn tương lai; nó KHÔNG phủ được BIC
có thật sự chọn ứng viên thấp nhất hay không, nhãn regime có thật sự sắp
theo return hay không, hay bộ lọc ổn định/flicker có tính đúng hay không —
bốn thứ mà `forward/logger.py` phụ thuộc trực tiếp mỗi ngày.

`test_stability_filter_delays_confirmation`/`test_flicker_rate_computation`
gọi thẳng `_update_stability()` (private) thay vì đi qua
`predict_regime_filtered()` — cố tình: `predict_regime_filtered()` suy ra
`raw_state` từ forward algorithm trên `self.model`, và ép argmax đi đúng
một chuỗi trạng thái mong muốn qua nhiều bar liên tiếp đòi hỏi tinh chỉnh
means_/covars_/transmat_ rất mong manh, dễ flaky. `_update_stability()` là
state machine THUẦN (không đụng self.model), nhận thẳng raw_state — kiểm
tra đúng cơ chế hysteresis mà không phụ thuộc vào việc suy luận state từ
dữ liệu có ra đúng chuỗi mong muốn hay không.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hmmlearn.hmm import GaussianHMM

from core.hmm_engine import HMMRegimeEngine

_ENGINE_DEFAULTS: dict = dict(
    n_candidates=[3],
    n_init=1,
    covariance_type="diag",
    min_train_bars=1,
    stability_bars=3,
    flicker_window=20,
    flicker_threshold=4,
)


def _make_engine(**overrides: object) -> HMMRegimeEngine:
    kwargs = {**_ENGINE_DEFAULTS, **overrides}
    return HMMRegimeEngine(**kwargs)


# ----------------------------------------------------------------------
# BIC model selection
# ----------------------------------------------------------------------


def test_bic_selects_lowest_score() -> None:
    """Dữ liệu nhiễu i.i.d. thuần (không có cấu trúc regime thật) — một
    model 1 state phải có BIC thấp hơn NHIỀU so với model 5 state: cả hai
    đạt log-likelihood gần như nhau (không có gì để 5 state "học" thêm
    ngoài khớp nhiễu), nhưng 5 state tốn nhiều tham số hơn hẳn (means +
    covars + transmat 5x5) nên bị phạt nặng. Kỳ vọng này ổn định, không
    phụ thuộc seed — không phải may rủi thống kê.

    Cố tình đặt candidate "phải thua" (5) TRƯỚC candidate "phải thắng" (1)
    trong `n_candidates` — nếu code có bug kiểu "luôn chọn ứng viên đầu
    tiên", test này bắt được; đặt đúng thứ tự (1 trước) sẽ không phân biệt
    được hai loại bug đó.
    """
    rng = np.random.default_rng(42)
    features = pd.DataFrame({"log_return_1": rng.normal(0.0, 1.0, size=250)})

    engine = _make_engine(n_candidates=[5, 1], n_init=2, covariance_type="diag")
    best_model, bic_results = engine.scan_bic(features)

    assert {r.n_components for r in bic_results} == {5, 1}

    lowest = min(bic_results, key=lambda r: r.bic)
    assert lowest.n_components == 1, (
        f"kỳ vọng n_components=1 thắng trên nhiễu i.i.d., BIC thật: "
        f"{[(r.n_components, r.bic) for r in bic_results]}"
    )
    # Model trả về PHẢI khớp đúng candidate có BIC thấp nhất đã ghi lại —
    # đây là phần "chọn" thật sự, tách khỏi phần "BIC nào thấp nhất".
    assert best_model.n_components == lowest.n_components
    assert best_model.bic(features.to_numpy()) == pytest.approx(lowest.bic)


def test_scan_bic_does_not_mutate_engine_state() -> None:
    """`scan_bic()` tách riêng khỏi `select_and_train()` đúng như docstring
    hứa — không được đụng vào self.model/self.bic_results của engine đang
    phục vụ suy luận online."""
    rng = np.random.default_rng(7)
    features = pd.DataFrame({"log_return_1": rng.normal(0.0, 1.0, size=100)})
    engine = _make_engine(n_candidates=[1, 2], n_init=1)

    engine.scan_bic(features)

    assert engine.model is None
    assert engine.bic_results == []


# ----------------------------------------------------------------------
# Gán nhãn regime — sắp theo return, KHÔNG theo state index thô
# ----------------------------------------------------------------------


def _fake_model(
    n_components: int, covariance_type: str, means: np.ndarray, covars: np.ndarray
) -> GaussianHMM:
    """Model KHÔNG fit thật — chỉ để kiểm tra `_build_regime_infos()` đọc
    đúng means_/covars_ đã fit, không cần chạy EM (nhanh, xác định).

    `n_features` bình thường được `GaussianHMM._check()` set trong lúc
    `.fit()` (= `means_.shape[1]`) — set tay ở đây vì bỏ qua fit thật;
    thiếu nó thì property `covars_` (đọc lại từ `_covars_` nội bộ qua
    `fill_covars()`) raise AttributeError — xác nhận bằng chạy thật, không
    suy luận từ tài liệu hmmlearn."""
    model = GaussianHMM(n_components=n_components, covariance_type=covariance_type)
    model.means_ = means
    model.n_features = means.shape[1]
    model.covars_ = covars
    return model


@pytest.mark.parametrize("covariance_type", ["full", "diag", "tied", "spherical"])
def test_extract_variances_matches_covars_diagonal_for_every_covariance_type(
    covariance_type: str,
) -> None:
    """Bug thật đã sửa ở `core/hmm_engine.py::_extract_variances` (không
    phải đọc code — lộ ra khi viết chính test này): `model.covars_`
    (property công khai hmmlearn) LUÔN trả về ma trận ĐẦY ĐỦ
    `(n_components, n_features, n_features)` bất kể `covariance_type` —
    bản cũ giả định shape gọn theo từng loại, chỉ đúng tình cờ ở "full".
    Fit THẬT (không phải model gán tay) cho cả 4 loại để xác nhận không
    còn lệch, kể cả với `_covars_` nội bộ có shape khác nhau thật sự."""
    rng = np.random.default_rng(3)
    X = rng.normal(size=(150, 2))
    engine = _make_engine(n_candidates=[2], n_init=1, covariance_type=covariance_type)
    engine.feature_names = ["log_return_1", "other_feature"]
    model = GaussianHMM(n_components=2, covariance_type=covariance_type, n_iter=10, random_state=0)
    model.fit(X)
    engine.model = model

    for feature_idx in (0, 1):
        variances = engine._extract_variances(feature_idx)
        expected = np.array([model.covars_[s, feature_idx, feature_idx] for s in range(2)])
        np.testing.assert_allclose(variances, expected)
        assert (variances >= 0).all()


def test_regime_labels_sorted_by_return() -> None:
    """3 state, cố tình xáo trộn: state_id 0 có mean return CAO NHẤT
    (phải thành "BULL"), state_id 1 THẤP NHẤT ("BEAR"), state_id 2 giữa
    ("NEUTRAL") — state_id thô KHÔNG khớp thứ tự rank. Nếu code gán nhãn
    theo state_id thay vì theo rank của mean return, test này bắt được."""
    engine = _make_engine(n_candidates=[3])
    engine.feature_names = ["log_return_1", "realized_vol_20"]
    engine.model = _fake_model(
        n_components=3,
        covariance_type="diag",
        means=np.array([[0.8, 0.0], [-0.5, 0.0], [0.0, 0.0]]),
        covars=np.array([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]]),
    )

    infos = engine._build_regime_infos()

    by_id = {info.regime_id: info for info in infos}
    assert by_id[0].regime_name == "BULL"
    assert by_id[1].regime_name == "BEAR"
    assert by_id[2].regime_name == "NEUTRAL"
    assert by_id[0].expected_return == pytest.approx(0.8)
    assert by_id[1].expected_return == pytest.approx(-0.5)
    assert by_id[2].expected_return == pytest.approx(0.0)
    # _build_regime_infos() trả về đã sort theo regime_id, không phải theo rank.
    assert [info.regime_id for info in infos] == [0, 1, 2]


def test_regime_labels_use_correct_label_set_per_n_components() -> None:
    """4 state dùng bộ nhãn khác 3 state (_REGIME_LABELS[4], có "CRASH"/
    "EUPHORIA" ở hai đầu) — xác nhận không lẫn bộ nhãn giữa các n_components."""
    engine = _make_engine(n_candidates=[4])
    engine.feature_names = ["log_return_1"]
    engine.model = _fake_model(
        n_components=4,
        covariance_type="diag",
        means=np.array([[-1.0], [-0.2], [0.2], [1.0]]),
        covars=np.array([[1.0], [1.0], [1.0], [1.0]]),
    )

    infos = engine._build_regime_infos()

    by_id = {info.regime_id: info.regime_name for info in infos}
    assert by_id == {0: "CRASH", 1: "BEAR", 2: "BULL", 3: "EUPHORIA"}


def test_vol_rank_assigns_low_high_mid_vol_independent_of_return_rank() -> None:
    """Vol rank sắp ĐỘC LẬP với return rank (docstring _build_regime_infos)
    — state có return cao nhất nhưng volatility THẤP nhất vẫn phải là
    LOW_VOL, không lây theo rank return."""
    engine = _make_engine(n_candidates=[3])
    engine.feature_names = ["log_return_1"]
    engine.model = _fake_model(
        n_components=3,
        covariance_type="diag",
        # return: state0 thấp nhất, state1 giữa, state2 cao nhất
        # volatility (variance): state0 CAO nhất, state1 giữa, state2 THẤP nhất
        # -> ngược chiều nhau hoàn toàn
        means=np.array([[-1.0], [0.0], [1.0]]),
        covars=np.array([[9.0], [1.0], [0.01]]),
    )

    infos = engine._build_regime_infos()
    by_id = {info.regime_id: info for info in infos}

    assert "HIGH_VOL" in by_id[0].recommended_strategy_type  # return thấp nhất, vol cao nhất
    assert "LOW_VOL" in by_id[2].recommended_strategy_type  # return cao nhất, vol thấp nhất


# ----------------------------------------------------------------------
# Bộ lọc ổn định (hysteresis) — §2.6
# ----------------------------------------------------------------------


def test_stability_filter_delays_confirmation() -> None:
    engine = _make_engine(stability_bars=3)

    # Bar đầu tiên — chưa có gì để so sánh, xác nhận ngay lập tức.
    assert engine._update_stability(0) is True
    assert engine._current_confirmed_state == 0
    assert engine._confirmed_bars_count == 1

    # raw_state đổi sang 1, nhưng chưa đủ stability_bars=3 liên tiếp —
    # PHẢI vẫn báo cáo state cũ (0), is_confirmed=False.
    assert engine._update_stability(1) is False
    assert engine._current_confirmed_state == 0

    assert engine._update_stability(1) is False
    assert engine._current_confirmed_state == 0

    # Bar thứ 3 liên tiếp ở state 1 — đủ ngưỡng, XÁC NHẬN đổi.
    assert engine._update_stability(1) is True
    assert engine._current_confirmed_state == 1
    assert engine._confirmed_bars_count == 3
    assert engine.detect_regime_change() is True


def test_stability_filter_resets_pending_on_interrupted_sequence() -> None:
    """raw_state đổi sang 1 (1 bar), rồi quay lại 0 TRƯỚC khi đủ
    stability_bars — bộ đếm pending phải reset, không được cộng dồn hai
    ứng viên khác nhau vào cùng một bộ đếm."""
    engine = _make_engine(stability_bars=3)
    engine._update_stability(0)  # xác lập state 0

    engine._update_stability(1)  # pending=1, count=1
    assert engine._pending_state == 1
    assert engine._pending_bars_count == 1

    engine._update_stability(0)  # quay lại state đã confirmed — reset pending
    assert engine._pending_state is None
    assert engine._pending_bars_count == 0
    assert engine._current_confirmed_state == 0  # chưa hề đổi

    # Giờ thử lại từ đầu với 1 — phải mất đủ 3 bar liên tiếp MỚI, không
    # được tính gộp với lần thử trước đó.
    assert engine._update_stability(1) is False
    assert engine._update_stability(1) is False
    assert engine._update_stability(1) is True
    assert engine._current_confirmed_state == 1


def test_get_regime_stability_tracks_confirmed_bars_count() -> None:
    engine = _make_engine(stability_bars=1)
    engine._update_stability(0)
    assert engine.get_regime_stability() == 1
    engine._update_stability(0)
    assert engine.get_regime_stability() == 2
    engine._update_stability(0)
    assert engine.get_regime_stability() == 3


# ----------------------------------------------------------------------
# Flicker rate — §2.6, đếm số lần ĐÃ XÁC NHẬN đổi trong flicker_window
# ----------------------------------------------------------------------


def test_flicker_rate_computation() -> None:
    """stability_bars=1 để mọi raw_state khác state hiện tại confirm
    NGAY (cô lập việc đếm cửa sổ trượt khỏi việc chờ hysteresis, đã có
    test riêng ở test_stability_filter_delays_confirmation)."""
    engine = _make_engine(stability_bars=1, flicker_window=5, flicker_threshold=2)

    # Bar 1: xác lập lần đầu — KHÔNG tính là một lần "đổi" (change_history=[F]).
    engine._update_stability(0)
    assert engine.get_regime_flicker_rate() == 0.0
    assert engine.is_flickering() is False

    # 4 bar tiếp theo đổi liên tục 1,0,1,0 — với stability_bars=1, mỗi lần
    # đều confirm ngay -> 4 lần đổi liên tiếp trong cửa sổ 5 bar gần nhất.
    for raw in (1, 0, 1, 0):
        engine._update_stability(raw)
    assert engine.get_regime_flicker_rate() == 4.0
    assert engine.is_flickering() is True  # 4 > threshold 2

    # Đứng yên (không đổi) — cửa sổ trượt, các lần "đổi" cũ dần rơi khỏi
    # flicker_window, flicker rate phải GIẢM dần theo thời gian.
    engine._update_stability(0)  # không đổi (đã ở state 0)
    assert engine.get_regime_flicker_rate() == 4.0  # entry F mới thay entry F cũ nhất, net không đổi

    engine._update_stability(0)
    assert engine.get_regime_flicker_rate() == 3.0

    engine._update_stability(0)
    assert engine.get_regime_flicker_rate() == 2.0
    # Đúng ngưỡng biên: > threshold mới flickering, == thì KHÔNG.
    assert engine.is_flickering() is False


def test_flicker_window_caps_history_length() -> None:
    """change_history không được phình vô hạn — luôn bị cắt về đúng
    flicker_window phần tử gần nhất, bất kể chạy bao nhiêu bar."""
    engine = _make_engine(stability_bars=1, flicker_window=3)
    for raw in range(20):
        engine._update_stability(raw % 2)

    assert len(engine._change_history) == 3
