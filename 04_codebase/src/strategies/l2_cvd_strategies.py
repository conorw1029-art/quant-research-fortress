"""
CVD + Microprice L2 Strategies
================================
Strategies using Cumulative Volume Delta combined with microprice signals.

CVD measures the net directional pressure of all executed trades.
Divergence between CVD and price = hidden strength/weakness.

These work on either:
- L2 bars (cvd_delta from actual trade classification)
- Standard bars with cvd_delta approximated from buy_vol/sell_vol
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy
from src.strategies.l2_ofi_strategies import _l2_trades, _compute_atr


def _get_cvd(data: pd.DataFrame) -> pd.Series:
    """Get CVD column from either L2 bars or standard bars."""
    if "cvd_delta" in data.columns:
        return data["cvd_delta"].fillna(0.0)
    if "buy_vol" in data.columns and "sell_vol" in data.columns:
        return (data["buy_vol"] - data["sell_vol"]).fillna(0.0)
    return pd.Series(0.0, index=data.index)


class CVDMicropriceStrategy(BaseStrategy):
    """
    CVD Divergence + Microprice Confirmation.
    THESIS: When CVD is positive (buyers active) AND microprice > last close
    (order book tilted up), the next bar should be bullish.
    Dual confirmation from flow and book depth.
    """
    name     = "CVD_Microprice"
    category = "l2_cvd"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "cvd_pct":       [60, 70, 80],   # top/bottom percentile for "strong" CVD
        "mp_ticks":      [0.5, 1.0],     # minimum microprice deviation
        "rr_ratio":      [1.5, 2.0],
        "hold_bars":     [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"cvd_pct": 70, "mp_ticks": 0.75,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        cvd   = _get_cvd(data)
        close = data["close"]

        pct     = float(self.params["cvd_pct"])
        mp_tick = float(self.params["mp_ticks"])

        cvd_high = cvd.rolling(60, min_periods=10).quantile(pct / 100)
        cvd_low  = cvd.rolling(60, min_periods=10).quantile(1 - pct / 100)

        strong_buy  = cvd >= cvd_high
        strong_sell = cvd <= cvd_low

        # Microprice filter
        if "microprice_last" in data.columns:
            mp = data["microprice_last"]
            mp_above = (mp - close) > mp_tick
            mp_below = (close - mp) > mp_tick
        else:
            mp_above = pd.Series(True, index=data.index)
            mp_below = pd.Series(True, index=data.index)

        signals = pd.Series(0, index=data.index)
        signals[strong_buy  & mp_above] =  1
        signals[strong_sell & mp_below] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class CVDSlopeRegimeStrategy(BaseStrategy):
    """
    CVD Slope Regime Filter.
    THESIS: The slope of the cumulative CVD (rate of change) tells us whether
    buying or selling is accelerating. Enter in the direction of the slope,
    exit when slope reverses.
    """
    name     = "CVD_Slope_Regime"
    category = "l2_cvd"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "slope_window": [5, 10, 20],
        "slope_thr":    [0.0],          # above 0 = positive slope
        "rr_ratio":     [1.5, 2.0],
        "hold_bars":    [8, 15],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"slope_window": 10, "slope_thr": 0.0,
                                 "rr_ratio": 1.5, "hold_bars": 10}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        cvd = _get_cvd(data).cumsum()  # cumulative CVD

        w    = int(self.params["slope_window"])
        thr  = float(self.params["slope_thr"])

        # Linear slope via rolling regression
        slope = cvd.diff(w) / w

        prev_slope = slope.shift(1).fillna(0.0)

        # Slope crosses zero = regime change
        cross_up   = (slope > thr)  & (prev_slope <= thr)
        cross_down = (slope < -thr) & (prev_slope >= -thr)

        signals = pd.Series(0, index=data.index)
        signals[cross_up]   =  1
        signals[cross_down] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class CVDAccelerationStrategy(BaseStrategy):
    """
    CVD Acceleration / Deceleration.
    THESIS: A sudden spike in CVD (delta of the delta) signals a burst of
    aggressive institutional activity. Enter with the burst.
    Deceleration after a long trend = fade signal.
    """
    name     = "CVD_Acceleration"
    category = "l2_cvd"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "accel_std":  [1.5, 2.0, 2.5],  # standard deviations for "spike"
        "rr_ratio":   [1.5, 2.0],
        "hold_bars":  [5, 8],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"accel_std": 2.0, "rr_ratio": 1.5, "hold_bars": 6}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        cvd = _get_cvd(data)
        accel = cvd.diff().fillna(0.0)

        std_thr = float(self.params["accel_std"])
        rolling_std = accel.rolling(30, min_periods=10).std().replace(0, np.nan)
        z_score = accel / rolling_std

        signals = pd.Series(0, index=data.index)
        signals[z_score >  std_thr] =  1
        signals[z_score < -std_thr] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class CVDVWAPStrategy(BaseStrategy):
    """
    CVD at VWAP — Premium Edge.
    THESIS: A positive CVD cross when price is at or below session VWAP means
    buyers are stepping in at value. High probability long setup.
    A negative CVD cross at VWAP = selling into value = high prob short.
    """
    name     = "CVD_VWAP"
    category = "l2_cvd"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "vwap_band":  [0.25, 0.5],   # fraction of ATR to define "at VWAP"
        "cvd_pct":    [60, 70],
        "rr_ratio":   [1.5, 2.0],
        "hold_bars":  [8, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"vwap_band": 0.5, "cvd_pct": 65,
                                 "rr_ratio": 2.0, "hold_bars": 10}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "session_vwap" not in data.columns:
            return pd.Series(0, index=data.index)

        cvd    = _get_cvd(data)
        close  = data["close"]
        vwap   = data["session_vwap"]

        pct     = float(self.params["cvd_pct"])
        band    = float(self.params["vwap_band"])

        atr = _compute_atr(data, period=10)
        at_vwap = ((close - vwap).abs() <= band * atr)

        cvd_high = cvd.rolling(60, min_periods=10).quantile(pct / 100)
        cvd_low  = cvd.rolling(60, min_periods=10).quantile(1 - pct / 100)

        signals = pd.Series(0, index=data.index)
        signals[at_vwap & (cvd >= cvd_high)] =  1
        signals[at_vwap & (cvd <= cvd_low)]  = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)
