#!/usr/bin/env python3
"""
tick_nt8_import.py — Import NinjaTrader 8 historical bar exports into fortress parquets
========================================================================================
NinjaTrader → Historical Data Export → CSV → parquet files used by the executor.

This is the fastest path to multi-year 1m/5m/15m data.
With 2+ years of proper historical data, the V678 WFO results for 1m/5m/15m strategies
become statistically meaningful (currently only 10-70 days of data = noise).

HOW TO EXPORT FROM NINJATRADE 8:
  1. NinjaTrader → Tools → Historical Data Manager
  2. Select instrument (e.g. "GC 09-26" for Sep 2026 Gold)
  3. Select timeframe (1 Min, 5 Min, 15 Min etc.)
  4. Export → CSV
  5. File format: Date, Time, Open, High, Low, Close, Volume
     Example row: 20240115, 09:30:00, 2050.1, 2051.3, 2049.8, 2050.9, 1234

  OR: use Data Series → right-click → Export to file

INSTRUMENT MAPPING:
  NT8 symbol → fortress symbol
  GC 09-26   → GC  (Gold)
  SI 09-26   → SI  (Silver)
  ES 09-26   → ES  (S&P 500 E-mini)
  NQ 09-26   → NQ  (Nasdaq E-mini)
  (use continuous contract @GC, @SI, @ES, @NQ for longest history)

Usage:
    python tick_nt8_import.py --file GC_1m.csv --symbol GC --timeframe 1
    python tick_nt8_import.py --file /path/to/NQ_5m.csv --symbol NQ --timeframe 5
    python tick_nt8_import.py --dir /path/to/exports/  # batch import all CSVs in directory

    # Auto-detect symbol and timeframe from filename (NT8 export convention):
    python tick_nt8_import.py --file "GC 09-26_1 Min_20200101_20260624.csv"
"""
from __future__ import annotations

import argparse, re, sys
from pathlib import Path
import pandas as pd

ROOT    = Path(__file__).parent.parent
BAR_DIR = Path("/opt/fortress/01_data/tick_bars")
if not BAR_DIR.exists():
    BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

# NT8 symbol → fortress base symbol
NT8_SYMBOL_MAP = {
    "GC": "GC", "@GC": "GC",
    "SI": "SI", "@SI": "SI",
    "ES": "ES", "@ES": "ES",
    "NQ": "NQ", "@NQ": "NQ",
    "MGC": "GC",
    "MES": "ES",
    "MNQ": "NQ",
}

# NT8 timeframe string → minutes
NT8_TF_MAP = {
    "1 min": 1,  "1min": 1,  "1m": 1,  "1": 1,
    "3 min": 3,  "3min": 3,  "3m": 3,  "3": 3,
    "5 min": 5,  "5min": 5,  "5m": 5,  "5": 5,
    "15 min": 15, "15min": 15, "15m": 15, "15": 15,
    "30 min": 30, "30min": 30, "30m": 30, "30": 30,
    "60 min": 60, "60min": 60, "60m": 60, "60": 60, "1 hour": 60,
}


def parse_nt8_csv(path: Path) -> pd.DataFrame:
    """Parse a NinjaTrader 8 exported CSV into a standard OHLCV DataFrame."""
    # Try to detect the format by reading the first few lines
    with open(path, "r") as f:
        sample = [f.readline() for _ in range(5)]

    has_header = any(c.isalpha() for c in sample[0][:20])

    # NT8 exports have no header by default: Date;Time;Open;High;Low;Close;Volume
    # or with semicolons/commas
    sep = ";" if ";" in sample[0] else ","

    if has_header:
        df = pd.read_csv(path, sep=sep)
        df.columns = [c.strip().lower() for c in df.columns]
        # Map NT8 column names
        col_map = {
            "date": "date", "time": "time",
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "vol": "volume",
        }
        df = df.rename(columns={c: col_map.get(c, c) for c in df.columns})
    else:
        df = pd.read_csv(path, sep=sep, header=None)
        # Standard NT8 format: Date, Time, Open, High, Low, Close, Volume
        if df.shape[1] >= 7:
            df.columns = ["date", "time", "open", "high", "low", "close", "volume"] + \
                         [f"extra_{i}" for i in range(df.shape[1] - 7)]
        elif df.shape[1] == 6:
            # Some formats have date+time combined
            df.columns = ["datetime", "open", "high", "low", "close", "volume"]
        else:
            raise ValueError(f"Unexpected column count: {df.shape[1]}")

    # Build datetime index
    if "date" in df.columns and "time" in df.columns:
        dt_str = df["date"].astype(str) + " " + df["time"].astype(str)
        ts = pd.to_datetime(dt_str, infer_datetime_format=True)
    elif "datetime" in df.columns:
        ts = pd.to_datetime(df["datetime"], infer_datetime_format=True)
    else:
        raise ValueError("Cannot find date/time columns")

    # NT8 exports are typically in US/Eastern or exchange local time
    # Convert to UTC (futures trade on CME which is US/Central = UTC-6/5)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("America/Chicago", ambiguous="NaT", nonexistent="NaT")
    ts = ts.dt.tz_convert("UTC")

    out = pd.DataFrame({
        "open":   pd.to_numeric(df["open"],   errors="coerce"),
        "high":   pd.to_numeric(df["high"],   errors="coerce"),
        "low":    pd.to_numeric(df["low"],    errors="coerce"),
        "close":  pd.to_numeric(df["close"],  errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int),
    }, index=ts)
    out.index.name = "ts"

    # Add required stub columns
    out["buy_vol"]   = 0
    out["sell_vol"]  = 0
    out["cvd_delta"] = 0
    out["cvd"]       = 0
    out["n_trades"]  = out["volume"]

    return out.dropna(subset=["close"]).sort_index()


def merge_into_parquet(new_df: pd.DataFrame, symbol: str, bar_min: int) -> dict:
    """Merge new bars into existing parquet, deduplicating by timestamp."""
    parquet_path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"

    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        existing.index = pd.to_datetime(existing.index, utc=True)
        combined = pd.concat([existing, new_df])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        n_added = len(combined) - len(existing)
    else:
        combined = new_df
        n_added = len(combined)

    combined.to_parquet(parquet_path)

    first = combined.index[0].strftime("%Y-%m-%d")
    last  = combined.index[-1].strftime("%Y-%m-%d")
    return {
        "symbol":    symbol,
        "bar_min":   bar_min,
        "total":     len(combined),
        "added":     n_added,
        "date_range": f"{first} → {last}",
        "path":      str(parquet_path),
    }


def guess_symbol_tf(filename: str) -> tuple[str | None, int | None]:
    """Try to extract symbol and timeframe from NT8 export filename."""
    name = Path(filename).stem.upper()
    sym = None
    for nt_sym, fort_sym in NT8_SYMBOL_MAP.items():
        if nt_sym in name:
            sym = fort_sym
            break

    tf = None
    for tf_str, tf_min in NT8_TF_MAP.items():
        if tf_str.upper() in name:
            tf = tf_min
            break
    # Also try "1MIN", "5MIN" etc. pattern
    m = re.search(r"(\d+)\s*MIN", name)
    if m and tf is None:
        tf = int(m.group(1))

    return sym, tf


def import_file(csv_path: Path, symbol: str, bar_min: int, verbose: bool = True) -> dict:
    if verbose:
        print(f"\n  Importing {csv_path.name}")
        print(f"  Symbol: {symbol}  Timeframe: {bar_min}m")

    df = parse_nt8_csv(csv_path)

    if verbose:
        print(f"  Parsed: {len(df)} bars  ({df.index[0].date()} → {df.index[-1].date()})")

    result = merge_into_parquet(df, symbol, bar_min)

    if verbose:
        print(f"  Merged: {result['total']} total bars (+{result['added']} new)  {result['date_range']}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Import NinjaTrader 8 historical bar exports into fortress parquets"
    )
    parser.add_argument("--file", type=str, help="Path to a single NT8 CSV export")
    parser.add_argument("--dir", type=str, help="Directory of NT8 CSV exports to batch import")
    parser.add_argument("--symbol", type=str, help="Base symbol (GC, SI, ES, NQ)")
    parser.add_argument("--timeframe", type=int, help="Bar size in minutes (1, 3, 5, 15, 30, 60)")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    results = []

    if args.file:
        path = Path(args.file)
        sym  = args.symbol
        tf   = args.timeframe

        # Auto-detect if not provided
        if sym is None or tf is None:
            auto_sym, auto_tf = guess_symbol_tf(path.name)
            sym = sym or auto_sym
            tf  = tf  or auto_tf

        if sym is None:
            print(f"ERROR: Cannot detect symbol from '{path.name}'. Use --symbol GC (or SI/ES/NQ)")
            sys.exit(1)
        if tf is None:
            print(f"ERROR: Cannot detect timeframe from '{path.name}'. Use --timeframe 1 (minutes)")
            sys.exit(1)

        sym = NT8_SYMBOL_MAP.get(sym.upper(), sym.upper())
        results.append(import_file(path, sym, tf, args.verbose))

    elif args.dir:
        csv_files = list(Path(args.dir).glob("*.csv")) + list(Path(args.dir).glob("*.txt"))
        print(f"\n  Found {len(csv_files)} files in {args.dir}")
        for f in sorted(csv_files):
            sym, tf = guess_symbol_tf(f.name)
            if sym and tf:
                results.append(import_file(f, sym, tf, args.verbose))
            else:
                print(f"  SKIP {f.name} — could not detect symbol/timeframe")

    else:
        parser.print_help()
        print("\n  EXAMPLE USAGE:")
        print("  python tick_nt8_import.py --file 'GC 09-26_1 Min_20200101_20260624.csv' --symbol GC --timeframe 1")
        print("  python tick_nt8_import.py --dir ~/nt8_exports/")
        sys.exit(0)

    if results:
        print(f"\n  {'='*55}")
        print(f"  IMPORT COMPLETE — {len(results)} files")
        print(f"  {'='*55}")
        for r in results:
            print(f"  {r['symbol']}/{r['bar_min']}m: {r['total']} bars  {r['date_range']}  (+{r['added']} new)")

        print(f"\n  Bars written to: {BAR_DIR}")
        print(f"  Run backtest: python tick_runner_v678.py")
        print(f"  Restart executor: systemctl restart fortress-executor")


if __name__ == "__main__":
    main()
