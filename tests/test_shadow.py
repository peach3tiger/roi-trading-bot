"""Phase 12c §B — `ops/shadow_runner.py` + `ops/shadow_diff.py`.

Phép kiểm quan trọng nhất ở đây là `test_khong_co_duong_nao_toi_tang_dat_lenh`:
nó đọc MÃ NGUỒN, không quan sát hành vi. Lý do là "không có đường tới
`order_executor`" không quan sát được từ hành vi — một shadow runner đúng
và một shadow runner chưa từng gặp tình huống đặt lệnh trông y hệt nhau.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pytest

from ops.compare_versions import EXACT_FIELDS
from ops.shadow_diff import (
    MIN_HOURS,
    OBSERVED_ONLY,
    RECOMMENDED_HOURS,
    BarRecord,
    diff,
    enough_coverage,
    load_dir,
    load_log,
    parse_trace_log,
)
from ops.shadow_runner import _rules_as_dict, _ShadowLogger, shadow_log_path

_ROOT = Path(__file__).resolve().parent.parent
_SHADOW_SRC = _ROOT / "ops" / "shadow_runner.py"


# ----------------------------------------------------------------------
# CHẶN Ở TẦNG KIẾN TRÚC — nghiệm thu 12c #3
# ----------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    """Mọi module được import, đọc bằng AST.

    AST chứ KHÔNG grep — cùng bài học §C.2 của chính prompt này: grep tìm
    `.predict(` từng bắt nhầm docstring đang GIẢI THÍCH vì sao không được
    dùng `predict()`, và "công cụ sai bắt code phải chiều nó" là cái giá
    không đáng trả. Ở đây docstring của `shadow_runner.py` cố ý nhắc tên
    `order_executor` để giải thích lệnh cấm; một phép grep sẽ báo động vì
    chính lời giải thích đó.
    """
    cay = ast.parse(path.read_text(encoding="utf-8"))
    ten: set[str] = set()
    for node in ast.walk(cay):
        if isinstance(node, ast.Import):
            ten.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            ten.add(node.module)
            ten.update(f"{node.module}.{a.name}" for a in node.names)
    return ten


def test_khong_co_duong_nao_toi_tang_dat_lenh() -> None:
    """Ràng buộc #2 của Phase 12c. Chặn bằng KIẾN TRÚC, không bằng cờ:
    một cờ `dry_run=True` có thể bị lật nhầm — bởi biến môi trường, một
    dòng config, một lần copy-paste — còn một import không tồn tại thì
    không có cách nào bị lật."""
    modules = _imported_modules(_SHADOW_SRC)

    cam = {m for m in modules if "order_executor" in m}
    assert not cam, f"shadow_runner import tầng đặt lệnh: {sorted(cam)}"


def test_khong_goi_submit_order_hay_close_position() -> None:
    """Import là một nửa; nửa kia là LỜI GỌI. Một `getattr(mod, "submit_order")`
    không xuất hiện trong danh sách import."""
    cay = ast.parse(_SHADOW_SRC.read_text(encoding="utf-8"))

    goi = {
        node.func.attr
        for node in ast.walk(cay)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert not ({"submit_order", "close_position", "modify_stop", "cancel_order"} & goi)


def test_validator_AST_cua_du_an_cung_dong_y() -> None:
    """`config/validate.py::check_shadow_runner_no_executor` là cổng chạy
    trước mỗi lần khởi động bot. Trước Phase 12c nó ĐẠT một cách RỖNG vì
    `ops/shadow_runner.py` không tồn tại — giờ nó kiểm thật."""
    from config.validate import check_shadow_runner_no_executor

    assert _SHADOW_SRC.exists(), "file phải tồn tại, nếu không phép kiểm lại rỗng"
    assert check_shadow_runner_no_executor() == []


# ----------------------------------------------------------------------
# Ghi log — cùng định dạng trace với main.py
# ----------------------------------------------------------------------


def test_shadow_log_mot_file_mot_ngay(tmp_path: Path) -> None:
    from datetime import date

    p = shadow_log_path(date(2026, 8, 14), base=tmp_path)

    assert p.name == "2026-08-14.jsonl"
    assert p.parent == tmp_path


def test_shadow_logger_ghi_jsonl_kem_trace(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from monitoring import trace as t

    t.set_bar_trace(datetime(2026, 8, 14, tzinfo=timezone.utc), "BTCUSDT")
    log = _ShadowLogger(tmp_path / "a.jsonl")

    t.log_layer(log, t.LAYER_COMPOSE, final="0.30", capped_by="trend_gate")

    d = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8").strip())
    assert d["layer"] == "compose"
    assert d["capped_by"] == "trend_gate"
    assert d["trace"] == "2026-08-14T00:00:00+00:00:BTCUSDT"


def test_instrument_rules_qua_str_khong_qua_float() -> None:
    """`base_precision` quyết định làm tròn qty (CLAUDE.md #3). Ép float ở
    đây làm mất đúng chữ số cuối mà nó tồn tại để giữ."""

    class _Rules:
        symbol = "BTCUSDT"
        base_precision = Decimal("0.000001")
        tick_size = Decimal("0.01")

    d = _rules_as_dict(_Rules())

    assert d is not None
    assert d["base_precision"] == "0.000001"
    assert all(isinstance(v, str) for v in d.values())


def test_instrument_rules_None_thi_None() -> None:
    assert _rules_as_dict(None) is None


# ----------------------------------------------------------------------
# MỘT parser cho HAI nguồn
# ----------------------------------------------------------------------


def _dong(trace: str, layer: str, **f: Any) -> str:
    return json.dumps({"trace": trace, "layer": layer, "message": layer, **f})


def _bar_lines(trace: str, *, hmm: str = "0.95", cap: str = "1.00", final: str = "0.95",
               regime_id: str = "1", rules: Optional[dict] = None, **extra: Any) -> list[str]:
    return [
        _dong(trace, "features", n_features=8),
        _dong(trace, "hmm", alloc_out=hmm, regime_id=regime_id),
        _dong(trace, "trend_gate", cap=cap),
        _dong(trace, "risk", cap=final),
        _dong(trace, "compose", final=final, capped_by="hmm", instrument_rules=rules, **extra),
    ]


def test_gop_nhieu_dong_thanh_mot_bar() -> None:
    ban_ghi = parse_trace_log(_bar_lines("T1"))

    assert set(ban_ghi) == {"T1"}
    r = ban_ghi["T1"]
    assert (r.regime_id, r.hmm_allocation, r.trend_gate_cap, r.final_allocation) == (
        "1", "0.95", "1.00", "0.95",
    )


def test_bo_qua_dong_khong_phai_json_va_dong_khong_co_trace() -> None:
    """Cả hai file đều có thể lẫn dòng log thường. Một parser chết vì một
    dòng lạ sẽ biến "có khác biệt" thành "công cụ hỏng"."""
    lines = ["khong phai json", json.dumps({"message": "khong co trace"}), *_bar_lines("T1")]

    assert set(parse_trace_log(lines)) == {"T1"}


def test_dong_trace_rong_bi_bo_qua() -> None:
    """`NO_TRACE` ("-") là dòng NGOÀI phạm vi bar — gộp nó vào sẽ tạo một
    "bar" giả gom mọi dòng không thuộc bar nào."""
    from monitoring.trace import NO_TRACE

    lines = [_dong(NO_TRACE, "compose", final="0.5"), *_bar_lines("T1")]

    assert set(parse_trace_log(lines)) == {"T1"}


def test_cung_mot_parser_doc_duoc_ca_hai_nguon() -> None:
    """Đây là điều §C.4 mua được. Hai nguồn, một hàm đọc — một parser
    riêng cho từng bên sẽ trôi lệch, và lúc đó "hai bên khớp" chỉ nghĩa là
    hai parser đồng ý với nhau."""
    shadow = parse_trace_log(_bar_lines("T1"))
    production = parse_trace_log(_bar_lines("T1"))

    assert shadow == production


# ----------------------------------------------------------------------
# So sánh — bốn trường TIÊU CHÍ
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "doi,ten_truong",
    [
        ({"regime_id": "2"}, "regime_id"),
        ({"hmm": "0.60"}, "hmm_allocation"),
        ({"cap": "0.30"}, "trend_gate_cap"),
        ({"final": "0.30"}, "final_allocation"),
    ],
)
def test_moi_truong_tieu_chi_deu_bi_bat(doi: dict, ten_truong: str) -> None:
    bao_cao = diff(parse_trace_log(_bar_lines("T1", **doi)), parse_trace_log(_bar_lines("T1")))

    assert not bao_cao.ok
    assert [d.field_name for d in bao_cao.diffs] == [ten_truong]


def test_bon_truong_lay_TU_compare_versions() -> None:
    """Đọc từ `ops/compare_versions.py`, không gõ lại. Hai chỗ gõ tay cùng
    một danh sách sẽ lệch nhau, và bên thiếu hơn âm thầm quyết định mức
    bảo vệ thật."""
    import ops.shadow_diff as sd

    assert sd.EXACT_FIELDS is EXACT_FIELDS


def test_khop_hoan_toan_thi_ok() -> None:
    bao_cao = diff(parse_trace_log(_bar_lines("T1")), parse_trace_log(_bar_lines("T1")))

    assert bao_cao.ok
    assert bao_cao.n_common == 1


def test_instrument_rules_lech_thi_bat() -> None:
    """Quy tắc làm tròn của SÀN — một trong bốn câu hỏi duy nhất shadow
    mode tồn tại để trả lời."""
    a = parse_trace_log(_bar_lines("T1", rules={"base_precision": "0.000001"}))
    b = parse_trace_log(_bar_lines("T1", rules={"base_precision": "0.001"}))

    bao_cao = diff(a, b)

    assert [d.field_name for d in bao_cao.diffs] == ["instrument_rules"]


def test_do_tre_va_lech_dong_ho_CHI_GHI_NHAN() -> None:
    """Hai tiến trình gọi mạng ở hai thời điểm khác nhau thì độ trễ khác
    nhau là đương nhiên. Biến chúng thành tiêu chí sẽ tạo một cổng đỏ ngẫu
    nhiên, và một cổng đỏ ngẫu nhiên sẽ bị vô hiệu hoá trong một tuần."""
    a = parse_trace_log(_bar_lines("T1", clock_skew_ms=12.0, api_latency_ms=250.0))
    b = parse_trace_log(_bar_lines("T1", clock_skew_ms=900.0, api_latency_ms=30.0))

    bao_cao = diff(a, b)

    assert bao_cao.ok, "độ trễ lệch KHÔNG được làm cổng đỏ"
    assert bao_cao.observed["api_latency_ms"]
    assert "chênh trung bình" in bao_cao.render()


def test_hai_truong_chi_ghi_nhan_dung_nhu_khai_bao() -> None:
    assert OBSERVED_ONLY == ("clock_skew_ms", "api_latency_ms")


def test_bar_le_mot_ben_bi_bo_qua_nhung_duoc_DEM() -> None:
    """Shadow khởi động sau production vài phút là bình thường. Nhưng "bỏ
    qua" mà không đếm sẽ biến shadow chạy 20 phút thành "khớp 100%"."""
    shadow = parse_trace_log(_bar_lines("T1") + _bar_lines("T2"))
    production = parse_trace_log(_bar_lines("T1"))

    bao_cao = diff(shadow, production)

    assert (bao_cao.n_shadow, bao_cao.n_production, bao_cao.n_common) == (2, 1, 1)
    assert bao_cao.ok


def test_khong_co_bar_chung_thi_KHONG_phai_ok() -> None:
    """Cổng chưa so gì mà báo xanh là đúng loại cổng rỗng `CLAUDE.md` #19
    sinh ra để chặn."""
    bao_cao = diff(parse_trace_log(_bar_lines("T1")), parse_trace_log(_bar_lines("T2")))

    assert bao_cao.n_common == 0
    assert not bao_cao.ok
    assert "CHƯA ĐỦ DỮ LIỆU" in bao_cao.render()


def test_thieu_truong_o_MOT_ben_la_khac_biet() -> None:
    thieu = [d for d in _bar_lines("T1") if '"layer": "trend_gate"' not in d]

    bao_cao = diff(parse_trace_log(thieu), parse_trace_log(_bar_lines("T1")))

    assert [d.field_name for d in bao_cao.diffs] == ["trend_gate_cap"]
    assert "<thiếu>" in bao_cao.render()


# ----------------------------------------------------------------------
# Độ phủ 24–48 giờ
# ----------------------------------------------------------------------


def test_duoi_24h_la_chua_du() -> None:
    du, ly_do = enough_coverage(0)

    assert not du
    assert str(MIN_HOURS) in ly_do


def test_dung_24h_la_du_nhung_duoi_khuyen_nghi() -> None:
    du, ly_do = enough_coverage(1)

    assert du
    assert str(RECOMMENDED_HOURS) in ly_do
    assert "dưới mức khuyến nghị" in ly_do


def test_48h_dat_khuyen_nghi() -> None:
    du, ly_do = enough_coverage(2)

    assert du
    assert "đạt mức khuyến nghị" in ly_do


# ----------------------------------------------------------------------
# Đọc file / thư mục
# ----------------------------------------------------------------------


def test_load_log_file_khong_ton_tai_thi_rong(tmp_path: Path) -> None:
    assert load_log(tmp_path / "khong-co.jsonl") == {}


def test_load_dir_gop_nhieu_ngay(tmp_path: Path) -> None:
    """Shadow chạy 24–48 giờ nên nó LUÔN nằm trên >= 2 file."""
    (tmp_path / "2026-08-13.jsonl").write_text("\n".join(_bar_lines("T1")), encoding="utf-8")
    (tmp_path / "2026-08-14.jsonl").write_text("\n".join(_bar_lines("T2")), encoding="utf-8")

    assert set(load_dir(tmp_path)) == {"T1", "T2"}


def test_cli_thoat_1_khi_chua_du_do_phu(tmp_path: Path) -> None:
    """Mã thoát LÀ cổng. "Khớp 100% trên 0 bar" mà thoát 0 là cổng rỗng."""
    import ops.shadow_diff as sd

    (tmp_path / "2026-08-14.jsonl").write_text("\n".join(_bar_lines("T1")), encoding="utf-8")
    prod = tmp_path / "regime.log"
    prod.write_text("\n".join(_bar_lines("T9")), encoding="utf-8")

    assert sd.main(["--shadow-dir", str(tmp_path), "--production-log", str(prod)]) == 1


# ----------------------------------------------------------------------
# `capped_by` — nghiệm thu 12c #6
# ----------------------------------------------------------------------


def test_capped_by_trend_gate_va_risk_deu_dung() -> None:
    """Hai ca §C.3 đòi dựng: một ca trend gate là tầng giới hạn, một ca
    risk manager là tầng giới hạn. `capped_by` là trường có giá trị nhất
    trong thiết kế — kiến trúc là `min()` ba tầng, nên câu hỏi đầu tiên
    khi bất thường luôn là TẦNG NÀO đang giới hạn."""
    from monitoring.trace import capped_by

    assert capped_by(Decimal("0.95"), Decimal("0.30"), Decimal("0.30")) == "trend_gate"
    assert capped_by(Decimal("0.95"), Decimal("1.00"), Decimal("0.50")) == "risk"


def test_capped_by_di_vao_log_va_doc_lai_duoc() -> None:
    ban_ghi = parse_trace_log(_bar_lines("T1"))

    assert ban_ghi["T1"].capped_by == "hmm"


# ----------------------------------------------------------------------
# `trace_id` tất định — nghiệm thu 12c #7
# ----------------------------------------------------------------------


def test_chay_lai_cung_bar_cho_trace_id_giong_het() -> None:
    """Cùng lý do `orderLinkId` tất định (CLAUDE.md #8): chạy lại cùng bar
    phải cho cùng id, để log backtest/shadow/forward/live so trực tiếp
    được mà không cần khớp mờ."""
    from datetime import datetime, timezone

    from monitoring.trace import new_bar_trace

    ts = datetime(2026, 8, 6, tzinfo=timezone.utc)

    assert new_bar_trace(ts, "BTCUSDT") == new_bar_trace(ts, "BTCUSDT")
    assert new_bar_trace(ts, "BTCUSDT") != new_bar_trace(ts, "ETHUSDT")


def test_grep_mot_trace_id_dung_lai_du_chuoi(tmp_path: Path) -> None:
    """Nghiệm thu 12c #5: grep một `trace_id` bất kỳ -> tái dựng đủ chuỗi
    từ `features` tới tầng cuối.

    Shadow có NĂM tầng, không sáu: `rebalance` là quyết định ĐẶT LỆNH và
    shadow không có tầng đó. Ghi một dòng "skipped" ở đó sẽ vẽ ra một bước
    chưa từng chạy.
    """
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(_bar_lines("2026-08-06T00:00:00+00:00:BTCUSDT")), encoding="utf-8")

    khop = [
        json.loads(d)
        for d in f.read_text(encoding="utf-8").splitlines()
        if "2026-08-06T00:00:00+00:00:BTCUSDT" in d
    ]

    assert [d["layer"] for d in khop] == ["features", "hmm", "trend_gate", "risk", "compose"]


def test_ban_ghi_bar_la_frozen() -> None:
    """`BarRecord` bất biến: `diff()` không được sửa dữ liệu nó đang so."""
    r = BarRecord(trace="T1")

    with pytest.raises((AttributeError, TypeError)):
        r.trace = "T2"  # type: ignore[misc]
