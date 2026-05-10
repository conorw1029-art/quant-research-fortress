"""
Calendar Event Strategy — Generic Framework
=============================================
Reusable base for ALL calendar/event-driven strategies.
Subclasses only need to supply a date list and entry/exit times.

Tested events: FOMC (SURVIVOR on ES, ZN), NFP, CPI, ECB, BOJ,
Fed Minutes, EIA Inventory, USDA Crop Reports.

Architecture:
  CalendarEventStrategy(BaseStrategy)
    ├── FOMCDriftStrategy        (already exists, keep as-is)
    ├── NFPDriftStrategy         (new)
    ├── CPIDriftStrategy         (new)
    ├── ECBDriftStrategy         (new)
    ├── BOJDriftStrategy         (new)
    ├── FedMinutesDriftStrategy  (new)
    ├── EIAInventoryStrategy     (new)
    └── USDAReportStrategy       (new)

Each subclass is <20 lines. The base class handles all entry/exit logic.
"""

from abc import abstractmethod
from datetime import time, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy, Trade


class CalendarEventStrategy(BaseStrategy):
    """
    Generic long-only calendar event strategy.

    Subclasses must define:
      - EVENT_DATES: list of "YYYY-MM-DD" strings
      - EVENT_NAME: human-readable name
      - DEFAULT_ENTRY_TYPE: "prior_1550" or "day_0935"
      - DEFAULT_EXIT_TIME: "1415" or "1555" (HHMM ET)
    """

    name = "CalendarEvent"
    category = "calendar"
    timeframe = "5min"
    min_holding_bars = 1
    max_trades_per_day = 1

    # Subclass MUST override these
    EVENT_DATES: List[str] = []
    EVENT_NAME: str = "generic_event"
    DEFAULT_ENTRY_TYPE: str = "prior_1550"
    DEFAULT_EXIT_TIME: str = "1415"

    param_grid = {
        "entry_type": ["prior_1550", "day_0935"],
        "exit_time": ["1415", "1555"],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "entry_type": self.DEFAULT_ENTRY_TYPE,
            "exit_time": self.DEFAULT_EXIT_TIME,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Not used — calendar strategies use custom signals_to_trades."""
        return pd.Series(0, index=data.index)

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series) -> List[Trade]:
        """
        Calendar-based trade generation.
        Long-only on event dates. Entry/exit controlled by params.
        """
        entry_type = self.params.get("entry_type", self.DEFAULT_ENTRY_TYPE)
        exit_time_str = self.params.get("exit_time", self.DEFAULT_EXIT_TIME)
        exit_t = time(int(exit_time_str[:2]), int(exit_time_str[2:]))

        # Build date lookup
        all_dates = sorted(set(data.index.date))
        date_to_idx = {d: i for i, d in enumerate(all_dates)}

        trades = []
        for event_date_str in self.EVENT_DATES:
            event_date = pd.to_datetime(event_date_str).date()

            # --- ENTRY ---
            if entry_type == "prior_1550":
                # Enter at prior day's 15:50 close
                idx = date_to_idx.get(event_date)
                if idx is None or idx == 0:
                    continue
                prior_day = all_dates[idx - 1]
                prior_bars = data[data.index.date == prior_day]
                entry_bars = prior_bars[prior_bars.index.time >= time(15, 50)]
                if len(entry_bars) == 0:
                    continue
                entry_price = entry_bars.iloc[-1]["close"]
                entry_ts = entry_bars.index[-1]

            elif entry_type == "day_0935":
                # Enter at event day 09:35 open
                day_bars = data[data.index.date == event_date]
                entry_bars = day_bars[day_bars.index.time >= time(9, 35)]
                if len(entry_bars) == 0:
                    continue
                entry_price = entry_bars.iloc[0]["open"]
                entry_ts = entry_bars.index[0]
            else:
                continue

            # --- EXIT ---
            day_bars = data[data.index.date == event_date]
            exit_bars = day_bars[day_bars.index.time >= exit_t]
            if len(exit_bars) == 0:
                if len(day_bars) == 0:
                    continue
                exit_price = day_bars.iloc[-1]["close"]
                exit_ts = day_bars.index[-1]
            else:
                exit_price = exit_bars.iloc[0]["close"]
                exit_ts = exit_bars.index[0]

            pnl = exit_price - entry_price  # long only, gross

            trades.append(Trade(
                entry_time=entry_ts,
                exit_time=exit_ts,
                entry_price=entry_price,
                exit_price=exit_price,
                direction=1,
                gross_pnl=pnl,
                exit_reason="event_window",
                bars_held=max(1, len(day_bars[
                    (day_bars.index >= entry_ts) & (day_bars.index <= exit_ts)
                ])),
                session_date=event_date,
            ))

        return trades


# ======================================================================
# SUBCLASS: NFP Drift
# ======================================================================

# Non-Farm Payrolls release dates (08:30 ET, first Friday of month)
# Source: Bureau of Labor Statistics release schedule
NFP_DATES = [
    # 2010
    "2010-01-08", "2010-02-05", "2010-03-05", "2010-04-02",
    "2010-05-07", "2010-06-04", "2010-07-02", "2010-08-06",
    "2010-09-03", "2010-10-08", "2010-11-05", "2010-12-03",
    # 2011
    "2011-01-07", "2011-02-04", "2011-03-04", "2011-04-01",
    "2011-05-06", "2011-06-03", "2011-07-08", "2011-08-05",
    "2011-09-02", "2011-10-07", "2011-11-04", "2011-12-02",
    # 2012
    "2012-01-06", "2012-02-03", "2012-03-09", "2012-04-06",
    "2012-05-04", "2012-06-01", "2012-07-06", "2012-08-03",
    "2012-09-07", "2012-10-05", "2012-11-02", "2012-12-07",
    # 2013
    "2013-01-04", "2013-02-01", "2013-03-08", "2013-04-05",
    "2013-05-03", "2013-06-07", "2013-07-05", "2013-08-02",
    "2013-09-06", "2013-10-22", "2013-11-08", "2013-12-06",
    # 2014
    "2014-01-10", "2014-02-07", "2014-03-07", "2014-04-04",
    "2014-05-02", "2014-06-06", "2014-07-03", "2014-08-01",
    "2014-09-05", "2014-10-03", "2014-11-07", "2014-12-05",
    # 2015
    "2015-01-09", "2015-02-06", "2015-03-06", "2015-04-03",
    "2015-05-08", "2015-06-05", "2015-07-02", "2015-08-07",
    "2015-09-04", "2015-10-02", "2015-11-06", "2015-12-04",
    # 2016
    "2016-01-08", "2016-02-05", "2016-03-04", "2016-04-01",
    "2016-05-06", "2016-06-03", "2016-07-08", "2016-08-05",
    "2016-09-02", "2016-10-07", "2016-11-04", "2016-12-02",
    # 2017
    "2017-01-06", "2017-02-03", "2017-03-10", "2017-04-07",
    "2017-05-05", "2017-06-02", "2017-07-07", "2017-08-04",
    "2017-09-01", "2017-10-06", "2017-11-03", "2017-12-08",
    # 2018
    "2018-01-05", "2018-02-02", "2018-03-09", "2018-04-06",
    "2018-05-04", "2018-06-01", "2018-07-06", "2018-08-03",
    "2018-09-07", "2018-10-05", "2018-11-02", "2018-12-07",
    # 2019
    "2019-01-04", "2019-02-01", "2019-03-08", "2019-04-05",
    "2019-05-03", "2019-06-07", "2019-07-05", "2019-08-02",
    "2019-09-06", "2019-10-04", "2019-11-01", "2019-12-06",
    # 2020
    "2020-01-10", "2020-02-07", "2020-03-06", "2020-04-03",
    "2020-05-08", "2020-06-05", "2020-07-02", "2020-08-07",
    "2020-09-04", "2020-10-02", "2020-11-06", "2020-12-04",
    # 2021
    "2021-01-08", "2021-02-05", "2021-03-05", "2021-04-02",
    "2021-05-07", "2021-06-04", "2021-07-02", "2021-08-06",
    "2021-09-03", "2021-10-08", "2021-11-05", "2021-12-03",
    # 2022
    "2022-01-07", "2022-02-04", "2022-03-04", "2022-04-01",
    "2022-05-06", "2022-06-03", "2022-07-08", "2022-08-05",
    "2022-09-02", "2022-10-07", "2022-11-04", "2022-12-02",
    # 2023
    "2023-01-06", "2023-02-03", "2023-03-10", "2023-04-07",
    "2023-05-05", "2023-06-02", "2023-07-07", "2023-08-04",
    "2023-09-01", "2023-10-06", "2023-11-03", "2023-12-08",
    # 2024
    "2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05",
    "2024-05-03", "2024-06-07", "2024-07-05", "2024-08-02",
    "2024-09-06", "2024-10-04", "2024-11-01", "2024-12-06",
]


class NFPDriftStrategy(CalendarEventStrategy):
    """
    THESIS: NFP release creates predictable drift in ES/ZN/6E/GC.
    12 events/year. Pre-registration: 4 variants (entry × exit).
    """
    name = "NFP_Drift"
    description = "Pre/post NFP release drift"
    EVENT_DATES = NFP_DATES
    EVENT_NAME = "nfp"
    DEFAULT_ENTRY_TYPE = "day_0935"  # NFP is at 08:30, enter after open
    DEFAULT_EXIT_TIME = "1200"       # Exit midday

    param_grid = {
        "entry_type": ["prior_1550", "day_0935"],
        "exit_time": ["1000", "1200", "1555"],  # 90min, 3.5hr, EOD after release
    }


# ======================================================================
# SUBCLASS: CPI Drift
# ======================================================================

# CPI release dates (08:30 ET, ~10th-13th of month)
CPI_DATES = [
    # 2010
    "2010-01-15", "2010-02-19", "2010-03-18", "2010-04-14",
    "2010-05-19", "2010-06-17", "2010-07-16", "2010-08-13",
    "2010-09-17", "2010-10-15", "2010-11-17", "2010-12-15",
    # 2011
    "2011-01-14", "2011-02-17", "2011-03-17", "2011-04-15",
    "2011-05-13", "2011-06-15", "2011-07-15", "2011-08-18",
    "2011-09-15", "2011-10-19", "2011-11-16", "2011-12-16",
    # 2012
    "2012-01-19", "2012-02-17", "2012-03-16", "2012-04-13",
    "2012-05-15", "2012-06-14", "2012-07-17", "2012-08-15",
    "2012-09-14", "2012-10-16", "2012-11-15", "2012-12-14",
    # 2013
    "2013-01-16", "2013-02-21", "2013-03-15", "2013-04-16",
    "2013-05-16", "2013-06-18", "2013-07-16", "2013-08-15",
    "2013-09-17", "2013-10-30", "2013-11-20", "2013-12-17",
    # 2014
    "2014-01-16", "2014-02-20", "2014-03-18", "2014-04-15",
    "2014-05-15", "2014-06-17", "2014-07-22", "2014-08-19",
    "2014-09-17", "2014-10-22", "2014-11-20", "2014-12-17",
    # 2015
    "2015-01-16", "2015-02-26", "2015-03-24", "2015-04-17",
    "2015-05-22", "2015-06-18", "2015-07-17", "2015-08-19",
    "2015-09-16", "2015-10-15", "2015-11-17", "2015-12-15",
    # 2016
    "2016-01-20", "2016-02-19", "2016-03-16", "2016-04-14",
    "2016-05-17", "2016-06-16", "2016-07-15", "2016-08-16",
    "2016-09-14", "2016-10-18", "2016-11-17", "2016-12-15",
    # 2017
    "2017-01-18", "2017-02-15", "2017-03-15", "2017-04-14",
    "2017-05-12", "2017-06-14", "2017-07-14", "2017-08-11",
    "2017-09-14", "2017-10-13", "2017-11-15", "2017-12-13",
    # 2018
    "2018-01-12", "2018-02-14", "2018-03-13", "2018-04-11",
    "2018-05-10", "2018-06-12", "2018-07-12", "2018-08-10",
    "2018-09-13", "2018-10-11", "2018-11-14", "2018-12-12",
    # 2019
    "2019-01-11", "2019-02-13", "2019-03-12", "2019-04-10",
    "2019-05-10", "2019-06-12", "2019-07-11", "2019-08-13",
    "2019-09-12", "2019-10-10", "2019-11-13", "2019-12-11",
    # 2020
    "2020-01-14", "2020-02-13", "2020-03-11", "2020-04-10",
    "2020-05-12", "2020-06-10", "2020-07-14", "2020-08-12",
    "2020-09-11", "2020-10-13", "2020-11-12", "2020-12-10",
    # 2021
    "2021-01-13", "2021-02-10", "2021-03-10", "2021-04-13",
    "2021-05-12", "2021-06-10", "2021-07-13", "2021-08-11",
    "2021-09-14", "2021-10-13", "2021-11-10", "2021-12-10",
    # 2022
    "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12",
    "2022-05-11", "2022-06-10", "2022-07-13", "2022-08-10",
    "2022-09-13", "2022-10-13", "2022-11-10", "2022-12-13",
    # 2023
    "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12",
    "2023-05-10", "2023-06-13", "2023-07-12", "2023-08-10",
    "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12",
    # 2024
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
    "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
]


class CPIDriftStrategy(CalendarEventStrategy):
    """
    THESIS: CPI release creates drift. Hot CPI = rates up, equities down.
    Long-only test first (same as FOMC); if rejected, test short-only.
    12 events/year.
    """
    name = "CPI_Drift"
    description = "Pre/post CPI release drift"
    EVENT_DATES = CPI_DATES
    EVENT_NAME = "cpi"
    DEFAULT_ENTRY_TYPE = "day_0935"
    DEFAULT_EXIT_TIME = "1200"

    param_grid = {
        "entry_type": ["prior_1550", "day_0935"],
        "exit_time": ["1000", "1200", "1555"],
    }


# ======================================================================
# SUBCLASS: ECB Rate Decision
# ======================================================================

# ECB rate decisions (typically 13:45 CET = ~07:45 ET announcement,
# press conference at 14:30 CET = ~08:30 ET)
ECB_DATES = [
    # 2010
    "2010-01-14", "2010-02-04", "2010-03-04", "2010-04-08",
    "2010-05-06", "2010-06-10", "2010-07-08", "2010-08-05",
    "2010-09-02", "2010-10-07", "2010-11-04", "2010-12-02",
    # 2011
    "2011-01-13", "2011-02-03", "2011-03-03", "2011-04-07",
    "2011-05-05", "2011-06-09", "2011-07-07", "2011-08-04",
    "2011-09-08", "2011-10-06", "2011-11-03", "2011-12-08",
    # 2012
    "2012-01-12", "2012-02-09", "2012-03-08", "2012-04-04",
    "2012-05-03", "2012-06-06", "2012-07-05", "2012-08-02",
    "2012-09-06", "2012-10-04", "2012-11-08", "2012-12-06",
    # 2013
    "2013-01-10", "2013-02-07", "2013-03-07", "2013-04-04",
    "2013-05-02", "2013-06-06", "2013-07-04", "2013-08-01",
    "2013-09-05", "2013-10-02", "2013-11-07", "2013-12-05",
    # 2014
    "2014-01-09", "2014-02-06", "2014-03-06", "2014-04-03",
    "2014-05-08", "2014-06-05", "2014-07-03", "2014-08-07",
    "2014-09-04", "2014-10-02", "2014-11-06", "2014-12-04",
    # 2015
    "2015-01-22", "2015-03-05", "2015-04-15", "2015-06-03",
    "2015-07-16", "2015-09-03", "2015-10-22", "2015-12-03",
    # 2016 (moved to 6-week cycle)
    "2016-01-21", "2016-03-10", "2016-04-21", "2016-06-02",
    "2016-07-21", "2016-09-08", "2016-10-20", "2016-12-08",
    # 2017
    "2017-01-19", "2017-03-09", "2017-04-27", "2017-06-08",
    "2017-07-20", "2017-09-07", "2017-10-26", "2017-12-14",
    # 2018
    "2018-01-25", "2018-03-08", "2018-04-26", "2018-06-14",
    "2018-07-26", "2018-09-13", "2018-10-25", "2018-12-13",
    # 2019
    "2019-01-24", "2019-03-07", "2019-04-10", "2019-06-06",
    "2019-07-25", "2019-09-12", "2019-10-24", "2019-12-12",
    # 2020
    "2020-01-23", "2020-03-12", "2020-04-30", "2020-06-04",
    "2020-07-16", "2020-09-10", "2020-10-29", "2020-12-10",
    # 2021
    "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
    "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
    # 2022
    "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
    "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
    # 2023
    "2023-02-02", "2023-03-16", "2023-04-06" , "2023-06-15",
    "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
    # 2024
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
]


class ECBDriftStrategy(CalendarEventStrategy):
    """
    THESIS: ECB rate decisions move 6E (Euro FX) and ZB (bonds).
    8 events/year. Primary target: 6E.
    """
    name = "ECB_Drift"
    description = "Pre/post ECB rate decision drift"
    EVENT_DATES = ECB_DATES
    EVENT_NAME = "ecb"
    DEFAULT_ENTRY_TYPE = "prior_1550"
    DEFAULT_EXIT_TIME = "1200"

    param_grid = {
        "entry_type": ["prior_1550", "day_0935"],
        "exit_time": ["1000", "1200", "1555"],
    }


# ======================================================================
# SUBCLASS: EIA Weekly Petroleum Inventory
# ======================================================================

# EIA reports every Wednesday at 10:30 ET (except holidays → Thursday)
# Too many dates to hardcode 2010-2024. Generate programmatically.

def _generate_eia_dates(start_year=2010, end_year=2024) -> List[str]:
    """Generate approximate EIA release Wednesdays."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            for day in range(1, 32):
                try:
                    d = pd.Timestamp(year, month, day)
                    if d.dayofweek == 2:  # Wednesday
                        dates.append(d.strftime("%Y-%m-%d"))
                except ValueError:
                    continue
    return dates

EIA_DATES = _generate_eia_dates()


class EIAInventoryStrategy(CalendarEventStrategy):
    """
    THESIS: EIA crude inventory report creates directional move in CL.
    ~52 events/year — high sample size.
    Entry AFTER release (10:30 ET), ride the momentum.
    """
    name = "EIA_Inventory"
    description = "Post-EIA crude inventory report drift on CL"
    EVENT_DATES = EIA_DATES
    EVENT_NAME = "eia"
    DEFAULT_ENTRY_TYPE = "day_0935"  # Enter before report
    DEFAULT_EXIT_TIME = "1430"       # Exit afternoon

    param_grid = {
        "entry_type": ["day_0935"],  # Only makes sense to enter before/at report
        "exit_time": ["1100", "1200", "1430"],
    }


# ======================================================================
# SUBCLASS: Fed Minutes
# ======================================================================

# FOMC minutes released 3 weeks after each decision, 14:00 ET
FED_MINUTES_DATES = [
    # 2010
    "2010-02-17", "2010-04-06", "2010-05-19", "2010-07-14",
    "2010-08-31", "2010-10-12", "2010-11-23",
    # 2011
    "2011-01-04", "2011-02-16", "2011-04-05", "2011-05-18",
    "2011-07-12", "2011-08-30", "2011-10-12", "2011-11-22",
    # 2012
    "2012-01-03", "2012-02-15", "2012-04-03", "2012-05-16",
    "2012-07-11", "2012-08-22", "2012-10-04", "2012-11-14",
    # 2013
    "2013-01-03", "2013-02-20", "2013-04-10", "2013-05-22",
    "2013-07-10", "2013-08-21", "2013-10-09", "2013-11-20",
    # 2014
    "2014-01-08", "2014-02-19", "2014-04-09", "2014-05-21",
    "2014-07-09", "2014-08-20", "2014-10-08", "2014-11-19",
    # 2015
    "2015-01-07", "2015-02-18", "2015-04-08", "2015-05-20",
    "2015-07-08", "2015-08-19", "2015-10-08", "2015-11-18",
    # 2016
    "2016-01-06", "2016-02-17", "2016-04-06", "2016-05-18",
    "2016-07-06", "2016-08-17", "2016-10-12", "2016-11-23",
    # 2017
    "2017-01-04", "2017-02-22", "2017-04-05", "2017-05-24",
    "2017-07-05", "2017-08-16", "2017-10-11", "2017-11-22",
    # 2018
    "2018-01-03", "2018-02-21", "2018-04-11", "2018-05-23",
    "2018-07-05", "2018-08-22", "2018-10-17", "2018-11-29",
    # 2019
    "2019-01-09", "2019-02-20", "2019-04-10", "2019-05-22",
    "2019-07-10", "2019-08-21", "2019-10-09", "2019-11-20",
    # 2020
    "2020-01-03", "2020-02-19", "2020-04-08", "2020-05-20",
    "2020-07-01", "2020-08-19", "2020-10-07", "2020-11-25",
    # 2021
    "2021-01-06", "2021-02-17", "2021-04-07", "2021-05-19",
    "2021-07-07", "2021-08-18", "2021-10-13", "2021-11-24",
    # 2022
    "2022-01-05", "2022-02-16", "2022-04-06", "2022-05-25",
    "2022-07-06", "2022-08-17", "2022-10-12", "2022-11-23",
    # 2023
    "2023-01-04", "2023-02-22", "2023-04-12", "2023-05-24",
    "2023-07-05", "2023-08-16", "2023-10-11", "2023-11-21",
    # 2024
    "2024-01-03", "2024-02-21", "2024-04-10", "2024-05-22",
    "2024-07-03", "2024-08-21", "2024-10-09", "2024-11-26",
]


class FedMinutesDriftStrategy(CalendarEventStrategy):
    """
    THESIS: Fed minutes release (14:00 ET) creates drift similar to
    FOMC decisions but softer. ~8 events/year.
    """
    name = "Fed_Minutes_Drift"
    description = "Post-Fed minutes release drift"
    EVENT_DATES = FED_MINUTES_DATES
    EVENT_NAME = "fed_minutes"
    DEFAULT_ENTRY_TYPE = "day_0935"
    DEFAULT_EXIT_TIME = "1555"

    param_grid = {
        "entry_type": ["prior_1550", "day_0935"],
        "exit_time": ["1415", "1555"],
    }