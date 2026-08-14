"""Cấu hình dùng chung cho toàn bộ test suite.

Hai việc: chốt profile Hypothesis, và ghi biên lai cho cổng §E
(`ops/readiness_gate.py`) sau mỗi phiên `-m slow` xanh.

Profile Hypothesis đặt ở `conftest.py` chứ
không ở từng file test — profile phải được nạp TRƯỚC khi bất kỳ
`@given` nào chạy, và conftest là chỗ duy nhất bảo đảm được điều đó bất
kể pytest thu thập file theo thứ tự nào.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, settings

# `deadline=None` — Hypothesis mặc định bỏ 200ms cho MỖI ví dụ. Property
# nào chạm tới HMM (`predict_regime_filtered`, `select_and_train`) sẽ vượt
# ngưỡng đó trên một số ví dụ chứ không phải mọi ví dụ, nên test sẽ lúc
# xanh lúc đỏ tuỳ tải máy. Một test không tái lập được còn tệ hơn không có
# test: nó dạy người đọc bỏ qua màu đỏ.
#
# `derandomize=True` — mặc định Hypothesis sinh ví dụ ngẫu nhiên mới mỗi
# lần chạy. Với một dự án vừa bỏ công chốt tính tất định của backtest
# (`tests/test_determinism.py`, `hmm.seed`), để bộ test tự nó không tái
# lập được là mâu thuẫn thẳng: cùng một commit sẽ cho kết quả khác nhau
# giữa hai lần chạy, và "test đỏ" mất nghĩa là "code sai".
#
# Đánh đổi CÓ CHỦ Ý: derandomize làm mất khả năng dò rộng dần theo thời
# gian — Hypothesis sẽ luôn thử đúng bộ ví dụ ấy. Muốn dò rộng thì bật
# profile khác một cách TƯỜNG MINH, đừng bỏ dòng này:
#
#     pytest -p no:cacheprovider --hypothesis-profile=explore
#
# `suppress_health_check=[HealthCheck.too_slow]` — cùng lý do `deadline`:
# health check này đo tốc độ SINH dữ liệu, và một strategy dựng DataFrame
# cho HMM tất nhiên chậm. Nó cảnh báo về hiệu năng, không về tính đúng.
settings.register_profile(
    "ci",
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)

# Profile để dò rộng khi CẦN — không dùng mặc định, xem chú thích trên.
settings.register_profile(
    "explore",
    deadline=None,
    derandomize=False,
    max_examples=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("ci")


# ----------------------------------------------------------------------
# Biên lai chạy slow — cổng §E (`ops/readiness_gate.py`)
# ----------------------------------------------------------------------


def pytest_sessionfinish(session: "pytest.Session", exitstatus: int) -> None:
    """Ghi `.slow_receipt.json` sau một phiên `-m slow` XANH HOÀN TOÀN.

    Sinh biên lai TỰ ĐỘNG, từ chính lần chạy thật, thay vì để người ta gõ
    tay một con dấu "tôi đã chạy rồi" — một lời khai không ai kiểm chứng
    thì không phải bằng chứng.

    Chính sách "khi nào được cấp biên lai" nằm ở
    `ops/readiness_gate.py::should_write_receipt`, KHÔNG ở đây: conftest là
    hạ tầng của chính pytest nên một `if` sai trong file này sống sót qua
    mọi phép đột biến. Đo được, và đã đo — hai đột biến lọt lưới ở bản đầu.
    File này chỉ còn phần không thể tách: đếm test `slow` từ `session`.
    """
    from ops.readiness_gate import should_write_receipt, write_receipt

    slow_count = sum(
        1 for item in getattr(session, "items", []) if item.get_closest_marker("slow") is not None
    )
    if not should_write_receipt(
        exitstatus=exitstatus, tests_failed=session.testsfailed, slow_tests=slow_count
    ):
        return

    write_receipt(slow_tests=slow_count)
