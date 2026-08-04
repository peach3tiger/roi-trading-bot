"""tests.test_look_ahead — BẮT BUỘC. Chứng minh predict_regime_filtered
không có look-ahead bias, và model.predict() CÓ (để chứng minh test có
tác dụng).

Đây là chi tiết kỹ thuật quan trọng nhất của toàn bộ dự án — xem CLAUDE.md
bất biến #1. Implement đầy đủ ở Phase 2. Sau khi implement, file này
KHÔNG được skip, không được xfail, không được comment out (bất biến #15).
"""

import pytest


def test_no_look_ahead_bias() -> None:
    """Chạy predict_regime_filtered trên dữ liệu tới bar N. Chạy lại trên
    dữ liệu tới bar N+50, cắt lấy kết quả tại bar N. Hai kết quả PHẢI
    giống hệt nhau. Nếu khác → có look-ahead bias → dừng lại, sửa trước
    khi đi tiếp."""
    pytest.skip("TODO: Phase 2 — core/hmm_engine.py predict_regime_filtered")


def test_model_predict_has_look_ahead_bias() -> None:
    """Chạy cùng phép so sánh với model.predict() để thấy nó FAIL — bằng
    chứng cho thấy test_no_look_ahead_bias thật sự có tác dụng phát hiện bug."""
    pytest.skip("TODO: Phase 2 — chứng minh model.predict() thất bại phép test này")
