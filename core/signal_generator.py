"""core.signal_generator — kết hợp HMM + trend gate + risk manager thành signal cuối cùng.

Điểm kết hợp duy nhất của nguyên tắc lõi toàn hệ thống:

    final_allocation = min(hmm_allocation, trend_gate_cap, risk_manager_cap)

Luôn dùng hàm tối thiểu (min) — không bao giờ hàm tối đa, không trung bình
cộng, không "hoà giải" giữa các tầng (xem CLAUDE.md bất biến #2). Module
này là nơi DUY NHẤT ba tầng gặp nhau; mỗi tầng bên dưới không biết về sự
tồn tại của các tầng khác.

## File này nằm trong phạm vi cổng §E

`GATED_PREFIXES = ("core/", "backtest/")`: mọi thay đổi ở đây BẮT BUỘC
chạy `pytest -m slow` trước khi merge. Cổng đã được kiểm chứng HAI CHIỀU
trên CI thật (2026-08-16) — commit chạm `core/` thì chạy test chậm;
commit chỉ chạm `docs/` thì bỏ qua. Một cổng LUÔN chạy cũng vô dụng như
một cổng không bao giờ chạy: nó sẽ bị tắt trong tuần đầu.

Xem `ops/readiness_gate.py`, job `slow-gate` trong
`.github/workflows/ci.yml`, và `docs/DECISIONS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pandas as pd

from core.hmm_engine import HMMRegimeEngine, RegimeState
from core.regime_strategies import Signal, StrategyOrchestrator
from core.risk_manager import PortfolioState, RiskDecision, RiskManager
from core.trend_gate import StructuralTrendGate


@dataclass(frozen=True)
class SignalGeneratorResult:
    """`RiskDecision` một mình không đủ cho caller (main loop, Phase 10):
    cần cả `regime_state`/`is_flickering` để log và ghi `state_snapshot.json`
    — hai giá trị này được tính bên trong `generate()` nhưng trước đây bị
    vứt đi sau khi dùng xong, không có cách nào đọc lại từ bên ngoài."""

    decision: RiskDecision
    regime_state: RegimeState
    is_flickering: bool
    # BỐN giá trị của chuỗi tầng, lộ ra để CHẨN ĐOÁN (Phase 12b §B.1 —
    # `monitoring/health.py`). Trước đây `generate()` tính chúng rồi vứt
    # đi, nên `health.json` chỉ nói được allocation cuối là bao nhiêu chứ
    # không nói được TẦNG NÀO đang giới hạn — thông tin quan trọng nhất
    # khi có gì bất thường. `tests/test_wiring_equivalence.py` từng phải
    # gọi lại `orchestrator.generate_signal()` lần hai chỉ để lấy
    # `hmm_allocation`; file test đó KHÔNG được sửa theo trong cùng thay
    # đổi này (một phép kiểm không nên đổi cùng lúc với thứ nó kiểm).
    #
    # `risk_manager_cap` == `final_allocation` trong đường dây hiện tại —
    # giữ CẢ HAI là có chủ ý, không phải thừa: phép so
    # `final == min(hmm, trend_gate, risk)` ở `health.py::_check_invariants`
    # chỉ đỏ khi risk manager trả về allocation CAO HƠN trần của hai tầng
    # trước, tức đúng chế độ hỏng mà CLAUDE.md bất biến #2 cấm. Gộp hai
    # trường lại là bỏ mất phép kiểm đó.
    hmm_allocation: Decimal  # TRƯỚC khi áp trần trend gate
    trend_gate_cap: Decimal
    risk_manager_cap: Decimal  # 0 khi risk manager TỪ CHỐI lệnh
    final_allocation: Decimal


def compose_layer_allocations(*caps: Decimal) -> Decimal:
    """Kết hợp N trần tỷ trọng thành một trần cuối cùng — LUÔN hàm tối
    thiểu, không có ngoại lệ. Đây là điểm thực thi của nguyên tắc lõi toàn
    hệ thống (CLAUDE.md bất biến #2), tách thành hàm thuần độc lập để có
    thể kiểm chứng bằng property test mà không cần dựng đủ cả ba tầng
    (risk_manager chưa implement ở phase này vẫn kiểm được nguyên tắc).
    """
    if not caps:
        raise ValueError("Cần ít nhất một trần để kết hợp.")
    return min(caps)


class SignalGenerator:
    """Orchestrator cấp cao nhất: HMM filtered inference → strategy signal
    → trend gate cap → risk manager veto → RiskDecision cuối cùng.
    """

    def __init__(
        self,
        hmm_engine: HMMRegimeEngine,
        trend_gate: StructuralTrendGate,
        strategy_orchestrator: StrategyOrchestrator,
        risk_manager: RiskManager,
    ) -> None:
        self.hmm_engine = hmm_engine
        self.trend_gate = trend_gate
        self.strategy_orchestrator = strategy_orchestrator
        self.risk_manager = risk_manager

    def generate(
        self,
        symbol: str,
        features: pd.DataFrame,
        bars: pd.DataFrame,
        current_allocation: Decimal,
        portfolio_state: PortfolioState,
    ) -> SignalGeneratorResult:
        """Chạy toàn bộ pipeline cho một bar mới.

        Thứ tự: HMM filtered (trên `features` đã z-score) → StrategyOrchestrator
        → áp trần trend gate (trên `bars` giá thô) qua hàm tối thiểu →
        risk_manager.validate_signal (có thể giảm thêm hoặc từ chối, không
        bao giờ tăng).

        `features` (ma trận đã chuẩn hoá cho HMM) và `bars` (OHLCV thô cho
        trend gate/strategy) là hai đầu vào khác nhau có chủ đích — dùng
        nhầm cái này cho cái kia sẽ cho kết quả vô nghĩa (z-score không
        phải giá, ATR/EMA cần giá thô không phải z-score).
        """
        regime_state = self.hmm_engine.predict_regime_filtered(features)
        is_flickering = self.hmm_engine.is_flickering()

        signal = self.strategy_orchestrator.generate_signal(
            symbol,
            regime_state,
            self.hmm_engine.regime_infos,
            bars,
            current_allocation,
            is_flickering,
        )

        hmm_allocation = signal.target_allocation_pct
        trend_gate_cap = self.trend_gate.get_allocation_cap(bars)
        signal = self._apply_layer_caps(signal, trend_gate_cap)

        decision = self.risk_manager.validate_signal(signal, portfolio_state)
        # Từ chối = không giữ vị thế nào, tức trần bằng 0. KHÔNG dùng
        # `signal.target_allocation_pct` làm giá trị thay thế: đó là thứ
        # risk manager vừa BÁC BỎ, ghi nó vào `health.json` sẽ vẽ ra một
        # trạng thái chưa từng xảy ra.
        risk_manager_cap = (
            decision.modified_signal.target_allocation_pct
            if decision.approved and decision.modified_signal is not None
            else Decimal("0")
        )
        return SignalGeneratorResult(
            decision=decision,
            regime_state=regime_state,
            is_flickering=is_flickering,
            hmm_allocation=hmm_allocation,
            trend_gate_cap=trend_gate_cap,
            risk_manager_cap=risk_manager_cap,
            final_allocation=risk_manager_cap,
        )

    def _apply_layer_caps(self, signal: Signal, trend_gate_cap: Decimal) -> Signal:
        """Áp hàm tối thiểu giữa allocation của signal và trần trend gate —
        không bao giờ dùng hàm tối đa."""
        capped = compose_layer_allocations(signal.target_allocation_pct, trend_gate_cap)
        if capped == signal.target_allocation_pct:
            return signal
        return replace(
            signal,
            target_allocation_pct=capped,
            reasoning=f"{signal.reasoning} [trend_gate cap={trend_gate_cap} -> allocation giảm còn {capped}]",
        )
