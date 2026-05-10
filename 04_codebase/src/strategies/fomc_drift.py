"""
FOMC Pre‑Announcement Drift Strategy (H6) – wrapped for fortress.
Entry: long at prior session close (16:00 bar day before FOMC).
Exit: 14:00 ET on announcement day minus exit_offset minutes.
"""
from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np
from src.strategies.base import BaseStrategy

# FOMC announcement dates (2010–2025) – Eastern Time
FOMC_DATES = pd.to_datetime([
    "2010-01-27", "2010-03-16", "2010-04-28", "2010-06-23", "2010-08-10",
    "2010-09-21", "2010-11-03", "2010-12-14",
    "2011-01-26", "2011-03-15", "2011-04-27", "2011-06-22", "2011-08-09",
    "2011-09-21", "2011-11-02", "2011-12-13",
    "2012-01-25", "2012-03-13", "2012-04-25", "2012-06-20", "2012-08-01",
    "2012-09-13", "2012-10-24", "2012-12-12",
    "2013-01-30", "2013-03-20", "2013-05-01", "2013-06-19", "2013-07-31",
    "2013-09-18", "2013-10-30", "2013-12-18",
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18", "2014-07-30",
    "2014-09-17", "2014-10-29", "2014-12-17",
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29",
    "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27",
    "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26",
    "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01",
    "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31",
    "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10",
    "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28",
    "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26",
    "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-11-12", "2025-12-17"
]).tz_localize("US/Eastern")

class FOMCDriftStrategy(BaseStrategy):
    name = "FOMC_Drift"
    param_grid = {
        "exit_offset": [0, 30, 60],        # minutes before 14:00
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        if params is not None:
            self.params = params
        else:
            self.params = {"exit_offset": 0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """FOMC drift uses calendar-based trades; no technical signal needed."""
        return pd.Series(0, index=data.index)

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series, max_bars_per_trade: int = 78) -> List[Dict]:
        """
        Builds a long trade for each FOMC announcement:
        Entry at close of the last bar of the previous session.
        Exit at first bar at or after 14:00 ET minus exit_offset on announcement day.
        """
        exit_offset = self.params["exit_offset"]
        trades = []

        if data.index.tz is None:
            raise ValueError("Data index must be timezone‑aware US/Eastern")

        for fomc_date in FOMC_DATES:
            # Previous day: find the last available bar (the 16:00 RTH close)
            prev_date = fomc_date - pd.Timedelta(days=1)
            same_date = data[data.index.date == prev_date.date()]
            if same_date.empty:
                continue
            entry_bar = same_date.iloc[-1]          # last bar of that day
            entry_price = entry_bar["close"]
            entry_time = entry_bar.name

            # Exit: first bar at or after 14:00 - offset
            exit_time_target = fomc_date.normalize() + pd.Timedelta(hours=14) - pd.Timedelta(minutes=exit_offset)
            after_mask = data.index >= exit_time_target
            if not after_mask.any():
                continue
            exit_bar = data[after_mask].iloc[0]
            exit_price = exit_bar["close"]
            exit_time = exit_bar.name

            gross_pnl = exit_price - entry_price   # long
            trades.append({
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "direction": 1,
                "exit_type": "fomc",
                "gross_pnl": gross_pnl,
            })

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df