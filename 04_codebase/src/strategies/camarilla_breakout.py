"""
Camarilla Pivot Breakout
==========================
THESIS: Camarilla pivots are computed differently from standard pivots
        and have specific behavioral interpretations:
        - H3/L3: most likely range — fade these
        - H4/L4: breakout zone — when price breaks H4 or L4 with momentum,
                 it tends to trend strongly

We test the H4/L4 BREAKOUT thesis (the high-probability one according
to Camarilla theory).

CAMARILLA FORMULAS:
  Range = PrevHigh - PrevLow
  H4 = PrevClose + Range * 1.1/2
  L4 = PrevClose - Range * 1.1/2
  H3 = PrevClose + Range * 1.1/4
  L3 = PrevClose - Range * 1.1/4

SIGNAL:
  - Long: close > H4 (breakout above 4th resistance)
  - Short: close < L4 (breakout below 4th support)

EXIT:
  - Target: target_atr * ATR
  - Stop: 1.0 * ATR
  - Timeout: 24 bars

PARAM GRID: 2 x 2 = 4 combos.
  use_close_filter: [True, False]   # require close confirmation vs intrabar
  target_atr: [1.5, 2.0]
"""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy
import src.data.data_schema as S


class CamarillaBreakoutStrategy(BaseStrategy):

    name = "Camarilla_Breakout"
    description = "H4/L4 Camarilla pivot breakouts (trend continuation thesis)"
    category = "level_breakout"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "use_close_filter": [True, False],
        "target_atr": [1.5, 2.0],
    }

    STOP_ATR = 1.0
    TIMEOUT_BARS = 24

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"use_close_filter": True, "target_atr": 1.5}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if S.SESSION_DATE not in data.columns:
            return pd.Series(0, index=data.index)

        daily_h = data.groupby(S.SESSION_DATE)["high"].max()
        daily_l = data.groupby(S.SESSION_DATE)["low"].min()
        daily_c = data.groupby(S.SESSION_DATE)["close"].last()

        sessions = sorted(data[S.SESSION_DATE].unique())
        h4_map, l4_map = {}, {}
        for i in range(1, len(sessions)):
            prev = sessions[i - 1]
            curr = sessions[i]
            r = daily_h[prev] - daily_l[prev]
            pc = daily_c[prev]
            h4_map[curr] = pc + r * 1.1 / 2
            l4_map[curr] = pc - r * 1.1 / 2

        sd = data[S.SESSION_DATE]
        h4 = sd.map(h4_map); l4 = sd.map(l4_map)

        signals = pd.Series(0, index=data.index)

        if self.params["use_close_filter"]:
            long_cond = (data["close"] > h4) & h4.notna()
            short_cond = (data["close"] < l4) & l4.notna()
        else:
            long_cond = (data["high"] > h4) & h4.notna()
            short_cond = (data["low"] < l4) & l4.notna()

        signals[long_cond] = 1
        signals[short_cond] = -1
        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        target_mult = self.params["target_atr"]
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


class CamarillaBreakoutNQStrategy(CamarillaBreakoutStrategy):
    name = "Camarilla_Breakout_NQ"
    description = "Camarilla breakout on NQ"