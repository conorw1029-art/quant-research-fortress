"""
Bar Pattern Strategies
========================
Inside Bar, Outside Bar, Pin Bar — classic price action patterns.

These are all parameterized to work on any timeframe and market.
The pattern detection is in generate_signals(); exits use the
standard BaseStrategy stop/target/timeout framework.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class InsideBarStrategy(BaseStrategy):
    """
    THESIS: Inside bars (bar range contained within prior bar) represent
    consolidation. Breakout from the mother bar indicates directional
    commitment. Better in trending regimes.

    SIGNAL: Bar N is inside bar N-1. On bar N+1, if price breaks above
    mother bar high → long; below mother bar low → short.
    """

    name = "Inside_Bar"
    description = "Inside bar breakout"
    category = "level_breakout"
    timeframe = "1h"
    min_holding_bars = 1
    max_trades_per_day = 1

    param_grid = {
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.0, 1.5, 2.0],
        "timeout_bars": [6, 12],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "stop_atr_mult": 1.5,
            "rr_ratio": 1.5,
            "timeout_bars": 12,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        h = data["high"].values
        l = data["low"].values
        c = data["close"].values

        for i in range(2, len(data)):
            # Bar i-1 is inside bar i-2 (mother bar)
            is_inside = (h[i-1] <= h[i-2]) and (l[i-1] >= l[i-2])
            if not is_inside:
                continue

            # Bar i breaks mother bar
            if c[i] > h[i-2]:
                signals.iloc[i] = 1
            elif c[i] < l[i-2]:
                signals.iloc[i] = -1

        return signals


class OutsideBarStrategy(BaseStrategy):
    """
    THESIS: Outside bars (engulfing — current bar range exceeds prior bar)
    represent strong momentum. Trade in direction of the outside bar's close.

    SIGNAL: Bar N is outside bar (high > prior high AND low < prior low).
    Direction = sign of (close - open) of the outside bar.
    """

    name = "Outside_Bar"
    description = "Outside bar (engulfing) continuation"
    category = "momentum"
    timeframe = "1h"
    min_holding_bars = 1
    max_trades_per_day = 1

    param_grid = {
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.0, 1.5, 2.0],
        "timeout_bars": [6, 12],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "stop_atr_mult": 1.5,
            "rr_ratio": 1.5,
            "timeout_bars": 12,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        h = data["high"].values
        l = data["low"].values
        o = data["open"].values
        c = data["close"].values

        for i in range(1, len(data)):
            is_outside = (h[i] > h[i-1]) and (l[i] < l[i-1])
            if not is_outside:
                continue

            if c[i] > o[i]:
                signals.iloc[i] = 1
            elif c[i] < o[i]:
                signals.iloc[i] = -1

        return signals


class PinBarStrategy(BaseStrategy):
    """
    THESIS: Pin bars (long wick, small body) at extremes indicate
    rejection and reversal. The wick must be >= 2x the body.

    SIGNAL: Bullish pin bar (long lower wick) → long.
    Bearish pin bar (long upper wick) → short.
    """

    name = "Pin_Bar"
    description = "Pin bar reversal at extremes"
    category = "mean_reversion"
    timeframe = "1h"
    min_holding_bars = 1
    max_trades_per_day = 1

    param_grid = {
        "wick_body_ratio": [2.0, 3.0],
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.5, 2.0],
        "timeout_bars": [6, 12],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "wick_body_ratio": 2.0,
            "stop_atr_mult": 1.5,
            "rr_ratio": 2.0,
            "timeout_bars": 12,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)
        ratio = self.params["wick_body_ratio"]

        h = data["high"].values
        l = data["low"].values
        o = data["open"].values
        c = data["close"].values

        for i in range(1, len(data)):
            body = abs(c[i] - o[i])
            if body < 1e-10:
                body = 1e-10  # avoid div by zero

            upper_wick = h[i] - max(o[i], c[i])
            lower_wick = min(o[i], c[i]) - l[i]

            # Bullish pin bar: long lower wick
            if lower_wick >= ratio * body and lower_wick > upper_wick:
                signals.iloc[i] = 1

            # Bearish pin bar: long upper wick
            elif upper_wick >= ratio * body and upper_wick > lower_wick:
                signals.iloc[i] = -1

        return signals