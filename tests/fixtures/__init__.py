"""Fixture dữ liệu ĐÃ COMMIT cho test tất định/hồi quy.

`btcusdt_1d_2018_2026.parquet` — BTC/USDT 1D, 2018-01-01 → 2026-08-04,
3138 bar, 7 cột, 171 KB. Sinh bằng `scripts/build_test_fixture.py`, ghim
SHA256 trong `tests/golden/frozen_hashes.json`.

Ba test đọc nó: `test_determinism.py`, `test_snapshot.py`,
`regression_harness.py`. Trước 2026-08-14 cả ba gọi `HistoryLoader()` —
tức là qua MẠNG. Xem `load_fixture()` về vì sao đó là khiếm khuyết của
chính các test đó, không phải chuyện riêng của CI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

FIXTURE_PATH = Path(__file__).resolve().parent / "btcusdt_1d_2018_2026.parquet"


def load_fixture(
    end: Optional[Union[str, datetime, pd.Timestamp]] = None,
    *,
    start: Optional[Union[str, datetime, pd.Timestamp]] = None,
    path: Optional[Path] = None,
) -> pd.DataFrame:
    """Đọc fixture, cắt về `[start, end]` (bao gồm hai đầu). KHÔNG gọi mạng.

    `start` BẮT BUỘC với caller nào trước đây truyền mốc bắt đầu cho
    `HistoryLoader().load(symbol, tf, start, end)`. Bỏ nó đi là đổi ĐẦU
    VÀO chứ không phải đổi nguồn: fixture bắt đầu từ 2018-01-01, nên một
    caller từng bắt đầu ở 2018-02-09 sẽ nhận thêm 39 bar warmup, z-score
    đổi, HMM đổi, và MỌI chỉ số đổi theo.

    Đã xảy ra 2026-08-14: `regression_harness.py` mất mốc `_START` khi
    chuyển sang fixture — Sharpe 0.941 -> 1.078, n_trades 739 -> 822. Lỗi
    ẩn được vì chính test đó chưa từng được thu thập (xem
    `tests/test_collection_scope.py`).

    Thiếu file -> `AssertionError` kèm hướng dẫn, **TUYỆT ĐỐI KHÔNG
    `pytest.skip`**. Một test bị skip lặng lẽ chính là cách "878 passed"
    trở thành lời nói dối; `ops/verify_scope.py` tồn tại vì đúng chuyện đó
    (CLAUDE.md #19).
    """
    target = path or FIXTURE_PATH
    if not target.exists():
        raise AssertionError(
            f"Thiếu fixture {target}.\n"
            "Sinh lại: python scripts/build_test_fixture.py\n"
            "KHÔNG skip — các test đọc file này là cổng chặn hồi quy."
        )
    bars = pd.read_parquet(target)
    if start is not None:
        bars = bars.loc[bars.index >= pd.Timestamp(start)]
    if end is not None:
        bars = bars.loc[bars.index <= pd.Timestamp(end)]
    return bars
