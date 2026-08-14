"""Bộ test có THẬT SỰ thu thập thứ nó nghĩ là đang thu thập không.

CLAUDE.md #19 — mọi khẳng định "sạch"/"xanh" phải kèm PHẠM VI đã kiểm.
File này gác đúng lớp đó ở mức pytest: một test không được COLLECT thì
không bao giờ đỏ, và một suite xanh vì nó chưa chạy gì trông y hệt một
suite xanh vì mọi thứ đều đúng.

## Lỗ hổng đã xảy ra (2026-08-14)

`tests/regression_harness.py` — test hồi quy MẠNH NHẤT của dự án, so
backtest đầy đủ với baseline Phase 7 — **chưa từng được thu thập lần nào**
kể từ khi được viết. Pattern mặc định của pytest là `test_*.py`, và tên
file không khớp.

Hệ quả không phải "thiếu một test": cổng §E (`ops/readiness_gate.py`) bắt
buộc chạy `pytest -m slow` trước khi merge thay đổi chạm `core/`, và lệnh
đó thu 8 test **không có** cái quan trọng nhất. Cổng đã thi hành một lệnh
không chứa thứ nó sinh ra để bảo vệ.

Che mất lỗi này suốt Phase 12b–12c: mọi lần tôi kiểm harness đều gọi
TƯỜNG MINH `pytest tests/regression_harness.py`, và cách gọi đó bỏ qua
pattern. Đúng chế độ hỏng "công cụ báo sạch mà không nói đã nhìn vào đâu".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _collected(*args: str) -> set[str]:
    """ID test mà pytest THỰC SỰ thu thập với bộ tham số đã cho."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    return {
        dong.strip()
        for dong in proc.stdout.splitlines()
        if "::" in dong and not dong.startswith(" ")
    }


def test_regression_harness_duoc_thu_thap_boi_m_slow() -> None:
    """Khẳng định trung tâm của file này.

    `pytest -m slow` là lệnh mà cổng §E bắt buộc chạy. Nếu harness không
    nằm trong đó, cổng đang gác một cánh cửa không dẫn tới đâu.
    """
    ids = _collected("-m", "slow")

    assert any("regression_harness.py::" in i for i in ids), (
        "tests/regression_harness.py KHÔNG được `pytest -m slow` thu thập.\n"
        "Kiểm `python_files` trong pyproject.toml — pattern mặc định là `test_*.py` "
        "và tên file này không khớp."
    )


def test_moi_file_test_deu_duoc_thu_thap() -> None:
    """Chống trôi lệch tổng quát: mọi file trong `tests/` trông như file
    test phải góp ít nhất một test vào lần thu thập KHÔNG lọc marker.

    Bắt được cả trường hợp tên file lạ (như harness) lẫn trường hợp một
    file chết vì lỗi import mà không ai để ý.
    """
    ids = _collected("-m", "")
    file_co_test = {i.split("::")[0] for i in ids}

    bo_qua = {"__init__.py", "conftest.py"}
    tren_dia = {
        f"tests/{p.name}"
        for p in (_ROOT / "tests").glob("*.py")
        if p.name not in bo_qua and p.name != "fixtures"
    }
    # `fixtures/` là package dữ liệu, không phải file test.
    tren_dia.discard("tests/fixtures")

    thieu = {f for f in tren_dia if f not in file_co_test}

    assert not thieu, (
        f"{len(thieu)} file trong tests/ không góp test nào: {sorted(thieu)}\n"
        "Hoặc nó không phải file test (đổi tên/chuyển chỗ), hoặc pytest không "
        "thu thập được nó — cả hai đều phải xử lý, không được im lặng."
    )


def test_so_test_slow_khop_giua_hai_cach_dem() -> None:
    """`pytest -m slow` và `pytest -m ''` phải nhất quán: số test slow
    cộng số test không-slow bằng tổng.

    Một chênh lệch ở đây nghĩa là có test rơi ra ngoài CẢ HAI nhóm — tức
    là không bao giờ chạy ở bất kỳ lệnh nào.
    """
    slow = _collected("-m", "slow")
    khong_slow = _collected("-m", "not slow")
    tat_ca = _collected("-m", "")

    assert slow | khong_slow == tat_ca
    assert not (slow & khong_slow), "một test vừa slow vừa không-slow?"
