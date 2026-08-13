"""Nhịp retrain HMM — `monitoring/forward_watchdog.py::check_retrain_cadence`.

Sai lệch #1 (lịch retrain reset khi cuộn schema, `docs/DECISIONS.md`) chỉ
được phát hiện BA NGÀY SAU, một cách tình cờ. Đây là phép kiểm lấp khoảng
trống đó.

Điểm thiết kế quan trọng nhất mà file này khoá lại: mốc `2026-08-09`
KHÔNG phải ngoại lệ hardcode cho lần lệch 08-08. Nó là **mốc bắt đầu
giám sát**, và phép kiểm chỉ xét khoảng cách nào có ĐIỂM SAU >= mốc. Khác
biệt kiểm chứng được: đổi mốc thì hành vi đổi theo một cách nhất quán —
không có ngày nào được đối xử đặc biệt.

Đặt phép kiểm ở watchdog chứ KHÔNG ở `forward/logger.py`: file đó đóng
băng với SHA256 ghim, sửa nó = kết thúc thí nghiệm (bất biến #15).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

import forward.logger as logger_mod
import forward.runner as runner_mod
from monitoring.forward_watchdog import (
    CADENCE_DRIFT,
    CADENCE_NO_DATA,
    CADENCE_OK,
    CADENCE_UNAVAILABLE,
    check_retrain_cadence,
)

_START = date(2026, 8, 9)


def _fake_log(retrain_dates: list[str], other_dates: list[str] | None = None) -> pd.DataFrame:
    """Log giả tối thiểu — chỉ hai cột mà `check_retrain_cadence` đọc."""
    rows = [(d, True) for d in retrain_dates] + [(d, False) for d in other_dates or []]
    rows.sort()
    return pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], utc=True),
            "hmm_retrained": [r[1] for r in rows],
        }
    )


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Trả về hàm nối `load_all_bars`/`load_frozen_settings` giả."""

    def _wire(df: pd.DataFrame, interval: int = 7) -> None:
        monkeypatch.setattr(runner_mod, "load_all_bars", lambda: df)
        monkeypatch.setattr(
            logger_mod, "load_frozen_settings", lambda: {"hmm": {"retrain_interval_days": interval}}
        )

    return _wire


# ======================================================================
# Mốc bắt đầu giám sát KHÔNG phải ngoại lệ hardcode
# ======================================================================


def test_sai_lech_1_nam_ngoai_pham_vi_giam_sat(wire: Any) -> None:
    """08-05 → 08-08 cách 3 ngày (sai lệch #1) KHÔNG được báo động.

    Không phải vì có dòng nào bỏ qua ngày 08-08, mà vì ĐIỂM SAU của khoảng
    đó (08-08) nằm trước mốc bắt đầu giám sát 08-09.
    """
    wire(_fake_log(["2026-08-05", "2026-08-08"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 8, 13))

    assert result.status != CADENCE_DRIFT
    assert result.gaps == (), "không khoảng cách retrain nào được xét"


def test_doi_moc_thi_hanh_vi_doi_theo_nhat_quan(wire: Any) -> None:
    """Bằng chứng "mốc giám sát" chứ không phải "ngoại lệ cho 08-08".

    Lùi mốc về trước 08-08 thì CHÍNH khoảng 08-05 → 08-08 bị bắt. Nếu
    trong code có một ngoại lệ hardcode cho ngày đó, test này sẽ đỏ.
    """
    wire(_fake_log(["2026-08-05", "2026-08-08"]))

    result = check_retrain_cadence(monitoring_start=date(2026, 8, 1))

    assert result.status == CADENCE_DRIFT
    assert len(result.gaps) == 1
    assert result.gaps[0].days == 3


def test_khoang_bat_qua_moc_van_duoc_kiem(wire: Any) -> None:
    """Điểm TRƯỚC nằm ngoài mốc, điểm SAU nằm trong -> VẪN kiểm.

    Đây là khoảng 08-08 → 08-15 thật sẽ xảy ra. Nó hợp lệ (7 ngày) nên
    phải xanh, nhưng nó PHẢI được xét — nếu lấy điểm TRƯỚC làm tiêu chí
    phạm vi thì khoảng này bị bỏ qua và ta mất luôn phép kiểm đầu tiên.
    """
    wire(_fake_log(["2026-08-05", "2026-08-08", "2026-08-15"]))

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == CADENCE_OK
    assert [g.days for g in result.gaps] == [7]
    assert result.gaps[0].previous == date(2026, 8, 8)


# ======================================================================
# Ngưỡng ±1 ngày
# ======================================================================


@pytest.mark.parametrize(
    "second_date,days,expect",
    [
        ("2026-08-15", 6, CADENCE_OK),  # 7-1, biên dưới
        ("2026-08-16", 7, CADENCE_OK),  # đúng nhịp
        ("2026-08-17", 8, CADENCE_OK),  # 7+1, biên trên
        ("2026-08-14", 5, CADENCE_DRIFT),  # 7-2
        ("2026-08-18", 9, CADENCE_DRIFT),  # 7+2
    ],
)
def test_nguong_dung_sai(wire: Any, second_date: str, days: int, expect: str) -> None:
    """±1 ngày: bar là mốc UTC nguyên ngày và runner có thể chạy bù nhiều
    bar một lần, nên một ngày xê dịch là bình thường. Lệch 2 ngày trở lên
    thì không giải thích được bằng lịch chạy."""
    wire(_fake_log(["2026-08-09", second_date]))

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == expect
    assert result.gaps[0].days == days


def test_doc_interval_tu_config_dong_bang_khong_hardcode(wire: Any) -> None:
    """Đổi `retrain_interval_days` thì ngưỡng đổi theo — con số 7 không
    được nằm cứng trong phép kiểm."""
    wire(_fake_log(["2026-08-09", "2026-08-23"]), interval=14)

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == CADENCE_OK
    assert result.interval_days == 14


# ======================================================================
# Không đọc được ≠ không sao
# ======================================================================


def test_khong_doc_duoc_config_la_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Không kiểm được" KHÁC HẲN "đã kiểm, không sao". Trả `ok` ở đây là
    nói dối đúng lúc phép kiểm mất tác dụng."""

    def _boom() -> dict:
        raise FileNotFoundError("config_frozen.yaml biến mất")

    monkeypatch.setattr(logger_mod, "load_frozen_settings", _boom)

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == CADENCE_UNAVAILABLE
    assert "FileNotFoundError" in result.detail


def test_khong_doc_duoc_log_la_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logger_mod, "load_frozen_settings", lambda: {"hmm": {"retrain_interval_days": 7}})

    def _boom() -> pd.DataFrame:
        raise ValueError("log hỏng")

    monkeypatch.setattr(runner_mod, "load_all_bars", _boom)

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == CADENCE_UNAVAILABLE
    assert result.interval_days == 7  # đọc được config thì vẫn báo lại


def test_khong_bao_gio_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Watchdog cam kết không raise — phép kiểm này chạy bên trong nó."""
    monkeypatch.setattr(logger_mod, "load_frozen_settings", lambda: {"khong_co_khoa_hmm": {}})

    assert check_retrain_cadence(monitoring_start=_START).status == CADENCE_UNAVAILABLE


# ======================================================================
# Khoảng HỞ CUỐI — retrain ngừng hẳn
# ======================================================================


def test_dung_nhip_roi_im_lang_60_ngay_van_bi_bat(wire: Any) -> None:
    """KHẲNG ĐỊNH TRUNG TÂM của phần này.

    Hai lần retrain ĐÚNG NHỊP rồi ngừng hẳn: mọi khoảng cách giữa hai lần
    retrain đều xanh, nên nếu chỉ kiểm chúng thì `status = ok` — đúng lúc
    cần báo động nhất. Đây là chế độ hỏng nguy hiểm nhất (retrain chết) mà
    phép kiểm cặp-đôi mù hoàn toàn.
    """
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 10, 15))

    assert result.status == CADENCE_DRIFT
    assert all(g.ok for g in result.gaps), "mọi khoảng cách retrain đều đúng nhịp"
    assert result.trailing_gap is not None
    assert result.trailing_gap.ok is False
    assert result.trailing_gap.days == 60


def test_thong_diep_noi_CHUA_RETRAIN_khong_phai_nhip_lech(wire: Any) -> None:
    """Hai triệu chứng dẫn tới hai chỗ điều tra khác nhau, nên thông điệp
    phải phân biệt được ngay: "chưa retrain N ngày" mô tả một việc KHÔNG
    xảy ra; "nhịp lệch" mô tả một việc đã xảy ra sai thời điểm."""
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    detail = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 10, 15)).detail

    assert "CHƯA RETRAIN 60 ngày" in detail
    assert "lệch nhịp" not in detail


@pytest.mark.parametrize(
    "today,days,expect",
    [
        ("2026-08-23", 7, CADENCE_OK),  # đúng nhịp, chưa tới hạn
        ("2026-08-24", 8, CADENCE_OK),  # 7+1, biên trên
        ("2026-08-25", 9, CADENCE_DRIFT),  # 7+2, vượt trần
    ],
)
def test_tran_khoang_ho_cuoi(wire: Any, today: str, days: int, expect: str) -> None:
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date.fromisoformat(today))

    assert result.trailing_gap is not None
    assert result.trailing_gap.days == days
    assert result.status == expect


def test_khoang_ho_cuoi_chi_co_tran_khong_co_san(wire: Any) -> None:
    """Vừa retrain hôm qua -> khoảng hở 1 ngày. Đó là BÌNH THƯỜNG, không
    phải "lệch nhịp quá ngắn". Khác `RetrainGap` vốn kiểm cả hai phía."""
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 8, 17))

    assert result.status == CADENCE_OK
    assert result.trailing_gap is not None
    assert result.trailing_gap.days == 1


def test_khoang_ho_cuoi_theo_cung_moc_giam_sat(wire: Any) -> None:
    """Cùng quy tắc "ĐIỂM SAU quyết định phạm vi" — điểm sau ở đây là hôm
    nay, nên trước mốc giám sát thì không đo."""
    wire(_fake_log(["2026-06-01"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 8, 8))

    assert result.status == CADENCE_NO_DATA
    assert result.trailing_gap is None


def test_ca_hai_loai_lech_cung_bao_cao(wire: Any) -> None:
    """Nhịp lệch VÀ ngừng hẳn cùng lúc -> thông điệp phải có cả hai, không
    chỉ cái gặp trước."""
    wire(_fake_log(["2026-08-09", "2026-08-25"]))  # cách 16 ngày

    detail = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 10, 1)).detail

    assert "lệch nhịp" in detail
    assert "CHƯA RETRAIN" in detail


def test_as_dict_ghi_lai_khoang_ho_cuoi(wire: Any) -> None:
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    payload = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 10, 15)).as_dict()

    assert payload["trailing_gap"] == {
        "last_retrain": "2026-08-16",
        "today": "2026-10-15",
        "days": 60,
        "ok": False,
    }


# ======================================================================
# Ca biên
# ======================================================================


def test_chua_co_lan_retrain_nao(wire: Any) -> None:
    wire(_fake_log([], other_dates=["2026-08-10", "2026-08-11"]))

    assert check_retrain_cadence(monitoring_start=_START).status == CADENCE_NO_DATA


def test_dung_mot_lan_retrain_van_do_duoc_khoang_ho_cuoi(wire: Any) -> None:
    """Một điểm không tạo được khoảng cách GIỮA hai lần retrain — nhưng
    vẫn đo được khoảng hở tới hôm nay. Đó chính là giá trị của phép kiểm
    khoảng hở cuối: nó hoạt động ngay từ lần retrain đầu tiên."""
    wire(_fake_log(["2026-08-20"]))

    result = check_retrain_cadence(monitoring_start=_START, today_utc=date(2026, 8, 24))

    assert result.gaps == ()
    assert result.status == CADENCE_OK
    assert result.trailing_gap is not None
    assert result.trailing_gap.days == 4


def test_nhieu_khoang_bao_cao_dung_so_lech(wire: Any) -> None:
    """Báo cả tổng số khoảng lẫn số khoảng lệch — "2/4 lệch" nói được mức
    độ, "có lệch" thì không."""
    wire(_fake_log(["2026-08-09", "2026-08-16", "2026-08-30", "2026-09-06"]))

    result = check_retrain_cadence(monitoring_start=_START)

    assert result.status == CADENCE_DRIFT
    assert "1/3" in result.detail
    assert [g.ok for g in result.gaps] == [True, False, True]


def test_as_dict_ghi_lai_moc_giam_sat(wire: Any) -> None:
    """`monitoring_since` phải có trong JSON: người đọc `watchdog.out.log`
    cần biết phép kiểm bắt đầu từ đâu để không tưởng nó phủ toàn bộ lịch
    sử thí nghiệm."""
    wire(_fake_log(["2026-08-09", "2026-08-16"]))

    payload = check_retrain_cadence(monitoring_start=_START).as_dict()

    assert payload["monitoring_since"] == "2026-08-09"
    assert payload["interval_days"] == 7
    assert payload["gaps"][0]["days"] == 7
