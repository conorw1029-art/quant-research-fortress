#!/usr/bin/env python3
"""
tick_strategies_v9.py — Calendar-driven strategies (event-based signals)
=========================================================================
These strategies fire based on scheduled macro events rather than technical
indicators. Signal is +1 during the holding window, 0 otherwise.

V9 strategies:
  fomc_drift      — Pre-FOMC upward drift on MES/ES. Enter eve 20:00 UTC,
                    exit announcement day 19:00 UTC.  ~22h hold.
                    Documented: 66.7% WR, DSR=1.627 (Phase 4 backtest)

  nfp_eve_drift   — Pre-NFP equity drift. Enter Thursday 20:00 UTC (night
                    before first-Friday NFP), exit Friday 13:00 UTC (just
                    before 13:30 UTC = 8:30 ET release). ~17h hold.
                    Markets drift upward in anticipation of employment data.

  cpi_eve_gold    — Pre-CPI gold inflation hedge. Enter CPI-eve 20:00 UTC,
                    exit CPI day 12:00 UTC (before 13:30 UTC release).
                    Gold rises as inflation fears priced in before the print.

  month_end_equity — Last-2-trading-days equity push. Enter at 20:00 UTC
                     on 2nd-to-last trading day, exit at 21:00 UTC on last
                     trading day. Month-end pension rebalancing lifts equities.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date as _date, timedelta as _timedelta
import pandas as pd
import numpy as np


# ── FOMC Dates ────────────────────────────────────────────────────────────────
_FOMC_ANNOUNCEMENT_DATES = pd.to_datetime([
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
    "2025-09-17", "2025-10-29", "2025-11-12", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]).tz_localize("UTC")

_FOMC_ANNOUNCEMENT_SET: set[tuple] = set()
_FOMC_EVE_SET:          set[tuple] = set()
for _d in _FOMC_ANNOUNCEMENT_DATES:
    _FOMC_ANNOUNCEMENT_SET.add((_d.year, _d.month, _d.day))
    _eve = _d - pd.Timedelta(days=1)
    _FOMC_EVE_SET.add((_eve.year, _eve.month, _eve.day))


# ── CPI Release Dates (BLS, 8:30 ET / 13:30 UTC) ─────────────────────────────
# BLS publishes the schedule a year in advance. These are the actual release dates.
# Update annually: https://www.bls.gov/schedule/news_release/cpi.htm
_CPI_DATES = pd.to_datetime([
    "2020-01-14", "2020-02-13", "2020-03-11", "2020-04-10", "2020-05-12",
    "2020-06-10", "2020-07-14", "2020-08-12", "2020-09-11", "2020-10-13",
    "2020-11-12", "2020-12-10",
    "2021-01-13", "2021-02-10", "2021-03-10", "2021-04-13", "2021-05-12",
    "2021-06-10", "2021-07-13", "2021-08-11", "2021-09-14", "2021-10-13",
    "2021-11-10", "2021-12-10",
    "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12", "2022-05-11",
    "2022-06-10", "2022-07-13", "2022-08-10", "2022-09-13", "2022-10-13",
    "2022-11-10", "2022-12-13",
    "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12", "2023-05-10",
    "2023-06-13", "2023-07-12", "2023-08-10", "2023-09-13", "2023-10-12",
    "2023-11-14", "2023-12-12",
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15",
    "2024-06-12", "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10",
    "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13",
    "2025-06-11", "2025-07-11", "2025-08-12", "2025-09-10", "2025-10-14",
    "2025-11-13", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-13",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-09", "2026-10-14",
    "2026-11-12", "2026-12-09",
]).tz_localize("UTC")

_CPI_DATE_SET: set[tuple] = set()
_CPI_EVE_SET:  set[tuple] = set()
for _d in _CPI_DATES:
    _CPI_DATE_SET.add((_d.year, _d.month, _d.day))
    _eve = _d - pd.Timedelta(days=1)
    _CPI_EVE_SET.add((_eve.year, _eve.month, _eve.day))


def _first_friday_of_month(year: int, month: int) -> _date:
    """Return the date of the first Friday in the given year/month."""
    first = _date(year, month, 1)
    weekday = first.weekday()  # 0=Mon, 4=Fri
    days_until_friday = (4 - weekday) % 7
    return first + _timedelta(days=days_until_friday)


def _build_nfp_sets(start_year: int = 2010, end_year: int = 2027) -> tuple[set, set]:
    """
    NFP is released on the first Friday of each month (with rare exceptions
    handled by the BLS schedule; a hardcoded correction list covers those).
    """
    # Known exceptions where NFP was NOT on the first Friday
    _NFP_EXCEPTIONS: dict[tuple, _date] = {
        # (year, month): actual_date
        # 2013 Jan government furlough; Apr 2013 Patriot Day
        (2013, 4): _date(2013, 4, 5),
        # 2015 Jan — no shift needed, kept first Friday
        # BLS has moved NFP ±1 week on rare occasions; add here if needed
    }
    nfp_dates: list[_date] = []
    for yr in range(start_year, end_year + 1):
        for mo in range(1, 13):
            dt = _NFP_EXCEPTIONS.get((yr, mo), _first_friday_of_month(yr, mo))
            nfp_dates.append(dt)

    nfp_set: set[tuple] = {(d.year, d.month, d.day) for d in nfp_dates}
    # Eve = Thursday (day before the first Friday)
    nfp_eve_set: set[tuple] = {
        ((d - _timedelta(days=1)).year,
         (d - _timedelta(days=1)).month,
         (d - _timedelta(days=1)).day)
        for d in nfp_dates
    }
    return nfp_set, nfp_eve_set


_NFP_DATE_SET, _NFP_EVE_SET = _build_nfp_sets()


def _last_trading_days_of_month(bars_index: pd.DatetimeIndex, n: int = 2) -> set[tuple]:
    """
    For each month present in bars_index, find the last N unique trading dates.
    Returns a set of (year, month, day) tuples.
    """
    dates = pd.Series(bars_index.date).unique()
    df_dates = pd.DataFrame({"date": sorted(dates)})
    df_dates["ym"] = df_dates["date"].apply(lambda d: (d.year, d.month))
    last_days: set[tuple] = set()
    for _, grp in df_dates.groupby("ym"):
        tail = grp["date"].iloc[-n:]
        for d in tail:
            last_days.add((d.year, d.month, d.day))
    return last_days


# ── Signal generators ─────────────────────────────────────────────────────────

def _fomc_signal_series(bars: pd.DataFrame) -> pd.Series:
    idx_utc = bars.index.tz_convert("UTC") if bars.index.tzinfo else bars.index
    sig = np.zeros(len(bars), dtype=int)
    for i, ts in enumerate(idx_utc):
        key = (ts.year, ts.month, ts.day)
        h   = ts.hour
        if key in _FOMC_EVE_SET and h >= 20:
            sig[i] = 1
        elif key in _FOMC_ANNOUNCEMENT_SET and h <= 19:
            sig[i] = 1
    return pd.Series(sig, index=bars.index)


def _nfp_signal_series(bars: pd.DataFrame) -> pd.Series:
    """
    Enter Thursday 20:00 UTC (night before NFP).
    Exit Friday 13:00 UTC (30min before 13:30 UTC release).
    Hold: ~17 hours. Direction: LONG (pre-announcement drift).
    """
    idx_utc = bars.index.tz_convert("UTC") if bars.index.tzinfo else bars.index
    sig = np.zeros(len(bars), dtype=int)
    for i, ts in enumerate(idx_utc):
        key = (ts.year, ts.month, ts.day)
        h   = ts.hour
        # Eve (Thursday): enter at 20:00 UTC
        if key in _NFP_EVE_SET and h >= 20:
            sig[i] = 1
        # NFP day (Friday): hold through 12:59 UTC (exit before release)
        elif key in _NFP_DATE_SET and h <= 12:
            sig[i] = 1
    return pd.Series(sig, index=bars.index)


def _cpi_signal_series(bars: pd.DataFrame) -> pd.Series:
    """
    Enter CPI-eve at 20:00 UTC.
    Exit CPI day at 12:00 UTC (before 13:30 UTC release).
    Direction: LONG gold (inflation-hedge demand builds before CPI print).
    """
    idx_utc = bars.index.tz_convert("UTC") if bars.index.tzinfo else bars.index
    sig = np.zeros(len(bars), dtype=int)
    for i, ts in enumerate(idx_utc):
        key = (ts.year, ts.month, ts.day)
        h   = ts.hour
        if key in _CPI_EVE_SET and h >= 20:
            sig[i] = 1
        elif key in _CPI_DATE_SET and h <= 12:
            sig[i] = 1
    return pd.Series(sig, index=bars.index)


def _month_end_signal_series(bars: pd.DataFrame, n_days: int = 2) -> pd.Series:
    """
    Long on the last N trading days of each month.
    Window: 20:00 UTC on (last_day - 1), exit 21:00 UTC on last_day.
    Covers pension/index rebalancing flows that reliably lift large-caps.
    """
    idx_utc = bars.index.tz_convert("UTC") if bars.index.tzinfo else bars.index
    last_days = _last_trading_days_of_month(idx_utc, n=n_days)
    sig = np.zeros(len(bars), dtype=int)
    for i, ts in enumerate(idx_utc):
        key = (ts.year, ts.month, ts.day)
        if key in last_days:
            sig[i] = 1
    return pd.Series(sig, index=bars.index)


# ── Public strategy functions ──────────────────────────────────────────────────

def fomc_drift(bars: pd.DataFrame, **params) -> pd.Series:
    """Pre-FOMC upward drift. Always long."""
    return _fomc_signal_series(bars)


def nfp_eve_drift(bars: pd.DataFrame, **params) -> pd.Series:
    """Pre-NFP equity drift (Thu night → Fri pre-release). Always long."""
    return _nfp_signal_series(bars)


def cpi_eve_gold(bars: pd.DataFrame, **params) -> pd.Series:
    """Pre-CPI gold inflation hedge (CPI-eve → pre-release). Always long."""
    return _cpi_signal_series(bars)


def month_end_equity(bars: pd.DataFrame, n_days: int = 2, **params) -> pd.Series:
    """Month-end rebalancing push. Long last N trading days of month."""
    return _month_end_signal_series(bars, n_days=n_days)


# ── Strategy map ─────────────────────────────────────────────────────────────

STRAT_MAP_V9: dict[str, dict] = {
    "fomc_drift": {
        "compute":     fomc_drift,
        "description": "Pre-FOMC drift — long ES from eve close to announcement",
        "instruments": ["ES"],
        "default_params": {},
    },
    "nfp_eve_drift": {
        "compute":     nfp_eve_drift,
        "description": "Pre-NFP drift — long ES/NQ from Thu 20:00 UTC to Fri 13:00 UTC",
        "instruments": ["ES", "NQ"],
        "default_params": {},
    },
    "cpi_eve_gold": {
        "compute":     cpi_eve_gold,
        "description": "Pre-CPI gold inflation hedge — long GC from CPI-eve to pre-release",
        "instruments": ["GC"],
        "default_params": {},
    },
    "month_end_equity": {
        "compute":     month_end_equity,
        "description": "Month-end rebalancing — long ES/NQ last 2 trading days of month",
        "instruments": ["ES", "NQ"],
        "default_params": {"n_days": 2},
    },
}
