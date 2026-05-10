"""
Prior Day High/Low Breakout
=============================
THESIS: Yesterday's high and low are watched levels. A clean break
        with momentum tends to continue in the breakout direction
        as stops get triggered and traders chase.

SIGNAL:
  - Long:  Price closes above prior day high (with buffer)
  - Short: Price closes below prior day low (with buffer)
  - Buffer prevents triggering on minor pierces
  - First signal per day only (avoid clustering)

EXIT:
  - Target: target_atr * ATR
  - Stop: 1.0 * ATR (close stop — breakouts that fail tend to reverse hard)
  - Timeout: 24 bars (2 hours on 5-min)

PARAM GRID: 2 x 2 = 4 combos.
  buffer_atr: [0.05, 0.10]   # buffer as fraction of ATR above/below level
  target_atr: [1.0, 1.5]
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy
import src.data.data_schema as S


class PriorDayBreakoutStrategy(BaseStrategy):

    name = "Prior_Day_Breakout"
    description = "Breakout above prior day high / below prior day low"
    category = "level_breakout"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "buffer_atr": [0.05, 0.10],
        "target_atr": [1.0, 1.5],
    }

    STOP_ATR = 1.0
    TIMEOUT_BARS = 24  # 2 hours on 5-min

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"buffer_atr": 0.10, "target_atr": 1.5}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        Compute prior day's high/low (causal — uses previous session only).
        Signal fires when current close breaks the level + buffer.
        """
        if S.SESSION_DATE not in data.columns:
            return pd.Series(0, index=data.index)

        # Compute daily HL — using groupby on session_date
        daily_high = data.groupby(S.SESSION_DATE)["high"].max()
        daily_low = data.groupby(S.SESSION_DATE)["low"].min()

        # Map to bar level: each bar gets *yesterday's* HL
        sessions_sorted = sorted(data[S.SESSION_DATE].unique())
        prior_high_map = {}
        prior_low_map = {}
        for i in range(1, len(sessions_sorted)):
            prior_high_map[sessions_sorted[i]] = daily_high[sessions_sorted[i - 1]]
            prior_low_map[sessions_sorted[i]] = daily_low[sessions_sorted[i - 1]]

        prior_high = data[S.SESSION_DATE].map(prior_high_map)
        prior_low = data[S.SESSION_DATE].map(prior_low_map)

        # ATR-based buffer
        atr = data["atr"]
        buffer = self.params["buffer_atr"] * atr

        signals = pd.Series(0, index=data.index)

        long_cond = (
            (data["close"] > prior_high + buffer) &
            prior_high.notna() & atr.notna()
        )
        short_cond = (
            (data["close"] < prior_low - buffer) &
            prior_low.notna() & atr.notna()
        )

        signals[long_cond] = 1
        signals[short_cond] = -1

        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        target_mult = self.params["target_atr"]
        stop_mult = self.STOP_ATR
        timeout = min(self.TIMEOUT_BARS, max_bars_per_trade)
        trades = []
        sessions_traded = set()

        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                bar = data.iloc[entry_loc]
                session = bar.get(S.SESSION_DATE)
                if session in sessions_traded:
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                entry_price = float(entry_bar["open"])
                entry_time = entry_bar.name
                direction = int(signals[idx])
                atr_val = float(data["atr"].iloc[entry_loc])
                if np.isnan(atr_val) or atr_val <= 0:
                    continue

                target_pts = target_mult * atr_val
                stop_pts = stop_mult * atr_val
                target_price = entry_price + direction * target_pts
                stop_loss = entry_price - direction * stop_pts

                exit_price = None; exit_time = None; exit_type = "timeout"
                for i in range(1, timeout + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    fb = data.iloc[entry_loc + 1 + i]
                    if direction == 1:
                        if fb["low"] <= stop_loss:
                            exit_price = stop_loss; exit_time = fb.name; exit_type = "stop"; break
                        if fb["high"] >= target_price:
                            exit_price = target_price; exit_time = fb.name; exit_type = "target"; break
                    else:
                        if fb["high"] >= stop_loss:
                            exit_price = stop_loss; exit_time = fb.name; exit_type = "stop"; break
                        if fb["low"] <= target_price:
                            exit_price = target_price; exit_time = fb.name; exit_type = "target"; break

                if exit_price is None:
                    eidx = entry_loc + 1 + timeout
                    if eidx < len(data):
                        exit_bar = data.iloc[eidx]
                        exit_price = float(exit_bar["close"])
                        exit_time = exit_bar.name
                    else:
                        continue

                gross_pnl = direction * (exit_price - entry_price)
                trades.append({
                    "entry_time": entry_time, "entry_price": entry_price,
                    "exit_time": exit_time, "exit_price": exit_price,
                    "direction": direction, "exit_type": exit_type,
                    "gross_pnl": gross_pnl,
                })
                sessions_traded.add(session)
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


class PriorDayBreakoutNQStrategy(PriorDayBreakoutStrategy):
    name = "Prior_Day_Breakout_NQ"
    description = "Prior day breakout on NQ"