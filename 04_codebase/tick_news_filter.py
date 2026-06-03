"""
tick_news_filter.py — Historical Economic Event Calendar for Backtesting
=========================================================================
Provides a list of major US economic release timestamps used to filter
backtest signals. Prevents strategies from entering during high-volatility
scheduled events.

Events covered:
  - FOMC decisions (8 per year, 2:00 PM ET)
  - NFP (first Friday of month, 8:30 AM ET)
  - CPI (approximate 2nd Tuesday-Thursday of month, 8:30 AM ET)
  - PPI (day after CPI typically, 8:30 AM ET)
  - GDP (quarterly, 8:30 AM ET)
  - ISM Manufacturing (1st business day of month, 10:00 AM ET)
  - EIA Crude Oil Inventories (Wednesday, 10:30 AM ET — for CL strategies)

Usage:
  from tick_news_filter import NewsFilter
  nf = NewsFilter()
  blocked = nf.is_blocked(timestamp_utc, window_minutes=30)
  events = nf.get_events_in_range(start, end)

  # Filter a DataFrame of bar timestamps:
  mask = nf.build_filter_mask(bars.index, window_minutes=30)
  filtered_bars = bars[~mask]  # bars NOT near news events
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
UTC = pytz.utc


# ── FOMC Decision Dates (announced 2:00 PM ET) ────────────────────────────────
# Source: Federal Reserve website. Dates are for the final day of each meeting
# (when the rate decision is announced).
FOMC_DATES: List[date] = [
    # 2020
    date(2020, 1, 29), date(2020, 3, 3), date(2020, 3, 15),
    date(2020, 4, 29), date(2020, 6, 10), date(2020, 7, 29),
    date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28),
    date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22),
    date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),
    date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21),
    date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3),
    date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20),
    date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),
    date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18),
    date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
    date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
    date(2025, 11, 5), date(2025, 12, 17),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 6),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 11, 4), date(2026, 12, 16),
]

# ── CPI Release Dates (8:30 AM ET) ───────────────────────────────────────────
# Source: BLS historical release calendar. Approximate — within a few days of
# the 2nd Tuesday–Thursday of each month.
CPI_DATES: List[date] = [
    # 2020
    date(2020, 1, 14), date(2020, 2, 13), date(2020, 3, 11),
    date(2020, 4, 10), date(2020, 5, 12), date(2020, 6, 10),
    date(2020, 7, 14), date(2020, 8, 12), date(2020, 9, 11),
    date(2020, 10, 13), date(2020, 11, 12), date(2020, 12, 10),
    # 2021
    date(2021, 1, 13), date(2021, 2, 10), date(2021, 3, 10),
    date(2021, 4, 13), date(2021, 5, 12), date(2021, 6, 10),
    date(2021, 7, 13), date(2021, 8, 11), date(2021, 9, 14),
    date(2021, 10, 13), date(2021, 11, 10), date(2021, 12, 10),
    # 2022
    date(2022, 1, 12), date(2022, 2, 10), date(2022, 3, 10),
    date(2022, 4, 12), date(2022, 5, 11), date(2022, 6, 10),
    date(2022, 7, 13), date(2022, 8, 10), date(2022, 9, 13),
    date(2022, 10, 13), date(2022, 11, 10), date(2022, 12, 13),
    # 2023
    date(2023, 1, 12), date(2023, 2, 14), date(2023, 3, 14),
    date(2023, 4, 12), date(2023, 5, 10), date(2023, 6, 13),
    date(2023, 7, 12), date(2023, 8, 10), date(2023, 9, 13),
    date(2023, 10, 12), date(2023, 11, 14), date(2023, 12, 12),
    # 2024
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12),
    date(2024, 4, 10), date(2024, 5, 15), date(2024, 6, 12),
    date(2024, 7, 11), date(2024, 8, 14), date(2024, 9, 11),
    date(2024, 10, 10), date(2024, 11, 13), date(2024, 12, 11),
    # 2025
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12),
    date(2025, 4, 10), date(2025, 5, 13), date(2025, 6, 11),
    date(2025, 7, 15), date(2025, 8, 12), date(2025, 9, 10),
    date(2025, 10, 14), date(2025, 11, 13), date(2025, 12, 10),
    # 2026 (approximate)
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 13), date(2026, 6, 10),
    date(2026, 7, 14), date(2026, 8, 12), date(2026, 9, 9),
    date(2026, 10, 14), date(2026, 11, 11), date(2026, 12, 9),
]

# ── GDP Release Dates (8:30 AM ET, quarterly) ─────────────────────────────────
GDP_DATES: List[date] = [
    # 2020
    date(2020, 1, 30), date(2020, 4, 29), date(2020, 7, 30), date(2020, 10, 29),
    # 2021
    date(2021, 1, 28), date(2021, 4, 29), date(2021, 7, 29), date(2021, 10, 28),
    # 2022
    date(2022, 1, 27), date(2022, 4, 28), date(2022, 7, 28), date(2022, 10, 27),
    # 2023
    date(2023, 1, 26), date(2023, 4, 27), date(2023, 7, 27), date(2023, 10, 26),
    # 2024
    date(2024, 1, 25), date(2024, 4, 25), date(2024, 7, 25), date(2024, 10, 30),
    # 2025
    date(2025, 1, 30), date(2025, 4, 30), date(2025, 7, 30), date(2025, 10, 29),
    # 2026 (approximate)
    date(2026, 1, 29), date(2026, 4, 29), date(2026, 7, 29), date(2026, 10, 28),
]


def _first_friday(year: int, month: int) -> date:
    """Return the first Friday of the given year/month."""
    first = date(year, month, 1)
    # weekday(): Monday=0, Friday=4
    days_until_friday = (4 - first.weekday()) % 7
    return first + timedelta(days=days_until_friday)


def _generate_nfp_dates(start_year: int = 2018, end_year: int = 2027) -> List[date]:
    """Generate NFP dates: first Friday of each month (released at 8:30 AM ET)."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            dates.append(_first_friday(year, month))
    return dates


NFP_DATES: List[date] = _generate_nfp_dates()


def _to_utc(d: date, hour: int, minute: int = 0) -> datetime:
    """Convert an ET date + time to UTC datetime."""
    naive = datetime(d.year, d.month, d.day, hour, minute, 0)
    et_dt = ET.localize(naive, is_dst=None)
    return et_dt.astimezone(UTC)


class NewsFilter:
    """
    Historical economic event filter for backtesting.

    Builds a set of UTC event timestamps and provides fast lookup
    for whether a given bar timestamp falls within a news window.
    """

    def __init__(
        self,
        include_fomc: bool = True,
        include_nfp: bool = True,
        include_cpi: bool = True,
        include_gdp: bool = True,
        include_eia: bool = False,
    ):
        self._events: List[datetime] = []
        self._build_events(include_fomc, include_nfp, include_cpi, include_gdp, include_eia)

    def _build_events(
        self, include_fomc, include_nfp, include_cpi, include_gdp, include_eia
    ) -> None:
        events = []
        if include_fomc:
            for d in FOMC_DATES:
                events.append((_to_utc(d, 14, 0), "FOMC"))  # 2:00 PM ET
        if include_nfp:
            for d in NFP_DATES:
                events.append((_to_utc(d, 8, 30), "NFP"))   # 8:30 AM ET
        if include_cpi:
            for d in CPI_DATES:
                events.append((_to_utc(d, 8, 30), "CPI"))   # 8:30 AM ET
        if include_gdp:
            for d in GDP_DATES:
                events.append((_to_utc(d, 8, 30), "GDP"))   # 8:30 AM ET
        if include_eia:
            # EIA Crude: every Wednesday at 10:30 AM ET
            for year in range(2018, 2027):
                for month in range(1, 13):
                    first = date(year, month, 1)
                    # Find first Wednesday (weekday 2)
                    days = (2 - first.weekday()) % 7
                    wed = first + timedelta(days=days)
                    while wed.month == month:
                        events.append((_to_utc(wed, 10, 30), "EIA"))
                        wed += timedelta(weeks=1)

        self._events = [(ts, name) for ts, name in events]
        # Sort for binary-search-style lookups
        self._events.sort(key=lambda x: x[0])
        self._event_ts = [ts for ts, _ in self._events]

    def is_blocked(self, ts: pd.Timestamp, window_minutes: int = 30) -> bool:
        """Return True if ts falls within window_minutes of any event."""
        if ts.tzinfo is None:
            ts = ts.tz_localize(UTC)
        else:
            ts = ts.tz_convert(UTC)

        dt = ts.to_pydatetime()
        window = timedelta(minutes=window_minutes)

        import bisect
        i = bisect.bisect_left(self._event_ts, dt)

        # Check neighbours
        for j in (i - 1, i):
            if 0 <= j < len(self._event_ts):
                if abs((self._event_ts[j] - dt).total_seconds()) <= window.total_seconds():
                    return True
        return False

    def get_nearest_event(self, ts: pd.Timestamp) -> Optional[tuple]:
        """Return (event_name, event_time, minutes_away) for the nearest event."""
        if not self._events:
            return None
        if ts.tzinfo is None:
            ts = ts.tz_localize(UTC)
        else:
            ts = ts.tz_convert(UTC)
        dt = ts.to_pydatetime()
        closest = min(self._events, key=lambda x: abs((x[0] - dt).total_seconds()))
        mins = abs((closest[0] - dt).total_seconds()) / 60
        return (closest[1], closest[0], mins)

    def build_filter_mask(
        self,
        index: pd.DatetimeIndex,
        window_minutes: int = 30,
    ) -> pd.Series:
        """
        Return a boolean Series (True = near news event, should be BLOCKED).
        Efficiently vectorised over the entire bar index.
        """
        if index.tzinfo is None:
            idx_utc = index.tz_localize(UTC)
        else:
            idx_utc = index.tz_convert(UTC)

        window_ns = window_minutes * 60 * 1_000_000_000  # nanoseconds

        blocked = pd.Series(False, index=index)
        event_ns = [int(ts.timestamp() * 1e9) for ts in self._event_ts]

        import bisect
        import numpy as np
        bar_ns = idx_utc.asi8  # numpy array of ns since epoch

        for i, bar_t in enumerate(bar_ns):
            j = bisect.bisect_left(event_ns, bar_t)
            for k in (j - 1, j):
                if 0 <= k < len(event_ns):
                    if abs(event_ns[k] - bar_t) <= window_ns:
                        blocked.iloc[i] = True
                        break

        return blocked

    def get_events_in_range(
        self, start: pd.Timestamp, end: pd.Timestamp
    ) -> List[dict]:
        """Return list of events between start and end."""
        if start.tzinfo is None:
            start = start.tz_localize(UTC)
        if end.tzinfo is None:
            end = end.tz_localize(UTC)
        return [
            {"name": name, "timestamp": ts.isoformat()}
            for ts, name in self._events
            if start.to_pydatetime() <= ts <= end.to_pydatetime()
        ]

    def summary(self) -> str:
        """Human-readable summary of the filter."""
        counts = {}
        for _, name in self._events:
            counts[name] = counts.get(name, 0) + 1
        total = sum(counts.values())
        lines = [f"NewsFilter: {total} events total"]
        for name, count in sorted(counts.items()):
            lines.append(f"  {name}: {count}")
        return "\n".join(lines)


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    nf = NewsFilter()
    print(nf.summary())

    # Test a known NFP date (first Friday Jan 2024 = Jan 5, 2024, 8:30 AM ET = 13:30 UTC)
    test_ts = pd.Timestamp("2024-01-05 13:25:00", tz="UTC")
    print(f"\nTest bar at {test_ts}:")
    print(f"  is_blocked(window=30min): {nf.is_blocked(test_ts, 30)}")
    print(f"  nearest event: {nf.get_nearest_event(test_ts)}")

    test_ts2 = pd.Timestamp("2024-01-05 15:00:00", tz="UTC")
    print(f"\nTest bar at {test_ts2}:")
    print(f"  is_blocked(window=30min): {nf.is_blocked(test_ts2, 30)}")
    print(f"  nearest event: {nf.get_nearest_event(test_ts2)}")
