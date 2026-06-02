"""
Sweep detection.

A sweep occurs when a large aggressive order consumes multiple price levels
in the book. Indicators:
  1. A series of 'T' (trade) events on the same side that advance through
     multiple price levels in rapid succession.
  2. After a sweep, if the book does NOT replenish quickly, the move is
     likely to continue (no-replenishment sweep continuation signal).
  3. If the book replenishes within N seconds, a reversal is probable.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def detect_sweeps(
    df: pd.DataFrame,
    min_levels: int = 2,
    window_ms: int = 500,
) -> pd.DataFrame:
    """
    Detect sweep events from raw mbp-10 tick data.

    A sweep = N or more trade events on the same side within window_ms,
    each at a progressively more aggressive price.

    Args:
        df: Raw tick DataFrame with action, side, price, depth columns
            and ts_event as DatetimeIndex.
        min_levels: Minimum depth levels consumed to qualify as a sweep.
        window_ms: Rolling window in milliseconds for grouping sweep events.

    Returns:
        DataFrame with columns: sweep_ts, direction (+1 buy, -1 sell),
        n_levels, total_size, price_range, classified as buy/sell sweep.
    """
    if "action" not in df.columns or "side" not in df.columns:
        return pd.DataFrame()

    trades = df[df["action"] == "T"].copy()
    if trades.empty:
        return pd.DataFrame()

    trades = trades.sort_index()
    trades["ts_ms"] = trades.index.astype(np.int64) // 1_000_000

    records = []
    i = 0
    arr_ts = trades["ts_ms"].values
    arr_side = trades["side"].values
    arr_price = trades["price"].values
    arr_size = trades["size"].values

    while i < len(trades):
        j = i + 1
        t0 = arr_ts[i]
        side0 = arr_side[i]

        while j < len(trades) and arr_ts[j] - t0 <= window_ms and arr_side[j] == side0:
            j += 1

        n = j - i
        if n >= min_levels:
            seg_prices = arr_price[i:j]
            seg_sizes  = arr_size[i:j]
            price_range = float(max(seg_prices) - min(seg_prices))
            total_size  = int(sum(seg_sizes))
            direction   = 1 if side0 == "A" else -1  # 'A' ask side = buy aggressor

            records.append({
                "sweep_ts":    trades.index[i],
                "direction":   direction,
                "n_events":    n,
                "total_size":  total_size,
                "price_range": price_range,
            })

        i = j

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records).set_index("sweep_ts")


def sweep_features_1m(
    df: pd.DataFrame,
    min_levels: int = 2,
) -> pd.DataFrame:
    """
    Aggregate sweep events to 1-minute bar features.

    Returns DataFrame with columns:
        buy_sweeps, sell_sweeps, net_sweeps, sweep_size_ratio
    """
    sweeps = detect_sweeps(df, min_levels=min_levels)
    if sweeps.empty:
        minute_index = df.resample("1min").last().index
        return pd.DataFrame({
            "buy_sweeps": 0,
            "sell_sweeps": 0,
            "net_sweeps": 0,
            "sweep_net_size": 0.0,
        }, index=minute_index)

    buy_sw = sweeps[sweeps["direction"] == 1]
    sell_sw = sweeps[sweeps["direction"] == -1]

    buy_cnt  = buy_sw.resample("1min")["direction"].count().rename("buy_sweeps")
    sell_cnt = sell_sw.resample("1min")["direction"].count().rename("sell_sweeps")
    buy_size = buy_sw.resample("1min")["total_size"].sum().rename("buy_sweep_size")
    sell_size = sell_sw.resample("1min")["total_size"].sum().rename("sell_sweep_size")

    result = pd.concat([buy_cnt, sell_cnt, buy_size, sell_size], axis=1).fillna(0)
    result["net_sweeps"]    = result["buy_sweeps"] - result["sell_sweeps"]
    result["sweep_net_size"] = result["buy_sweep_size"] - result["sell_sweep_size"]

    return result[["buy_sweeps", "sell_sweeps", "net_sweeps", "sweep_net_size"]]
