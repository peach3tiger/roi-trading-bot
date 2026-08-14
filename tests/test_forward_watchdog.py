"""Canh gác độ tươi `forward/log.csv` (`monitoring/forward_watchdog.py`).

Mỗi test ở đây là một ĐỘT BIẾN đã chạy thật trước khi tin watchdog
(CLAUDE.md kỷ luật #16): phá đúng thứ nó phải bắt, xác nhận đỏ, rồi giữ
lại làm test.

`test_bat_duoc_lech_schema_dong_nhat` là test QUAN TRỌNG NHẤT file này và
là lý do file này tồn tại ở dạng hiện tại. Bản watchdog đầu tiên chỉ hỏi
"`read_existing_log()` có ném lỗi không" và nó báo **KHOẺ** trên đúng kiểu
hỏng đó: khi mọi dòng dư đúng một trường so với header, pandas không ném
gì cả — nó lặng lẽ lấy cột đầu làm index, mọi cột dịch một ô, `date` chứa
giá trị `run_at_utc`, nên cả `staleness_days` lẫn số dòng đều trông bình
thường. Nếu ai đó sau này rút gọn `inspect_log()` về mỗi try/except, test
này phải đỏ.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from forward.runner import ACTIVE_LOG_PATH
from monitoring.forward_watchdog import build_alert_message, inspect_log, run_watchdog

_TODAY = date(2026, 8, 8)

# File log ĐANG HOẠT ĐỘNG (hiện là log_v2.csv), hỏi `forward.runner` chứ
# không hardcode: watchdog canh file đang chạy, nên nguyên liệu test phải
# đi theo cùng lần cuộn schema. Trỏ cứng vào `log.csv` sẽ làm cả file test
# này sai ngay lần cuộn sau — đã xảy ra đúng một lần khi cuộn sang v2.
_REAL_LOG = ACTIVE_LOG_PATH


def _real_rows() -> list[list[str]]:
    """Dùng CHÍNH file log thật làm nguyên liệu, không tự bịa dữ liệu mẫu.

    Một fixture bịa tay sẽ trôi khỏi schema thật ngay lần đổi cột sau và
    lúc đó test vẫn xanh trong khi watchdog đã mù — đúng chế độ hỏng mà cả
    file này đang cố bắt.
    """
    with _REAL_LOG.open() as fh:
        return list(csv.reader(fh))


def _write(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _with_last_bar(path: Path, last_bar: date) -> Path:
    """Ghi lại log thật với ngày dịch sao cho bar cuối = `last_bar`."""
    rows = _real_rows()
    data = [list(r) for r in rows[1:]]
    start = last_bar - timedelta(days=len(data) - 1)
    for n, r in enumerate(data):
        r[0] = str(start + timedelta(days=n))
    return _write(path, [rows[0]] + data)


# ----------------------------------------------------------------------
# Đối chứng
# ----------------------------------------------------------------------


def test_log_khoe_khong_canh_bao(tmp_path: Path) -> None:
    """Bar D ghi vào ngày D+1, nên staleness=1 là TRẠNG THÁI BÌNH THƯỜNG."""
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=1))
    fresh = inspect_log(p, today_utc=_TODAY)

    assert fresh.parse_ok
    assert fresh.staleness_days == 1
    assert build_alert_message(fresh) is None


# ----------------------------------------------------------------------
# Đột biến: lệch schema
# ----------------------------------------------------------------------


def test_bat_duoc_lech_schema_dong_nhat(tmp_path: Path) -> None:
    """MỌI dòng dư một trường: pandas KHÔNG ném lỗi, nuốt cột đầu làm index.

    Đây là ca mà try/except quanh `read_existing_log()` bắt trượt hoàn
    toàn. Xem docstring module.
    """
    rows = _real_rows()
    idx = rows[0].index("warning_count")
    header_cu = [c for c in rows[0] if c != "warning_count"]
    p = _write(tmp_path / "log.csv", [header_cu] + [list(r) for r in rows[1:]])

    fresh = inspect_log(p, today_utc=_TODAY)

    assert not fresh.parse_ok, "pandas parse trót lọt — watchdog phải bắt bằng kiểm schema"
    assert "lệch schema" in (fresh.parse_error or "")
    assert "warning_count" in (fresh.parse_error or "")
    assert build_alert_message(fresh) is not None
    assert idx == 6  # ghim vị trí cột đã gây ra sự cố thật


def test_bat_duoc_lech_schema_khong_dong_nhat(tmp_path: Path) -> None:
    """Tái hiện NGUYÊN VĂN sự cố 2026-08-06.

    Header + dòng đầu ở schema cũ (31 cột), dòng sau ở schema mới (32 cột)
    — độ rộng không đồng nhất nên pandas ném `ParserError` thật. Đây là
    trạng thái `forward/log.csv` đã nằm suốt 2026-08-06 → 08-08 trong khi
    launchd vẫn chạy đều và exit 1 mỗi lần, không ai hay.
    """
    rows = _real_rows()
    idx = rows[0].index("warning_count")
    header_cu = [c for c in rows[0] if c != "warning_count"]
    dong_cu = [v for n, v in enumerate(rows[1]) if n != idx]
    p = _write(tmp_path / "log.csv", [header_cu, dong_cu] + [list(r) for r in rows[2:]])

    fresh = inspect_log(p, today_utc=_TODAY)

    assert not fresh.parse_ok
    msg = build_alert_message(fresh)
    assert msg is not None
    assert "MỌI lần chạy sau đều exit 1" in msg


# ----------------------------------------------------------------------
# Đột biến: log ngừng tăng
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "staleness,phai_canh_bao",
    [
        (1, False),  # bình thường
        (2, False),  # lỡ đúng một lần — laptop ngủ qua mốc 08:00, chấp nhận được
        (3, True),  # lỡ hai lần trở lên — bất thường thật sự
        (36, True),
    ],
)
def test_nguong_do_tuoi(tmp_path: Path, staleness: int, phai_canh_bao: bool) -> None:
    """Ngưỡng phải chịu được ĐÚNG một lần lỡ lịch rồi mới kêu.

    Kêu từ staleness=2 sẽ báo động giả mỗi lần máy ngủ qua đêm, và một
    watchdog kêu oan đều đặn là watchdog sẽ bị ngó lơ đúng hôm nó kêu thật.
    """
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=staleness))
    fresh = inspect_log(p, today_utc=_TODAY)

    assert fresh.staleness_days == staleness
    assert (build_alert_message(fresh) is not None) is phai_canh_bao


def test_bat_duoc_file_bien_mat(tmp_path: Path) -> None:
    fresh = inspect_log(tmp_path / "khong-ton-tai.csv", today_utc=_TODAY)

    assert not fresh.exists
    assert "không tồn tại" in (build_alert_message(fresh) or "")


def test_bat_duoc_file_rong(tmp_path: Path) -> None:
    """Chỉ có header, chưa bar nào — khác 'file biến mất', cùng mức nghiêm trọng."""
    p = _write(tmp_path / "log.csv", [_real_rows()[0]])
    fresh = inspect_log(p, today_utc=_TODAY)

    assert fresh.parse_ok
    assert build_alert_message(fresh) is not None


# ----------------------------------------------------------------------
# Thông điệp + mã thoát
# ----------------------------------------------------------------------


def test_thong_diep_luon_kem_buoc_chan_doan(tmp_path: Path) -> None:
    """Cảnh báo đọc trên điện thoại lúc nửa đêm phải nói được LÀM GÌ TIẾP.

    Một cảnh báo chỉ báo "có chuyện" mà không kèm lệnh kiểm tra chỉ tạo lo
    lắng, không tạo hành động — và 12 tháng không người trông thì mỗi cảnh
    báo bỏ lỡ là dữ liệu mất vĩnh viễn.
    """
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=10))
    msg = build_alert_message(inspect_log(p, today_utc=_TODAY))

    assert msg is not None
    assert "launchctl print" in msg
    assert "launchd.err.log" in msg


def test_run_watchdog_khong_gui_khi_log_khoe(tmp_path: Path) -> None:
    """`send=True` nhưng log khoẻ -> KHÔNG gửi gì.

    `AlertManager` vẫn được dựng (để báo cáo `telegram_configured` mỗi
    ngày), nhưng `.send()` không bao giờ được gọi khi không có sự cố.
    """
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=1))
    res = run_watchdog(p, today_utc=_TODAY, send=True, env_path=tmp_path / ".env")

    assert res["stale"] is False
    assert res["alert_sent"] is False


@pytest.mark.parametrize("stale", [False, True])
def test_bao_cao_trang_thai_kenh_ke_ca_khi_log_khoe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale: bool
) -> None:
    """`telegram_configured` phải có trong MỌI lần chạy, không chỉ khi có sự cố.

    Nếu chỉ kiểm kênh lúc cần gửi, thì phát hiện "Telegram chưa cấu hình"
    rơi đúng vào hôm thí nghiệm chết — tức là không phát hiện được gì. Ghi
    trạng thái kênh mỗi ngày vào `watchdog.out.log` biến điểm mù đó thành
    thứ nhìn thấy được trước khi cần tới.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    lag = 10 if stale else 1
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=lag))

    res = run_watchdog(p, today_utc=_TODAY, send=False, env_path=tmp_path / ".env")

    assert res["stale"] is stale
    assert res["telegram_configured"] is True
    assert res["alert_sent"] is False  # send=False -> không bao giờ gửi thật


def test_bao_cao_kenh_chua_cau_hinh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Biến rỗng (`TELEGRAM_BOT_TOKEN=`) phải bị coi là CHƯA cấu hình.

    Đúng trạng thái `.env` của máy này lúc dựng watchdog: tên biến có mặt,
    giá trị rỗng. `AlertManager` quy đổi chuỗi rỗng thành None, watchdog
    phải phản ánh đúng như vậy thay vì thấy "biến tồn tại" rồi báo khoẻ.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    p = _with_last_bar(tmp_path / "log.csv", _TODAY - timedelta(days=1))

    ket_qua = run_watchdog(p, today_utc=_TODAY, send=False, env_path=tmp_path / ".env")

    assert ket_qua["telegram_configured"] is False


def test_load_dotenv_khong_ghi_de_moi_truong_that(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Biến đã có trong `os.environ` phải THẮNG `.env`.

    launchd không có env của shell nên `.env` là nguồn duy nhất khi chạy
    tự động — nhưng chạy tay với biến tạm thì không được bị file ghi đè.
    Trả về TÊN biến, không bao giờ giá trị (CLAUDE.md bất biến #6).
    """
    from monitoring.forward_watchdog import load_dotenv

    env = tmp_path / ".env"
    env.write_text("TELEGRAM_CHAT_ID=tu-file\nWATCHDOG_TEST_MOI=tu-file\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "co-san")
    monkeypatch.delenv("WATCHDOG_TEST_MOI", raising=False)

    loaded = load_dotenv(env)

    import os

    assert os.environ["TELEGRAM_CHAT_ID"] == "co-san"
    assert os.environ["WATCHDOG_TEST_MOI"] == "tu-file"
    assert loaded == ["WATCHDOG_TEST_MOI"]
    assert not any("tu-file" in name or "co-san" in name for name in loaded)
