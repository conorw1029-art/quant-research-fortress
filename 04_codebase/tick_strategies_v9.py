"""
tick_strategies_v9.py — Calendar-driven strategies (event-based signals)
=========================================================================
These strategies fire based on scheduled macro events rather than technical
indicators. Signal is +1 during the holding window, 0 otherwise.

V9 strategies:
  fomc_drift  — Pre-FOMC upward drift on MES (ES index). Enter at close of
                FOMC-eve bar (≈21:00 UTC), exit ≈18:00 UTC on announcement day.
                Documented phenomenon: S&P drifts upward in ~22h before Fed decision.
                Phase 4 backtest: 57 trades/12 years, 66.7% WR, DSR=1.627.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

# ── FOMC Announcement Dates (Eastern Time) ────────────────────────────────────
# Entry: FOMC-eve bar 20:00-22:00 UTC (16:00-18:00 ET)
# Exit:  FOMC day 17:00-19:00 UTC (≈ 14:00 ET announcement ± 1h)
# Direction: always LONG (pre-FOMC drift is reliably bullish)

_FOMC_ANNOUNCEMENT_DATES = pd.to_datetime([
    # 2010–2025 (from Phase 4 backtest)
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
    # 2026 (Fed schedules released annually; update if dates shift)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]).tz_localize("UTC")

# Build a lookup set: (year, month, day) of announcement days and eve days
_FOMC_ANNOUNCEMENT_SET: set[tuple] = set()
_FOMC_EVE_SET:          set[tuple] = set()

for _d in _FOMC_ANNOUNCEMENT_DATES:
    _FOMC_ANNOUNCEMENT_SET.add((_d.year, _d.month, _d.day))
    _eve = _d - pd.Timedelta(days=1)
    _FOMC_EVE_SET.add((_eve.year, _eve.month, _eve.day))


def _fomc_signal_series(bars: pd.DataFrame) -> pd.Series:
    """
    Return +1 for bars in the FOMC holding window, 0 elsewhere.

    Holding window: from 20:00 UTC on FOMC-eve through 19:00 UTC on
    announcement day (≈covers 14:00 ET announcement and 1h buffer).
    """
    idx_utc = bars.index.tz_convert("UTC") if bars.index.tzinfo else bars.index
    sig = np.zeros(len(bars), dtype=int)

    for i, ts in enumerate(idx_utc):
        key = (ts.year, ts.month, ts.day)
        h   = ts.hour
        # Eve window: 20:00-23:59 UTC on FOMC eve
        if key in _FOMC_EVE_SET and h >= 20:
            sig[i] = 1
        # Announcement-day window: 00:00-19:00 UTC on FOMC day
        elif key in _FOMC_ANNOUNCEMENT_SET and h <= 19:
            sig[i] = 1

    return pd.Series(sig, index=bars.index)


def fomc_drift(bars: pd.DataFrame, **params) -> pd.Series:
    """
    FOMC pre-announcement drift on MES/ES.
    Always long. No parameters needed.
    """
    return _fomc_signal_series(bars)


# ── Strategy map ─────────────────────────────────────────────────────────────

STRAT_MAP_V9: dict[str, dict] = {
    "fomc_drift": {
        "compute": fomc_drift,
        "description": "Pre-FOMC drift — long MES from eve close to announcement",
        "instruments": ["ES"],
        "default_params": {},
    },
}
