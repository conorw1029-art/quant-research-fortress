"""
Standard Pivot Point Reaction
================================
THESIS: Daily floor pivots (P, R1, R2, S1, S2) are widely watched. Price
        often reacts at these levels. We test the FADE behavior at R1/S1
        — buy at S1 with confirmation, sell at R1 with confirmation.

PIVOT FORMULA (standard):
  P  = (PrevHigh + PrevLow + PrevClose) / 3
  R1 = 2*P - PrevLow
  S1 = 2*P - PrevHigh
  R2 = P + (PrevHigh - PrevLow)
  S2 = P - (PrevHigh - PrevLow)

SIGNAL:
  - Long at S1: low touches S1, then close moves back above S1 (rejection)
  - Short at R1: high touches R1, then close moves back below R1 (rejection)
  - Use S2/R2 variants too

EXIT:
  - Target: pivot point P (or 1.0 * ATR, whichever closer)
  - Stop: 1.0 * ATR
  - Timeout: 24 bars (2h)

PARAM GRID: 2 x 2 = 4 combos.
  level: ["s1_r1", "s2_r2"]
  target_atr: [1.0, 1.5]
"""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy
import src.data.data_schema as S


class PivotReactionStrategy(BaseStrategy):

    name = "Pivot_Reaction"
    description = "Reaction at standard daily pivot points S1/R1 or S2/R2"
    category = "level_reaction"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 2

    param_grid = {
        "level": ["s1_r1", "s2_r2"],
        "target_atr": [1.0, 1.5],
    }

    STOP_ATR = 1.0
    TIMEOUT_BARS = 24

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"level": "s1_r1", "target_atr": 1.0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if S.SESSION_DATE not in data.columns:
            return pd.Series(0, index=data.index)

        # Compute prior day OHLC
        daily_h = data.groupby(S.SESSION_DATE)["high"].max()
        daily_l = data.groupby(S.SESSION_DATE)["low"].min()
        daily_c = data.groupby(S.SESSION_DATE)["close"].last()

        sessions = sorted(data[S.SESSION_DATE].unique())

        # Map each session -> previous session's pivots
        p_map, r1_map, s1_map, r2_map, s2_map = {}, {}, {}, {}, {}
        for i in range(1, len(sessions)):
            prev = sessions[i - 1]
            curr = sessions[i]
            ph, pl, pc = daily_h[prev], daily_l[prev], daily_c[prev]
            p = (ph + pl + pc) / 3
            r1 = 2 * p - pl
            s1 = 2 * p - ph
            r2 = p + (ph - pl)
            s2 = p - (ph - pl)
            p_map[curr] = p
            r1_map[curr] = r1
            s1_map[curr] = s1
            r2_map[curr] = r2
            s2_map[curr] = s2

        sd = data[S.SESSION_DATE]
        pivot = sd.map(p_map)
        r1 = sd.map(r1_map); s1 = sd.map(s1_map)
        r2 = sd.map(r2_map); s2 = sd.map(s2_map)

        if self.params["level"] == "s1_r1":
            sup, res = s1, r1
        else:
            sup, res = s2, r2

        signals = pd.Series(0, index=data.index)

        # Long at support: bar's low touched support AND close stayed above
        long_cond = (
            (data["low"] <= sup) &
            (data["close"] > sup) &
            sup.notna()
        )
        # Short at resistance: bar's high touched resistance AND close stayed below
        short_cond = (
            (data["high"] >= res) &
            (data["close"] < res) &
            res.notna()
        )

        signals[long_cond] = 1
        signals[short_cond] = -1

        # Store pivot for use as target
        data["_pivot"] = pivot

        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        target_mult = self.params["target_atr"]
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

                target_pts = target_mult * atr_val
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


class PivotReactionNQStrategy(PivotReactionStrategy):
    name = "Pivot_Reaction_NQ"
    description = "Pivot reaction on NQ"