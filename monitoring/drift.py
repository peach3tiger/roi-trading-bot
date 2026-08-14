"""So sánh hành vi ĐANG CHẠY với baseline backtest Phase 7. §C.1.

**Trôi lệch xuất hiện trước khi thua lỗ xuất hiện.** Một bot vẫn có lãi
nhưng đã rebalance gấp đôi tần suất baseline là một bot đang trên đường
đốt hết lợi nhuận vào phí; nhìn P&L thì chưa thấy gì.

## Đây là bên GHI, `monitoring/dashboard.py` là bên ĐỌC

Panel dashboard KHÔNG tính lại gì — nó đọc `drift.json` và tô màu theo cờ
`alert` mà file này đã quyết định. Hai nguồn sự thật cho cùng một chỉ số là
cách chắc chắn nhất để không ai tin cái nào. Hợp đồng schema ghi ở
`dashboard.py::load_drift_panel_data`; file này phải ghi đúng nó.

Vì cùng lý do đó, "bảng `rich` cho dashboard" mà §C.1 nhắc tới ĐÃ tồn tại
(`Dashboard._drift_panel`) và không được viết lại ở đây.

## Baseline ĐỌC TỪ FILE, không hardcode

`tests/snapshots/phase7_baseline/`. Các con số trong §C.1 (30.6/18.1/16.5/
34.8 %, 32.3 %, 11.68 %) là KẾT QUẢ ĐO, không phải hằng số của hệ thống —
chép chúng vào code nghĩa là lần regenerate baseline tiếp theo sẽ làm mọi
so sánh ở đây nói dối mà không có gì đỏ.

Bốn mức allocation cũng không hardcode: đọc bốn mức danh nghĩa từ
`settings.yaml` (`cap_bear_structure` 0.30, `high_vol_allocation` 0.50,
`mid_vol_allocation_trend_broken` 0.60, `low_vol_allocation` 0.95) rồi
chia rổ tại TRUNG ĐIỂM giữa hai mức liền nhau. Định nghĩa này tái tạo đúng
30.6/18.1/16.5/34.8 của §C.1 trên baseline — có test ghim, và đó là bằng
chứng duy nhất cho thấy nó cùng định nghĩa với người đã đo con số gốc.

## Ghi `${STATE_DIR}/drift.json`

KHÔNG phải `monitoring/state/drift.json` như §C.1 viết: thư mục đó nằm
trong cây MÃ NGUỒN, và `status.json` đã phải chuyển khỏi đúng chỗ ấy ngày
2026-08-08. Cùng lý do với `monitoring/health.py`.

## Backtest và forward test KHÔNG cùng tên cột

`regime_history.csv` dùng `final_allocation_pct`/`strategy_target_allocation_pct`;
`forward/log_v2.csv` dùng `final_allocation`/`hmm_allocation`/`hmm_retrained`.
`normalize_bars()` quy về MỘT bộ tên trước khi đo. Để mỗi hàm đo tự biết
hai bộ tên là cách chắc chắn để bộ thứ ba (khi xuất hiện) chỉ được thêm
vào một nửa số chỗ.

## CHỈ ĐỌC `forward/`

Chỉ số `warning_count` lấy từ log forward test qua `forward.runner`. Đó là
bằng chứng của một thí nghiệm 12 tháng đang chạy — module này KHÔNG BAO
GIỜ ghi vào `forward/`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BASELINE_DIR = _REPO_ROOT / "tests" / "snapshots" / "phase7_baseline"
_DEFAULT_STATE_DIR = "state"

# Cửa sổ trượt §C.1. Bar 1D nên 30 ngày = 30 bar.
WINDOW_DAYS = 30

# Số lần train liên tiếp phải TĂNG ĐƠN ĐIỆU mới kích hoạt cảnh báo
# `warning_count`. 3 chứ không phải 2: hai lần tăng liên tiếp xảy ra
# thường xuyên do ngẫu nhiên trong khởi tạo EM, và một chỉ báo lúc nào
# cũng đỏ thì không ai đọc.
WARNING_TREND_LEN = 3


def default_drift_path() -> Path:
    """`${STATE_DIR}/drift.json`, đọc env ở THỜI ĐIỂM GỌI — cùng lý do với
    `alerts.py::_default_status_path` và `health.py::default_health_path`."""
    return Path(os.environ.get("STATE_DIR", _DEFAULT_STATE_DIR)) / "drift.json"


@dataclass(frozen=True)
class DriftThresholds:
    """Ngưỡng §C.1. Đọc từ `settings.yaml` (CLAUDE.md bất biến #14)."""

    allocation_pts: float = 15.0  # bất kỳ mức nào lệch > 15 điểm %
    rebalance_pts: float = 10.0
    fee_pct_of_gross_max: float = 20.0  # ngưỡng TUYỆT ĐỐI, không so baseline
    flicker_multiple: float = 2.0  # cao hơn baseline 2x
    trend_gate_block_pts: float = 20.0

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "DriftThresholds":
        raw = (settings.get("monitoring", {}) or {}).get("drift", {}) or {}
        d = cls()
        return cls(
            allocation_pts=float(raw.get("allocation_pts", d.allocation_pts)),
            rebalance_pts=float(raw.get("rebalance_pts", d.rebalance_pts)),
            fee_pct_of_gross_max=float(raw.get("fee_pct_of_gross_max", d.fee_pct_of_gross_max)),
            flicker_multiple=float(raw.get("flicker_multiple", d.flicker_multiple)),
            trend_gate_block_pts=float(raw.get("trend_gate_block_pts", d.trend_gate_block_pts)),
        )


@dataclass(frozen=True)
class Behaviour:
    """Sáu chỉ số §C.1 đo trên MỘT tập bar — baseline hay cửa sổ hiện tại
    đều dùng chung kiểu này.

    Dùng chung một kiểu, và quan trọng hơn là chung một HÀM ĐO
    (`measure()`), là điều làm phép so có nghĩa: hai định nghĩa khác nhau
    cho cùng một chỉ số thì hiệu số giữa chúng không đo cái gì cả.
    """

    n_bars: int
    allocation_mix_pct: tuple[float, float, float, float]
    rebalance_rate_pct: Optional[float]
    fee_pct_of_gross: Optional[float]
    flicker_rate_pct: float
    trend_gate_block_pct: float


@dataclass(frozen=True)
class Bands:
    """Dải giá trị BÌNH THƯỜNG của một chỉ số, đo trên chính các cửa sổ
    `WINDOW_DAYS` bar của baseline (phân vị 1–99).

    Vì sao cần, ĐO ĐƯỢC: phân bố allocation trên cửa sổ 30 bar có độ lệch
    chuẩn ~41 ĐIỂM %. Ngưỡng 15 điểm của §C.1 nằm sâu bên trong nhiễu tự
    nhiên đó, nên khi so cửa sổ 30 bar với con số toàn kỳ (30.6/18.1/16.5/
    34.8), **99.0%** số cửa sổ của CHÍNH baseline vượt ngưỡng. Trend gate:
    72.9%. Đổi baseline sang trung vị-của-cửa-sổ không cứu được (100.0% /
    32.7%) — vấn đề nằm ở kích thước cửa sổ so với biên độ chỉ số, không
    nằm ở chọn mốc so.

    Một chỉ báo đỏ 99% thời gian không phải chỉ báo. Nên cảnh báo cần CẢ
    HAI: vượt ngưỡng §C.1 (đủ LỚN) **và** nằm ngoài dải này (đủ HIẾM).
    Ngưỡng §C.1 KHÔNG bị nới — nó vẫn là điều kiện cần. Xem
    `docs/DECISIONS.md`, mục "Ngưỡng drift §C.1 quá chặt so với nhiễu cửa
    sổ 30 bar".
    """

    allocation_mix: tuple[tuple[float, float], ...]
    trend_gate_block: tuple[float, float]

    @staticmethod
    def _outside(value: float, band: tuple[float, float]) -> bool:
        return value < band[0] or value > band[1]

    def allocation_outside(self, mix: Sequence[float]) -> bool:
        return any(self._outside(v, b) for v, b in zip(mix, self.allocation_mix))

    def trend_gate_outside(self, value: float) -> bool:
        return self._outside(value, self.trend_gate_block)


@dataclass(frozen=True)
class MetricResult:
    """Một dòng trong `drift.json`. `current`/`baseline` là CHUỖI ĐÃ ĐỊNH
    DẠNG — hợp đồng với panel (xem `dashboard.py::load_drift_panel_data`):
    panel không làm phép tính nào trên chúng."""

    name: str
    current: str
    baseline: str
    alert: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current": self.current,
            "baseline": self.baseline,
            "alert": self.alert,
        }


# Backtest (`regime_history.csv`) và forward test (`log_v2.csv`) đặt tên
# khác nhau cho cùng đại lượng. Bảng này là chỗ DUY NHẤT biết điều đó.
_ALIASES: dict[str, tuple[str, ...]] = {
    "final_allocation": ("final_allocation", "final_allocation_pct"),
    "hmm_allocation": ("hmm_allocation", "strategy_target_allocation_pct"),
    "trend_gate_cap": ("trend_gate_cap",),
    "is_flickering": ("is_flickering",),
    "retrained": ("hmm_retrained", "retrained"),
    "warning_count": ("warning_count",),
}


def normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Đổi tên cột về một bộ chuẩn. Cột không có thì bỏ qua — `measure()`
    đòi những cột nó cần, còn `warning_count`/`retrained` chỉ có ở forward
    log v2 và vắng mặt là hợp lệ (xem `forward/SCHEMA.md`)."""
    doi_ten: dict[str, str] = {}
    for chuan, ten_co_the in _ALIASES.items():
        for ten in ten_co_the:
            if ten in bars.columns and ten != chuan:
                doi_ten[ten] = chuan
                break
    return bars.rename(columns=doi_ten) if doi_ten else bars


# ----------------------------------------------------------------------
# Rổ allocation — bốn mức danh nghĩa, biên là TRUNG ĐIỂM
# ----------------------------------------------------------------------


def nominal_allocation_levels(settings: dict[str, Any]) -> tuple[float, ...]:
    """Bốn mức allocation danh nghĩa, đọc từ config chứ không chép tay.

    Chúng là những giá trị mà hệ thống THẬT SỰ nhắm tới; mọi giá trị khác
    trong log là kết quả trôi giá giữa hai lần rebalance quanh một trong
    bốn mức này.
    """
    strategy = settings.get("strategy", {}) or {}
    gate = settings.get("trend_gate", {}) or {}
    muc = {
        float(gate.get("cap_bear_structure", 0.30)),
        float(strategy.get("high_vol_allocation", 0.50)),
        float(strategy.get("mid_vol_allocation_trend_broken", 0.60)),
        float(strategy.get("low_vol_allocation", 0.95)),
    }
    return tuple(sorted(muc))


def allocation_bin_edges(levels: Sequence[float]) -> tuple[float, ...]:
    """Trung điểm giữa hai mức liền nhau. `n` mức -> `n-1` biên.

    Vì sao trung điểm chứ không phải chính các mức: allocation thực tế
    dao động quanh mức danh nghĩa (giá đổi giữa hai lần rebalance, ngưỡng
    `rebalance_threshold_pct` 25% cho phép trôi khá xa). Chia rổ TẠI mức
    sẽ ném phần lớn các bar "đang ở mức 0.95" sang rổ dưới.
    """
    return tuple((levels[i] + levels[i + 1]) / 2 for i in range(len(levels) - 1))


def allocation_mix(allocations: pd.Series, edges: Sequence[float]) -> tuple[float, ...]:
    """% số bar rơi vào từng rổ. Tổng luôn 100 (hoặc 0 khi không có bar)."""
    n = len(allocations)
    if n == 0:
        return tuple(0.0 for _ in range(len(edges) + 1))

    values = allocations.astype(float)
    counts = []
    truoc = float("-inf")
    for edge in [*edges, float("inf")]:
        counts.append(int(((values > truoc) & (values <= edge)).sum()))
        truoc = edge
    return tuple(round(c / n * 100, 1) for c in counts)


# ----------------------------------------------------------------------
# Đo hành vi
# ----------------------------------------------------------------------


def measure(
    bars: pd.DataFrame,
    *,
    edges: Sequence[float],
    rebalance_rate_pct: Optional[float] = None,
    fee_pct_of_gross: Optional[float] = None,
) -> Behaviour:
    """Đo sáu chỉ số trên `bars`.

    `bars` đi qua `normalize_bars()` trước — backtest và forward test dùng
    TÊN CỘT KHÁC NHAU cho cùng đại lượng (xem `_ALIASES`), và đó là thứ
    phải xử lý ở một chỗ chứ không phải ở mỗi hàm đo.

    `rebalance_rate_pct`/`fee_pct_of_gross` truyền từ ngoài: chúng đến từ
    `cost_report`/`trade_log` chứ không đọc được từ lịch sử regime.
    """
    bars = normalize_bars(bars)
    alloc = bars["final_allocation"].astype(float)
    tg_cap = bars["trend_gate_cap"].astype(float)
    hmm_target = bars["hmm_allocation"].astype(float)
    flicker = bars["is_flickering"].astype(bool)

    n = len(bars)
    return Behaviour(
        n_bars=n,
        allocation_mix_pct=allocation_mix(alloc, edges),  # type: ignore[arg-type]
        rebalance_rate_pct=rebalance_rate_pct,
        fee_pct_of_gross=fee_pct_of_gross,
        flicker_rate_pct=round(float(flicker.mean()) * 100, 2) if n else 0.0,
        # "Trend gate CHẶN HMM" = trần thấp hơn thứ HMM muốn. Bằng nhau
        # KHÔNG tính là chặn: trần 0.95 trên một signal 0.95 không giới
        # hạn gì cả, và đếm nó vào sẽ làm chỉ số này bão hoà ở gần 100%
        # trong mọi thị trường tăng.
        trend_gate_block_pct=round(float((tg_cap < hmm_target).mean()) * 100, 1) if n else 0.0,
    )


def load_baseline(
    settings: dict[str, Any], *, baseline_dir: Path = _DEFAULT_BASELINE_DIR
) -> Behaviour:
    """Đo baseline TỪ FILE bằng CHÍNH `measure()` — không đọc một bảng số
    đã tính sẵn.

    Đây là điểm quan trọng nhất của hàm này: nếu baseline được nạp từ một
    danh sách hằng số còn "hiện tại" được tính bằng `measure()`, thì hiệu
    số giữa chúng đo cả sự khác nhau giữa hai ĐỊNH NGHĨA lẫn sự khác nhau
    giữa hai HÀNH VI — và không ai tách được hai thứ đó ra.
    """
    regime = pd.read_csv(baseline_dir / "regime_history.csv")
    cost = pd.read_csv(baseline_dir / "cost_report.csv")
    equity = pd.read_csv(baseline_dir / "equity_curve.csv")

    n_bars = len(equity)
    rebalance = float(cost["n_rebalances"].iloc[0]) / n_bars * 100 if n_bars else None
    fee_pct = float(cost["cost_pct_of_gross_profit"].iloc[0])

    return measure(
        regime,
        edges=allocation_bin_edges(nominal_allocation_levels(settings)),
        rebalance_rate_pct=round(rebalance, 1) if rebalance is not None else None,
        fee_pct_of_gross=round(fee_pct, 2),
    )


def load_baseline_bands(
    settings: dict[str, Any],
    *,
    baseline_dir: Path = _DEFAULT_BASELINE_DIR,
    window_days: int = WINDOW_DAYS,
    low_pct: float = 1.0,
    high_pct: float = 99.0,
) -> Bands:
    """Trượt cửa sổ `window_days` qua toàn bộ baseline, lấy phân vị 1–99
    của từng chỉ số. Đây là "bình thường trông như thế nào" ở đúng kích
    thước cửa sổ mà cảnh báo sẽ dùng."""
    regime = normalize_bars(pd.read_csv(baseline_dir / "regime_history.csv"))
    edges = allocation_bin_edges(nominal_allocation_levels(settings))

    mixes: list[Sequence[float]] = []
    blocks: list[float] = []
    for i in range(window_days, len(regime) + 1):
        cua_so = measure(regime.iloc[i - window_days : i], edges=edges)
        mixes.append(cua_so.allocation_mix_pct)
        blocks.append(cua_so.trend_gate_block_pct)

    if not mixes:
        rong = ((0.0, 100.0),) * (len(edges) + 1)
        return Bands(allocation_mix=rong, trend_gate_block=(0.0, 100.0))

    cot = list(zip(*mixes))
    return Bands(
        allocation_mix=tuple(
            (
                float(pd.Series(c).quantile(low_pct / 100)),
                float(pd.Series(c).quantile(high_pct / 100)),
            )
            for c in cot
        ),
        trend_gate_block=(
            float(pd.Series(blocks).quantile(low_pct / 100)),
            float(pd.Series(blocks).quantile(high_pct / 100)),
        ),
    )


# ----------------------------------------------------------------------
# `warning_count` — xu hướng tăng đơn điệu, đọc từ forward log
# ----------------------------------------------------------------------


def monotonic_increasing_tail(values: Sequence[float], length: int = WARNING_TREND_LEN) -> bool:
    """`length` giá trị CUỐI có tăng đơn điệu ngặt không.

    Ít hơn `length` giá trị -> `False`. KHÔNG phải "chưa đủ dữ liệu thì cứ
    cảnh báo cho chắc": một cảnh báo phát ra khi chưa đủ bằng chứng dạy
    người đọc bỏ qua nó, và lần có bằng chứng thật sẽ chìm cùng.
    """
    if len(values) < length:
        return False
    tail = list(values)[-length:]
    return all(tail[i] < tail[i + 1] for i in range(length - 1))


def retrain_warning_counts(bars: Optional[pd.DataFrame]) -> list[float]:
    """`warning_count` tại mỗi bar CÓ RETRAIN, theo thứ tự thời gian.

    `None`/thiếu cột -> danh sách rỗng. Schema v1 của `forward/log.csv`
    không có cột này (thêm ở v2, xem `forward/SCHEMA.md`), nên "không có"
    là trạng thái HỢP LỆ chứ không phải lỗi.
    """
    if bars is None or bars.empty:
        return []
    bars = normalize_bars(bars)
    if "warning_count" not in bars.columns or "retrained" not in bars.columns:
        return []

    retrained = bars[bars["retrained"].astype(str).str.lower().isin(("true", "1", "yes"))]
    counts = pd.to_numeric(retrained["warning_count"], errors="coerce").dropna()
    return [float(v) for v in counts]


def _load_forward_bars() -> Optional[pd.DataFrame]:
    """CHỈ ĐỌC `forward/`. Nuốt mọi lỗi: forward test là một thí nghiệm
    đang chạy, và việc nó chưa có dữ liệu / file lỗi không được phép làm
    hỏng năm chỉ số còn lại."""
    try:
        from forward.runner import load_all_bars

        return load_all_bars()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được log forward cho chỉ số warning_count: %s", exc)
        return None


# ----------------------------------------------------------------------
# So sánh + xuất
# ----------------------------------------------------------------------


def _fmt_mix(mix: Sequence[float]) -> str:
    return " / ".join(f"{v:.1f}" for v in mix) + " %"


def compare(
    current: Behaviour,
    baseline: Behaviour,
    thresholds: DriftThresholds,
    *,
    warning_counts: Sequence[float] = (),
    window_complete: bool = True,
    bands: Optional[Bands] = None,
) -> list[MetricResult]:
    """Sáu chỉ số §C.1, theo đúng thứ tự bảng trong prompt.

    `window_complete=False` (cửa sổ chưa đủ `WINDOW_DAYS` bar) TẮT cảnh báo
    của năm chỉ số tính trên cửa sổ, nhưng vẫn in giá trị đo được.

    Vì sao cần: forward test bắt đầu 2026-08-06 nên cửa sổ 30 ngày phải tới
    khoảng 2026-09-05 mới đầy. Trước đó, 9 bar toàn allocation 0.30 cho
    "100.0 / 0.0 / 0.0 / 0.0 %" và cảnh báo đỏ rực — không phải vì hành vi
    trôi lệch, mà vì mẫu quá nhỏ để nói bất cứ điều gì. Một chỉ báo đỏ suốt
    tháng đầu sẽ dạy người đọc bỏ qua nó, và lần trôi lệch THẬT sẽ chìm
    cùng. Cùng nguyên tắc với `monotonic_increasing_tail()`.

    `warning_count` KHÔNG bị tắt: nó đọc trên toàn bộ lịch sử retrain, độc
    lập với cửa sổ 30 ngày, nên nó có đủ bằng chứng của riêng nó.

    `bands=None` -> chỉ dùng ngưỡng §C.1 (đường dùng trong test so baseline
    với chính nó, nơi hai vế cùng kích thước mẫu nên dải không có nghĩa).
    Đường THẬT (`run()`) luôn truyền `bands`.
    """
    results: list[MetricResult] = []
    thieu_mau = f" (cửa sổ {current.n_bars}/{WINDOW_DAYS} bar — chưa đủ mẫu)"
    hau_to = "" if window_complete else thieu_mau

    lech_alloc = [abs(c - b) for c, b in zip(current.allocation_mix_pct, baseline.allocation_mix_pct)]
    results.append(
        MetricResult(
            name="Phân bố allocation (4 mức)",
            current=_fmt_mix(current.allocation_mix_pct) + hau_to,
            baseline=_fmt_mix(baseline.allocation_mix_pct),
            # BẤT KỲ mức nào lệch quá ngưỡng, không phải tổng hay trung
            # bình: một mức tăng 16 điểm và một mức giảm 16 điểm sẽ triệt
            # tiêu nhau ở tổng, trong khi đó chính là thay đổi hành vi.
            alert=(
                window_complete
                and max(lech_alloc, default=0.0) > thresholds.allocation_pts
                # VÀ nằm ngoài dải bình thường của cửa sổ cùng kích thước.
                # Xem docstring `Bands`: thiếu điều kiện này, 99.0% cửa sổ
                # của chính baseline sẽ báo động.
                and (bands is None or bands.allocation_outside(current.allocation_mix_pct))
            ),
        )
    )

    results.append(
        _so_sanh_diem_pt(
            "Tỷ lệ rebalance / bar",
            current.rebalance_rate_pct,
            baseline.rebalance_rate_pct,
            thresholds.rebalance_pts,
            hau_to=hau_to,
            enabled=window_complete,
        )
    )

    fee_hien_tai = current.fee_pct_of_gross
    results.append(
        MetricResult(
            name="Phí / lợi nhuận gộp",
            current="—" if fee_hien_tai is None else f"{fee_hien_tai:.2f} %{hau_to}",
            baseline=(
                "—" if baseline.fee_pct_of_gross is None else f"{baseline.fee_pct_of_gross:.2f} %"
            ),
            # Ngưỡng TUYỆT ĐỐI (> 20%), không phải "lệch khỏi baseline".
            # §C.1 cố ý: phí ăn 20% lợi nhuận gộp là xấu bất kể baseline
            # từng là bao nhiêu.
            alert=(
                window_complete
                and fee_hien_tai is not None
                and fee_hien_tai > thresholds.fee_pct_of_gross_max
            ),
        )
    )

    nguong_flicker = baseline.flicker_rate_pct * thresholds.flicker_multiple
    results.append(
        MetricResult(
            name="Flicker rate",
            current=f"{current.flicker_rate_pct:.2f} %{hau_to}",
            baseline=f"{baseline.flicker_rate_pct:.2f} % (trần {nguong_flicker:.2f} %)",
            # Baseline 0 -> mọi flicker > 0 là cao hơn "2x của 0". Đúng ý
            # định: backtest Phase 7 không flicker lần nào, nên bot thật
            # flicker là một thay đổi hành vi thật, không phải nhiễu.
            alert=window_complete and current.flicker_rate_pct > nguong_flicker,
        )
    )

    tang_don_dieu = monotonic_increasing_tail(warning_counts)
    results.append(
        MetricResult(
            name="warning_count mỗi lần train",
            current=(
                " -> ".join(f"{v:.0f}" for v in list(warning_counts)[-WARNING_TREND_LEN:])
                if warning_counts
                else "—"
            ),
            baseline=f"cảnh báo khi tăng đơn điệu {WARNING_TREND_LEN} lần liên tiếp",
            alert=tang_don_dieu,
        )
    )

    results.append(
        _so_sanh_diem_pt(
            "Thời gian trend gate chặn HMM",
            current.trend_gate_block_pct,
            baseline.trend_gate_block_pct,
            thresholds.trend_gate_block_pts,
            hau_to=hau_to,
            enabled=window_complete
            and (bands is None or bands.trend_gate_outside(current.trend_gate_block_pct)),
        )
    )

    return results


def _so_sanh_diem_pt(
    name: str,
    current: Optional[float],
    baseline: Optional[float],
    nguong_pt: float,
    *,
    hau_to: str = "",
    enabled: bool = True,
) -> MetricResult:
    """Thiếu một trong hai vế -> KHÔNG cảnh báo. Một phép so với `None`
    không phải bằng chứng của bất cứ điều gì."""
    du_lieu = current is not None and baseline is not None
    return MetricResult(
        name=name,
        current="—" if current is None else f"{current:.1f} %{hau_to}",
        baseline="—" if baseline is None else f"{baseline:.1f} %",
        alert=enabled and du_lieu and abs(current - baseline) > nguong_pt,  # type: ignore[operator]
    )


def build_payload(metrics: Sequence[MetricResult], *, window_days: int = WINDOW_DAYS) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "metrics": [m.as_dict() for m in metrics],
    }


def write_drift(payload: dict[str, Any], path: Optional[Path] = None) -> Path:
    """Ghi NGUYÊN TỬ. KHÔNG raise `OSError` — đường quan sát, cùng hợp đồng
    với `health.py::write_health`."""
    target = path or default_drift_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("Không ghi được %s: %s", target, exc)
    return target


def recent_window(bars: pd.DataFrame, *, window_days: int = WINDOW_DAYS) -> pd.DataFrame:
    """`window_days` bar CUỐI. Bar 1D nên số bar = số ngày.

    Cắt theo SỐ BAR chứ không theo dấu thời gian: một khoảng đứt (bot dừng
    ba ngày, đã xảy ra 2026-08-06..08) sẽ làm cửa sổ theo ngày chỉ còn 27
    bar, và mọi tỷ lệ tính trên nó lệch đi mà không có gì báo. Số bar cho
    một mẫu có kích thước biết trước.
    """
    return bars.tail(window_days)


def run(
    settings: dict[str, Any],
    *,
    bars: Optional[pd.DataFrame] = None,
    path: Optional[Path] = None,
    baseline_dir: Path = _DEFAULT_BASELINE_DIR,
    rebalance_rate_pct: Optional[float] = None,
    fee_pct_of_gross: Optional[float] = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """Đo cửa sổ hiện tại, so với baseline, ghi `drift.json`, trả payload.

    `bars=None` -> đọc log forward (chỉ đọc). Truyền tường minh khi muốn đo
    một tập bar khác — đó là cách `tests/` kiểm sanity "baseline so với
    chính nó không được cảnh báo gì".
    """
    baseline = load_baseline(settings, baseline_dir=baseline_dir)
    bands = load_baseline_bands(settings, baseline_dir=baseline_dir, window_days=window_days)
    forward_bars = bars if bars is not None else _load_forward_bars()

    if forward_bars is None or forward_bars.empty:
        current = Behaviour(
            n_bars=0,
            allocation_mix_pct=(0.0, 0.0, 0.0, 0.0),
            rebalance_rate_pct=None,
            fee_pct_of_gross=None,
            flicker_rate_pct=0.0,
            trend_gate_block_pct=0.0,
        )
        warning_counts: list[float] = []
    else:
        cua_so = recent_window(forward_bars, window_days=window_days)
        current = measure(
            cua_so,
            edges=allocation_bin_edges(nominal_allocation_levels(settings)),
            rebalance_rate_pct=rebalance_rate_pct,
            fee_pct_of_gross=fee_pct_of_gross,
        )
        # Xu hướng `warning_count` đọc trên TOÀN BỘ lịch sử, không cắt cửa
        # sổ 30 ngày: retrain 7 ngày một lần nên 30 ngày chỉ chứa ~4 lần —
        # cắt cửa sổ sẽ làm quy tắc "3 lần liên tiếp" gần như không bao
        # giờ có đủ mẫu.
        warning_counts = retrain_warning_counts(forward_bars)

    metrics = compare(
        current,
        baseline,
        DriftThresholds.from_settings(settings),
        warning_counts=warning_counts,
        window_complete=current.n_bars >= window_days,
        bands=bands,
    )
    payload = build_payload(metrics, window_days=window_days)
    write_drift(payload, path)
    return payload


def main() -> int:
    import main as main_mod

    payload = run(main_mod.load_settings())
    for m in payload["metrics"]:
        co = "!!" if m["alert"] else "  "
        print(f"{co} {m['name']:32} {m['current']:>28}   (baseline {m['baseline']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
