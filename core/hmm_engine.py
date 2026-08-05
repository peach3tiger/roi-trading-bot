"""core.hmm_engine — Gaussian HMM regime classifier.

HMM ở đây là bộ phân loại biến động (volatility classifier), không phải bộ
dự báo hướng giá. Nó xác định thị trường đang ở môi trường vol thấp, trung
bình hay cao; tầng strategy dùng phân loại đó để đặt tỷ trọng danh mục.

BẤT BIẾN QUAN TRỌNG NHẤT CỦA MODULE NÀY: không bao giờ dùng phương thức
`predict` hay `decode` của hmmlearn để suy luận regime tại thời điểm hiện
tại. Cả hai đều chạy thuật toán Viterbi trên toàn chuỗi và sửa lại các trạng thái
quá khứ bằng dữ liệu tương lai — đó là look-ahead bias, và nó sẽ làm
backtest đẹp một cách giả tạo trong khi live trading thất bại. Suy luận
online CHỈ được thực hiện qua `predict_regime_filtered`, cài đặt forward
algorithm, chỉ dùng dữ liệu tới thời điểm hiện tại (xem CLAUDE.md bất
biến #1 và tests/test_look_ahead.py).

Để gán nhãn regime (§2.8) và tính expected_return/expected_volatility, ta
đọc trực tiếp `means_`/`covars_` đã fit của model — KHÔNG suy luận per-bar
bằng predict()/decode()/predict_proba(). Đây là mô tả THAM SỐ của phân
phối mỗi state, không phải một phép gán trạng thái cho dữ liệu train, nên
không có khái niệm look-ahead ở đây và không cần đụng tới bất kỳ phương
thức suy luận nào của hmmlearn.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp

logger = logging.getLogger(__name__)

# Nhãn regime, sắp theo mean return TĂNG DẦN — chỉ để người đọc, không
# điều khiển quyết định giao dịch (xem §2.2 của brain doc). StrategyOrchestrator
# sắp theo volatility một cách ĐỘC LẬP và bỏ qua nhãn này hoàn toàn.
_REGIME_LABELS: dict[int, list[str]] = {
    3: ["BEAR", "NEUTRAL", "BULL"],
    4: ["CRASH", "BEAR", "BULL", "EUPHORIA"],
    5: ["CRASH", "BEAR", "NEUTRAL", "BULL", "EUPHORIA"],
    6: ["CRASH", "STRONG_BEAR", "WEAK_BEAR", "WEAK_BULL", "STRONG_BULL", "EUPHORIA"],
    7: ["CRASH", "STRONG_BEAR", "WEAK_BEAR", "NEUTRAL", "WEAK_BULL", "STRONG_BULL", "EUPHORIA"],
}

_RETURN_FEATURE_NAME = "log_return_1"
_LOG_FLOOR = 1e-300  # tránh log(0) khi startprob_/transmat_ có xác suất 0 tuyệt đối
_EM_MAX_ITER = 200
_EM_TOL = 1e-4


@dataclass(frozen=True)
class RegimeInfo:
    """Metadata mô tả một regime đã train, không đổi giữa các bar.

    Sắp theo `expected_volatility` (không phải return) để ánh xạ sang
    strategy — xem core/regime_strategies.py.

    `expected_return`/`expected_volatility` là mean/std CỦA FEATURE
    `log_return_1` ĐÃ Z-SCORE (đơn vị tương đối, không phải % thật) — lấy
    trực tiếp từ `means_`/`covars_` của model, dùng để XẾP HẠNG các state
    với nhau, không phải để đọc như lợi nhuận kỳ vọng thực tế.

    `recommended_strategy_type`/`max_allocation_pct`/`min_confidence_to_act`
    là giá trị TẠM THỜI mang tính mô tả — StrategyOrchestrator ở Phase 4
    mới là nơi thật sự gán strategy class theo vol_rank (§3 của brain doc).
    """

    regime_id: int
    regime_name: str
    expected_return: float
    expected_volatility: float
    recommended_strategy_type: str
    max_allocation_pct: float
    min_confidence_to_act: float


@dataclass(frozen=True)
class RegimeState:
    """Kết quả suy luận regime tại một bar cụ thể — output của filtered inference."""

    label: str
    state_id: int
    probability: float
    state_probabilities: np.ndarray
    timestamp: datetime
    is_confirmed: bool
    consecutive_bars: int


@dataclass(frozen=True)
class BICCandidateResult:
    """Kết quả BIC của một ứng viên n_components — bản ghi tốt nhất trong
    n_init lần khởi tạo ngẫu nhiên cho ứng viên đó."""

    n_components: int
    bic: float
    log_likelihood: float
    converged: bool
    n_iter: int
    n_params: int


@dataclass
class _AlphaCache:
    """Cache log-alpha để suy luận tăng dần không phải tính lại từ đầu mỗi
    bar khi input là phần nối dài chính xác của lần gọi trước."""

    index: pd.Index
    log_alpha: np.ndarray


class HMMRegimeEngine:
    """Chọn model bằng BIC, train, và suy luận regime không look-ahead.

    Model tự chọn số regime trong `n_candidates` bằng BIC thay vì cố định,
    vì độ phức tạp thị trường thay đổi theo giai đoạn — ép cố định
    n_components là một dạng bias lựa chọn ẩn.
    """

    model: GaussianHMM | None
    _alpha_cache: _AlphaCache | None
    _current_confirmed_state: int | None
    _confirmed_bars_count: int
    _pending_state: int | None
    _pending_bars_count: int
    _just_changed: bool
    _change_history: list[bool]

    def __init__(
        self,
        n_candidates: list[int],
        n_init: int,
        covariance_type: str,
        min_train_bars: int,
        stability_bars: int,
        flicker_window: int,
        flicker_threshold: int,
    ) -> None:
        self.n_candidates = list(n_candidates)
        self.n_init = n_init
        self.covariance_type = covariance_type
        self.min_train_bars = min_train_bars
        self.stability_bars = stability_bars
        self.flicker_window = flicker_window
        self.flicker_threshold = flicker_threshold

        self.model = None
        self.feature_names: list[str] = []
        self.regime_infos: list[RegimeInfo] = []
        self.bic_results: list[BICCandidateResult] = []
        self.training_date: datetime | None = None
        self.data_hash: str | None = None

        self._label_by_state: dict[int, str] = {}
        self._reset_online_state()

    # ------------------------------------------------------------------
    # Training / model selection
    # ------------------------------------------------------------------

    def select_and_train(self, features: pd.DataFrame) -> list[RegimeInfo]:
        """Thử mọi n_components trong n_candidates, chọn BIC thấp nhất, và
        gán nhãn regime cho model thắng cuộc.

        Log toàn bộ BIC của mọi ứng viên và cái nào được chọn — bắt buộc,
        đây là bằng chứng chọn model có kỷ luật (xem CLAUDE.md bất biến #13).

        Gán nhãn (`_build_regime_infos`) chỉ định nghĩa cho n_components
        trong `_REGIME_LABELS` (3-7, đúng dải trong settings.yaml/spec).
        Muốn quét BIC ở dải rộng hơn để khảo sát/đối chiếu (không cần
        nhãn) — dùng `scan_bic()` trực tiếp.
        """
        if len(features) < self.min_train_bars:
            raise ValueError(
                f"Cần tối thiểu {self.min_train_bars} bar để train, chỉ có {len(features)}."
            )

        self.feature_names = list(features.columns)
        self.model, self.bic_results = self.scan_bic(features)
        self.training_date = datetime.now(timezone.utc)
        self.data_hash = self._compute_data_hash(features)
        self.regime_infos = self._build_regime_infos()
        self._label_by_state = {info.regime_id: info.regime_name for info in self.regime_infos}

        # Model mới → mọi trạng thái suy luận trực tuyến cũ (cache alpha,
        # bộ đếm ổn định, lịch sử flicker) không còn hợp lệ.
        self._reset_online_state()

        return self.regime_infos

    def scan_bic(self, features: pd.DataFrame) -> tuple[GaussianHMM, list[BICCandidateResult]]:
        """Thử mọi n_components trong n_candidates, trả về model có BIC
        thấp nhất kèm bảng BIC đầy đủ — KHÔNG gán nhãn, KHÔNG mutate
        trạng thái của engine (`self.model`, `self.bic_results`,...).

        Tách riêng khỏi `select_and_train` để dùng cho khảo sát/ablation
        (ví dụ quét n_components rộng hơn dải đã có nhãn, hoặc so sánh
        covariance_type khác) mà không đụng tới model đang phục vụ suy
        luận trực tuyến.

        hmmlearn không có tham số `n_init` — tự implement bằng vòng lặp
        random restart: mỗi ứng viên chạy `n_init` lần khởi tạo ngẫu
        nhiên khác nhau (EM dễ kẹt local optimum), giữ lại lần có
        log-likelihood cao nhất, rồi mới tính BIC của ứng viên đó.
        """
        X = features.to_numpy()

        bic_results: list[BICCandidateResult] = []
        best_model: GaussianHMM | None = None
        best_bic = np.inf

        for n_components in self.n_candidates:
            best_restart: GaussianHMM | None = None
            best_restart_score = -np.inf

            for seed in range(self.n_init):
                candidate = GaussianHMM(
                    n_components=n_components,
                    covariance_type=self.covariance_type,
                    n_iter=_EM_MAX_ITER,
                    tol=_EM_TOL,
                    random_state=seed,
                )
                candidate.fit(X)
                score = candidate.score(X)
                if score > best_restart_score:
                    best_restart_score = score
                    best_restart = candidate

            assert best_restart is not None
            bic = best_restart.bic(X)
            n_params = sum(best_restart._get_n_fit_scalars_per_param().values())  # noqa: SLF001
            result = BICCandidateResult(
                n_components=n_components,
                bic=bic,
                log_likelihood=best_restart_score,
                converged=bool(best_restart.monitor_.converged),
                n_iter=best_restart.monitor_.iter,
                n_params=n_params,
            )
            bic_results.append(result)
            logger.info(
                "HMM candidate n_components=%d covariance_type=%s bic=%.2f "
                "log_likelihood=%.2f n_params=%d converged=%s n_iter=%d (best of %d restarts)",
                n_components,
                self.covariance_type,
                bic,
                best_restart_score,
                n_params,
                result.converged,
                result.n_iter,
                self.n_init,
            )

            if bic < best_bic:
                best_bic = bic
                best_model = best_restart

        assert best_model is not None
        logger.info(
            "BIC thấp nhất: n_components=%d covariance_type=%s (BIC=%.2f)",
            best_model.n_components,
            self.covariance_type,
            best_bic,
        )

        return best_model, bic_results

    def _build_regime_infos(self) -> list[RegimeInfo]:
        """Đọc trực tiếp means_/covars_ của model đã fit — KHÔNG per-bar
        state assignment, nên không đụng tới predict()/decode()."""
        assert self.model is not None
        n_components = self.model.n_components

        return_idx = self.feature_names.index(_RETURN_FEATURE_NAME)
        means = self.model.means_[:, return_idx]
        variances = self._extract_variances(return_idx)

        order = np.argsort(means)  # tăng dần theo mean return
        labels_by_rank = _REGIME_LABELS[n_components]

        vol_order = np.argsort(variances)  # tăng dần theo volatility — ĐỘC LẬP với `order`
        vol_rank_of_state = {int(state): int(rank) for rank, state in enumerate(vol_order)}

        infos: list[RegimeInfo] = []
        for rank, state_id in enumerate(order):
            state_id = int(state_id)
            vol_rank = vol_rank_of_state[state_id] / max(1, n_components - 1)
            if vol_rank <= 0.33:
                strategy_hint = "LOW_VOL (tạm thời — StrategyOrchestrator gán chính thức ở Phase 4)"
                max_alloc = 0.95
            elif vol_rank >= 0.67:
                strategy_hint = "HIGH_VOL (tạm thời — StrategyOrchestrator gán chính thức ở Phase 4)"
                max_alloc = 0.50
            else:
                strategy_hint = "MID_VOL (tạm thời — StrategyOrchestrator gán chính thức ở Phase 4)"
                max_alloc = 0.95

            infos.append(
                RegimeInfo(
                    regime_id=state_id,
                    regime_name=labels_by_rank[rank],
                    expected_return=float(means[state_id]),
                    expected_volatility=float(np.sqrt(max(variances[state_id], 0.0))),
                    recommended_strategy_type=strategy_hint,
                    max_allocation_pct=max_alloc,
                    min_confidence_to_act=0.55,
                )
            )

        return sorted(infos, key=lambda info: info.regime_id)

    def _extract_variances(self, feature_idx: int) -> np.ndarray:
        """Variance của một feature cụ thể theo từng state, theo đúng
        shape của `covars_` ứng với `covariance_type` của model."""
        assert self.model is not None
        covars = self.model.covars_
        if self.covariance_type == "full":
            return np.asarray([covars[s, feature_idx, feature_idx] for s in range(self.model.n_components)])
        if self.covariance_type == "diag":
            return np.asarray(covars[:, feature_idx])
        if self.covariance_type == "tied":
            value = covars[feature_idx, feature_idx]
            return np.full(self.model.n_components, value)
        # spherical: một variance dùng chung cho mọi feature của mỗi state
        return np.asarray(covars)

    # ------------------------------------------------------------------
    # Forward algorithm (pure, không cache) — nền tảng toán học
    # ------------------------------------------------------------------

    @staticmethod
    def _forward_step(
        log_alpha_prev: np.ndarray, log_transmat: np.ndarray, log_frame_t: np.ndarray
    ) -> np.ndarray:
        """Một bước đệ quy forward: alpha_t = (alpha_{t-1} @ transmat) * emission_t, trong log space."""
        result: np.ndarray = logsumexp(log_alpha_prev[:, None] + log_transmat, axis=0) + log_frame_t
        return result

    def _forward_log_alpha(self, X: np.ndarray) -> np.ndarray:
        """Forward pass đầy đủ trên toàn bộ X, KHÔNG cache — hàm THUẦN:
        cùng X luôn cho cùng log_alpha, không phụ thuộc trạng thái nội bộ
        của engine. Dùng làm nền tảng cho cả bản có cache lẫn
        `predict_regime_filtered_history`.
        """
        assert self.model is not None
        log_startprob = np.log(self.model.startprob_ + _LOG_FLOOR)
        log_transmat = np.log(self.model.transmat_ + _LOG_FLOOR)
        log_frame = self.model._compute_log_likelihood(X)  # noqa: SLF001 — hmmlearn không có API công khai tương đương

        n_obs, n_states = log_frame.shape
        log_alpha = np.empty((n_obs, n_states))
        log_alpha[0] = log_startprob + log_frame[0]
        for t in range(1, n_obs):
            log_alpha[t] = self._forward_step(log_alpha[t - 1], log_transmat, log_frame[t])
        return log_alpha

    def predict_regime_filtered_history(self, features: pd.DataFrame) -> pd.DataFrame:
        """P(state_t | obs_1:t) cho MỌI bar trong `features`, không cache,
        không tác dụng phụ lên trạng thái online (stability/flicker).

        Dùng để: (a) lấy toàn bộ lịch sử regime cho backtest, (b) kiểm
        chứng không look-ahead — so sánh kết quả tại bar N giữa hai lần
        chạy với dữ liệu dài ngắn khác nhau phải giống hệt nhau (xem
        tests/test_look_ahead.py).
        """
        if self.model is None:
            raise RuntimeError("Model chưa được train — gọi select_and_train() trước.")

        log_alpha = self._forward_log_alpha(features.to_numpy())
        log_norm = logsumexp(log_alpha, axis=1, keepdims=True)
        proba = np.exp(log_alpha - log_norm)
        state_ids = proba.argmax(axis=1)

        result = pd.DataFrame(
            proba,
            index=features.index,
            columns=[f"state_{i}_proba" for i in range(proba.shape[1])],
        )
        result["state_id"] = state_ids
        result["label"] = [self._label_by_state.get(int(s), str(s)) for s in state_ids]
        return result

    # ------------------------------------------------------------------
    # Suy luận trực tuyến (có cache + bộ lọc ổn định) — dùng trong live/backtest loop
    # ------------------------------------------------------------------

    def _filtered_proba_incremental(self, features_up_to_now: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model chưa được train — gọi select_and_train() trước.")
        if features_up_to_now.empty:
            raise ValueError("features_up_to_now rỗng")

        X = features_up_to_now.to_numpy()
        n_obs = X.shape[0]

        cached_len = 0 if self._alpha_cache is None else self._alpha_cache.log_alpha.shape[0]
        reuse = (
            self._alpha_cache is not None
            and cached_len <= n_obs
            and features_up_to_now.index[:cached_len].equals(self._alpha_cache.index)
        )

        log_transmat = np.log(self.model.transmat_ + _LOG_FLOOR)
        log_frame = self.model._compute_log_likelihood(X)  # noqa: SLF001

        log_alpha = np.empty((n_obs, self.model.n_components))
        if reuse and self._alpha_cache is not None and cached_len > 0:
            log_alpha[:cached_len] = self._alpha_cache.log_alpha
            start_t = cached_len
        else:
            log_startprob = np.log(self.model.startprob_ + _LOG_FLOOR)
            log_alpha[0] = log_startprob + log_frame[0]
            start_t = 1

        for t in range(start_t, n_obs):
            log_alpha[t] = self._forward_step(log_alpha[t - 1], log_transmat, log_frame[t])

        self._alpha_cache = _AlphaCache(index=features_up_to_now.index, log_alpha=log_alpha)

        log_norm = logsumexp(log_alpha[-1])
        proba: np.ndarray = np.exp(log_alpha[-1] - log_norm)
        return proba

    def predict_regime_filtered(self, features_up_to_now: pd.DataFrame) -> RegimeState:
        """Tính P(state_t | observations_1:t) bằng forward algorithm.

        CHỈ dùng dữ liệu quá khứ và hiện tại — không gọi phương thức
        `predict` hay `decode` ở đây, kể cả gián tiếp. Làm việc trong log
        space để tránh underflow trên chuỗi dài. Cache alpha của bar trước
        để suy luận tăng dần (incremental) trong vòng lặp live/backtest
        thay vì tính lại toàn chuỗi mỗi bar.

        Cập nhật bộ lọc ổn định (§2.6): trạng thái trả về (`state_id`,
        `label`) là regime ĐÃ XÁC NHẬN, không phải argmax thô — chỉ đổi
        sau khi trạng thái mới thắng thế `stability_bars` bar liên tiếp.
        """
        proba = self._filtered_proba_incremental(features_up_to_now)
        raw_state = int(np.argmax(proba))
        is_confirmed = self._update_stability(raw_state)

        state_id = self._current_confirmed_state
        assert state_id is not None
        timestamp = features_up_to_now.index[-1]
        if hasattr(timestamp, "to_pydatetime"):
            timestamp = timestamp.to_pydatetime()

        return RegimeState(
            label=self._label_by_state.get(state_id, str(state_id)),
            state_id=state_id,
            probability=float(proba[state_id]),
            state_probabilities=proba,
            timestamp=timestamp,
            is_confirmed=is_confirmed,
            consecutive_bars=self._confirmed_bars_count,
        )

    def predict_regime_proba(self, features_up_to_now: pd.DataFrame) -> np.ndarray:
        """Phân phối xác suất đầy đủ trên mọi state tại bar hiện tại.

        Đọc thuần tuý — không cập nhật bộ lọc ổn định/flicker (chỉ
        `predict_regime_filtered` mới làm việc đó, vì đó là lệnh gọi
        chính thức mỗi bar trong vòng lặp live/backtest).
        """
        return self._filtered_proba_incremental(features_up_to_now)

    def _update_stability(self, raw_state: int) -> bool:
        """Hysteresis: trạng thái mới phải thắng thế `stability_bars` bar
        liên tiếp mới được xác nhận; trong lúc đó vẫn báo cáo regime cũ.
        """
        self._just_changed = False

        if self._current_confirmed_state is None:
            self._current_confirmed_state = raw_state
            self._confirmed_bars_count = 1
            self._pending_state = None
            self._pending_bars_count = 0
            is_confirmed = True
        elif raw_state == self._current_confirmed_state:
            self._confirmed_bars_count += 1
            self._pending_state = None
            self._pending_bars_count = 0
            is_confirmed = True
        else:
            if self._pending_state == raw_state:
                self._pending_bars_count += 1
            else:
                self._pending_state = raw_state
                self._pending_bars_count = 1

            if self._pending_bars_count >= self.stability_bars:
                self._current_confirmed_state = raw_state
                self._confirmed_bars_count = self._pending_bars_count
                self._pending_state = None
                self._pending_bars_count = 0
                is_confirmed = True
                self._just_changed = True
            else:
                is_confirmed = False

        self._change_history.append(self._just_changed)
        if len(self._change_history) > self.flicker_window:
            self._change_history = self._change_history[-self.flicker_window :]

        return is_confirmed

    # ------------------------------------------------------------------
    # Getters trạng thái online
    # ------------------------------------------------------------------

    def get_regime_stability(self) -> int:
        """Số bar liên tiếp regime hiện tại đã được xác nhận."""
        return self._confirmed_bars_count

    def get_transition_matrix(self) -> np.ndarray:
        """`transmat_` của model đã train — để giải thích/debug, không dùng để suy luận online."""
        if self.model is None:
            raise RuntimeError("Model chưa được train — gọi select_and_train() trước.")
        transmat: np.ndarray = self.model.transmat_.copy()
        return transmat

    def detect_regime_change(self) -> bool:
        """True nếu regime vừa xác nhận khác với regime xác nhận trước đó
        (chỉ True đúng vào bar mà việc xác nhận xảy ra)."""
        return self._just_changed

    def get_regime_flicker_rate(self) -> float:
        """Số lần đổi regime (đã xác nhận) trong `flicker_window` bar gần nhất."""
        return float(sum(self._change_history))

    def is_flickering(self) -> bool:
        """True nếu flicker rate vượt `flicker_threshold` — bật uncertainty mode."""
        return self.get_regime_flicker_rate() > self.flicker_threshold

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Lưu model bằng pickle kèm metadata: n_regimes, bic, training_date,
        labels, feature_list, data_hash."""
        if self.model is None:
            raise RuntimeError("Chưa train model — không có gì để lưu.")

        payload = {
            "model": self.model,
            "feature_names": self.feature_names,
            "regime_infos": self.regime_infos,
            "label_by_state": self._label_by_state,
            "bic_results": self.bic_results,
            "training_date": self.training_date,
            "data_hash": self.data_hash,
            "config": {
                "n_candidates": self.n_candidates,
                "n_init": self.n_init,
                "covariance_type": self.covariance_type,
                "min_train_bars": self.min_train_bars,
                "stability_bars": self.stability_bars,
                "flicker_window": self.flicker_window,
                "flicker_threshold": self.flicker_threshold,
            },
        }
        Path(path).write_bytes(pickle.dumps(payload))

    def load(self, path: str) -> None:
        payload = pickle.loads(Path(path).read_bytes())

        self.model = payload["model"]
        self.feature_names = payload["feature_names"]
        self.regime_infos = payload["regime_infos"]
        self._label_by_state = payload["label_by_state"]
        self.bic_results = payload["bic_results"]
        self.training_date = payload["training_date"]
        self.data_hash = payload["data_hash"]

        cfg = payload["config"]
        self.n_candidates = cfg["n_candidates"]
        self.n_init = cfg["n_init"]
        self.covariance_type = cfg["covariance_type"]
        self.min_train_bars = cfg["min_train_bars"]
        self.stability_bars = cfg["stability_bars"]
        self.flicker_window = cfg["flicker_window"]
        self.flicker_threshold = cfg["flicker_threshold"]

        # Model vừa nạp từ đĩa — mọi lịch sử suy luận trực tuyến trước đó
        # thuộc về tiến trình khác, không còn ý nghĩa.
        self._reset_online_state()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_online_state(self) -> None:
        self._alpha_cache = None
        self._current_confirmed_state = None
        self._confirmed_bars_count = 0
        self._pending_state = None
        self._pending_bars_count = 0
        self._just_changed = False
        self._change_history = []

    @staticmethod
    def _compute_data_hash(features: pd.DataFrame) -> str:
        hasher = hashlib.sha256()
        hasher.update(pd.util.hash_pandas_object(features, index=True).to_numpy().tobytes())
        return hasher.hexdigest()
