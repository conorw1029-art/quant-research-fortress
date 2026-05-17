#!/usr/bin/env python3
"""
Tick Data Processor — L2 Feature Bar Builder
=============================================
Converts raw Databento tick CSVs into enriched 1-minute feature bars saved
as parquet files. Handles multi-GB files via chunked reading.

Output schema (parquet per symbol):
  Timestamp index (UTC, 1-minute bars)
  OHLCV:   open, high, low, close, volume
  Delta:   buy_vol, sell_vol, cvd_delta, cvd (cumulative)
  Flow:    n_trades, trade_rate, large_buys, large_sells
  Book:    spread_mean, bid_sz_mean, ask_sz_mean, book_pressure  (GC/SI only)
  OBI:     obi_5 (order book imbalance, top 5 levels)            (GC/SI only)

Usage:
  python tick_processor.py --symbol GC
  python tick_processor.py --all
  python tick_processor.py --all --bar-minutes 5
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TICK_DIR = Path(__file__).parent.parent / "01_data" / "tick"
BAR_DIR  = Path(__file__).parent.parent / "01_data" / "tick_bars"

# Aggressor side threshold for "large print" (contracts)
LARGE_PRINT = {
    "GC": 10,
    "SI": 20,
    "ES": 50,
    "NQ": 30,
}

# Which symbols have mbp data available
HAS_MBP = {"GC", "SI"}

TRADES_COLS = ["ts_event", "side", "price", "size"]
MBP1_COLS   = ["ts_event", "bid_px_00", "ask_px_00", "bid_sz_00", "ask_sz_00"]
MBP10_COLS  = (
    ["ts_event"]
    + [f"bid_px_{i:02d}" for i in range(10)]
    + [f"ask_px_{i:02d}" for i in range(10)]
    + [f"bid_sz_{i:02d}" for i in range(10)]
    + [f"ask_sz_{i:02d}" for i in range(10)]
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_trades(symbol: str, chunksize: int = 500_000) -> pd.DataFrame:
    path = TICK_DIR / f"{symbol}_tick_trades.csv"
    print(f"  Loading trades: {path} ({path.stat().st_size / 1e6:.0f} MB)")
    chunks = []
    for chunk in pd.read_csv(
        path,
        usecols=TRADES_COLS,
        parse_dates=["ts_event"],
        chunksize=chunksize,
        low_memory=False,
    ):
        # Keep only actual trades (action='T' in trades schema means all rows are trades)
        chunk = chunk[chunk["side"].isin(["B", "A", "N"])].copy()
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    df = df.sort_values("ts_event").reset_index(drop=True)
    print(f"    {len(df):,} trade ticks loaded")
    return df


def load_mbp1(symbol: str, chunksize: int = 500_000) -> pd.DataFrame:
    path = TICK_DIR / f"{symbol}_tick_mbp1.csv"
    print(f"  Loading mbp-1: {path} ({path.stat().st_size / 1e6:.0f} MB)")
    chunks = []
    for chunk in pd.read_csv(
        path,
        usecols=MBP1_COLS,
        parse_dates=["ts_event"],
        chunksize=chunksize,
        low_memory=False,
    ):
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    df = df.sort_values("ts_event").reset_index(drop=True)
    print(f"    {len(df):,} mbp-1 ticks loaded")
    return df


def load_mbp10(symbol: str, chunksize: int = 200_000) -> pd.DataFrame:
    path = TICK_DIR / f"{symbol}_tick_mbp10.csv"
    print(f"  Loading mbp-10: {path} ({path.stat().st_size / 1e6:.0f} MB)")
    chunks = []
    for chunk in pd.read_csv(
        path,
        usecols=MBP10_COLS,
        parse_dates=["ts_event"],
        chunksize=chunksize,
        low_memory=False,
    ):
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    df = df.sort_values("ts_event").reset_index(drop=True)
    print(f"    {len(df):,} mbp-10 ticks loaded")
    return df


# ---------------------------------------------------------------------------
# Bar builders
# ---------------------------------------------------------------------------

def build_trade_bars(trades: pd.DataFrame, symbol: str, freq: str) -> pd.DataFrame:
    large_thr = LARGE_PRINT.get(symbol, 10)
    trades = trades.set_index("ts_event")

    buy  = trades[trades["side"] == "B"]
    sell = trades[trades["side"] == "A"]

    # OHLCV
    ohlcv = trades["price"].resample(freq).ohlc()
    ohlcv["volume"] = trades["size"].resample(freq).sum()

    # Delta
    ohlcv["buy_vol"]   = buy["size"].resample(freq).sum().reindex(ohlcv.index, fill_value=0)
    ohlcv["sell_vol"]  = sell["size"].resample(freq).sum().reindex(ohlcv.index, fill_value=0)
    ohlcv["cvd_delta"] = ohlcv["buy_vol"] - ohlcv["sell_vol"]
    ohlcv["cvd"]       = ohlcv["cvd_delta"].cumsum()

    # Flow
    ohlcv["n_trades"]   = trades["size"].resample(freq).count()
    ohlcv["trade_rate"] = ohlcv["n_trades"]  # trades per bar; normalise later if needed

    # Large prints
    large = trades[trades["size"] >= large_thr]
    large_buy  = large[large["side"] == "B"]
    large_sell = large[large["side"] == "A"]
    ohlcv["large_buys"]  = large_buy["size"].resample(freq).count().reindex(ohlcv.index, fill_value=0)
    ohlcv["large_sells"] = large_sell["size"].resample(freq).count().reindex(ohlcv.index, fill_value=0)

    # Drop empty bars (outside session)
    ohlcv = ohlcv.dropna(subset=["open"])
    return ohlcv


def build_mbp1_bars(mbp1: pd.DataFrame, freq: str) -> pd.DataFrame:
    mbp1 = mbp1.set_index("ts_event")
    mbp1["spread"] = mbp1["ask_px_00"] - mbp1["bid_px_00"]

    bars = pd.DataFrame(index=mbp1["spread"].resample(freq).mean().index)
    bars["spread_mean"]   = mbp1["spread"].resample(freq).mean()
    bars["bid_sz_mean"]   = mbp1["bid_sz_00"].resample(freq).mean()
    bars["ask_sz_mean"]   = mbp1["ask_sz_00"].resample(freq).mean()

    total = bars["bid_sz_mean"] + bars["ask_sz_mean"]
    bars["book_pressure"] = (bars["bid_sz_mean"] - bars["ask_sz_mean"]) / total.replace(0, np.nan)
    return bars


def build_mbp10_bars(mbp10: pd.DataFrame, freq: str) -> pd.DataFrame:
    mbp10 = mbp10.set_index("ts_event")

    bid_cols = [f"bid_sz_{i:02d}" for i in range(5)]
    ask_cols = [f"ask_sz_{i:02d}" for i in range(5)]

    bid_total = mbp10[bid_cols].sum(axis=1)
    ask_total = mbp10[ask_cols].sum(axis=1)
    obi_raw   = (bid_total - ask_total) / (bid_total + ask_total).replace(0, np.nan)

    bars = pd.DataFrame()
    bars["obi_5"] = obi_raw.resample(freq).mean()
    return bars


# ---------------------------------------------------------------------------
# Main per-symbol processor
# ---------------------------------------------------------------------------

def process_symbol(symbol: str, bar_minutes: int) -> None:
    freq = f"{bar_minutes}min"
    print(f"\n{'='*60}")
    print(f"  Processing {symbol}  ({bar_minutes}-minute bars)")
    print(f"{'='*60}")

    trades = load_trades(symbol)
    bars = build_trade_bars(trades, symbol, freq)
    del trades

    if symbol in HAS_MBP:
        mbp1 = load_mbp1(symbol)
        mbp1_bars = build_mbp1_bars(mbp1, freq)
        del mbp1
        bars = bars.join(mbp1_bars, how="left")

        mbp10 = load_mbp10(symbol)
        mbp10_bars = build_mbp10_bars(mbp10, freq)
        del mbp10
        bars = bars.join(mbp10_bars, how="left")

    BAR_DIR.mkdir(parents=True, exist_ok=True)
    out = BAR_DIR / f"{symbol}_bars_{bar_minutes}m.parquet"
    bars.to_parquet(out, engine="pyarrow", compression="snappy")

    size_mb = out.stat().st_size / 1e6
    print(f"\n  Saved: {len(bars):,} bars  ({size_mb:.1f} MB)  ->  {out}")
    print(f"  Date range: {bars.index.min()}  to  {bars.index.max()}")
    print(f"  Columns: {bars.columns.tolist()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build enriched tick bars from Databento CSVs")
    parser.add_argument("--symbol", type=str, help="Single symbol: GC, SI, ES, NQ")
    parser.add_argument("--all", action="store_true", help="Process all available symbols")
    parser.add_argument("--bar-minutes", type=int, default=1,
                        help="Bar size in minutes (default: 1)")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.all:
        # Only process symbols that have trades files
        symbols = [
            s for s in ["GC", "SI", "ES", "NQ"]
            if (TICK_DIR / f"{s}_tick_trades.csv").exists()
        ]
    else:
        parser.print_help()
        sys.exit(1)

    print(f"Symbols to process: {symbols}")
    print(f"Bar size: {args.bar_minutes} minute(s)")

    for sym in symbols:
        try:
            process_symbol(sym, args.bar_minutes)
        except Exception as e:
            print(f"  ERROR processing {sym}: {e}")
            import traceback; traceback.print_exc()

    print(f"\nDone. Bar files in: {BAR_DIR}")


if __name__ == "__main__":
    main()
