"""broker.bybit_client — BybitClient implement ExchangeClient qua pybit.

Testnet (api-testnet.bybit.com) là mặc định — xem CLAUDE.md bất biến #6.
Chuyển sang mainnet yêu cầu gõ tay chuỗi xác nhận đầy đủ, không có cờ tắt
qua middleware.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from broker.base import (
    Balance,
    ExchangeClient,
    Order,
    OrderRequest,
    OrderResult,
    Position,
)
from broker.instrument_rules import InstrumentRules

LIVE_CONFIRMATION_PHRASE = "YES I UNDERSTAND THE RISKS"


class BybitClient(ExchangeClient):
    """Bọc SDK `pybit` (Bybit v5 unified API).

    Credentials đọc từ .env, không bao giờ hardcode hay log — kể cả một
    phần. Đồng bộ thời gian server lúc khởi động (lệch recv_window là
    nguyên nhân số 1 gây lỗi auth với Bybit); implement token bucket rate
    limit ở tầng này (600 request / 5 giây / IP) thay vì chờ bị sàn chặn.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True) -> None:
        if not testnet:
            self._require_live_confirmation()

    def _require_live_confirmation(self) -> None:
        """Bắt buộc gõ tay LIVE_CONFIRMATION_PHRASE trước khi cho phép mainnet."""
        raise NotImplementedError

    def _sync_server_time(self) -> None:
        """Cảnh báo nếu lệch đồng hồ > 1 giây."""
        raise NotImplementedError

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        raise NotImplementedError

    def get_historical_klines(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raise NotImplementedError

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[pd.Series], None]
    ) -> None:
        raise NotImplementedError

    def subscribe_executions(self, callback: Callable[[OrderResult], None]) -> None:
        raise NotImplementedError
