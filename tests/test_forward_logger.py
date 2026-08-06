"""tests.test_forward_logger — append-only, backfill, idempotency, no đặt lệnh.

File MỚI, khớp forward/logger.py (forward test ghi log, không đặt lệnh).
Yêu cầu bắt buộc kiểm chứng bằng test (không phải chỉ đọc code):
`forward/log.csv` không bao giờ bị sửa dòng cũ, chạy lại cùng ngày không ghi
trùng, và chạy hằng ngày hay hằng tuần cho cùng một log.csv cuối cùng.
"""

from __future__ import annotations

import inspect
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import forward.logger as fwd

# ----------------------------------------------------------------------
# append_row — CHỈ APPEND
# ----------------------------------------------------------------------


def _sample_row(date_str: str) -> dict[str, object]:
    row = dict.fromkeys(fwd._CSV_FIELDNAMES, "")
    row["date"] = date_str
    row["hmm_retrained"] = False
    return row


def test_append_row_never_rewrites_prior_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    fwd.append_row(_sample_row("2026-01-01"), path=path)
    fwd.append_row(_sample_row("2026-01-02"), path=path)
    fwd.append_row(_sample_row("2026-01-03"), path=path)

    before = path.read_text(encoding="utf-8")
    before_lines = before.splitlines()
    assert len(before_lines) == 4  # header + 3 dòng

    fwd.append_row(_sample_row("2026-01-04"), path=path)
    after_lines = path.read_text(encoding="utf-8").splitlines()

    assert len(after_lines) == 5
    # Mọi dòng đã có trước đó giữ NGUYÊN VĂN — không dòng nào bị viết lại.
    assert after_lines[: len(before_lines)] == before_lines


def test_append_row_writes_header_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    for i in range(1, 4):
        fwd.append_row(_sample_row(f"2026-01-0{i}"), path=path)

    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0]
    assert lines.count(header) == 1


def test_read_existing_log_roundtrips_hmm_retrained_bool(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    row_a = _sample_row("2026-01-01")
    row_a["hmm_retrained"] = True
    row_b = _sample_row("2026-01-02")
    row_b["hmm_retrained"] = False
    fwd.append_row(row_a, path=path)
    fwd.append_row(row_b, path=path)

    df = fwd.read_existing_log(path=path)
    assert df is not None
    assert df["hmm_retrained"].tolist() == [True, False]


# ----------------------------------------------------------------------
# pending_bar_dates — backfill + idempotency, hàm thuần
# ----------------------------------------------------------------------


def _dates(*iso: str) -> list[pd.Timestamp]:
    return [pd.Timestamp(d, tz="UTC") for d in iso]


def test_pending_bar_dates_first_run_returns_only_latest_bar() -> None:
    available = _dates("2026-01-01", "2026-01-02", "2026-01-03")
    result = fwd.pending_bar_dates(None, available)
    # Lần đầu KHÔNG backfill về quá khứ — chỉ đúng 1 bar gần nhất.
    assert result == [available[-1]]


def test_pending_bar_dates_empty_when_already_up_to_date() -> None:
    available = _dates("2026-01-01", "2026-01-02")
    last_logged = pd.Timestamp("2026-01-02")
    assert fwd.pending_bar_dates(last_logged, available) == []


def test_pending_bar_dates_backfills_all_missing_in_order() -> None:
    available = _dates("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")
    last_logged = pd.Timestamp("2026-01-01")
    result = fwd.pending_bar_dates(last_logged, available)
    assert result == available[1:]


def test_pending_bar_dates_first_run_never_backfills_history() -> None:
    """Rule "lần đầu chỉ 1 bar" (không backfill quá khứ) là đặc thù CHỈ áp
    dụng khi log.csv chưa có dòng nào — xem docstring module forward/logger.py.
    """
    available = _dates(*[f"2026-01-{d:02d}" for d in range(1, 8)])
    assert fwd.pending_bar_dates(None, available) == [available[-1]]


def test_pending_bar_dates_daily_vs_weekly_cadence_converge_to_same_log() -> None:
    """SAU KHI thí nghiệm đã bắt đầu (đã có ít nhất 1 dòng log), chạy 'hằng
    ngày' (1 bar mỗi lần gọi) hay 'hằng tuần' (gộp nhiều bar một lần gọi)
    phải cho ra ĐÚNG cùng tập bar được xử lý, theo đúng thứ tự — đây chính
    là tính chất "backfill được" mà requirement 3 đòi hỏi.
    """
    experiment_start = pd.Timestamp("2026-01-01", tz="UTC")  # bar đã log ở lần chạy đầu
    available = _dates(*[f"2026-01-{d:02d}" for d in range(2, 9)])  # 7 bar kế tiếp

    # "Hằng ngày": gọi lại nhiều lần, mỗi lần cập nhật last_logged bằng bar
    # vừa xử lý (mô phỏng ghi vào log.csv rồi đọc lại ở lần chạy sau).
    daily_processed: list[pd.Timestamp] = []
    last_logged = experiment_start
    while True:
        batch = fwd.pending_bar_dates(last_logged, available)
        if not batch:
            break
        daily_processed.append(batch[0])
        last_logged = batch[0]

    # "Hằng tuần": gọi đúng 1 lần, gộp cả 7 bar cùng lúc.
    weekly_processed = fwd.pending_bar_dates(experiment_start, available)

    assert daily_processed == available
    assert weekly_processed == available


# ----------------------------------------------------------------------
# thresholded_target
# ----------------------------------------------------------------------


def test_thresholded_target_holds_below_threshold() -> None:
    result = fwd.thresholded_target(
        raw_target=Decimal("0.60"), current_allocation=Decimal("0.50"), threshold_fraction=Decimal("0.25")
    )
    assert result == Decimal("0.50")  # lệch 0.10 < ngưỡng 0.25 — giữ nguyên


def test_thresholded_target_rebalances_above_threshold() -> None:
    result = fwd.thresholded_target(
        raw_target=Decimal("0.90"), current_allocation=Decimal("0.50"), threshold_fraction=Decimal("0.25")
    )
    assert result == Decimal("0.90")  # lệch 0.40 >= ngưỡng — đổi target


# ----------------------------------------------------------------------
# latest_closed_bar_date
# ----------------------------------------------------------------------


def test_latest_closed_bar_date_excludes_in_progress_bar() -> None:
    now = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)  # giữa ngày 06/08
    result = fwd.latest_closed_bar_date(now)
    assert result == pd.Timestamp("2026-08-05", tz="UTC")  # bar hôm nay chưa đóng


def test_latest_closed_bar_date_is_stable_regardless_of_hour() -> None:
    early = fwd.latest_closed_bar_date(datetime(2026, 8, 6, 0, 1, tzinfo=timezone.utc))
    late = fwd.latest_closed_bar_date(datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc))
    assert early == late == pd.Timestamp("2026-08-05", tz="UTC")


# ----------------------------------------------------------------------
# Không đặt lệnh, không import broker/ — kiểm tra tĩnh trên chính source
# ----------------------------------------------------------------------


def test_no_broker_import() -> None:
    source = inspect.getsource(fwd)
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import broker"), f"import broker/ bị cấm: {line!r}"
        assert not stripped.startswith("from broker"), f"import broker/ bị cấm: {line!r}"


# ----------------------------------------------------------------------
# Integration nhẹ: run_forward_test trên dữ liệu tổng hợp, không mạng
# ----------------------------------------------------------------------


def _make_synthetic_ohlcv(n_bars: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0005, 0.02, n_bars)
    close = 20000 * np.exp(np.cumsum(log_returns))
    high = close * (1 + rng.uniform(0.001, 0.02, n_bars))
    low = close * (1 - rng.uniform(0.001, 0.02, n_bars))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    trade_count = rng.integers(1000, 5000, n_bars)
    index = pd.date_range(fwd.DATA_START, periods=n_bars, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": trade_count * 0.1,
            "trade_count": trade_count,
        },
        index=index,
    )


def _stub_settings() -> dict:
    return {
        "hmm": {
            "n_candidates": [3],
            "n_init": 1,
            "covariance_type": "full",
            "min_train_bars": 40,
            "zscore_lookback": 30,
            "retrain_interval_days": 7,
            "stability_bars": 3,
            "flicker_window": 20,
            "flicker_threshold": 4,
        },
        "features": {"use_trade_count_not_volume": True},
        "trend_gate": {
            "sma_period": 10,
            "slope_lookback": 3,
            "buffer_pct": 2.0,
            "confirm_bars": 2,
            "cap_bull_structure": 1.00,
            "cap_transition": 0.60,
            "cap_bear_structure": 0.30,
        },
        "strategy": {"min_confidence": 0.55, "rebalance_threshold_pct": 25},
        "costs": {"taker_fee_pct": 0.10, "maker_fee_pct": 0.10, "slippage_pct": 0.03, "assume_taker": True},
    }


@pytest.fixture
def _forward_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Trỏ log.csv/warnings.log sang tmp_path, chặn hoàn toàn mạng/config thật."""
    log_path = tmp_path / "log.csv"
    warnings_path = tmp_path / "warnings.log"
    monkeypatch.setattr(fwd, "_LOG_PATH", log_path)
    monkeypatch.setattr(fwd, "_WARNINGS_LOG_PATH", warnings_path)
    monkeypatch.setattr(fwd, "_MODEL_CACHE_PATH", tmp_path / "state" / "hmm_model.pkl")
    monkeypatch.setattr(fwd, "load_frozen_settings", _stub_settings)

    synthetic = _make_synthetic_ohlcv(n_bars=200)

    class _StubLoader:
        def load(self, *args: object, **kwargs: object) -> pd.DataFrame:
            return synthetic

    monkeypatch.setattr(fwd, "HistoryLoader", _StubLoader)
    return log_path, synthetic, warnings_path


def test_run_forward_test_first_run_appends_exactly_one_row(_forward_harness) -> None:
    log_path, synthetic, _warnings_path = _forward_harness
    now = (synthetic.index[160] + pd.Timedelta(days=1)).to_pydatetime()  # bar 160 vừa đóng

    result = fwd.run_forward_test(now=now)

    assert result["appended"] == 1
    df = fwd.read_existing_log(path=log_path)
    assert df is not None and len(df) == 1
    assert df.iloc[0]["date"] == synthetic.index[160].tz_localize(None)


def test_run_forward_test_same_day_rerun_is_idempotent(_forward_harness) -> None:
    log_path, synthetic, _warnings_path = _forward_harness
    now = (synthetic.index[160] + pd.Timedelta(days=1)).to_pydatetime()

    fwd.run_forward_test(now=now)
    content_after_first = log_path.read_text(encoding="utf-8")

    result_second = fwd.run_forward_test(now=now)

    assert result_second["appended"] == 0
    assert log_path.read_text(encoding="utf-8") == content_after_first  # không ghi thêm gì


def test_run_forward_test_backfills_gap_without_touching_prior_rows(_forward_harness) -> None:
    log_path, synthetic, _warnings_path = _forward_harness
    now_day1 = (synthetic.index[160] + pd.Timedelta(days=1)).to_pydatetime()
    fwd.run_forward_test(now=now_day1)
    content_after_day1 = log_path.read_text(encoding="utf-8")
    lines_after_day1 = content_after_day1.splitlines()

    now_day4 = (synthetic.index[163] + pd.Timedelta(days=1)).to_pydatetime()  # máy tắt 3 ngày
    result = fwd.run_forward_test(now=now_day4)

    assert result["appended"] == 3  # bar 161, 162, 163
    lines_after_backfill = log_path.read_text(encoding="utf-8").splitlines()
    assert lines_after_backfill[: len(lines_after_day1)] == lines_after_day1
    assert len(lines_after_backfill) == len(lines_after_day1) + 3

    df = fwd.read_existing_log(path=log_path)
    assert df is not None
    assert len(df) == 4
    # 4 track đều có equity > 0 xuyên suốt — không cash âm, không NaN.
    for prefix in fwd._TRACK_PREFIXES:
        assert (df[f"{prefix}_equity"].astype(float) > 0).all()


# ----------------------------------------------------------------------
# Warnings — KHÔNG filter, chuyển hướng vào forward/warnings.log + đếm
# ----------------------------------------------------------------------


def test_format_warning_lines_includes_bar_date_category_and_message() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("divide by zero encountered in matmul", RuntimeWarning)

    lines = fwd._format_warning_lines(caught, "2026-08-05", now=datetime(2026, 8, 6, tzinfo=timezone.utc))

    assert len(lines) == 1
    parts = lines[0].rstrip("\n").split("\t")
    assert parts[0] == "2026-08-06T00:00:00+00:00"
    assert parts[1] == "2026-08-05"
    assert parts[2] == "RuntimeWarning"
    assert parts[3] == "divide by zero encountered in matmul"


def test_format_warning_lines_empty_when_nothing_caught() -> None:
    assert fwd._format_warning_lines([], "2026-08-05") == []


def test_format_warning_lines_does_not_drop_any_category() -> None:
    """"KHÔNG filter" — mọi warning bắt được đều thành một dòng, bất kể
    category hay nội dung (đối lập với pyproject.toml's pytest
    filterwarnings, vốn CHỦ Ý bỏ qua matmul/convergence warning khi test)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("a", RuntimeWarning)
        warnings.warn("b", UserWarning)
        warnings.warn("c", DeprecationWarning)

    lines = fwd._format_warning_lines(caught, "2026-08-05")
    assert len(lines) == 3


def test_write_warnings_log_never_rewrites_prior_lines(tmp_path: Path) -> None:
    path = tmp_path / "warnings.log"
    fwd._write_warnings_log(["line1\n", "line2\n"], path=path)
    before = path.read_text(encoding="utf-8")

    fwd._write_warnings_log(["line3\n"], path=path)
    after = path.read_text(encoding="utf-8")

    assert after.startswith(before)
    assert after == before + "line3\n"


def test_write_warnings_log_noop_when_no_lines(tmp_path: Path) -> None:
    path = tmp_path / "warnings.log"
    fwd._write_warnings_log([], path=path)
    assert not path.exists()  # không tạo file rỗng khi không có gì để ghi


def test_run_forward_test_counts_and_logs_warnings_per_bar(
    _forward_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ép một warning thật xảy ra đúng 1 lần trong khối xử lý bar (qua
    `compose_layer_allocations`, gọi đúng 1 lần/bar) — kiểm chứng
    `warning_count` trong log.csv khớp, và forward/warnings.log nhận đúng
    dòng, gắn đúng bar date, KHÔNG bị filter mất.
    """
    log_path, synthetic, warnings_path = _forward_harness
    real_compose = fwd.compose_layer_allocations

    def _compose_and_warn(*args: object) -> object:
        warnings.warn("synthetic test warning", RuntimeWarning)
        return real_compose(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(fwd, "compose_layer_allocations", _compose_and_warn)

    now = (synthetic.index[160] + pd.Timedelta(days=1)).to_pydatetime()
    fwd.run_forward_test(now=now)

    df = fwd.read_existing_log(path=log_path)
    assert df is not None
    assert df.iloc[0]["warning_count"] == 1

    assert warnings_path.exists()
    log_lines = warnings_path.read_text(encoding="utf-8").splitlines()
    bar_lines = [ln for ln in log_lines if "\t2026" in ln or synthetic.index[160].strftime("%Y-%m-%d") in ln]
    assert any("synthetic test warning" in ln for ln in bar_lines)
    assert any("RuntimeWarning" in ln for ln in bar_lines)


def test_run_forward_test_zero_warnings_when_none_raised(_forward_harness) -> None:
    log_path, synthetic, warnings_path = _forward_harness
    now = (synthetic.index[160] + pd.Timedelta(days=1)).to_pydatetime()

    fwd.run_forward_test(now=now)

    df = fwd.read_existing_log(path=log_path)
    assert df is not None
    assert int(df.iloc[0]["warning_count"]) == 0
    assert not warnings_path.exists()  # không tạo file khi không có warning nào
