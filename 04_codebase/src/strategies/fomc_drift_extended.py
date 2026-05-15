"""
FOMC Drift Extended — Multi-Market (Batch 6)
Applies the pre-FOMC drift to non-equity instruments (GC, CL, M6E).
Direction is a parameter: long (1) or short (-1) before FOMC.
The IS period selects whichever direction was profitable in-sample.

Uses the same FOMC date list as fomc_drift.py.
ONE_SHOT_IS_OOS — too few events per year for WFO.
"""
from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np
from src.strategies.base import BaseStrategy
from src.strategies.fomc_drift import FOMC_DATES


class FOMCDriftExtendedStrategy(BaseStrategy):
    name = "FOMC_Drift_Extended"
    category = "calendar"
    timeframe = "5min"
    version = "1.0"
    max_trades_per_day = 1
    param_grid = {
        "direction":   [1, -1],       # 1=long, -1=short into FOMC
        "exit_offset": [0, 30, 60],   # minutes before 14:00 ET on FOMC day
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"direction": 1, "exit_offset": 0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return pd.Series(0, index=data.index)

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        direction   = self.params["direction"]
        exit_offset = self.params["exit_offset"]
        trades = []

        if data.index.tz is None:
            raise ValueError("Data index must be timezone-aware US/Eastern")

        for fomc_date in FOMC_DATES:
            # Entry: last bar of the previous trading day
            prev_date = fomc_date - pd.Timedelta(days=1)
            same_date = data[data.index.date == prev_date.date()]
            if same_date.empty:
                # Try two days before (weekend)
                prev_date2 = fomc_date - pd.Timedelta(days=2)
                same_date = data[data.index.date == prev_date2.date()]
                if same_date.empty:
                    continue

            entry_bar   = same_date.iloc[-1]
            entry_price = entry_bar["close"]
            entry_time  = entry_bar.name

            # Exit: first bar at or after (14:00 - exit_offset) on FOMC day
            exit_target = (fomc_date.normalize()
                           + pd.Timedelta(hours=14)
                           - pd.Timedelta(minutes=exit_offset))
            after_mask = data.index >= exit_target
            if not after_mask.any():
                continue
            exit_bar   = data[after_mask].iloc[0]
            exit_price = exit_bar["close"]
            exit_time  = exit_bar.name

            gross_pnl = (exit_price - entry_price) * direction
            trades.append({
                "entry_time":  entry_time,
                "entry_price": entry_price,
                "exit_time":   exit_time,
                "exit_price":  exit_price,
                "direction":   direction,
                "exit_type":   "fomc",
                "gross_pnl":   gross_pnl,
            })

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
