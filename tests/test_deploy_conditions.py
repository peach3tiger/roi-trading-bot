"""Phase 12c §E — `ops/deploy_conditions.py`.

Điểm dễ sai nhất ở đây không phải công thức mà là **trạng thái thứ ba**:
`ok=None` (không xác định được). Trộn nó vào `True` cho một cổng rỗng;
trộn vào `False` thì một sự cố đo lường chặn deploy vĩnh viễn. Phần lớn
test dưới đây kiểm đúng ranh giới đó.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from ops.deploy_conditions import (
    MAX_VOL_PERCENTILE,
    MIN_HISTORY_BARS,
    Condition,
    DeployReadiness,
    check_no_active_breaker,
    check_no_halt_lock,
    check_no_pending_orders,
    check_volatility,
    evaluate,
    percentile_of,
    realized_vol_24h,
    vol_history,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "btcusdt_1d_2018_2026.parquet"


def _closes(n: int = 500, buoc: float = 1.0) -> list[float]:
    """Giá tăng đều `buoc`% mỗi bar — biến động không đổi, nên phân vị của
    bar cuối nằm giữa phân phối."""
    gia = [100.0]
    for _ in range(n):
        gia.append(gia[-1] * (1 + buoc / 100))
    return gia


# ----------------------------------------------------------------------
# §E.1 — công thức
# ----------------------------------------------------------------------


def test_vol_24h_la_log_return_tuyet_doi() -> None:
    v = realized_vol_24h([100.0, 110.0])

    assert v == pytest.approx(abs(math.log(1.1)) * 100)


def test_vol_24h_doi_xung_theo_chieu() -> None:
    """Sập 10% và tăng 10% đều là "thị trường động". Một cổng chỉ nhìn
    chiều giảm sẽ cho deploy giữa lúc giá bay lên 30%."""
    len_ = realized_vol_24h([100.0, 110.0])
    xuong = realized_vol_24h([110.0, 100.0])

    assert len_ == pytest.approx(xuong)


def test_chua_du_hai_gia_thi_None_khong_phai_0() -> None:
    """0 là giá trị BÌNH THƯỜNG NHẤT có thể — trả 0 khi không tính được
    sẽ lặng lẽ cho qua cổng."""
    assert realized_vol_24h([]) is None
    assert realized_vol_24h([100.0]) is None


def test_gia_khong_duong_thi_None() -> None:
    """`log(0)` là `-inf`, `log(âm)` là lỗi. Dữ liệu hỏng phải thành
    "không biết", không thành một con số."""
    assert realized_vol_24h([0.0, 100.0]) is None
    assert realized_vol_24h([100.0, -5.0]) is None


def test_vol_history_cung_dai_luong_cung_cua_so() -> None:
    """Bước 1 của CLAUDE.md #18 — bước hay bị bỏ nhất. Giá trị hiện tại là
    `|log return|` MỘT bar, nên phân phối nền cũng phải là `|log return|`
    TỪNG bar; so với độ lệch chuẩn toàn kỳ là so hai thứ có phương sai
    khác nhau hàng chục lần."""
    gia = [100.0, 110.0, 99.0]
    ls = vol_history(gia)

    assert len(ls) == len(gia) - 1
    assert ls[-1] == pytest.approx(realized_vol_24h(gia))


def test_percentile_of() -> None:
    assert percentile_of(5.0, [1.0, 2.0, 3.0, 4.0]) == 100.0
    assert percentile_of(0.5, [1.0, 2.0, 3.0, 4.0]) == 0.0
    assert percentile_of(2.5, [1.0, 2.0, 3.0, 4.0]) == 50.0


# ----------------------------------------------------------------------
# §E.1 — cổng
# ----------------------------------------------------------------------


def test_bien_dong_binh_thuong_thi_dat() -> None:
    dk = check_volatility(_closes())

    assert dk.ok is True
    assert "phân vị" in dk.detail


def test_bien_dong_cao_bat_thuong_thi_truot() -> None:
    """Bar cuối nhảy 30% trong khi lịch sử toàn 1% — phải chặn."""
    gia = _closes()
    gia.append(gia[-1] * 1.30)

    dk = check_volatility(gia)

    assert dk.ok is False


def test_chua_du_lich_su_thi_KHONG_XAC_DINH_chu_khong_dat() -> None:
    """"Phân vị 80" tính trên vài chục điểm là một con số trông có thẩm
    quyền mà không có. Cho qua ở đây biến cổng thành trang trí."""
    dk = check_volatility(_closes(n=50))

    assert dk.ok is None
    assert str(MIN_HISTORY_BARS) in dk.detail


def test_nguong_la_PHAN_VI_khong_phai_so_tuyet_doi() -> None:
    """Ngưỡng tuyệt đối sai dần khi chế độ biến động của thị trường đổi;
    phân vị tự hiệu chỉnh. Cùng dữ liệu nhân đôi biến động -> vẫn đạt, vì
    phân phối nền cũng nhân đôi theo."""
    it_bien_dong = check_volatility(_closes(buoc=0.5))
    nhieu_bien_dong = check_volatility(_closes(buoc=5.0))

    assert it_bien_dong.ok is True
    assert nhieu_bien_dong.ok is True


def test_phan_vi_mac_dinh_dung_nhu_E1() -> None:
    assert MAX_VOL_PERCENTILE == 80.0


def test_nguong_p80_khop_phep_do_tren_fixture() -> None:
    """Ghim con số đã ĐO và ghi vào docstring module: p80 = 3.561% trên
    3137 bar. Nếu fixture bị sinh lại, con số này đổi và mọi câu trong tài
    liệu về "chặn 20% số ngày" hết hiệu lực — test này bắt được lúc đó."""
    import pandas as pd

    bars = pd.read_parquet(_FIXTURE)
    ls = sorted(vol_history(list(bars["close"])))
    p80 = ls[int(len(ls) * 0.80)]

    assert p80 == pytest.approx(3.561, abs=0.01)
    assert len(ls) == 3137


def test_ty_le_chan_deploy_dung_20_phan_tram() -> None:
    """CLAUDE.md #18 bước 4: BÁO CÁO tỷ lệ báo động giả đo được. Ở đây nó
    bằng đúng định nghĩa phân vị (p80 chặn 20%) — nhưng phải ĐO chứ không
    suy, vì `percentile_of` dùng "nhỏ hơn nghiêm ngặt" và giá trị lặp có
    thể làm lệch."""
    import pandas as pd

    bars = pd.read_parquet(_FIXTURE)
    ls = vol_history(list(bars["close"]))
    nguong = sorted(ls)[int(len(ls) * 0.80)]

    ty_le = sum(1 for v in ls if v > nguong) / len(ls)

    assert ty_le == pytest.approx(0.20, abs=0.01)


# ----------------------------------------------------------------------
# §E.2 — lệnh chờ / breaker / halt lock
# ----------------------------------------------------------------------


class _San:
    def __init__(self, lenh: Any = (), no: bool = False) -> None:
        self._lenh = lenh
        self._no = no

    def get_open_orders(self) -> Any:
        if self._no:
            raise ConnectionError("mạng hỏng")
        return self._lenh


class _Lenh:
    def __init__(self, oid: str) -> None:
        self.order_id = oid
        self.side = "BUY"


def test_khong_lenh_cho_thi_dat() -> None:
    assert check_no_pending_orders(_San()).ok is True


def test_co_lenh_cho_thi_truot() -> None:
    """Deploy giữa lúc có lệnh chưa khớp nghĩa là instance MỚI khởi động
    với một lệnh nó không biết mình đã đặt."""
    dk = check_no_pending_orders(_San(lenh=[_Lenh("A1"), _Lenh("A2")]))

    assert dk.ok is False
    assert "A1" in dk.detail


def test_khong_hoi_duoc_san_thi_KHONG_XAC_DINH() -> None:
    """"Không hỏi được sàn" và "sàn trả lời không có lệnh nào" là hai
    chuyện khác nhau, và chỉ một trong hai cho phép deploy."""
    dk = check_no_pending_orders(_San(no=True))

    assert dk.ok is None
    assert "ConnectionError" in dk.detail


class _RiskManager:
    def __init__(self, lich_su: Any = (), lock: Any = None) -> None:
        self.circuit_breaker = self
        self._lich_su = list(lich_su)
        self._halt_lock_path = lock

    def get_history(self) -> list:
        return self._lich_su


class _Status:
    def __init__(self, muc: str) -> None:
        self.level = type("L", (), {"value": muc})()


def test_breaker_NONE_thi_dat() -> None:
    assert check_no_active_breaker(_RiskManager()).ok is True


def test_breaker_dang_hoat_dong_thi_truot() -> None:
    """Breaker hoạt động nghĩa là hệ thống đang TỰ BẢO VỆ. Thêm một biến
    số vào đúng lúc đó là chồng rủi ro lên rủi ro."""
    dk = check_no_active_breaker(_RiskManager(lich_su=[_Status("DAILY_HALT")]))

    assert dk.ok is False
    assert "DAILY_HALT" in dk.detail


def test_halt_lock_ton_tai_thi_truot(tmp_path: Path) -> None:
    lock = tmp_path / "trading_halted.lock"
    lock.write_text("peak drawdown", encoding="utf-8")

    dk = check_no_halt_lock(_RiskManager(lock=lock))

    assert dk.ok is False


def test_khong_co_halt_lock_thi_dat(tmp_path: Path) -> None:
    assert check_no_halt_lock(_RiskManager(lock=tmp_path / "khong-co.lock")).ok is True


# ----------------------------------------------------------------------
# Tổng hợp — `None` KHÔNG được tính là đạt
# ----------------------------------------------------------------------


def test_moi_dieu_kien_dat_thi_ok() -> None:
    kq = DeployReadiness((Condition("a", True, ""), Condition("b", True, "")))

    assert kq.ok


def test_mot_dieu_kien_KHONG_XAC_DINH_thi_khong_ok() -> None:
    """Đây là ranh giới quan trọng nhất của module: coi "không đo được" là
    "đạt" biến cổng thành trang trí."""
    kq = DeployReadiness((Condition("a", True, ""), Condition("b", None, "")))

    assert not kq.ok


def test_mot_dieu_kien_truot_thi_khong_ok() -> None:
    kq = DeployReadiness((Condition("a", True, ""), Condition("b", False, "")))

    assert not kq.ok


def test_evaluate_gop_du_bon_dieu_kien(tmp_path: Path) -> None:
    kq = evaluate(
        closes=_closes(),
        exchange_client=_San(),
        risk_manager=_RiskManager(lock=tmp_path / "khong-co.lock"),
    )

    assert len(kq.conditions) == 4
    assert kq.ok


def test_risk_manager_khong_lo_halt_lock_thi_KHONG_XAC_DINH() -> None:
    """`RiskManager` thật luôn có `_halt_lock_path`; thiếu nó nghĩa là đối
    tượng truyền vào không phải cái ta nghĩ. Trả `None` (không biết) thay
    vì `True` — một cổng không tìm thấy thứ nó phải kiểm thì chưa kiểm gì."""
    dk = check_no_halt_lock(_RiskManager(lock=None))

    assert dk.ok is None


def test_evaluate_khong_co_san_thi_chi_kiem_bien_dong() -> None:
    """Chạy được khi mạng bị chặn — nhưng lúc đó nó chỉ kiểm §E.1, và báo
    cáo phải cho thấy điều đó thay vì giả vờ đã kiểm đủ."""
    kq = evaluate(closes=_closes())

    assert len(kq.conditions) == 1


# ----------------------------------------------------------------------
# §E.3 — KHÔNG đo được bằng máy
# ----------------------------------------------------------------------


def test_bao_cao_luon_hoi_cau_hoi_ve_NGUOI() -> None:
    """§E.3 là điều kiện THẬT đằng sau luật "không deploy tối Thứ Sáu".
    Một hàm không trả lời được nó; giả vờ trả lời được sẽ tệ hơn không hỏi.

    Nên nó phải xuất hiện ở MỌI báo cáo, kể cả báo cáo toàn ĐẠT — đó là
    lúc người ta dễ bỏ qua nhất.
    """
    ra = DeployReadiness((Condition("a", True, ""),)).render()

    assert "2 GIỜ" in ra
    assert "Thứ Sáu" in ra


def test_bao_cao_phan_biet_ba_trang_thai() -> None:
    ra = DeployReadiness(
        (Condition("a", True, "x"), Condition("b", False, "y"), Condition("c", None, "z"))
    ).render()

    assert "ĐẠT" in ra and "TRƯỢT" in ra and "???" in ra


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def test_cli_chay_duoc_tren_fixture_va_in_phan_vi(capsys: pytest.CaptureFixture) -> None:
    """Nghiệm thu 12c #9: chạy được trên dữ liệu thật, in ra phân vị vol
    hiện tại."""
    import ops.deploy_conditions as dc

    ma = dc.main(["--fixture", str(_FIXTURE)])
    ra = capsys.readouterr().out

    assert ma == 0
    assert "phân vị" in ra
    assert "n=3137 bar" in ra


def test_cli_thoat_1_khi_chua_du_dieu_kien(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Mã thoát LÀ cổng."""
    import pandas as pd

    import ops.deploy_conditions as dc

    bars = pd.read_parquet(_FIXTURE)
    gia = list(bars["close"])
    gia[-1] = gia[-2] * 1.35  # bar cuối nhảy 35%
    f = tmp_path / "sap.parquet"
    pd.DataFrame({"close": gia}, index=bars.index).to_parquet(f)

    assert dc.main(["--fixture", str(f)]) == 1
    assert "CHƯA đủ điều kiện" in capsys.readouterr().out


# ----------------------------------------------------------------------
# Tài liệu phải theo kịp code (cùng cách test_health ghim RUNBOOK)
# ----------------------------------------------------------------------

_RUNBOOK = Path(__file__).resolve().parent.parent / "ops" / "RUNBOOK.md"
_CLAUDE = Path(__file__).resolve().parent.parent / "CLAUDE.md"


def test_runbook_co_checklist_trien_khai_va_ba_dieu_kien() -> None:
    """Một checklist deploy nằm trong đầu người deploy là một checklist sẽ
    bị bỏ bước vào đúng hôm bận."""
    noi_dung = _RUNBOOK.read_text(encoding="utf-8")

    assert "## Triển khai phiên bản mới — CHECKLIST" in noi_dung
    for lenh in ("ops.compare_versions", "ops.shadow_diff", "ops.deploy_conditions"):
        assert lenh in noi_dung, f"checklist thiếu bước {lenh}"
    assert "2 GIỜ" in noi_dung, "thiếu §E.3 — điều kiện về NGƯỜI"


def test_runbook_giai_thich_vi_sao_khong_blue_green() -> None:
    """Đây là quyết định người đọc sau này sẽ muốn đảo ngược. Không ghi lý
    do thì nó sẽ bị đảo ngược."""
    noi_dung = _RUNBOOK.read_text(encoding="utf-8")

    assert "blue-green" in noi_dung
    assert "orderLinkId" in noi_dung, "thiếu lý do THẬT: idempotency không cứu được"


def test_runbook_ghi_dung_nguong_da_do() -> None:
    """3.561% / 20% là con số ĐO ĐƯỢC. Nếu fixture đổi, chúng đổi theo và
    `test_nguong_p80_khop_phep_do_tren_fixture` sẽ đỏ trước — test này bắt
    trường hợp code đúng mà tài liệu tụt lại."""
    noi_dung = _RUNBOOK.read_text(encoding="utf-8")

    assert "3.561" in noi_dung
    assert "20%" in noi_dung


def _gon(text: str) -> str:
    """Gộp mọi khoảng trắng thành một dấu cách.

    Tài liệu trong dự án này ngắt dòng ở ~72 ký tự, nên một cụm từ bất kỳ
    có thể bị cắt làm đôi. So chuỗi thô sẽ đỏ vì CÁCH TRÌNH BÀY chứ không
    vì NỘI DUNG — đúng loại test dạy người ta sửa tài liệu cho vừa công
    cụ (cùng bài học §C.2 với grep/`.predict(`).
    """
    return " ".join(text.split())


def test_claude_md_co_bat_bien_20() -> None:
    noi_dung = _gon(_CLAUDE.read_text(encoding="utf-8"))

    assert "20. Không bao giờ hai tiến trình cùng khả năng đặt lệnh" in noi_dung
    assert "vị thế nhân đôi" in noi_dung, "thiếu HẬU QUẢ — lý do bất biến này tồn tại"
