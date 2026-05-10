"""
OPEX Week Effect (C3)
======================
THESIS: Monthly options expiration week (3rd Friday) exhibits a mild
        bullish drift. Mechanism: options dealers are net long gamma
        heading into expiry, requiring net buying to delta-hedge as
        prices drift upward. Also: window-dressing by institutions
        ahead of expiry.

SIGNAL:
  - Long at close of N trading days before OPEX Friday.
  - Exit at close of OPEX Friday (or Thursday = day before).

OPEX FRIDAY: 3rd Friday of each month.
  If 3rd Friday is a market holiday, use preceding Thursday.

VARIANTS (3):
  - entry_offset=4: enter Monday of OPEX week (4 days before Friday)
  - entry_offset=3: enter Tuesday of OPEX week
  - entry_offset=2: enter Wednesday of OPEX week
  exit is always OPEX Friday close (or last bar of that day).

METHOD: One-shot IS/OOS. No walk-forward.
  IS: 2010-2018. OOS: 2019-2024.

ACADEMIC BASIS:
  Ni, S.X., Pearson, N.D., Poteshman, A.M. (2005). "Stock price
  clustering and price pinning." Journal of Financial Economics.
  Stivers, C., Sun, L. (2013). "Market cycles and the performance
  of relative strength strategies."

NOTE: This is a LONG-ONLY strategy. Short OPEX trades (expecting a
      drop) are theoretically possible but empirically weaker.
      We test long-only here.
"""

from typing import Any, Dict, List, Optional
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


# ── OPEX date generation ───────────────────────────────────────────
def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    # Find first Friday
    first_day = date(year, month, 1)
    # Weekday: Monday=0, Friday=4
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    third_friday = first_friday + timedelta(weeks=2)
    return third_friday


def _generate_opex_dates(start_year: int = 2010, end_year: int = 2026) -> list:
    """Generate all monthly OPEX Fridays."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            d = _third_friday(year, month)
            if d <= date(end_year, 12, 31):
                dates.append(d)
    return dates


OPEX_DATES = _generate_opex_dates()


class OPEXWeekStrategy(BaseStrategy):
    """
    Long during OPEX week. Entry N trading days before OPEX Friday.
    Exit at OPEX Friday close.
    """

    name = "OPEX_Week"
    description = "Long during monthly options expiration week"
    category = "calendar"
    timeframe = "5min"
    version = "1.0"

    # entry_offset: how many trading days before OPEX Friday to enter
    # 4 = Monday, 3 = Tuesday, 2 = Wednesday of OPEX week
    param_grid = {"entry_offset": [2, 3, 4]}

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"entry_offset": 3}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Not used — this strategy builds trades directly."""
        return pd.Series(0, index=data.index)

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        """
        For each OPEX month:
          1. Find OPEX Friday in data.
          2. Walk backward entry_offset trading days to find entry day.
          3. Enter at last bar of entry day (close).
          4. Exit at last bar of OPEX Friday (close).
        """
        entry_offset = self.params["entry_offset"]
        trades = []

        if data.index.tz is None:
            raise ValueError("Data index must be timezone-aware (US/Eastern).")

        # Get all unique trading dates in this dataset
        trading_dates = sorted(set(data.index.date))
        trading_dates_set = set(trading_dates)

        for opex_friday in OPEX_DATES:
            # Check OPEX Friday is a trading day (skip holidays)
            # If not, back up to the nearest prior trading day
            exit_date = opex_friday
            attempts = 0
            while exit_date not in trading_dates_set and attempts < 5:
                exit_date = exit_date - timedelta(days=1)
                attempts += 1

            if exit_date not in trading_dates_set:
                continue

            # Find entry date: entry_offset trading days before exit_date
            exit_pos = trading_dates.index(exit_date)
            entry_pos = exit_pos - entry_offset
            if entry_pos < 0:
                continue

            entry_date = trading_dates[entry_pos]

            # Get last bar of entry day (close)
            entry_day_bars = data[data.index.date == entry_date]
            if entry_day_bars.empty:
                continue

            # Get last bar of exit day (close)
            exit_day_bars = data[data.index.date == exit_date]
            if exit_day_bars.empty:
                continue

            entry_bar = entry_day_bars.iloc[-1]
            exit_bar = exit_day_bars.iloc[-1]

            entry_price = float(entry_bar["close"])
            exit_price = float(exit_bar["close"])
            entry_time = entry_bar.name
            exit_time = exit_bar.name

            # Skip if entry and exit are the same bar
            if entry_time >= exit_time:
                continue

            gross_pnl = exit_price - entry_price  # long only

            trades.append({
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "direction": 1,
                "exit_type": "opex_close",
                "gross_pnl": gross_pnl,
                "opex_friday": str(opex_friday),
                "entry_date": str(entry_date),
            })

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df