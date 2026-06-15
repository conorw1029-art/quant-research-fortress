"""
tick_yfinance_updater.py — Free OHLCV bar updater via Yahoo Finance
====================================================================
Downloads 1-min delayed futures data from Yahoo Finance and appends
new bars to the parquet files used by the executor.

Covers: GC (gold), SI (silver), ES (S&P 500)
NQ is handled separately by FortressBarWriter via NinjaTrader.

Data is ~15-20 min delayed but free — no credentials needed.
Generates all timeframes (1m, 3m, 5m, 15m, 30m) from 1-min base data.

Usage:
    python tick_yfinance_updater.py           # update once and exit
    python tick_yfinance_updater.py --loop    # run forever, update every 5 min
    python tick_yfinance_updater.py --verbose # show every bar appended
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"

# Yahoo Finance symbol → base symbol used in parquet filenames
SYMBOLS = {
    "GC=F": "GC",   # Gold futures (continuous front month)
    "SI=F": "SI",   # Silver futures
    "ES=F": "ES",   # S&P 500 E-mini futures
    "NQ=F": "NQ",   # Nasdaq futures (fallback — NinjaTrader is primary for NQ)
}

# Timeframes to generate from 1-min base data
TIMEFRAMES = [1, 3, 5, 15, 30]


def download_1m(yf_symbol: str) -> pd.DataFrame | None:
    """Download last 5 days of 1-min bars. Returns UTC-indexed DataFrame."""
    try:
        df = yf.download(yf_symbol, period="5d", interval="1m",
                         progress=False, auto_adjust=True)
        if df is None or len(df) == 0:
            return None

        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["close"])

        # Convert index to UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("America/New_York")
        df.index = df.index.tz_convert("UTC")
        df.index.name = "ts"

        # Add stub columns expected by the executor
        # buy_vol/sell_vol/cvd are unavailable without tick data — set to 0
        df["buy_vol"]   = 0
        df["sell_vol"]  = 0
        df["cvd_delta"] = 0
        df["cvd"]       = 0
        df["n_trades"]  = df["volume"].astype(int)

        return df.sort_index()

    except Exception as e:
        print(f"  [YF] Download error for {yf_symbol}: {e}")
        return None


def resample_to(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample 1-min bars to a higher timeframe."""
    rule = f"{minutes}min"
    agg = {
        "open":      "first",
        "high":      "max",
        "low":       "min",
        "close":     "last",
        "volume":    "sum",
        "buy_vol":   "sum",
        "sell_vol":  "sum",
        "cvd_delta": "sum",
        "n_trades":  "sum",
    }
    resampled = df.resample(rule, label="left", closed="left").agg(agg)
    resampled["cvd"] = resampled["cvd_delta"].cumsum()
    return resampled.dropna(subset=["close"])


def append_to_parquet(new_df: pd.DataFrame, parquet_path: Path,
                      verbose: bool = False) -> int:
    """Append only rows newer than the existing parquet. Returns rows added."""
    new_df = new_df.copy()
    new_df.index = pd.to_datetime(new_df.index, utc=True)

    last_ts = None
    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path, columns=[])
            existing.index = pd.to_datetime(existing.index, utc=True)
            if len(existing) > 0:
                last_ts = existing.index.max()
        except Exception:
            pass

    to_add = new_df[new_df.index > last_ts] if last_ts is not None else new_df

    # Drop the current (incomplete) bar — last bar in yfinance is still forming
    if len(to_add) > 1:
        to_add = to_add.iloc[:-1]

    if len(to_add) == 0:
        return 0

    if parquet_path.exists():
        try:
            existing_full = pd.read_parquet(parquet_path)
            existing_full.index = pd.to_datetime(existing_full.index, utc=True)
            for col in to_add.columns:
                if col not in existing_full.columns:
                    existing_full[col] = np.nan
            for col in existing_full.columns:
                if col not in to_add.columns:
                    to_add[col] = np.nan
            combined = pd.concat([existing_full, to_add]).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
        except Exception:
            combined = to_add
    else:
        combined = to_add

    try:
        combined.to_parquet(parquet_path, engine="pyarrow", compression="snappy")
        if verbose:
            print(f"    +{len(to_add)} bars → {parquet_path.name}  "
                  f"(newest: {to_add.index.max().strftime('%Y-%m-%d %H:%M')} UTC)")
        return len(to_add)
    except Exception as e:
        print(f"  [YF] Parquet write error {parquet_path.name}: {e}")
        return 0


def update_all(verbose: bool = False) -> dict[str, int]:
    """Download and update all symbols and timeframes. Returns {symbol: rows_added}."""
    totals: dict[str, int] = {}

    for yf_sym, base in SYMBOLS.items():
        print(f"[YF] Fetching {base} ({yf_sym})...")
        df_1m = download_1m(yf_sym)
        if df_1m is None or len(df_1m) == 0:
            print(f"  [YF] No data for {base} — skipping")
            continue

        added_total = 0
        for tf in TIMEFRAMES:
            df_tf = resample_to(df_1m, tf) if tf > 1 else df_1m
            path  = BAR_DIR / f"{base}_bars_{tf}m.parquet"
            n     = append_to_parquet(df_tf, path, verbose=verbose)
            added_total += n
            if n > 0 and not verbose:
                print(f"  {base} {tf}m: +{n} bars")

        totals[base] = added_total

    return totals


def main():
    parser = argparse.ArgumentParser(
        description="Free OHLCV bar updater — Yahoo Finance → parquets")
    parser.add_argument("--loop",    action="store_true",
                        help="Run forever, updating every 5 minutes")
    parser.add_argument("--interval", type=int, default=300,
                        help="Loop interval in seconds (default 300 = 5 min)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.loop:
        print(f"[YF] Running in loop mode — updating every {args.interval}s")
        print(f"[YF] Covering: {', '.join(SYMBOLS.values())}  (Ctrl+C to stop)")
        print()
        while True:
            now = datetime.now(timezone.utc)
            print(f"[{now.strftime('%H:%M:%S')} UTC] Updating...")
            totals = update_all(verbose=args.verbose)
            total_bars = sum(totals.values())
            if total_bars:
                print(f"  Total: {total_bars} new bars added")
            else:
                print("  All parquets up to date.")
            print()
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("[YF] Stopped.")
                break
    else:
        print(f"[YF] One-shot update — {', '.join(SYMBOLS.values())}")
        totals = update_all(verbose=args.verbose)
        total = sum(totals.values())
        print(f"\n[YF] Done. {total} new bars added across all timeframes.")


if __name__ == "__main__":
    main()
