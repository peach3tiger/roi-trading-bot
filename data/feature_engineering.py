"""data.feature_engineering — pure feature functions cho HMM.

Mọi hàm ở đây PHẢI là pure function: cùng input DataFrame → cùng output,
không state, không I/O. Đây là điều kiện để kiểm tra look-ahead bias được
khả thi (xem tests/test_look_ahead.py) — nếu feature có state ẩn, không
thể chứng minh feature tại bar t chỉ phụ thuộc dữ liệu tới bar t.

Mọi rolling window chỉ nhìn về quá khứ: không bao giờ `center=True`
(xem CLAUDE.md bất biến #11).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeatureConfig:
    """Tham số cấu hình tầng feature — đọc từ settings.yaml, không hardcode."""

    zscore_lookback: int = 365
    use_trade_count_not_volume: bool = True
    tier2_derivatives: bool = False
    tier3_temporal: bool = False


def compute_tier1_features(ohlcv: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Tầng 1 — bắt buộc, chỉ cần OHLCV: returns, volatility, trend, mean
    reversion, momentum, range, và hoạt động giao dịch (trade_count thay
    vì volume — volume bị bóp méo bởi wash trading trên sàn crypto).
    """
    raise NotImplementedError


def compute_log_returns(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Log return 1, 5, 20 chu kỳ."""
    raise NotImplementedError


def compute_realized_vol(returns: pd.Series, window: int) -> pd.Series:
    """Rolling std của log return — KHÔNG center."""
    raise NotImplementedError


def compute_vol_ratio(vol_short: pd.Series, vol_long: pd.Series) -> pd.Series:
    """Tỷ lệ vol 5 chu kỳ / 20 chu kỳ — đo tốc độ thay đổi biến động."""
    raise NotImplementedError


def compute_adx(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX(14) — sức mạnh xu hướng."""
    raise NotImplementedError


def compute_sma_slope(close: pd.Series, sma_period: int, slope_lookback: int) -> pd.Series:
    """Độ dốc SMA 50 trong slope_lookback chu kỳ."""
    raise NotImplementedError


def compute_rsi_zscore(close: pd.Series, rsi_period: int, zscore_lookback: int) -> pd.Series:
    """Z-score của RSI(14) — mean reversion."""
    raise NotImplementedError


def compute_distance_to_sma(close: pd.Series, sma_period: int) -> pd.Series:
    """Khoảng cách từ giá đóng cửa tới SMA, tính theo % giá."""
    raise NotImplementedError


def compute_roc(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Rate of change 10 và 20 chu kỳ."""
    raise NotImplementedError


def compute_atr_normalized(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(14) chuẩn hoá theo close."""
    raise NotImplementedError


def compute_trade_count_zscore(trade_count: pd.Series, mean_window: int = 50) -> pd.Series:
    """Z-score của trade_count vs mean 50, cộng slope SMA 10."""
    raise NotImplementedError


def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    """Chuẩn hoá rolling z-score dùng cho mọi feature — lookback 365, KHÔNG phải 252."""
    raise NotImplementedError


def compute_tier2_features(
    ohlcv: pd.DataFrame, derivatives: pd.DataFrame, config: FeatureConfig
) -> pd.DataFrame:
    """Tầng 2 — crypto-native: funding_rate, funding_zscore, oi_change_pct,
    oi_price_divergence, perp_spot_basis, taker_buy_ratio.

    Yêu cầu dữ liệu derivatives có lịch sử ngắn hơn OHLCV — chỉ bật khi
    ablation test đã chứng minh giá trị (xem CLAUDE.md bất biến #13).
    """
    raise NotImplementedError


def compute_tier3_features(ohlcv: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Tầng 3 — cấu trúc thời gian: weekend_flag, cycle_position.

    Rủi ro overfit cao (chỉ ~2-3 chu kỳ trong dữ liệu). Dùng thận trọng.
    """
    raise NotImplementedError
