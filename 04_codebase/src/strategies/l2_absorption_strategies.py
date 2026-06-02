"""
Absorption / Iceberg L2 Strategies
====================================
Strategies that detect institutional iceberg orders absorbing aggressive flow.

Requires L2 bars with: absorption_score, absorption_buy, absorption_sell,
cvd_delta, price_range_tick.

Key edges:
1. Heavy selling absorbed without price falling → long
2. Heavy buying absorbed without price rising → short
3. Repeated replenishment at same level = strong iceberg
4. CVD divergence + absorption = high confidence signal
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy
from src.strategies.l2_ofi_strategies import _l2_trades, _compute_atr


class AbsorptionReversalStrategy(BaseStrategy):
    """
    Absorption Reversal.
    THESIS: When aggressive sellers drive large volume (negative CVD) but
    price doesn't fall, an iceberg buyer is absorbing — snap-back trade.
    Uses rolling percentile to adapt to market regime.
    """
    name     = "Absorption_Reversal"
    category = "l2_absorption"
    timeframe = "1min"
    version  = "2.0"

    param_grid = {
        "abs_pct":   [80, 85, 90],    # top percentile = "significant absorption"
        "roll_win":  [30, 60],
        "rr_ratio":  [1.5, 2.0],
        "hold_bars": [5, 8, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"abs_pct": 85, "roll_win": 60, "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "absorption_score" not in data.columns:
            return pd.Series(0, index=data.index)

        pct    = float(self.params["abs_pct"])
        roll_w = int(self.params["roll_win"])
        score  = data["absorption_score"].fillna(0.0)

        # Rolling extreme thresholds
        score_high = score.rolling(roll_w, min_periods=10).quantile(pct / 100)
        score_low  = score.rolling(roll_w, min_periods=10).quantile(1 - pct / 100)

        signals = pd.Series(0, index=data.index)
        signals[score >= score_high] =  1
        signals[score <= score_low]  = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class CVDAbsorptionStrategy(BaseStrategy):
    """
    CVD Divergence + Absorption (High Confidence).
    THESIS: When cumulative CVD is strongly negative (heavy selling) but
    the bar closed UP or flat (absorption confirmed), the next move is
    almost certainly higher. This is the strongest absorption signal.
    """
    name     = "CVD_Absorption"
    category = "l2_absorption"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "cvd_pct":    [20, 30],          # percentile threshold (bottom/top)
        "price_bias": ["close", "body"], # whether to use close or body
        "rr_ratio":   [1.5, 2.0, 2.5],
        "hold_bars":  [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"cvd_pct": 25, "price_bias": "close",
                                 "rr_ratio": 2.0, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "cvd_delta" not in data.columns:
            return pd.Series(0, index=data.index)

        cvd   = data["cvd_delta"].fillna(0.0)
        close = data["close"]
        open_ = data["open"]

        pct = float(self.params["cvd_pct"])
        cvd_low  = cvd.rolling(100, min_periods=20).quantile(pct / 100)
        cvd_high = cvd.rolling(100, min_periods=20).quantile(1 - pct / 100)

        # Heavy selling (extreme low CVD) but price closed green
        heavy_selling = cvd <= cvd_low
        heavy_buying  = cvd >= cvd_high
        closed_up    = close > open_
        closed_down  = close < open_

        signals = pd.Series(0, index=data.index)
        signals[heavy_selling & closed_up]   =  1   # sell absorbed, go long
        signals[heavy_buying  & closed_down] = -1   # buy absorbed, go short
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)


class RepeatedReplenishmentStrategy(BaseStrategy):
    """
    Repeated Replenishment (Persistent Iceberg).
    THESIS: When imbalance stays consistently positive over N bars despite
    active selling (negative CVD), a large resting buyer keeps replenishing.
    This is a persistent iceberg — price is supported and will rise.
    """
    name     = "Repeated_Replenishment"
    category = "l2_absorption"
    timeframe = "1min"
    version  = "1.0"

    param_grid = {
        "imbal_thr":      [0.2, 0.3, 0.4],
        "persist_bars":   [3, 5],
        "cvd_negative":   [True],
        "rr_ratio":       [1.5, 2.0],
        "hold_bars":      [8, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"imbal_thr": 0.3, "persist_bars": 4,
                                 "cvd_negative": True, "rr_ratio": 1.5, "hold_bars": 10}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        imbal_col = "imbal_L5_last" if "imbal_L5_last" in data.columns else None
        if imbal_col is None:
            return pd.Series(0, index=data.index)

        thr     = float(self.params["imbal_thr"])
        persist = int(self.params["persist_bars"])
        imbal   = data[imbal_col].fillna(0.0)
        cvd     = data.get("cvd_delta", pd.Series(0.0, index=data.index)).fillna(0.0)

        # Persistent positive imbalance (bid dominates) despite selling
        bid_dominant  = (imbal >  thr).rolling(persist).min().fillna(0).astype(bool)
        ask_dominant  = (imbal < -thr).rolling(persist).min().fillna(0).astype(bool)

        signals = pd.Series(0, index=data.index)
        if self.params.get("cvd_negative", True):
            sell_pressure = cvd < 0
            buy_pressure  = cvd > 0
            signals[bid_dominant & sell_pressure] =  1
            signals[ask_dominant & buy_pressure]  = -1
        else:
            signals[bid_dominant]  =  1
            signals[ask_dominant]  = -1

        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _l2_trades(data, signals,
                          float(self.params["rr_ratio"]),
                          int(self.params["hold_bars"]),
                          max_bars_per_trade)
