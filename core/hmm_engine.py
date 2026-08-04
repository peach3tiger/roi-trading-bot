"""core.hmm_engine — Gaussian HMM regime classifier.

HMM ở đây là bộ phân loại biến động (volatility classifier), không phải bộ
dự báo hướng giá. Nó xác định thị trường đang ở môi trường vol thấp, trung
bình hay cao; tầng strategy dùng phân loại đó để đặt tỷ trọng danh mục.

BẤT BIẾN QUAN TRỌNG NHẤT CỦA MODULE NÀY: không bao giờ dùng `model.predict()`
hay `model.decode()` của hmmlearn để suy luận regime tại thời điểm hiện tại.
Cả hai đều chạy thuật toán Viterbi trên toàn chuỗi và sửa lại các trạng thái
quá khứ bằng dữ liệu tương lai — đó là look-ahead bias, và nó sẽ làm
backtest đẹp một cách giả tạo trong khi live trading thất bại. Suy luận
online CHỈ được thực hiện qua `predict_regime_filtered`, cài đặt forward
algorithm, chỉ dùng dữ liệu tới thời điểm hiện tại (xem CLAUDE.md bất
biến #1 và tests/test_look_ahead.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


@dataclass(frozen=True)
class RegimeInfo:
    """Metadata mô tả một regime đã train, không đổi giữa các bar.

    Sắp theo `expected_volatility` (không phải return) để ánh xạ sang
    strategy — xem core/regime_strategies.py.
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


class HMMRegimeEngine:
    """Chọn model bằng BIC, train, và suy luận regime không look-ahead.

    Model tự chọn số regime trong `n_candidates` bằng BIC thay vì cố định,
    vì độ phức tạp thị trường thay đổi theo giai đoạn — ép cố định
    n_components là một dạng bias lựa chọn ẩn.
    """

    model: GaussianHMM | None

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
        ...

    def select_and_train(self, features: pd.DataFrame) -> list[RegimeInfo]:
        """Thử mọi n_components trong n_candidates, chọn BIC thấp nhất.

        Log toàn bộ BIC của mọi ứng viên và cái nào được chọn — bắt buộc,
        đây là bằng chứng chọn model có kỷ luật (xem CLAUDE.md bất biến #13).
        """
        raise NotImplementedError

    def predict_regime_filtered(self, features_up_to_now: pd.DataFrame) -> RegimeState:
        """Tính P(state_t | observations_1:t) bằng forward algorithm.

        CHỈ dùng dữ liệu quá khứ và hiện tại — không có `model.predict()`
        hay `model.decode()` ở đây, kể cả gián tiếp. Làm việc trong log
        space để tránh underflow trên chuỗi dài. Cache alpha của bar trước
        để suy luận tăng dần (incremental) trong vòng lặp live/backtest
        thay vì tính lại toàn chuỗi mỗi bar.
        """
        raise NotImplementedError

    def predict_regime_proba(self, features_up_to_now: pd.DataFrame) -> np.ndarray:
        """Phân phối xác suất đầy đủ trên mọi state tại bar hiện tại."""
        raise NotImplementedError

    def get_regime_stability(self) -> int:
        """Số bar liên tiếp regime hiện tại đã được xác nhận."""
        raise NotImplementedError

    def get_transition_matrix(self) -> np.ndarray:
        """`transmat_` của model đã train — để giải thích/debug, không dùng để suy luận online."""
        raise NotImplementedError

    def detect_regime_change(self) -> bool:
        """True nếu regime vừa xác nhận khác với regime xác nhận trước đó."""
        raise NotImplementedError

    def get_regime_flicker_rate(self) -> float:
        """Số lần đổi regime trong `flicker_window` bar gần nhất."""
        raise NotImplementedError

    def is_flickering(self) -> bool:
        """True nếu flicker rate vượt `flicker_threshold` — bật uncertainty mode."""
        raise NotImplementedError

    def save(self, path: str) -> None:
        """Lưu model bằng pickle kèm metadata: n_regimes, bic, training_date,
        labels, feature_list, data_hash."""
        raise NotImplementedError

    def load(self, path: str) -> None:
        raise NotImplementedError
