"""
Donchian Channel Breakout — Intraday (Batch 5)
Donchian channel computed on 5-min bars (rolling don_period bars of high/low).
Distinct from donchian_breakout.py which runs on 1D data.
Break above upper channel → long; break below lower → short.
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class DonchianIntradayStrategy(BaseStrategy):
    name = "Donchian_Intraday"
    category = "trend"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "don_period": [20, 40],
        "stop_atr": [1.5, 2.0],
        "hold_bars": [6, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"don_period": 20, "stop_atr": 1.5, "hold_bars": 6}

    def generate_signals(self, data):
        don_period = self.params["don_period"]
        high = data["high"]
        low = data["low"]
        close = data["close"]
        # channel based on prior don_period bars (use shift to avoid look-ahead)
        upper = high.shift(1).rolling(don_period, min_periods=don_period).max()
        lower = low.shift(1).rolling(don_period, min_periods=don_period).min()
        # breakout: current close exceeds prior channel extreme
        signals = pd.Series(0, index=data.index)
        signals[(close > upper) & upper.notna()] = 1
        signals[(close < lower) & lower.notna()] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        hold_bars = min(self.params["hold_bars"], max_bars_per_trade)
        stop_atr = self.params["stop_atr"]
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
                stop_loss = entry_price - direction * stop_atr * atr_val
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
