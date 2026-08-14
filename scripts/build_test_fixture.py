"""Sinh `tests/fixtures/btcusdt_1d_*.parquet` — dữ liệu cho test tất định.

## Vì sao fixture tồn tại

`tests/test_determinism.py` khẳng định hai lần chạy backtest giống nhau
bit-for-bit. Trước 2026-08-14 nó lấy dữ liệu qua `HistoryLoader()` — tức
là qua MẠNG. Tiền đề của chính nó sai ngay trên máy dev: Binance có thể
sửa lại bar lịch sử, cache có thể trống, mạng có thể chậm. Trên CI nó còn
sai theo cách ồn ào hơn (`451 Service unavailable from a restricted
location` — Binance chặn IP runner), nhưng CI chỉ làm lộ ra khiếm khuyết
đã có sẵn.

Một test tất định phải có ĐẦU VÀO tất định.

## Hai cột ĐÃ BỎ, và cái giá của việc bỏ

`HistoryLoader` trả 9 cột; fixture giữ 7. Hai cột bị bỏ:

- **`quote_volume`** — không hàm nào trong `data/`, `core/`, `backtest/`,
  `monitoring/` đọc nó.
- **`taker_buy_quote_volume`** — như trên.
  `data/feature_engineering.py` dùng `taker_buy_base_volume` (ĐÃ GIỮ) cho
  `taker_buy_ratio`; nó không dùng bản `quote`.

Bỏ hai cột đó đưa file từ 228 KB xuống 171 KB — dưới trần 200 KB. Một cột
không ai đọc thì không phải bằng chứng của gì cả.

**`trade_count` KHÔNG nằm trong hai cột bị bỏ — nó được GIỮ.** Điều đó
quan trọng vì Phase 12d §B.2 bắt `monitoring/data_harness.py` kiểm
`trade_count >= 0`: nếu cột đó bị bỏ, mọi test dựng dữ liệu từ fixture này
sẽ **không kiểm được điều kiện đó** và phép kiểm sẽ xanh một cách rỗng
nghĩa. Cùng lý do `volume` được giữ (§B.2 kiểm `volume >= 0` và
`volume != 0`).

Hai cột ĐÃ BỎ không xuất hiện trong phép kiểm nào của §B.2, nên việc bỏ
chúng không làm mất phép kiểm nào. Thêm một kiểm mới chạm tới
`quote_volume`/`taker_buy_quote_volume` thì phải sinh lại fixture với cột
đó — `COLUMNS` bên dưới là chỗ duy nhất quyết định.

## Chạy lại khi nào

Gần như không bao giờ. Fixture được ghim SHA256 trong
`tests/golden/frozen_hashes.json`; sinh lại nó làm mọi baseline đo trên nó
hết hiệu lực. Nếu cần mở rộng phạm vi ngày, đổi hằng số bên dưới, chạy
script, cập nhật hash, và ghi lý do vào `docs/DECISIONS.md` TRƯỚC.

    python scripts/build_test_fixture.py
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

CCXT_SYMBOL = "BTC/USDT"
TIMEFRAME = "1D"
# Khớp `tests/test_determinism.py`: `_DATA_START` và `end` của kịch bản
# ĐẦY ĐỦ. Lấy dư một ngày ở đuôi là vô hại; thiếu một ngày thì test đỏ.
START = datetime(2018, 1, 1, tzinfo=timezone.utc)
# Tới 2026-08-04 — mốc `_END` của `tests/regression_harness.py`, tức là
# dải RỘNG NHẤT mà bất kỳ test nào cần. MỘT fixture cho cả ba test
# (determinism, snapshot, regression harness) thay vì ba file: ba fixture
# nghĩa là ba lần phải nhớ sinh lại, và lần quên đầu tiên sẽ im lặng.
END = datetime(2026, 8, 4, tzinfo=timezone.utc)

# Chỉ giữ cột đường pipeline THẬT SỰ đọc. Bỏ `quote_volume`,
# `taker_buy_quote_volume` (không hàm nào đọc) đưa file từ 228 KB xuống
# 171 KB — dưới trần, và một cột không ai đọc thì không phải bằng chứng
# của gì cả. Giữ `taker_buy_base_volume` vì `data/feature_engineering.py`
# dùng nó cho `taker_buy_ratio` (Tier 2, hiện tắt) — bỏ nó sẽ chặn mất
# khả năng test Tier 2 sau này mà không tải lại mạng.
COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "taker_buy_base_volume",
)

FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "btcusdt_1d_2018_2026.parquet"
# Trần kích thước — fixture phải commit được. Vượt thì thu hẹp phạm vi
# ngày hoặc đổi sang CSV nén, đừng lặng lẽ commit một file 5MB.
MAX_BYTES = 200 * 1024


def build(*, path: Path = FIXTURE, start: datetime = START, end: datetime = END) -> Path:
    from data.history_loader import HistoryLoader

    # `bar_offset_hours` để MẶC ĐỊNH — đã kiểm bằng đo: mặc định cho kết
    # quả y hệt `bar_offset_hours=0` mà `tests/regression_harness.py`
    # truyền tường minh, nên một fixture phục vụ được cả hai đường gọi.
    bars = HistoryLoader().load(CCXT_SYMBOL, TIMEFRAME, start, end)
    if bars.empty:
        raise RuntimeError("HistoryLoader trả khung rỗng — không ghi fixture rỗng đè lên bản cũ.")

    thieu = [c for c in COLUMNS if c not in bars.columns]
    if thieu:
        raise RuntimeError(f"HistoryLoader không trả các cột {thieu} — fixture sẽ thiếu dữ liệu.")
    bars = bars[list(COLUMNS)]

    path.parent.mkdir(parents=True, exist_ok=True)
    # `index=True`: chỉ số thời gian LÀ dữ liệu, không phải trang trí —
    # mất nó thì fixture không tái tạo được backtest.
    bars.to_parquet(path, index=True, compression="zstd")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=FIXTURE)
    args = parser.parse_args(argv)

    import pandas as pd

    p = build(path=args.out)
    kich_thuoc = p.stat().st_size
    df = pd.read_parquet(p)

    print(f"đã ghi   : {p.relative_to(_REPO_ROOT)}")
    print(f"kích thước: {kich_thuoc:,} byte ({kich_thuoc / 1024:.1f} KB, trần {MAX_BYTES / 1024:.0f} KB)")
    print(f"số bar   : {len(df)}")
    print(f"khoảng   : {df.index[0]} → {df.index[-1]}")
    print(f"cột      : {list(df.columns)}")
    print(f"sha256   : {sha256(p)}")
    print()
    print("Cập nhật `tests/golden/frozen_hashes.json` khoá "
          f"`{p.relative_to(_REPO_ROOT)}` bằng sha256 ở trên.")

    if kich_thuoc > MAX_BYTES:
        print(f"\nLỖI: vượt trần {MAX_BYTES:,} byte — thu hẹp phạm vi ngày hoặc đổi định dạng.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
