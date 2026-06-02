"""
Absorption / Iceberg detection.

Absorption = aggressive orders are being absorbed by a resting position
without significant price movement. Classic signature of an institutional
iceberg order defending a level.

Detection criteria:
  - Large traded volume (high trade activity) on one side
  - Minimal net price movement over that window
  - Book replenishes quickly at the same level (depth stays flat)

If sell volume is absorbed without price falling, a bullish reversal is likely.
If buy volume is absorbed without price rising, a bearish reversal is likely.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _approx_cvd(df: pd.DataFrame) -> pd.Series:
    """
    Approximate cumulative volume delta from trades action and side.
    'T' on 'A' (ask) side = buy aggressor = +size
    'T' on 'B' (bid) side = sell aggressor = -size
    """
    if "action" not in df.columns:
        return pd.Series(0.0, index=df.index)

    trades = df[df["action"] == "T"].copy()
    if trades.empty:
        return pd.Series(0.0, index=df.index)

    sign = pd.Series(0.0, index=trades.index)
    sign[trades["side"] == "A"] = 1.0
    sign[trades["side"] == "B"] = -1.0

    cvd_tick = sign * trades["size"].fillna(0.0)

    # Reindex back to original df index — handle duplicate timestamps
    if df.index.is_unique:
        return cvd_tick.reindex(df.index, fill_value=0.0)
    else:
        # Align by position: create full-length Series with zeros
        out = pd.Series(0.0, index=df.index)
        # Add cvd values at their positions
        for ts, val in cvd_tick.items():
            mask = out.index == ts
            out.loc[mask] += val / mask.sum()  # distribute equally across dups
        return out


def absorption_features_1m(
    df: pd.DataFrame,
    price_move_threshold: float = 0.5,
) -> pd.DataFrame:
    """
    Compute per-minute absorption features.

    Args:
        df: Raw tick DataFrame with ts_event index.
        price_move_threshold: Price range fraction of tick size below which
                              we classify movement as "absorbed."

    Returns:
        DataFrame with columns:
            cvd_delta       — net traded volume direction
            price_range     — high-low of tick trades in bar
            absorption_buy  — 1 if large sell volume absorbed (price didn't fall)
            absorption_sell — 1 if large buy volume absorbed (price didn't rise)
            absorption_score — signed absorption intensity
    """
    cvd_tick = _approx_cvd(df)
    price_tick = df.get("price", pd.Series(np.nan, index=df.index))

    r_cvd   = cvd_tick.resample("1min")
    r_price = price_tick.resample("1min")

    cvd_bar   = r_cvd.sum()
    price_hi  = r_price.max()
    price_lo  = r_price.min()
    price_rng = (price_hi - price_lo).fillna(0.0)

    # Absorption: heavy selling (negative CVD) but price barely moved down
    # We define "barely moved" as < threshold × median_range
    median_range = price_rng.median() if price_rng.median() > 0 else 1.0

    large_sell = cvd_bar < cvd_bar.quantile(0.2)
    large_buy  = cvd_bar > cvd_bar.quantile(0.8)
    tight_move = price_rng < price_move_threshold * median_range

    absorption_buy  = (large_sell & tight_move).astype(int)
    absorption_sell = (large_buy  & tight_move).astype(int)

    # Absorption score: negative CVD with low range = strong bullish absorption
    cvd_std = cvd_bar.std() if cvd_bar.std() > 0 else 1.0
    rng_std = price_rng.std() if price_rng.std() > 0 else 1.0
    absorption_score = -(cvd_bar / cvd_std) * (1 - price_rng / (price_rng + rng_std))

    return pd.DataFrame({
        "cvd_delta":       cvd_bar,
        "price_range_tick": price_rng,
        "absorption_buy":  absorption_buy,
        "absorption_sell": absorption_sell,
        "absorption_score": absorption_score,
    })
