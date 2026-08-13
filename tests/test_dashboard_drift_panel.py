"""Panel SO SÁNH BASELINE — đọc `drift.json`, không tự tính lại drift.

`monitoring/drift.py` (Phase 12b §C.1) CHƯA được xây, nên `drift.json`
chưa tồn tại. Panel phải nói ra điều đó rõ ràng thay vì để trống.

"Để trống im lặng" là chế độ hỏng tệ nhất ở đây: một ô rỗng trông y hệt
"không có gì đáng lo", trong khi nó có thể nghĩa là cơ chế phát hiện trôi
lệch chưa từng chạy. Toàn bộ giá trị của panel này nằm ở chỗ nó phân biệt
được BỐN trạng thái, và không trạng thái nào cho ra ô rỗng.

Mọi test truyền `drift_path` TƯỜNG MINH — không test nào đụng đường mặc
định `monitoring/state/drift.json`, để kết quả không phụ thuộc vào việc
máy chạy test có tình cờ tồn tại file đó hay không.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from monitoring.dashboard import (
    DRIFT_EMPTY,
    DRIFT_MISSING,
    DRIFT_OK,
    DRIFT_UNREADABLE,
    Dashboard,
    DashboardState,
    DriftMetric,
    drift_metric_style,
    load_drift_panel_data,
)

_PANEL_TITLE = "SO SÁNH BASELINE"


def _state() -> DashboardState:
    """DashboardState tối thiểu — panel drift KHÔNG đọc gì từ nó, nhưng
    `render_text()` cần một state hợp lệ để vẽ các panel còn lại."""
    return DashboardState(
        regime_label="BULL",
        regime_probability=0.9,
        vol_rank="LOW",
        stability_bars=3,
        flicker_count=0,
        flicker_window=20,
        is_confirmed=True,
        equity=Decimal("10000"),
        daily_pnl=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
        allocation_pct=Decimal("0.5"),
        position_qty=Decimal("0.1"),
        cash=Decimal("5000"),
        position_direction="LONG",
        position_entry_price=Decimal("50000"),
        position_unrealized_pnl_pct=Decimal("1"),
        position_stop_loss=Decimal("48000"),
        position_days_held=2,
        recent_signals=(),
        daily_dd_pct=Decimal("1"),
        daily_dd_limit_pct=Decimal("5"),
        peak_dd_pct=Decimal("2"),
        peak_dd_limit_pct=Decimal("20"),
        monthly_fees_paid=Decimal("10"),
        monthly_fees_pct_of_gross=Decimal("5"),
        poll_latency_ms=100.0,
        last_poll_at="2026-08-08T00:00:00+00:00",
        bars_behind=0,
        api_latency_ms=50.0,
        clock_drift_ms=10.0,
        hmm_last_trained_days_ago=3,
        is_testnet=True,
    )


def _render(drift_path: Path) -> str:
    return Dashboard(drift_path=drift_path).render_text(_state(), width=120)


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ======================================================================
# 1. CHƯA CÓ FILE — trạng thái hiện tại của dự án
# ======================================================================


def test_thieu_file_noi_ro_chua_co_du_lieu(tmp_path: Path) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM."""
    data = load_drift_panel_data(tmp_path / "drift.json")

    assert data.status == DRIFT_MISSING
    assert "Chưa có dữ liệu drift" in data.detail
    assert data.metrics == ()


def test_thieu_file_panel_van_hien_va_khong_trong(tmp_path: Path) -> None:
    """Panel phải CÓ MẶT trong layout và có chữ đọc được — không phải một
    ô rỗng, cũng không phải biến mất khỏi dashboard."""
    out = _render(tmp_path / "drift.json")

    assert _PANEL_TITLE in out, "panel bị bỏ khỏi layout khi thiếu file"
    assert "Chưa có dữ liệu drift" in out


def test_thieu_file_chi_ro_ai_sinh_ra_no(tmp_path: Path) -> None:
    """Thông điệp phải nói được PHẢI LÀM GÌ. "Chưa có dữ liệu" trần chỉ
    tạo thắc mắc; nói rõ file do Phase 12b sinh ra thì người đọc biết ngay
    đây là việc chưa xây, không phải thứ đang hỏng."""
    out = _render(tmp_path / "drift.json")

    assert "drift.json" in out
    assert "Phase 12b" in out


def test_thieu_file_khong_bia_ra_con_so_nao(tmp_path: Path) -> None:
    """KHÔNG tự tính lại logic drift.

    Baseline Phase 7 (30.6 / 18.1 / 16.5 / 34.8, 32.3%, 11.68%) nằm trong
    `tests/snapshots/phase7_baseline/`. Nếu panel tự nạp chúng khi thiếu
    file, dashboard sẽ hiện một con số KHÁC với con số mà cơ chế cảnh báo
    dùng — hai nguồn sự thật cho cùng một chỉ số là cách chắc chắn nhất để
    không ai tin cái nào.
    """
    data = load_drift_panel_data(tmp_path / "drift.json")

    assert data.metrics == ()
    for baseline_figure in ("30.6", "18.1", "16.5", "34.8", "32.3", "11.68"):
        assert baseline_figure not in data.detail


# ======================================================================
# 2. CÓ FILE NHƯNG KHÔNG DÙNG ĐƯỢC — phải KHÁC "chưa có"
# ======================================================================


def test_json_hong_khong_bi_dan_nhan_chua_co_du_lieu(tmp_path: Path) -> None:
    """Gộp "file hỏng" vào "chưa có dữ liệu" là đúng cái bẫy panel này
    sinh ra để tránh: người vận hành cho rằng Phase 12b chưa xây xong, và
    một file JSON hỏng nằm im vô thời hạn."""
    path = tmp_path / "drift.json"
    path.write_text("{ đây không phải json", encoding="utf-8")

    data = load_drift_panel_data(path)

    assert data.status == DRIFT_UNREADABLE
    assert "Chưa có dữ liệu drift" not in data.detail
    assert "không đọc được" in data.detail


@pytest.mark.parametrize(
    "payload",
    [
        {"metrics": "không phải list"},
        {"khong_co_khoa_metrics": []},
        ["list ở tầng ngoài cùng"],
        "chuỗi trần",
        42,
    ],
    ids=["metrics-sai-kieu", "thieu-metrics", "list-ngoai-cung", "chuoi", "so"],
)
def test_sai_hop_dong_schema_la_unreadable(tmp_path: Path, payload: object) -> None:
    """Sai hợp đồng -> UNREADABLE kèm thông điệp nói rõ chờ đợi gì. Đây là
    hợp đồng mà Phase 12b phải thoả khi được xây."""
    data = load_drift_panel_data(_write(tmp_path / "drift.json", payload))

    assert data.status == DRIFT_UNREADABLE
    assert "metrics" in data.detail


def test_file_rong_khac_file_thieu(tmp_path: Path) -> None:
    """Đọc được, đúng schema, nhưng 0 chỉ số — nghĩa là drift.py ĐÃ chạy
    mà không sinh được gì. Khác hẳn "chưa xây drift.py"."""
    data = load_drift_panel_data(_write(tmp_path / "drift.json", {"metrics": []}))

    assert data.status == DRIFT_EMPTY
    assert "Chưa có dữ liệu drift" not in data.detail


@pytest.mark.parametrize(
    "broken",
    [b"\x00\x01\x02\xff\xfe", b""],
    ids=["nhi-phan", "file-rong-hoan-toan"],
)
def test_khong_bao_gio_crash(tmp_path: Path, broken: bytes) -> None:
    """ "KHÔNG crash" là yêu cầu tuyệt đối: panel này nằm trong vòng render
    của dashboard đang chạy 24/7."""
    path = tmp_path / "drift.json"
    path.write_bytes(broken)

    data = load_drift_panel_data(path)
    assert data.status == DRIFT_UNREADABLE

    assert _PANEL_TITLE in _render(path)  # render cũng không được nổ


def test_duong_dan_la_thu_muc_khong_crash(tmp_path: Path) -> None:
    """Biên hiếm nhưng có thật: ai đó `mkdir monitoring/state/drift.json`."""
    path = tmp_path / "drift.json"
    path.mkdir()

    assert load_drift_panel_data(path).status == DRIFT_UNREADABLE
    assert _PANEL_TITLE in _render(path)


# ======================================================================
# 3. CÓ DỮ LIỆU — panel chỉ HIỂN THỊ, không diễn giải
# ======================================================================


def _ok_payload() -> dict:
    return {
        "generated_at_utc": "2026-08-08T00:05:00+00:00",
        "metrics": [
            {
                "name": "Phân bố allocation",
                "current": "28.1 / 19.0 / 15.2 / 37.7 %",
                "baseline": "30.6 / 18.1 / 16.5 / 34.8 %",
                "alert": False,
            },
            {
                "name": "Phí / lợi nhuận gộp",
                "current": "24.0%",
                "baseline": "11.68%",
                "alert": True,
            },
        ],
    }


def test_hien_du_chi_so_va_moc_thoi_gian(tmp_path: Path) -> None:
    path = _write(tmp_path / "drift.json", _ok_payload())

    data = load_drift_panel_data(path)
    assert data.status == DRIFT_OK
    assert len(data.metrics) == 2

    out = _render(path)
    assert "Phân bố allocation" in out
    assert "Phí / lợi nhuận gộp" in out
    assert "2026-08-08T00:05:00+00:00" in out


def test_alert_do_ben_ghi_quyet_dinh_khong_phai_panel(tmp_path: Path) -> None:
    """Loader giữ nguyên cờ `alert`, không diễn giải lại."""
    payload = {
        "metrics": [
            {"name": "Bịa", "current": "10", "baseline": "10", "alert": True},
        ]
    }
    data = load_drift_panel_data(_write(tmp_path / "drift.json", payload))

    assert data.metrics[0].alert is True


@pytest.mark.parametrize(
    "current,baseline,alert,expect_danger",
    [
        ("10", "10", True, True),  # BẰNG NHAU mà vẫn cảnh báo -> phải đỏ
        ("10", "99", False, False),  # KHÁC NHAU mà không cảnh báo -> phải xanh
        ("10", "99", True, True),
        ("10", "10", False, False),
    ],
    ids=["bang-nhau-alert", "khac-nhau-khong-alert", "khac-nhau-alert", "bang-nhau-khong-alert"],
)
def test_to_mau_theo_co_alert_khong_theo_phep_so_sanh(
    current: str, baseline: str, alert: bool, expect_danger: bool
) -> None:
    """Panel KHÔNG được tự so sánh `current` với `baseline`.

    Hai ca đầu là ca phân biệt: nếu ai đó đổi sang
    `current != baseline` thì cả hai đỏ ngược lại. Kiểm hàm thuần chứ
    không kiểm text đã render — `render_text()` lược bỏ màu nên lỗi này
    KHÔNG quan sát được từ output (đã đo bằng đột biến: bản test cũ chỉ
    kiểm loader và để đột biến này lọt lưới).

    Ngưỡng của từng chỉ số nằm ở bảng Phase 12b §C.1, mỗi chỉ số một quy
    tắc — panel so sánh bằng `!=` là tự bịa ra một logic drift thứ hai.
    """
    metric = DriftMetric(name="X", current=current, baseline=baseline, alert=alert)

    style = drift_metric_style(metric)

    assert (style == "bold red") is expect_danger
    assert style in ("bold red", "bold green")


def test_gia_tri_hien_thi_nguyen_van_khong_dinh_dang_lai(tmp_path: Path) -> None:
    """`current`/`baseline` là chuỗi ĐÃ định dạng bởi bên ghi. Panel định
    dạng lại nghĩa là nó phải biết đơn vị từng chỉ số — tức là biết logic
    drift, đúng thứ nó KHÔNG được biết."""
    payload = {
        "metrics": [
            {"name": "X", "current": "2.5×", "baseline": "1.0×", "alert": True},
        ]
    }
    data = load_drift_panel_data(_write(tmp_path / "drift.json", payload))

    assert data.metrics[0].current == "2.5×"
    assert data.metrics[0].baseline == "1.0×"


def test_phan_tu_sai_hinh_dang_bi_bo_qua_nhung_khong_im_lang(tmp_path: Path) -> None:
    """Bỏ qua phần tử hỏng còn hơn làm hỏng cả panel — nhưng phải ĐẾM lại
    và nói ra, vì nó nghĩa là bên ghi và bên đọc đã lệch hợp đồng."""
    payload = {
        "metrics": [
            {"name": "Tốt", "current": "1", "baseline": "2", "alert": False},
            {"khong_co_name": True},
            "không phải dict",
        ]
    }
    data = load_drift_panel_data(_write(tmp_path / "drift.json", payload))

    assert data.status == DRIFT_OK
    assert len(data.metrics) == 1
    assert "2 phần tử sai hình dạng" in data.detail


def test_thieu_generated_at_van_ve_duoc(tmp_path: Path) -> None:
    """Mốc thời gian là tuỳ chọn — thiếu nó không được làm mất cả panel."""
    payload = {"metrics": [{"name": "X", "current": "1", "baseline": "2", "alert": False}]}
    out = _render(_write(tmp_path / "drift.json", payload))

    assert "không rõ thời điểm" in out


# ======================================================================
# 4. Panel LUÔN có mặt, ở mọi trạng thái
# ======================================================================


def test_panel_co_mat_o_moi_trang_thai(tmp_path: Path) -> None:
    """Không trạng thái nào được làm panel biến mất hoặc rỗng."""
    cases = {
        "missing": tmp_path / "khong-ton-tai.json",
        "unreadable": _write(tmp_path / "hong.json", "chuỗi trần"),
        "empty": _write(tmp_path / "rong.json", {"metrics": []}),
        "ok": _write(tmp_path / "ok.json", _ok_payload()),
    }

    for label, path in cases.items():
        out = _render(path)
        assert _PANEL_TITLE in out, f"panel biến mất ở trạng thái {label}"

        # Cắt lấy phần thân panel, khẳng định nó có chữ — không phải khung rỗng.
        body = out.split(_PANEL_TITLE, 1)[1]
        assert any(ch.isalnum() for ch in body[:400]), f"panel rỗng ở trạng thái {label}"
