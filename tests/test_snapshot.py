"""Smoke test — backtest ngắn, config MẶC ĐỊNH, so với snapshot đã commit.

Phase 12b §A.3. Ba tầng kiểm hồi quy, mỗi tầng một vai trò khác nhau —
đừng để chúng trùng nhau:

| Test | Phạm vi | Chi phí | Chạy khi |
|---|---|---|---|
| `test_forward_golden.py` | một lượt pipeline forward, dữ liệu tổng hợp | <1s | mỗi lần chạy test |
| `test_snapshot.py` (file này) | backtest 14 ngày, dữ liệu THẬT, config THẬT | ~8s | mỗi commit |
| `regression_harness.py` | backtest đầy đủ vs baseline Phase 7 | ~137s | trước merge lớn |

File này là **canary**, không phải phép đo chính xác. Nó bắt được "có thứ
gì đó vỡ" trong 8 giây thay vì 137 — feature tính sai, HMM train khác,
allocation lệch, phí đổi. Phép so chính xác với đầy đủ ngưỡng là việc của
`regression_harness.py`.

## Vì sao dùng config MẶC ĐỊNH chứ không rút gọn

`tests/test_determinism.py` rút gọn cấu hình HMM cho bản nhanh, vì ở đó
thứ cần kiểm (tính tất định của pipeline) không phụ thuộc cấu hình. Ở đây
thì ngược lại: canary phải chạy đúng thứ production chạy. Một smoke test
trên cấu hình rút gọn sẽ xanh trong khi cấu hình thật đã vỡ.

Giá của điều đó là 8 giây. Chấp nhận — nó vẫn nhanh hơn harness 17 lần.

## KHÔNG sinh lại snapshot để "cho xanh"

Cùng quy tắc `regression_harness.py`: fail ở đây là một CÂU HỎI ("thay đổi
vừa rồi có cố ý ảnh hưởng kết quả không?"), không phải một việc vặt phải
dọn. Cố ý → ghi `docs/DECISIONS.md` trước, rồi sinh lại. Không cố ý →
revert.

## Cửa sổ được CHỌN BẰNG ĐO — và một giới hạn ĐÃ ĐO, chưa khắc phục

Cửa sổ đầu tiên (2024-01-01..08, đúng 7 ngày như §A.3 gợi ý) có đúng MỘT
regime và MỘT lệnh. Cửa sổ hiện tại (2022-06-10..24) có BA regime
(NEUTRAL -> EUPHORIA -> CRASH), 10 lệnh, và RẺ HƠN (7.9s so với 8.8s: số
window walk-forward vẫn là một, chỉ thêm vài bar OOS vốn rất rẻ). Nên đổi
sang nó.

**Nhưng cả hai cửa sổ đều KHÔNG bắt được đột biến `_EMA_PERIOD` 50 -> 40**
— một thay đổi nằm thẳng trên đường quyết định allocation, và
`regression_harness.py` bắt được nó ngay (Sharpe lệch 0.031). Đo hai lần,
cùng kết quả. Việc thêm regime KHÔNG lấp được khoảng trống này. (Nguyên
nhân chính xác chưa truy — chỉ ghi lại điều đã ĐO.)

Đó là giới hạn nội tại của một cửa sổ 15 bar: nó không thể chạm hết mọi
nhánh strategy × mọi quan hệ giá-EMA. Ghi ra thay vì để người đọc suy ra
rằng "smoke xanh" nghĩa là allocation không đổi.

Đột biến ĐÃ đo trên file này:

| Đột biến | Đường | Kết quả |
|---|---|---|
| `taker_fee_pct` 0.10 -> 0.15 | chi phí | BẮT ĐƯỢC |
| `hmm.seed` 0 -> 1 | train | BẮT ĐƯỢC |
| `_EMA_PERIOD` 50 -> 40 | allocation | **KHÔNG bắt được** |

Phủ đường allocation là việc của `regression_harness.py` (`pytest -m
slow`). Đừng dùng file này làm cổng duy nhất trước một merge chạm vào
`core/regime_strategies.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import main as main_mod

_SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "smoke_7d.json"

_SYMBOL = "BTCUSDT"
_CCXT_SYMBOL = "BTC/USDT"
_DATA_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
_START = datetime(2022, 6, 10, tzinfo=timezone.utc)
_END = datetime(2022, 6, 24, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def smoke_result() -> Any:
    """Chạy backtest MỘT LẦN cho cả file — ~8s là chi phí một lần, không
    phải mỗi assert."""
    from backtest.backtester import WalkForwardBacktester
    from data.history_loader import HistoryLoader

    settings = main_mod.load_settings()
    wf = main_mod.build_walk_forward_config(settings)
    ohlcv = HistoryLoader().load(_CCXT_SYMBOL, "1D", _DATA_START, _END)
    backtester = WalkForwardBacktester(
        hmm_engine=main_mod.build_hmm_engine(settings, min_train_bars=wf.is_bars),
        strategy_orchestrator=main_mod.build_orchestrator(settings),
        trend_gate=main_mod.build_trend_gate(settings, enabled=True),
        cost_model=main_mod.build_cost_model(settings),
        config=wf,
        feature_config=main_mod.build_feature_config(settings),
    )
    return backtester.run(_SYMBOL, ohlcv, _START, _END)


@pytest.fixture(scope="module")
def snapshot() -> dict[str, Any]:
    return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))


_FAIL_HINT = (
    "\n\nFail ở đây là một CÂU HỎI, không phải việc vặt phải dọn: thay đổi vừa "
    "rồi có CỐ Ý ảnh hưởng kết quả không?\n"
    "  Cố ý   -> ghi docs/DECISIONS.md TRƯỚC, rồi sinh lại tests/snapshots/smoke_7d.json\n"
    "  Không  -> revert. Đừng sửa snapshot.\n"
    "Chạy `pytest -m slow` để xem regression harness đầy đủ nói gì."
)


def test_so_bar_dung(smoke_result: Any, snapshot: dict[str, Any]) -> None:
    """Số bar đổi nghĩa là cửa sổ walk-forward đã đổi — mọi so sánh còn lại
    trong file này lập tức vô nghĩa, nên kiểm nó TRƯỚC."""
    assert len(smoke_result.equity_curve) == snapshot["n_bars"], _FAIL_HINT


def test_final_equity_khop(smoke_result: Any, snapshot: dict[str, Any]) -> None:
    """So bằng `Decimal` trên CHUỖI, không phải float: equity đi qua đường
    Decimal suốt backtest (bất biến #3), và ép sang float ở đây sẽ làm phép
    so chấp nhận đúng loại sai lệch mà Decimal sinh ra để loại bỏ."""
    actual = Decimal(str(smoke_result.equity_curve["equity"].iloc[-1]))

    assert actual == Decimal(snapshot["final_equity"]), _FAIL_HINT


def test_so_lenh_khop(smoke_result: Any, snapshot: dict[str, Any]) -> None:
    assert len(smoke_result.trade_log) == snapshot["n_trades"], _FAIL_HINT


def test_tong_phi_khop(smoke_result: Any, snapshot: dict[str, Any]) -> None:
    """Phí là chỉ báo sớm cho giao dịch quá nhiều (CLAUDE.md bất biến #7) —
    một thay đổi làm bot rebalance thường xuyên hơn sẽ lộ ra ở đây trước
    khi lộ ra ở equity."""
    actual = Decimal(str(smoke_result.cost_report.total_fee_usdt))

    assert actual == Decimal(snapshot["total_fee_usdt"]), _FAIL_HINT


def test_chuoi_regime_khop(smoke_result: Any, snapshot: dict[str, Any]) -> None:
    """Cửa sổ này đi qua BA regime với hai lần chuyển
    (NEUTRAL -> EUPHORIA -> CRASH), nên phép so chuỗi bắt được cả nhãn sai
    lẫn thời điểm chuyển sai — xem "Cửa sổ được CHỌN BẰNG ĐO" ở docstring
    module."""
    actual = list(smoke_result.regime_history["regime_label"])

    assert actual == snapshot["regime_labels"], _FAIL_HINT


def test_snapshot_ghi_lai_cau_hinh_da_dung(snapshot: dict[str, Any]) -> None:
    """Snapshot phải mang theo cấu hình sinh ra nó. Một file toàn số mà
    không nói được nó đo cái gì thì lần fail sau sẽ không ai tái tạo được."""
    cfg = snapshot["_cau_hinh"]

    assert cfg["symbol"] == _SYMBOL
    assert cfg["start"] == _START.date().isoformat()
    assert cfg["end"] == _END.date().isoformat()
    assert cfg["data_start"] == _DATA_START.date().isoformat()


def test_khong_dung_cau_hinh_rut_gon(snapshot: dict[str, Any]) -> None:
    """Canary phải chạy đúng thứ production chạy.

    Nếu ai đó rút gọn `n_candidates`/`n_init`/`covariance_type` để test
    nhanh hơn, smoke test sẽ xanh trong khi cấu hình THẬT đã vỡ — đúng loại
    an tâm giả mà một canary không được phép tạo ra.
    """
    hmm = main_mod.load_settings()["hmm"]

    assert hmm["n_candidates"] == [3, 4, 5, 6, 7]
    assert hmm["n_init"] == 10
    assert hmm["covariance_type"] == "full"
    assert hmm["seed"] == 0
