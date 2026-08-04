"""data.feature_engineering — pure feature functions cho HMM.

Mọi hàm ở đây PHẢI là pure function: cùng input DataFrame → cùng output,
không state, không I/O. Đây là điều kiện để kiểm tra look-ahead bias được
khả thi (xem tests/test_look_ahead.py) — nếu feature có state ẩn, không
thể chứng minh feature tại bar t chỉ phụ thuộc dữ liệu tới bar t.

Mọi rolling window chỉ nhìn về quá khứ: không bao giờ đặt tham số `center`
của `.rolling()` thành True (xem CLAUDE.md bất biến #11).

**Chỉ Tầng 1 (OHLCV) được implement ở đây.** Tầng 2/3 để sau, sau khi
Tầng 1 đã được validate bằng ablation test — xem CLAUDE.md bất biến #13.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator
from ta.volatility import AverageTrueRange


@dataclass(frozen=True)
class FeatureConfig:
    """Tham số cấu hình tầng feature — đọc từ settings.yaml, không hardcode."""

    zscore_lookback: int = 365
    use_trade_count_not_volume: bool = True
    tier2_derivatives: bool = False
    tier3_temporal: bool = False


def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    """Chuẩn hoá rolling z-score dùng cho mọi feature — lookback 365, KHÔNG
    phải quy ước ngày-giao-dịch-trong-năm của equities. Không truyền
    `center` (mặc định False của `.rolling()`) là bắt buộc: mỗi điểm chỉ
    được chuẩn hoá bằng thống kê của quá khứ, không phải cả cửa sổ bao
    quanh nó.
    """
    mean = series.rolling(window=lookback, min_periods=lookback).mean()
    std = series.rolling(window=lookback, min_periods=lookback).std(ddof=0)
    return (series - mean) / std.replace(0.0, np.nan)


def compute_log_returns(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Log return 1, 5, 20 chu kỳ."""
    log_close = np.log(close)
    return pd.DataFrame({f"log_return_{p}": log_close.diff(p) for p in periods})


def compute_realized_vol(returns: pd.Series, window: int) -> pd.Series:
    """Rolling std của log return — KHÔNG center."""
    return returns.rolling(window=window, min_periods=window).std(ddof=0)


def compute_vol_ratio(vol_short: pd.Series, vol_long: pd.Series) -> pd.Series:
    """Tỷ lệ vol 5 chu kỳ / 20 chu kỳ — đo tốc độ thay đổi biến động."""
    return vol_short / vol_long.replace(0.0, np.nan)


def compute_adx(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX(14) — sức mạnh xu hướng, không phân biệt hướng."""
    indicator = ADXIndicator(
        high=ohlcv["high"], low=ohlcv["low"], close=ohlcv["close"], window=period, fillna=False
    )
    return cast(pd.Series, indicator.adx())


def compute_sma_slope(series: pd.Series, sma_period: int, slope_lookback: int) -> pd.Series:
    """Độ dốc (% thay đổi) của SMA(sma_period) qua slope_lookback chu kỳ.

    Dùng chung cho cả SMA50 giá (Tầng 1 "Trend") và SMA10 trade_count
    (Tầng 1 "Activity") — công thức giống hệt nhau, chỉ khác input series.
    """
    sma = series.rolling(window=sma_period, min_periods=sma_period).mean()
    sma_prior = sma.shift(slope_lookback)
    return (sma - sma_prior) / sma_prior.replace(0.0, np.nan)


def compute_rsi_zscore(close: pd.Series, rsi_period: int, zscore_lookback: int) -> pd.Series:
    """Z-score của RSI(14) — RSI thô (0-100) không so sánh được trực tiếp
    với các feature khác, phải chuẩn hoá cùng lookback 365 như mọi feature."""
    rsi = RSIIndicator(close=close, window=rsi_period, fillna=False).rsi()
    return rolling_zscore(rsi, zscore_lookback)


def compute_distance_to_sma(close: pd.Series, sma_period: int) -> pd.Series:
    """Khoảng cách từ giá đóng cửa tới SMA, tính theo % giá."""
    sma = close.rolling(window=sma_period, min_periods=sma_period).mean()
    return (close - sma) / sma * 100.0


def compute_roc(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Rate of change 10 và 20 chu kỳ, tính theo %."""
    return pd.DataFrame({f"roc_{p}": close.pct_change(periods=p) * 100.0 for p in periods})


def compute_atr_normalized(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(14) chuẩn hoá theo close — so sánh được giữa các mức giá khác nhau
    theo thời gian (BTC $3k năm 2018 vs $60k+ hiện tại)."""
    atr = cast(
        pd.Series,
        AverageTrueRange(
            high=ohlcv["high"], low=ohlcv["low"], close=ohlcv["close"], window=period, fillna=False
        ).average_true_range(),
    )
    return atr / ohlcv["close"]


def compute_trade_count_zscore(trade_count: pd.Series, mean_window: int = 50) -> pd.Series:
    """Z-score của trade_count vs mean 50 (KHÔNG phải 365 — hoạt động giao
    dịch đổi baseline nhanh hơn nhiều so với giá; đây là ngoại lệ có chủ
    đích với lookback chung của các feature khác).
    """
    mean = trade_count.rolling(window=mean_window, min_periods=mean_window).mean()
    std = trade_count.rolling(window=mean_window, min_periods=mean_window).std(ddof=0)
    return (trade_count - mean) / std.replace(0.0, np.nan)


def compute_tier1_features(ohlcv: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Tầng 1 — bắt buộc, chỉ cần OHLCV: returns, volatility, trend, mean
    reversion, momentum, range, và hoạt động giao dịch (trade_count thay
    vì volume — volume bị bóp méo bởi wash trading trên sàn crypto).

    Mọi cột được z-score hoá (lookback 365) ở bước cuối cùng, kể cả những
    cột tự thân đã là z-score (rsi_zscore, trade_count_zscore) — HMM
    Gaussian cần các chiều đầu vào có scale tương đương nhau để covariance
    ước lượng có ý nghĩa; áp z-score thống nhất một lần nữa là vô hại
    (deterministic, không look-ahead) và đơn giản hơn nhiều so với xử lý
    ngoại lệ cho từng cột.
    """
    close = ohlcv["close"]
    activity = ohlcv["trade_count"] if config.use_trade_count_not_volume else ohlcv["volume"]

    raw = pd.DataFrame(index=ohlcv.index)
    raw = raw.join(compute_log_returns(close, [1, 5, 20]))

    vol_20 = compute_realized_vol(raw["log_return_1"], window=20)
    vol_5 = compute_realized_vol(raw["log_return_1"], window=5)
    raw["realized_vol_20"] = vol_20
    raw["vol_ratio_5_20"] = compute_vol_ratio(vol_5, vol_20)

    raw["adx_14"] = compute_adx(ohlcv, period=14)
    raw["sma50_slope"] = compute_sma_slope(close, sma_period=50, slope_lookback=30)

    raw["rsi_zscore_14"] = compute_rsi_zscore(
        close, rsi_period=14, zscore_lookback=config.zscore_lookback
    )
    raw["distance_to_sma200_pct"] = compute_distance_to_sma(close, sma_period=200)

    raw = raw.join(compute_roc(close, [10, 20]))
    raw["atr_norm_14"] = compute_atr_normalized(ohlcv, period=14)

    raw["trade_count_zscore_50"] = compute_trade_count_zscore(activity, mean_window=50)
    raw["trade_count_sma10_slope"] = compute_sma_slope(activity, sma_period=10, slope_lookback=10)

    normalized = raw.apply(lambda col: rolling_zscore(col, config.zscore_lookback))
    return normalized.dropna()


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
