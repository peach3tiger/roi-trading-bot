"""tests.test_layer_composition — min() chứ không max(). Bất biến #2 của CLAUDE.md.

Property test trên giá trị ngẫu nhiên: final_allocation phải luôn bằng
min(hmm_allocation, trend_gate_cap, risk_manager_cap), không bao giờ vượt
giá trị nhỏ nhất trong ba tầng. Implement ở Phase 3.5/5 cùng
core/signal_generator.py. Sau khi implement, file này KHÔNG được skip,
không được xfail, không được comment out (bất biến #15).
"""

import pytest


def test_final_allocation_is_min_of_three_layers() -> None:
    pytest.skip("TODO: Phase 3.5/5 — core/signal_generator.py, property test giá trị ngẫu nhiên")


def test_no_layer_can_increase_others_output() -> None:
    pytest.skip("TODO: Phase 3.5/5 — kiểm chứng bằng min() thay vì max()/trung bình")
