"""
Overnight Session High/Low Fade
=================================
THESIS: Overnight ranges (Globex session before RTH open) are heavily watched.
        When RTH price approaches the overnight high or low, traders fade
        these levels because they're "obvious" and stop-running often
        results in reversal.

Note: We have RTH-only data, so we use the prior day's RTH range as a proxy
      for the "overnight" reference range. This is a reasonable approximation
      since the overnight session typically extends but doesn't drastically
      exceed the prior RTH range without major news.

SIGNAL:
  - Long fade: low touches prior_low (range support), close back above
  - Short fade: high touches prior_high (range resistance), close back below

The KEY DIFFERENCE from prior_day_breakout: this is the FADE/REJECTION
play, not the breakout. We test the opposite hypothesis on the same level.

EXIT:
  - Target: midpoint of prior range (mean reversion target)
  - Stop: 0.75 * ATR
  - Timeout: 18 bars (90 min)

PARAM GRID: 2 x 2 = 4 combos.
  rejection_strength: [0.25, 0.5]   # require close >= rejection_strength * range from extreme
  target_type: ["midpoint", "atr"]   # target = range midpoint or 1.0 * ATR
"""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy
import src.data.data_schema as S


class OvernightFadeStrategy(BaseStrategy):

    name = "Overnight_Fade"
    description = "Fade prior session H/L on rejection in current session"
    category = "level_reaction"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "rejection_strength": [0.25, 0.5],
        "target_type": ["midpoint", "atr"],
    }

    STOP_ATR = 0.75
    TIMEOUT_BARS = 18

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"rejection_strength": 0.25, "target_type": "midpoint"}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if S.SESSION_DATE not in data.columns:
            return pd.Series(0, index=data.index)

        daily_h = data.groupby(S.SESSION_DATE)["high"].max()
        daily_l = data.groupby(S.SESSION_DATE)["low"].min()

        sessions = sorted(data[S.SESSION_DATE].unique())
        prev_h_map, prev_l_map, prev_mid_map = {}, {}, {}
        for i in range(1, len(sessions)):
            prev = sessions[i - 1]
            curr = sessions[i]
            prev_h_map[curr] = daily_h[prev]
            prev_l_map[curr] = daily_l[prev]
            prev_mid_map[curr] = (daily_h[prev] + daily_l[prev]) / 2

        sd = data[S.SESSION_DATE]
        prev_h = sd.map(prev_h_map)
        prev_l = sd.map(prev_l_map)
        prev_mid = sd.map(prev_mid_map)
        prev_range = prev_h - prev_l

        rej_strength = self.params["rejection_strength"]

        # Long fade at prior low: low <= prev_l, close >= prev_l + rej_strength * range
        long_cond = (
            (data["low"] <= prev_l) &
            (data["close"] >= prev_l + rej_strength * prev_range) &
            prev_l.notna()
        )
        # Short fade at prior high
        short_cond = (
            (data["high"] >= prev_h) &
            (data["close"] <= prev_h - rej_strength * prev_range) &
            prev_h.notna()
        )

        signals = pd.Series(0, index=data.index)
        signals[long_cond] = 1
        signals[short_cond] = -1

        # Store for target use
        data["_prev_mid"] = prev_mid
        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        target_type = self.params["target_type"]
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

                stop_pts = self.STOP_ATR * atr_val
                stop_loss = entry_price - direction * stop_pts

                # Target
                if target_type == "midpoint":
                    prev_mid = data["_prev_mid"].iloc[entry_loc] if "_prev_mid" in data.columns else None
                    if prev_mid is None or np.isnan(prev_mid):
                        target_price = entry_price + direction * 1.0 * atr_val
                    else:
                        target_price = float(prev_mid)
                        # Make sure target is in the right direction
                        if direction == 1 and target_price <= entry_price:
                            target_price = entry_price + 1.0 * atr_val
                        elif direction == -1 and target_price >= entry_price:
                            target_price = entry_price - 1.0 * atr_val
                else:
                    target_price = entry_price + direction * 1.0 * atr_val

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


class OvernightFadeNQStrategy(OvernightFadeStrategy):
    name = "Overnight_Fade_NQ"
    description = "Overnight fade on NQ"