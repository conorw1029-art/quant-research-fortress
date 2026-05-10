"""
Holiday Effect (C4)
====================
THESIS: US equity markets exhibit a persistent pre-holiday bullish bias.
        Mechanism: reduced institutional selling pressure, short-covering
        by traders who don't want overnight risk over holidays, and
        historically positive sentiment around holidays.

SIGNAL:
  - Long at close of day before US holiday.
  - Exit at close of first trading day after the holiday.

HOLIDAYS TESTED (liquid markets, well-documented effect):
  - New Year's Day (Jan 1)
  - Martin Luther King Jr. Day (3rd Monday Jan)
  - Presidents Day (3rd Monday Feb)
  - Good Friday (variable — market closed)
  - Memorial Day (last Monday May)
  - Independence Day (Jul 4)
  - Labor Day (1st Monday Sep)
  - Thanksgiving (4th Thursday Nov)
  - Christmas (Dec 25)

VARIANTS (3):
  - holiday_set="major": only the 4 highest-volume holidays
    (Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas)
  - holiday_set="all": all 9 holidays
  - holiday_set="year_end": Christmas + New Year only (strongest documented effect)

METHOD: One-shot IS/OOS. No walk-forward.
  IS: 2010-2018. OOS: 2019-2024.

ACADEMIC BASIS:
  Ariel, R.A. (1990). "High Stock Returns before Holidays: Existence
  and Evidence on Possible Causes." Journal of Finance.
  Kim, C.W., Park, J. (1994). "Holiday effects and stock returns."
  Journal of Financial and Quantitative Analysis.
"""

from typing import Any, Dict, List, Optional
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


# ── Holiday date generation ────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of weekday (0=Mon) in month/year."""
    first = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in month/year."""
    # Find the last day of month
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def _good_friday(year: int) -> date:
    """Calculate Good Friday (Friday before Easter Sunday)."""
    # Butcher's algorithm for Easter
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return easter - timedelta(days=2)


def _generate_holidays(start_year: int = 2010, end_year: int = 2026) -> Dict[str, List[date]]:
    """Generate holiday dates by category."""
    major = []      # The big 5 (strongest historical effect)
    all_hols = []   # All 9
    year_end = []   # Christmas + New Year

    for year in range(start_year, end_year + 1):
        # New Year's Day (Jan 1, adjusted for weekends)
        ny = date(year, 1, 1)
        if ny.weekday() == 5: ny = date(year, 1, 3)   # Sat -> Mon
        if ny.weekday() == 6: ny = date(year, 1, 2)   # Sun -> Mon
        all_hols.append(ny)

        # MLK Day (3rd Monday Jan)
        mlk = _nth_weekday(year, 1, 0, 3)
        all_hols.append(mlk)

        # Presidents Day (3rd Monday Feb)
        pres = _nth_weekday(year, 2, 0, 3)
        all_hols.append(pres)

        # Good Friday
        gf = _good_friday(year)
        all_hols.append(gf)

        # Memorial Day (last Monday May)
        mem = _last_weekday(year, 5, 0)
        all_hols.append(mem)
        major.append(mem)

        # Independence Day (Jul 4, adjusted)
        ind = date(year, 7, 4)
        if ind.weekday() == 5: ind = date(year, 7, 3)  # Sat -> Fri
        if ind.weekday() == 6: ind = date(year, 7, 5)  # Sun -> Mon
        all_hols.append(ind)
        major.append(ind)

        # Labor Day (1st Monday Sep)
        lab = _nth_weekday(year, 9, 0, 1)
        all_hols.append(lab)
        major.append(lab)

        # Thanksgiving (4th Thursday Nov)
        tg = _nth_weekday(year, 11, 3, 4)
        all_hols.append(tg)
        major.append(tg)

        # Christmas (Dec 25, adjusted)
        xmas = date(year, 12, 25)
        if xmas.weekday() == 5: xmas = date(year, 12, 24)  # Sat -> Fri
        if xmas.weekday() == 6: xmas = date(year, 12, 26)  # Sun -> Mon
        all_hols.append(xmas)
        major.append(xmas)
        year_end.append(xmas)

        # New Year's Eve (Dec 31) — sometimes included in year_end effect
        nye = date(year, 12, 31)
        if nye.weekday() not in (5, 6):  # skip if weekend
            year_end.append(nye)

    return {
        "major": sorted(set(major)),
        "all": sorted(set(all_hols)),
        "year_end": sorted(set(year_end)),
    }


HOLIDAY_SETS = _generate_holidays()


class HolidayEffectStrategy(BaseStrategy):
    """
    Long at close before US holiday, exit at close after return.
    """

    name = "Holiday_Effect"
    description = "Pre-holiday bullish drift in ES futures"
    category = "calendar"
    timeframe = "5min"
    version = "1.0"

    param_grid = {"holiday_set": ["major", "all", "year_end"]}

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"holiday_set": "major"}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return pd.Series(0, index=data.index)

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        holiday_set_name = self.params["holiday_set"]
        holidays = set(HOLIDAY_SETS[holiday_set_name])
        trades = []

        if data.index.tz is None:
            raise ValueError("Data index must be timezone-aware (US/Eastern).")

        trading_dates = sorted(set(data.index.date))
        trading_dates_set = set(trading_dates)

        for i, tdate in enumerate(trading_dates):
            # Is tomorrow a holiday?
            next_day = tdate + timedelta(days=1)
            # Account for multi-day weekends: check next 3 calendar days
            is_pre_holiday = False
            holiday_date = None
            for offset in range(1, 5):
                candidate = tdate + timedelta(days=offset)
                if candidate in holidays:
                    is_pre_holiday = True
                    holiday_date = candidate
                    break
                if candidate in trading_dates_set:
                    break  # hit the next trading day before a holiday

            if not is_pre_holiday:
                continue

            # Find first trading day after the holiday
            post_holiday = holiday_date + timedelta(days=1)
            attempts = 0
            while post_holiday not in trading_dates_set and attempts < 7:
                post_holiday = post_holiday + timedelta(days=1)
                attempts += 1

            if post_holiday not in trading_dates_set:
                continue

            # Entry: close of pre-holiday trading day
            entry_bars = data[data.index.date == tdate]
            if entry_bars.empty:
                continue

            # Exit: close of first post-holiday trading day
            exit_bars = data[data.index.date == post_holiday]
            if exit_bars.empty:
                continue

            entry_bar = entry_bars.iloc[-1]
            exit_bar = exit_bars.iloc[-1]

            entry_price = float(entry_bar["close"])
            exit_price = float(exit_bar["close"])
            entry_time = entry_bar.name
            exit_time = exit_bar.name

            if entry_time >= exit_time:
                continue

            gross_pnl = exit_price - entry_price

            trades.append({
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "direction": 1,
                "exit_type": "post_holiday_close",
                "gross_pnl": gross_pnl,
                "holiday": str(holiday_date),
            })

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df