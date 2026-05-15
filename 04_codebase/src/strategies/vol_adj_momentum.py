"""
Volatility-Adjusted Momentum / Z-Score (Batch 5)
Z-score of rolling returns over lookback_bars bars.
Z > threshold → long (momentum continuation); Z < -threshold → short.
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class VolAdjMomentumStrategy(BaseStrategy):
    name = "Vol_Adj_Momentum"
    category = "momentum"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "lookback_bars": [12, 24],
        "threshold": [1.5, 2.0],
        "hold_bars": [6, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"lookback_bars": 12, "threshold": 1.5, "hold_bars": 6}

    def generate_signals(self, data):
        lookback = self.params["lookback_bars"]
        threshold = self.params["threshold"]
        close = data["close"]
        returns = close.pct_change()
        roll_mean = returns.rolling(lookback, min_periods=lookback).mean()
        roll_std = returns.rolling(lookback, min_periods=lookback).std()
        z_score = (returns - roll_mean) / roll_std.replace(0, np.nan)
        signals = pd.Series(0, index=data.index)
        signals[z_score > threshold] = 1
        signals[z_score < -threshold] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        hold_bars = min(self.params["hold_bars"], max_bars_per_trade)
        stop_atr_mult = 1.5
        trades = []
        traded_sessions = set()

        for idx in signals[signals != 0].index:
            session = data.loc[idx, "session_date"] if "session_date" in data.columns else idx.date()
            if session in traded_sessions:
                continue
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue
                entry_bar = data.iloc[entry_loc + 1]
                entry_time = entry_bar.name
                t = entry_time.time()
                if not (pd.Timestamp("09:30").time() <= t <= pd.Timestamp("15:30").time()):
                    continue
                entry_price = entry_bar["open"]
                direction = int(signals[idx])
                atr_val = data["atr"].iloc[entry_loc]
                if np.isnan(atr_val) or atr_val == 0:
                    continue
                stop_loss = entry_price - direction * stop_atr_mult * atr_val
                exit_price = None
                exit_time = None
                exit_type = "timeout"

                for i in range(1, hold_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar = data.iloc[entry_loc + 1 + i]
                    if direction == 1 and bar["low"] <= stop_loss:
                        exit_price = stop_loss
                        exit_time = bar.name
                        exit_type = "stop"
                        break
                    elif direction == -1 and bar["high"] >= stop_loss:
                        exit_price = stop_loss
                        exit_time = bar.name
                        exit_type = "stop"
                        break

                if exit_price is None:
                    exit_idx = entry_loc + 1 + hold_bars
                    if exit_idx < len(data):
                        exit_bar = data.iloc[exit_idx]
                        exit_price = exit_bar["close"]
                        exit_time = exit_bar.name
                    else:
                        continue

                gross_pnl = (exit_price - entry_price) * direction
                traded_sessions.add(session)
                trades.append({
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": exit_time,
                    "exit_price": exit_price,
                    "direction": direction,
                    "exit_type": exit_type,
                    "gross_pnl": gross_pnl,
                    "stop_price": stop_loss,
                })
            except Exception:
                continue
        return trades

    def trades_to_dataframe(self, trades):
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df
