"""
Bondarenko-Muravyev Overnight Drift
====================================
Replicates "Market Return Around the Clock: A Puzzle" (JFQA 2023).

The paper documents that ~100% of average S&P 500 futures return is earned
in the 4-hour window around European market open: roughly 23:30 ET to 03:30 ET.
Reported Sharpe: 1.6 after transaction costs.

Mechanism: European traders waking up resolve uncertainty accumulated overnight.
Inventory rebalancing by dealers (Boyarchenko-Larsen-Whelan 2023) compensates
liquidity providers via positive overnight returns.

Strategy:
  - Enter long at 23:30 ET (when European traders begin trading)
  - Exit at 03:30 ET
  - Trade every weekday (excluding Friday→Monday weekend gap)

This is the cleanest possible replication. No optimization — the entry/exit
times are pre-registered from the academic paper.

Variants tested:
  - 23:30-03:30 ET (paper's primary window)
  - 24:00-03:00 ET (Boyarchenko-Larsen-Whelan window)
  - 02:00-03:00 ET (1-hour pure European-open window)
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import datetime as dt
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class BondarenkoOvernightDriftStrategy(BaseStrategy):
    """
    Long-only overnight drift on equity index futures.

    Parameters
    ----------
    entry_hour, entry_minute : int
        Entry time in US/Eastern.
    exit_hour, exit_minute : int
        Exit time in US/Eastern (next day if exit < entry hour).
    """
    name = "Bondarenko_Overnight_Drift"
    category = "calendar"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "window": [
            (23, 30, 3, 30),
            (0, 0, 3, 0),
            (2, 0, 3, 0),
        ],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"window": (23, 30, 3, 30)}

    def _get_window(self):
        eh, em, xh, xm = self.params["window"]
        return dt.time(eh, em), dt.time(xh, xm)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        entry_t, exit_t = self._get_window()
        signals = pd.Series(0, index=data.index, dtype=int)

        if not isinstance(data.index, pd.DatetimeIndex):
            return signals

        bar_times = data.index.time
        bar_dates = data.index.date

        is_entry_time = pd.Series(
            [(t == entry_t) for t in bar_times],
            index=data.index,
        )

        if not is_entry_time.any():
            target_minutes = entry_t.hour * 60 + entry_t.minute
            bar_minutes = np.array([t.hour * 60 + t.minute for t in bar_times])

            df_aux = pd.DataFrame({
                "minutes": bar_minutes,
                "date": bar_dates,
            }, index=data.index)
            df_aux["is_entry"] = (df_aux["minutes"] >= target_minutes)
            df_aux["entry_flag"] = (
                df_aux["is_entry"]
                & ~df_aux.groupby("date")["is_entry"].shift(1).fillna(False).astype(bool)
            )
            signals[df_aux["entry_flag"]] = 1
        else:
            signals[is_entry_time] = 1

        if isinstance(data.index, pd.DatetimeIndex):
            day_of_week = data.index.dayofweek
            if entry_t.hour >= 18:
                friday_mask = (day_of_week == 4)
            else:
                friday_mask = day_of_week.isin([5, 6])
            signals[friday_mask] = 0

        return signals

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 480,
    ) -> List[Dict[str, Any]]:
        entry_t, exit_t = self._get_window()
        trades: List[Dict[str, Any]] = []

        active_signals = signals[signals != 0]

        for entry_idx in active_signals.index:
            try:
                entry_loc = data.index.get_loc(entry_idx)
                if entry_loc + 1 >= len(data):
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time = entry_bar.name

                if (exit_t.hour * 60 + exit_t.minute) <= (entry_t.hour * 60 + entry_t.minute):
                    target_date = (entry_time + pd.Timedelta(days=1)).date()
                else:
                    target_date = entry_time.date()

                target_minutes = exit_t.hour * 60 + exit_t.minute

                exit_loc = None
                for j in range(entry_loc + 2, min(entry_loc + max_bars_per_trade, len(data))):
                    bar = data.index[j]
                    bar_min = bar.hour * 60 + bar.minute
                    if bar.date() == target_date and bar_min >= target_minutes:
                        exit_loc = j
                        break
                    if bar.date() > target_date:
                        exit_loc = j
                        break

                if exit_loc is None:
                    continue

                exit_bar = data.iloc[exit_loc]
                exit_price = exit_bar["open"]
                exit_time = exit_bar.name
                direction = 1

                gross_pnl = (exit_price - entry_price) * direction

                trades.append({
                    "entry_time":  entry_time,
                    "entry_price": entry_price,
                    "exit_time":   exit_time,
                    "exit_price":  exit_price,
                    "direction":   direction,
                    "exit_type":   "time_window",
                    "gross_pnl":   gross_pnl,
                })
            except Exception:
                continue

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
