"""
London Open Breakout (LOB) — M6E / M6B
========================================
THESIS: The London-New York overlap session opens at 08:20 ET for CME FX
futures. This is the highest-volume window for EUR/USD. European economic
data (released at 08:30 ET: CPI, PPI, unemployment) and overnight position
rebalancing concentrate directional flow into the first 15-30 minutes.
A breakout of the opening range formed in those first minutes has strong
follow-through into the NY morning session.

This is DISTINCT from the equity ORB (09:30 ET) in three key ways:
  1. Driven by macro data (08:30 ET releases), not equity open auction.
  2. Currency markets trend on 4-8h timeframes; momentum extends longer.
  3. No volume filter (FX volume data is less reliable than equity volume).

SIGNAL:
  - Opening range = high/low of first orb_minutes bars from 08:20 ET
  - First bar to close strictly above range_high → long signal
  - First bar to close strictly below range_low  → short signal
  - One trade per session only; no signals after cutoff_time

ENTRY / EXIT:
  - Entry at close of signal bar (market order approximation)
  - Target: entry ± rr_ratio × range_size
  - Stop:   opposite side of the opening range
  - Hard exit at cutoff_time if still open (11:30 ET = London close)

VALIDATION STATUS: EXPERIMENTAL (first run pending)
"""

from typing import Any, Dict, List, Optional
from datetime import time

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy, Trade


class LondonOpenBreakoutStrategy(BaseStrategy):
    """London Open Breakout for CME FX futures (M6E, M6B)."""

    name        = "London_Open_Breakout"
    description = "Opening range breakout at London-NY session overlap (08:20 ET)"
    category    = "session_breakout"
    timeframe   = "5min"
    version     = "1.0"
    min_holding_bars  = 1
    max_trades_per_day = 1

    # Opening range starts at RTH open for M6E
    _SESSION_OPEN  = time(8, 20)   # M6E RTH open
    _CUTOFF        = time(11, 30)  # London close; no new entries after this

    param_grid = {
        "orb_minutes": [15, 30],       # how long to build the opening range
        "rr_ratio":    [1.0, 1.5, 2.0], # risk/reward ratio for target
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"orb_minutes": 30, "rr_ratio": 1.5}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        Returns a Series of {-1, 0, +1} signals indexed like data.
        Signal is placed on the BAR where the breakout close is confirmed.
        Entry is at the next bar's open (simulated in signals_to_trades).
        """
        signals = pd.Series(0, index=data.index)

        if "session_date" not in data.columns:
            return signals

        orb_minutes = int(self.params["orb_minutes"])

        # Compute ORB end time: session_open + orb_minutes
        orb_end_total_minutes = (self._SESSION_OPEN.hour * 60
                                 + self._SESSION_OPEN.minute
                                 + orb_minutes)
        orb_end = time(orb_end_total_minutes // 60, orb_end_total_minutes % 60)

        for session_date, group in data.groupby("session_date"):
            if len(group) < 4:
                continue

            # ORB bars: from session open through orb_end (inclusive)
            t = group.index.time
            orb_mask = (t >= self._SESSION_OPEN) & (t <= orb_end)
            orb_bars = group[orb_mask]

            if len(orb_bars) == 0:
                continue

            range_high = orb_bars["high"].max()
            range_low  = orb_bars["low"].min()
            range_size = range_high - range_low

            if range_size <= 0:
                continue

            # Post-ORB bars where breakout signals are valid
            post_mask = (t > orb_end) & (t < self._CUTOFF)
            post_bars = group[post_mask]

            if len(post_bars) == 0:
                continue

            # Find first bar that closes outside the ORB (and only that one)
            signaled = False
            for idx in post_bars.index:
                if signaled:
                    break
                bar_close = post_bars.loc[idx, "close"]
                if bar_close > range_high:
                    signals.loc[idx] = 1
                    signaled = True
                elif bar_close < range_low:
                    signals.loc[idx] = -1
                    signaled = True

        return signals

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        """
        Convert signals to trades with ATR-based stop and fixed-multiple target.

        Entry: next bar's open after the signal bar.
        Stop:  opposite side of the ORB (range_low for long, range_high for short).
        Target: entry ± rr_ratio × (entry - stop) to maintain the risk ratio.
        Cutoff: hard exit at 11:30 ET bar.
        """
        rr_ratio   = float(self.params["rr_ratio"])
        orb_minutes = int(self.params["orb_minutes"])

        orb_end_total_minutes = (self._SESSION_OPEN.hour * 60
                                 + self._SESSION_OPEN.minute
                                 + orb_minutes)
        orb_end = time(orb_end_total_minutes // 60, orb_end_total_minutes % 60)

        # Precompute ORB ranges per session
        orb_cache: Dict[Any, Dict] = {}
        for session_date, group in data.groupby("session_date"):
            t = group.index.time
            orb_mask = (t >= self._SESSION_OPEN) & (t <= orb_end)
            orb_bars = group[orb_mask]
            if len(orb_bars) == 0:
                continue
            orb_cache[session_date] = {
                "high": orb_bars["high"].max(),
                "low":  orb_bars["low"].min(),
            }

        trades = []
        signal_indices = signals[signals != 0].index

        for sig_idx in signal_indices:
            direction = int(signals[sig_idx])

            # Session date for this signal
            session_date = data.loc[sig_idx, "session_date"] if "session_date" in data.columns else None
            orb = orb_cache.get(session_date, None)
            if orb is None:
                continue

            range_high = orb["high"]
            range_low  = orb["low"]

            # Entry at next bar open
            sig_loc = data.index.get_loc(sig_idx)
            if sig_loc + 1 >= len(data):
                continue

            entry_bar   = data.iloc[sig_loc + 1]
            entry_price = entry_bar["open"]
            entry_time  = entry_bar.name

            # Stop = opposite ORB side
            stop_price = range_low if direction == 1 else range_high
            stop_dist  = abs(entry_price - stop_price)
            if stop_dist <= 0:
                continue

            target_price = entry_price + direction * rr_ratio * stop_dist

            # Walk forward bar by bar
            exit_price = None
            exit_time  = None
            exit_type  = "timeout"

            for i in range(1, max_bars_per_trade + 1):
                loc = sig_loc + 1 + i
                if loc >= len(data):
                    break

                bar = data.iloc[loc]

                # Hard cutoff at London close
                if bar.name.time() >= self._CUTOFF:
                    exit_price = bar["open"]   # exit at open of the cutoff bar
                    exit_time  = bar.name
                    exit_type  = "time_cutoff"
                    break

                # Stop check (pessimistic fill at stop price)
                if direction == 1 and bar["low"] <= stop_price:
                    exit_price = stop_price
                    exit_time  = bar.name
                    exit_type  = "stop"
                    break
                elif direction == -1 and bar["high"] >= stop_price:
                    exit_price = stop_price
                    exit_time  = bar.name
                    exit_type  = "stop"
                    break

                # Target check
                if direction == 1 and bar["high"] >= target_price:
                    exit_price = target_price
                    exit_time  = bar.name
                    exit_type  = "target"
                    break
                elif direction == -1 and bar["low"] <= target_price:
                    exit_price = target_price
                    exit_time  = bar.name
                    exit_type  = "target"
                    break

            # Fallback: close at last bar
            if exit_price is None:
                last_loc = min(sig_loc + 1 + max_bars_per_trade, len(data) - 1)
                exit_price = data.iloc[last_loc]["close"]
                exit_time  = data.iloc[last_loc].name
                exit_type  = "timeout"

            gross_pnl = (exit_price - entry_price) * direction

            trades.append({
                "entry_time":  entry_time,
                "entry_price": entry_price,
                "exit_time":   exit_time,
                "exit_price":  exit_price,
                "direction":   direction,
                "exit_type":   exit_type,
                "gross_pnl":   gross_pnl,
                "stop_price":  stop_price,
                "target_price": target_price,
                "orb_high":    range_high,
                "orb_low":     range_low,
            })

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
