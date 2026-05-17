#!/usr/bin/env python3
"""
Tick Bar Resampler
==================
Resamples existing 1-minute parquet bar files to any target resolution.
Runs in seconds — no re-reading of raw tick CSVs needed.

Usage:
  python tick_resample.py               # builds 3, 15, 30-min from 1-min
  python tick_resample.py --targets 3 15 30 60
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BAR_DIR = Path(__file__).parent.parent / "01_data" / "tick_bars"
SYMBOLS = ["GC", "SI", "ES", "NQ"]

# How each column aggregates when resampling
AGG_MAP = {
    "open":          "first",
    "high":          "max",
    "low":           "min",
    "close":         "last",
    "volume":        "sum",
    "buy_vol":       "sum",
    "sell_vol":      "sum",
    "cvd_delta":     "sum",
    "cvd":           "last",     # cumulative — take end-of-bar value
    "n_trades":      "sum",
    "trade_rate":    "sum",
    "large_buys":    "sum",
    "large_sells":   "sum",
    # MBP features (GC/SI only) — time-average
    "spread_mean":   "mean",
    "bid_sz_mean":   "mean",
    "ask_sz_mean":   "mean",
    "book_pressure": "mean",
    "obi_5":         "mean",
}


def resample_symbol(symbol: str, source_min: int, target_min: int) -> None:
    src = BAR_DIR / f"{symbol}_bars_{source_min}m.parquet"
    dst = BAR_DIR / f"{symbol}_bars_{target_min}m.parquet"

    if not src.exists():
        print(f"  SKIP {symbol}: source {src.name} not found")
        return

    df = pd.read_parquet(src)
    df.index = pd.to_datetime(df.index, utc=True)

    freq = f"{target_min}min"
    agg  = {col: agg for col, agg in AGG_MAP.items() if col in df.columns}

    resampled = df.resample(freq).agg(agg).dropna(subset=["open"])

    resampled.to_parquet(dst, engine="pyarrow", compression="snappy")
    mb = dst.stat().st_size / 1e6
    print(f"  {symbol} {target_min}m: {len(resampled):,} bars  ({mb:.1f} MB)  -> {dst.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets",  type=int, nargs="+", default=[3, 15, 30],
                        help="Target bar sizes in minutes (default: 3 15 30)")
    parser.add_argument("--source",   type=int, default=1,
                        help="Source bar size in minutes (default: 1)")
    parser.add_argument("--symbols",  type=str, nargs="+", default=SYMBOLS)
    args = parser.parse_args()

    print(f"Resampling from {args.source}-min bars to: {args.targets}")
    for target in args.targets:
        print(f"\n--- {target}-minute bars ---")
        for sym in args.symbols:
            resample_symbol(sym, args.source, target)

    print("\nDone.")


if __name__ == "__main__":
    main()
