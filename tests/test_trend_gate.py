"""tests.test_trend_gate — Gate chỉ giảm, không tăng. Implement ở Phase 3.5."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from core.trend_gate import StructuralTrendGate, StructureState, TrendGateConfig


def _make_bars_from_closes(closes: list[float] | np.ndarray, start: str = "2020-01-01") -> pd.DataFrame:
    closes_arr = np.asarray(closes, dtype=float)
    index = pd.date_range(start, periods=len(closes_arr), freq="D", tz="UTC")
    close = pd.Series(closes_arr, index=index)
    high = close * 1.001
    low = close * 0.999
    open_ = close.shift(1).bfill()
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def test_gate_never_increases_allocation() -> None:
    closes = np.linspace(100, 200, 260)  # xu hướng tăng dài, đủ warmup SMA200
    bars = _make_bars_from_closes(closes)
    gate = StructuralTrendGate(TrendGateConfig())

    cap = gate.get_allocation_cap(bars)
    assert cap <= Decimal("1.00")  # trần BULL_STRUCTURE (100%) là mức cao nhất có thể

    # Kết hợp với bất kỳ allocation nào từ tầng khác qua hàm tối thiểu
    # không bao giờ cho ra kết quả CAO HƠN chính allocation đó.
    for hmm_alloc in [Decimal("0.10"), Decimal("0.50"), Decimal("0.95"), Decimal("1.00")]:
        combined = min(hmm_alloc, cap)
        assert combined <= hmm_alloc
        assert combined <= cap


def test_buffer_prevents_whipsaw() -> None:
    """Xu hướng tăng dài rồi đi ngang (để SMA200 hội tụ gần mức giá hiện
    tại — SMA200 phản ứng rất chậm với một xu hướng dốc, dao động quanh
    giá cuối của một con dốc sẽ KHÔNG chạm gần SMA200 thật), sau đó dao
    động hẹp ±1% quanh chính giá trị SMA200 đã hội tụ đó — dải dao động
    nằm trong buffer ±2%, trạng thái không được đổi trong suốt đoạn đó.
    """
    n_ramp = 200
    ramp = np.linspace(100, 200, n_ramp)
    n_plateau = 150
    plateau = np.full(n_plateau, 200.0)
    base = np.concatenate([ramp, plateau])
    sma_at_end_of_base = pd.Series(base).rolling(200, min_periods=200).mean().iloc[-1]

    n_tail = 40
    rng = np.random.default_rng(0)
    tail = sma_at_end_of_base * (1 + rng.uniform(-0.01, 0.01, n_tail))
    closes = np.concatenate([base, tail])
    bars = _make_bars_from_closes(closes)

    gate = StructuralTrendGate(TrendGateConfig())
    history = gate.get_structure_history(bars)

    tail_states = history["confirmed_state"].iloc[-n_tail:]
    assert tail_states.notna().all()
    assert tail_states.nunique() == 1, f"trạng thái đổi trong vùng dao động hẹp: {tail_states.unique()}"

    # Xác nhận thật sự đang test đúng điều kiện: giá phải dao động cả hai
    # phía quanh SMA200 (không phải tình cờ luôn nằm một bên) và luôn
    # trong dải buffer.
    price_vs_sma = gate._compute_price_vs_sma200(bars).iloc[-n_tail:]
    assert price_vs_sma.min() < 0 < price_vs_sma.max(), "test không thực sự bắt chéo SMA200 cả hai phía"
    assert price_vs_sma.abs().max() < float(TrendGateConfig().buffer_pct)


def _make_down_then_up_bars() -> pd.DataFrame:
    """Tăng dài (BULL vững) -> sập 70% trong 200 bar (đủ lâu để slope SMA200
    thật sự quay âm — SMA200 phản ứng rất chậm với riêng giá) -> hồi phục
    x3 trong 150 bar. Đủ dữ liệu để cả hai chiều raw_state đều đạt tới
    BEAR_STRUCTURE và BULL_STRUCTURE một cách dứt khoát, không chỉ dừng ở
    TRANSITION.
    """
    n_base = 260
    base = np.linspace(100, 250, n_base)
    n_crash = 200
    crash = np.linspace(base[-1], base[-1] * 0.3, n_crash)[1:]  # bỏ điểm trùng đầu với base[-1]
    n_recover = 150
    recover = np.linspace(crash[-1], crash[-1] * 3.0, n_recover)[1:]
    closes = np.concatenate([base, crash, recover])
    return _make_bars_from_closes(closes)


def _find_sustained_run(raw: pd.Series, state_value: str, confirm_bars: int, min_run: int) -> int:
    """Tìm bar đầu tiên mà raw_state == state_value và duy trì liên tục ít
    nhất `min_run` bar kể từ đó — tránh bắt nhầm một bar lẻ do nhiễu."""
    is_target = raw == state_value
    for i in range(len(is_target) - min_run):
        if is_target.iloc[i] and is_target.iloc[i : i + min_run].all():
            return i
    raise AssertionError(
        f"kịch bản test không tạo được đoạn {state_value} đủ dài (>= {min_run} bar) — "
        "tăng độ dài/mạnh của cú sập hoặc hồi phục"
    )


def test_confirm_bars_delays_state_change() -> None:
    """raw_state đổi hẳn (duy trì liên tục) sang BEAR_STRUCTURE tại một
    bar X — confirmed_state phải giữ nguyên trạng thái CŨ trong
    confirm_bars-1 bar tiếp theo, rồi mới đổi sang BEAR_STRUCTURE đúng ở
    bar thứ confirm_bars kể từ X."""
    config = TrendGateConfig()
    bars = _make_down_then_up_bars()
    gate = StructuralTrendGate(config)
    history = gate.get_structure_history(bars).dropna()

    raw = history["raw_state"]
    confirmed = history["confirmed_state"]

    run_start = _find_sustained_run(
        raw, StructureState.BEAR_STRUCTURE.value, config.confirm_bars, min_run=config.confirm_bars + 5
    )
    state_before = confirmed.iloc[run_start - 1]
    assert state_before != StructureState.BEAR_STRUCTURE.value

    for i in range(run_start, run_start + config.confirm_bars - 1):
        assert confirmed.iloc[i] == state_before, (
            f"confirmed_state đổi sớm ở bar thứ {i - run_start + 1} sau khi raw đổi — "
            f"phải đợi đủ {config.confirm_bars} bar"
        )
    assert confirmed.iloc[run_start + config.confirm_bars - 1] == StructureState.BEAR_STRUCTURE.value


def test_cap_only_tightens_immediately_loosens_after_confirmation() -> None:
    """Trần giảm có hiệu lực NGAY ở bar đầu tiên raw_state xấu đi (không
    cần đợi xác nhận). Trần chỉ tăng trở lại SAU KHI trạng thái mới được
    xác nhận đủ confirm_bars — kiểm cả hai chiều trên cùng một chuỗi
    giảm-rồi-hồi-phục."""
    config = TrendGateConfig()
    bars = _make_down_then_up_bars()
    gate = StructuralTrendGate(config)
    history = gate.get_structure_history(bars).dropna()

    raw = history["raw_state"]
    confirmed = history["confirmed_state"]
    cap = history["cap"]

    # --- Chiều xuống: cap siết ngay khi raw xấu đi, dù confirmed chưa đổi ---
    down_start = _find_sustained_run(
        raw, StructureState.BEAR_STRUCTURE.value, config.confirm_bars, min_run=config.confirm_bars + 5
    )
    assert confirmed.iloc[down_start] != StructureState.BEAR_STRUCTURE.value  # chưa xác nhận
    assert cap.iloc[down_start] == config.cap_bear_structure  # nhưng trần đã siết ngay

    # --- Chiều lên: sau khi đã ở BEAR_STRUCTURE, raw hồi lại BULL_STRUCTURE
    # --- nhưng trần KHÔNG được nới ngay, phải đợi xác nhận.
    after_bear = raw.iloc[down_start:]
    up_start_local = _find_sustained_run(
        after_bear.reset_index(drop=True),
        StructureState.BULL_STRUCTURE.value,
        config.confirm_bars,
        min_run=config.confirm_bars + 5,
    )
    up_start = down_start + up_start_local

    assert confirmed.iloc[up_start] != StructureState.BULL_STRUCTURE.value  # chưa xác nhận
    assert cap.iloc[up_start] < config.cap_bull_structure  # trần CHƯA được nới lên 100%
    for i in range(up_start, up_start + config.confirm_bars - 1):
        assert cap.iloc[i] < config.cap_bull_structure, "trần nới lên sớm trước khi được xác nhận"
    assert cap.iloc[up_start + config.confirm_bars - 1] == config.cap_bull_structure
