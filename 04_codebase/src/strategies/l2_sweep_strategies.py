"""
Sweep-based L2 Strategies
==========================
Strategies based on sweep detection (large aggressive orders consuming
multiple book levels).

Requires L2 bars with columns: buy_sweeps, sell_sweeps, net_sweeps,
sweep_net_size, imbal_L5_last, cvd_delta.

Key edges:
1. Sweep + no book replenishment → continuation (institutional aggression)
2. Sweep followed by rapid book replenishment → reversal (trapped aggressor)
3. Session H/L sweep reversal (classic liquidity grab)
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy
from src.strategies.l2_ofi_strategies import _l2_trades, _compute_atr, _scan_exit


class SweepContinuationStrategy(BaseStrategy):
    """
    Sweep Continuation.
    THESIS: Multi-level buy sweeps signal institutional urgency. If the book
    does not replenish (net_sweeps stays positive over next N bars), price
    will continue in sweep direction.
    """
    name     = "Sweep_Continuation"
    category = "l2_sweep"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "min_sweeps":  [2, 3],
        "confirm_bars": [1, 2],
        "rr_ratio":    [1.5, 2.0],
        "hold_bars":   [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"min_sweeps": 2, "confirm_bars": 1,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "buy_sweeps" not in data.columns:
            return pd.Series(0, index=data.index)

        min_sw   = int(self.params["min_sweeps"])
        conf     = int(self.params["confirm_bars"])

        buy_sw  = data["buy_sweeps"].fillna(0)
        sell_sw = data["sell_sweeps"].fillna(0)

        # Significant sweep episode
        big_buy_sweep  = buy_sw >= min_sw
        big_sell_sweep = sell_sw >= min_sw

        # Rolling confirmation: sustained after the sweep bar
        buy_conf  = big_buy_sweep.rolling(conf + 1).sum() >= conf
        sell_conf = big_sell_sweep.rolling(conf + 1).sum() >= conf

        signals = pd.Series(0, index=data.index)
        signals[buy_conf  & ~big_sell_sweep] =  1
        signals[sell_conf & ~big_buy_sweep]  = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class SweepAbsorptionReversalStrategy(BaseStrategy):
    """
    Sweep + Absorption Reversal.
    THESIS: A sweep that fails to move price (absorbed by a large iceberg)
    traps aggressive traders. Fade the sweep for a sharp reversal.
    """
    name     = "Sweep_Absorption_Reversal"
    category = "l2_sweep"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "min_sweeps":       [2, 3],
        "absorption_thr":   [0.5, 0.8],
        "rr_ratio":         [1.5, 2.0],
        "hold_bars":        [5, 8],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"min_sweeps": 2, "absorption_thr": 0.6,
                                 "rr_ratio": 1.5, "hold_bars": 6}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "buy_sweeps" not in data.columns or "absorption_score" not in data.columns:
            return pd.Series(0, index=data.index)

        min_sw = int(self.params["min_sweeps"])
        abs_thr = float(self.params["absorption_thr"])

        buy_sw  = data["buy_sweeps"].fillna(0)
        sell_sw = data["sell_sweeps"].fillna(0)
        abs_score = data["absorption_score"].fillna(0.0)

        # Buy sweep but price absorbed (score < -abs_thr = buy was absorbed)
        buy_swept_absorbed  = (buy_sw >= min_sw)  & (abs_score < -abs_thr)
        # Sell sweep but price absorbed
        sell_swept_absorbed = (sell_sw >= min_sw) & (abs_score > abs_thr)

        signals = pd.Series(0, index=data.index)
        signals[sell_swept_absorbed] =  1  # sell sweep absorbed → go long
        signals[buy_swept_absorbed]  = -1  # buy sweep absorbed → go short
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class SessionHighLowSweepReversalStrategy(BaseStrategy):
    """
    Session H/L Sweep Reversal (Liquidity Grab).
    THESIS: When price sweeps above session high or below session low with
    a sweep event and immediately reverses, it's a liquidity grab — all the
    stops above the high were hit, now the move reverses sharply.
    """
    name     = "Session_HL_Sweep_Reversal"
    category = "l2_sweep"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "min_sweeps": [1, 2],
        "rr_ratio":   [1.5, 2.0, 2.5],
        "hold_bars":  [8, 12, 20],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"min_sweeps": 1, "rr_ratio": 2.0, "hold_bars": 12}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "buy_sweeps" not in data.columns:
            return pd.Series(0, index=data.index)

        min_sw = int(self.params["min_sweeps"])

        close  = data["close"]
        high   = data["high"]
        low    = data["low"]

        # Rolling session high/low (from 9:30 ET, approximate as 240-bar window)
        sess_high = high.rolling(240, min_periods=5).max().shift(1)
        sess_low  = low.rolling(240, min_periods=5).min().shift(1)

        buy_sw  = data["buy_sweeps"].fillna(0)
        sell_sw = data["sell_sweeps"].fillna(0)

        # Price went above session high but closed back below it → short
        grabbed_high = (high > sess_high) & (close < sess_high) & (buy_sw >= min_sw)
        # Price went below session low but closed back above it → long
        grabbed_low  = (low < sess_low)  & (close > sess_low)  & (sell_sw >= min_sw)

        signals = pd.Series(0, index=data.index)
        signals[grabbed_low]  =  1
        signals[grabbed_high] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)
