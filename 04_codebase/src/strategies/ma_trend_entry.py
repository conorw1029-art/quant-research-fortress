"""
MA Trend Filter + Pullback Entry (Batch 5)
SMA(slow_period) defines trend direction; enter when price crosses SMA
in the direction of the trend (rising SMA → long on cross-above).
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class MATrendEntryStrategy(BaseStrategy):
    name = "MA_Trend_Entry"
    category = "trend"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "slow_period": [50, 100],
        "stop_atr": [1.5, 2.0],
        "hold_bars": [6, 12],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"slow_period": 50, "stop_atr": 1.5, "hold_bars": 6}

    def generate_signals(self, data):
        slow = self.params["slow_period"]
        close = data["close"]
        sma = close.rolling(slow, min_periods=slow).mean()
        sma_rising = sma > sma.shift(1)
        # cross above SMA with SMA rising → long
        cross_above = (close > sma) & (close.shift(1) <= sma.shift(1))
        # cross below SMA with SMA falling → short
        cross_below = (close < sma) & (close.shift(1) >= sma.shift(1))
        signals = pd.Series(0, index=data.index)
        signals[cross_above & sma_rising & sma.notna()] = 1
        signals[cross_below & ~sma_rising & sma.notna()] = -1
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
                # RTH gate — skip pre/after-hours entries
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
