"""Phase 12b §C.2 — `monitoring/daily_digest.py`.

Hai điều được kiểm kỹ nhất, vì cả hai đều là chỗ một báo cáo "trông đúng"
mà sai:

1. `limiting_layer()` — cách làm ngây thơ (`min()` rồi xem trần nào bằng)
   sẽ báo "risk manager giới hạn" ở 100 % số bar, vì trong đường dây hiện
   tại `risk_manager_cap == final_allocation == min(ba trần)`. Đúng về mặt
   số học, vô dụng về mặt vận hành.

2. Nguồn thiếu KHÁC nguồn rỗng. `*không có dữ liệu*` và `0` cần phản ứng
   khác hẳn nhau, và một mục biến mất khỏi báo cáo trông giống hệt một mục
   bằng 0.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from monitoring.alerts import AlertType
from monitoring.daily_digest import (
    LAYER_HMM,
    LAYER_RISK,
    LAYER_TIE,
    LAYER_TREND,
    DigestData,
    collect,
    digest_path,
    limiting_layer,
    read_json_lines,
    render,
    run,
    summary_line,
    write_digest,
)

_NGAY = date(2026, 8, 13)


# ----------------------------------------------------------------------
# `limiting_layer` — điểm dễ sai nhất
# ----------------------------------------------------------------------


def _d(x: str) -> Decimal:
    return Decimal(x)


def test_risk_cat_sau_hon_ca_hai_thi_la_risk() -> None:
    assert limiting_layer(_d("0.95"), _d("1.00"), _d("0.50")) == LAYER_RISK


def test_trend_gate_thap_hon_hmm_thi_la_trend_gate() -> None:
    assert limiting_layer(_d("0.95"), _d("0.30"), _d("0.30")) == LAYER_TREND


def test_hmm_thap_hon_trend_gate_thi_la_hmm() -> None:
    assert limiting_layer(_d("0.50"), _d("1.00"), _d("0.50")) == LAYER_HMM


def test_risk_bang_min_KHONG_phai_risk_gioi_han() -> None:
    """Khẳng định trung tâm. `risk_manager_cap == final_allocation ==
    min(ba trần)` trong đường dây hiện tại (xem `core/signal_generator.py`),
    nên risk LUÔN bằng giá trị nhỏ nhất.

    Một cài đặt ngây thơ (`min()` rồi xem trần nào bằng nó) sẽ báo "risk
    manager giới hạn" ở 100 % số bar — đúng số học, vô dụng vận hành. Ở
    đây trend gate mới là thứ thật sự cắt.
    """
    assert limiting_layer(_d("0.95"), _d("0.30"), _d("0.30")) != LAYER_RISK


def test_hmm_bang_trend_thi_dong_hang() -> None:
    """Gán bừa cho một bên sẽ làm thống kê nghiêng vĩnh viễn về bên đó."""
    assert limiting_layer(_d("0.60"), _d("0.60"), _d("0.60")) == LAYER_TIE


def test_thieu_du_lieu_thi_none() -> None:
    assert limiting_layer(None, _d("1.0"), _d("1.0")) is None
    assert limiting_layer(_d("1.0"), None, _d("1.0")) is None
    assert limiting_layer(_d("1.0"), _d("1.0"), None) is None


# ----------------------------------------------------------------------
# Đọc log
# ----------------------------------------------------------------------


def _viet_log(path: Path, ban_ghi: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in ban_ghi) + "\n", encoding="utf-8")


def test_doc_json_lines(tmp_path: Path) -> None:
    f = tmp_path / "x.log"
    _viet_log(f, [{"a": 1}, {"a": 2}])

    assert read_json_lines(f) == [{"a": 1}, {"a": 2}]


def test_file_khong_ton_tai_thi_rong(tmp_path: Path) -> None:
    assert read_json_lines(tmp_path / "khong-co.log") == []


def test_dong_hong_khong_lam_hong_ca_file(tmp_path: Path) -> None:
    """Log có thể bị cắt giữa chừng nếu tiến trình chết đúng lúc ghi. Mất
    một dòng còn hơn mất cả báo cáo — nhưng phải ĐẾM ĐƯỢC, không im lặng."""
    f = tmp_path / "x.log"
    f.write_text('{"a": 1}\n{ khong phai json\n{"a": 2}\n', encoding="utf-8")

    ban_ghi = read_json_lines(f)

    assert {"a": 1} in ban_ghi and {"a": 2} in ban_ghi
    assert sum(1 for r in ban_ghi if r.get("_hong")) == 1


# ----------------------------------------------------------------------
# `collect` trên log dựng sẵn
# ----------------------------------------------------------------------


def _bar(gio: int, regime: str, **them: Any) -> dict[str, Any]:
    return {
        "timestamp": datetime(2026, 8, 13, gio, tzinfo=timezone.utc).isoformat(),
        "event": "regime_state",
        "regime": regime,
        "equity": "10000",
        "daily_pnl": "12.5",
        "cumulative_fees_paid": "3.75",
        **them,
    }


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    log_dir = tmp_path / "logs"
    _viet_log(
        log_dir / "regime.log",
        [
            _bar(1, "BULL", hmm_allocation="0.95", trend_gate_cap="1.00", risk_manager_cap="0.95"),
            _bar(2, "BULL", hmm_allocation="0.95", trend_gate_cap="0.30", risk_manager_cap="0.30"),
            _bar(3, "BEAR", hmm_allocation="0.95", trend_gate_cap="1.00", risk_manager_cap="0.50"),
            # Ngày KHÁC — không được lọt vào thống kê ngày 13.
            {
                "timestamp": datetime(2026, 8, 12, 5, tzinfo=timezone.utc).isoformat(),
                "regime": "NEUTRAL",
            },
        ],
    )
    _viet_log(
        log_dir / "trades.log",
        [
            {"timestamp": _bar(1, "x")["timestamp"], "event": "order_submitted"},
            {"timestamp": _bar(2, "x")["timestamp"], "event": "order_submitted"},
            {
                "timestamp": _bar(3, "x")["timestamp"],
                "event": "signal_rejected",
                "rejection_reason": "vượt max_trades_per_day",
            },
            {
                "timestamp": _bar(3, "x")["timestamp"],
                "event": "signal_rejected",
                "rejection_reason": "vượt max_trades_per_day",
            },
        ],
    )
    return log_dir


def test_dem_bar_theo_dung_ngay(logs: Path, tmp_path: Path) -> None:
    """Bản ghi của ngày 12 không được lọt vào thống kê ngày 13."""
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.n_bars == 3


def test_phan_bo_regime_va_so_lan_doi(logs: Path, tmp_path: Path) -> None:
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.regime_counts == {"BULL": 2, "BEAR": 1}
    assert data.regime_changes == 1


def test_dem_lenh_va_ly_do_tu_choi(logs: Path, tmp_path: Path) -> None:
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.n_orders == 2
    assert data.rejections == (("vượt max_trades_per_day", 2),)


def test_dem_tang_gioi_han(logs: Path, tmp_path: Path) -> None:
    """Ba bar: HMM cắt (0.95 < 1.00), trend gate cắt (0.30 < 0.95), risk
    cắt (0.50 < min(0.95, 1.00))."""
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.layer_counts == {LAYER_HMM: 1, LAYER_TREND: 1, LAYER_RISK: 1}


def test_tai_chinh_lay_tu_ban_ghi_CUOI_trong_ngay(logs: Path, tmp_path: Path) -> None:
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.equity == Decimal("10000")
    assert data.daily_pnl == Decimal("12.5")
    assert data.cumulative_fees == Decimal("3.75")


def test_thieu_log_thi_ghi_vao_missing_sources(tmp_path: Path) -> None:
    data = collect(_NGAY, log_dir=tmp_path / "khong-co", state_dir=tmp_path)

    assert any("regime.log" in p for p in data.missing_sources)
    assert any("trades.log" in p for p in data.missing_sources)


# ----------------------------------------------------------------------
# Drift: CHỈ ĐỌC, và "không đọc được" KHÁC "không có cảnh báo"
# ----------------------------------------------------------------------


def test_doc_canh_bao_drift_tu_file(logs: Path, tmp_path: Path) -> None:
    (tmp_path / "drift.json").write_text(
        json.dumps(
            {
                "metrics": [
                    {"name": "Phân bố allocation (4 mức)", "alert": True},
                    {"name": "Flicker rate", "alert": False},
                ]
            }
        ),
        encoding="utf-8",
    )

    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.drift_alerts == ("Phân bố allocation (4 mức)",)


def test_drift_khong_co_file_thi_None_khong_phai_rong(logs: Path, tmp_path: Path) -> None:
    """`None` (không đọc được) KHÁC `()` (đọc được, sạch). Gộp hai thứ lại
    nghĩa là "drift.py chưa chạy lần nào" hiển thị y hệt "mọi chỉ số đều
    trong ngưỡng" — hai tình huống cần phản ứng ngược nhau."""
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert data.drift_alerts is None


def test_drift_hong_thi_cung_la_None(logs: Path, tmp_path: Path) -> None:
    (tmp_path / "drift.json").write_text("{ khong phai json", encoding="utf-8")

    assert collect(_NGAY, log_dir=logs, state_dir=tmp_path).drift_alerts is None


def test_digest_khong_tinh_lai_drift() -> None:
    """`monitoring/drift.py` là bên DUY NHẤT quyết định cờ `alert`."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "daily_digest.py").read_text(
        encoding="utf-8"
    )

    assert "from monitoring.drift import" not in src
    assert "DriftThresholds" not in src


# ----------------------------------------------------------------------
# Kết xuất — mọi mục §C.2 LUÔN xuất hiện
# ----------------------------------------------------------------------

_MUC_C2 = [
    "## Hoạt động",
    "### Lý do từ chối",
    "## Regime",
    "## Tầng nào giới hạn allocation",
    "## Tài chính",
    "## Cảnh báo drift đang bật",
    "## hmmlearn",
]


@pytest.mark.parametrize("muc", _MUC_C2)
def test_muc_luon_xuat_hien_du_rong(muc: str) -> None:
    """Một mục biến mất trông giống hệt một mục bằng 0."""
    assert muc in render(DigestData(day=_NGAY))


@pytest.mark.parametrize("muc", _MUC_C2)
def test_muc_luon_xuat_hien_khi_co_du_lieu(muc: str, logs: Path, tmp_path: Path) -> None:
    assert muc in render(collect(_NGAY, log_dir=logs, state_dir=tmp_path))


def test_thieu_du_lieu_ghi_ro_khong_phai_de_trong() -> None:
    ra = render(DigestData(day=_NGAY))

    assert "*không có dữ liệu*" in ra


def test_canh_bao_thieu_nguon_hien_len_dau() -> None:
    ra = render(DigestData(day=_NGAY, missing_sources=("logs/regime.log",)))

    assert "Thiếu nguồn dữ liệu" in ra
    assert ra.index("Thiếu nguồn dữ liệu") < ra.index("## Hoạt động")


def test_dong_log_hong_duoc_bao_cao() -> None:
    assert "3 dòng log hỏng" in render(DigestData(day=_NGAY, corrupt_log_lines=3))


def test_render_la_ham_thuan(logs: Path, tmp_path: Path) -> None:
    """Gọi hai lần cho kết quả y hệt — không đọc đồng hồ, không đọc file."""
    data = collect(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert render(data) == render(data)


def test_drift_khong_doc_duoc_hien_khac_drift_sach() -> None:
    khong_doc = render(DigestData(day=_NGAY, drift_alerts=None))
    sach = render(DigestData(day=_NGAY, drift_alerts=()))

    assert khong_doc != sach
    assert "Không có chỉ số nào vượt ngưỡng" in sach
    assert "Không có chỉ số nào vượt ngưỡng" not in khong_doc


# ----------------------------------------------------------------------
# Ghi file + Telegram
# ----------------------------------------------------------------------


def test_ghi_dung_duong_dan(tmp_path: Path) -> None:
    duong_dan = write_digest(DigestData(day=_NGAY), log_dir=tmp_path)

    assert duong_dan == tmp_path / "digest" / "2026-08-13.md"
    assert duong_dan.read_text(encoding="utf-8").startswith("# Digest 2026-08-13")


def test_ghi_khong_de_lai_tmp(tmp_path: Path) -> None:
    write_digest(DigestData(day=_NGAY), log_dir=tmp_path)

    assert [p.name for p in (tmp_path / "digest").iterdir()] == ["2026-08-13.md"]


def test_ghi_khong_raise_khi_khong_ghi_duoc(tmp_path: Path) -> None:
    chan = tmp_path / "digest"
    chan.write_text("x", encoding="utf-8")  # file, không phải thư mục

    write_digest(DigestData(day=_NGAY), log_dir=tmp_path)


class _FakeAlertManager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, alert: Any) -> bool:
        self.sent.append(alert)
        return True


def test_gui_telegram_khi_co_alert_manager(logs: Path, tmp_path: Path) -> None:
    am = _FakeAlertManager()

    run(_NGAY, log_dir=logs, alert_manager=am, state_dir=tmp_path)

    assert len(am.sent) == 1
    assert am.sent[0].alert_type is AlertType.DAILY_DIGEST
    assert am.sent[0].severity == "INFO"


def test_khong_co_alert_manager_thi_van_ghi_file(logs: Path, tmp_path: Path) -> None:
    """Gửi Telegram là TUỲ CHỌN (§C.2 "nếu đã cấu hình"); ghi file thì
    không."""
    run(_NGAY, log_dir=logs, state_dir=tmp_path)

    assert digest_path(_NGAY, logs).exists()


def test_tom_tat_dat_canh_bao_drift_len_dau() -> None:
    """Tin nhắn dài bị cắt trên điện thoại, và phần bị cắt luôn là phần
    cuối."""
    dong = summary_line(DigestData(day=_NGAY, drift_alerts=("A", "B"), n_bars=1))

    assert dong.index("drift") < dong.index("bar")
    assert "2 cảnh báo" in dong


def test_tom_tat_phan_biet_sach_voi_khong_doc_duoc() -> None:
    assert "sạch" in summary_line(DigestData(day=_NGAY, drift_alerts=()))
    assert "không đọc được" in summary_line(DigestData(day=_NGAY, drift_alerts=None))


def test_mac_dinh_la_NGAY_HOM_QUA(logs: Path, tmp_path: Path) -> None:
    """Digest chạy 00:05 UTC — năm phút SAU khi ngày mới bắt đầu. Ngày cần
    tổng kết là ngày vừa kết thúc. Mặc định `date.today()` sẽ cho một báo
    cáo trống mỗi sáng, và không có gì đỏ."""
    data = run(log_dir=logs, state_dir=tmp_path)

    assert data.day == datetime.now(timezone.utc).date() - timedelta(days=1)


# ----------------------------------------------------------------------
# `log_state` phải ghi ba trần, nếu không mục §C.2 luôn rỗng
# ----------------------------------------------------------------------


def test_log_state_ghi_ba_tran(tmp_path: Path) -> None:
    """Nối dây: `monitoring/logger.py::log_state` phải ghi ba trần, và
    `main.py::process_one_bar` phải truyền chúng. Thiếu một trong hai thì
    mục "tầng nào giới hạn" của §C.2 luôn rỗng — và một mục luôn rỗng
    trông y hệt một hệ thống chưa bao giờ bị giới hạn."""
    from monitoring.logger import get_logger, log_state

    lg = get_logger("test_digest_caps", str(tmp_path))
    log_state(
        lg,
        regime="BULL",
        probability=0.9,
        equity=Decimal("10000"),
        positions={},
        daily_pnl=Decimal("0"),
        cumulative_fees_paid=Decimal("0"),
        hmm_allocation=Decimal("0.95"),
        trend_gate_cap=Decimal("0.30"),
        risk_manager_cap=Decimal("0.30"),
        drawdown_pct=Decimal("1.5"),
    )

    ban_ghi = read_json_lines(tmp_path / "test_digest_caps.log")
    assert ban_ghi, "log_state không ghi được gì"
    cuoi = ban_ghi[-1]
    assert cuoi["hmm_allocation"] == "0.95"
    assert cuoi["trend_gate_cap"] == "0.30"
    assert cuoi["risk_manager_cap"] == "0.30"
    assert cuoi["drawdown_pct"] == "1.5"


def test_main_truyen_ba_tran_vao_log_state() -> None:
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")

    assert "hmm_allocation=result.hmm_allocation" in src
    assert "trend_gate_cap=result.trend_gate_cap" in src
    assert "risk_manager_cap=result.risk_manager_cap" in src


def test_drift_sai_schema_thi_cung_la_None(logs: Path, tmp_path: Path) -> None:
    """Đọc được JSON nhưng `metrics` không phải list — sai hợp đồng, tức
    là bên ghi và bên đọc đã lệch nhau. Trả `()` ở đây sẽ hiển thị y hệt
    "drift sạch", giấu mất chính sự lệch đó.

    Nhánh này KHÁC nhánh JSON hỏng (`test_drift_hong_thi_cung_la_None`) —
    đo bằng đột biến: sửa riêng nhánh này, test kia vẫn xanh.
    """
    (tmp_path / "drift.json").write_text(
        json.dumps({"metrics": "khong-phai-list"}), encoding="utf-8"
    )

    assert collect(_NGAY, log_dir=logs, state_dir=tmp_path).drift_alerts is None
