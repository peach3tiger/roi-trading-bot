"""Phase 12b §B.1/§B.3 — `monitoring/health.py`.

Mỗi điều kiện `down`/`degraded` trong §B.1 có một test RIÊNG, và mỗi test
đổi ĐÚNG MỘT trường so với `_ok_inputs()`. Gộp nhiều điều kiện vào một
test sẽ vẫn xanh khi ba trong bốn nhánh bị xoá — đúng loại lỗi xác minh
mà CLAUDE.md #16 nói là chế độ hỏng chủ đạo của dự án này.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from monitoring import health
from monitoring.alerts import AlertType
from monitoring.health import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_OK,
    HealthInputs,
    HealthThresholds,
    assert_healthy_or_alert,
    evaluate,
    write_health,
)

_NOW = datetime(2026, 8, 14, 9, 35, tzinfo=timezone.utc)


def _ok_inputs(**overrides: Any) -> HealthInputs:
    """Trạng thái LÀNH MẠNH hoàn toàn. Mọi test khác là bản này đổi đúng
    một trường — nên khi một test đỏ, trường vừa đổi CHÍNH LÀ nguyên nhân."""
    base = HealthInputs(
        updated_at=_NOW,
        last_bar_time=datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc),
        bars_behind=0,
        api_ok=True,
        api_latency_ms=230.0,
        poll_latency_ms=180.0,
        clock_skew_ms=34.0,
        hmm_regime="WEAK_BULL",
        hmm_confidence=0.72,
        hmm_model_age_days=2.0,
        trend_gate="BULL_STRUCTURE",
        hmm_allocation=Decimal("0.95"),
        trend_gate_cap=Decimal("1.00"),
        risk_manager_cap=Decimal("1.00"),
        final_allocation=Decimal("0.95"),
        position_delta_pct=Decimal("2.3"),
        unfilled_orders=0,
        unfilled_value_usdt=Decimal("0"),
        oldest_unfilled_age_seconds=None,
        circuit_breaker="NONE",
        cumulative_fees_usdt=Decimal("47.50"),
        fees_pct_of_gross=8.1,
        last_alert_minutes_ago=12.0,
        uptime_seconds=86400.0,
        testnet=True,
    )
    return replace(base, **overrides) if overrides else base


def test_trang_thai_lanh_manh_la_ok() -> None:
    report = evaluate(_ok_inputs())

    assert report.status == STATUS_OK
    assert report.reasons == ()


# ----------------------------------------------------------------------
# `down` — §B.1: mất feed > 2 chu kỳ bar, API không phản hồi, breaker halt
# ----------------------------------------------------------------------


def test_api_khong_phan_hoi_la_down() -> None:
    report = evaluate(_ok_inputs(api_ok=False))

    assert report.status == STATUS_DOWN


def test_cham_qua_hai_bar_la_down() -> None:
    report = evaluate(_ok_inputs(bars_behind=3))

    assert report.status == STATUS_DOWN


def test_dung_hai_bar_van_chi_la_degraded() -> None:
    """Biên: "> 2 chu kỳ" nghĩa là 2 CHƯA phải down. Test riêng cho biên vì
    `>` và `>=` trông giống nhau khi đọc lướt và khác hẳn khi chạy."""
    report = evaluate(_ok_inputs(bars_behind=2))

    assert report.status == STATUS_DEGRADED


@pytest.mark.parametrize("level", ["DAILY_HALT", "WEEKLY_HALT", "PEAK_HALT"])
def test_circuit_breaker_halt_la_down(level: str) -> None:
    report = evaluate(_ok_inputs(circuit_breaker=level))

    assert report.status == STATUS_DOWN


@pytest.mark.parametrize("level", ["DAILY_REDUCE", "WEEKLY_REDUCE"])
def test_circuit_breaker_reduce_khong_phai_down(level: str) -> None:
    """REDUCE là hoạt động BÌNH THƯỜNG của breaker (giảm size một nửa,
    §5.2), không phải sự cố. Đánh dấu nó "down" sẽ làm trạng thái đỏ trong
    mọi đợt biến động — và một chỉ báo lúc nào cũng đỏ thì không ai đọc."""
    report = evaluate(_ok_inputs(circuit_breaker=level))

    assert report.status == STATUS_OK


def test_halt_levels_suy_ra_tu_risk_manager() -> None:
    """`_HALT_LEVELS` phải đến TỪ `core/risk_manager.py`, không phải một
    danh sách chép tay ở `health.py` — thêm một level HALT mới mà quên cập
    nhật ở đây sẽ làm health.json báo "ok" trong lúc bot đã đóng hết vị thế."""
    from core.risk_manager import _SIZE_MULTIPLIER

    expected = {level.value for level, mult in _SIZE_MULTIPLIER.items() if mult == 0}

    assert health._HALT_LEVELS == expected
    assert expected, "risk_manager không còn level HALT nào — kiểm tra lại"


# ----------------------------------------------------------------------
# `degraded` — §B.1: bars_behind > 0, lệch đồng hồ, lệnh treo, model cũ
# ----------------------------------------------------------------------


def test_cham_mot_bar_la_degraded() -> None:
    report = evaluate(_ok_inputs(bars_behind=1))

    assert report.status == STATUS_DEGRADED


def test_lech_dong_ho_la_degraded() -> None:
    report = evaluate(_ok_inputs(clock_skew_ms=1500.0))

    assert report.status == STATUS_DEGRADED


def test_lech_dong_ho_am_cung_la_degraded() -> None:
    """Đồng hồ chạy CHẬM hơn sàn cũng làm request ký bị từ chối (-1021)
    hệt như chạy nhanh. So `abs()`, không so dấu."""
    report = evaluate(_ok_inputs(clock_skew_ms=-1500.0))

    assert report.status == STATUS_DEGRADED


def test_lenh_treo_qua_lau_la_degraded() -> None:
    report = evaluate(_ok_inputs(unfilled_orders=1, oldest_unfilled_age_seconds=400.0))

    assert report.status == STATUS_DEGRADED


def test_lenh_treo_trong_nguong_van_ok() -> None:
    report = evaluate(_ok_inputs(unfilled_orders=1, oldest_unfilled_age_seconds=20.0))

    assert report.status == STATUS_OK


def test_model_hmm_qua_cu_la_degraded() -> None:
    """2× chu kỳ retrain (7 ngày) = 14. 15 ngày phải đỏ."""
    report = evaluate(_ok_inputs(hmm_model_age_days=15.0))

    assert report.status == STATUS_DEGRADED


def test_model_hmm_dung_hai_chu_ky_van_ok() -> None:
    report = evaluate(_ok_inputs(hmm_model_age_days=14.0))

    assert report.status == STATUS_OK


def test_down_thang_degraded() -> None:
    """Khi cả hai loại điều kiện cùng đúng, status là `down` — nhưng lý do
    của CẢ HAI phải còn trong `reasons`. Sửa xong cái `down` rồi mới phát
    hiện còn một cái `degraded` là cách tốn thời gian nhất để xử lý sự cố."""
    report = evaluate(_ok_inputs(api_ok=False, clock_skew_ms=1500.0))

    assert report.status == STATUS_DOWN
    assert len(report.reasons) == 2
    assert any("API" in r for r in report.reasons)
    assert any("đồng hồ" in r for r in report.reasons)


def test_status_ok_thi_khong_co_ly_do() -> None:
    assert evaluate(_ok_inputs()).reasons == ()


# ----------------------------------------------------------------------
# Bốn trường allocation + bất biến #2
# ----------------------------------------------------------------------


def test_payload_co_du_bon_truong_allocation() -> None:
    """Ba trường `hmm_allocation`/`trend_gate_cap`/`risk_manager_cap` bên
    cạnh `final_allocation` là điểm §B.1 nói thiết kế gốc THIẾU: chỉ nhìn
    `final_allocation` thì không biết TẦNG NÀO đang giới hạn."""
    payload = evaluate(_ok_inputs()).payload

    assert payload["hmm_allocation"] == "0.95"
    assert payload["trend_gate_cap"] == "1.00"
    assert payload["risk_manager_cap"] == "1.00"
    assert payload["final_allocation"] == "0.95"


def test_allocation_ghi_dang_chuoi_khong_phai_float() -> None:
    """Xem `_alloc_to_json`: ghi bằng `float` thì chính file bằng chứng mất
    khả năng chứng minh `final == min(...)`."""
    payload = evaluate(_ok_inputs()).payload

    for key in ("hmm_allocation", "trend_gate_cap", "risk_manager_cap", "final_allocation"):
        assert isinstance(payload[key], str), key


def test_bat_bien_2_bi_vi_pham_thi_bao_cao() -> None:
    """`final` LỚN HƠN min(ba tầng) — đúng thứ CLAUDE.md bất biến #2 cấm."""
    report = evaluate(
        _ok_inputs(
            hmm_allocation=Decimal("0.95"),
            trend_gate_cap=Decimal("0.30"),
            risk_manager_cap=Decimal("1.00"),
            final_allocation=Decimal("0.95"),
        )
    )

    assert len(report.invariant_violations) == 1
    assert "BẤT BIẾN #2" in report.invariant_violations[0]
    assert report.payload["invariant_violations"] == list(report.invariant_violations)


def test_vi_pham_bat_bien_khong_lam_doi_status() -> None:
    """CỐ Ý: `degraded` nghĩa là "hạ tầng có vấn đề, chờ hoặc thử lại"; vi
    phạm bất biến nghĩa là "code sai, phải sửa". Trộn hai thứ thì bug được
    xử lý bằng cách chờ — tức là không bao giờ được xử lý. Kênh riêng của
    nó là `AlertType.INTERNAL_ERROR` qua `assert_healthy_or_alert()`."""
    report = evaluate(_ok_inputs(final_allocation=Decimal("0.99")))

    assert report.invariant_violations
    assert report.status == STATUS_OK


def test_thieu_du_lieu_thi_khong_ket_luan_vi_pham() -> None:
    """Chưa có bar nào (`None`) không phải bằng chứng vi phạm."""
    report = evaluate(_ok_inputs(trend_gate_cap=None))

    assert report.invariant_violations == ()


# ----------------------------------------------------------------------
# Ngưỡng đọc từ settings.yaml (CLAUDE.md bất biến #14)
# ----------------------------------------------------------------------


def test_nguong_doc_tu_settings_that() -> None:
    import main as main_mod

    th = HealthThresholds.from_settings(main_mod.load_settings())

    assert th.clock_skew_degraded_ms == 1000.0
    assert th.unfilled_order_degraded_seconds == 300.0
    assert th.retrain_interval_days == 7
    assert th.hmm_model_age_degraded_days == 14.0


def test_settings_rong_thi_dung_mac_dinh() -> None:
    """Một `settings.yaml` thiếu key không được làm sập vòng lặp chính."""
    th = HealthThresholds.from_settings({})

    assert th == HealthThresholds()


# ----------------------------------------------------------------------
# Ghi file
# ----------------------------------------------------------------------


def test_write_health_ghi_json_doc_lai_duoc(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "health.json"

    write_health(evaluate(_ok_inputs()), target)

    assert json.loads(target.read_text(encoding="utf-8"))["status"] == STATUS_OK


def test_write_health_khong_de_lai_file_tmp(tmp_path: Path) -> None:
    """Ghi nguyên tử: tmp phải đã được `replace()` đi, không nằm lại."""
    target = tmp_path / "health.json"

    write_health(evaluate(_ok_inputs()), target)

    assert list(tmp_path.iterdir()) == [target]


def test_write_health_khong_raise_khi_khong_ghi_duoc(tmp_path: Path) -> None:
    """Đường quan sát không được giết vòng lặp chính. Dùng một FILE làm
    thư mục cha -> `mkdir` ném `NotADirectoryError` (một `OSError`)."""
    blocker = tmp_path / "khong-phai-thu-muc"
    blocker.write_text("x", encoding="utf-8")

    write_health(evaluate(_ok_inputs()), blocker / "health.json")


def test_default_health_path_doc_env_luc_goi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không phải hằng số mức module — cùng lý do với
    `alerts.py::_default_status_path`: `STATE_DIR` được đặt lúc chạy."""
    monkeypatch.setenv("STATE_DIR", "/tmp/dat-luc-chay")

    assert health.default_health_path() == Path("/tmp/dat-luc-chay/health.json")


def test_health_json_khong_nam_trong_cay_ma_nguon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lệch có chủ ý khỏi `monitoring/state/health.json` của prompt §B.1:
    `status.json` từng nằm đúng chỗ đó và đã phải chuyển đi (2026-08-08)."""
    monkeypatch.delenv("STATE_DIR", raising=False)

    assert "monitoring" not in health.default_health_path().parts


# ----------------------------------------------------------------------
# §B.3 — kiểm tra 60 giây sau khởi động
# ----------------------------------------------------------------------


class _FakeAlertManager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, alert: Any) -> bool:
        self.sent.append(alert)
        return True


def test_khoi_dong_lanh_manh_thi_khong_canh_bao() -> None:
    am = _FakeAlertManager()

    assert_healthy_or_alert(lambda: evaluate(_ok_inputs()), am, delay_seconds=0, sleep=lambda _: None)

    assert am.sent == []


def test_khoi_dong_down_thi_canh_bao_ngay() -> None:
    am = _FakeAlertManager()

    assert_healthy_or_alert(
        lambda: evaluate(_ok_inputs(api_ok=False)), am, delay_seconds=0, sleep=lambda _: None
    )

    assert len(am.sent) == 1
    assert am.sent[0].alert_type is AlertType.HEALTH_CHECK_FAILED
    assert am.sent[0].severity == "CRITICAL"


def test_canh_bao_mang_theo_ly_do() -> None:
    """Nhãn `HEALTH_CHECK_FAILED` không nói được nguyên nhân — lý do thật
    phải đi kèm, nếu không người vận hành lại phải tự đi dò."""
    am = _FakeAlertManager()

    assert_healthy_or_alert(
        lambda: evaluate(_ok_inputs(hmm_model_age_days=30.0)),
        am,
        delay_seconds=0,
        sleep=lambda _: None,
    )

    assert "Model HMM cũ" in am.sent[0].message


def test_khong_dung_nhan_feed_hay_api_cho_nguyen_nhan_khac() -> None:
    """Chọn `DATA_FEED_LOST`/`API_LOST` cho một model HMM quá cũ sẽ gửi
    người vận hành đi kiểm tra mạng cho vấn đề nằm ở chỗ khác."""
    am = _FakeAlertManager()

    assert_healthy_or_alert(
        lambda: evaluate(_ok_inputs(hmm_model_age_days=30.0)),
        am,
        delay_seconds=0,
        sleep=lambda _: None,
    )

    assert am.sent[0].alert_type not in (AlertType.DATA_FEED_LOST, AlertType.API_LOST)


def test_vi_pham_bat_bien_gui_internal_error() -> None:
    am = _FakeAlertManager()

    assert_healthy_or_alert(
        lambda: evaluate(_ok_inputs(final_allocation=Decimal("0.99"))),
        am,
        delay_seconds=0,
        sleep=lambda _: None,
    )

    assert [a.alert_type for a in am.sent] == [AlertType.INTERNAL_ERROR]


def test_do_SAU_khi_ngu_day_khong_phai_truoc() -> None:
    """`report_provider` là callable chính vì điều này. Chụp trạng thái
    TRƯỚC khi ngủ rồi mới ngủ thì hàm này chỉ xác nhận bot khởi động được
    — đúng việc `ops/health_check.py` đã làm, và bỏ lỡ đúng thứ §B.3 sinh
    ra để bắt: hỏng SAU khởi động."""
    order: list[str] = []
    am = _FakeAlertManager()

    def _provider() -> Any:
        order.append("do")
        return evaluate(_ok_inputs())

    def _sleep(_: float) -> None:
        order.append("ngu")

    assert_healthy_or_alert(_provider, am, delay_seconds=60, sleep=_sleep)

    assert order == ["ngu", "do"]


def test_ngu_dung_so_giay_duoc_yeu_cau() -> None:
    slept: list[float] = []
    am = _FakeAlertManager()

    assert_healthy_or_alert(
        lambda: evaluate(_ok_inputs()), am, delay_seconds=60, sleep=slept.append
    )

    assert slept == [60.0]


def test_mac_dinh_la_60_giay() -> None:
    """§B.3 nói rõ 60 giây — ghim để một lần "tối ưu" xuống 1 giây không
    lặng lẽ biến phép kiểm này thành kiểm lúc bot chưa kịp chạy bar nào."""
    assert health._STARTUP_CHECK_DELAY_S == 60.0
