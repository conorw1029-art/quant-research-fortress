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

With --l2 flag, also writes {symbol}_bars_l2_{bar}m.parquet containing all
columns above PLUS the V10 L2 strategy features:
  imbal_L5_last   — top-5 depth imbalance at bar close [-1, +1]
  microprice_last — (bid*ask_sz + ask*bid_sz)/(bid_sz+ask_sz) at bar close
  session_vwap    — cumulative VWAP reset at midnight UTC each day

Usage:
  python tick_processor.py --symbol GC
  python tick_processor.py --all
  python tick_processor.py --all --bar-minutes 5
  python tick_processor.py --symbol GC --l2          # emit L2 bar file
  python tick_processor.py --all --l2                # emit L2 for all mbp symbols
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


def build_l2_bars(
    trades: pd.DataFrame,
    mbp1: pd.DataFrame,
    mbp10: pd.DataFrame,
    freq: str,
) -> pd.DataFrame:
    """
    Build the extra columns required by V10 L2 strategies:
      imbal_L5_last  — top-5 depth imbalance at bar close  [-1, +1]
      microprice_last — (bid * ask_sz + ask * bid_sz)/(bid_sz + ask_sz) at bar close
      session_vwap   — cumulative VWAP reset at midnight UTC each day

    Returns a DataFrame indexed on the bar timestamps (same as trade bars).
    """
    # ── microprice and imbal from mbp-10 (last tick per bar) ──────────────
    mbp10 = mbp10.set_index("ts_event").sort_index()
    mbp1  = mbp1.set_index("ts_event").sort_index()

    bid_sz_cols = [f"bid_sz_{i:02d}" for i in range(5)]
    ask_sz_cols = [f"ask_sz_{i:02d}" for i in range(5)]

    bid5 = mbp10[bid_sz_cols].sum(axis=1)
    ask5 = mbp10[ask_sz_cols].sum(axis=1)
    denom5 = (bid5 + ask5).replace(0, np.nan)
    imbal_tick = (bid5 - ask5) / denom5

    bid0 = mbp1["bid_sz_00"]
    ask0 = mbp1["ask_sz_00"]
    bid_px = mbp1["bid_px_00"]
    ask_px = mbp1["ask_px_00"]
    denom_mp = (bid0 + ask0).replace(0, np.nan)
    micro_tick = (bid_px * ask0 + ask_px * bid0) / denom_mp

    bars = pd.DataFrame({
        "imbal_L5_last":   imbal_tick.resample(freq).last(),
        "microprice_last": micro_tick.resample(freq).last(),
    })

    # ── session VWAP from trades, reset at midnight UTC each day ──────────
    t = trades.set_index("ts_event").sort_index().copy()
    t["pv"] = t["price"] * t["size"]

    # Group by date then compute cumulative sums within each date
    t["date"] = t.index.normalize()
    t["cum_pv"]  = t.groupby("date")["pv"].cumsum()
    t["cum_vol"] = t.groupby("date")["size"].cumsum()
    t["vwap_tick"] = t["cum_pv"] / t["cum_vol"].replace(0, np.nan)

    bars["session_vwap"] = t["vwap_tick"].resample(freq).last()

    return bars


# ---------------------------------------------------------------------------
# Main per-symbol processor
# ---------------------------------------------------------------------------

def process_symbol(symbol: str, bar_minutes: int, emit_l2: bool = False) -> None:
    freq = f"{bar_minutes}min"
    print(f"\n{'='*60}")
    print(f"  Processing {symbol}  ({bar_minutes}-minute bars)")
    print(f"{'='*60}")

    trades = load_trades(symbol)
    bars = build_trade_bars(trades, symbol, freq)

    mbp1 = mbp10 = None
    if symbol in HAS_MBP:
        mbp1 = load_mbp1(symbol)
        mbp1_bars = build_mbp1_bars(mbp1, freq)
        bars = bars.join(mbp1_bars, how="left")

        mbp10 = load_mbp10(symbol)
        mbp10_bars = build_mbp10_bars(mbp10, freq)
        bars = bars.join(mbp10_bars, how="left")

    BAR_DIR.mkdir(parents=True, exist_ok=True)
    out = BAR_DIR / f"{symbol}_bars_{bar_minutes}m.parquet"
    bars.to_parquet(out, engine="pyarrow", compression="snappy")
    size_mb = out.stat().st_size / 1e6
    print(f"\n  Saved: {len(bars):,} bars  ({size_mb:.1f} MB)  ->  {out}")
    print(f"  Date range: {bars.index.min()}  to  {bars.index.max()}")
    print(f"  Columns: {bars.columns.tolist()}")

    # ── Optional: emit L2 bar file for V10 strategies ─────────────────────
    if emit_l2:
        if symbol not in HAS_MBP:
            print(f"  SKIP L2 output: {symbol} has no mbp data")
            del trades
            return
        if mbp1 is None or mbp10 is None:
            print(f"  SKIP L2 output: mbp files not loaded")
            del trades
            return

        print(f"  Building L2 columns (imbal_L5_last, microprice_last, session_vwap)...")
        l2_extra = build_l2_bars(trades, mbp1, mbp10, freq)
        bars_l2 = bars.join(l2_extra, how="left")

        out_l2 = BAR_DIR / f"{symbol}_bars_l2_{bar_minutes}m.parquet"
        bars_l2.to_parquet(out_l2, engine="pyarrow", compression="snappy")
        size_l2 = out_l2.stat().st_size / 1e6
        print(f"  Saved L2: {len(bars_l2):,} bars  ({size_l2:.1f} MB)  ->  {out_l2}")
        print(f"  L2 columns added: {[c for c in l2_extra.columns.tolist()]}")

    del trades
    if mbp1 is not None: del mbp1
    if mbp10 is not None: del mbp10


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build enriched tick bars from Databento CSVs")
    parser.add_argument("--symbol", type=str, help="Single symbol: GC, SI, ES, NQ")
    parser.add_argument("--all", action="store_true", help="Process all available symbols")
    parser.add_argument("--bar-minutes", type=int, default=1,
                        help="Bar size in minutes (default: 1)")
    parser.add_argument("--l2", action="store_true",
                        help="Also emit {symbol}_bars_l2_{bar}m.parquet with L2 columns "
                             "(imbal_L5_last, microprice_last, session_vwap). "
                             "Only for GC/SI which have mbp-10 data.")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.all:
        symbols = [
            s for s in ["GC", "SI", "ES", "NQ"]
            if (TICK_DIR / f"{s}_tick_trades.csv").exists()
        ]
    else:
        parser.print_help()
        sys.exit(1)

    print(f"Symbols to process: {symbols}")
    print(f"Bar size: {args.bar_minutes} minute(s)")
    if args.l2:
        print("L2 output: ENABLED (will write _bars_l2_ files for GC/SI)")

    for sym in symbols:
        try:
            process_symbol(sym, args.bar_minutes, emit_l2=args.l2)
        except Exception as e:
            print(f"  ERROR processing {sym}: {e}")
            import traceback; traceback.print_exc()

    print(f"\nDone. Bar files in: {BAR_DIR}")


if __name__ == "__main__":
    main()
