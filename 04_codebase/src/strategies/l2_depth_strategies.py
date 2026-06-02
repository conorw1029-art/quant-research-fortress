"""
Depth / Queue Imbalance L2 Strategies
=======================================
Strategies using order book depth imbalance features.

Imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
Range: [-1, 1]. Positive = more buyers queued (bullish pressure).

These are mean-reversion or momentum strategies based on sustained
or extreme imbalance conditions.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy
from src.strategies.l2_ofi_strategies import _l2_trades, _compute_atr


class DepthImbalanceMomentumStrategy(BaseStrategy):
    """
    Depth Imbalance Momentum.
    THESIS: When the order book consistently shows strong bid imbalance,
    market makers and institutions are defending the ask price.
    Prices should drift higher as resting sell liquidity gets consumed.
    """
    name     = "Depth_Imbalance_Momentum"
    category = "l2_depth"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "imbal_thr":    [0.3, 0.4, 0.5],
        "persist_bars": [2, 3, 5],
        "rr_ratio":     [1.5, 2.0],
        "hold_bars":    [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"imbal_thr": 0.4, "persist_bars": 3,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        imbal_col = "imbal_L5_last" if "imbal_L5_last" in data.columns else None
        if imbal_col is None:
            return pd.Series(0, index=data.index)

        thr     = float(self.params["imbal_thr"])
        persist = int(self.params["persist_bars"])
        imbal   = data[imbal_col].fillna(0.0)

        strong_bid = (imbal >  thr)
        strong_ask = (imbal < -thr)

        # Sustained imbalance
        bid_persist = strong_bid.rolling(persist).sum() >= persist
        ask_persist = strong_ask.rolling(persist).sum() >= persist

        # Only enter when imbalance first reaches the sustained level
        signals = pd.Series(0, index=data.index)
        signals[bid_persist & ~bid_persist.shift(1).fillna(False)] =  1
        signals[ask_persist & ~ask_persist.shift(1).fillna(False)] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class DepthImbalanceMeanRevStrategy(BaseStrategy):
    """
    Depth Imbalance Mean Reversion (Fading Extreme Imbalance).
    THESIS: Extreme imbalance is often temporary — when one side dominates
    overwhelmingly, it's often about to be absorbed. Fade the extreme.
    """
    name     = "Depth_Imbalance_MeanRev"
    category = "l2_depth"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "extreme_thr": [0.6, 0.7, 0.8],
        "rr_ratio":    [1.0, 1.5],
        "hold_bars":   [3, 5, 8],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"extreme_thr": 0.7, "rr_ratio": 1.2, "hold_bars": 5}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        imbal_col = "imbal_L5_last" if "imbal_L5_last" in data.columns else None
        if imbal_col is None:
            return pd.Series(0, index=data.index)

        thr   = float(self.params["extreme_thr"])
        imbal = data[imbal_col].fillna(0.0)
        prev  = imbal.shift(1).fillna(0.0)

        # Extreme bid imbalance that starts to normalize → short (fade)
        extreme_bid_fading = (prev > thr)  & (imbal < thr)
        extreme_ask_fading = (prev < -thr) & (imbal > -thr)

        signals = pd.Series(0, index=data.index)
        signals[extreme_ask_fading] =  1
        signals[extreme_bid_fading] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class MultiTimeframeOFIStrategy(BaseStrategy):
    """
    Multi-Timeframe OFI (Trend + Entry).
    THESIS: Use slow rolling OFI direction as trend filter, then enter
    on bars where fast OFI is in the top/bottom percentile aligned with trend.
    Uses rolling percentiles (not absolute thresholds) to adapt to each market regime.
    """
    name     = "MultiTF_OFI"
    category = "l2_depth"
    timeframe = "1min"
    version  = "2.0"

    param_grid = {
        "trend_window": [20, 40],    # bars for slow OFI trend
        "entry_pct":    [75, 85],    # entry when fast OFI in top/bottom N%
        "rr_ratio":     [1.5, 2.0],
        "hold_bars":    [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"trend_window": 30, "entry_pct": 80,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "ofi_5" not in data.columns:
            return pd.Series(0, index=data.index)

        ofi  = data["ofi_5"].fillna(0.0)
        w    = int(self.params["trend_window"])
        pct  = float(self.params["entry_pct"])

        # Slow trend: rolling mean of OFI (sign gives direction)
        slow_ofi = ofi.rolling(w, min_periods=5).mean()

        # Rolling percentile thresholds for fast OFI entry
        ofi_high = ofi.rolling(60, min_periods=20).quantile(pct / 100)
        ofi_low  = ofi.rolling(60, min_periods=20).quantile(1 - pct / 100)

        buy_signal  = (ofi >= ofi_high) & (slow_ofi > slow_ofi.rolling(w).mean())
        sell_signal = (ofi <= ofi_low)  & (slow_ofi < slow_ofi.rolling(w).mean())

        signals = pd.Series(0, index=data.index)
        signals[buy_signal]  =  1
        signals[sell_signal] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)
