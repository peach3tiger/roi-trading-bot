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


# ----------------------------------------------------------------------
# Cách ly môi trường — bộ test KHÔNG được đọc `.env` thật
# ----------------------------------------------------------------------

# Biến môi trường mang CREDENTIAL hoặc điều khiển đích gửi ra ngoài.
#
# ĐÂY LÀ DANH SÁCH ĐEN, và danh sách đen có khuyết tật cố hữu: biến thứ
# N+1 thêm sau này sẽ không được xoá và không ai biết. Nó KHÔNG phải lớp
# phòng thủ chính — lớp chính là `_chan_doc_env_that()` bên dưới, vá
# ĐƯỜNG RÒ RỈ nên đóng cho MỌI biến, có tên hay chưa.
#
# Danh sách này chỉ còn một việc: chặn biến người dùng đã `export` sẵn
# trong shell (đường mà việc vá `load_dotenv` không với tới).
# `tests/test_env_isolation.py` ghim nó không tụt lại sau code.
CREDENTIAL_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET",
    "EXCHANGE_TESTNET",
    "MONITORING_WEBHOOK_URL",
    "MONITORING_SMTP_HOST",
    "MONITORING_SMTP_PORT",
    "MONITORING_SMTP_USERNAME",
    "MONITORING_SMTP_PASSWORD",
    "MONITORING_EMAIL_FROM",
    "MONITORING_EMAIL_TO",
)

# Biến môi trường ĐƯỢC PHÉP không nằm trong `CREDENTIAL_ENV`: đường dẫn và
# tham số vận hành, không phải bí mật, và nhiều test cố tình đặt chúng.
# Mỗi tên ở đây là một quyết định "cái này KHÔNG phải credential" — biến
# thứ 20 xuất hiện sẽ phải vào một trong hai danh sách, không thể im lặng.
NON_SECRET_ENV = (
    "CONFIG_PATH",
    "LOG_DIR",
    "MODEL_PATH",
    "REQUIRE_HMM_MODEL",
    "STATE_DIR",
    "WATCHDOG_POLL_SEC",
    "WATCHDOG_STALE_SEC",
    # Đường dẫn file do GitHub Actions cấp; `ops/ci_bao_cao.py` ghi bảng
    # markdown vào đó. Không bí mật, và vắng mặt ở local là trạng thái
    # BÌNH THƯỜNG — `them_summary()` trả False.
    "GITHUB_STEP_SUMMARY",
    # Số thread BLAS. `ci.yml` đặt cả năm ở tầng job và
    # `ops/kiem_tat_dinh.py` đọc chúng để in dấu vân tay. Tham số VẬN
    # HÀNH, không phải bí mật — nhưng chúng đổi KẾT QUẢ SỐ, nên phải có
    # mặt trong dấu vân tay chứ không được lặng lẽ vắng.
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


@pytest.fixture(autouse=True)
def _cach_ly_moi_truong(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hai lớp, lớp đầu mới là lớp thật.

    ## Bug đã gặp (2026-08-14), không phải phòng xa

    `monitoring/forward_watchdog.py::load_dotenv()` gọi với đường dẫn MẶC
    ĐỊNH đọc `.env` THẬT của dự án và ghi thẳng vào `os.environ`.
    `monkeypatch` không hoàn tác được thứ nó không đặt, nên biến rò rỉ
    sang mọi test chạy sau trong cùng phiên.

    Hậu quả ĐO ĐƯỢC: ngày `.env` được điền `TELEGRAM_BOT_TOKEN` thật, hai
    test trong `test_monitoring_alerts.py` chuyển từ xanh sang đỏ mà KHÔNG
    dòng code nào đổi. Chúng chỉ không gửi thật vì đã patch
    `requests.post`.

    ## Lớp 1 — chặn ĐƯỜNG RÒ RỈ (đóng cho MỌI biến)

    Vá `load_dotenv` để lời gọi với đường dẫn mặc định NÉM LỖI. Đây là
    lớp mạnh hơn hẳn một danh sách tên biến: nó không cần biết biến tên gì,
    nên biến thứ 20 thêm vào `.env` cũng bị chặn.

    Ném lỗi chứ không trả `[]` im lặng: một lời gọi mặc định trong test là
    một lỗi lập trình cần thấy ngay, không phải một no-op cần bỏ qua.

    Gọi với đường dẫn TƯỜNG MINH vẫn chạy bình thường — đó là cách
    `test_load_dotenv_khong_ghi_de_moi_truong_that` kiểm chính hàm này.

    Dưới launchd, `load_dotenv()` vẫn hoạt động đầy đủ: đó là tiến trình
    khác, không có conftest nào.

    ## Lớp 2 — xoá credential đã `export` sẵn trong shell

    Đường mà lớp 1 không với tới. Danh sách đen, và
    `tests/test_env_isolation.py` ghim nó không tụt lại sau code.
    """
    import monitoring.forward_watchdog as fw

    that = fw.load_dotenv

    def _chan_doc_env_that(path: "object | None" = None) -> "list[str]":
        if path is None:
            raise AssertionError(
                "load_dotenv() với đường dẫn MẶC ĐỊNH bị chặn trong test — nó đọc "
                "`.env` THẬT và ghi vào os.environ, rò rỉ sang mọi test chạy sau. "
                "Truyền đường dẫn tường minh (tmp_path) nếu đang kiểm chính hàm này."
            )
        return that(path)  # type: ignore[arg-type]

    monkeypatch.setattr(fw, "load_dotenv", _chan_doc_env_that)

    for ten in CREDENTIAL_ENV:
        monkeypatch.delenv(ten, raising=False)
