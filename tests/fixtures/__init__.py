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
    end: Optional[Union[str, datetime, pd.Timestamp]] = None, *, path: Optional[Path] = None
) -> pd.DataFrame:
    """Đọc fixture, cắt tới `end` (bao gồm). KHÔNG gọi mạng.

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
    if end is None:
        return bars
    return bars.loc[bars.index <= pd.Timestamp(end)]
