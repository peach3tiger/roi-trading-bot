"""`ops/kiem_tat_dinh.py` — công cụ đo tất định nội máy.

Một công cụ đo mà chính nó không được đo thì chỉ chuyển niềm tin từ chỗ
này sang chỗ khác. Phép kiểm quan trọng nhất ở đây là
`test_bam_phan_biet_duoc_bit_cuoi`: nếu hash không phân biệt được sai
khác chữ số cuối, công cụ sẽ báo "tất định" cho đúng thứ nó sinh ra để
bắt.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ops.kiem_tat_dinh import _BIEN_THREAD, bam_equity, dau_van_tay, main


def _khung(gia_tri: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"equity": gia_tri, "allocation_pct": [0.5] * len(gia_tri)})


def test_bam_phan_biet_duoc_bit_cuoi() -> None:
    """Sai khác ở chữ số thứ 16 vẫn là bất định — và đó chính là thứ EM
    khuếch đại thành model khác sau vài chục vòng lặp.

    `pytest.approx` hay `np.allclose` sẽ nói hai giá trị này bằng nhau.
    Đó là câu trả lời đúng cho câu hỏi "chiến lược có trôi không" và là
    câu trả lời SAI cho câu hỏi "hai lần chạy có cùng bit không".
    """
    a = bam_equity(_khung([1.0, 2.0, 3.0]))
    b = bam_equity(_khung([1.0, 2.0, 3.0000000000000004]))

    assert a != b


def test_bam_on_dinh_giua_hai_lan_goi() -> None:
    assert bam_equity(_khung([1.5, 2.5])) == bam_equity(_khung([1.5, 2.5]))


def test_bam_KHONG_lam_tron() -> None:
    """Nếu ai đó thay `repr()` bằng `round(v, 9)` cho "gọn", test này đỏ.
    Ngưỡng 1e-9 là ngưỡng của harness, không phải của phép so bit."""
    a = bam_equity(_khung([1.0]))
    b = bam_equity(_khung([1.0 + 1e-15]))

    assert a != b


def test_bam_doc_nhieu_cot_khong_chi_equity() -> None:
    """Hai đường equity trùng nhau nhưng allocation khác nhau vẫn là hai
    lần chạy khác nhau — chúng sẽ tách ra ở bar sau."""
    x = _khung([1.0, 2.0])
    y = _khung([1.0, 2.0])
    y["allocation_pct"] = [0.5, 0.9]

    assert bam_equity(x) != bam_equity(y)


def test_bam_bo_qua_cot_vang_mat() -> None:
    """`qty`/`cash` không phải lúc nào cũng có; thiếu cột phải bỏ qua chứ
    không nổ — nếu không, công cụ chẩn đoán lại thành thứ cần chẩn đoán."""
    assert bam_equity(pd.DataFrame({"equity": [1.0]}))


# ----------------------------------------------------------------------
# Dấu vân tay — CLAUDE.md #19
# ----------------------------------------------------------------------


@pytest.mark.parametrize("truong", ["machine", "python", "numpy", "blas_name", "threadpool"])
def test_dau_van_tay_co_du_truong_quyet_dinh(truong: str) -> None:
    """Mỗi trường ở đây là một thứ ĐÃ hoặc CÓ THỂ làm hai máy cho hai kết
    quả khác nhau. Thiếu một trường nghĩa là bản ghi "cùng môi trường"
    không chứng minh được điều nó nói."""
    assert dau_van_tay()[truong]


def test_dau_van_tay_bao_cao_moi_bien_thread() -> None:
    """Kể cả khi CHƯA ĐẶT. Một biến vắng mặt trong báo cáo không phân biệt
    được với một biến bằng 1 — và hai thứ đó cho hai kết quả khác nhau."""
    vt = dau_van_tay()

    for b in _BIEN_THREAD:
        assert b in vt, f"thiếu {b} trong dấu vân tay"


def test_runs_0_chi_in_moi_truong_khong_chay_backtest() -> None:
    """`--runs 0` phải rẻ: nó là thứ dán vào mọi báo cáo, không phải một
    lần chạy 3 phút."""
    assert main(["--runs", "0"]) == 0
