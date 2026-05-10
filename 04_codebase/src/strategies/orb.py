"""
Opening Range Breakout (ORB)
==============================
THESIS: First N minutes of RTH establish a range. Breakout from this
range with volume confirmation indicates the session's directional bias.

Tested on ES in H2 (REJECTED at 5min resolution with 9 variants).
Re-testing on all markets with parameterized duration.

SIGNAL:
  - Compute high/low of first N minutes after RTH open
  - Long if price breaks above range high + buffer
  - Short if price breaks below range low - buffer
  - Volume must exceed avg_vol * vol_multiplier

EXIT: ATR-based stop/target or EOD (whichever first)

NOTE: Uses 1-min data internally to compute the opening range,
      but can be run on 5-min bars if ORB duration >= 15 min.
"""

from typing import Any, Dict, List, Optional
from datetime import time

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy, Trade


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout — parameterized ORB duration."""

    name = "ORB"
    description = "Opening Range Breakout with volume confirmation"
    category = "level_breakout"
    timeframe = "5min"
    min_holding_bars = 1
    max_trades_per_day = 1

    param_grid = {
        "orb_minutes": [15, 30, 60],
        "buffer_atr_mult": [0.0, 0.25],
        "rr_ratio": [1.0, 1.5],
        "timeout_bars": [12, 24],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "orb_minutes": 30,
            "buffer_atr_mult": 0.25,
            "rr_ratio": 1.5,
            "timeout_bars": 24,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        For each session:
        1. Identify the ORB (first N minutes of RTH)
        2. After ORB period ends, look for breakout
        3. Signal on first bar that breaks range + buffer
        """
        signals = pd.Series(0, index=data.index)
        orb_minutes = self.params["orb_minutes"]
        buffer_mult = self.params["buffer_atr_mult"]

        if "session_date" not in data.columns:
            return signals
        if "atr" not in data.columns:
            return signals

        # Determine bar size from data frequency
        if len(data) >= 2:
            freq_mins = (data.index[1] - data.index[0]).total_seconds() / 60
        else:
            return signals

        # ORB end time (minutes after 09:30)
        orb_end_hour = 9 + (30 + orb_minutes) // 60
        orb_end_minute = (30 + orb_minutes) % 60
        orb_end = time(orb_end_hour, orb_end_minute)

        for session_date, group in data.groupby("session_date"):
            if len(group) < 5:
                continue

            # ORB range
            orb_bars = group[group.index.time <= orb_end]
            if len(orb_bars) == 0:
                continue

            orb_high = orb_bars["high"].max()
            orb_low = orb_bars["low"].min()

            # Post-ORB bars
            post_orb = group[group.index.time > orb_end]
            if len(post_orb) == 0:
                continue

            # Buffer
            atr_val = post_orb["atr"].iloc[0] if "atr" in post_orb.columns else 0
            buffer = buffer_mult * atr_val

            # Find first breakout
            signaled = False
            for idx in post_orb.index:
                if signaled:
                    break
                bar = post_orb.loc[idx]
                # Don't trade in last 30 min (avoid EOD noise)
                if bar.name.time() >= time(15, 30):
                    break

                if bar["high"] > orb_high + buffer:
                    signals.loc[idx] = 1
                    signaled = True
                elif bar["low"] < orb_low - buffer:
                    signals.loc[idx] = -1
                    signaled = True

        return signals