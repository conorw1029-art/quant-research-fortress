"""
RTH Opening Range Breakout (Batch 6)
First orb_bars bars (5-min) of the RTH session establish the opening range.
First close above the range high → long; first close below range low → short.
One trade per session. ATR stop, hold_bars exit.

Distinct from london_open_breakout.py (M6E at 08:20 ET).
This targets the US RTH open — applicable to ES, GC, CL.
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class RTHORBStrategy(BaseStrategy):
    name = "RTH_ORB"
    category = "breakout"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "orb_bars":  [6, 12],     # 30 min or 60 min opening range
        "stop_atr":  [1.5, 2.0],  # ATR multiplier for stop
        "hold_bars": [6, 12],     # bars until timeout exit
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"orb_bars": 6, "stop_atr": 1.5, "hold_bars": 6}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Unused — all trade logic is in signals_to_trades."""
        return pd.Series(0, index=data.index)

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        orb_bars  = self.params["orb_bars"]
        stop_atr  = self.params["stop_atr"]
        hold_bars = min(self.params["hold_bars"], max_bars_per_trade)
        trades = []

        session_col = "session_date" if "session_date" in data.columns else None

        if session_col:
            groups = data.groupby(data[session_col])
        else:
            groups = data.groupby(data.index.date)

        for date, group in groups:
            if len(group) <= orb_bars:
                continue

            # Opening range from first orb_bars bars
            orb_window = group.iloc[:orb_bars]
            orb_high   = orb_window["high"].max()
            orb_low    = orb_window["low"].min()

            # Post-ORB bars: find first breakout close
            post_orb = group.iloc[orb_bars:]
            signal_found = False

            for idx in post_orb.index:
                if signal_found:
                    break
                bar = data.loc[idx]
                close = bar["close"]

                if close > orb_high:
                    direction = 1
                elif close < orb_low:
                    direction = -1
                else:
                    continue

                signal_found = True
                bar_loc = data.index.get_loc(idx)
                if bar_loc + 1 >= len(data):
                    break

                entry_bar   = data.iloc[bar_loc + 1]
                entry_time  = entry_bar.name
                entry_price = entry_bar["open"]

                atr_val = data["atr"].iloc[bar_loc]
                if np.isnan(atr_val) or atr_val == 0:
                    break

                stop_loss  = entry_price - direction * stop_atr * atr_val
                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, hold_bars + 1):
                    if bar_loc + 1 + i >= len(data):
                        break
                    chk = data.iloc[bar_loc + 1 + i]
                    if direction == 1 and chk["low"] <= stop_loss:
                        exit_price = stop_loss
                        exit_time  = chk.name
                        exit_type  = "stop"
                        break
                    elif direction == -1 and chk["high"] >= stop_loss:
                        exit_price = stop_loss
                        exit_time  = chk.name
                        exit_type  = "stop"
                        break

                if exit_price is None:
                    exit_idx = bar_loc + 1 + hold_bars
                    if exit_idx < len(data):
                        exit_bar   = data.iloc[exit_idx]
                        exit_price = exit_bar["close"]
                        exit_time  = exit_bar.name
                    else:
                        break

                gross_pnl = (exit_price - entry_price) * direction
                trades.append({
                    "entry_time":  entry_time,
                    "entry_price": entry_price,
                    "exit_time":   exit_time,
                    "exit_price":  exit_price,
                    "direction":   direction,
                    "exit_type":   exit_type,
                    "gross_pnl":   gross_pnl,
                    "stop_price":  stop_loss,
                    "orb_high":    orb_high,
                    "orb_low":     orb_low,
                })

        return trades

    def trades_to_dataframe(self, trades):
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
