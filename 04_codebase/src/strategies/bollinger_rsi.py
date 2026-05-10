"""
Bollinger Band + RSI Mean Reversion (A1) — original loop version.
Reverted from vectorized due to off-by-one bug in vectorized exits.
"""
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy

class BollingerRSIStrategy(BaseStrategy):
    name = "Bollinger_RSI"
    category = "mean_reversion"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 2
    param_grid = {"bb_period": [20, 50], "rsi_extreme": [25, 30]}
    BB_STD = 2.0
    STOP_ATR = 1.5
    TARGET_ATR = 1.0
    TIMEOUT_BARS = 12

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"bb_period": 20, "rsi_extreme": 30}

    def generate_signals(self, data):
        bb_period = self.params["bb_period"]
        rsi_extreme = self.params["rsi_extreme"]
        rsi_high = 100 - rsi_extreme
        close = data["close"]
        rsi = data["rsi"] if "rsi" in data.columns else pd.Series(50.0, index=data.index)
        bb_mid = close.rolling(bb_period, min_periods=bb_period).mean()
        bb_std = close.rolling(bb_period, min_periods=bb_period).std()
        bb_upper = bb_mid + self.BB_STD * bb_std
        bb_lower = bb_mid - self.BB_STD * bb_std
        signals = pd.Series(0, index=data.index)
        signals[(data["low"] <= bb_lower) & (rsi < rsi_extreme) & bb_lower.notna()] = 1
        signals[(data["high"] >= bb_upper) & (rsi > rsi_high) & bb_upper.notna()] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        timeout_bars = min(self.TIMEOUT_BARS, max_bars_per_trade)
        # Recompute BB middle for dynamic targets
        bb_period = self.params["bb_period"]
        bb_mid = data["close"].rolling(bb_period, min_periods=bb_period).mean()
        trades = []
        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue
                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time = entry_bar.name
                direction = int(signals[idx])
                atr_val = data["atr"].loc[idx]
                if np.isnan(atr_val) or atr_val == 0:
                    continue
                stop_pts = self.STOP_ATR * atr_val
                stop_loss = entry_price - direction * stop_pts
                exit_price = None
                exit_time = None
                exit_type = "timeout"
                for i in range(1, timeout_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar = data.iloc[entry_loc + 1 + i]
                    current_mid = bb_mid.iloc[entry_loc + 1 + i]
                    if direction == 1:
                        if bar["low"] <= stop_loss:
                            exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                    else:
                        if bar["high"] >= stop_loss:
                            exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                    if not np.isnan(current_mid):
                        if direction == 1 and bar["high"] >= current_mid:
                            exit_price = current_mid; exit_time = bar.name; exit_type = "target_bb_mid"; break
                        elif direction == -1 and bar["low"] <= current_mid:
                            exit_price = current_mid; exit_time = bar.name; exit_type = "target_bb_mid"; break
                if exit_price is None:
                    exit_index = entry_loc + 1 + timeout_bars
                    if exit_index < len(data):
                        exit_bar = data.iloc[exit_index]
                        exit_price = exit_bar["close"]
                        exit_time = exit_bar.name
                        exit_type = "timeout"
                    else:
                        continue
                gross_pnl = (exit_price - entry_price) * direction
                trades.append({
                    "entry_time": entry_time, "entry_price": entry_price,
                    "exit_time": exit_time, "exit_price": exit_price,
                    "direction": direction, "exit_type": exit_type, "gross_pnl": gross_pnl,
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