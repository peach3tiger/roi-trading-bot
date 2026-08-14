"""Property-based test (Hypothesis) cho các hàm THUẦN — Phase 12b §A.2.

Mỗi property chạy ≥ 1000 ví dụ. Profile Hypothesis (`deadline=None`,
`derandomize=True`) chốt ở `tests/conftest.py` — xem lý do ở đó.

## Vì sao property test, không phải thêm ví dụ

Một test ví dụ khẳng định "với đầu vào NÀY, ra kết quả KIA". Một property
khẳng định một QUAN HỆ đúng với mọi đầu vào hợp lệ. Với hệ thống này, các
bất biến quan trọng nhất đều là quan hệ chứ không phải giá trị:
`final <= min(mọi tầng)` đúng với mọi tổ hợp, không chỉ với ba con số ai
đó nghĩ ra lúc viết test.

## `test_layer_composition.py` đã được GỘP vào đây

File đó tự cuộn một property test bằng `random.Random(42)` + 10.000 vòng
lặp, phủ đúng property `compose_layer_allocations`. Giữ hai chỗ nghĩa là
hai bộ sinh dữ liệu khác nhau cho cùng một bất biến, và bộ nào yếu hơn sẽ
âm thầm quyết định mức bảo vệ thật.

`CLAUDE.md` #15 đã cập nhật: `test_layer_composition.py` ->
`test_properties.py` trong danh sách bảy file bắt buộc. Bất biến #2 KHÔNG
bị hạ cấp — nó vẫn có test riêng, chỉ đổi nhà và đổi bộ sinh dữ liệu
(Hypothesis biết thu nhỏ phản ví dụ; `random.Random` thì không).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

_EXAMPLES = 1000

# `Decimal` từ chuỗi, KHÔNG từ float: `Decimal(0.1)` mang theo sai số nhị
# phân của float và biến property thành phép đo sai số biểu diễn thay vì
# đo logic (CLAUDE.md bất biến #3).
_allocations = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("1"), places=6, allow_nan=False, allow_infinity=False
)
_prices = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_qty_deltas = st.decimals(
    min_value=Decimal("-1000"),
    max_value=Decimal("1000"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)


# ======================================================================
# 1. compose_layer_allocations — bất biến #2 (GỘP từ test_layer_composition)
# ======================================================================


@given(caps=st.lists(_allocations, min_size=1, max_size=5))
@settings(max_examples=_EXAMPLES)
def test_compose_luon_la_ham_toi_thieu(caps: list[Decimal]) -> None:
    """`final == min(mọi input)` — không max, không trung bình, không
    "hoà giải". CLAUDE.md bất biến #2."""
    from core.signal_generator import compose_layer_allocations

    result = compose_layer_allocations(*caps)

    assert result == min(caps)
    assert all(result <= cap for cap in caps)


@given(caps=st.lists(_allocations, min_size=1, max_size=4), extra=_allocations)
@settings(max_examples=_EXAMPLES)
def test_them_mot_tang_khong_bao_gio_lam_tang_ket_qua(caps: list[Decimal], extra: Decimal) -> None:
    """Thêm tầng chỉ có thể GIỮ NGUYÊN hoặc GIẢM. Đây là tính chất khiến
    hệ thống an toàn khi một tầng hỏng: một tầng lỗi trả 1.0 không kéo
    được kết quả lên."""
    from core.signal_generator import compose_layer_allocations

    assert compose_layer_allocations(*caps, extra) <= compose_layer_allocations(*caps)


def test_compose_rong_phai_raise() -> None:
    """Không có tầng nào thì KHÔNG có trần — trả về một giá trị mặc định ở
    đây sẽ là bịa ra một trần không ai đặt."""
    from core.signal_generator import compose_layer_allocations

    with pytest.raises(ValueError):
        compose_layer_allocations()


# ======================================================================
# 2. trend_gate.get_allocation_cap — luôn trong [0, 1]
# ======================================================================


def _min_bars_for_trend_gate() -> int:
    """`sma_period + slope_lookback`, ĐỌC TỪ CONFIG.

    Gõ cứng 230 ở đây sẽ trôi im lặng ngay lần ai đó đổi `sma_period` — và
    triệu chứng sẽ là property test đỏ với `ValueError: cần tối thiểu ...`,
    trông y hệt một bug thật. Bản đầu của test này gõ 210 và đỏ đúng kiểu
    đó; Hypothesis chỉ đang báo rằng ĐẦU VÀO không hợp lệ, không phải hàm sai.
    """
    from main import load_settings

    tg = load_settings()["trend_gate"]
    return int(tg["sma_period"]) + int(tg["slope_lookback"])


@st.composite
def _ohlcv(draw: Any) -> pd.DataFrame:
    """OHLCV hợp lệ: high >= max(open,close), low <= min(open,close), giá
    dương, đủ dài để trend gate tính được trần."""
    min_bars = _min_bars_for_trend_gate()
    n = draw(st.integers(min_value=min_bars, max_value=min_bars + 60))
    closes = draw(
        st.lists(
            st.floats(min_value=1.0, max_value=200_000.0, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    close = pd.Series(closes)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    # `.to_numpy(dtype=float)` chứ không `.values`: `.values` trả
    # `ndarray | ExtensionArray`, và nhân `ExtensionArray` với một `float`
    # không có kiểu hợp lệ (mypy: `Unsupported operand types for *`). Ở
    # đây là dữ liệu GIÁ dùng để sinh feature — `float` là đúng (CLAUDE.md
    # bất biến #3 cho phép float cho feature/thống kê), nên nói tường minh
    # ra thay vì để nó phụ thuộc vào dtype mà pandas tình cờ chọn.
    gia = close.to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "open": gia,
            "high": gia * 1.01,
            "low": gia * 0.99,
            "close": gia,
            "volume": 1.0,
            "trade_count": 1000.0,
        },
        index=idx,
    )


@given(bars=_ohlcv())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_trend_gate_cap_luon_trong_khoang_0_1(bars: pd.DataFrame) -> None:
    """Trần ngoài [0,1] nghĩa là tầng này có thể cho phép đòn bẩy (>1) hoặc
    vị thế âm (<0) — cả hai đều nằm ngoài thiết kế long-only không đòn bẩy."""
    from main import build_trend_gate, load_settings

    cap = build_trend_gate(load_settings(), enabled=True).get_allocation_cap(bars)

    assert Decimal("0") <= cap <= Decimal("1")


# ======================================================================
# 3. risk_manager — không bao giờ TĂNG allocation đầu vào
# ======================================================================


@given(target=_allocations)
@settings(max_examples=_EXAMPLES, deadline=None)
def test_risk_manager_khong_bao_gio_tang_allocation(target: Decimal, tmp_path_factory: Any) -> None:
    """Tầng risk chỉ được GIẢM. Một `validate_signal` trả về allocation LỚN
    HƠN đầu vào sẽ phá bất biến #2 từ bên trong — `compose_layer_allocations`
    vẫn lấy min, nhưng min của một giá trị đã bị thổi lên."""
    from core.risk_manager import RiskManager
    from tests.test_main_loop import _risk_manager_config
    from tests.test_nine_bug_fixes import _exit_signal_for, _portfolio_state

    lock = tmp_path_factory.mktemp("risk") / "halt.lock"
    rm = RiskManager(_risk_manager_config(), halt_lock_path=lock)

    decision = rm.validate_signal(_exit_signal_for(rm, target=target), _portfolio_state())

    if decision.approved and decision.modified_signal is not None:
        assert decision.modified_signal.target_allocation_pct <= target
        assert decision.modified_signal.target_allocation_pct <= rm._effective_max_allocation


# ======================================================================
# 4. cost_model.rebalance_cost — không âm, đơn điệu theo |delta|
# ======================================================================


@given(delta=_qty_deltas, price=_prices)
@settings(max_examples=_EXAMPLES)
def test_chi_phi_khong_bao_gio_am(delta: Decimal, price: Decimal) -> None:
    """Chi phí âm biến phí thành lợi nhuận — sai lệch CÓ LỢI cho kết quả
    nên rất khó nhận ra khi đọc equity curve."""
    from main import build_cost_model, load_settings

    assert build_cost_model(load_settings()).rebalance_cost(delta, price) >= 0


@given(a=_qty_deltas, b=_qty_deltas, price=_prices)
@settings(max_examples=_EXAMPLES)
def test_chi_phi_don_dieu_theo_do_lon_giao_dich(a: Decimal, b: Decimal, price: Decimal) -> None:
    """|a| <= |b| -> cost(a) <= cost(b). Giao dịch to hơn không bao giờ rẻ
    hơn — nếu vi phạm, backtest sẽ thấy chia nhỏ lệnh là "đắt hơn" và mọi
    kết luận về chi phí đảo chiều."""
    from main import build_cost_model, load_settings

    assume(abs(a) <= abs(b))
    model = build_cost_model(load_settings())

    assert model.rebalance_cost(a, price) <= model.rebalance_cost(b, price)


# ======================================================================
# 5. InstrumentRules.round_qty — luôn XUỐNG, luôn là bội của precision
# ======================================================================


_PRECISIONS = st.sampled_from(
    [Decimal("0.000001"), Decimal("0.001"), Decimal("0.05"), Decimal("0.5"), Decimal("1")]
)


@given(qty=st.decimals(min_value=Decimal("0"), max_value=Decimal("10000"), places=8), prec=_PRECISIONS)
@settings(max_examples=_EXAMPLES)
def test_round_qty_khong_bao_gio_lam_tang(qty: Decimal, prec: Decimal) -> None:
    """ROUND_DOWN, CLAUDE.md bất biến #3. Làm tròn LÊN có thể tạo lệnh vượt
    số dư khả dụng — sàn từ chối, hoặc tệ hơn là khớp một phần."""

    rules = _rules(prec)

    assert rules.round_qty(qty) <= qty


@given(qty=st.decimals(min_value=Decimal("0"), max_value=Decimal("10000"), places=8), prec=_PRECISIONS)
@settings(max_examples=_EXAMPLES)
def test_round_qty_luon_la_boi_cua_precision(qty: Decimal, prec: Decimal) -> None:
    """`base_precision` 0.5 hay 0.05 là ca mà `quantize` âm thầm sai — nó
    chỉ làm tròn theo luỹ thừa 10. Property này bắt đúng ca đó."""

    rounded = _rules(prec).round_qty(qty)

    assert (rounded / prec) % 1 == 0


def _rules(precision: Decimal) -> Any:
    from broker.instrument_rules import InstrumentRules

    return InstrumentRules(
        symbol="BTCUSDT",
        base_precision=precision,
        quote_precision=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        min_order_qty=precision,
        min_order_amt=Decimal("5"),
        max_order_qty=Decimal("1000000"),
    )


# ======================================================================
# 6. predict_regime_filtered — state_probabilities là PHÂN PHỐI hợp lệ
# ======================================================================


@pytest.fixture(scope="module")
def trained_engine() -> Any:
    """Engine nhỏ, train MỘT LẦN cho cả module — 1000 ví dụ × một lần train
    sẽ mất hàng giờ."""
    from core.hmm_engine import HMMRegimeEngine

    rng = np.random.default_rng(11)
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    features = pd.DataFrame(
        {
            "log_return_1": rng.normal(0, 1, 400),
            "realized_vol_20": rng.normal(0, 1, 400),
        },
        index=idx,
    )
    engine = HMMRegimeEngine(
        n_candidates=[3],
        n_init=2,
        covariance_type="diag",
        min_train_bars=100,
        stability_bars=1,
        flicker_window=20,
        flicker_threshold=4,
    )
    engine.select_and_train(features)
    return engine, features


# Sinh CHÍNH GIÁ TRỊ feature, không chỉ độ dài.
#
# Bản đầu chỉ draw `n_bars` trong 5..400 — đúng 396 giá trị khả dĩ, nên
# Hypothesis vét cạn không gian và dừng ở 396 ví dụ, KHÔNG đạt mốc 1000 mà
# §A.2 yêu cầu. Một property "chạy 1000 ví dụ" mà thực tế chỉ có 396 đầu
# vào khác nhau là một con số nghiệm thu không đúng sự thật.
#
# Giá trị feature đã z-score nên dải [-6, 6] phủ rộng hơn thực tế nhiều.
_feature_values = st.lists(
    st.floats(min_value=-6.0, max_value=6.0, allow_nan=False, allow_infinity=False),
    min_size=5,
    max_size=400,
)


@given(log_returns=_feature_values, vols=_feature_values)
@settings(max_examples=_EXAMPLES, deadline=None)
def test_state_probabilities_la_phan_phoi_hop_le(
    trained_engine: Any, log_returns: list[float], vols: list[float]
) -> None:
    """Tổng = 1.0 (sai số 1e-9), mọi phần tử >= 0.

    Đây là tính chất khiến `probability` đọc được như một mức tin cậy. Nếu
    tổng trôi khỏi 1, mọi ngưỡng `min_confidence` trong hệ thống đang so
    với một thang đo không xác định — và nó sẽ trôi ÂM THẦM, vì không ai
    nhìn vector xác suất thô.
    """
    engine, _ = trained_engine

    n = min(len(log_returns), len(vols))
    frame = pd.DataFrame(
        {"log_return_1": log_returns[:n], "realized_vol_20": vols[:n]},
        index=pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
    )

    state = engine.predict_regime_filtered(frame)
    probs = np.asarray(state.state_probabilities, dtype=float)

    assert probs.sum() == pytest.approx(1.0, abs=1e-9)
    assert (probs >= 0).all()
    assert np.isfinite(probs).all(), "NaN/inf trong vector xác suất"
