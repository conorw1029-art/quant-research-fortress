"""
RSI Mean-Reversion Strategy (H5b) — original loop version.
Reverted from vectorized due to off-by-one bug in vectorized exits.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class RSIMeanRevStrategy(BaseStrategy):
    name = "RSI_MeanRev"
    category = "mean_reversion"
    timeframe = "5min"

    param_grid = {
        "oversold": [25, 30],
        "target_atr": [0.75, 1.0],
    }

    OVERBOUGHT = 75
    STOP_ATR = 1.5
    TIMEOUT_BARS = 12

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"oversold": 25, "target_atr": 1.0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi = data["rsi"]
        oversold = self.params["oversold"]
        signals = pd.Series(0, index=data.index)
        if len(rsi) < 2:
            return signals
        signals[(rsi.shift(1) >= oversold) & (rsi < oversold)] = 1
        signals[(rsi.shift(1) <= self.OVERBOUGHT) & (rsi > self.OVERBOUGHT)] = -1
        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        target_mult = self.params["target_atr"]
        stop_mult = self.STOP_ATR
        timeout_bars = min(self.TIMEOUT_BARS, max_bars_per_trade)
        trades = []
        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue
                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time = entry_bar.name
                direction = 1 if signals[idx] == 1 else -1
                atr_val = data["atr"].loc[idx]
                if np.isnan(atr_val) or atr_val == 0:
                    continue
                target_pts = target_mult * atr_val
                stop_pts = stop_mult * atr_val
                target_price = entry_price + direction * target_pts
                stop_loss = entry_price - direction * stop_pts
                exit_price = None
                exit_time = None
                exit_type = "timeout"
                for i in range(1, timeout_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar = data.iloc[entry_loc + 1 + i]
                    if direction == 1:
                        if bar["low"] <= stop_loss:
                            exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                        if bar["high"] >= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
                    else:
                        if bar["high"] >= stop_loss:
                            exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                        if bar["low"] <= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
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

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df