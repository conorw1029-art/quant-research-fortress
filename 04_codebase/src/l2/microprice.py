"""
Microprice computation.

Microprice is a weighted midpoint that accounts for queue imbalance:
  microprice = bid * (ask_sz / (bid_sz + ask_sz)) + ask * (bid_sz / (bid_sz + ask_sz))

When bid_sz >> ask_sz, the microprice pulls toward the ask (market will move up).
When ask_sz >> bid_sz, the microprice pulls toward the bid (market will move down).

This gives a more accurate "fair value" estimate than the plain midpoint.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_microprice(df: pd.DataFrame) -> pd.Series:
    """
    Compute microprice from L1 bid/ask price and size.

    Returns Series aligned to df index. NaN where bid/ask are missing.
    """
    bid_px = df.get("bid_px_00", pd.Series(np.nan, index=df.index))
    ask_px = df.get("ask_px_00", pd.Series(np.nan, index=df.index))
    bid_sz = df.get("bid_sz_00", pd.Series(np.nan, index=df.index)).fillna(0.0)
    ask_sz = df.get("ask_sz_00", pd.Series(np.nan, index=df.index)).fillna(0.0)

    total = bid_sz + ask_sz
    total_safe = total.replace(0, np.nan)

    w_bid = ask_sz / total_safe
    w_ask = bid_sz / total_safe

    return bid_px * w_bid + ask_px * w_ask


def compute_midprice(df: pd.DataFrame) -> pd.Series:
    """Simple midpoint: (bid + ask) / 2."""
    bid_px = df.get("bid_px_00", pd.Series(np.nan, index=df.index))
    ask_px = df.get("ask_px_00", pd.Series(np.nan, index=df.index))
    return (bid_px + ask_px) / 2.0


def compute_spread(df: pd.DataFrame) -> pd.Series:
    """Bid-ask spread in price units."""
    bid_px = df.get("bid_px_00", pd.Series(np.nan, index=df.index))
    ask_px = df.get("ask_px_00", pd.Series(np.nan, index=df.index))
    return (ask_px - bid_px).clip(lower=0)


def microprice_features_1m(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate microprice to 1-minute bars."""
    mp = compute_microprice(df)
    mid = compute_midprice(df)
    spread = compute_spread(df)

    r = pd.DataFrame({
        "microprice": mp,
        "midprice":   mid,
        "spread":     spread,
    }).resample("1min")

    return pd.DataFrame({
        "microprice_last": r["microprice"].last(),
        "microprice_mean": r["microprice"].mean(),
        "midprice_last":   r["midprice"].last(),
        "spread_mean":     r["spread"].mean(),
        "spread_max":      r["spread"].max(),
    })
