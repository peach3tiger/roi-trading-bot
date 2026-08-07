"""tests.test_wiring_equivalence — ba bản dựng song song của cùng một
composition logic (`hmm_allocation` ∩ `trend_gate_cap` = `final_allocation`)
KHÔNG được trôi lệch nhau, dù KHÔNG được hợp nhất thành một hàm dùng chung:

  1. `_run_golden_pipeline()` (tests/test_forward_golden.py) — khoá WIRING
     cho golden test.
  2. `forward/logger.py:428-584` — thí nghiệm forward ĐÃ ĐÓNG BĂNG, KHÔNG
     BAO GIỜ được sửa (xem docstring module đó, `docs/DECISIONS.md`
     "Forward test — tiền đăng ký"). File test này KHÔNG import/gọi bất
     kỳ hàm nào của `forward/logger.py` ngoài hằng số `FEATURE_SUBSET`
     (đã đóng băng, chỉ đọc) — cùng ngoại lệ đã có ở
     `tests/test_forward_golden.py`.
  3. `core/signal_generator.py::SignalGenerator` — đường `main.py` DÙNG
     THẬT (Phase 10).

Ba bản này KHÔNG được hợp nhất (đường (2) đóng băng, không sửa được) —
test này chỉ đảm bảo chúng KHÔNG TRÔI LỆCH nhau, không chứng minh chúng
"nên" hợp nhất. Đây là test TƯƠNG ĐƯƠNG (equivalence), không phải
refactor — không sửa một dòng nào trong `forward/logger.py` hay
`core/signal_generator.py`.

**Xác nhận đường gọi trùng nhau giữa (1) và (2)** đã làm ở
`tests/test_forward_golden.py` (2026-08-07, xem docstring file đó) —
CẢ HAI cùng gọi thẳng `HMMRegimeEngine`/`StrategyOrchestrator`/
`StructuralTrendGate`/`compose_layer_allocations`, bỏ qua class
`SignalGenerator` hoàn toàn. Vì công thức compose ở (1) và (2) ĐÃ xác nhận
giống hệt nhau (cùng lời gọi `compose_layer_allocations(hmm_allocation,
trend_gate_cap)`), file này đại diện (1)+(2) bằng MỘT lần tính duy nhất
(biến hậu tố `_golden` bên dưới) — không cần tính hai lần cho hai công
thức đã biết là giống hệt nhau. Việc CÒN LẠI, và là trọng tâm thật của
file này, là so (1)+(2) với (3) — `SignalGenerator`, đường DUY NHẤT trong
ba đường có nguy cơ trôi lệch (nó là code còn sống, được sửa gần đây ở
Phase 10 — xem `docs/DECISIONS.md` mục Phase 10).

**Vì sao KHÔNG dùng chung một `HMMRegimeEngine` cho cả ba đường:**
`predict_regime_filtered()` có tác dụng phụ (bộ lọc ổn định
`_current_confirmed_state`/`_pending_state`/`_pending_bars_count`, cache
alpha) TÍCH LUỸ qua các lần gọi — gọi nó HAI LẦN cho "cùng một bar" (một
lần trực tiếp, một lần gián tiếp qua `SignalGenerator.generate()`) sẽ
CỘNG DỒN bộ đếm ổn định hai lần cho một bar, làm hỏng phép so sánh thay vì
xác nhận nó. Giải pháp: HAI `HMMRegimeEngine` ĐỘC LẬP, train GIỐNG HỆT
nhau (cùng seed, cùng config — `select_and_train` xác định luận hoàn
toàn, không có nguồn ngẫu nhiên nào ngoài `random_state=seed` cố định
trong `scan_bic`), rồi cho ăn CÙNG một chuỗi bar theo CÙNG thứ tự — xác
nhận bằng `assert` tường minh mỗi bar (`regime_id`/`regime_label`/
`is_flickering` phải khớp) thay vì GIẢ ĐỊNH suông rằng train giống hệt
thì suy luận cũng giống hệt.

`StrategyOrchestrator`/`StructuralTrendGate` KHÔNG có tác dụng phụ — dùng
CHUNG một instance an toàn cho cả ba đường, gọi lại nhiều lần cho cùng
input luôn cho cùng kết quả. Tiền đề này KHÔNG chỉ dựa vào đọc docstring
của hai lớp đó — khoá bằng một `assert` runtime NGAY ĐẦU hàm test (gọi
`orchestrator.generate_signal()` hai lần với input giống hệt, khẳng định
output giống hệt): rẻ hơn nhiều so với đọc lại code mỗi lần nghi ngờ, và
tự động phát hiện nếu ai đó sau này thêm state ẩn vào
`StrategyOrchestrator`. Cùng assertion, độc lập, cũng có ở
`tests/test_strategies.py::test_generate_signal_is_idempotent_no_hidden_state`
— lặp lại NGAY TẠI ĐÂY vì toàn bộ so sánh trong file này dựa trên đúng
giả định đó, không nên chỉ tin một test ở file khác.

`bars_window` dùng `ohlcv.loc[:ts]` (KHÔNG giới hạn) cho cả ba đường —
khớp đúng quy ước của `_run_golden_pipeline()`. Đã xác nhận bằng thực
nghiệm (không suy luận): với dải bar mục tiêu của test này,
`get_allocation_cap(ohlcv.loc[:ts])` và `get_allocation_cap(ohlcv.loc[:ts].tail(300))`
(quy ước `forward/logger.py`/`main.py` dùng) cho CÙNG kết quả ở mọi bar —
0/60 lệch — nên chọn quy ước nào không ảnh hưởng tới việc test này đo
đúng thứ cần đo (công thức compose, không phải quy ước cắt cửa sổ, vốn đã
ghi riêng ở docstring `tests/test_forward_golden.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from core.hmm_engine import HMMRegimeEngine, RegimeInfo, RegimeState
from core.regime_strategies import StrategyOrchestrator
from core.risk_manager import PortfolioState, RiskManager
from core.signal_generator import SignalGenerator, compose_layer_allocations
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from data.feature_engineering import FeatureConfig, compute_all_features
from forward.logger import FEATURE_SUBSET  # hằng số đã đóng băng, CHỈ ĐỌC — không gọi hàm nào của module

_SYMBOL = "BTCUSDT"

# Không cần khớp tests/test_forward_golden.py — file này tự so ba đường
# với NHAU trên một lần chạy, không so với baseline đã commit — nhưng giữ
# cùng giá trị cho quen thuộc/dễ đối chiếu khi đọc cả hai file.
_SEED = 12345
_N_BARS = 400
_TRAIN_BARS = 150
_N_PREDICT_BARS = 60
_ZSCORE_LOOKBACK = 60
_HMM_N_CANDIDATES = [3, 4, 5]
_HMM_N_INIT = 3


def _make_synthetic_ohlcv(n_bars: int, seed: int) -> pd.DataFrame:
    """Cùng công thức với `tests/test_forward_golden.py`/`tests/test_backtester.py`
    — không import chéo giữa file test để mỗi file tự chứa (quy ước đã có
    từ trước), nhưng cố ý giữ công thức giống nhau."""
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


def _make_engine() -> HMMRegimeEngine:
    return HMMRegimeEngine(
        n_candidates=_HMM_N_CANDIDATES,
        n_init=_HMM_N_INIT,
        covariance_type="full",
        min_train_bars=_TRAIN_BARS,
        stability_bars=3,
        flicker_window=20,
        flicker_threshold=4,
    )


def _passthrough_risk_manager(halt_lock_path: Path) -> RiskManager:
    """Cấu hình rộng rãi hết mức — risk_manager KHÔNG được phép là biến số
    trong phép so sánh này (nó không tồn tại ở đường (1)/(2)). Không cap,
    không circuit breaker, không chặn lệnh trùng, không halt lock — mọi
    signal đi qua nguyên vẹn, để `final_allocation` của đường (3) chỉ còn
    phản ánh đúng `compose_layer_allocations` bên trong
    `SignalGenerator._apply_layer_caps()`, giống hệt (1)/(2)."""
    config: dict[str, object] = {
        "max_position_pct": 100,
        "max_risk_per_trade_pct": 1.0,
        "min_order_value_usdt": 5,
        "min_cash_buffer_pct": 0.0,
        "max_trades_per_day": 10_000,
        "max_leverage": 1.0,
        "spread_max_pct": 0.10,
        "usdt_depeg_threshold_pct": 0.5,
        "duplicate_order_window_seconds": 0,
        "circuit_breaker": {
            "daily_dd_reduce_pct": 99.0,
            "daily_dd_halt_pct": 99.5,
            "weekly_dd_reduce_pct": 99.0,
            "weekly_dd_halt_pct": 99.5,
            "peak_dd_halt_pct": 99.5,
        },
    }
    return RiskManager(config, halt_lock_path=halt_lock_path)


def test_three_wiring_paths_produce_identical_allocations(tmp_path: Path) -> None:
    ohlcv = _make_synthetic_ohlcv(_N_BARS, _SEED)
    feature_config = FeatureConfig(
        zscore_lookback=_ZSCORE_LOOKBACK,
        use_trade_count_not_volume=True,
        tier2_derivatives=False,
        tier3_temporal=False,
        feature_subset=FEATURE_SUBSET,
    )
    features = compute_all_features(ohlcv, feature_config)

    # Hai engine ĐỘC LẬP, train giống hệt — xem docstring module vì sao
    # không dùng chung một object.
    engine_golden = _make_engine()
    engine_sg = _make_engine()
    train_features = features.iloc[:_TRAIN_BARS]
    engine_golden.select_and_train(train_features)
    engine_sg.select_and_train(train_features)

    # Tiền đề: cùng model đã train (dataclass RegimeInfo frozen -> so bằng
    # giá trị, không phải danh tính object).
    assert engine_golden.regime_infos == engine_sg.regime_infos, (
        "Hai engine train cùng seed/config nhưng regime_infos khác nhau — "
        "select_and_train() không còn xác định luận như kỳ vọng, dừng ở "
        "đây trước khi so sánh downstream (vô nghĩa nếu tiền đề này sai)."
    )

    # StrategyOrchestrator/StructuralTrendGate: THUẦN, dùng chung an toàn
    # cho cả ba đường — xem docstring module.
    orchestrator = StrategyOrchestrator(
        min_confidence=0.55, rebalance_threshold_pct=Decimal("25"), uncertainty_mode="halve"
    )
    trend_gate = StructuralTrendGate(TrendGateConfig())

    # Tiền đề bên trên ("StrategyOrchestrator THUẦN") trước đây chỉ được
    # xác nhận bằng ĐỌC LẠI code mỗi lần nghi ngờ — khoá lại bằng một
    # assertion runtime NGAY TẠI ĐÂY thay vì đọc code: gọi
    # generate_signal() HAI LẦN với đầu vào giống hệt, khẳng định output
    # giống hệt. Toàn bộ vòng lặp bên dưới (dùng `orchestrator` nhiều lần
    # cho các bar khác nhau, và hai lần trên cùng bar để so (1)+(2) với
    # (3)) dựa trên đúng giả định này — nếu nó sai, mọi so sánh sau đó vô
    # nghĩa, nên dừng NGAY ở đây thay vì để lộ ra thành một lệch
    # `final_allocation` khó chẩn đoán ở bar nào đó về sau.
    #
    # Dùng `regime_state`/`bars` TỰ TẠO (KHÔNG lấy từ `engine_golden`/
    # `engine_sg`): `predict_regime_filtered()` có tác dụng phụ tích luỹ
    # (bộ lọc ổn định) — gọi nó ở đây để "mượn" một regime_state thật sẽ
    # làm lệch đồng bộ hai engine TRƯỚC KHI vòng lặp chính bắt đầu, đúng
    # loại lỗi mà docstring module này đã cảnh báo.
    _idempotency_regime_state = RegimeState(
        label="TEST",
        state_id=0,
        probability=0.9,
        state_probabilities=np.array([0.9, 0.1]),
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        is_confirmed=True,
        consecutive_bars=5,
    )
    _idempotency_regime_infos = [
        RegimeInfo(
            regime_id=0,
            regime_name="A",
            expected_return=0.0,
            expected_volatility=0.1,
            recommended_strategy_type="placeholder",
            max_allocation_pct=1.0,
            min_confidence_to_act=0.55,
        )
    ]
    _idempotency_bars = ohlcv.iloc[:_TRAIN_BARS]  # đủ dài cho EMA50/ATR14, độc lập với engine
    _idempotency_args = (
        _SYMBOL,
        _idempotency_regime_state,
        _idempotency_regime_infos,
        _idempotency_bars,
        Decimal("0.4"),
        False,
    )
    _idempotency_first = orchestrator.generate_signal(*_idempotency_args)
    _idempotency_second = orchestrator.generate_signal(*_idempotency_args)
    assert _idempotency_first == _idempotency_second, (
        "orchestrator.generate_signal() với ĐÚNG CÙNG input cho kết quả khác nhau ở lần gọi thứ "
        f"hai — StrategyOrchestrator có state ẩn: first={_idempotency_first!r} "
        f"second={_idempotency_second!r}. TOÀN BỘ phép so sánh dưới đây (dùng chung một "
        "orchestrator cho nhiều bar, và hai lần trên cùng bar để so (1)+(2) với (3)) dựa trên "
        "giả định orchestrator THUẦN — dừng ở đây, không so sánh tiếp."
    )

    risk_manager = _passthrough_risk_manager(tmp_path / "trading_halted.lock")
    signal_generator = SignalGenerator(engine_sg, trend_gate, orchestrator, risk_manager)

    portfolio_state = PortfolioState(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        available_balance=Decimal("10000"),
        positions={},
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        peak_equity=Decimal("10000"),
        drawdown=Decimal("0"),
        circuit_breaker_status={},
        flicker_rate=0.0,
    )

    current_allocation = Decimal("0")
    checked_bars = 0

    for offset in range(_N_PREDICT_BARS):
        i = _TRAIN_BARS + offset
        ts = features.index[i]
        features_so_far = features.iloc[: i + 1]
        bars_window = ohlcv.loc[:ts]  # KHÔNG giới hạn — xem docstring module

        # --- Đường (1)+(2): golden/forward-style — gọi thẳng, không qua SignalGenerator ---
        regime_state_golden = engine_golden.predict_regime_filtered(features_so_far)
        is_flickering_golden = engine_golden.is_flickering()
        signal_golden = orchestrator.generate_signal(
            _SYMBOL,
            regime_state_golden,
            engine_golden.regime_infos,
            bars_window,
            current_allocation,
            is_flickering_golden,
        )
        trend_gate_cap_golden = trend_gate.get_allocation_cap(bars_window)
        hmm_allocation_golden = signal_golden.target_allocation_pct
        final_allocation_golden = compose_layer_allocations(hmm_allocation_golden, trend_gate_cap_golden)

        # --- Đường (3): SignalGenerator thật — đường main.py dùng ---
        result_sg = signal_generator.generate(
            _SYMBOL, features_so_far, bars_window, current_allocation, portfolio_state
        )

        # Tiền đề trước khi so composition: cùng regime_state/is_flickering
        # (xác nhận bằng đột biến của phép replay xác định luận ở trên,
        # không giả định suông).
        assert result_sg.regime_state.state_id == regime_state_golden.state_id, (
            f"Bar {i} (ts={ts.date()}): regime_id lệch giữa engine độc lập — "
            f"SignalGenerator={result_sg.regime_state.state_id} golden-style="
            f"{regime_state_golden.state_id}. Nếu lệch ở đây, hai engine đã trôi "
            "khỏi trạng thái đồng bộ — kiểm tra select_and_train()/predict_regime_filtered() "
            "có còn xác định luận không TRƯỚC khi nghi ngờ composition."
        )
        assert result_sg.regime_state.label == regime_state_golden.label
        assert result_sg.is_flickering == is_flickering_golden

        # SignalGenerator không lộ hmm_allocation (giá trị TRƯỚC khi áp
        # trend_gate cap) qua kết quả trả về — tính lại bằng CHÍNH
        # orchestrator (thuần, gọi lại cho cùng input luôn cho cùng kết
        # quả bit-for-bit, không phải suy luận/xấp xỉ).
        signal_sg_precap = orchestrator.generate_signal(
            _SYMBOL,
            result_sg.regime_state,
            engine_sg.regime_infos,
            bars_window,
            current_allocation,
            result_sg.is_flickering,
        )
        hmm_allocation_sg = signal_sg_precap.target_allocation_pct
        trend_gate_cap_sg = trend_gate.get_allocation_cap(bars_window)

        assert hmm_allocation_sg == hmm_allocation_golden, (
            f"Bar {i} (ts={ts.date()}): hmm_allocation LỆCH — SignalGenerator="
            f"{hmm_allocation_sg} golden/forward-style={hmm_allocation_golden}."
        )
        assert trend_gate_cap_sg == trend_gate_cap_golden, (
            f"Bar {i} (ts={ts.date()}): trend_gate_cap LỆCH — SignalGenerator="
            f"{trend_gate_cap_sg} golden/forward-style={trend_gate_cap_golden}."
        )

        assert result_sg.decision.approved, (
            f"Bar {i}: risk_manager (đã cấu hình pass-through hoàn toàn) vẫn từ chối — "
            f"{result_sg.decision.rejection_reason}. Kiểm tra lại cấu hình "
            "_passthrough_risk_manager() trước khi nghi ngờ SignalGenerator."
        )
        final_signal_sg = result_sg.decision.modified_signal
        assert final_signal_sg is not None
        final_allocation_sg = final_signal_sg.target_allocation_pct

        assert final_allocation_sg == final_allocation_golden, (
            f"Bar {i} (ts={ts.date()}): final_allocation LỆCH giữa SignalGenerator "
            f"({final_allocation_sg}) và golden/forward-style ({final_allocation_golden}) — "
            f"hmm_allocation khớp ({hmm_allocation_golden}), trend_gate_cap khớp "
            f"({trend_gate_cap_golden}), nên lệch nằm ở CÔNG THỨC KẾT HỢP hai giá trị đó "
            "(SignalGenerator._apply_layer_caps() hoặc compose_layer_allocations()) — "
            "xem docstring module, KHÔNG tự sửa forward/logger.py hay golden test."
        )

        current_allocation = final_allocation_golden
        checked_bars += 1

    assert checked_bars == _N_PREDICT_BARS, "Vòng lặp dừng sớm — không phủ hết dải bar dự kiến."
