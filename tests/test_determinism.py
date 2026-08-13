"""Backtest phải TẤT ĐỊNH — hai lần chạy giống bit-for-bit.

Đây là ĐIỀU KIỆN TIÊN QUYẾT của regression harness (§0,
`prompts/phase-12b-harness-engineering.md`). Ngưỡng "Sharpe chênh ≤ 0.001"
chỉ có nghĩa nếu cùng đầu vào cho cùng đầu ra. Nếu không, harness sẽ báo
động giả liên tục rồi bị vô hiệu hoá vì phiền — và lúc đó ta mất luôn
phép kiểm, không phải chỉ mất độ tin cậy của nó.

**Đã ĐO, không suy luận.** Prompt §0 giả định `random_state` chưa được cố
định và backtest chưa tất định. Giả định đó SAI với code hiện tại:
`HMMRegimeEngine.select_and_train()` vốn đã lặp `random_state` theo một
dãy cố định. Đo thật trên một window walk-forward đầy đủ (365 IS + 182
OOS, `n_candidates=[3,4,5,6,7]` × `n_init=10`, `covariance_type=full`):
hai lần chạy cho `equity_curve` SHA256 giống hệt.

Việc duy nhất §0 còn thiếu là làm cho tính tất định đó được KHAI BÁO thay
vì tình cờ đúng — `hmm.seed` trong `settings.yaml`, mặc định 0, cho đúng
dãy cũ. Xem docstring `HMMRegimeEngine.__init__`.

**Chi phí:** ~12 giây mỗi lần chạy, hai lần = ~25s. Đó là giá của việc
kiểm tính tất định trên CẤU HÌNH THẬT. Chạy với cấu hình rút gọn (ít
candidate/init hơn) sẽ nhanh hơn nhiều nhưng không kiểm được thứ cần
kiểm: rủi ro nằm ở chính cấu hình đang dùng để sinh baseline.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pytest

import main as main_mod

# Cửa sổ NHỎ NHẤT còn hợp lệ: đúng MỘT window walk-forward.
#
# `_plan_windows()` lập kế hoạch trên `features` (đã trừ ~741 bar warmup
# của Tầng 1), và cần `is_bars=365` bar trước khi OOS bắt đầu. Nên
# `data_start` phải sớm hơn `start` khoảng 741 + 365 bar — đo thật, không
# đoán: 2018-01-01 cho ~2375 bar thô -> ~1634 bar feature.
_DATA_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_END = datetime(2024, 7, 2, tzinfo=timezone.utc)

_SYMBOL = "BTCUSDT"
_CCXT_SYMBOL = "BTC/USDT"


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    from data.history_loader import HistoryLoader

    return HistoryLoader().load(_CCXT_SYMBOL, "1D", _DATA_START, _END)


def _run_backtest(settings: dict[str, Any], ohlcv: pd.DataFrame) -> Any:
    from backtest.backtester import WalkForwardBacktester

    wf = main_mod.build_walk_forward_config(settings)
    backtester = WalkForwardBacktester(
        hmm_engine=main_mod.build_hmm_engine(settings, min_train_bars=wf.is_bars),
        strategy_orchestrator=main_mod.build_orchestrator(settings),
        trend_gate=main_mod.build_trend_gate(settings, enabled=True),
        cost_model=main_mod.build_cost_model(settings),
        config=wf,
        feature_config=main_mod.build_feature_config(settings),
    )
    return backtester.run(_SYMBOL, ohlcv, _START, _END)


def _digest(frame: pd.DataFrame) -> str:
    """SHA256 của CSV — so bit-for-bit, không phải `assert_frame_equal` với
    dung sai. §0 nói "giống bit-for-bit"; một phép so có dung sai sẽ bỏ lọt
    đúng loại lệch nhỏ mà ngưỡng 0.001 cần loại trừ."""
    return hashlib.sha256(frame.to_csv().encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def two_runs(ohlcv: pd.DataFrame) -> tuple[Any, Any]:
    """Chạy backtest HAI LẦN, dùng lại cho mọi assert trong file — ~25s là
    chi phí một lần, không phải mỗi test."""
    settings = main_mod.load_settings()
    return _run_backtest(settings, ohlcv), _run_backtest(settings, ohlcv)


def test_equity_curve_giong_bit_for_bit(two_runs: tuple[Any, Any]) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM — nghiệm thu §0."""
    first, second = two_runs

    assert _digest(first.equity_curve) == _digest(second.equity_curve)
    assert len(first.equity_curve) > 0, "backtest rỗng thì phép so vô nghĩa"


@pytest.mark.parametrize("attr", ["trade_log", "regime_history", "model_selection"])
def test_moi_bang_ket_qua_giong_bit_for_bit(two_runs: tuple[Any, Any], attr: str) -> None:
    """Không chỉ equity: chuỗi regime và lệnh cũng phải trùng khớp.

    `equity_curve` giống nhau mà `regime_history` khác nghĩa là hai đường
    khác nhau tình cờ cho cùng số dư — chưa phải tất định.
    """
    first, second = two_runs

    assert _digest(getattr(first, attr)) == _digest(getattr(second, attr))


def test_cost_report_giong_nhau(two_runs: tuple[Any, Any]) -> None:
    first, second = two_runs

    assert first.cost_report.as_dict() == second.cost_report.as_dict()


def test_seed_mac_dinh_cho_dung_day_cu(ohlcv: pd.DataFrame) -> None:
    """`seed=0` phải cho ĐÚNG dãy `random_state` trước khi tham số tồn tại
    (`range(n_init)`), nếu không mọi baseline đã đo hết hiệu lực.

    Kiểm bằng cách so engine dựng từ settings (seed đọc từ config) với
    engine dựng tay không truyền seed — hai bên phải cho cùng model.
    """
    from core.hmm_engine import HMMRegimeEngine

    settings = main_mod.load_settings()
    assert settings["hmm"]["seed"] == 0, "settings.yaml phải khai báo seed=0"

    from_config = main_mod.build_hmm_engine(settings, min_train_bars=365)
    hmm = settings["hmm"]
    without_seed = HMMRegimeEngine(
        n_candidates=list(hmm["n_candidates"]),
        n_init=hmm["n_init"],
        covariance_type=hmm["covariance_type"],
        min_train_bars=365,
        stability_bars=hmm["stability_bars"],
        flicker_window=hmm["flicker_window"],
        flicker_threshold=hmm["flicker_threshold"],
    )

    assert from_config.seed == without_seed.seed == 0


def test_seed_khac_nhau_cho_ket_qua_khac_nhau(ohlcv: pd.DataFrame) -> None:
    """Đột biến ngược: nếu `seed` KHÔNG ảnh hưởng gì thì cả cơ chế là giả.

    Dùng cấu hình rút gọn (1 candidate, 2 init) — ở đây ta chỉ cần chứng
    minh seed CÓ tác dụng, không cần cấu hình thật.
    """
    import numpy as np

    from core.hmm_engine import HMMRegimeEngine
    from data.feature_engineering import compute_all_features

    feature_config = main_mod.build_feature_config(main_mod.load_settings())
    features = compute_all_features(ohlcv.iloc[:1200], feature_config)

    def _train(seed: int) -> Any:
        engine = HMMRegimeEngine(
            n_candidates=[3],
            n_init=2,
            covariance_type="diag",
            min_train_bars=100,
            stability_bars=1,
            flicker_window=20,
            flicker_threshold=4,
            seed=seed,
        )
        engine.select_and_train(features.iloc[:300])
        return engine.model.means_

    assert not np.allclose(_train(0), _train(1000)), (
        "đổi seed mà model không đổi — tham số seed không có tác dụng thật, "
        "cả cơ chế tất định khai báo là giả"
    )
