"""
Overnight Gap Fill — Mean Reversion (Batch 6)
Fades the overnight gap when gap_pct is in a defined range.
Gap up → short; gap down → long. Target: prior session close (full fill).
Stop: ATR-based. Hard exit at 11:00 ET (~90 min after open) if not filled.

Requires: gap_pct (computed by ESDataLoader.add_features, first bar only),
          prior_close, atr columns.
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class GapFillStrategy(BaseStrategy):
    name = "Gap_Fill"
    category = "mean_reversion"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "gap_min_pct": [0.1, 0.2],    # minimum gap size to trade (%)
        "gap_max_pct": [0.5, 0.75],   # maximum gap size to trade (%)
        "stop_atr":    [1.0, 1.5],    # ATR multiplier for stop loss
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"gap_min_pct": 0.1, "gap_max_pct": 0.5, "stop_atr": 1.0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        gap_pct  = data["gap_pct"]
        gap_min  = self.params["gap_min_pct"]
        gap_max  = self.params["gap_max_pct"]

        signals = pd.Series(0, index=data.index)

        # gap_pct is only non-NaN on the first bar of each session
        valid = gap_pct.notna()
        gap_up   = valid & (gap_pct >  gap_min) & (gap_pct <  gap_max)
        gap_down = valid & (gap_pct < -gap_min) & (gap_pct > -gap_max)

        signals[gap_up]   = -1   # fade the up-gap → short
        signals[gap_down] =  1   # fade the down-gap → long

        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        stop_atr = self.params["stop_atr"]
        # Hard exit at 11:00 ET = ~18 bars after 09:30 open (18×5min = 90 min)
        max_hold = 18
        trades = []
        traded_sessions = set()

        for idx in signals[signals != 0].index:
            session = (data.loc[idx, "session_date"]
                       if "session_date" in data.columns else idx.date())
            if session in traded_sessions:
                continue

            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                entry_bar   = data.iloc[entry_loc + 1]
                entry_time  = entry_bar.name
                entry_price = entry_bar["open"]
                direction   = int(signals[idx])

                # Target: prior session close (full gap fill)
                target_price = data.loc[idx, "prior_close"]
                if np.isnan(target_price):
                    continue

                atr_val = data["atr"].iloc[entry_loc]
                if np.isnan(atr_val) or atr_val == 0:
                    continue

                stop_loss = entry_price - direction * stop_atr * atr_val

                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, max_hold + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar = data.iloc[entry_loc + 1 + i]

                    # Check stop
                    if direction == 1 and bar["low"] <= stop_loss:
                        exit_price = stop_loss
                        exit_time  = bar.name
                        exit_type  = "stop"
                        break
                    elif direction == -1 and bar["high"] >= stop_loss:
                        exit_price = stop_loss
                        exit_time  = bar.name
                        exit_type  = "stop"
                        break

                    # Check target (gap fill)
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

                if exit_price is None:
                    exit_idx = entry_loc + 1 + max_hold
                    if exit_idx < len(data):
                        exit_bar   = data.iloc[exit_idx]
                        exit_price = exit_bar["close"]
                        exit_time  = exit_bar.name
                    else:
                        continue

                gross_pnl = (exit_price - entry_price) * direction
                traded_sessions.add(session)
                trades.append({
                    "entry_time":  entry_time,
                    "entry_price": entry_price,
                    "exit_time":   exit_time,
                    "exit_price":  exit_price,
                    "direction":   direction,
                    "exit_type":   exit_type,
                    "gross_pnl":   gross_pnl,
                    "stop_price":  stop_loss,
                    "target_price": target_price,
                })
            except Exception:
                continue

        return trades

    def trades_to_dataframe(self, trades):
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
