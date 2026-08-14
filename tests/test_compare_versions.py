"""Phase 12c §A — `ops/compare_versions.py`.

Cổng này quyết định một phiên bản có được phép chạm lệnh thật hay không.
Một cổng chỉ được kiểm qua mock là cổng chưa bao giờ mở worktree nào, nên
file này có CẢ HAI: phần lớn test chạy trên `DataFrame` dựng tay (nhanh,
phủ hết nhánh so sánh), cộng một test ĐẦU-CUỐI THẬT dùng `git worktree`
thật và backtest thật (`-m slow`).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pytest

from ops.compare_versions import (
    CONTEXT_BARS,
    EQUITY_TOLERANCE,
    EXACT_FIELDS,
    ComparisonReport,
    Divergence,
    compare_frames,
    compare_versions,
    run_at_ref,
)

_ROOT = Path(__file__).resolve().parent.parent


def _regime(*, n: int = 20, doi: Optional[dict[str, tuple[int, Any]]] = None) -> pd.DataFrame:
    """`regime_history` tối thiểu. Giá trị là CHUỖI `Decimal` — đúng như
    backtester ghi ra CSV, nên test đi qua cùng đường chuyển kiểu.

    `doi` là dict TƯỜNG MINH chứ không `**kwargs`: với `**kwargs`, mypy
    không loại trừ được khả năng khoá trùng tên tham số `n`, và một trùng
    tên như thế sẽ đổi kích thước khung thay vì đổi một ô — im lặng.
    """
    df = pd.DataFrame(
        {
            "timestamp": [f"2024-01-{i + 1:02d} 00:00:00+00:00" for i in range(n)],
            "regime_id": [0] * n,
            "strategy_target_allocation_pct": ["0.95"] * n,
            "trend_gate_cap": ["1.00"] * n,
            "final_allocation_pct": ["0.95"] * n,
        }
    )
    for cot, (i, gia_tri) in (doi or {}).items():
        df.loc[i, cot] = gia_tri
    return df


def _equity(*, n: int = 20, doi: Optional[dict[str, tuple[int, Any]]] = None) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "timestamp": [f"2024-01-{i + 1:02d} 00:00:00+00:00" for i in range(n)],
            "equity": ["10000.0"] * n,
        }
    )
    for cot, (i, gia_tri) in (doi or {}).items():
        df.loc[i, cot] = gia_tri
    return df


# ----------------------------------------------------------------------
# Bốn trường quyết định — khớp 100%, KHÔNG dung sai
# ----------------------------------------------------------------------


def test_giong_het_thi_khong_lech() -> None:
    khac, boi_canh = compare_frames(_regime(), _regime(), _equity(), _equity())

    assert khac == []
    assert boi_canh == []


@pytest.mark.parametrize(
    "cot,ten_truong,gia_tri_moi",
    [
        ("regime_id", "regime_id", 3),
        ("strategy_target_allocation_pct", "hmm_allocation", "0.60"),
        ("trend_gate_cap", "trend_gate_cap", "0.30"),
        ("final_allocation_pct", "final_allocation", "0.30"),
    ],
)
def test_moi_truong_quyet_dinh_deu_bi_bat(cot: str, ten_truong: str, gia_tri_moi: Any) -> None:
    """Bốn trường, bốn test. Gộp lại thành một sẽ vẫn xanh khi ba trong bốn
    nhánh so sánh bị xoá."""
    khac, _ = compare_frames(_regime(), _regime(doi={cot: (5, gia_tri_moi)}), _equity(), _equity())

    assert [d.field_name for d in khac] == [ten_truong]
    assert khac[0].bar.startswith("2024-01-06")


def test_bon_truong_dung_nhu_khai_bao() -> None:
    """`EXACT_FIELDS` là hợp đồng với `ops/shadow_diff.py` và §A của
    prompt — hai chỗ gõ tay cùng danh sách sẽ lệch nhau."""
    assert EXACT_FIELDS == ("regime_id", "hmm_allocation", "trend_gate_cap", "final_allocation")


def test_khong_co_dung_sai_cho_bon_truong() -> None:
    """Lệch NHỎ NHẤT có thể vẫn phải bắt. Đây là điểm §A nói rõ: không có
    ngưỡng dung sai cho "thay đổi được coi là không ảnh hưởng logic"."""
    khac, _ = compare_frames(
        _regime(),
        _regime(doi={"final_allocation_pct": (3, "0.9500000000000000000000000001")}),
        _equity(),
        _equity(),
    )

    assert len(khac) == 1


def test_khong_ep_float_o_bat_ky_dau() -> None:
    """Hai giá trị KHÁC NHAU nhưng BẰNG NHAU sau khi ép `float`.

    `float("0.1") + float("0.2") != float("0.3")` là chuyện ai cũng biết;
    chuyện ít biết hơn là hai chuỗi `Decimal` khác nhau có thể ép về CÙNG
    một `float`. Nếu phép so đi qua `float`, test này xanh một cách sai.
    """
    a, b = "1.0000000000000000001", "1.0000000000000000002"
    assert float(a) == float(b)  # tiền đề: float KHÔNG phân biệt được

    khac, _ = compare_frames(
        _regime(), _regime(doi={"trend_gate_cap": (2, b)}), _equity(), _equity()
    )

    assert len(khac) == 1, "phép so đã đi qua float — mất khả năng phân biệt Decimal"


# ----------------------------------------------------------------------
# equity — dung sai 1e-9, và CHỈ equity
# ----------------------------------------------------------------------


def test_equity_trong_dung_sai_thi_bo_qua() -> None:
    khac, _ = compare_frames(
        _regime(), _regime(), _equity(), _equity(doi={"equity": (4, "10000.0000000001")})
    )

    assert khac == []


def test_equity_vuot_dung_sai_thi_bat() -> None:
    khac, _ = compare_frames(_regime(), _regime(), _equity(), _equity(doi={"equity": (4, "10000.001")}))

    assert [d.field_name for d in khac] == ["equity"]


def test_dung_sai_equity_dung_nhu_khai_bao() -> None:
    assert EQUITY_TOLERANCE == Decimal("1e-9")


# ----------------------------------------------------------------------
# Bar đầu tiên lệch + bối cảnh
# ----------------------------------------------------------------------


def test_bar_dau_tien_la_bar_SOM_NHAT() -> None:
    """Không phải "một bar lệch nào đó" — phải là bar SỚM NHẤT. Lệch ở bar
    12 thường là HỆ QUẢ của lệch ở bar 7; báo nhầm bar sẽ dẫn người điều
    tra đi sai hướng ngay từ bước đầu."""
    b = _regime()
    b.loc[12, "regime_id"] = 9
    b.loc[7, "regime_id"] = 9

    khac, _ = compare_frames(_regime(), b, _equity(), _equity())

    assert khac[0].bar.startswith("2024-01-08")


def test_thu_het_khac_biet_khong_dung_som() -> None:
    """"Lệch một bar" và "lệch từ bar đó trở đi" là hai chẩn đoán khác
    nhau, và số lượng nói ngay điều đó."""
    b = _regime()
    for i in range(5, 20):
        b.loc[i, "regime_id"] = 9

    khac, _ = compare_frames(_regime(), b, _equity(), _equity())

    assert len(khac) == 15


def test_boi_canh_co_khoang_10_bar_va_danh_dau_cho_lech() -> None:
    khac, boi_canh = compare_frames(_regime(), _regime(doi={"regime_id": (10, 9)}), _equity(), _equity())

    assert khac
    assert boi_canh, "lệch mà không in bối cảnh"
    assert any("->" in dong for dong in boi_canh), "không đánh dấu bar lệch"
    # 1 dòng tiêu đề + CONTEXT_BARS+1 bar x 4 trường
    assert len(boi_canh) == 1 + (CONTEXT_BARS + 1) * len(EXACT_FIELDS)


def test_boi_canh_cat_dung_o_bien_dau() -> None:
    """Lệch ở bar 0 — `lo` không được âm."""
    khac, boi_canh = compare_frames(_regime(), _regime(doi={"regime_id": (0, 9)}), _equity(), _equity())

    assert khac
    assert boi_canh


# ----------------------------------------------------------------------
# Số bar khác nhau
# ----------------------------------------------------------------------


def test_so_bar_khac_nhau_la_mot_khac_biet() -> None:
    """Hai ref cho số bar khác nhau nghĩa là chúng KHÔNG so được — và đó
    tự nó là kết quả cần báo, không phải lý do để so phần chung rồi im."""
    khac, _ = compare_frames(_regime(n=20), _regime(n=15), _equity(n=20), _equity(n=15))

    assert khac[0].field_name == "n_bars"
    assert (khac[0].value_a, khac[0].value_b) == ("20", "15")


# ----------------------------------------------------------------------
# Báo cáo
# ----------------------------------------------------------------------


def test_bao_cao_khop_noi_ro_so_bar() -> None:
    ra = ComparisonReport(ref_a="A", ref_b="B", n_bars_a=184, n_bars_b=184).render()

    assert "KHỚP 100%" in ra
    assert "184" in ra


def test_bao_cao_lech_in_bar_dau_tien_va_hai_gia_tri() -> None:
    ra = ComparisonReport(
        ref_a="A",
        ref_b="B",
        n_bars_a=20,
        n_bars_b=20,
        divergences=(Divergence("2024-01-06", "regime_id", "0", "3"),),
    ).render()

    assert "LỆCH" in ra
    assert "2024-01-06" in ra
    assert "regime_id" in ra
    assert "0" in ra and "3" in ra


def test_bao_cao_lech_nhac_KHONG_NOI_NGUONG() -> None:
    """Thông điệp này là nửa còn lại của cổng: một cổng đỏ mà không nói
    phải làm gì tiếp sẽ được xử lý bằng cách nới ngưỡng."""
    ra = ComparisonReport(
        ref_a="A", ref_b="B", n_bars_a=1, n_bars_b=1,
        divergences=(Divergence("x", "regime_id", "0", "1"),),
    ).render()

    assert "KHÔNG nới ngưỡng" in ra
    assert "DECISIONS.md" in ra


def test_bao_cao_loi_khong_bao_giu_la_KHOP() -> None:
    """Backtest ở một ref chết -> `ok` phải False. Một lỗi bị đọc thành
    "khớp 100%" là cách tệ nhất cổng này có thể hỏng."""
    ra = ComparisonReport(ref_a="A", ref_b="B", n_bars_a=0, n_bars_b=0, error="boom")

    assert not ra.ok
    assert "LỖI" in ra.render()


# ----------------------------------------------------------------------
# Nối dây `compare_versions` (runner tiêm được)
# ----------------------------------------------------------------------


def _fake_runner_factory(regime_b: pd.DataFrame, equity_b: pd.DataFrame) -> Any:
    def _chay(ref: str, *, start: date, end: date, out_dir: Path, **_: Any) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        r = _regime() if ref == "A" else regime_b
        e = _equity() if ref == "A" else equity_b
        r.to_csv(out_dir / "regime.csv", index=False)
        e.to_csv(out_dir / "equity.csv", index=False)
        return out_dir

    return _chay


def test_compare_versions_khop() -> None:
    ra = compare_versions("A", "A", runner=_fake_runner_factory(_regime(), _equity()))

    assert ra.ok
    assert ra.n_bars_a == ra.n_bars_b == 20


def test_compare_versions_lech() -> None:
    ra = compare_versions(
        "A", "B", runner=_fake_runner_factory(_regime(doi={"regime_id": (6, 4)}), _equity())
    )

    assert not ra.ok
    assert ra.divergences[0].field_name == "regime_id"


def test_runner_no_thi_bao_cao_loi_chu_khong_nem() -> None:
    """CLI phải in được thông điệp lỗi thay vì đổ traceback — người chạy
    cổng này đang muốn biết ĐI TIẾP ĐƯỢC KHÔNG, không muốn debug."""

    def _no(*a: Any, **k: Any) -> Path:
        raise RuntimeError("worktree hỏng")

    ra = compare_versions("A", "B", runner=_no)

    assert not ra.ok
    assert "worktree hỏng" in (ra.error or "")


# ----------------------------------------------------------------------
# `run_at_ref` — worktree phải được dọn
# ----------------------------------------------------------------------


def _worktrees() -> set[str]:
    ra = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {d.split(" ", 1)[1] for d in ra.splitlines() if d.startswith("worktree ")}


def test_ref_khong_ton_tai_khong_de_lai_worktree(tmp_path: Path) -> None:
    """Dọn trong `finally`, kể cả khi thất bại. Một worktree bỏ quên làm
    `git worktree list` bẩn dần và `git gc` không dọn được commit nó giữ."""
    truoc = _worktrees()

    with pytest.raises(subprocess.CalledProcessError):
        run_at_ref("khong-phai-ref-nao-ca", start=date(2024, 1, 1), end=date(2024, 1, 2),
                   out_dir=tmp_path / "out")

    assert _worktrees() == truoc


def test_backtest_that_bai_khong_de_lai_worktree(tmp_path: Path) -> None:
    """Nhánh khác: ref HỢP LỆ nhưng backtest nổ (fixture không tồn tại)."""
    truoc = _worktrees()

    with pytest.raises(RuntimeError, match="thất bại"):
        run_at_ref(
            "HEAD",
            start=date(2024, 1, 1),
            end=date(2024, 1, 2),
            out_dir=tmp_path / "out",
            fixture=tmp_path / "khong-co.parquet",
        )

    assert _worktrees() == truoc


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def test_cli_thoat_1_khi_lech(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mã thoát LÀ cổng — CI đọc nó. In "LỆCH" mà thoát 0 nghĩa là cổng
    không chặn gì cả."""
    import ops.compare_versions as cv

    monkeypatch.setattr(
        cv, "compare_versions",
        lambda *a, **k: ComparisonReport("A", "B", 1, 1, (Divergence("x", "regime_id", "0", "1"),)),
    )

    assert cv.main(["--ref-a", "A", "--ref-b", "B"]) == 1


def test_cli_thoat_0_khi_khop(monkeypatch: pytest.MonkeyPatch) -> None:
    import ops.compare_versions as cv

    monkeypatch.setattr(cv, "compare_versions", lambda *a, **k: ComparisonReport("A", "A", 1, 1))

    assert cv.main(["--ref-a", "A", "--ref-b", "A"]) == 0


# ----------------------------------------------------------------------
# ĐẦU-CUỐI THẬT — git worktree thật, backtest thật
# ----------------------------------------------------------------------


@pytest.mark.slow
def test_dau_cuoi_HEAD_so_voi_chinh_no() -> None:
    """Nghiệm thu 12c #1. Mock không thay được test này: nó là thứ duy nhất
    chứng minh `git worktree` + script sinh lúc chạy + đọc fixture bằng
    đường dẫn tuyệt đối THỰC SỰ hoạt động.

    Đã bắt được hai lỗi thật ở lần chạy đầu: `pd.Timestamp` tz-naive so với
    index tz-aware của fixture, và worktree không được dọn khi ref sai.
    """
    ra = compare_versions("HEAD", "HEAD", date(2024, 1, 1), date(2024, 7, 2))

    assert ra.ok, ra.render()
    assert ra.n_bars_a == ra.n_bars_b > 100


@pytest.mark.slow
def test_dau_cuoi_khong_goi_mang() -> None:
    """Fixture, không phải `HistoryLoader`. Script chạy trong worktree được
    SINH LÚC CHẠY nên nó đọc fixture của cây hiện tại — hai ref chắc chắn
    nhận cùng một byte dữ liệu, và một khác biệt dữ liệu không bị đọc nhầm
    thành khác biệt logic."""
    import ops.compare_versions as cv

    assert "read_parquet" in cv._RUNNER
    assert "HistoryLoader" not in cv._RUNNER
    assert sys.executable
