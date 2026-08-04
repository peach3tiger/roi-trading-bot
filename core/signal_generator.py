"""core.signal_generator — kết hợp HMM + trend gate + risk manager thành signal cuối cùng.

Điểm kết hợp duy nhất của nguyên tắc lõi toàn hệ thống:

    final_allocation = min(hmm_allocation, trend_gate_cap, risk_manager_cap)

Không max(), không trung bình cộng, không "hoà giải" giữa các tầng — xem
CLAUDE.md bất biến #2. Module này là nơi DUY NHẤT ba tầng gặp nhau; mỗi
tầng bên dưới không biết về sự tồn tại của các tầng khác.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from core.hmm_engine import HMMRegimeEngine
from core.regime_strategies import Signal, StrategyOrchestrator
from core.risk_manager import PortfolioState, RiskDecision, RiskManager
from core.trend_gate import StructuralTrendGate


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
        ...

    def generate(
        self,
        bars: pd.DataFrame,
        current_allocation: Decimal,
        portfolio_state: PortfolioState,
    ) -> RiskDecision:
        """Chạy toàn bộ pipeline cho một bar mới và trả RiskDecision cuối cùng.

        Thứ tự: HMM filtered → StrategyOrchestrator.generate_signal →
        min(hmm_allocation, trend_gate.get_allocation_cap(bars)) áp vào
        signal.target_allocation_pct → risk_manager.validate_signal (có
        thể giảm thêm hoặc từ chối, không bao giờ tăng).
        """
        raise NotImplementedError

    def _apply_layer_caps(self, signal: Signal, trend_gate_cap: Decimal) -> Signal:
        """Áp min() giữa allocation của signal và trần trend gate — không max()."""
        raise NotImplementedError
