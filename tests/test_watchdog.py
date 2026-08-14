"""Phase 12d §A — `monitoring/watchdog.py`.

Phần lớn file này kiểm `evaluate()`/`terminate()` bằng hàm thuần và tín
hiệu tiêm được. Nhưng hai mục nghiệm thu nói về TIẾN TRÌNH THẬT BỊ TREO,
và một watchdog chỉ được kiểm bằng mock là watchdog chưa bao giờ giết thứ
gì — nên `test_bot_treo_that_*` dựng một tiến trình con thật, `SIGSTOP`
nó, rồi để watchdog leo thang tới `SIGKILL`.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from monitoring.alerts import AlertType
from monitoring.watchdog import (
    REASON_PID_GONE,
    REASON_STALE,
    REASON_STUCK,
    Heartbeat,
    Watchdog,
    WatchdogConfig,
    evaluate,
    process_alive,
    read_heartbeat,
    terminate,
    write_heartbeat,
    write_kill_report,
)


def _hb(loop_seq: int = 1, pid: int = 4242) -> Heartbeat:
    return Heartbeat(pid=pid, updated_at=None, bar_ts="2026-08-14", loop_seq=loop_seq, raw={"pid": pid})


_SONG = {"alive": lambda _pid: True}
_CHET = {"alive": lambda _pid: False}


# ----------------------------------------------------------------------
# Ba quy tắc phát hiện — mỗi quy tắc một test, mỗi test đổi ĐÚNG một thứ
# ----------------------------------------------------------------------


def test_binh_thuong_thi_song() -> None:
    v = evaluate(_hb(), age_seconds=10, same_seq_count=1, config=WatchdogConfig(), **_SONG)

    assert v.alive


def test_heartbeat_cu_qua_thi_treo() -> None:
    v = evaluate(_hb(), age_seconds=120, same_seq_count=1, config=WatchdogConfig(), **_SONG)

    assert not v.alive and v.reason == REASON_STALE


def test_dung_90s_chua_phai_treo() -> None:
    """Biên: "> 90" nghĩa là đúng 90 chưa tính. `>` và `>=` trông giống
    nhau khi đọc lướt và khác hẳn khi chạy."""
    v = evaluate(_hb(), age_seconds=90, same_seq_count=1, config=WatchdogConfig(), **_SONG)

    assert v.alive


def test_loop_seq_dung_yen_thi_treo() -> None:
    """Quy tắc 1 MỘT MÌNH bỏ lọt ca này: file vẫn được ghi lại (mtime
    tươi, 10s) nhưng vòng lặp đứng."""
    v = evaluate(_hb(), age_seconds=10, same_seq_count=3, config=WatchdogConfig(), **_SONG)

    assert not v.alive and v.reason == REASON_STUCK


def test_loop_seq_dung_hai_lan_chua_du() -> None:
    v = evaluate(_hb(), age_seconds=10, same_seq_count=2, config=WatchdogConfig(), **_SONG)

    assert v.alive


def test_pid_bien_mat_thi_chet() -> None:
    v = evaluate(_hb(), age_seconds=10, same_seq_count=1, config=WatchdogConfig(), **_CHET)

    assert not v.alive and v.reason == REASON_PID_GONE


def test_chua_co_heartbeat_thi_KHONG_ket_luan() -> None:
    """Bot có thể CHƯA khởi động. Một watchdog giết tiến trình dựa trên
    một file chưa từng tồn tại là watchdog tự tạo ra sự cố."""
    v = evaluate(None, age_seconds=None, same_seq_count=0, config=WatchdogConfig())

    assert v.alive


def test_pid_chet_thang_hon_moi_quy_tac_khac() -> None:
    """Cả ba điều kiện cùng đúng -> lý do phải là `pid_gone`: gửi SIGTERM
    tới một PID đã chết là vô nghĩa, và nếu PID đó đã được cấp lại cho
    tiến trình khác thì đó là bắn nhầm."""
    v = evaluate(_hb(), age_seconds=999, same_seq_count=99, config=WatchdogConfig(), **_CHET)

    assert v.reason == REASON_PID_GONE


# ----------------------------------------------------------------------
# Cấu hình phải tự bác bỏ giá trị vô nghĩa
# ----------------------------------------------------------------------


def test_poll_lon_hon_stale_bi_tu_choi() -> None:
    """Poll 120s với ngưỡng 90s nghĩa là có thể bỏ lỡ trọn một chu kỳ —
    con số 90 chỉ còn là trang trí."""
    with pytest.raises(ValueError, match="NHỎ HƠN"):
        WatchdogConfig(stale_after_seconds=90, poll_seconds=120)


def test_stuck_checks_bang_1_bi_tu_choi() -> None:
    with pytest.raises(ValueError, match="đứng yên"):
        WatchdogConfig(stuck_checks=1)


# ----------------------------------------------------------------------
# Đếm loop_seq qua nhiều lần kiểm
# ----------------------------------------------------------------------


def test_dem_lien_tiep_reset_khi_loop_seq_tang(tmp_path: Path) -> None:
    """Bot chạy tiếp sau một lúc chậm -> bộ đếm phải về 1, không cộng dồn."""
    hb_file = tmp_path / "heartbeat.json"
    wd = Watchdog(WatchdogConfig(), heartbeat_file=hb_file)

    for seq in (5, 5, 6):
        write_heartbeat(hb_file, loop_seq=seq, pid=os.getpid())
        v = wd.check_once()

    assert v.alive
    assert wd._same_seq_count == 1


def test_ba_lan_cung_loop_seq_thi_bao_treo(tmp_path: Path) -> None:
    hb_file = tmp_path / "heartbeat.json"
    wd = Watchdog(WatchdogConfig(), heartbeat_file=hb_file)
    write_heartbeat(hb_file, loop_seq=7, pid=os.getpid())

    ket_qua = [wd.check_once() for _ in range(3)]

    assert [v.alive for v in ket_qua] == [True, True, False]
    assert ket_qua[-1].reason == REASON_STUCK


# ----------------------------------------------------------------------
# Ghi/đọc heartbeat
# ----------------------------------------------------------------------


def test_ghi_roi_doc_lai_duoc(tmp_path: Path) -> None:
    p = write_heartbeat(tmp_path / "hb.json", loop_seq=12, bar_ts="2026-08-13", pid=999)
    hb = read_heartbeat(p)

    assert hb is not None
    assert (hb.pid, hb.loop_seq, hb.bar_ts) == (999, 12, "2026-08-13")


def test_ghi_khong_de_lai_tmp(tmp_path: Path) -> None:
    write_heartbeat(tmp_path / "hb.json", loop_seq=1)

    assert [p.name for p in tmp_path.iterdir()] == ["hb.json"]


def test_ghi_khong_raise_khi_khong_ghi_duoc(tmp_path: Path) -> None:
    """Heartbeat là đường quan sát; volume đầy không được giết vòng lặp
    giao dịch. Hệ quả CÓ CHỦ Ý: watchdog sẽ thấy file cũ dần rồi kết luận
    bot treo — nghiêng về phía DỪNG khi không chắc, đúng hướng."""
    chan = tmp_path / "khong-phai-thu-muc"
    chan.write_text("x", encoding="utf-8")

    write_heartbeat(chan / "hb.json", loop_seq=1)


@pytest.mark.parametrize(
    "noi_dung", ["{ khong phai json", "[]", '{"pid": 1}', '{"loop_seq": 2}', '{"pid": "x", "loop_seq": 1}']
)
def test_heartbeat_hong_thi_None(tmp_path: Path, noi_dung: str) -> None:
    """Thiếu `pid` hoặc `loop_seq` -> không quyết định được gì. "Không
    quyết định được" phải khác "bot khoẻ"."""
    f = tmp_path / "hb.json"
    f.write_text(noi_dung, encoding="utf-8")

    assert read_heartbeat(f) is None


def test_pid_am_khong_bao_gio_song() -> None:
    assert not process_alive(0)
    assert not process_alive(-1)


# ----------------------------------------------------------------------
# §A.4 — thứ tự kết thúc
# ----------------------------------------------------------------------


class _GiaLapTienTrinh:
    """Tiến trình giả: sống tới khi nhận đủ `chet_sau_sigterm` giây."""

    def __init__(self, chet_sau_sigterm: float | None) -> None:
        self.tin_hieu: list[int] = []
        self.chet_sau = chet_sau_sigterm
        self.da_ngu = 0.0
        self._chet = False

    def send(self, _pid: int, sig: int) -> None:
        self.tin_hieu.append(sig)
        if sig == signal.SIGKILL:
            self._chet = True

    def alive(self, _pid: int) -> bool:
        if self._chet:
            return False
        if self.chet_sau is None:
            return True
        return self.da_ngu < self.chet_sau

    def sleep(self, s: float) -> None:
        self.da_ngu += s


def test_sigterm_truoc_khong_bao_gio_sigkill_thang() -> None:
    """Bot có thể đang giữa lúc gửi lệnh. SIGKILL ngay để lại lệnh mồ côi
    mà `state_snapshot.json` không kịp ghi."""
    gia = _GiaLapTienTrinh(chet_sau_sigterm=2.0)

    dung = terminate(1234, WatchdogConfig(), send=gia.send, alive=gia.alive, sleep=gia.sleep)

    assert gia.tin_hieu[0] == signal.SIGTERM
    assert dung == "SIGTERM"
    assert signal.SIGKILL not in gia.tin_hieu


def test_khong_chet_sau_30s_thi_sigkill() -> None:
    gia = _GiaLapTienTrinh(chet_sau_sigterm=None)

    dung = terminate(1234, WatchdogConfig(), send=gia.send, alive=gia.alive, sleep=gia.sleep)

    assert gia.tin_hieu == [signal.SIGTERM, signal.SIGKILL]
    assert dung == "SIGKILL"


def test_khong_cho_du_30s_khi_bot_thoat_som() -> None:
    """Poll từng giây thay vì ngủ trọn 30: trong 28 giây thừa, supervisor
    có thể đã khởi động lại một PID mới TRÙNG SỐ, và SIGKILL sẽ bắn nhầm
    tiến trình đó."""
    gia = _GiaLapTienTrinh(chet_sau_sigterm=2.0)

    terminate(1234, WatchdogConfig(), send=gia.send, alive=gia.alive, sleep=gia.sleep)

    assert gia.da_ngu <= 3.0, f"chờ {gia.da_ngu}s dù bot đã thoát sau 2s"


def test_pid_da_chet_thi_khong_gui_gi() -> None:
    def _khong_ton_tai(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    dung = terminate(1234, WatchdogConfig(), send=_khong_ton_tai, alive=lambda _p: False)

    assert dung == "none"


# ----------------------------------------------------------------------
# Báo cáo + cảnh báo + KHÔNG khởi động lại
# ----------------------------------------------------------------------


class _FakeAlertManager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, alert: Any) -> bool:
        self.sent.append(alert)
        return True


def test_bao_cao_ghi_du_bon_thu(tmp_path: Path) -> None:
    """§A.4 mục 3: thời điểm, lý do, tín hiệu đã dùng, heartbeat cuối."""
    from monitoring.watchdog import Verdict

    p = write_kill_report(
        Verdict(alive=False, reason=REASON_STALE, detail="cũ 120s"),
        _hb(loop_seq=42),
        "SIGKILL",
        path=tmp_path / "kill.json",
    )
    d = json.loads(p.read_text(encoding="utf-8"))

    assert d["reason"] == REASON_STALE
    assert d["signal_used"] == "SIGKILL"
    assert d["last_heartbeat"] is not None
    assert d["killed_at_utc"]


def test_bao_cao_ghi_ro_KHONG_khoi_dong_lai(tmp_path: Path) -> None:
    """Watchdog giết bot rồi supervisor khởi động lại ngay sẽ tạo một vòng
    lặp crash mà không ai để ý — biểu đồ uptime trông hoàn hảo."""
    from monitoring.watchdog import Verdict

    p = write_kill_report(Verdict(False, REASON_STALE, "x"), None, "SIGTERM", path=tmp_path / "k.json")
    d = json.loads(p.read_text(encoding="utf-8"))

    assert d["restarted"] is False
    assert "recovery_checklist" in d["ghi_chu"]


def test_watchdog_khong_co_duong_nao_khoi_dong_lai_bot() -> None:
    """Ràng buộc #2 của Phase 12d. Kiểm ở mức mã nguồn vì đây là thứ
    KHÔNG quan sát được từ hành vi: một watchdog không khởi động lại trông
    y hệt một watchdog chưa từng cần khởi động lại."""
    src = (Path(__file__).resolve().parent.parent / "monitoring" / "watchdog.py").read_text(
        encoding="utf-8"
    )

    for cam in ("subprocess.Popen", "subprocess.run", "os.execv", "os.spawn", "launchctl"):
        assert cam not in src, f"watchdog có vẻ tự khởi động lại bot qua {cam}"


def test_gui_canh_bao_WATCHDOG_KILL(tmp_path: Path) -> None:
    from monitoring.watchdog import Verdict

    am = _FakeAlertManager()
    wd = Watchdog(
        WatchdogConfig(),
        heartbeat_file=tmp_path / "hb.json",
        kill_report_file=tmp_path / "kill.json",
        alert_manager=am,
    )

    wd.handle_dead(Verdict(alive=False, reason=REASON_PID_GONE, detail="PID biến mất"))

    assert len(am.sent) == 1
    assert am.sent[0].alert_type is AlertType.WATCHDOG_KILL
    assert am.sent[0].severity == "CRITICAL"


def test_pid_gone_thi_khong_gui_tin_hieu(tmp_path: Path) -> None:
    """PID đã biến mất mà vẫn gửi SIGTERM: nếu số PID đó đã được cấp lại
    cho tiến trình khác thì watchdog vừa giết nhầm một tiến trình vô can."""
    from monitoring.watchdog import Verdict

    wd = Watchdog(
        WatchdogConfig(), heartbeat_file=tmp_path / "hb.json", kill_report_file=tmp_path / "k.json"
    )
    write_heartbeat(tmp_path / "hb.json", loop_seq=1, pid=999999)

    dung = wd.handle_dead(Verdict(alive=False, reason=REASON_PID_GONE, detail="x"))

    assert dung == "none"


# ----------------------------------------------------------------------
# NGHIỆM THU: tiến trình THẬT bị treo
# ----------------------------------------------------------------------


def _bot_gia(tmp_path: Path, ghi_heartbeat: bool) -> subprocess.Popen:
    """Tiến trình con thật: ghi heartbeat rồi ngủ. Không import gì của dự
    án để khởi động nhanh."""
    hb = tmp_path / "heartbeat.json"
    code = (
        "import json,os,sys,time\n"
        f"hb = {str(hb)!r}\n"
        "seq = 0\n"
        "while True:\n"
        "    seq += 1\n"
        f"    if {ghi_heartbeat}:\n"
        "        open(hb,'w').write(json.dumps({'pid': os.getpid(), 'loop_seq': seq,\n"
        "            'updated_at': '2026-08-14T00:00:00+00:00', 'bar_ts': None}))\n"
        "    time.sleep(0.05)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL)


def test_bot_treo_that_bi_phat_hien_va_bi_KILL(tmp_path: Path) -> None:
    """Nghiệm thu 12d #1, chạy THẬT: `SIGSTOP` một tiến trình con →
    heartbeat ngừng tươi → watchdog gửi SIGTERM (bị treo nên không xử lý
    được) → leo thang SIGKILL → ghi `watchdog_kill.json`.

    Ngưỡng rút ngắn (1.0s thay vì 90s, grace 1.0s thay vì 30s) để test
    chạy trong vài giây; ĐƯỜNG CODE hoàn toàn giống vận hành.
    """
    proc = _bot_gia(tmp_path, ghi_heartbeat=True)
    try:
        time.sleep(0.5)
        assert (tmp_path / "heartbeat.json").exists(), "bot giả chưa kịp ghi heartbeat"

        os.kill(proc.pid, signal.SIGSTOP)  # TREO, không chết
        time.sleep(1.2)

        wd = Watchdog(
            WatchdogConfig(stale_after_seconds=1.0, poll_seconds=0.1, sigterm_grace_seconds=1.0),
            heartbeat_file=tmp_path / "heartbeat.json",
            kill_report_file=tmp_path / "watchdog_kill.json",
        )
        verdict = wd.check_once()

        assert not verdict.alive, "watchdog KHÔNG phát hiện được tiến trình bị SIGSTOP"
        assert verdict.reason == REASON_STALE

        dung = wd.handle_dead(verdict)

        assert dung == "SIGKILL", f"tiến trình treo phải leo thang tới SIGKILL, không phải {dung}"
        bao_cao = json.loads((tmp_path / "watchdog_kill.json").read_text(encoding="utf-8"))
        assert bao_cao["signal_used"] == "SIGKILL"
        assert bao_cao["restarted"] is False
    finally:
        for sig in (signal.SIGCONT, signal.SIGKILL):
            try:
                os.kill(proc.pid, sig)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)


def test_heartbeat_bi_cat_nhung_tien_trinh_con_song(tmp_path: Path) -> None:
    """Nghiệm thu 12d #2: tiến trình sống và heartbeat KHÔNG được ghi lại
    nữa. Ở đây `mtime` bắt được. Ca "file vẫn ghi nhưng `loop_seq` đứng"
    do `test_ba_lan_cung_loop_seq_thi_bao_treo` phủ."""
    proc = _bot_gia(tmp_path, ghi_heartbeat=False)
    try:
        write_heartbeat(tmp_path / "heartbeat.json", loop_seq=1, pid=proc.pid)
        time.sleep(1.2)

        wd = Watchdog(
            WatchdogConfig(stale_after_seconds=1.0, poll_seconds=0.1),
            heartbeat_file=tmp_path / "heartbeat.json",
            kill_report_file=tmp_path / "k.json",
        )
        verdict = wd.check_once()

        assert not verdict.alive
        assert proc.poll() is None, "tiền đề sai: tiến trình con đã tự chết"
    finally:
        proc.kill()
        proc.wait(timeout=5)


# ----------------------------------------------------------------------
# Nối dây: bot phải ghi heartbeat mỗi VÒNG POLL
# ----------------------------------------------------------------------


def test_main_ghi_heartbeat_moi_vong_khong_phai_moi_bar() -> None:
    """`loop_seq=iterations` (số vòng poll), KHÔNG phải số bar đã xử lý.

    Bar 1D nghĩa là số bar đứng yên suốt 24 giờ một cách hoàn toàn bình
    thường — dùng nó làm `loop_seq` sẽ khiến quy tắc `loop_seq_stuck` giết
    bot mỗi ngày một lần.
    """
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")

    assert "write_heartbeat(" in src
    assert "loop_seq=iterations" in src
