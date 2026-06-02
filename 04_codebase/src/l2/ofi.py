"""
Order Flow Imbalance (OFI) computation.

OFI measures the net pressure of aggressive order flow:
  - Aggressive buy: event that lifts the ask (trade action 'T' on ask side,
    or book update that reduces ask depth)
  - Aggressive sell: event that hits the bid (trade on bid side,
    or book update that reduces bid depth)

For mbp-10 data, we track changes in L1 bid/ask quantities between
consecutive updates. An increase in bid size = limit buy order added (passive).
A decrease in bid size = limit buy cancelled or trade executed against bid.

The standard OFI formula (Cont et al.):
  OFI = Δbid_sz * sign(bid_px change) - Δask_sz * sign(ask_px change)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_ofi(
    df: pd.DataFrame,
    levels: int = 5,
    normalize: bool = True,
) -> pd.Series:
    """
    Compute OFI from raw mbp-10 tick DataFrame.

    Args:
        df: Raw tick df with columns bid_px_00..bid_px_NN, bid_sz_00..bid_sz_NN,
            ask_px_00..ask_px_NN, ask_sz_00..ask_sz_NN, indexed by ts_event.
        levels: Number of book levels to use (1-10).
        normalize: If True, divide by total depth to get relative imbalance.

    Returns:
        Series of OFI values aligned to the input index.
    """
    levels = min(levels, 10)
    ofi = pd.Series(0.0, index=df.index)

    for lvl in range(levels):
        tag = f"{lvl:02d}"
        bp_col = f"bid_px_{tag}"
        ap_col = f"ask_px_{tag}"
        bs_col = f"bid_sz_{tag}"
        as_col = f"ask_sz_{tag}"

        if not all(c in df.columns for c in (bp_col, ap_col, bs_col, as_col)):
            break

        bid_px = df[bp_col].fillna(0.0)
        ask_px = df[ap_col].fillna(0.0)
        bid_sz = df[bs_col].fillna(0.0)
        ask_sz = df[as_col].fillna(0.0)

        d_bid_px = bid_px.diff().fillna(0.0)
        d_ask_px = ask_px.diff().fillna(0.0)
        d_bid_sz = bid_sz.diff().fillna(0.0)
        d_ask_sz = ask_sz.diff().fillna(0.0)

        # OFI contribution at this level
        e_bid = np.where(d_bid_px >= 0, d_bid_sz, -bid_sz)
        e_ask = np.where(d_ask_px <= 0, -d_ask_sz, ask_sz)

        ofi += pd.Series(e_bid - e_ask, index=df.index)

    if normalize:
        total_depth = sum(
            df.get(f"bid_sz_{i:02d}", pd.Series(0.0, index=df.index)).fillna(0.0) +
            df.get(f"ask_sz_{i:02d}", pd.Series(0.0, index=df.index)).fillna(0.0)
            for i in range(levels)
        )
        total_depth = total_depth.replace(0, np.nan)
        ofi = ofi / total_depth

    return ofi


def ofi_by_minute(
    df: pd.DataFrame,
    levels: int = 5,
) -> pd.Series:
    """
    Resample raw tick OFI to 1-minute sums.

    Returns Series with 1-minute DatetimePeriod index.
    """
    ofi_tick = compute_ofi(df, levels=levels, normalize=False)
    return ofi_tick.resample("1min").sum()
