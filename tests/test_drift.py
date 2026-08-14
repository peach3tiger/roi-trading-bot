"""Phase 12b §C.1 — `monitoring/drift.py`.

Phép kiểm quan trọng nhất trong file này là `test_baseline_khong_tu_bao_dong`:
baseline so với CHÍNH NÓ phải im lặng hoàn toàn. Một hệ cảnh báo tự báo
động trên dữ liệu sinh ra nó là hệ cảnh báo không đo gì cả — và điều đó
không lộ ra ở bất kỳ test đơn vị nào.

Phép kiểm quan trọng thứ hai là `test_dinh_nghia_khop_con_so_trong_C1`:
nó chứng minh định nghĩa chỉ số ở đây TRÙNG với định nghĩa của người đã đo
30.6/18.1/16.5/34.8. Không có nó, "hiệu số so với baseline" có thể đang đo
sự khác nhau giữa hai ĐỊNH NGHĨA thay vì giữa hai HÀNH VI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import main as main_mod
from monitoring import drift
from monitoring.drift import (
    WARNING_TREND_LEN,
    WINDOW_DAYS,
    Behaviour,
    DriftThresholds,
    allocation_bin_edges,
    allocation_mix,
    compare,
    load_baseline,
    load_baseline_bands,
    measure,
    monotonic_increasing_tail,
    nominal_allocation_levels,
    normalize_bars,
    recent_window,
    retrain_warning_counts,
    run,
    write_drift,
)

_BASELINE_DIR = Path(__file__).resolve().parent / "snapshots" / "phase7_baseline"


@pytest.fixture(scope="module")
def settings() -> dict[str, Any]:
    return main_mod.load_settings()


@pytest.fixture(scope="module")
def baseline(settings: dict[str, Any]) -> Behaviour:
    return load_baseline(settings, baseline_dir=_BASELINE_DIR)


# ----------------------------------------------------------------------
# Định nghĩa chỉ số phải KHỚP con số §C.1
# ----------------------------------------------------------------------


def test_dinh_nghia_khop_con_so_trong_C1(baseline: Behaviour) -> None:
    """§C.1 nêu 30.6/18.1/16.5/34.8 %, 32.3 %, 11.68 % — đo bởi ai đó,
    trước khi file này tồn tại. Tái tạo đúng ba con số ấy là bằng chứng
    DUY NHẤT cho thấy `measure()` đang đo cùng thứ họ đã đo.

    Không có phép kiểm này, mọi "lệch so với baseline" có thể là lệch giữa
    hai định nghĩa chứ không phải giữa hai hành vi — và không ai tách được
    hai thứ đó ra sau khi cảnh báo đã bắn.
    """
    assert baseline.allocation_mix_pct == (30.6, 18.1, 16.5, 34.8)
    assert baseline.rebalance_rate_pct == 32.3
    assert baseline.fee_pct_of_gross == 11.68


def test_bon_muc_doc_tu_settings_khong_hardcode(settings: dict[str, Any]) -> None:
    assert nominal_allocation_levels(settings) == (0.30, 0.50, 0.60, 0.95)


def test_bien_ro_la_trung_diem() -> None:
    assert allocation_bin_edges((0.30, 0.50, 0.60, 0.95)) == pytest.approx((0.40, 0.55, 0.775))


def test_doi_settings_thi_doi_bien_ro(settings: dict[str, Any]) -> None:
    """Baseline đọc từ FILE, mức allocation đọc từ CONFIG — cả hai đều
    không được hardcode. Nếu ai đó chỉnh `high_vol_allocation`, rổ phải
    dịch theo."""
    sua = {**settings, "strategy": {**settings["strategy"], "high_vol_allocation": 0.45}}

    assert nominal_allocation_levels(sua) == (0.30, 0.45, 0.60, 0.95)


# ----------------------------------------------------------------------
# SANITY CHECK nghiệm thu — baseline không được tự báo động
# ----------------------------------------------------------------------


def test_baseline_khong_tu_bao_dong(settings: dict[str, Any], baseline: Behaviour) -> None:
    """Mục nghiệm thu §C.1: "chạy trên chính dữ liệu backtest Phase 7 →
    không cảnh báo gì".

    Chạy `compare()` với current == baseline. Bất kỳ cờ `alert` nào bật ở
    đây nghĩa là một ngưỡng đã bị đặt chặt hơn chính sai số của baseline —
    tức là chỉ báo đó sẽ đỏ vĩnh viễn ngay từ ngày đầu.
    """
    ket_qua = compare(baseline, baseline, DriftThresholds.from_settings(settings))

    bao_dong = [m.name for m in ket_qua if m.alert]
    assert bao_dong == [], f"baseline tự báo động ở: {bao_dong}"


def test_baseline_qua_run_khong_bao_dong(settings: dict[str, Any], tmp_path: Path) -> None:
    """Như trên nhưng qua ĐƯỜNG THẬT `run()` (đọc CSV, chuẩn hoá cột, cắt
    cửa sổ, ghi file) thay vì gọi thẳng `compare()` — hai đường có thể lệch
    nhau ở tầng nối dây."""
    regime = pd.read_csv(_BASELINE_DIR / "regime_history.csv")
    cost = pd.read_csv(_BASELINE_DIR / "cost_report.csv")
    n_bars = len(pd.read_csv(_BASELINE_DIR / "equity_curve.csv"))

    payload = run(
        settings,
        bars=regime,
        path=tmp_path / "drift.json",
        baseline_dir=_BASELINE_DIR,
        rebalance_rate_pct=round(float(cost["n_rebalances"].iloc[0]) / n_bars * 100, 1),
        fee_pct_of_gross=round(float(cost["cost_pct_of_gross_profit"].iloc[0]), 2),
        # Cửa sổ = TOÀN BỘ baseline. Để mặc định 30 bar thì "hiện tại" là
        # 30 bar cuối của baseline chứ không phải baseline — so một lát cắt
        # với trung bình 6 năm, tức là một phép so khác hẳn (xem
        # `test_dai_ha_false_positive_tu_99_xuong_duoi_5`).
        window_days=len(regime),
    )

    bao_dong = [m["name"] for m in payload["metrics"] if m["alert"]]
    assert bao_dong == [], f"baseline tự báo động qua run(): {bao_dong}"


# ----------------------------------------------------------------------
# Từng ngưỡng §C.1, mỗi cái một test
# ----------------------------------------------------------------------


def _lech(baseline: Behaviour, **doi: Any) -> Behaviour:
    from dataclasses import replace

    return replace(baseline, **doi)


def _bao_dong(ket_qua: list[Any], ten_chua: str) -> bool:
    return next(m.alert for m in ket_qua if ten_chua in m.name)


def test_allocation_lech_qua_15_diem_thi_bao(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    lech = _lech(baseline, allocation_mix_pct=(46.0, 18.1, 16.5, 19.4))

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "allocation")


def test_allocation_lech_15_diem_van_im(settings: dict[str, Any], baseline: Behaviour) -> None:
    """Biên: "> 15" nghĩa là đúng 15 chưa báo."""
    lech = _lech(baseline, allocation_mix_pct=(45.6, 18.1, 16.5, 19.8))

    assert not _bao_dong(
        compare(lech, baseline, DriftThresholds.from_settings(settings)), "allocation"
    )


def test_mot_muc_tang_mot_muc_giam_van_bao(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """BẤT KỲ mức nào lệch quá ngưỡng, không phải tổng hay trung bình: một
    mức +16 và một mức -16 triệt tiêu nhau ở tổng, trong khi đó chính là
    thay đổi hành vi."""
    lech = _lech(baseline, allocation_mix_pct=(46.6, 18.1, 16.5, 18.8))

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "allocation")


def test_rebalance_lech_qua_10_diem_thi_bao(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    lech = _lech(baseline, rebalance_rate_pct=45.0)

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "rebalance")


def test_phi_la_nguong_TUYET_DOI_khong_so_baseline(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """§C.1 cố ý: phí ăn > 20% lợi nhuận gộp là xấu BẤT KỂ baseline từng là
    bao nhiêu. 25% phải báo dù chỉ lệch 13 điểm so với baseline 11.68 —
    một phép so tương đối sẽ bỏ lọt."""
    lech = _lech(baseline, fee_pct_of_gross=25.0)

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "Phí")


def test_phi_duoi_20_thi_im_du_gap_doi_baseline(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    lech = _lech(baseline, fee_pct_of_gross=19.0)

    assert not _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "Phí")


def test_flicker_cao_hon_baseline_thi_bao(settings: dict[str, Any], baseline: Behaviour) -> None:
    """Baseline flicker = 0 (backtest Phase 7 không flicker lần nào), nên
    "2× của 0" = 0 và mọi flicker > 0 là thay đổi hành vi thật."""
    assert baseline.flicker_rate_pct == 0.0
    lech = _lech(baseline, flicker_rate_pct=0.5)

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "Flicker")


def test_trend_gate_lech_qua_20_diem_thi_bao(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    lech = _lech(baseline, trend_gate_block_pct=baseline.trend_gate_block_pct + 21)

    assert _bao_dong(compare(lech, baseline, DriftThresholds.from_settings(settings)), "trend gate")


def test_nguong_doc_tu_settings(settings: dict[str, Any]) -> None:
    th = DriftThresholds.from_settings(settings)

    assert th.allocation_pts == 15.0
    assert th.rebalance_pts == 10.0
    assert th.fee_pct_of_gross_max == 20.0
    assert th.flicker_multiple == 2.0
    assert th.trend_gate_block_pts == 20.0


def test_settings_rong_thi_dung_mac_dinh() -> None:
    assert DriftThresholds.from_settings({}) == DriftThresholds()


# ----------------------------------------------------------------------
# Cửa sổ chưa đủ mẫu
# ----------------------------------------------------------------------


def test_cua_so_chua_du_thi_khong_bao_dong(settings: dict[str, Any], baseline: Behaviour) -> None:
    """Forward test bắt đầu 2026-08-06; cửa sổ 30 ngày phải tới ~2026-09-05
    mới đầy. Trước đó 9 bar toàn allocation 0.30 cho "100 / 0 / 0 / 0 %" —
    đỏ rực, không phải vì trôi lệch mà vì mẫu quá nhỏ. Một chỉ báo đỏ suốt
    tháng đầu dạy người đọc bỏ qua nó."""
    lech = _lech(baseline, n_bars=9, allocation_mix_pct=(100.0, 0.0, 0.0, 0.0))

    ket_qua = compare(
        lech, baseline, DriftThresholds.from_settings(settings), window_complete=False
    )

    assert [m.name for m in ket_qua if m.alert] == []


def test_cua_so_chua_du_van_IN_gia_tri_do_duoc(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """Tắt CẢNH BÁO, không tắt QUAN SÁT — người vận hành vẫn phải thấy con
    số, kèm lý do vì sao nó chưa được dùng để báo động."""
    lech = _lech(baseline, n_bars=9, allocation_mix_pct=(100.0, 0.0, 0.0, 0.0))

    ket_qua = compare(
        lech, baseline, DriftThresholds.from_settings(settings), window_complete=False
    )
    dong = next(m for m in ket_qua if "allocation" in m.name)

    assert "100.0" in dong.current
    assert "9/30" in dong.current


def test_warning_count_KHONG_bi_tat_boi_cua_so(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """`warning_count` đọc trên toàn bộ lịch sử retrain, độc lập với cửa sổ
    30 ngày — nó có đủ bằng chứng riêng nên không bị tắt cùng."""
    ket_qua = compare(
        baseline,
        baseline,
        DriftThresholds.from_settings(settings),
        warning_counts=list(range(10, 10 + WARNING_TREND_LEN * 10, 10)),
        window_complete=False,
    )

    assert _bao_dong(ket_qua, "warning_count")


def test_recent_window_cat_theo_SO_BAR(settings: dict[str, Any]) -> None:
    """Cắt theo số bar chứ không theo dấu thời gian: một khoảng đứt (bot
    dừng ba ngày — đã xảy ra 2026-08-06..08) làm cửa sổ theo ngày chỉ còn
    27 bar, và mọi tỷ lệ tính trên nó lệch mà không có gì báo."""
    bars = pd.DataFrame({"x": range(100)})

    assert len(recent_window(bars)) == WINDOW_DAYS


# ----------------------------------------------------------------------
# `warning_count` — xu hướng tăng đơn điệu
# ----------------------------------------------------------------------


def test_du_so_lan_tang_lien_tiep_thi_dung() -> None:
    """Dùng `WARNING_TREND_LEN` chứ không viết cứng số 3: giá trị đó được
    HIỆU CHỈNH bằng đo (3 -> 4 ngày 2026-08-14, docs/DECISIONS.md "ĐO #2"),
    và một test viết cứng sẽ biến mỗi lần hiệu chỉnh thành một lần sửa
    test — tức là test đang ghim con số thay vì ghim HÀNH VI."""
    du = [0] + list(range(1, WARNING_TREND_LEN + 1))

    assert monotonic_increasing_tail(du)


def test_thieu_mot_lan_thi_chua_du() -> None:
    thieu = list(range(1, WARNING_TREND_LEN))

    assert not monotonic_increasing_tail(thieu)


def test_bang_nhau_khong_phai_tang() -> None:
    """Tăng ĐƠN ĐIỆU NGẶT: 10 -> 10 -> 11 không phải xu hướng tăng ba lần."""
    assert not monotonic_increasing_tail([10, 10, 11])


def test_chi_xet_duoi_cuoi() -> None:
    """Tăng rồi giảm rồi tăng lại: chỉ `WARNING_TREND_LEN` giá trị CUỐI
    mới tính."""
    tang = list(range(1, WARNING_TREND_LEN + 1))

    assert not monotonic_increasing_tail([*tang, 99, 0])
    assert monotonic_increasing_tail([99, *tang])


def test_chua_du_du_lieu_thi_khong_bao() -> None:
    """"Chưa đủ bằng chứng" -> im lặng, KHÔNG phải "cứ cảnh báo cho chắc".
    Một cảnh báo phát khi chưa đủ bằng chứng dạy người đọc bỏ qua nó."""
    assert not monotonic_increasing_tail(list(range(1, WARNING_TREND_LEN)))
    assert not monotonic_increasing_tail([])


def test_warning_counts_chi_lay_bar_co_retrain() -> None:
    bars = pd.DataFrame(
        {
            "hmm_retrained": ["False", "True", "False", "True"],
            "warning_count": [0, 100, 0, 200],
        }
    )

    assert retrain_warning_counts(bars) == [100.0, 200.0]


def test_warning_counts_thieu_cot_thi_rong() -> None:
    """Schema v1 của `forward/log.csv` không có `warning_count` — "không
    có" là trạng thái HỢP LỆ, không phải lỗi (xem `forward/SCHEMA.md`)."""
    assert retrain_warning_counts(pd.DataFrame({"date": ["2026-08-06"]})) == []


def test_warning_counts_None_thi_rong() -> None:
    assert retrain_warning_counts(None) == []


# ----------------------------------------------------------------------
# Chuẩn hoá tên cột — backtest và forward KHÔNG cùng schema
# ----------------------------------------------------------------------


def test_chuan_hoa_ten_cot_backtest() -> None:
    df = pd.DataFrame(
        {"final_allocation_pct": [0.3], "strategy_target_allocation_pct": [0.95]}
    )

    ra = normalize_bars(df)

    assert "final_allocation" in ra.columns
    assert "hmm_allocation" in ra.columns


def test_chuan_hoa_ten_cot_forward() -> None:
    df = pd.DataFrame({"final_allocation": [0.3], "hmm_allocation": [0.95], "hmm_retrained": [True]})

    ra = normalize_bars(df)

    assert "retrained" in ra.columns
    assert list(ra["final_allocation"]) == [0.3]


def test_do_duoc_ca_hai_schema_cho_cung_ket_qua(settings: dict[str, Any]) -> None:
    """Cùng dữ liệu, hai bộ tên cột, phải cho cùng số đo. Nếu không, mọi
    so sánh baseline-với-forward là so hai thứ khác nhau."""
    edges = allocation_bin_edges(nominal_allocation_levels(settings))
    chung = {"trend_gate_cap": [0.3, 1.0], "is_flickering": [False, False]}
    kieu_backtest = pd.DataFrame(
        {**chung, "final_allocation_pct": [0.3, 0.95], "strategy_target_allocation_pct": [0.95, 0.95]}
    )
    kieu_forward = pd.DataFrame(
        {**chung, "final_allocation": [0.3, 0.95], "hmm_allocation": [0.95, 0.95]}
    )

    assert measure(kieu_backtest, edges=edges) == measure(kieu_forward, edges=edges)


# ----------------------------------------------------------------------
# Rổ allocation
# ----------------------------------------------------------------------


def test_mix_tong_bang_100() -> None:
    mix = allocation_mix(pd.Series([0.3, 0.5, 0.6, 0.95]), (0.40, 0.55, 0.775))

    assert sum(mix) == pytest.approx(100.0)
    assert mix == (25.0, 25.0, 25.0, 25.0)


def test_mix_rong_thi_toan_khong() -> None:
    assert allocation_mix(pd.Series([], dtype=float), (0.40, 0.55, 0.775)) == (0.0, 0.0, 0.0, 0.0)


def test_gia_tri_dao_quanh_muc_van_vao_dung_ro() -> None:
    """0.9503 (trôi giá quanh mức 0.95 giữa hai lần rebalance) phải nằm
    cùng rổ với 0.95 — đó là lý do biên là TRUNG ĐIỂM chứ không phải chính
    các mức."""
    assert allocation_mix(pd.Series([0.9503, 0.95]), (0.40, 0.55, 0.775)) == (0.0, 0.0, 0.0, 100.0)


# ----------------------------------------------------------------------
# Ghi file + hợp đồng với panel dashboard
# ----------------------------------------------------------------------


def test_drift_json_dung_hop_dong_panel(settings: dict[str, Any], tmp_path: Path) -> None:
    """Bên GHI và bên ĐỌC phải khớp. Đọc bằng CHÍNH hàm của panel thay vì
    khẳng định lại schema bằng tay — khẳng định tay là một bản sao của hợp
    đồng, và bản sao sẽ trôi lệch."""
    from monitoring.dashboard import DRIFT_OK, load_drift_panel_data

    target = tmp_path / "drift.json"
    run(settings, bars=pd.read_csv(_BASELINE_DIR / "regime_history.csv"), path=target,
        baseline_dir=_BASELINE_DIR)

    data = load_drift_panel_data(target)

    assert data.status == DRIFT_OK
    assert len(data.metrics) == 6
    assert data.generated_at_utc is not None


def test_panel_va_drift_dung_CUNG_duong_dan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bên ghi và bên đọc phải trỏ cùng một file. Tách hai đường ra nghĩa
    là panel hiện "chưa có dữ liệu drift" dù `drift.py` đã chạy xong, và
    người debug đi tìm nhầm chỗ."""
    from monitoring.dashboard import _default_drift_path

    monkeypatch.setenv("STATE_DIR", "/tmp/kiem-duong-dan")

    assert _default_drift_path() == drift.default_drift_path()


def test_drift_json_khong_nam_trong_cay_ma_nguon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lệch có chủ ý khỏi `monitoring/state/drift.json` của §C.1 — cùng lý
    do `status.json` đã phải chuyển đi ngày 2026-08-08."""
    monkeypatch.delenv("STATE_DIR", raising=False)

    assert "monitoring" not in drift.default_drift_path().parts


def test_write_drift_khong_raise_khi_khong_ghi_duoc(tmp_path: Path) -> None:
    chan = tmp_path / "khong-phai-thu-muc"
    chan.write_text("x", encoding="utf-8")

    write_drift({"metrics": []}, chan / "drift.json")


def test_write_drift_khong_de_lai_tmp(tmp_path: Path) -> None:
    target = tmp_path / "drift.json"

    write_drift({"metrics": []}, target)

    assert list(tmp_path.iterdir()) == [target]
    assert json.loads(target.read_text(encoding="utf-8")) == {"metrics": []}


# ----------------------------------------------------------------------
# CHỈ ĐỌC `forward/`
# ----------------------------------------------------------------------


def test_drift_khong_ghi_vao_forward() -> None:
    """Ràng buộc nghiệm thu: `grep -rn "forward/" monitoring/` chỉ có thao
    tác ĐỌC. `forward/` là bằng chứng của một thí nghiệm 12 tháng đang
    chạy — ghi vào đó là làm hỏng thí nghiệm."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "drift.py").read_text(
        encoding="utf-8"
    )

    for cam in ("open(", ".to_csv(", ".write_text(", "log_v2.csv"):
        assert cam not in src.split('"""')[-1] or "forward" not in src, (
            f"drift.py có vẻ đang ghi vào forward/ qua {cam!r}"
        )
    assert "load_all_bars" in src, "phải đọc forward qua forward.runner.load_all_bars (chỉ đọc)"


def test_forward_hong_khong_lam_hong_nam_chi_so_con_lai(
    settings: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forward test là thí nghiệm đang chạy; file lỗi/chưa có không được
    phép làm hỏng năm chỉ số không liên quan tới nó."""

    def _no(*_a: Any, **_k: Any) -> Any:
        raise OSError("giả lập forward log hỏng")

    monkeypatch.setattr(drift, "_load_forward_bars", lambda: None)
    payload = run(settings, path=tmp_path / "drift.json", baseline_dir=_BASELINE_DIR)

    assert len(payload["metrics"]) == 6


def test_so_chi_so_dung_sau_nhu_bang_C1(settings: dict[str, Any], baseline: Behaviour) -> None:
    ket_qua = compare(baseline, baseline, DriftThresholds.from_settings(settings))

    assert len(ket_qua) == 6


# ----------------------------------------------------------------------
# Dải bình thường — điều kiện Ý NGHĨA THỐNG KÊ bên cạnh ngưỡng §C.1
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def bands(settings: dict[str, Any]) -> Any:
    return load_baseline_bands(settings, baseline_dir=_BASELINE_DIR)


def _cua_so_baseline(settings: dict[str, Any]) -> list[Behaviour]:
    regime = normalize_bars(pd.read_csv(_BASELINE_DIR / "regime_history.csv"))
    edges = allocation_bin_edges(nominal_allocation_levels(settings))
    return [
        measure(regime.iloc[i - WINDOW_DAYS : i], edges=edges)
        for i in range(WINDOW_DAYS, len(regime) + 1)
    ]


def test_nguong_C1_mot_minh_bao_dong_gan_nhu_moi_cua_so(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """ĐO, không suy đoán: chỉ dùng ngưỡng §C.1 thì gần như MỌI cửa sổ 30
    bar của chính baseline đều báo động.

    Nguyên nhân: phân bố allocation trên cửa sổ 30 bar có độ lệch chuẩn
    ~41 điểm %, còn ngưỡng là 15 điểm. Ngưỡng nằm sâu trong nhiễu tự nhiên.
    Đây là khiếm khuyết của §C.1 như đã viết, không phải lỗi cài đặt —
    test này giữ bằng chứng cho điều đó.
    """
    th = DriftThresholds.from_settings(settings)
    cua_so = _cua_so_baseline(settings)

    bao = sum(1 for c in cua_so if any(m.alert for m in compare(c, baseline, th)))

    assert bao / len(cua_so) > 0.9, "khiếm khuyết §C.1 đã biến mất — đọc lại docstring Bands"


def test_dai_ha_false_positive_tu_99_xuong_duoi_5(
    settings: dict[str, Any], baseline: Behaviour, bands: Any
) -> None:
    """Với thêm điều kiện "nằm ngoài dải p1–p99 của cửa sổ cùng kích
    thước", tỷ lệ báo động sai trên chính baseline rơi xuống ~1%.

    Ngưỡng §C.1 KHÔNG bị nới: nó vẫn là điều kiện CẦN. Dải chỉ thêm điều
    kiện thứ hai — đủ LỚN (ngưỡng) và đủ HIẾM (dải)."""
    th = DriftThresholds.from_settings(settings)
    cua_so = _cua_so_baseline(settings)

    bao = sum(1 for c in cua_so if any(m.alert for m in compare(c, baseline, th, bands=bands)))

    assert bao / len(cua_so) < 0.05, f"vẫn còn {bao / len(cua_so) * 100:.1f}% báo động sai"


def test_dai_khong_lam_bo_lot_lech_that(
    settings: dict[str, Any], baseline: Behaviour, bands: Any
) -> None:
    """Dải không được biến cảnh báo thành vô hiệu: một giá trị nằm ngoài
    dải p1–p99 VÀ vượt ngưỡng §C.1 vẫn phải báo.

    Dùng rổ thứ BA (dải p1–p99 = 0–98.0): đặt nó ở 100 % là nằm ngoài dải.
    Rổ 1/2/4 KHÔNG dùng được cho phép kiểm này vì dải của chúng phủ trọn
    0–100 — xem `test_dai_allocation_gan_nhu_phu_tron_o_cua_so_30_bar`.
    """
    from dataclasses import replace

    lech = replace(baseline, allocation_mix_pct=(0.0, 0.0, 100.0, 0.0))

    ket_qua = compare(lech, baseline, DriftThresholds.from_settings(settings), bands=bands)

    assert _bao_dong(ket_qua, "allocation")


def test_dai_allocation_gan_nhu_phu_tron_o_cua_so_30_bar(bands: Any) -> None:
    """PHÁT HIỆN, ghi lại chứ không giấu: ở cửa sổ 30 bar, dải "bình
    thường" của ba trong bốn rổ phủ trọn 0–100 %.

    Nghĩa là chỉ số "phân bố allocation" gần như KHÔNG mang thông tin ở
    kích thước cửa sổ mà §C.1 quy định — 30 bar của một chiến lược chuyển
    chế độ thì rơi gọn vào một hai rổ, và điều đó bình thường.

    Bề rộng dải p1–p99 theo kích thước cửa sổ (đo trên baseline Phase 7):

        30 bar : [100.0, 100.0,  98.0, 100.0]
        90 bar : [100.0,  55.6,  70.0,  95.6]
        182 bar: [100.0,  40.1,  51.1,  92.9]
        365 bar: [ 87.2,  35.9,  30.0,  81.9]

    Chỉ số này bắt đầu có nghĩa từ khoảng 180 bar. Xem `docs/DECISIONS.md`,
    mục "Ngưỡng drift §C.1 quá chặt so với nhiễu cửa sổ 30 bar".
    """
    rong = [hi - lo for lo, hi in bands.allocation_mix]

    assert sum(1 for r in rong if r >= 99.9) >= 3


def test_dai_tinh_tren_dung_kich_thuoc_cua_so(settings: dict[str, Any]) -> None:
    """Dải phải đo trên cửa sổ CÙNG kích thước với cửa sổ sẽ được so. Một
    dải tính trên cửa sổ 7 bar rồi đem so cửa sổ 30 bar là quay lại đúng
    lỗi so-khác-kích-thước-mẫu mà nó sinh ra để sửa."""
    hep = load_baseline_bands(settings, baseline_dir=_BASELINE_DIR, window_days=7)
    rong = load_baseline_bands(settings, baseline_dir=_BASELINE_DIR, window_days=365)

    assert hep != rong


def test_run_luon_truyen_dai(settings: dict[str, Any], tmp_path: Path) -> None:
    """Đường THẬT phải dùng dải. Không có nó, `drift.json` đỏ 99% thời
    gian và người vận hành sẽ ngừng đọc."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "drift.py").read_text(
        encoding="utf-8"
    )

    assert "bands=bands" in src


def test_trend_gate_bang_nhau_KHONG_tinh_la_chan(settings: dict[str, Any]) -> None:
    """Trần 0.95 trên một signal 0.95 không giới hạn gì cả. Đếm nó vào sẽ
    làm chỉ số này bão hoà gần 100 % trong mọi thị trường tăng — lúc đó nó
    không còn phân biệt được "trend gate đang chặn" với "trend gate đang
    mở hết cỡ".

    Biên `<` vs `<=`: hai ký tự, hai chỉ số hoàn toàn khác nhau.
    """
    edges = allocation_bin_edges(nominal_allocation_levels(settings))
    bars = pd.DataFrame(
        {
            "final_allocation": [0.95, 0.30],
            "hmm_allocation": [0.95, 0.95],
            "trend_gate_cap": [0.95, 0.30],  # bar 1: BẰNG NHAU, bar 2: chặn thật
            "is_flickering": [False, False],
        }
    )

    assert measure(bars, edges=edges).trend_gate_block_pct == 50.0


# ----------------------------------------------------------------------
# Ngưỡng đã hiệu chỉnh bằng ĐO (CLAUDE.md #18) — ghim giá trị + tỷ lệ FP
# ----------------------------------------------------------------------


def test_warning_trend_len_da_hieu_chinh() -> None:
    """3 -> 4 (docs/DECISIONS.md "ĐO #2"). Dưới giả thuyết không (iid),
    P(L giá trị liên tiếp tăng đơn điệu) = 1/L!:

        L=3 -> 1/6   -> 1 báo động giả mỗi  6.0 tuần
        L=4 -> 1/24  -> 1 báo động giả mỗi 24.0 tuần

    Forward test 12 tháng ~ 52 lần retrain: L=3 cho ~8.7 báo động giả
    trong MỘT thí nghiệm, nhiều hơn số sự kiện thật nó quan sát được.
    """
    assert WARNING_TREND_LEN == 4


def test_ty_le_bao_dong_gia_cua_warning_trend_khop_1_tren_L_giai_thua() -> None:
    """Đo lại chính con số đã dùng để chọn ngưỡng, bằng mô phỏng.

    Không tin bảng trong DECISIONS.md: nếu ai đó đổi
    `monotonic_increasing_tail` thành "không ngặt" (`<=`), tỷ lệ báo động
    giả nhảy vọt trong khi mọi test hành vi khác vẫn xanh.
    """
    import math

    import numpy as np

    rng = np.random.default_rng(0)
    L = WARNING_TREND_LEN
    x = rng.random((20_000, L))
    ty_le = float(np.mean([monotonic_increasing_tail(list(hang)) for hang in x]))

    assert ty_le == pytest.approx(1 / math.factorial(L), abs=0.01)


def test_large_pnl_alert_pct_da_hieu_chinh() -> None:
    """2.0 -> 2.93 = p90 của |daily drawdown| trên bar lỗ
    (docs/DECISIONS.md "ĐO #1"). 2.0 nằm ở p82.4 và phát 32 lần/năm — gấp
    3.5 lần chính circuit breaker mà nó cảnh báo trước."""
    assert main_mod.load_settings()["monitoring"]["large_pnl_alert_pct"] == 2.93


def test_large_pnl_van_som_hon_circuit_breaker() -> None:
    """Bất biến của thiết kế, không phải của con số: cảnh báo "chú ý" phải
    kích hoạt TRƯỚC khi breaker can thiệp vào allocation. Một lần hiệu
    chỉnh đẩy nó vượt 3.85 sẽ làm nó vô dụng."""
    cfg = main_mod.load_settings()

    assert (
        cfg["monitoring"]["large_pnl_alert_pct"]
        < cfg["risk"]["circuit_breaker"]["daily_dd_reduce_pct"]
    )


def test_cua_so_allocation_dai_hon_cua_so_chung() -> None:
    """365 vs 30 (docs/DECISIONS.md "ĐO #3"). Ở cửa sổ 30–182 bar, chỉ số
    phân bố allocation KHÔNG phân biệt được bot hỏng hoàn toàn với hoạt
    động bình thường — đo trực tiếp, không suy từ bề rộng dải."""
    from monitoring.drift import ALLOCATION_WINDOW_DAYS

    assert ALLOCATION_WINDOW_DAYS == 365
    assert ALLOCATION_WINDOW_DAYS > WINDOW_DAYS


def test_cua_so_365_bat_duoc_bot_ket_mot_ro(settings: dict[str, Any], baseline: Behaviour) -> None:
    """SỨC PHÁT HIỆN — thứ mục DECISIONS đầu tiên đã bỏ sót không đo.

    Một bot kẹt hoàn toàn ở rổ allocation thấp nhất là hỏng rõ ràng. Ở cửa
    sổ 30 bar nó KHÔNG bị bắt (test dưới); ở 365 bar thì bị.
    """
    from dataclasses import replace

    bands = load_baseline_bands(settings, baseline_dir=_BASELINE_DIR, window_days=365)
    ket = replace(baseline, allocation_mix_pct=(100.0, 0.0, 0.0, 0.0))

    assert _bao_dong(
        compare(ket, baseline, DriftThresholds.from_settings(settings), bands=bands), "allocation"
    )


def test_cua_so_30_KHONG_bat_duoc_bot_ket_mot_ro(
    settings: dict[str, Any], baseline: Behaviour, bands: Any
) -> None:
    """Mặt kia của phép đo, giữ làm bằng chứng: ở cửa sổ 30 bar cùng một
    hỏng hóc KHÔNG bị bắt — vì "100 % số bar ở mức thấp nhất trong 30
    ngày" đúng là chuyện thường của chiến lược này (một đoạn bear 30 ngày
    cho hệt như vậy).

    Đây là lý do cửa sổ phải dài, và là thứ khiến câu "chỉ số này bắt đầu
    có nghĩa từ ~180 bar" trong bản DECISIONS đầu tiên là SAI.
    """
    from dataclasses import replace

    ket = replace(baseline, allocation_mix_pct=(100.0, 0.0, 0.0, 0.0))

    assert not _bao_dong(
        compare(ket, baseline, DriftThresholds.from_settings(settings), bands=bands), "allocation"
    )


def test_cua_so_allocation_co_co_du_mau_RIENG(
    settings: dict[str, Any], baseline: Behaviour
) -> None:
    """Dùng chung cờ "đủ mẫu" với năm chỉ số kia sẽ bật cảnh báo phân bố
    allocation từ ngày thứ 30 — 335 ngày trước khi nó có đủ dữ liệu."""
    from dataclasses import replace

    lech = replace(baseline, allocation_mix_pct=(100.0, 0.0, 0.0, 0.0))

    ket_qua = compare(
        lech,
        baseline,
        DriftThresholds.from_settings(settings),
        window_complete=True,  # 30 bar ĐÃ đủ cho năm chỉ số kia
        allocation_window_complete=False,  # nhưng 365 bar thì chưa
    )

    assert not _bao_dong(ket_qua, "allocation")


def test_run_dung_hai_cua_so(settings: dict[str, Any], tmp_path: Path) -> None:
    payload = run(
        settings,
        bars=pd.read_csv(_BASELINE_DIR / "regime_history.csv"),
        path=tmp_path / "drift.json",
        baseline_dir=_BASELINE_DIR,
    )

    assert payload["window_days"] == WINDOW_DAYS
    assert payload["allocation_window_days"] == 365
