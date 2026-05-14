"""
VWAP Reclaim / Reject
======================
THESIS: VWAP is the institutional benchmark. When price dips below the
session VWAP and then reclaims it with a strong close, institutional
buyers stepped in at value. That reclaim bar signals continuation above
VWAP. The reverse (price drops back below VWAP after trading above) is
a rejection signal.

SIGNAL:
  - Long  (reclaim): prior bar closes BELOW session_vwap
                     AND current bar closes ABOVE session_vwap
                     AND (volume >= vol_multiplier × volume_avg  OR vol_multiplier=1.0)
  - Short (reject):  prior bar closes ABOVE session_vwap
                     AND current bar closes BELOW session_vwap
                     AND same volume filter
  - Only between 10:00 and 14:00 ET (avoid open chaos and EOD flow)
  - One trade per session

ENTRY / EXIT:
  - Entry at next bar open
  - Stop:   low of signal bar (long) / high of signal bar (short)
  - Target: rr_ratio × stop_distance in direction of trade
  - Timeout: 12 bars

PARAM GRID: 3 × 2 × 2 = 12 combos
  vol_multiplier: [1.0, 1.2, 1.5]   (1.0 = no volume filter)
  rr_ratio:       [1.5, 2.0]
  hold_bars:      [6, 12]

LOOKAHEAD RISK: session_vwap is cumulative from session open — causal.
  Volume filter uses rolling average of prior bars — causal.
"""

from typing import Any, Dict, List, Optional
from datetime import time

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


_SESSION_START = time(10, 0)
_SESSION_END   = time(14, 0)


class VWAPReclaimStrategy(BaseStrategy):

    name        = "VWAP_Reclaim"
    description = "Session VWAP reclaim (long) / reject (short) with volume filter"
    category    = "vwap_mean_reversion"
    timeframe   = "5min"
    version     = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "vol_multiplier": [1.0, 1.2, 1.5],
        "rr_ratio":       [1.5, 2.0],
        "hold_bars":      [6, 12],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"vol_multiplier": 1.0, "rr_ratio": 1.5, "hold_bars": 12}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "session_vwap" not in data.columns:
            return pd.Series(0, index=data.index)

        vol_mult = float(self.params["vol_multiplier"])
        close    = data["close"]
        vwap     = data["session_vwap"]

        above_vwap = (close > vwap).astype(bool)
        prev_above = above_vwap.shift(1).fillna(False).astype(bool)

        # Volume gate (use volume_avg if available)
        if vol_mult > 1.0 and "volume_avg" in data.columns:
            vol_ok = (data["volume"] >= vol_mult * data["volume_avg"]).fillna(False)
        else:
            vol_ok = pd.Series(True, index=data.index)

        # Time-of-day gate
        t = data.index.time
        in_window = pd.Series(
            (t >= _SESSION_START) & (t < _SESSION_END),
            index=data.index
        )

        signals = pd.Series(0, index=data.index)
        # Reclaim: was below, now above
        reclaim = (~prev_above) & above_vwap & vol_ok & in_window
        # Reject:  was above, now below
        reject  = prev_above & (~above_vwap) & vol_ok & in_window

        signals[reclaim] =  1
        signals[reject]  = -1

        # Enforce one trade per session
        if "session_date" in data.columns:
            signals = self._one_per_session(signals, data["session_date"])

        return signals

    @staticmethod
    def _one_per_session(signals: pd.Series, session_date: pd.Series) -> pd.Series:
        result = signals.copy()
        seen = set()
        for idx, val in signals.items():
            if val == 0:
                continue
            sd = session_date.loc[idx]
            if sd in seen:
                result.loc[idx] = 0
            else:
                seen.add(sd)
        return result

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        rr_ratio   = float(self.params["rr_ratio"])
        hold_bars  = int(self.params["hold_bars"])
        timeout    = min(hold_bars, max_bars_per_trade)

        trades = []
        for idx in signals[signals != 0].index:
            try:
                direction = int(signals[idx])
                sig_loc   = data.index.get_loc(idx)
                if sig_loc + 1 >= len(data):
                    continue

                entry_bar   = data.iloc[sig_loc + 1]
                entry_price = entry_bar["open"]
                entry_time  = entry_bar.name

                # Stop = signal bar's edge
                sig_bar   = data.loc[idx]
                stop_price = sig_bar["low"] if direction == 1 else sig_bar["high"]
                stop_dist  = abs(entry_price - stop_price)
                if stop_dist <= 0:
                    continue

                target_price = entry_price + direction * rr_ratio * stop_dist

                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, timeout + 1):
                    loc = sig_loc + 1 + i
                    if loc >= len(data):
                        break
                    bar = data.iloc[loc]

                    if direction == 1:
                        if bar["low"] <= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["high"] >= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
                    else:
                        if bar["high"] >= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["low"] <= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break

                if exit_price is None:
                    last_loc   = min(sig_loc + 1 + timeout, len(data) - 1)
                    exit_price = data.iloc[last_loc]["close"]
                    exit_time  = data.iloc[last_loc].name
                    exit_type  = "timeout"

                trades.append({
                    "entry_time":  entry_time,  "entry_price": entry_price,
                    "exit_time":   exit_time,   "exit_price":  exit_price,
                    "direction":   direction,    "exit_type":   exit_type,
                    "gross_pnl":   (exit_price - entry_price) * direction,
                    "stop_price":  stop_price,   "target_price": target_price,
                })
            except Exception:
                continue
        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
