"""ops.health_check — kiểm tra sẵn sàng trước khi vào vòng lặp chính.

Chạy độc lập (`python ops/health_check.py`), dùng bởi:
  - `ops/entrypoint.sh` — chạy trước khi exec lệnh chính, fail loud thay vì
    để main.py tự khám phá config sai/model thiếu/đĩa đầy giữa vòng lặp sống.
  - `Dockerfile HEALTHCHECK` — Docker gọi định kỳ, container "unhealthy"
    nếu script này thoát khác 0.

Cố tình chỉ import từ `core/` đúng một chỗ (`core.hmm_engine.HMMRegimeEngine`,
chỉ để load-thử model, không train/suy luận) — health check phải tự đứng
vững được, không phụ thuộc phần còn lại của app có đang hỏng hay không.
Không bao giờ log giá trị API key/secret, kể cả một phần (CLAUDE.md bất
biến #6).

Mỗi kiểm tra trả về STATUS: OK, WARN (in ra nhưng không chặn khởi động),
hoặc FAIL (làm script thoát mã 1). Xem `ops/RUNBOOK.md` để biết ý nghĩa
từng mục và cách xử lý khi FAIL.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Status = Literal["OK", "WARN", "FAIL"]

_REQUIRED_SETTINGS_SECTIONS = (
    "exchange",
    "costs",
    "hmm",
    "features",
    "trend_gate",
    "strategy",
    "risk",
    "backtest",
)

# Ngưỡng dung lượng đĩa — model HMM + log xoay vòng (10MB x nhiều file, xem
# monitoring/logger.py) không cần nhiều, nhưng đĩa đầy làm log/state_snapshot
# ghi lỗi âm thầm nếu không kiểm tra trước.
_DISK_FAIL_FREE_MB = 500
_DISK_WARN_FREE_MB = 2000

_EXCHANGE_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def check_config(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("config", "FAIL", f"Không tìm thấy {config_path}")
    try:
        with config_path.open(encoding="utf-8") as fh:
            settings = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return CheckResult("config", "FAIL", f"{config_path} không parse được YAML: {exc}")

    if not isinstance(settings, dict):
        return CheckResult("config", "FAIL", f"{config_path} không phải mapping ở gốc")

    missing = [s for s in _REQUIRED_SETTINGS_SECTIONS if s not in settings]
    if missing:
        return CheckResult("config", "FAIL", f"{config_path} thiếu mục: {missing}")

    testnet = settings.get("exchange", {}).get("testnet")
    if testnet is not True:
        # Không FAIL cứng — có thể đang cố ý chạy mainnet đã xác nhận đầy đủ —
        # nhưng PHẢI hiện rõ ràng, không âm thầm bỏ qua (CLAUDE.md bất biến #6).
        return CheckResult("config", "WARN", f"{config_path}: exchange.testnet != true — ĐANG NHẮM MAINNET")
    return CheckResult("config", "OK", f"{config_path} hợp lệ, testnet=true")


def _is_testnet() -> bool:
    # Đổi tên 2026-08-06 (BYBIT_TESTNET -> EXCHANGE_TESTNET) cùng đợt đổi
    # sàn Bybit -> Binance qua ccxt — tên biến không còn buộc vào một sàn
    # cụ thể, đúng tinh thần `exchange.name` đọc từ settings.yaml (xem
    # _exchange_id() bên dưới). Đọc cả tên cũ làm fallback: .env có sẵn từ
    # trước migration vẫn hoạt động cho tới khi người vận hành cập nhật.
    value = os.environ.get("EXCHANGE_TESTNET", os.environ.get("BYBIT_TESTNET", "true"))
    return value.strip().lower() != "false"


def _exchange_id(config_path: Path) -> str:
    """`exchange.name` từ settings.yaml (vd. "binance") — KHÔNG hardcode
    một sàn cụ thể ở đây, cùng nguyên tắc `broker/ccxt_client.py::CCXTClient`.
    Mặc định "binance" nếu đọc config lỗi — `check_config()` đã báo lỗi
    config riêng, check này chỉ cần một giá trị hợp lý để không crash."""
    try:
        with config_path.open(encoding="utf-8") as fh:
            settings = yaml.safe_load(fh)
        name = settings.get("exchange", {}).get("name")
        return str(name) if name else "binance"
    except (OSError, yaml.YAMLError, AttributeError):
        return "binance"


def check_exchange_reachable(config_path: Path) -> CheckResult:
    """Ping public endpoint (fetch_time) — KHÔNG cần API key/secret, không
    bao giờ log giá trị credential dù có sẵn trong env. Chỉ kiểm tra kết
    nối mạng + API sàn còn sống — KHÔNG xác thực được key, xem
    `check_exchange_authenticated` cho việc đó. Hai check tách riêng có
    chủ đích: "reachable" OK không có nghĩa "đặt lệnh được" — xem
    ops/RUNBOOK.md.
    """
    try:
        import ccxt
    except ImportError:
        return CheckResult("exchange_reachable", "FAIL", "Thiếu thư viện ccxt")

    exchange_id = _exchange_id(config_path)
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        return CheckResult("exchange_reachable", "FAIL", f"ccxt không hỗ trợ sàn {exchange_id!r}")

    testnet = _is_testnet()
    label = "testnet" if testnet else "MAINNET"
    try:
        exchange = exchange_class({"enableRateLimit": True, "timeout": _EXCHANGE_TIMEOUT_MS})
        if testnet:
            exchange.set_sandbox_mode(True)
        t0 = time.monotonic()
        server_time_ms = exchange.fetch_time()
        latency_ms = (time.monotonic() - t0) * 1000
    except Exception as exc:  # ccxt raise nhiều loại lỗi khác nhau (network, exchange, auth)
        return CheckResult(
            "exchange_reachable", "FAIL", f"Không kết nối được {exchange_id} ({label}): {exc}"
        )

    local_ms = time.time() * 1000
    drift_ms = abs(local_ms - server_time_ms)
    if drift_ms > 1000:
        # Ngưỡng đúng spec §6.3 — "cảnh báo nếu lệch > 1 giây", nguyên nhân
        # số 1 gây lỗi auth với sàn. WARN chứ không FAIL — chưa chắc chặn
        # được kết nối, nhưng phải hiện ra để xử lý trước khi lệch nặng hơn.
        return CheckResult(
            "exchange_reachable",
            "WARN",
            f"{exchange_id} {label} phản hồi sau {latency_ms:.0f}ms nhưng lệch đồng hồ "
            f"{drift_ms:.0f}ms > 1000ms (xem §6.3)",
        )
    return CheckResult("exchange_reachable", "OK", f"{exchange_id} {label} phản hồi sau {latency_ms:.0f}ms")


def check_exchange_authenticated(config_path: Path) -> CheckResult:
    """Một request CẦN xác thực (`fetch_balance` — nhẹ nhất có sẵn qua
    ccxt, không đặt lệnh, không thay đổi trạng thái tài khoản).

    Đây là check phát hiện chế độ hỏng phổ biến nhất khi vận hành thật:
    key hết hạn, bị revoke, thiếu quyền, hoặc dán nhầm key MAINNET vào môi
    trường testnet (hoặc ngược lại) — tất cả đều để `check_exchange_reachable`
    PASS bình thường (đó là endpoint public) trong khi bot không thể đặt
    lệnh được. Không bao giờ log giá trị `api_key`/`api_secret`, chỉ dùng
    để dựng client — CLAUDE.md bất biến #6.
    """
    try:
        import ccxt
    except ImportError:
        return CheckResult("exchange_authenticated", "FAIL", "Thiếu thư viện ccxt")

    exchange_id = _exchange_id(config_path)
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        return CheckResult("exchange_authenticated", "FAIL", f"ccxt không hỗ trợ sàn {exchange_id!r}")

    # Đổi tên 2026-08-06 (BYBIT_API_KEY/SECRET -> EXCHANGE_API_KEY/SECRET)
    # cùng đợt đổi sàn — xem _is_testnet(). Fallback tên cũ cho .env có
    # sẵn từ trước migration.
    api_key = os.environ.get("EXCHANGE_API_KEY", os.environ.get("BYBIT_API_KEY", ""))
    api_secret = os.environ.get("EXCHANGE_API_SECRET", os.environ.get("BYBIT_API_SECRET", ""))
    if not api_key or not api_secret:
        return CheckResult(
            "exchange_authenticated",
            "FAIL",
            "Thiếu EXCHANGE_API_KEY/EXCHANGE_API_SECRET trong env — không xác thực được (không log giá trị)",
        )

    testnet = _is_testnet()
    label = "testnet" if testnet else "MAINNET"
    try:
        exchange = exchange_class(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": _EXCHANGE_TIMEOUT_MS,
            }
        )
        if testnet:
            exchange.set_sandbox_mode(True)
        exchange.fetch_balance()
    except ccxt.AuthenticationError as exc:
        # Thông điệp lỗi ccxt không chứa credential (chỉ mã lỗi/thông điệp
        # gốc của sàn, dạng "API key is invalid." — đã xác nhận bằng gọi
        # thật lúc viết check này với Bybit, cùng cơ chế cho mọi sàn ccxt
        # hỗ trợ), an toàn để in ra.
        return CheckResult(
            "exchange_authenticated",
            "FAIL",
            f"Xác thực {exchange_id} {label} THẤT BẠI — key sai/hết hạn/bị revoke/sai môi trường "
            f"testnet-mainnet (Brain-Crypto-Bybit.md §6.3): {exc}",
        )
    except Exception as exc:
        return CheckResult("exchange_authenticated", "FAIL", f"Lỗi khi xác thực {exchange_id} {label}: {exc}")

    return CheckResult("exchange_authenticated", "OK", f"Xác thực {exchange_id} {label} thành công")


def check_hmm_model(model_path: Path, *, required: bool) -> CheckResult:
    missing_status: Status = "FAIL" if required else "WARN"
    if not model_path.exists():
        return CheckResult(
            "hmm_model",
            missing_status,
            f"Không tìm thấy {model_path} — bình thường trước khi Phase 10 (main loop) "
            "train lần đầu, xem ops/RUNBOOK.md",
        )
    if model_path.stat().st_size == 0:
        return CheckResult("hmm_model", "FAIL", f"{model_path} tồn tại nhưng rỗng (0 byte)")

    try:
        from core.hmm_engine import HMMRegimeEngine

        engine = HMMRegimeEngine(
            n_candidates=[3, 4, 5, 6, 7],
            n_init=1,
            covariance_type="full",
            min_train_bars=1,
            stability_bars=3,
            flicker_window=20,
            flicker_threshold=4,
        )
        engine.load(str(model_path))
    except Exception as exc:
        return CheckResult("hmm_model", "FAIL", f"{model_path} tồn tại nhưng load lỗi (file hỏng?): {exc}")

    return CheckResult("hmm_model", "OK", f"{model_path} load được, train lúc {engine.training_date}")


def check_disk_space(path: Path) -> CheckResult:
    probe_path = path if path.exists() else (path.parent if path.parent.exists() else Path("/"))
    usage = shutil.disk_usage(probe_path)
    free_mb = usage.free / (1024 * 1024)

    if free_mb < _DISK_FAIL_FREE_MB:
        return CheckResult(
            "disk_space", "FAIL", f"Chỉ còn {free_mb:.0f}MB trống tại {probe_path} (< {_DISK_FAIL_FREE_MB}MB)"
        )
    if free_mb < _DISK_WARN_FREE_MB:
        return CheckResult(
            "disk_space", "WARN", f"Còn {free_mb:.0f}MB trống tại {probe_path} (< {_DISK_WARN_FREE_MB}MB)"
        )
    return CheckResult("disk_space", "OK", f"Còn {free_mb:.0f}MB trống tại {probe_path}")


def check_log_dir_writable(log_dir: Path) -> CheckResult:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".health_check_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("log_dir_writable", "FAIL", f"Không ghi được vào {log_dir}: {exc}")
    return CheckResult("log_dir_writable", "OK", f"{log_dir} ghi được")


def run_all() -> list[CheckResult]:
    config_path = Path(os.environ.get("CONFIG_PATH", "config/settings.yaml"))
    model_path = Path(os.environ.get("MODEL_PATH", "models/hmm_model.pkl"))
    log_dir = Path(os.environ.get("LOG_DIR", "logs"))
    require_model = os.environ.get("REQUIRE_HMM_MODEL", "true").strip().lower() != "false"

    return [
        check_config(config_path),
        check_exchange_reachable(config_path),
        check_exchange_authenticated(config_path),
        check_hmm_model(model_path, required=require_model),
        check_disk_space(log_dir),
        check_log_dir_writable(log_dir),
    ]


def main() -> int:
    results = run_all()

    exit_code = 0
    for r in results:
        print(f"[{r.status:<4}] {r.name}: {r.detail}")
        if r.status == "FAIL":
            exit_code = 1

    print(f"health_check: {'OK' if exit_code == 0 else 'FAIL'} ({len(results)} kiểm tra)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
