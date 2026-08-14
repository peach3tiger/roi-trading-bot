"""Phase 12d §D + §E — `scripts/emergency_kill.py`, `scripts/recovery_checklist.py`.

Hai mục nghiệm thu ở đây là hai mặt của cùng một sự thật về hệ thống này:

- §D: chỉ lệnh vào bị huỷ, lệnh bảo vệ còn nguyên.
- §E: vị thế không có stop là mục ưu tiên cao nhất.

Và một sự thật ĐO ĐƯỢC làm cả hai nặng hơn §D/§E dự đoán:
`broker/order_executor.py::modify_stop()` KHÔNG gửi lệnh nào lên sàn.
`test_stop_khong_ton_tai_tren_san` ghim nó — nếu ngày nào đó stop được đẩy
lên sàn thật, test đó đỏ và buộc đọc lại cả hai script.
"""

from __future__ import annotations

import signal
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.emergency_kill import (
    KillSummary,
    classify_orders,
    is_protective,
    run,
    stop_bot,
    write_halt_lock,
)
from scripts.recovery_checklist import (
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARN,
    build_report,
    check_allocation_match,
    check_locks,
    check_orphan_orders,
    check_stop_protection,
)

_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# Sự thật nền: stop KHÔNG nằm trên sàn
# ----------------------------------------------------------------------


def test_stop_khong_ton_tai_tren_san() -> None:
    """`modify_stop()` chỉ ghi `_current_stops` trong bộ nhớ; không có
    lệnh `STOP_LOSS_LIMIT` nào được gửi đi.

    Đây là TIỀN ĐỀ của mọi thứ còn lại trong file này. Khi nó đổi (stop
    được đẩy lên sàn thật), test này đỏ và buộc đọc lại cả
    `emergency_kill.py` lẫn `recovery_checklist.py` — cả hai đang nói với
    người vận hành rằng vị thế KHÔNG được canh sau khi bot dừng.
    """
    broker = (_ROOT / "broker").rglob("*.py")
    for f in broker:
        src = f.read_text(encoding="utf-8")
        for dau_hieu in ("stopPrice", "STOP_LOSS", "stop_price"):
            assert dau_hieu not in src, (
                f"{f.name} có `{dau_hieu}` — stop có vẻ ĐÃ được đẩy lên sàn. "
                "Đọc lại scripts/emergency_kill.py và scripts/recovery_checklist.py: "
                "cả hai đang giả định ngược lại."
            )


# ----------------------------------------------------------------------
# §D.2 — phân loại lệnh
# ----------------------------------------------------------------------


class _Order:
    def __init__(self, order_id: str, **thuoc_tinh: Any) -> None:
        self.order_id = order_id
        for k, v in thuoc_tinh.items():
            setattr(self, k, v)


def test_lenh_LIMIT_vao_thi_huy() -> None:
    assert not is_protective(_Order("v1", order_type="LIMIT"))


@pytest.mark.parametrize(
    "lenh",
    [
        _Order("s1", reduce_only=True),
        _Order("s2", reduceOnly=True),
        _Order("s3", order_type="STOP_LOSS_LIMIT"),
        _Order("s4", order_type="TAKE_PROFIT"),
        _Order("s5", order_type="LIMIT", raw={"reduceOnly": True}),
        _Order("s6", order_type="LIMIT", raw={"type": "STOP_LOSS"}),
        _Order("s7", order_type="LIMIT", raw={"stopPrice": "58000"}),
        _Order("s8", order_type="LIMIT", info={"stopPrice": "58000"}),
    ],
    ids=lambda o: o.order_id,
)
def test_moi_dau_hieu_bao_ve_deu_duoc_nhan_ra(lenh: Any) -> None:
    """Nhiều dấu hiệu vì `broker/base.py::Order` chưa có `reduceOnly` và
    ccxt trả cờ đó ở `raw`/`info` tuỳ sàn."""
    assert is_protective(lenh)


def test_thieu_thong_tin_thi_COI_LA_bao_ve() -> None:
    """Hướng nghiêng CÓ CHỦ Ý: bỏ sót một lệnh vào (vẫn còn chờ, huỷ tay
    được) rẻ hơn vô hạn so với huỷ nhầm một lệnh stop (vị thế trần trụi
    ngay giây tiếp theo)."""

    class _KhongBiet:
        order_id = "?"

    assert is_protective(_KhongBiet())


def test_chi_huy_lenh_vao_giu_nguyen_stop() -> None:
    """NGHIỆM THU 12d #8. Điểm dễ sai nhất và hậu quả nặng nhất của §D."""
    lenh = [
        _Order("vao-1", order_type="LIMIT"),
        _Order("stop-1", order_type="STOP_LOSS_LIMIT"),
        _Order("rebalance-1", order_type="LIMIT"),
        _Order("stop-2", reduce_only=True),
    ]

    plan = classify_orders(lenh)

    assert {o.order_id for o in plan.to_cancel} == {"vao-1", "rebalance-1"}
    assert {o.order_id for o in plan.protective} == {"stop-1", "stop-2"}


def test_moi_lenh_thuoc_dung_mot_nhom() -> None:
    """Không lệnh nào rơi vào cả hai, không lệnh nào rơi ra ngoài — một
    lệnh bị bỏ quên là một lệnh không ai huỷ và cũng không ai biết."""
    lenh = [_Order("a", order_type="LIMIT"), _Order("b", reduce_only=True)]

    plan = classify_orders(lenh)

    assert len(plan.to_cancel) + len(plan.protective) == len(lenh)


# ----------------------------------------------------------------------
# §D.1 — sáu bước
# ----------------------------------------------------------------------


class _FakeExchange:
    def __init__(self, orders: list[Any], positions: Optional[list[Any]] = None) -> None:
        self._orders = orders
        self._positions = positions or []
        self.cancelled: list[str] = []
        self.closed: list[str] = []

    def get_open_orders(self) -> list[Any]:
        return list(self._orders)

    def get_positions(self) -> list[Any]:
        return list(self._positions)

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    def close_position(self, *a: Any, **k: Any) -> Any:
        self.closed.append("closed")
        raise AssertionError("emergency_kill KHÔNG được đóng vị thế spot (§D.3)")


class _Pos:
    def __init__(self, symbol: str = "BTCUSDT", qty: str = "0.5") -> None:
        self.symbol = symbol
        self.qty = Decimal(qty)


def _chay(tmp_path: Path, exchange: _FakeExchange, **kw: Any) -> KillSummary:
    return run(
        "test",
        exchange_client=exchange,
        pid=None,
        halt_lock=tmp_path / "trading_halted.lock",
        snapshot=tmp_path / "state_snapshot.json",
        stop_bot_fn=lambda *_a, **_k: "SIGTERM",
        **kw,
    )


def test_huy_dung_lenh_vao_khong_dung_toi_stop(tmp_path: Path) -> None:
    ex = _FakeExchange([_Order("vao", order_type="LIMIT"), _Order("stop", reduce_only=True)])

    tom_tat = _chay(tmp_path, ex)

    assert ex.cancelled == ["vao"]
    assert tom_tat.protective_kept == ("stop",)


def test_KHONG_dong_vi_the_spot(tmp_path: Path) -> None:
    """§D.3. `_FakeExchange.close_position` ném `AssertionError` — nếu
    script gọi tới nó, test đỏ với thông điệp nói rõ vì sao."""
    ex = _FakeExchange([], [_Pos()])

    _chay(tmp_path, ex)

    assert ex.closed == []


def test_ghi_halt_lock_TRUOC_khi_huy_lenh(tmp_path: Path) -> None:
    """Nếu script chết giữa chừng, thứ còn lại phải là "đã cấm giao dịch"
    chứ không phải "đã huỷ vài lệnh rồi thôi"."""
    lock = tmp_path / "trading_halted.lock"
    thay_lock_luc_huy: list[bool] = []

    class _Ghi(_FakeExchange):
        def cancel_order(self, order_id: str) -> bool:
            thay_lock_luc_huy.append(lock.exists())
            return super().cancel_order(order_id)

    _chay(tmp_path, _Ghi([_Order("vao", order_type="LIMIT")]))

    assert thay_lock_luc_huy == [True]


def test_huy_that_bai_duoc_bao_cao_khong_bi_nuot(tmp_path: Path) -> None:
    class _Hong(_FakeExchange):
        def cancel_order(self, order_id: str) -> bool:
            raise ConnectionError("sàn từ chối")

    tom_tat = _chay(tmp_path, _Hong([_Order("vao", order_type="LIMIT")]))

    assert tom_tat.cancelled == ()
    assert len(tom_tat.cancel_failed) == 1
    assert "KIỂM TRA TAY TRÊN SÀN" in tom_tat.render()


def test_tom_tat_canh_bao_vi_the_khong_con_duoc_canh(tmp_path: Path) -> None:
    """Rủi ro THẬT của script này, ngược với thứ §D.2 dự đoán: giết bot
    chính là gỡ bỏ toàn bộ stop."""
    ra = _chay(tmp_path, _FakeExchange([], [_Pos()])).render()

    assert "KHÔNG nằm trên sàn" in ra
    assert "đặt stop THỦ CÔNG" in ra.lower() or "stop THỦ CÔNG" in ra


def test_khong_co_vi_the_thi_khong_canh_bao_thua(tmp_path: Path) -> None:
    """Cảnh báo phát khi không cần dạy người đọc bỏ qua nó."""
    ra = _chay(tmp_path, _FakeExchange([], [])).render()

    assert "KHÔNG nằm trên sàn" not in ra


def test_halt_lock_ghi_ro_khong_dong_vi_the(tmp_path: Path) -> None:
    p = write_halt_lock("lý do test", path=tmp_path / "l.lock")
    noi_dung = p.read_text(encoding="utf-8")

    assert "lý do test" in noi_dung
    assert "KHÔNG bị đóng" in noi_dung
    assert "recovery_checklist" in noi_dung


def test_stop_bot_dung_lai_terminate_cua_watchdog() -> None:
    """Hai bản của cùng một thứ tự kết thúc sẽ trôi lệch, và bản nào cũng
    có thể là bản đang chạy lúc khẩn cấp."""
    gui: list[int] = []

    dung = stop_bot(
        1234,
        grace_seconds=1.0,
        send=lambda _p, s: gui.append(s),
        alive=lambda _p: False,
        sleep=lambda _s: None,
    )

    assert gui == [signal.SIGTERM]
    assert dung == "SIGTERM"


def test_pid_None_thi_khong_gui_gi() -> None:
    assert stop_bot(None) == "none"


# ----------------------------------------------------------------------
# §E — recovery checklist
# ----------------------------------------------------------------------


def test_vi_the_khong_co_stop_LA_uu_tien_cao_nhat() -> None:
    """NGHIỆM THU 12d #9."""
    ket_qua = check_stop_protection({}, [_Pos()])

    assert len(ket_qua) == 1
    assert ket_qua[0].severity == SEV_CRITICAL
    assert "KHÔNG CÓ STOP" in ket_qua[0].title


def test_muc_uu_tien_cao_nhat_dung_DAU_bao_cao() -> None:
    """Người đọc một báo cáo sự cố đọc từ trên xuống và dừng khi thấy đủ."""
    ra = build_report(snapshot={}, positions=[_Pos()], lock_dir=Path("/khong-ton-tai")).render()
    i_critical = ra.index(SEV_CRITICAL)
    i_checklist = ra.index("Trước khi khởi động lại")

    assert i_critical < i_checklist


def test_co_stop_trong_snapshot_VAN_la_nghiem_trong() -> None:
    """Snapshot chỉ nói bot ĐỊNH dùng mức nào, không nói sàn đang giữ gì —
    và sàn không giữ gì cả."""
    ket_qua = check_stop_protection({"current_stop_loss": "58000"}, [_Pos()])

    assert ket_qua[0].severity == SEV_CRITICAL
    assert "CHỈ CÓ TRONG SNAPSHOT" in ket_qua[0].title


def test_khong_co_vi_the_thi_chi_la_thong_tin() -> None:
    """Ba trạng thái, ba mức. Gộp lại là bỏ mất thông tin cần nhất: "không
    có vị thế" và "có vị thế không stop" đều cho `stop is None`."""
    ket_qua = check_stop_protection({}, [])

    assert ket_qua[0].severity == SEV_INFO


def test_qty_0_khong_tinh_la_co_vi_the() -> None:
    assert check_stop_protection({}, [_Pos(qty="0")])[0].severity == SEV_INFO


def test_san_co_vi_the_snapshot_noi_khong_LA_nghiem_trong() -> None:
    """Bot khởi động lại sẽ coi như đang flat và có thể mua chồng lên vị
    thế đang có."""
    ket_qua = check_allocation_match({"current_allocation_pct": "0"}, [_Pos()])

    assert ket_qua[0].severity == SEV_CRITICAL
    assert "TIN SÀN" in ket_qua[0].action


def test_snapshot_noi_co_san_noi_khong_thi_chi_can_xem() -> None:
    ket_qua = check_allocation_match({"current_allocation_pct": "0.95"}, [])

    assert ket_qua[0].severity == SEV_WARN


def test_khop_nhau_thi_khong_bao_gi() -> None:
    assert check_allocation_match({"current_allocation_pct": "0.95"}, [_Pos()]) == []


def test_lenh_mo_duoc_bao_o_muc_CAN_XEM_khong_phai_nghiem_trong() -> None:
    """snapshot không lưu danh sách lệnh nên không kết luận chắc chắn
    được. Báo sai lên NGHIÊM TRỌNG sẽ làm mục đó mất giá."""
    ket_qua = check_orphan_orders({"last_processed_bar": "2026-08-13"}, [_Order("x")])

    assert ket_qua[0].severity == SEV_WARN


def test_khong_co_lenh_thi_im_lang() -> None:
    assert check_orphan_orders({}, []) == []


def test_lock_duoc_in_NOI_DUNG_khong_chi_co_hay_khong(tmp_path: Path) -> None:
    """Nội dung chính là thứ nói vì sao nó tồn tại."""
    (tmp_path / "trading_halted.lock").write_text("PEAK_HALT lúc 2026-08-14", encoding="utf-8")

    ket_qua = check_locks(base=tmp_path)

    assert len(ket_qua) == 1
    assert "PEAK_HALT" in ket_qua[0].detail
    assert ket_qua[0].severity == SEV_CRITICAL


def test_ca_hai_lock_deu_duoc_kiem(tmp_path: Path) -> None:
    (tmp_path / "trading_halted.lock").write_text("a", encoding="utf-8")
    (tmp_path / "data_quality.lock").write_text("b", encoding="utf-8")

    assert len(check_locks(base=tmp_path)) == 2


def test_khong_co_lock_thi_khong_bao(tmp_path: Path) -> None:
    assert check_locks(base=tmp_path) == []


def test_bao_cao_doc_watchdog_kill() -> None:
    ra = build_report(
        watchdog_kill={
            "killed_at_utc": "2026-08-14T01:00:00Z",
            "reason": "heartbeat_stale",
            "detail": "cũ 120s",
            "signal_used": "SIGKILL",
        },
        lock_dir=Path("/khong-ton-tai"),
    ).render()

    assert "heartbeat_stale" in ra
    assert "SIGKILL" in ra


def test_bao_cao_luon_co_checklist_thu_cong() -> None:
    """§E.8 — mục cuối là thứ con người phải tự xác nhận."""
    ra = build_report(lock_dir=Path("/khong-ton-tai")).render()

    assert "xác nhận THỦ CÔNG" in ra
    assert "KHÔNG khởi động lại bot" in ra


def test_bao_cao_noi_ro_san_KHONG_giu_stop() -> None:
    ra = build_report(snapshot={"current_stop_loss": "58000"}, lock_dir=Path("/khong-ton-tai")).render()

    assert "sàn KHÔNG giữ stop" in ra


# ----------------------------------------------------------------------
# Ràng buộc Phase 12d
# ----------------------------------------------------------------------


def test_khong_script_nao_tu_khoi_dong_lai_bot() -> None:
    """Ràng buộc #2."""
    for ten in ("emergency_kill.py", "recovery_checklist.py"):
        src = (_ROOT / "scripts" / ten).read_text(encoding="utf-8")
        for cam in ("subprocess.Popen", "subprocess.run", "os.execv", "launchctl", "systemctl"):
            assert cam not in src, f"{ten} có vẻ tự khởi động lại bot qua {cam}"


def test_recovery_checklist_khong_ghi_gi() -> None:
    """Ràng buộc #3 — phát hiện và báo cáo, KHÔNG tự sửa."""
    src = (_ROOT / "scripts" / "recovery_checklist.py").read_text(encoding="utf-8")

    for cam in ("write_text", "unlink", "cancel_order", "submit_order", "close_position", "os.kill"):
        assert cam not in src, f"recovery_checklist tự sửa trạng thái qua {cam!r}"


def test_khong_script_nao_ghi_vao_forward() -> None:
    """Ràng buộc #4."""
    for ten in ("emergency_kill.py", "recovery_checklist.py"):
        src = (_ROOT / "scripts" / ten).read_text(encoding="utf-8")
        assert "forward" not in src


def test_emergency_kill_khong_ghi_de_state_snapshot() -> None:
    """Bot ghi snapshot mỗi bar VÀ một lần nữa khi nhận SIGTERM. Ghi đè từ
    đây chỉ có thể làm mất dữ liệu mới hơn."""
    src = (_ROOT / "scripts" / "emergency_kill.py").read_text(encoding="utf-8")
    than_ham = src[src.index("def _read_known_stops") :]

    assert "write_state_snapshot" not in src
    assert "read_text" in than_ham
    assert "write_text" not in than_ham
