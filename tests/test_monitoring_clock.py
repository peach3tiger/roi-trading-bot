"""tests.test_monitoring_clock — monitoring/clock.py: hiệu chỉnh round-trip
đúng công thức NTP-style, median-of-3 loại nhiễu, không trộn field giữa
các lần đo khác nhau."""

from __future__ import annotations

from typing import Callable

from monitoring.clock import measure_clock_drift


def _fixed_offset_zero_round_trip(
    local_time_ms: int, offset_ms: int
) -> tuple[Callable[[], int], Callable[[], int]]:
    """t0 == t1 (round-trip 0) cho cả 3 lần đo — cô lập phần "offset" của
    công thức, không lẫn phần hiệu chỉnh round-trip."""
    local_calls = iter([local_time_ms] * 6)  # 3 sample x (t0, t1)
    server_calls = iter([local_time_ms + offset_ms] * 3)

    def local_ms_fn() -> int:
        return next(local_calls)

    def get_server_time() -> int:
        return next(server_calls)

    return local_ms_fn, get_server_time


def test_drift_positive_offset_zero_round_trip() -> None:
    local_fn, server_fn = _fixed_offset_zero_round_trip(1_000_000, 1500)
    check = measure_clock_drift(server_fn, local_ms_fn=local_fn)
    assert check.drift_ms == 1500
    assert check.round_trip_ms == 0


def test_drift_negative_offset_zero_round_trip() -> None:
    local_fn, server_fn = _fixed_offset_zero_round_trip(1_000_000, -3000)
    check = measure_clock_drift(server_fn, local_ms_fn=local_fn)
    assert check.drift_ms == -3000
    assert check.round_trip_ms == 0


def test_drift_zero_offset_zero_round_trip() -> None:
    local_fn, server_fn = _fixed_offset_zero_round_trip(1_000_000, 0)
    check = measure_clock_drift(server_fn, local_ms_fn=local_fn)
    assert check.drift_ms == 0
    assert check.round_trip_ms == 0


# ----------------------------------------------------------------------
# Hiệu chỉnh round-trip — cùng drift THẬT, round-trip khác nhau (50ms vs
# 400ms) phải cho drift_ms gần nhau. Công thức ngây thơ (server - t1) sẽ
# lệch theo đúng round-trip (350ms khác biệt) — công thức đúng thì không.
# ----------------------------------------------------------------------


def _make_fakes(samples: list[tuple[int, int, int]]) -> tuple[Callable[[], int], Callable[[], int]]:
    """`samples`: list[(t0, t1, server)] — mỗi phần tử là MỘT lần đo đầy
    đủ, kiểm soát tuyệt đối thứ tự local_ms_fn()/get_server_time() được
    measure_clock_drift() gọi (t0, server, t1, lặp lại)."""
    local_calls: list[int] = []
    for t0, t1, _server in samples:
        local_calls.append(t0)
        local_calls.append(t1)
    local_iter = iter(local_calls)
    server_iter = iter(server for _, _, server in samples)

    def local_ms_fn() -> int:
        return next(local_iter)

    def get_server_time() -> int:
        return next(server_iter)

    return local_ms_fn, get_server_time


def test_correction_makes_different_round_trips_agree() -> None:
    true_drift = 1000.0

    # round-trip 50ms: t0=0, t1=50, server đọc đúng lúc giữa (t=25) với drift thật 1000
    samples_fast = [(0, 50, int(25 + true_drift))] * 3
    local_fast, server_fast = _make_fakes(samples_fast)
    check_fast = measure_clock_drift(server_fast, local_ms_fn=local_fast)

    # round-trip 400ms: t0=0, t1=400, server đọc đúng lúc giữa (t=200) với CÙNG drift thật
    samples_slow = [(0, 400, int(200 + true_drift))] * 3
    local_slow, server_slow = _make_fakes(samples_slow)
    check_slow = measure_clock_drift(server_slow, local_ms_fn=local_slow)

    assert check_fast.round_trip_ms == 50
    assert check_slow.round_trip_ms == 400
    # Công thức đúng: cả hai phải ra ~1000ms, KHÔNG lệch nhau ~350ms (chênh
    # lệch round-trip) như công thức ngây thơ (server - t1) sẽ cho.
    assert abs(check_fast.drift_ms - check_slow.drift_ms) < 2.0
    assert abs(check_fast.drift_ms - true_drift) < 2.0
    assert abs(check_slow.drift_ms - true_drift) < 2.0


def test_naive_formula_would_disagree_by_round_trip_confirming_test_is_meaningful() -> None:
    """Không phải test chính — xác nhận rằng NẾU dùng công thức ngây thơ
    (server - t1) trên CHÍNH bộ dữ liệu ở test trên, hai round-trip khác
    nhau THẬT SỰ cho kết quả khác nhau đáng kể — chứng minh
    test_correction_makes_different_round_trips_agree() không vô nghĩa
    (không phải hai round-trip nào cũng tình cờ cho cùng kết quả)."""
    true_drift = 1000.0
    t1_fast, server_fast_val = 50, int(25 + true_drift)
    t1_slow, server_slow_val = 400, int(200 + true_drift)

    naive_fast = server_fast_val - t1_fast
    naive_slow = server_slow_val - t1_slow
    # naive_drift = true_drift - round_trip/2 (t0=0) => chênh lệch giữa hai
    # round-trip = (400-50)/2 = 175ms — lệch rõ rệt so với dung sai <2ms mà
    # công thức ĐÚNG đạt được ở test_correction_makes_different_round_trips_agree().
    assert abs(naive_fast - naive_slow) > 100


# ----------------------------------------------------------------------
# Median-of-3: chọn ĐÚNG cặp (drift, round_trip) của lần đo ở giữa, không
# trộn field từ các lần đo khác nhau.
# ----------------------------------------------------------------------


def test_median_selects_middle_sample_keeping_its_own_round_trip() -> None:
    samples = [
        (0, 100, 1050),  # rt=100, mid=50, drift=1000
        (0, 0, 2000),  # rt=0, mid=0, drift=2000
        (0, 400, 1700),  # rt=400, mid=200, drift=1500
    ]
    local_fn, server_fn = _make_fakes(samples)
    check = measure_clock_drift(server_fn, local_ms_fn=local_fn)

    assert check.drift_ms == 1500
    # round_trip PHẢI đến từ CÙNG lần đo có drift=1500 (400ms), KHÔNG phải
    # từ lần đo có drift nhỏ nhất (100ms) hay lớn nhất (0ms).
    assert check.round_trip_ms == 400


def test_measure_clock_drift_rejects_zero_samples() -> None:
    import pytest

    with pytest.raises(ValueError):
        measure_clock_drift(lambda: 0, samples=0)
