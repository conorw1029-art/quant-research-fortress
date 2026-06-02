"""
Depth Imbalance features.

Measures the relative weight of buyers vs sellers in the limit order book:
  imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)

Positive = more resting buy orders (bullish book pressure)
Negative = more resting sell orders (bearish book pressure)

We compute imbalance at multiple depth levels (L1, L3, L5, L10) and use
weighted variants that discount deeper levels.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def depth_imbalance(
    df: pd.DataFrame,
    levels: int = 5,
    weighted: bool = True,
) -> pd.Series:
    """
    Compute depth imbalance at a snapshot (end-of-bar or per-tick).

    Args:
        df: Raw tick or bar DataFrame with bid_sz_00..bid_sz_NN, ask_sz_00..ask_sz_NN.
        levels: Number of levels to aggregate.
        weighted: If True, weight deeper levels by 1/(lvl+1).

    Returns:
        Series in [-1, 1] range per row.
    """
    levels = min(levels, 10)
    bid_total = pd.Series(0.0, index=df.index)
    ask_total = pd.Series(0.0, index=df.index)

    for lvl in range(levels):
        w = 1.0 / (lvl + 1) if weighted else 1.0
        tag = f"{lvl:02d}"
        bid_col = f"bid_sz_{tag}"
        ask_col = f"ask_sz_{tag}"
        if bid_col not in df.columns:
            break
        bid_total += w * df[bid_col].fillna(0.0)
        ask_total += w * df[ask_col].fillna(0.0)

    denom = (bid_total + ask_total).replace(0, np.nan)
    return (bid_total - ask_total) / denom


def imbalance_snapshots(
    df: pd.DataFrame,
    freq: str = "1min",
    levels: int = 5,
    weighted: bool = True,
) -> pd.DataFrame:
    """
    Resample tick-level book to minute-level imbalance snapshots.

    Returns DataFrame with columns:
        imbal_L{n}, imbal_mean, imbal_std for the period.
    """
    imbal_tick = depth_imbalance(df, levels=levels, weighted=weighted)
    r = imbal_tick.resample(freq)
    return pd.DataFrame({
        f"imbal_L{levels}_last":  r.last(),
        f"imbal_L{levels}_mean":  r.mean(),
        f"imbal_L{levels}_std":   r.std().fillna(0.0),
        f"imbal_L{levels}_min":   r.min(),
        f"imbal_L{levels}_max":   r.max(),
    })


def queue_imbalance_at_top(df: pd.DataFrame) -> pd.Series:
    """
    Imbalance using only the top level (L1 bid vs L1 ask).
    Classic microstructure queue imbalance metric.
    """
    return depth_imbalance(df, levels=1, weighted=False)
