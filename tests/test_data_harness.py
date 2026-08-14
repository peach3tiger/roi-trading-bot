"""Phase 12d §B — `monitoring/data_harness.py`.

Phép kiểm quan trọng nhất ở đây là `test_bien_dong_THAT_khong_bi_khoa`.
Mọi phép kiểm khác chỉ chứng minh harness bắt được dữ liệu hỏng; phép kiểm
đó chứng minh nó **không** khoá bot đúng lúc thị trường sập — tức là nó
không phá chính hành vi phòng vệ đã xây bảy phase để có.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from monitoring.alerts import AlertType
from monitoring.data_harness import (
    LARGE_MOVE_PCT,
    MOVE_BAD_DATA,
    MOVE_REAL,
    MOVE_UNVERIFIED,
    check_freshness,
    check_integrity,
    classify_price_move,
    lock_active,
    lock_path,
    run_integrity_check,
    write_lock,
)

_IDX = pd.date_range("2026-08-01", periods=5, freq="D", tz="UTC")


def _bars(**doi: Any) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 13.0, 14.0],
            "high": [12.0, 13.0, 14.0, 15.0, 16.0],
            "low": [9.0, 10.0, 11.0, 12.0, 13.0],
            "close": [11.0, 12.0, 13.0, 14.0, 15.0],
            "volume": [100.0] * 5,
            "trade_count": [50.0] * 5,
        },
        index=_IDX,
    )
    for cot, gia_tri in doi.items():
        df[cot] = gia_tri
    return df


def _checks(bars: pd.DataFrame) -> set[str]:
    return {v.check for v in check_integrity(bars)}


# ----------------------------------------------------------------------
# §B.2 — tính đúng đắn
# ----------------------------------------------------------------------


def test_du_lieu_sach_khong_vi_pham() -> None:
    assert check_integrity(_bars()) == []


def test_close_lon_hon_high() -> None:
    """Nghiệm thu 12d #3. `close > high` là bar hỏng rõ ràng."""
    bars = _bars()
    bars.loc[_IDX[2], "close"] = 999.0

    assert "low <= close <= high" in _checks(bars)


def test_open_ngoai_khoang() -> None:
    bars = _bars()
    bars.loc[_IDX[1], "open"] = 0.5

    assert "low <= open <= high" in _checks(bars)


def test_low_lon_hon_high() -> None:
    bars = _bars()
    bars.loc[_IDX[3], "low"] = 99.0

    assert "low <= high" in _checks(bars)


def test_volume_am() -> None:
    bars = _bars()
    bars.loc[_IDX[0], "volume"] = -1.0

    assert "volume >= 0" in _checks(bars)


def test_volume_bang_khong_LA_loi() -> None:
    """Với BTC/USDT bar NGÀY, volume 0 không phải "thị trường trầm lắng" —
    nó chắc chắn là lỗi dữ liệu."""
    bars = _bars()
    bars.loc[_IDX[1], "volume"] = 0.0

    assert "volume != 0" in _checks(bars)


def test_trade_count_am() -> None:
    bars = _bars()
    bars.loc[_IDX[2], "trade_count"] = -5.0

    assert "trade_count >= 0" in _checks(bars)


def test_thieu_bar() -> None:
    bars = _bars().drop(_IDX[2])

    vi_pham = [v for v in check_integrity(bars) if v.check == "không thiếu bar"]

    assert len(vi_pham) == 1
    assert "thiếu 1 bar" in vi_pham[0].detail


def test_timestamp_trung() -> None:
    bars = _bars()
    trung = pd.concat([bars, bars.iloc[[2]]]).sort_index()

    assert "timestamp không trùng" in _checks(trung)


def test_bao_cao_MOI_vi_pham_khong_dung_o_cai_dau() -> None:
    """Khi dữ liệu hỏng, biết nó hỏng ở ba chỗ hay một chỗ là thông tin
    khác nhau về nguyên nhân."""
    bars = _bars()
    bars.loc[_IDX[1], "close"] = 999.0
    bars.loc[_IDX[2], "volume"] = 0.0
    bars.loc[_IDX[3], "low"] = 99.0

    assert len(_checks(bars)) >= 3


def test_vi_pham_mang_du_du_kien_de_tai_lap() -> None:
    """Một dòng "dữ liệu sai" không nói bar nào và giá trị bao nhiêu buộc
    người vận hành tự đi tìm lại — trong lúc bot đang dừng."""
    bars = _bars()
    bars.loc[_IDX[2], "close"] = 999.0

    v = next(v for v in check_integrity(bars) if v.check == "low <= close <= high")

    assert "2026-08-03" in v.bar
    assert v.values["close"] == 999.0
    assert "999" in v.detail


def test_khung_rong_khong_vi_pham() -> None:
    assert check_integrity(pd.DataFrame()) == []


# ----------------------------------------------------------------------
# §B.1 — độ tươi
# ----------------------------------------------------------------------


_NOW = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)


def test_bar_moi_thi_tuoi() -> None:
    assert check_freshness(_NOW - timedelta(hours=12), now=_NOW) is None


def test_bar_cu_hon_1_5_chu_ky_thi_khong_tuoi() -> None:
    assert check_freshness(_NOW - timedelta(days=2), now=_NOW) is not None


def test_dung_1_5_chu_ky_van_tuoi() -> None:
    assert check_freshness(_NOW - timedelta(hours=36), now=_NOW) is None


def test_khong_co_bar_nao_LA_vi_pham() -> None:
    """KHÔNG phải "chưa biết": một nguồn dữ liệu rỗng ở tầng giám sát
    nghĩa là không có gì để giao dịch trên đó."""
    assert check_freshness(None, now=_NOW) is not None


# ----------------------------------------------------------------------
# §B.3 — điểm khác biệt giữa "bảo vệ" và "tự bắn vào chân"
# ----------------------------------------------------------------------


def test_bien_dong_THAT_khong_bi_khoa() -> None:
    """NGHIỆM THU 12d #4, và là phép kiểm quan trọng nhất file này.

    BTC đã từng giảm ~40% trong một ngày (12/03/2020). Một cú sập thật là
    lúc CẦN bot hoạt động nhất — đó là lúc trend gate hạ trần, HMM chuyển
    CRASH, risk manager cắt size. Khoá bot đúng lúc đó là cách chắc chắn
    để bỏ lỡ chính hành vi phòng vệ đã xây bảy phase để có.
    """
    assert classify_price_move(Decimal("-35"), Decimal("100"), Decimal("100.5")) == MOVE_REAL


def test_bien_dong_GIA_thi_khoa() -> None:
    """Nghiệm thu 12d #5: nguồn phụ báo 155 trong khi nguồn chính báo 100."""
    assert classify_price_move(Decimal("-35"), Decimal("100"), Decimal("155")) == MOVE_BAD_DATA


def test_khong_lay_duoc_nguon_hai_thi_khoa_vi_than_trong() -> None:
    """Nhãn KHÁC `MOVE_BAD_DATA` dù cùng dẫn tới khoá: sửa kết nối nguồn
    phụ và điều tra dữ liệu là hai việc khác nhau."""
    verdict = classify_price_move(Decimal("-35"), Decimal("100"), None)

    assert verdict == MOVE_UNVERIFIED
    assert verdict != MOVE_BAD_DATA


def test_duoi_nguong_thi_khong_hoi_nguon_hai() -> None:
    assert classify_price_move(Decimal("-20"), Decimal("100"), None) == ""


def test_dung_30_phan_tram_chua_phai_lon() -> None:
    """Biên: "> 30%" nghĩa là đúng 30 chưa hỏi."""
    assert classify_price_move(LARGE_MOVE_PCT, Decimal("100"), None) == ""


def test_bien_dong_TANG_cung_duoc_xet() -> None:
    """`abs()`: một cú bơm giá +35% cũng đáng nghi như một cú sập −35%."""
    assert classify_price_move(Decimal("35"), Decimal("100"), Decimal("100.5")) == MOVE_REAL


def test_nguon_hai_chi_duoc_goi_khi_vuot_nguong() -> None:
    """Không tốn một round-trip mạng mỗi bar cho một nhánh gần như không
    bao giờ chạy."""
    goi: list[int] = []
    bars = _bars()  # biến động cuối ~7%, dưới ngưỡng

    def _ghi_nhan() -> Decimal:
        goi.append(1)
        return Decimal("1")

    run_integrity_check(bars, fetch_secondary=_ghi_nhan)

    assert goi == []


def test_nguon_hai_loi_thi_coi_nhu_khong_lay_duoc(tmp_path: Path) -> None:
    bars = _bars()
    bars.loc[_IDX[4], "close"] = 5.0  # −64% so với bar trước
    bars.loc[_IDX[4], "low"] = 4.0

    def _no() -> Decimal:
        raise ConnectionError("sàn phụ không phản hồi")

    ket_qua = run_integrity_check(bars, fetch_secondary=_no, lock_file=tmp_path / "l.lock")

    assert ket_qua.move_verdict == MOVE_UNVERIFIED
    assert ket_qua.lock_written


# ----------------------------------------------------------------------
# Lock
# ----------------------------------------------------------------------


class _FakeAlertManager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, alert: Any) -> bool:
        self.sent.append(alert)
        return True


def test_bar_hong_thi_ghi_lock_va_canh_bao(tmp_path: Path) -> None:
    """Nghiệm thu 12d #3 đầy đủ: bar hỏng → lock được ghi."""
    am = _FakeAlertManager()
    bars = _bars()
    bars.loc[_IDX[2], "close"] = 999.0

    ket_qua = run_integrity_check(bars, lock_file=tmp_path / "dq.lock", alert_manager=am)

    assert ket_qua.lock_written
    assert (tmp_path / "dq.lock").exists()
    assert am.sent[0].alert_type is AlertType.DATA_QUALITY_FAILED
    assert am.sent[0].severity == "CRITICAL"


def test_bien_dong_that_KHONG_ghi_lock_nhung_VAN_canh_bao(tmp_path: Path) -> None:
    """Cảnh báo có, khoá không. Người vận hành phải BIẾT thị trường vừa
    sập 35%; bot vẫn phải chạy."""
    am = _FakeAlertManager()
    bars = _bars()
    bars.loc[_IDX[4], ["open", "high", "low", "close"]] = [14.0, 14.0, 9.0, 9.1]

    ket_qua = run_integrity_check(
        bars, secondary_close=Decimal("9.11"), lock_file=tmp_path / "dq.lock", alert_manager=am
    )

    assert ket_qua.move_verdict == MOVE_REAL
    assert not ket_qua.lock_written
    assert not (tmp_path / "dq.lock").exists()
    assert am.sent[0].alert_type is AlertType.LARGE_PRICE_MOVE


def test_lock_ghi_du_kiem_tra_nao_that_bai(tmp_path: Path) -> None:
    """§B.4: file ghi rõ kiểm tra nào thất bại, bar nào, giá trị thực tế."""
    bars = _bars()
    bars.loc[_IDX[2], "close"] = 999.0
    p = write_lock(check_integrity(bars), path=tmp_path / "dq.lock")
    d = json.loads(p.read_text(encoding="utf-8"))

    assert d["violations"][0]["check"] == "low <= close <= high"
    assert d["violations"][0]["values"]["close"] == 999.0
    assert "rm " in d["cach_xoa"]


def test_lock_khong_tu_het_han(tmp_path: Path) -> None:
    """Một lock tự hết hạn nghĩa là nguyên nhân không bao giờ bị điều tra
    — bot tự chạy lại trên dữ liệu vẫn còn hỏng."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "data_harness.py").read_text(
        encoding="utf-8"
    )

    assert "expires" not in src and "ttl" not in src.lower()
    p = write_lock([], path=tmp_path / "dq.lock")
    assert lock_active(p)


def test_lock_path_theo_STATE_DIR(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATE_DIR", "/tmp/dq-test")

    assert lock_path() == Path("/tmp/dq-test/data_quality.lock")


# ----------------------------------------------------------------------
# Nối dây: bot phải ĐỌC lock và dừng sinh signal
# ----------------------------------------------------------------------


def test_main_doc_lock_va_dung_sinh_signal() -> None:
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")

    assert "data_quality_lock.exists()" in src
    assert "DATA_QUALITY_LOCK_FILENAME" in src


def test_main_bo_qua_ca_nhanh_enforce_stop_khi_lock() -> None:
    """Bỏ qua TOÀN BỘ xử lý bar, kể cả enforce stop-loss: nhánh đó so giá
    bar với stop, và giá bar chính là thứ vừa bị tuyên bố là không tin
    được. Đóng vị thế theo một mức giá sai là hiện thực hoá một khoản lỗ
    chưa từng xảy ra.

    Ghim bằng vị trí: cổng lock phải nằm TRƯỚC vòng `for ... pending`.
    """
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    vi_tri_lock = src.index("if data_quality_lock.exists():")
    vi_tri_vong = src.index("for bar_index, pending_bar in enumerate(pending):")

    assert vi_tri_lock < vi_tri_vong


def test_harness_khong_ghi_vao_forward() -> None:
    """Ràng buộc #4 của Phase 12d."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "data_harness.py").read_text(
        encoding="utf-8"
    )

    assert "forward" not in src


def test_bien_dong_GIA_GHI_LOCK_qua_duong_that(tmp_path: Path) -> None:
    """NGHIỆM THU 12d #5 đầy đủ, qua `run_integrity_check` chứ không chỉ
    `classify_price_move`.

    `test_bien_dong_GIA_thi_khoa` chỉ kiểm HÀM PHÂN LOẠI (thuần). Nó KHÔNG
    kiểm rằng phán quyết đó biến thành một lock. Đo được bằng đột biến: bỏ
    nhánh `MOVE_BAD_DATA` trong `_move_violations` thì test kia vẫn xanh —
    hệ thống phân loại đúng rồi không làm gì cả.
    """
    am = _FakeAlertManager()
    bars = _bars()
    bars.loc[_IDX[4], ["open", "high", "low", "close"]] = [14.0, 14.0, 9.0, 9.1]

    ket_qua = run_integrity_check(
        bars,
        secondary_close=Decimal("14.0"),  # nguồn phụ KHÔNG thấy cú sập
        lock_file=tmp_path / "dq.lock",
        alert_manager=am,
    )

    assert ket_qua.move_verdict == MOVE_BAD_DATA
    assert ket_qua.lock_written
    assert (tmp_path / "dq.lock").exists()
    assert any("hai nguồn khớp nhau" in v.check for v in ket_qua.violations)
    assert am.sent[-1].alert_type is AlertType.DATA_QUALITY_FAILED
