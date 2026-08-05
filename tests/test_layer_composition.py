"""tests.test_layer_composition — hàm tối thiểu, không phải hàm tối đa. Bất biến #2 của CLAUDE.md.

Property test trên giá trị ngẫu nhiên: final_allocation phải luôn bằng
giá trị nhỏ nhất của (hmm_allocation, trend_gate_cap, risk_manager_cap),
không bao giờ vượt giá trị nhỏ nhất trong ba tầng.
"""

from __future__ import annotations

import random
from decimal import Decimal

from core.signal_generator import compose_layer_allocations

_N_TRIALS = 10_000


def _random_decimal(rng: random.Random) -> Decimal:
    return Decimal(str(round(rng.uniform(0.0, 1.0), 6)))


def test_final_allocation_is_min_of_three_layers() -> None:
    rng = random.Random(42)
    for _ in range(_N_TRIALS):
        hmm_alloc = _random_decimal(rng)
        trend_gate_cap = _random_decimal(rng)
        risk_cap = _random_decimal(rng)

        result = compose_layer_allocations(hmm_alloc, trend_gate_cap, risk_cap)

        assert result == min(hmm_alloc, trend_gate_cap, risk_cap)
        assert result <= hmm_alloc
        assert result <= trend_gate_cap
        assert result <= risk_cap


def test_no_layer_can_increase_others_output() -> None:
    """Thêm một tầng vào phép kết hợp chỉ có thể giữ nguyên hoặc giảm kết
    quả — không bao giờ làm nó tăng lên, bất kể giá trị tầng mới thêm vào
    là bao nhiêu."""
    rng = random.Random(7)
    for _ in range(_N_TRIALS):
        hmm_alloc = _random_decimal(rng)
        trend_gate_cap = _random_decimal(rng)
        risk_cap = _random_decimal(rng)

        two_layer = compose_layer_allocations(hmm_alloc, trend_gate_cap)
        three_layer = compose_layer_allocations(hmm_alloc, trend_gate_cap, risk_cap)

        assert three_layer <= two_layer


def test_compose_requires_at_least_one_cap() -> None:
    try:
        compose_layer_allocations()
    except ValueError:
        pass
    else:
        raise AssertionError("compose_layer_allocations() rỗng phải raise ValueError")
