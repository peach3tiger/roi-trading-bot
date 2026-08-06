"""tests.test_health_check — ops/health_check.py phải ĐỎ đúng lúc.

Trọng tâm KHÔNG phải "mọi thứ OK thì health check báo OK" (dễ viết, dễ tự
lừa mình) mà là bốn cách nó phải FAIL, xác nhận bằng dựng lỗi thật qua một
sàn ccxt giả đăng ký thẳng vào module `ccxt` thật (không mock toàn bộ
`ops.health_check`) — nếu check nào lỡ nuốt lỗi hoặc lỡ nhắm nhầm sàn, test
này phải đỏ theo, không phải xanh giả:

1. Thiếu EXCHANGE_API_KEY/SECRET trong env -> exchange_authenticated FAIL,
   KHÔNG được gọi ra sàn (không cách nào xác thực được nếu chưa có key).
2. Key sai (ccxt.AuthenticationError thật) -> FAIL, thông điệp lỗi phải
   xuất hiện trong detail (không bị nuốt/thay bằng "OK" hay im lặng).
3. `settings.yaml: exchange.name` đổi sang sàn khác -> check phải gọi
   đúng `getattr(ccxt, <tên mới>)`, không phải sàn hardcode nào — xác
   nhận bằng hai sàn giả riêng biệt, chỉ một cái được gọi.
4. Endpoint public OK (`exchange_reachable`) nhưng auth fail
   (`exchange_authenticated`) -> `main()` phải thoát mã khác 0 (tổng thể
   FAIL), không được xanh nhờ các check khác đều OK (4/5, 5/6...).

Đăng ký fake exchange class thẳng vào module `ccxt` thật qua
`monkeypatch.setattr(ccxt, name, cls, raising=False)` — đi đúng đường
`getattr(ccxt, exchange_id, None)` mà `ops/health_check.py` dùng, không
tạo đường tắt riêng chỉ có trong test.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import ccxt
import pytest
import yaml

from ops import health_check


@pytest.fixture(autouse=True)
def _clean_exchange_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không để credential thật (nếu shell đang chạy test có export sẵn)
    lọt vào test — mọi test tự set đúng biến nó cần, không thừa hưởng môi
    trường ngoài."""
    for name in (
        "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET",
        "EXCHANGE_TESTNET",
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "BYBIT_TESTNET",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_config(tmp_path: Path, exchange_name: str) -> Path:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump({"exchange": {"name": exchange_name, "testnet": True}}), encoding="utf-8"
    )
    return config_path


def _register_fake_exchange(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    auth_error: Exception | None = None,
    reachable_error: Exception | None = None,
) -> list[str]:
    """Đăng ký một class sàn giả tên `name` thẳng vào module `ccxt` thật.

    Trả về danh sách `calls` — test đọc lại để xác nhận CHÍNH XÁC sàn nào
    đã bị gọi (đối chứng cho tiêu chí #3: không hardcode sàn)."""
    calls: list[str] = []

    class _FakeExchange:
        def __init__(self, params: dict[str, Any]) -> None:
            calls.append(name)
            self.params = params

        def set_sandbox_mode(self, flag: bool) -> None:
            pass

        def fetch_time(self) -> int:
            if reachable_error is not None:
                raise reachable_error
            return int(time.time() * 1000)

        def fetch_balance(self) -> dict:
            if auth_error is not None:
                raise auth_error
            return {}

    monkeypatch.setattr(ccxt, name, _FakeExchange, raising=False)
    return calls


# ----------------------------------------------------------------------
# 1. Thiếu key -> FAIL, không gọi ra sàn
# ----------------------------------------------------------------------


def test_missing_api_key_fails_without_calling_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, "fakeexch_missing_key")
    calls = _register_fake_exchange(monkeypatch, "fakeexch_missing_key")
    # Cố tình KHÔNG set EXCHANGE_API_KEY/SECRET (autouse fixture đã xoá sạch).

    result = health_check.check_exchange_authenticated(config_path)

    assert result.status == "FAIL"
    assert "EXCHANGE_API_KEY" in result.detail
    assert calls == [], "thiếu key thì không có gì để xác thực — không được gọi ra sàn"


def test_missing_only_secret_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Có key nhưng thiếu secret — vẫn phải FAIL, không được chạy nửa vời,
    và thông báo phải nêu ĐÚNG biến còn thiếu (EXCHANGE_API_SECRET), không
    liệt kê cả EXCHANGE_API_KEY (biến đó đã có)."""
    monkeypatch.setenv("EXCHANGE_API_KEY", "some_key")

    result = health_check.check_exchange_authenticated(Path("config/settings.yaml"))

    assert result.status == "FAIL"
    assert "EXCHANGE_API_SECRET" in result.detail
    assert "EXCHANGE_API_KEY" not in result.detail, (
        "EXCHANGE_API_KEY đã có giá trị — không được liệt kê nó là biến thiếu"
    )


def test_missing_only_key_names_only_that_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đối xứng với test trên — thiếu key, có secret, thông báo chỉ nêu
    EXCHANGE_API_KEY."""
    monkeypatch.setenv("EXCHANGE_API_SECRET", "some_secret")

    result = health_check.check_exchange_authenticated(Path("config/settings.yaml"))

    assert result.status == "FAIL"
    assert "EXCHANGE_API_KEY" in result.detail
    assert "EXCHANGE_API_SECRET" not in result.detail


def test_bybit_env_vars_no_longer_work_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chốt lại việc bỏ fallback: set CHỈ BYBIT_API_KEY/BYBIT_API_SECRET
    (không set EXCHANGE_*) phải vẫn FAIL — nếu ai lỡ thêm lại fallback,
    test này phải bắt được ngay."""
    monkeypatch.setenv("BYBIT_API_KEY", "old_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "old_secret")

    result = health_check.check_exchange_authenticated(Path("config/settings.yaml"))

    assert result.status == "FAIL"
    assert "EXCHANGE_API_KEY" in result.detail
    assert "EXCHANGE_API_SECRET" in result.detail


def test_bybit_testnet_env_var_no_longer_affects_is_testnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """BYBIT_TESTNET=false không còn được đọc — _is_testnet() phải trả về
    mặc định (True), không bị điều khiển bởi tên biến cũ."""
    monkeypatch.setenv("BYBIT_TESTNET", "false")

    assert health_check._is_testnet() is True


# ----------------------------------------------------------------------
# 2. Key sai -> FAIL, lỗi không bị nuốt
# ----------------------------------------------------------------------


def test_wrong_key_fails_and_error_message_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, "fakeexch_wrong_key")
    _register_fake_exchange(
        monkeypatch,
        "fakeexch_wrong_key",
        auth_error=ccxt.AuthenticationError("Invalid API-key, IP, or permissions for action."),
    )
    monkeypatch.setenv("EXCHANGE_API_KEY", "wrong")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "wrong")

    result = health_check.check_exchange_authenticated(config_path)

    assert result.status == "FAIL"
    assert "Invalid API-key, IP, or permissions for action." in result.detail, (
        "thông điệp lỗi thật của sàn phải xuất hiện trong detail — nuốt lỗi "
        "thành một câu chung chung là mất thông tin cần để chẩn đoán"
    )


def test_unexpected_non_auth_exception_also_fails_not_silently_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except Exception` bọc ngoài `except ccxt.AuthenticationError` phải
    vẫn trả FAIL cho MỌI lỗi khác lúc gọi fetch_balance — không được để lọt
    qua thành OK chỉ vì không đúng type AuthenticationError."""
    config_path = _write_config(tmp_path, "fakeexch_other_error")
    _register_fake_exchange(
        monkeypatch, "fakeexch_other_error", auth_error=RuntimeError("kết nối bị reset giữa chừng")
    )
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")

    result = health_check.check_exchange_authenticated(config_path)

    assert result.status == "FAIL"
    assert "kết nối bị reset giữa chừng" in result.detail


# ----------------------------------------------------------------------
# 3. exchange.name đổi -> check nhắm đúng sàn mới, không hardcode
# ----------------------------------------------------------------------


def test_reachable_check_targets_exchange_name_from_config_not_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls_a = _register_fake_exchange(monkeypatch, "fakeexch_a")
    calls_b = _register_fake_exchange(monkeypatch, "fakeexch_b")
    config_path = _write_config(tmp_path, "fakeexch_b")

    result = health_check.check_exchange_reachable(config_path)

    assert result.status == "OK"
    assert calls_a == [], "sàn KHÔNG được cấu hình mà vẫn bị gọi — hardcode sai sàn"
    assert calls_b == ["fakeexch_b"]
    assert "fakeexch_b" in result.detail


def test_authenticated_check_targets_exchange_name_from_config_not_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls_a = _register_fake_exchange(monkeypatch, "fakeexch_c")
    calls_b = _register_fake_exchange(monkeypatch, "fakeexch_d")
    config_path = _write_config(tmp_path, "fakeexch_d")
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")

    result = health_check.check_exchange_authenticated(config_path)

    assert result.status == "OK"
    assert calls_a == [], "sàn KHÔNG được cấu hình mà vẫn bị gọi — hardcode sai sàn"
    assert calls_b == ["fakeexch_d"]


def test_switching_exchange_name_switches_which_fake_gets_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đổi settings.yaml giữa hai lần gọi (đúng kịch bản đổi sàn Bybit ->
    Binance thật đã xảy ra) phải đổi đúng sàn bị gọi ở lần sau, không dính
    lại sàn cũ do cache/biến toàn cục nào đó."""
    calls_old = _register_fake_exchange(monkeypatch, "fakeexch_old")
    calls_new = _register_fake_exchange(monkeypatch, "fakeexch_new")

    config_old = _write_config(tmp_path, "fakeexch_old")
    health_check.check_exchange_reachable(config_old)
    assert calls_old == ["fakeexch_old"]
    assert calls_new == []

    config_new = _write_config(tmp_path, "fakeexch_new")
    health_check.check_exchange_reachable(config_new)
    assert calls_new == ["fakeexch_new"]
    assert calls_old == ["fakeexch_old"], "lần gọi sau không được gọi lại sàn cũ"


def test_unsupported_exchange_name_fails_with_clear_message(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "khong_ton_tai_trong_ccxt")
    result = health_check.check_exchange_reachable(config_path)
    assert result.status == "FAIL"
    assert "khong_ton_tai_trong_ccxt" in result.detail


# ----------------------------------------------------------------------
# 4. reachable OK + authenticated FAIL -> tổng thể FAIL, không xanh nhờ đa số
# ----------------------------------------------------------------------


def test_reachable_ok_but_authenticated_fail_are_independent_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hai check tách riêng có chủ đích (xem ops/RUNBOOK.md) — reachable OK
    không được lây lan thành authenticated OK."""
    config_path = _write_config(tmp_path, "fakeexch_split")
    _register_fake_exchange(
        monkeypatch, "fakeexch_split", auth_error=ccxt.AuthenticationError("bad key")
    )
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")

    reachable = health_check.check_exchange_reachable(config_path)
    authenticated = health_check.check_exchange_authenticated(config_path)

    assert reachable.status == "OK"
    assert authenticated.status == "FAIL"


def test_main_exits_nonzero_when_only_authenticated_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đây là tiêu chí quan trọng nhất: 5 check khác đều OK, CHỈ
    exchange_authenticated FAIL — main() phải thoát mã khác 0. Một health
    check báo "OK tổng thể" chỉ vì đa số check xanh là vô dụng: đúng lúc
    key hỏng lại là đúng lúc cần FAIL rõ ràng nhất."""
    mostly_green = [
        health_check.CheckResult("config", "OK", "hợp lệ"),
        health_check.CheckResult("exchange_reachable", "OK", "phản hồi 120ms"),
        health_check.CheckResult("exchange_authenticated", "FAIL", "key sai"),
        health_check.CheckResult("hmm_model", "OK", "load được"),
        health_check.CheckResult("disk_space", "OK", "còn nhiều"),
        health_check.CheckResult("log_dir_writable", "OK", "ghi được"),
    ]
    monkeypatch.setattr(health_check, "run_all", lambda: mostly_green)

    exit_code = health_check.main()

    assert exit_code != 0, "4/5 (hay 5/6) OK không được che mất 1 FAIL"


def test_main_exits_zero_only_when_nothing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đối chứng dương cho test trên — không phải để chứng minh "mọi thứ
    OK thì OK" (hiển nhiên), mà để xác nhận không có logic ẩn nào luôn trả
    khác 0 (nếu test trên PASS chỉ vì main() luôn thoát khác 0, test này
    sẽ bắt được)."""
    all_green = [
        health_check.CheckResult("config", "OK", "hợp lệ"),
        health_check.CheckResult("exchange_reachable", "OK", "phản hồi 120ms"),
        health_check.CheckResult("exchange_authenticated", "OK", "xác thực OK"),
    ]
    monkeypatch.setattr(health_check, "run_all", lambda: all_green)

    assert health_check.main() == 0


def test_main_exits_zero_when_only_warn_no_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """WARN không được nghiêm trọng hoá thành FAIL (đúng thiết kế: WARN in
    ra nhưng không chặn khởi động) — đối chứng để tách rõ WARN với FAIL,
    không lẫn hai khái niệm khi tính tổng thể."""
    warn_only = [
        health_check.CheckResult("config", "WARN", "exchange.testnet != true"),
        health_check.CheckResult("exchange_reachable", "OK", "phản hồi 120ms"),
    ]
    monkeypatch.setattr(health_check, "run_all", lambda: warn_only)

    assert health_check.main() == 0
