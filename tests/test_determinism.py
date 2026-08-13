"""Backtest phải TẤT ĐỊNH — hai lần chạy giống bit-for-bit.

Đây là ĐIỀU KIỆN TIÊN QUYẾT của regression harness (§0,
`prompts/phase-12b-harness-engineering.md`). Ngưỡng "Sharpe chênh ≤ 0.001"
chỉ có nghĩa nếu cùng đầu vào cho cùng đầu ra. Nếu không, harness sẽ báo
động giả liên tục rồi bị vô hiệu hoá vì phiền — và lúc đó ta mất luôn
phép kiểm, không phải chỉ mất độ tin cậy của nó.

**Đã ĐO, không suy luận.** Prompt §0 giả định `random_state` chưa được cố
định và backtest chưa tất định. Giả định đó SAI với code hiện tại:
`select_and_train()` vốn đã lặp `random_state` theo một dãy cố định. Hai
lần chạy cho `equity_curve` SHA256 giống hệt ngay từ đầu. Việc §0 còn
thiếu là làm cho tính tất định đó được KHAI BÁO (`hmm.seed`) thay vì tình
cờ đúng.

## Hai kịch bản, MỘT hàm dựng

`_build_and_run()` là đường duy nhất dựng backtest ở file này. Hai kịch
bản chỉ khác nhau ở THAM SỐ — không có logic nào bị nhân đôi, nên bản
nhanh không thể trôi khỏi bản đầy đủ.

| Kịch bản | Cấu hình | Thời gian (đo thật) | Chạy khi |
|---|---|---|---|
| `nhanh` | HMM rút gọn (2 cand × 3 init, diag), 2 window | ~3s | mặc định |
| `đầy đủ` | HMM thật (5 × 10, full cov), 365+182 | ~31s | `pytest -m slow` |

Cả hai con số là cho HAI lần chạy backtest (tất định cần so hai lần).

**Vì sao bản nhanh phải rút gọn CẢ cấu hình HMM, không chỉ cửa sổ** — đo
thật, không đoán:

- Chi phí bị chi phối bởi `n_candidates × n_init` lần `.fit()`, KHÔNG
  phải kích thước cửa sổ. Thu nhỏ cửa sổ mà giữ cấu hình thật còn CHẬM
  HƠN (5.7s so với 12s cho một window đầy đủ) vì cửa sổ nhỏ tạo NHIỀU
  window hơn trên cùng khoảng đánh giá, tức nhiều lần retrain hơn.
- `covariance_type: full` với `n_components` tới 7 cần 881 tham số tự do;
  trên 120 bar (840 điểm dữ liệu) hmmlearn báo degenerate. Bản nhanh sẽ
  train model rác — vẫn tất định, nhưng bơm cảnh báo degenerate vào mọi
  lần chạy suite là tiếng ồn không đổi lấy gì.

Bản nhanh vì thế nhắm đúng việc §0 giao cho nó: bắt lỗi **thứ tự dict,
numpy chưa seed, song song hoá** — những nguồn ngẫu nhiên ở TẦNG PIPELINE,
không phụ thuộc cấu hình HMM. Rủi ro riêng của cấu hình thật (5 candidate
× 10 init, full covariance) do bản `đầy đủ` gánh.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pytest

import main as main_mod

_SYMBOL = "BTCUSDT"
_CCXT_SYMBOL = "BTC/USDT"

# `_plan_windows()` lập kế hoạch trên `features` (đã trừ ~741 bar warmup
# Tầng 1) và cần `is_bars` bar trước khi OOS bắt đầu — nên `data_start`
# phải sớm hơn `start` khoảng 741 + is_bars bar. Đo thật: 2018-01-01 cho
# ~2375 bar thô -> ~1634 bar feature.
_DATA_START = datetime(2018, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Scenario:
    """Tham số phân biệt hai kịch bản. Mọi thứ khác dùng chung."""

    label: str
    start: datetime
    end: datetime
    is_bars: int
    oos_bars: int
    hmm_overrides: dict[str, Any]


_FAST = _Scenario(
    label="nhanh",
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 1, tzinfo=timezone.utc),
    is_bars=365,
    oos_bars=30,
    # Rút gọn để hai lần chạy vừa ~3s. `diag` thay `full`: trên cửa sổ này
    # `full` vừa chậm vừa degenerate — xem docstring module.
    hmm_overrides={"n_candidates": [3, 4], "n_init": 3, "covariance_type": "diag"},
)

_FULL = _Scenario(
    label="đầy đủ",
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 7, 2, tzinfo=timezone.utc),
    is_bars=365,
    oos_bars=182,
    hmm_overrides={},  # cấu hình THẬT từ settings.yaml
)

_SCENARIOS = [
    pytest.param(_FAST, id="nhanh"),
    pytest.param(_FULL, id="day-du", marks=pytest.mark.slow),
]

# Cache theo kịch bản: mỗi kịch bản chạy backtest ĐÚNG HAI LẦN cho cả
# file, không phải hai lần cho mỗi test.
_OHLCV: dict[str, pd.DataFrame] = {}
_RUNS: dict[str, tuple[Any, Any]] = {}


def _ohlcv(scenario: _Scenario) -> pd.DataFrame:
    if scenario.label not in _OHLCV:
        from data.history_loader import HistoryLoader

        _OHLCV[scenario.label] = HistoryLoader().load(_CCXT_SYMBOL, "1D", _DATA_START, scenario.end)
    return _OHLCV[scenario.label]


def _build_and_run(scenario: _Scenario) -> Any:
    """ĐƯỜNG DUY NHẤT dựng backtest ở file này.

    Hai kịch bản khác nhau ở tham số truyền vào đây, không ở logic. Một
    bản sao thứ hai của hàm này sẽ trôi khỏi bản gốc trong vài tháng, và
    lúc đó "bản nhanh xanh" không còn nói được gì về bản đầy đủ.
    """
    from backtest.backtester import WalkForwardBacktester

    settings = main_mod.load_settings()
    if scenario.hmm_overrides:
        settings = {**settings, "hmm": {**settings["hmm"], **scenario.hmm_overrides}}

    wf = replace(
        main_mod.build_walk_forward_config(settings),
        is_bars=scenario.is_bars,
        oos_bars=scenario.oos_bars,
        step_bars=scenario.oos_bars,
    )
    backtester = WalkForwardBacktester(
        hmm_engine=main_mod.build_hmm_engine(settings, min_train_bars=wf.is_bars),
        strategy_orchestrator=main_mod.build_orchestrator(settings),
        trend_gate=main_mod.build_trend_gate(settings, enabled=True),
        cost_model=main_mod.build_cost_model(settings),
        config=wf,
        feature_config=main_mod.build_feature_config(settings),
    )
    return backtester.run(_SYMBOL, _ohlcv(scenario), scenario.start, scenario.end)


def _two_runs(scenario: _Scenario) -> tuple[Any, Any]:
    if scenario.label not in _RUNS:
        _RUNS[scenario.label] = (_build_and_run(scenario), _build_and_run(scenario))
    return _RUNS[scenario.label]


def _digest(frame: pd.DataFrame) -> str:
    """SHA256 của CSV — so bit-for-bit, KHÔNG phải `assert_frame_equal` có
    dung sai. §0 nói "bit-for-bit"; một phép so có dung sai sẽ bỏ lọt đúng
    loại lệch nhỏ mà ngưỡng 0.001 cần loại trừ."""
    return hashlib.sha256(frame.to_csv().encode("utf-8")).hexdigest()


# ======================================================================
# Tất định — cùng phép kiểm, hai kịch bản
# ======================================================================


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_equity_curve_giong_bit_for_bit(scenario: _Scenario) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM — nghiệm thu §0."""
    first, second = _two_runs(scenario)

    assert _digest(first.equity_curve) == _digest(second.equity_curve)
    assert len(first.equity_curve) > 0, "backtest rỗng thì phép so vô nghĩa"


@pytest.mark.parametrize("scenario", _SCENARIOS)
@pytest.mark.parametrize("attr", ["trade_log", "regime_history", "model_selection"])
def test_moi_bang_ket_qua_giong_bit_for_bit(scenario: _Scenario, attr: str) -> None:
    """Không chỉ equity: chuỗi regime và lệnh cũng phải trùng khớp.

    `equity_curve` giống nhau mà `regime_history` khác nghĩa là hai đường
    khác nhau tình cờ cho cùng số dư — chưa phải tất định.
    """
    first, second = _two_runs(scenario)

    assert _digest(getattr(first, attr)) == _digest(getattr(second, attr))


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_cost_report_giong_nhau(scenario: _Scenario) -> None:
    first, second = _two_runs(scenario)

    assert first.cost_report.as_dict() == second.cost_report.as_dict()


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_co_it_nhat_hai_window(scenario: _Scenario) -> None:
    """Tiền đề của cả file: phải có ÍT NHẤT một lần retrain trong phạm vi
    đo, nếu không phép kiểm không chạm tới `random_state` lần nào và mọi
    assert phía trên xanh một cách rỗng nghĩa."""
    first, _ = _two_runs(scenario)

    assert len(first.model_selection) >= 1, "không window nào -> không retrain nào -> không kiểm gì"


# ======================================================================
# `seed` phải là thứ được KHAI BÁO, và phải có tác dụng thật
# ======================================================================


def test_seed_mac_dinh_cho_dung_day_cu() -> None:
    """`seed=0` phải cho ĐÚNG dãy `random_state` trước khi tham số tồn tại
    (`range(n_init)`), nếu không mọi baseline đã đo hết hiệu lực."""
    from core.hmm_engine import HMMRegimeEngine

    settings = main_mod.load_settings()
    assert settings["hmm"]["seed"] == 0, "settings.yaml phải khai báo seed=0"

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

    assert main_mod.build_hmm_engine(settings, min_train_bars=365).seed == without_seed.seed == 0


def test_seed_khac_nhau_cho_ket_qua_khac_nhau() -> None:
    """Đột biến ngược: nếu `seed` KHÔNG ảnh hưởng gì thì cả cơ chế là giả."""
    import numpy as np

    from core.hmm_engine import HMMRegimeEngine
    from data.feature_engineering import compute_all_features

    settings = main_mod.load_settings()
    features = compute_all_features(_ohlcv(_FAST).iloc[:1200], main_mod.build_feature_config(settings))

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
        assert engine.model is not None
        return engine.model.means_

    assert not np.allclose(_train(0), _train(1000)), (
        "đổi seed mà model không đổi — tham số seed không có tác dụng thật, "
        "cả cơ chế tất định khai báo là giả"
    )
