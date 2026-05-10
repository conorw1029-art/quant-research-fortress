"""
Round Number Magnetism
========================
THESIS: Price exhibits two well-documented behaviors near round numbers:
        (a) Magnetic attraction — price tends to drift TO the round number
        (b) Reaction at the level — round numbers act as support/resistance
        
        We test the REACTION/REVERSAL behavior: when price reaches a round
        number after a strong move, it tends to stall and reverse short-term.

SIGNAL:
  - Identify round number levels: every X points (e.g., 25, 50, 100)
  - Long when price drops to round number after extended downmove
  - Short when price rises to round number after extended upmove
  - "Extended move" = N bars in the direction without a touch of the level

EXIT:
  - Target: 0.5 * ATR (small bounce)
  - Stop: 0.75 * ATR (close stop — failed bounces continue)
  - Timeout: 12 bars (60 min)

PARAM GRID: 2 x 2 = 4 combos.
  round_step: [25, 50]      # which round numbers to trigger on
  approach_bars: [10, 20]   # how many bars of approach define "extended"
"""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy
import src.data.data_schema as S


class RoundNumberStrategy(BaseStrategy):

    name = "Round_Number_Reaction"
    description = "Mean-reversion at round number levels after extended approach"
    category = "level_reaction"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 2

    param_grid = {
        "round_step": [25, 50],
        "approach_bars": [10, 20],
    }

    TARGET_ATR = 0.5
    STOP_ATR = 0.75
    TIMEOUT_BARS = 12
    PROXIMITY_PCT = 0.001  # within 0.1% of round level counts as "at level"

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"round_step": 25, "approach_bars": 10}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        step = self.params["round_step"]
        approach = self.params["approach_bars"]

        close = data["close"]
        high = data["high"]
        low = data["low"]

        # Find nearest round number to each bar
        nearest_round = (close / step).round() * step
        distance = close - nearest_round
        proximity_threshold = close * self.PROXIMITY_PCT

        # At-level mask
        at_level = np.abs(distance) <= proximity_threshold

        # Approach detection: did price move toward this level for N bars?
        # For long: close has been falling (closes lower than N bars ago)
        # For short: close has been rising
        approach_long = close < close.shift(approach)
        approach_short = close > close.shift(approach)

        # Avoid being at the same level for many bars (only fire on fresh approaches)
        # Use crossing into the proximity zone
        was_outside = (~at_level).shift(1).fillna(True)
        fresh_arrival = at_level & was_outside

        signals = pd.Series(0, index=data.index)

        long_cond = fresh_arrival & approach_long & (low <= nearest_round)
        short_cond = fresh_arrival & approach_short & (high >= nearest_round)

        signals[long_cond] = 1
        signals[short_cond] = -1

        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        timeout = min(self.TIMEOUT_BARS, max_bars_per_trade)
        trades = []
        sessions_count: Dict[Any, int] = {}

        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                bar = data.iloc[entry_loc]
                session = bar.get(S.SESSION_DATE)
                if sessions_count.get(session, 0) >= self.max_trades_per_day:
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                entry_price = float(entry_bar["open"])
                entry_time = entry_bar.name
                direction = int(signals[idx])
                atr_val = float(data["atr"].iloc[entry_loc])
                if np.isnan(atr_val) or atr_val <= 0:
                    continue

                target_pts = self.TARGET_ATR * atr_val
                stop_pts = self.STOP_ATR * atr_val
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
                sessions_count[session] = sessions_count.get(session, 0) + 1
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


class RoundNumberNQStrategy(RoundNumberStrategy):
    name = "Round_Number_Reaction_NQ"
    description = "Round number reaction on NQ"
    # NQ uses larger round steps
    param_grid = {
        "round_step": [50, 100],
        "approach_bars": [10, 20],
    }