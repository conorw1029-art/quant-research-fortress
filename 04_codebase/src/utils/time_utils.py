"""
Time Utilities
===============
Session handling, RTH filtering, timezone conversion.
Stateless pure functions — no side effects.
"""

import datetime as dt
from typing import Optional, Tuple

import numpy as np
import pandas as pd


def ensure_eastern(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    source_tz: str = "UTC",
) -> pd.DataFrame:
    """
    Ensure timestamp column is tz-aware US/Eastern.
    Returns new DataFrame (never mutates input).
    """
    out = df.copy()
    ts = out[ts_col]

    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(source_tz)

    out[ts_col] = ts.dt.tz_convert("US/Eastern")
    return out


def filter_rth(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    rth_start: str = "09:30",
    rth_end: str = "16:00",
    add_flag: bool = True,
) -> pd.DataFrame:
    """
    Filter to Regular Trading Hours.

    Args:
        df: DataFrame with tz-aware Eastern timestamps.
        ts_col: Timestamp column name.
        rth_start: "HH:MM" start of RTH (inclusive).
        rth_end: "HH:MM" end of RTH (exclusive).
        add_flag: If True, add 'is_rth' column before filtering.

    Returns:
        Filtered DataFrame (copy).
    """
    start = dt.time(*map(int, rth_start.split(":")))
    end = dt.time(*map(int, rth_end.split(":")))

    t = df[ts_col].dt.time
    mask = (t >= start) & (t < end)

    if add_flag:
        out = df.copy()
        out["is_rth"] = mask
        return out[mask].copy()

    return df[mask].copy()


def assign_session_date(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
) -> pd.DataFrame:
    """
    Assign a trading session date to each bar.
    For RTH bars, session_date = calendar date.
    For overnight bars (if present), session_date = next trading day.

    For RTH-only data, this is simply the calendar date.
    """
    out = df.copy()
    out["session_date"] = out[ts_col].dt.date
    return out


def resample_ohlcv(
    df: pd.DataFrame,
    timeframe: str,
    ts_col: str = "timestamp",
) -> pd.DataFrame:
    """
    Resample OHLCV bars to a higher timeframe.
    Preserves: first open, max high, min low, last close, sum volume.

    Args:
        df: DataFrame with timestamp index or column, must have OHLCV columns.
        timeframe: Pandas resample string: "5min", "15min", "1h", "4h", "1D".
        ts_col: Timestamp column (will be set as index if not already).

    Returns:
        Resampled DataFrame with timestamp as index.
    """
    work = df.copy()
    if ts_col in work.columns:
        work = work.set_index(ts_col)

    resampled = work.resample(timeframe, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])

    return resampled