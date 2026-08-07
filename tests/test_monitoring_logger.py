"""tests.test_monitoring_logger — monitoring/logger.py: JSON structured,
rotating file, idempotent get_logger(), Decimal ghi đúng dưới dạng str()."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from broker.base import Position
from monitoring.logger import get_logger, log_state


def test_get_logger_creates_named_log_file(tmp_path: Path) -> None:
    logger = get_logger("main", str(tmp_path))
    logger.info("hello")
    for handler in logger.handlers:
        handler.flush()

    log_file = tmp_path / "main.log"
    assert log_file.exists()


def test_get_logger_is_idempotent_no_duplicate_handlers(tmp_path: Path) -> None:
    """Gọi get_logger() hai lần với cùng (name, log_dir) KHÔNG được cộng
    dồn handler — bug kinh điển làm mỗi dòng log bị ghi lặp N lần."""
    logger1 = get_logger("main", str(tmp_path))
    logger2 = get_logger("main", str(tmp_path))

    assert logger1 is logger2
    assert len(logger1.handlers) == 1


def test_get_logger_different_names_are_independent_files(tmp_path: Path) -> None:
    main_logger = get_logger("main", str(tmp_path))
    trades_logger = get_logger("trades", str(tmp_path))

    main_logger.info("main entry")
    trades_logger.info("trades entry")
    for h in main_logger.handlers + trades_logger.handlers:
        h.flush()

    assert (tmp_path / "main.log").exists()
    assert (tmp_path / "trades.log").exists()
    assert "main entry" in (tmp_path / "main.log").read_text(encoding="utf-8")
    assert "trades entry" in (tmp_path / "trades.log").read_text(encoding="utf-8")


def test_log_lines_are_valid_json(tmp_path: Path) -> None:
    logger = get_logger("regime_json_test", str(tmp_path))
    logger.info("plain message", extra={"custom_field": "abc"})
    for h in logger.handlers:
        h.flush()

    lines = (tmp_path / "regime_json_test.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "plain message"
    assert payload["custom_field"] == "abc"
    assert payload["level"] == "INFO"
    assert "timestamp" in payload


def test_log_state_writes_all_required_fields(tmp_path: Path) -> None:
    logger = get_logger("regime_state_test", str(tmp_path))
    positions = {
        "BTCUSDT": Position("BTCUSDT", Decimal("0.5"), Decimal("50000"), Decimal("51000"), Decimal("500"))
    }

    log_state(
        logger,
        regime="WEAK_BULL",
        probability=0.72,
        equity=Decimal("10523.45"),
        positions=positions,
        daily_pnl=Decimal("34.12"),
        cumulative_fees_paid=Decimal("12.5"),
    )
    for h in logger.handlers:
        h.flush()

    line = (tmp_path / "regime_state_test.log").read_text(encoding="utf-8").strip()
    payload = json.loads(line)

    assert payload["regime"] == "WEAK_BULL"
    assert payload["probability"] == 0.72
    # Decimal PHẢI ghi dưới dạng str() giữ nguyên độ chính xác — không phải
    # float() (CLAUDE.md bất biến #3, áp dụng cả ở đường audit/log).
    assert payload["equity"] == "10523.45"
    assert payload["daily_pnl"] == "34.12"
    assert payload["cumulative_fees_paid"] == "12.5"
    assert payload["positions"]["BTCUSDT"]["qty"] == "0.5"
    assert "timestamp" in payload


def test_log_state_precision_not_lost_to_float(tmp_path: Path) -> None:
    """Đột biến kiểm chứng (CLAUDE.md #16): một giá trị Decimal mà float()
    làm tròn sai (0.1 + 0.2 dạng kinh điển) phải sống sót nguyên vẹn qua
    log_state() -> file -> json.loads()."""
    logger = get_logger("precision_test", str(tmp_path))
    tricky = Decimal("10523.123456789012345")

    log_state(
        logger,
        regime="X",
        probability=0.5,
        equity=tricky,
        positions={},
        daily_pnl=Decimal("0"),
        cumulative_fees_paid=Decimal("0"),
    )
    for h in logger.handlers:
        h.flush()

    payload = json.loads((tmp_path / "precision_test.log").read_text(encoding="utf-8").strip())
    assert payload["equity"] == str(tricky)
    assert payload["equity"] != str(float(tricky))


def test_get_logger_does_not_propagate_to_root(tmp_path: Path, caplog: object) -> None:
    """propagate=False — không được in trùng ra console qua root logger's
    basicConfig (xem docstring get_logger())."""
    logger = get_logger("no_propagate_test", str(tmp_path))
    assert logger.propagate is False


def test_get_logger_creates_log_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "logs"
    assert not nested.exists()
    logger = get_logger("main", str(nested))
    logger.info("x")
    for h in logger.handlers:
        h.flush()
    assert (nested / "main.log").exists()
