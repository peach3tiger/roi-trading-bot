"""tests.test_monitoring_dashboard — monitoring/dashboard.py: layout đủ 6
panel, "Phí tháng này" luôn hiển thị, màu risk đổi đúng theo ngưỡng."""

from __future__ import annotations

from decimal import Decimal

from monitoring.dashboard import Dashboard, DashboardState, RecentSignal


def _state(**overrides: object) -> DashboardState:
    base = dict(
        regime_label="WEAK_BULL",
        regime_probability=0.72,
        vol_rank="LOW",
        stability_bars=14,
        flicker_count=1,
        flicker_window=20,
        is_confirmed=True,
        equity=Decimal("10523"),
        daily_pnl=Decimal("34"),
        daily_pnl_pct=Decimal("0.32"),
        allocation_pct=Decimal("0.95"),
        position_qty=Decimal("0.098421"),
        cash=Decimal("526"),
        position_direction="LONG",
        position_entry_price=Decimal("104230"),
        position_unrealized_pnl_pct=Decimal("1.2"),
        position_stop_loss=Decimal("98400"),
        position_days_held=3,
        recent_signals=(RecentSignal("00:00 UTC", "Rebalance 60%->95%", "Vol thap, xu huong OK"),),
        daily_dd_pct=Decimal("0.3"),
        daily_dd_limit_pct=Decimal("6"),
        peak_dd_pct=Decimal("1.2"),
        peak_dd_limit_pct=Decimal("20"),
        monthly_fees_paid=Decimal("24"),
        monthly_fees_pct_of_gross=Decimal("8.1"),
        poll_latency_ms=45.0,
        last_poll_at="2026-08-07T00:01:03+00:00",
        bars_behind=0,
        api_latency_ms=89.0,
        clock_drift_ms=34.0,
        hmm_last_trained_days_ago=2,
        is_testnet=True,
    )
    base.update(overrides)
    return DashboardState(**base)  # type: ignore[arg-type]


def test_render_text_contains_all_six_sections() -> None:
    text = Dashboard().render_text(_state())
    for header in ("REGIME", "PORTFOLIO", "VỊ THẾ", "SIGNAL GẦN ĐÂY", "RISK", "HỆ THỐNG"):
        assert header in text


def test_fees_panel_always_visible_even_when_zero() -> None:
    """Nghiệm thu phase-11-monitoring.md: "Phí tháng này" là ô KHÔNG được
    ẩn/rút gọn dù bằng 0 — vẫn phải xuất hiện trong text đã render."""
    text = Dashboard().render_text(
        _state(monthly_fees_paid=Decimal("0"), monthly_fees_pct_of_gross=Decimal("0"))
    )
    assert "Phí tháng này" in text


def test_regime_label_and_probability_shown() -> None:
    text = Dashboard().render_text(_state(regime_label="STRONG_BULL", regime_probability=0.5))
    assert "STRONG_BULL" in text
    assert "50%" in text


def test_flat_position_shows_no_position_open() -> None:
    text = Dashboard().render_text(
        _state(
            position_direction="FLAT",
            position_entry_price=None,
            position_unrealized_pnl_pct=None,
            position_stop_loss=None,
            position_days_held=None,
        )
    )
    assert "FLAT" in text


def test_no_recent_signals_shows_placeholder() -> None:
    text = Dashboard().render_text(_state(recent_signals=()))
    assert "Chưa có signal" in text


def test_testnet_vs_mainnet_label() -> None:
    testnet_text = Dashboard().render_text(_state(is_testnet=True))
    mainnet_text = Dashboard().render_text(_state(is_testnet=False))
    assert "TESTNET" in testnet_text
    assert "MAINNET" in mainnet_text


def test_run_stops_cleanly_on_stop_iteration() -> None:
    """Không cần time.sleep thật/KeyboardInterrupt giả — provider hữu hạn
    tự báo hết bằng StopIteration, run() phải trả về mà không raise."""
    calls = {"n": 0}

    def provider() -> DashboardState:
        calls["n"] += 1
        if calls["n"] > 2:
            raise StopIteration
        return _state()

    dashboard = Dashboard(refresh_interval_seconds=0)
    dashboard.run(provider)  # không raise
    assert calls["n"] == 3


# ----------------------------------------------------------------------
# Màu rủi ro — đột biến kiểm chứng ngưỡng thật sự phân biệt được ba mức
# ----------------------------------------------------------------------


def test_risk_style_ok_below_half_limit() -> None:
    from monitoring.dashboard import _STYLE_OK, _risk_style

    assert _risk_style(Decimal("2"), Decimal("10")) == _STYLE_OK


def test_risk_style_warn_between_half_and_limit() -> None:
    from monitoring.dashboard import _STYLE_WARN, _risk_style

    assert _risk_style(Decimal("6"), Decimal("10")) == _STYLE_WARN


def test_risk_style_danger_at_or_above_limit() -> None:
    from monitoring.dashboard import _STYLE_DANGER, _risk_style

    assert _risk_style(Decimal("10"), Decimal("10")) == _STYLE_DANGER
    assert _risk_style(Decimal("11"), Decimal("10")) == _STYLE_DANGER


# ----------------------------------------------------------------------
# HỆ THỐNG — schema REST polling (không còn ws_connected/ws_last_message_
# seconds_ago, xem monitoring/dashboard.py::DashboardState)
# ----------------------------------------------------------------------


def test_dashboard_state_has_no_websocket_fields() -> None:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(DashboardState)}
    assert "ws_connected" not in field_names
    assert "ws_last_message_seconds_ago" not in field_names
    assert {"poll_latency_ms", "last_poll_at", "bars_behind"} <= field_names


def test_system_panel_shows_poll_latency_and_last_poll_at() -> None:
    text = Dashboard().render_text(_state(poll_latency_ms=123.0, last_poll_at="2026-08-07T00:01:03+00:00"))
    assert "123ms" in text
    assert "2026-08-07T00:01:03+00:00" in text


def test_bars_behind_zero_shows_ok_icon() -> None:
    text = Dashboard().render_text(_state(bars_behind=0))
    assert "Trễ: 0 bar ✅" in text


def test_bars_behind_nonzero_shows_warning_icon() -> None:
    text = Dashboard().render_text(_state(bars_behind=2))
    assert "Trễ: 2 bar ❌" in text


