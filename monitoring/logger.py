"""monitoring.logger — log JSON có cấu trúc, file xoay vòng.

Không bao giờ dùng print() trong code production (xem CLAUDE.md phong
cách code). main.log, trades.log, alerts.log, regime.log — mỗi file 10MB,
giữ 30 ngày.
"""

from __future__ import annotations

import logging
from decimal import Decimal


def get_logger(name: str, log_dir: str) -> logging.Logger:
    """Trả về logger JSON structured, rotating file handler 10MB / giữ 30 ngày."""
    raise NotImplementedError


def log_state(
    logger: logging.Logger,
    regime: str,
    probability: float,
    equity: Decimal,
    positions: dict,
    daily_pnl: Decimal,
    cumulative_fees_paid: Decimal,
) -> None:
    """Mỗi entry gồm timestamp UTC, regime, probability, equity, positions,
    daily_pnl, cumulative_fees_paid."""
    raise NotImplementedError
