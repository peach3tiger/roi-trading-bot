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
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp

logger = logging.getLogger(__name__)

# Ngưỡng gọi một lần EM là PHÂN KỲ. EM có tính chất log-likelihood đơn
# điệu tăng, nên mọi delta âm đều bất thường; ngưỡng 1.0 loại nhiễu làm
# tròn (|delta| điển hình của nhiễu ~1e-9) mà vẫn bắt trọn nhóm phân kỳ
# thật — đo được trên backtest kiểm định: |delta| trong nhóm > 1 có trung
# vị 78.9 và cực đại 128.8, không có giá trị nào rơi vào khoảng 1..10.
# Xem docs/DECISIONS.md "Phân kỳ EM trong backtest kiểm định".
_EM_DIVERGENCE_DELTA = 1.0

# Sàn cho `n_init` trong CẤU HÌNH SẢN XUẤT. Không phải tham số tốc độ.
#
# Toàn bộ lớp bảo vệ chống phân kỳ EM nằm ở vòng random restart: `scan_bic`
# giữ restart có log-likelihood cao nhất, và một fit đã phân kỳ thì
# log-likelihood tệ nên luôn thua. Ít restart hơn = lớp bảo vệ mỏng hơn.
#
# Suy ra từ số đo, không phải số tròn (CLAUDE.md #18): ô (cửa sổ,
# n_components) TỆ NHẤT trong backtest kiểm định có 7/10 restart dùng
# được, tức 30% "bẩn". Để xác suất MỌI restart trong một ô đều bẩn dưới
# 0.1% cần n_init >= ln(0.001)/ln(0.3) = 5.74 -> 6.
#
# Chỉ áp cho `config/settings.yaml` (qua `config/validate.py`), KHÔNG áp
# trong `__init__`: test dùng n_init=1..3 trên dữ liệu tổng hợp là hợp lệ
# và cần thiết cho tốc độ.
MIN_N_INIT = 6


class _ThuPhanKy(logging.Handler):
    """Thu cảnh báo "Model is not converging" của hmmlearn cho MỘT lần fit.

    Đọc `monitor_.converged` KHÔNG thay được việc này: hmmlearn trả `True`
    khi `iter == n_iter`, tức chạm trần lặp cũng được tính là hội tụ. Và
    cảnh báo phát ra ở BẤT KỲ vòng lặp nào log-likelihood giảm, độc lập
    với `converged`. Đo bằng cái sai cho `0/650 không hội tụ` trong khi
    thực tế 68 lần phân kỳ.
    """

    _RE_DELTA = re.compile(r"Delta is (-?[\d.]+(?:[eE][+-]?\d+)?)")

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.deltas: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — handler không được phép làm hỏng fit
            return
        if "not converging" not in msg:
            return
        m = self._RE_DELTA.search(msg)
        if m:
            self.deltas.append(float(m.group(1)))

    @property
    def phan_ky_max(self) -> float:
        return max((abs(d) for d in self.deltas), default=0.0)


@contextmanager
def _theo_doi_phan_ky() -> Iterator[_ThuPhanKy]:
    """Gắn bộ thu vào logger của hmmlearn, gỡ ra trong `finally`.

    `propagate` giữ nguyên: bộ thu này QUAN SÁT, không nuốt cảnh báo —
    người vận hành vẫn thấy chúng trong log như trước.
    """
    thu = _ThuPhanKy()
    lg = logging.getLogger("hmmlearn")
    muc_cu = lg.level
    if muc_cu > logging.WARNING or muc_cu == logging.NOTSET:
        lg.setLevel(logging.WARNING)
    lg.addHandler(thu)
    try:
        yield thu
    finally:
        lg.removeHandler(thu)
        lg.setLevel(muc_cu)


class EMDivergenceError(RuntimeError):
    """Model được BIC chọn tự nó phân kỳ trong lúc train.

    Đây là RAISE chứ không phải log có chủ ý: một model phân kỳ vẫn cho ra
    `predict_regime_filtered()` chạy được và trả về số — nó không hỏng ở
    chỗ nhìn thấy được, nó hỏng ở chỗ những con số đó vô nghĩa.
    """

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


def _validate_feature_names(feature_names: list[str]) -> None:
    """Bắt buộc có `log_return_1`. Raise ngay, thông điệp nêu rõ thiếu gì.

    `_build_regime_infos()` xếp hạng regime bằng `means_[:, return_idx]`,
    nên thiếu feature này thì KHÔNG có cách nào gán nhãn CRASH/BULL/... —
    đây là lỗi cấu hình, không phải trạng hợp biên xử lý mềm được.

    Đặt ở đây (module level) thay vì trong `HMMRegimeEngine.__init__`: lúc
    `__init__` chạy CHƯA có feature nào để kiểm — `feature_names` chỉ tồn
    tại sau `select_and_train()` hoặc `load()`. Gọi từ cả hai chỗ đó, mỗi
    chỗ ở dòng ĐẦU TIÊN, là điểm sớm nhất mà phép kiểm này thực hiện được.
    """
    if _RETURN_FEATURE_NAME in feature_names:
        return
    raise ValueError(
        f"Thiếu feature bắt buộc {_RETURN_FEATURE_NAME!r} — HMM không gán nhãn regime "
        f"được nếu không có nó (_build_regime_infos xếp hạng theo mean của chính "
        f"feature này).\n"
        f"Nhận được {len(feature_names)} feature: {sorted(feature_names)}\n"
        f"Kiểm tra FeatureConfig/feature_subset ở settings.yaml — "
        f"{_RETURN_FEATURE_NAME} không được phép loại khỏi bộ feature."
    )


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
    # |delta| lớn nhất trong các cảnh báo "not converging" của restart
    # THẮNG cuộc. 0.0 = không có cảnh báo nào. Mặc định để mọi caller cũ
    # (test dựng thẳng dataclass) không phải đổi.
    max_em_divergence: float = 0.0


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
        seed: int = 0,
    ) -> None:
        """`seed` — gốc của dãy `random_state` truyền cho `GaussianHMM`.

        CÓ MẶC ĐỊNH, bắt buộc phải thế: `forward/logger.py` (đóng băng,
        SHA256 ghim) dựng engine này không truyền `seed`. Một tham số bắt
        buộc ở đây sẽ làm file đóng băng vỡ ngay, và sửa nó = kết thúc thí
        nghiệm (CLAUDE.md bất biến #15).

        `seed=0` cho dãy `0..n_init-1` — ĐÚNG hành vi trước khi tham số này
        tồn tại (`random_state=seed for seed in range(n_init)`), nên mọi
        kết quả backtest/forward đã ghi vẫn tái lập được bit-for-bit.

        Tham số này KHÔNG phải để chỉnh. Nó tồn tại để tính tất định trở
        thành thứ ĐƯỢC KHAI BÁO thay vì tình cờ đúng — đổi nó nghĩa là mọi
        baseline đã đo (reports/pruned8_base, tests/snapshots/) hết hiệu
        lực cùng lúc.
        """
        self.n_candidates = list(n_candidates)
        self.n_init = n_init
        self.covariance_type = covariance_type
        self.min_train_bars = min_train_bars
        self.stability_bars = stability_bars
        self.flicker_window = flicker_window
        self.flicker_threshold = flicker_threshold
        self.seed = seed

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
        # Kiểm TRƯỚC khi train: `_build_regime_infos()` cần
        # `log_return_1` để xếp hạng regime, nhưng nó chỉ chạy SAU
        # `scan_bic()` — tức là sau toàn bộ chi phí train. Thiếu feature thì
        # bản cũ nổ bằng `ValueError: 'log_return_1' is not in list` từ
        # `.index()`, sau nhiều phút fit, với thông điệp không nói được
        # feature nào thiếu hay có những feature nào.
        _validate_feature_names(list(features.columns))

        if len(features) < self.min_train_bars:
            raise ValueError(
                f"Cần tối thiểu {self.min_train_bars} bar để train, chỉ có {len(features)}."
            )

        self.feature_names = list(features.columns)
        self.model, self.bic_results = self.scan_bic(features)

        # HỢP ĐỒNG TƯỜNG MINH, không phải phụ phẩm.
        #
        # Lớp bảo vệ duy nhất chống model phân kỳ là vòng random restart —
        # `scan_bic` giữ restart có log-likelihood cao nhất, và một fit đã
        # phân kỳ thì log-likelihood tệ nên luôn thua. Đo trên backtest kiểm
        # định: 0/13 cửa sổ chọn phải model phân kỳ, dù 10/13 cửa sổ CÓ
        # chứa restart phân kỳ.
        #
        # Nhưng đó là một quan sát về dữ liệu đã thấy, không phải một bảo
        # đảm. Giảm `n_init` làm nó mỏng đi và KHÔNG phép kiểm nào phản đối
        # — cho tới dòng này. RAISE chứ không log: một model phân kỳ vẫn
        # trả về số từ `predict_regime_filtered()`, nó không hỏng ở chỗ
        # nhìn thấy được.
        self._assert_chosen_model_converged()

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

            # `self.seed + restart` (không phải `restart` trần): dãy
            # random_state được KHAI BÁO từ config thay vì trùng hợp bằng
            # chỉ số vòng lặp. `seed=0` cho đúng dãy cũ 0..n_init-1.
            #
            # Đây là thứ làm ngưỡng regression 0.001 có nghĩa: hai lần chạy
            # cùng dữ liệu phải cho cùng model. Đo thật ở
            # tests/test_determinism.py, không suy luận.
            best_restart_divergence = 0.0
            for restart in range(self.n_init):
                candidate = GaussianHMM(
                    n_components=n_components,
                    covariance_type=self.covariance_type,
                    n_iter=_EM_MAX_ITER,
                    tol=_EM_TOL,
                    random_state=self.seed + restart,
                )
                # Bọc TỪNG lần fit riêng: cảnh báo phải quy được về đúng
                # restart phát ra nó, không phải về cả cụm. Cụm thì restart
                # thắng luôn "có vẻ phân kỳ" chỉ vì một restart khác đã phân
                # kỳ, và khẳng định ở `select_and_train` thành vô dụng.
                with _theo_doi_phan_ky() as thu:
                    candidate.fit(X)
                score = candidate.score(X)
                if score > best_restart_score:
                    best_restart_score = score
                    best_restart = candidate
                    best_restart_divergence = thu.phan_ky_max

            assert best_restart is not None
            bic = best_restart.bic(X)
            n_params = sum(best_restart._get_n_fit_scalars_per_param().values())  # noqa: SLF001
            result = BICCandidateResult(
                n_components=n_components,
                bic=bic,
                max_em_divergence=best_restart_divergence,
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

    def _assert_chosen_model_converged(self) -> None:
        """Model được BIC chọn không được tự nó phân kỳ trong lúc train."""
        assert self.model is not None
        chon = min(self.bic_results, key=lambda r: r.bic)
        if chon.max_em_divergence <= _EM_DIVERGENCE_DELTA:
            return
        raise EMDivergenceError(
            f"Model được BIC chọn (n_components={chon.n_components}, "
            f"BIC={chon.bic:.2f}) phân kỳ trong lúc train: log-likelihood "
            f"GIẢM {chon.max_em_divergence:.1f} ở ít nhất một vòng EM "
            f"(ngưỡng {_EM_DIVERGENCE_DELTA}). EM đảm bảo log-likelihood đơn "
            f"điệu tăng, nên đây là hỏng số học, không phải 'chưa hội tụ'. "
            f"Tăng n_init (đang {self.n_init}, sàn sản xuất {MIN_N_INIT}) "
            f"hoặc giảm n_components / đổi covariance_type. "
            f"Xem docs/DECISIONS.md 'Phân kỳ EM trong backtest kiểm định'."
        )

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
        """Variance của một feature cụ thể theo từng state.

        SỬA 2026-08-07 (bug thật, phát hiện bằng test viết cho
        `tests/test_hmm.py`, không phải đọc code): `self.model.covars_`
        (property công khai của hmmlearn) LUÔN trả về ma trận covariance
        ĐẦY ĐỦ dạng `(n_components, n_features, n_features)` bất kể
        `covariance_type` — hmmlearn tự "phồng" `diag`/`tied`/`spherical`
        về dạng full trước khi trả ra (xem `hmmlearn/utils.py::fill_covars`,
        và `hmmlearn/hmm.py::GaussianHMM.covars_` property). Xác nhận bằng
        fit thật cả 4 loại (hmmlearn 0.3.3): `covars_.shape` luôn
        `(n_components, n_features, n_features)`, kể cả khi giá trị nội bộ
        `_covars_` (private, không phải thứ hàm này đọc) có shape gọn hơn
        theo từng loại.

        Bản CŨ giả định `covars_` giữ nguyên shape gọn theo
        `covariance_type` (đúng với vài phiên bản hmmlearn cũ hơn, không
        đúng với bản đang dùng) — nhánh `full` (đọc `covars[s, i, i]`)
        tình cờ đúng vì đó chính xác là shape thật của `covars_`; ba nhánh
        còn lại (`diag`/`tied`/`spherical`) đọc SAI vị trí phần tử, âm
        thầm trả về variance sai (vd. `diag` từng đọc nguyên một HÀNG của
        ma trận full thay vì phần tử đường chéo). Không lộ ra vì
        `settings.yaml: hmm.covariance_type: full` là cấu hình production
        duy nhất từng chạy — sẽ lộ ngay khi thử `covariance_type` khác
        trong ablation (CLAUDE.md bất biến #13 khuyến khích thử nghiệm
        này).

        Vì `covars_` luôn đầy đủ, không còn cần nhánh theo
        `covariance_type` — luôn đọc đúng phần tử đường chéo."""
        assert self.model is not None
        covars = self.model.covars_
        return np.asarray([covars[s, feature_idx, feature_idx] for s in range(self.model.n_components)])

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

        KHÔNG chuẩn hoá `log_alpha` ở mỗi bước `t` — đã kiểm tra lại việc
        này (2026-08-07, xem docs/DECISIONS.md) sau khi thấy cảnh báo
        `RuntimeWarning: divide by zero/overflow encountered in matmul` lúc
        chạy forward test. Kết luận: KHÔNG PHẢI bug ở đây.

        Chuẩn hoá mỗi bước (chia alpha cho hằng số `c_t = 1/Σ alpha_t(i)`,
        kiểu Rabiner) chỉ bắt buộc trong KHÔNG GIAN XÁC SUẤT THƯỜNG, nơi
        alpha là xác suất đồng thời co lại theo cấp SỐ NHÂN theo t và
        underflow về đúng 0.0 chỉ sau ~100-200 bước. Trong LOG SPACE,
        `log_alpha` chỉ giảm gần như TUYẾN TÍNH theo t (cộng dồn log-mật độ
        mỗi bước) — với is_bars cỡ vài nghìn bar thực tế, `log_alpha` rơi
        vào khoảng -1e4 tới -1e5, còn cách rất xa giới hạn biểu diễn của
        float64 (~-1.7e308). `logsumexp` trong `_forward_step` đã tự ổn định
        theo từng bước (trừ max trước khi exp), nên không cần chuẩn hoá
        thêm — và bước exp() DUY NHẤT của toàn thuật toán nằm ở
        `_filtered_proba_incremental`/`predict_regime_filtered_history`
        (`np.exp(log_alpha[-1] - logsumexp(log_alpha[-1]))`), luôn nhận
        input ≤ 0 nên không bao giờ overflow.

        Xác nhận bằng thực nghiệm trên dữ liệu thật (2657 bar,
        `min_train_bars=730`): `log_alpha` cuối chuỗi nằm trong khoảng
        [-22815, -9.2], không NaN/Inf, `predict_regime_filtered` chạy sạch
        (0 warning) trong khi `select_and_train` (đường `.fit()` EM/k-means
        CỦA HMMLEARN, không phải forward algorithm tự viết ở đây) là nơi
        THẬT SỰ phát ra các warning matmul đó — đã cô lập bằng
        `warnings.simplefilter("error")` quanh từng lệnh gọi riêng để xác
        nhận nguồn. Không sửa gì ở forward algorithm.
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
        # Cùng lý do như trong `select_and_train`: một file model cũ được
        # train bằng bộ feature khác sẽ nạp trót lọt rồi mới nổ ở lần gọi
        # `_build_regime_infos()`/`predict_regime_filtered()` sau đó.
        _validate_feature_names(list(payload["feature_names"]))
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
