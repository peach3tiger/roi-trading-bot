"""`run_live_loop()` — phần NỐI DÂY bên trong vòng lặp poll.

Khoảng trống mà file này lấp: `tests/test_nine_bug_fixes.py` gọi tay
`process_one_bar()` theo đúng chuỗi mà vòng lặp gọi — tức là kiểm một BẢN
SAO của logic, không phải logic thật. Nếu `run_live_loop()` quên truyền
`execute=is_latest`, hoặc lặp sai danh sách bar, hoặc gọi
`_pending_bar_dates` với đối số sai, không test nào cũ bắt được.

Ở đây `run_live_loop()` chạy THẬT, 3 vòng, qua `max_iterations=3`. Chỉ
BIÊN CHẠM RA NGOÀI bị giả lập (sàn, tải lịch sử, health check, đồng hồ,
alert) — toàn bộ đường `_pending_bar_dates` -> `process_one_bar(execute=)`
là code thật.

Kịch bản, dựng quanh một mốc `LAST` cố định:
  vòng 1 — 3 bar chưa xử lý: LAST-3, LAST-2 (execute=False), LAST-1 (True)
  vòng 2 — không có bar mới -> sleep, không xử lý bar nào
  vòng 3 — 1 bar mới: LAST (execute=True)
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import main as main_mod
from broker.base import OrderBook
from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import StrategyOrchestrator
from core.risk_manager import RiskManager
from core.signal_generator import SignalGenerator
from core.trend_gate import StructuralTrendGate, TrendGateConfig
from monitoring.alerts import Alert, AlertManager
from tests.test_main_loop import _FakeOrderExecutor, _risk_manager_config

_SYMBOL = "BTCUSDT"

# Warmup của Tầng 1 (z-score 365 + SMA200 + ...) ăn ~741 bar đầu — đo
# thật, không đoán. Cần dư đủ cho `min_train_bars` bên dưới.
_N_BARS = 1200


# ----------------------------------------------------------------------
# Dữ liệu + model, dựng MỘT LẦN cho cả module (train HMM là phần đắt nhất)
# ----------------------------------------------------------------------


def _make_ohlcv(n: int, end: pd.Timestamp) -> pd.DataFrame:
    """Giá đi lên đều, nhiễu thấp — đủ biến động để HMM tách được state,
    nhưng KHÔNG đủ để thủng stop (`price - 3*ATR`) ở 4 bar cuối.

    Vì sao phải kiểm soát điều đó: nếu bar cuối thủng stop,
    `process_one_bar()` đi nhánh THOÁT (đóng vị thế, không sinh signal,
    không phát alert) thay vì nhánh thực thi bình thường — kịch bản test
    sẽ đo nhầm đường. Đã xảy ra ở bản đầu với `sigma=0.012`:
    bar cuối breach, `submit_order` chỉ được gọi 1 lần thay vì 2.
    `test_khong_co_breach_stop_loss_trong_kich_ban` khoá tiền đề này.
    """
    index = pd.date_range(end=end, periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(11)
    steps = rng.normal(0.0010, 0.004, size=n)
    close = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=index)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": rng.uniform(80.0, 120.0, size=n),
            "trade_count": rng.integers(900, 2200, size=n).astype(float),
        },
        index=index,
    )


@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    last = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC") - pd.Timedelta(days=1)
    ohlcv = _make_ohlcv(_N_BARS, last)

    from data.feature_engineering import compute_all_features

    settings = main_mod.load_settings()
    feature_config = main_mod.build_feature_config(settings)
    features = compute_all_features(ohlcv, feature_config)

    engine = HMMRegimeEngine(
        n_candidates=[3],
        n_init=1,
        covariance_type="diag",
        min_train_bars=100,
        stability_bars=1,
        flicker_window=20,
        flicker_threshold=4,
    )
    engine.select_and_train(features)
    return {"ohlcv": ohlcv, "features": features, "engine": engine, "last": last}


# ----------------------------------------------------------------------
# Giả lập BIÊN chạm ra ngoài
# ----------------------------------------------------------------------


class _Balance:
    asset = "USDT"
    total = Decimal("10000")
    available = Decimal("10000")


class _FakeExchange:
    def __init__(self) -> None:
        self.positions: list[Any] = []

    def get_balance(self) -> _Balance:
        return _Balance()

    def get_positions(self) -> list[Any]:
        return list(self.positions)

    def get_instrument_rules(self, symbol: str) -> Any:
        from broker.instrument_rules import InstrumentRules

        return InstrumentRules(
            symbol=symbol,
            base_precision=Decimal("0.000001"),
            quote_precision=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            min_order_qty=Decimal("0.000001"),
            min_order_amt=Decimal("5"),
            max_order_qty=Decimal("100"),
        )

    def get_server_time(self) -> int:
        # Lệch ~0 -> cổng clock_drift_halt_ms lúc khởi động luôn qua.
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def get_orderbook(self, symbol: str) -> OrderBook:
        # `best_bid`/`best_ask` là @property tính từ bids/asks, KHÔNG phải
        # field constructor — truyền nhầm chúng làm `OrderBook(...)` ném
        # TypeError, `_check_spread_and_alert` nuốt lỗi thành
        # DATA_FEED_LOST, và phép kiểm spread không bao giờ chạy.
        return OrderBook(
            symbol=symbol,
            bids=[(Decimal("99.99"), Decimal("1"))],
            asks=[(Decimal("100.01"), Decimal("1"))],
            timestamp=datetime.now(timezone.utc),
        )


class _RecordingAlertManager(AlertManager):
    """`AlertManager` thật (giữ nguyên rate-limit/định dạng), chỉ chặn mọi
    kênh gửi ra ngoài và ghi lại alert đã phát."""

    def __init__(self) -> None:
        super().__init__(rate_limit_seconds=0, console_enabled=False)
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True


class _HealthOK:
    status = "OK"
    detail = "fake"


@pytest.fixture
def wired(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Nối mọi biên giả lập; trả về các đối tượng test cần khẳng định."""
    ohlcv = world["ohlcv"]
    last = world["last"]

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("STATE_DIR", str(state_dir))
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model.pkl"))

    settings = main_mod.load_settings()
    settings["monitoring"] = dict(settings["monitoring"])
    settings["monitoring"]["log_dir"] = str(tmp_path / "logs")

    # --- health check + đồng hồ + sàn ---------------------------------
    import ops.health_check as health

    monkeypatch.setattr(health, "check_exchange_reachable", lambda _c: _HealthOK())
    monkeypatch.setattr(health, "check_exchange_authenticated", lambda _c: _HealthOK())

    exchange = _FakeExchange()
    monkeypatch.setattr(main_mod, "build_exchange_client", lambda *a, **k: exchange)

    # --- tải lịch sử: trả lát cắt theo mốc `end` mà vòng lặp yêu cầu ---
    load_calls: list[pd.Timestamp] = []

    class _FakeLoader:
        def load(self, symbol: str, tf: str, start: Any, end: Any) -> pd.DataFrame:
            end_ts = pd.Timestamp(end)
            load_calls.append(end_ts)
            return ohlcv.loc[:end_ts]

    import data.history_loader as hist

    monkeypatch.setattr(hist, "HistoryLoader", _FakeLoader)

    # --- position tracker ---------------------------------------------
    class _FakeTracker:
        def __init__(self, _client: Any) -> None:
            self.poll_calls = 0

        def reconcile_on_startup(self) -> None:
            pass

        def poll(self) -> None:
            self.poll_calls += 1

    import broker.position_tracker as pt

    monkeypatch.setattr(pt, "PositionTracker", _FakeTracker)

    # --- HMM: dùng model đã train sẵn ở fixture module ------------------
    monkeypatch.setattr(main_mod, "build_hmm_engine", lambda *a, **k: world["engine"])

    # --- order executor + signal generator ------------------------------
    order_executor = _FakeOrderExecutor(exchange_client=exchange)
    monkeypatch.setattr(main_mod, "build_order_executor", lambda *a, **k: order_executor)

    # MỘT instance dùng lại cho mọi lần `run_live_loop()` trong cùng một
    # test — `CircuitBreaker._history` phải cộng dồn qua các lần gọi thì
    # `test_health_khong_goi_circuit_breaker_check` mới đo được gì.
    shared_risk_manager = RiskManager(
        _risk_manager_config(), halt_lock_path=state_dir / "trading_halted.lock"
    )

    def _fake_signal_generator(_settings: Any, hmm_engine: Any, **kwargs: Any) -> SignalGenerator:
        # Risk manager NỚI (max_trades_per_day cao, cửa sổ chống trùng 0)
        # để test đo được đường nối dây, không phải đo cổng risk.
        trend_gate = StructuralTrendGate(
            TrendGateConfig(
                sma_period=10,
                slope_lookback=5,
                buffer_pct=Decimal("2.0"),
                confirm_bars=3,
                cap_bull_structure=Decimal("1.00"),
                cap_transition=Decimal("0.60"),
                cap_bear_structure=Decimal("0.30"),
            )
        )
        orchestrator = StrategyOrchestrator(min_confidence=0.0, rebalance_threshold_pct=Decimal("0"))
        return SignalGenerator(hmm_engine, trend_gate, orchestrator, shared_risk_manager)

    monkeypatch.setattr(main_mod, "build_signal_generator", _fake_signal_generator)

    alert_manager = _RecordingAlertManager()
    monkeypatch.setattr(main_mod, "build_alert_manager", lambda *a, **k: alert_manager)

    # --- đồng hồ vòng lặp: kịch bản mốc bar cho từng VÒNG ---------------
    #
    # Đánh chỉ số bằng SỐ VÒNG ĐÃ XONG (`len(health_writes)`, mỗi vòng ghi
    # health đúng một lần trong `finally`), KHÔNG bằng số lần gọi.
    #
    # Bản cũ đếm số lần gọi và giả định "một lần gọi mỗi vòng, cộng một
    # lần lúc khởi động". Giả định đó vỡ ngay khi một caller thứ hai xuất
    # hiện: `build_health_inputs()` gọi `compute_bars_behind()`, vốn cũng
    # gọi `_latest_closed_bar_date()`. Kịch bản bị tiêu thụ hai lần mỗi
    # vòng và toàn bộ lịch trình trượt đi một nhịp — im lặng, vì mọi test
    # cũ chỉ đếm TỔNG chứ không đối chiếu vòng nào làm gì.
    #
    # Trong vận hành thật hàm này THUẦN theo `now`, gọi bao nhiêu lần cũng
    # cho cùng kết quả; chỉ bản giả lập theo kịch bản mới nhạy cảm với số
    # lần gọi. Đếm theo vòng khôi phục đúng tính chất đó.
    health_writes: list[dict[str, Any]] = []
    scripted = [
        last - pd.Timedelta(days=1),  # vòng 1: pending = LAST-3, LAST-2, LAST-1
        last - pd.Timedelta(days=1),  # vòng 2: không có bar mới -> sleep
        last,  # vòng 3: 1 bar mới
    ]

    def _scripted_latest_bar(_now: datetime) -> pd.Timestamp:
        return scripted[min(len(health_writes), len(scripted) - 1)]

    monkeypatch.setattr(main_mod, "_latest_closed_bar_date", _scripted_latest_bar)

    # --- ghi lại (bar, execute) + alert phát ra ở TỪNG bar --------------
    real_process = main_mod.process_one_bar
    processed: list[dict[str, Any]] = []

    def _recording_process(**kwargs: Any) -> Any:
        before = len(alert_manager.sent)
        result = real_process(**kwargs)
        processed.append(
            {
                "bar": kwargs["bar_ts"],
                "execute": kwargs["execute"],
                "alerts": len(alert_manager.sent) - before,
            }
        )
        return result

    monkeypatch.setattr(main_mod, "process_one_bar", _recording_process)

    # --- state khôi phục: đã xử lý tới LAST-4 -> vòng 1 có 3 bar --------
    #
    # `current_regime_id`/`current_trend_structure` đặt SENTINEL, không để
    # None: `_fire_bar_alerts()` chỉ phát REGIME_CHANGE/TREND_GATE_CHANGE
    # khi giá trị MỚI khác giá trị đang mang trong state, và bỏ qua hoàn
    # toàn khi state đang là None. Để None thì bar bị lỡ ĐẦU TIÊN không
    # bao giờ tạo alert — dù guard `and execute` có bị bỏ đi hay không —
    # nên `test_alert_chi_phat_o_bar_duoc_execute` xanh một cách RỖNG
    # NGHĨA. Đo được bằng đột biến: bỏ guard, test vẫn xanh.
    #
    # Sentinel đảm bảo bar bị lỡ đầu tiên LUÔN là một "thay đổi", nên nếu
    # guard biến mất thì alert chắc chắn phát và test chắc chắn đỏ.
    snapshot = state_dir / "state_snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "last_processed_bar": (last - pd.Timedelta(days=4)).date().isoformat(),
                "current_stop_loss": None,
                "current_allocation_pct": "0",
                "current_regime_id": 999,
                "current_regime_label": "SENTINEL_REGIME",
                "current_trend_structure": "SENTINEL_TREND",
                "session_started_at_utc": datetime.now(timezone.utc).isoformat(),
                "written_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    # --- spy `write_health` (Phase 12b §B.1) ---------------------------
    # `run_live_loop()` import nó BÊN TRONG hàm, nên vá ở module nguồn là
    # đủ và không cần biết thứ tự import. Ghi lại số lần gọi + số bản ghi
    # lịch sử breaker TẠI THỜI ĐIỂM mỗi lần ghi — đó là thứ cho phép đo
    # từng vòng poll riêng lẻ mà chỉ cần chạy vòng lặp MỘT lần (chạy hai
    # lần không được: `_FakeOrderExecutor` không có `restore_known_stop`).
    import monitoring.health as health_mod

    real_write_health = health_mod.write_health

    def _spy_write_health(report: Any, path: Any = None) -> Any:
        health_writes.append(
            {
                "status": report.status,
                "breaker_history_len": len(shared_risk_manager.circuit_breaker.get_history()),
            }
        )
        return real_write_health(report, path)

    monkeypatch.setattr(health_mod, "write_health", _spy_write_health)

    args = argparse.Namespace(dry_run=False, live=False, config="config/settings.yaml")

    return {
        "args": args,
        "settings": settings,
        "order_executor": order_executor,
        "alert_manager": alert_manager,
        "processed": processed,
        "sleeps": sleeps,
        "load_calls": load_calls,
        "last": last,
        "state_dir": state_dir,
        "risk_manager": shared_risk_manager,
        "health_writes": health_writes,
    }


# ======================================================================
# Khẳng định
# ======================================================================


def test_ba_vong_poll_xu_ly_dung_bar_va_dung_co_execute(wired: dict[str, Any]) -> None:
    """Khẳng định trung tâm: đúng bar nào được tua, đúng bar nào đặt lệnh."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    last = wired["last"]
    processed = wired["processed"]

    assert [(p["bar"], p["execute"]) for p in processed] == [
        (last - pd.Timedelta(days=3), False),  # vòng 1, bar cũ
        (last - pd.Timedelta(days=2), False),  # vòng 1, bar cũ
        (last - pd.Timedelta(days=1), True),  # vòng 1, bar cuối
        (last, True),  # vòng 3, bar mới
    ], "sai bar được xử lý hoặc sai cờ execute"


def test_khong_co_breach_stop_loss_trong_kich_ban(wired: dict[str, Any]) -> None:
    """TIỀN ĐỀ của mọi test khác trong file.

    Nếu một bar thủng stop, `process_one_bar()` đi nhánh THOÁT (đóng vị
    thế, không sinh signal, không phát alert) — mọi con số dưới đây sẽ đo
    nhầm đường mà vẫn "xanh" một cách vô nghĩa. Test này đỏ nghĩa là dữ
    liệu tổng hợp đã trôi, sửa `_make_ohlcv` chứ đừng nới assertion.
    """
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    assert wired["order_executor"].close_position_calls == []


def test_submit_order_dung_hai_lenh_o_dung_hai_bar_execute(wired: dict[str, Any]) -> None:
    """ĐÚNG 2 lệnh, ở ĐÚNG 2 bar `execute=True` — không hơn, không kém."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    last = wired["last"]
    executed_bars = [p["bar"] for p in wired["processed"] if p["execute"]]
    assert executed_bars == [last - pd.Timedelta(days=1), last]

    ordered_bars = [s.timestamp for s in wired["order_executor"].submit_order_calls]
    assert ordered_bars == [b.to_pydatetime() for b in executed_bars], (
        "số lệnh hoặc bar đặt lệnh không khớp danh sách bar execute"
    )

    # Nói lại bằng cách khác, để đột biến "luôn execute=True" chắc chắn đỏ:
    skipped = {p["bar"].to_pydatetime() for p in wired["processed"] if not p["execute"]}
    assert not (set(ordered_bars) & skipped), (
        "có lệnh đặt theo signal của bar bị lỡ — đúng bug mà execute=False sinh ra để chặn"
    )


def test_alert_chi_phat_o_bar_duoc_execute(wired: dict[str, Any]) -> None:
    """Alert mô tả "đang xảy ra" — dán lên bar quá khứ là sai, và sẽ spam
    đúng bằng số bar bị lỡ mỗi lần bot bật lại sau khi đứng máy."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    skipped = [p for p in wired["processed"] if not p["execute"]]
    assert skipped, "kịch bản phải có bar bị lỡ, nếu không test này vô nghĩa"

    for p in skipped:
        assert p["alerts"] == 0, f"bar bị lỡ {p['bar'].date()} phát {p['alerts']} alert — phải là 0"

    # Không đủ: phải chắc bar bị lỡ ĐÁNG LẼ tạo alert nếu guard biến mất.
    # State khôi phục mang SENTINEL_REGIME/SENTINEL_TREND nên bar bị lỡ đầu
    # tiên luôn là một "thay đổi" — xem chú thích ở fixture `wired`.
    # Không có tiền đề này, assertion trên xanh cả khi guard đã bị bỏ.
    executed_alerts = sum(p["alerts"] for p in wired["processed"] if p["execute"])
    assert executed_alerts > 0, (
        "không alert nào phát ở bar execute — cơ chế alert đang tắt, assertion phía trên sẽ xanh rỗng nghĩa"
    )


def test_vong_khong_co_bar_moi_thi_sleep_va_khong_xu_ly(wired: dict[str, Any]) -> None:
    """Vòng 2: `latest_bar <= last_processed` -> sleep, không bar nào."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    # 4 bar xử lý ở vòng 1 và 3; vòng 2 không thêm bar nào.
    assert len(wired["processed"]) == 4
    assert wired["sleeps"], "vòng không có bar mới phải sleep, không quay tít"


def test_max_iterations_dung_dung_so_vong(wired: dict[str, Any]) -> None:
    """`max_iterations` phải đếm MỌI vòng, kể cả vòng thoát bằng `continue`.

    `load()` được gọi đúng một lần mỗi vòng CÓ tải dữ liệu — vòng 2 thoát
    trước khi tải, nên 3 vòng cho 2 lần tải (chưa kể lần tải lúc khởi động).
    """
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    # 1 lần khởi động + vòng 1 + vòng 3 (vòng 2 thoát sớm, không tải).
    assert len(wired["load_calls"]) == 3


def test_max_iterations_khong_cho_thi_khong_doi_hanh_vi() -> None:
    """Mặc định phải là `None` — vận hành thật không được đổi một chút nào."""
    import inspect

    params = inspect.signature(main_mod.run_live_loop).parameters
    assert params["max_iterations"].default is None


def test_max_iterations_bang_khong_thi_khong_chay_vong_nao(wired: dict[str, Any]) -> None:
    """Biên: 0 vòng — khởi động xong rồi thoát ngay, không xử lý bar nào."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=0)

    assert wired["processed"] == []
    assert wired["order_executor"].submit_order_calls == []


def test_state_snapshot_ghi_lai_bar_cuoi(wired: dict[str, Any]) -> None:
    """Sau 3 vòng, snapshot phải trỏ tới bar mới nhất đã xử lý."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    payload = json.loads((wired["state_dir"] / "state_snapshot.json").read_text(encoding="utf-8"))
    assert payload["last_processed_bar"] == wired["last"].date().isoformat()


# ======================================================================
# Phase 12b §B.1 — health.json ghi từ VÒNG LẶP THẬT
#
# `tests/test_health.py` kiểm quy tắc `ok/degraded/down` trên đầu vào dựng
# tay. Nó KHÔNG kiểm được phần nối dây: vòng lặp có gọi `write_health()`
# không, có gọi ở nhánh "không có bar mới" không, ba trần có tới được file
# không. Đó là đúng khoảng trống mà file này tồn tại để lấp.
# ======================================================================


def _health(wired: dict[str, Any]) -> dict[str, Any]:
    return json.loads((wired["state_dir"] / "health.json").read_text(encoding="utf-8"))


def test_health_json_duoc_ghi_sau_vong_lap(wired: dict[str, Any]) -> None:
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    assert (wired["state_dir"] / "health.json").exists()


def test_health_json_co_du_bon_truong_allocation(wired: dict[str, Any]) -> None:
    """Điểm §B.1 nói thiết kế gốc THIẾU. Ba trần phải đi từ
    `SignalGeneratorResult` qua `LiveLoopState` tới file — nếu bất kỳ mắt
    xích nào đứt, chúng về `None` và test này đỏ."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)
    payload = _health(wired)

    for key in ("hmm_allocation", "trend_gate_cap", "risk_manager_cap", "final_allocation"):
        assert payload[key] is not None, key


def test_health_json_khong_vi_pham_bat_bien_2(wired: dict[str, Any]) -> None:
    """Đường dây thật, chạy thật: `final` không được lớn hơn trần nào."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)
    payload = _health(wired)

    caps = [Decimal(payload[k]) for k in ("hmm_allocation", "trend_gate_cap", "risk_manager_cap")]
    assert Decimal(payload["final_allocation"]) == min(caps)
    assert "invariant_violations" not in payload


def test_health_json_ghi_ca_o_vong_khong_co_bar_moi(wired: dict[str, Any]) -> None:
    """Vòng 2 của kịch bản KHÔNG có bar mới — thoát sớm bằng `continue`.
    Đó là trạng thái bình thường nhất của một bot bar-1D, và một
    `health.json` chỉ cập nhật ở nhánh "có bar mới" sẽ nằm im cả ngày rồi
    trông hệt như một tiến trình đã chết.

    Đếm số lần `write_health()` được gọi thay vì so dấu thời gian: 3 vòng
    poll phải cho ĐÚNG 3 lần ghi. Nếu lời gọi nằm ở cuối thân vòng lặp
    thay vì trong `finally`, vòng 2 bị `continue` nhảy qua và con số này
    còn 2.
    """
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    assert len(wired["health_writes"]) == 3


def test_health_json_nam_trong_state_dir(wired: dict[str, Any]) -> None:
    """Cùng thư mục với `state_snapshot.json` — một volume, một đường sao
    lưu, ngoài cây mã nguồn (xem docstring `monitoring/health.py`)."""
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=1)

    assert (wired["state_dir"] / "health.json").exists()
    assert not Path("monitoring/state/health.json").exists()


def test_health_khong_goi_circuit_breaker_check(wired: dict[str, Any]) -> None:
    """`CircuitBreaker.check()` GHI vào `_history` mỗi lần gọi. `health.json`
    ghi mỗi chu kỳ poll (60s trong vận hành), nên gọi `check()` từ đường
    quan sát sẽ bơm ~1440 bản ghi rác/ngày vào lịch sử breaker và lặp lại
    cùng một dòng WARNING mỗi phút — một phép QUAN SÁT không được làm thay
    đổi thứ nó quan sát.

    Đo bằng TỔNG: mỗi bar được xử lý sinh đúng một bản ghi (`validate_signal`
    gọi `check()` một lần), nên lịch sử phải dài đúng bằng số bar đã xử lý.
    Nếu đường health cũng gọi `check()`, con số này lớn hơn đúng bằng số
    vòng poll. So tổng thay vì so từng vòng để khẳng định không phụ thuộc
    vòng nào rảnh — chi tiết đó là của kịch bản, không phải của bất biến.
    """
    main_mod.run_live_loop(wired["args"], wired["settings"], max_iterations=3)

    so_bar = len(wired["processed"])
    so_ban_ghi = len(wired["risk_manager"].circuit_breaker.get_history())

    assert so_ban_ghi == so_bar, (
        f"{so_ban_ghi} bản ghi breaker cho {so_bar} bar đã xử lý — dư "
        f"{so_ban_ghi - so_bar}, đúng bằng số vòng poll nếu đường health đang gọi check()."
    )


def test_read_breaker_level_khong_doi_lich_su() -> None:
    """Đơn vị, tách khỏi vòng lặp: đọc 100 lần không thêm bản ghi nào."""
    rm = RiskManager(_risk_manager_config(), halt_lock_path=Path("/tmp/khong-ton-tai.lock"))
    truoc = len(rm.circuit_breaker.get_history())

    for _ in range(100):
        main_mod.read_breaker_level(rm)

    assert len(rm.circuit_breaker.get_history()) == truoc


def test_read_breaker_level_chua_check_lan_nao_la_none() -> None:
    rm = RiskManager(_risk_manager_config(), halt_lock_path=Path("/tmp/khong-ton-tai.lock"))

    assert main_mod.read_breaker_level(rm) == "NONE"

