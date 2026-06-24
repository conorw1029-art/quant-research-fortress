#!/usr/bin/env python3
"""
tick_history_bootstrap.py — One-shot historical bar downloader for backtesting
==============================================================================
Downloads the maximum available yfinance history for each interval and
writes parquet files to 01_data/tick_bars/. This gives V6/V7/V8/V9 runners
enough data for proper WFO backtesting.

yfinance limits:
  1m  → 7 days      (already handled by tick_yfinance_updater.py)
  5m  → 60 days
  15m → 60 days
  30m → 60 days     (treated as half-hour; futures trade ~23h/day = ~2,760 bars)
  1h  → 730 days    (~16,790 bars over 2 years)

Strategy: download 5m for 60 days as the base (resamples cleanly to 5m, 15m, 30m).
Download 1h for 730 days for the 30m and longer-window strategies.
Merge with existing 1m bars (do NOT overwrite those — updater handles 1m).

Usage:
    python tick_history_bootstrap.py           # download all symbols
    python tick_history_bootstrap.py --dry-run # show what would be downloaded
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = {
    "GC=F": "GC",
    "SI=F": "SI",
    "ES=F": "ES",
    "NQ=F": "NQ",
}

STUB_COLS = {
    "buy_vol":   0,
    "sell_vol":  0,
    "cvd_delta": 0,
    "cvd":       0,
    "n_trades":  0,
}


def _add_stubs(df: pd.DataFrame) -> pd.DataFrame:
    """Add zero-filled stub columns for tick-derived fields."""
    for col, val in STUB_COLS.items():
        if col not in df.columns:
            df[col] = val
    if "n_trades" in df.columns and df["n_trades"].sum() == 0:
        df["n_trades"] = df["volume"].astype(int)
    return df


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df = df.dropna(subset=["close"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "ts"
    return df.sort_index()


def _resample_to(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
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
    rs = df.resample(rule, label="left", closed="left").agg(agg)
    rs["cvd"] = rs["cvd_delta"].cumsum()
    return rs.dropna(subset=["close"])


def _merge_parquet(new_df: pd.DataFrame, path: Path, verbose: bool = True) -> int:
    """Merge new_df with existing parquet, keeping newer of duplicate timestamps."""
    new_df = new_df.copy()
    new_df.index = pd.to_datetime(new_df.index, utc=True)

    if path.exists():
        existing = pd.read_parquet(path)
        existing.index = pd.to_datetime(existing.index, utc=True)
        combined = pd.concat([existing, new_df])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
    else:
        combined = new_df

    combined.to_parquet(path)
    added = len(new_df) - (len(new_df) - len(combined) + (len(pd.read_parquet(path)) - len(combined) if path.exists() else 0))
    if verbose:
        print(f"    → {path.name}: {len(combined)} total bars (date range: "
              f"{combined.index[0].date()} to {combined.index[-1].date()})")
    return len(combined)


def download_and_store(yf_symbol: str, base_sym: str, verbose: bool = True, dry_run: bool = False):
    print(f"\n{'='*60}")
    print(f"  {base_sym} ({yf_symbol})")
    print(f"{'='*60}")

    # ── 5m base (60 days) → generates 5m, 15m, 30m ───────────────────────────
    print(f"  Downloading 5m (60d)...")
    try:
        raw5 = yf.download(yf_symbol, period="60d", interval="5m",
                           progress=False, auto_adjust=True)
        if raw5 is None or len(raw5) == 0:
            print(f"  [WARN] No 5m data returned")
            raw5 = None
        else:
            raw5 = _normalise(raw5)
            raw5 = _add_stubs(raw5)
            print(f"  5m data: {len(raw5)} bars "
                  f"({raw5.index[0].date()} – {raw5.index[-1].date()})")
    except Exception as e:
        print(f"  [ERROR] 5m download failed: {e}")
        raw5 = None

    if raw5 is not None and not dry_run:
        # 5m bars
        _merge_parquet(raw5, BAR_DIR / f"{base_sym}_bars_5m.parquet", verbose)
        # Resample 5m → 15m
        df15 = _resample_to(raw5, 15)
        _merge_parquet(df15, BAR_DIR / f"{base_sym}_bars_15m.parquet", verbose)
        # Resample 5m → 30m
        df30 = _resample_to(raw5, 30)
        _merge_parquet(df30, BAR_DIR / f"{base_sym}_bars_30m.parquet", verbose)
        # Resample 5m → 3m
        df3 = _resample_to(raw5, 3)
        _merge_parquet(df3, BAR_DIR / f"{base_sym}_bars_3m.parquet", verbose)

    # ── 1h base (730 days) → also feeds 30m via downsample ───────────────────
    print(f"  Downloading 1h (730d)...")
    try:
        raw1h = yf.download(yf_symbol, period="730d", interval="1h",
                            progress=False, auto_adjust=True)
        if raw1h is None or len(raw1h) == 0:
            print(f"  [WARN] No 1h data returned")
            raw1h = None
        else:
            raw1h = _normalise(raw1h)
            raw1h = _add_stubs(raw1h)
            print(f"  1h data: {len(raw1h)} bars "
                  f"({raw1h.index[0].date()} – {raw1h.index[-1].date()})")
    except Exception as e:
        print(f"  [ERROR] 1h download failed: {e}")
        raw1h = None

    if raw1h is not None and not dry_run:
        # 1h bars (for very long strategies — not in WFO runner but useful for V9)
        _merge_parquet(raw1h, BAR_DIR / f"{base_sym}_bars_60m.parquet", verbose)
        # Resample 1h → 30m supplement (adds history beyond 60d)
        df30h = _resample_to(raw1h, 30)
        _merge_parquet(df30h, BAR_DIR / f"{base_sym}_bars_30m.parquet", verbose)

    if dry_run:
        print(f"  [DRY-RUN] Would write 5m/3m/15m/30m/60m parquets for {base_sym}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbol", help="Single symbol to download (GC/SI/ES/NQ)")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    targets = SYMBOLS.items()
    if args.symbol:
        targets = [(k, v) for k, v in SYMBOLS.items() if v == args.symbol.upper()]
        if not targets:
            print(f"Unknown symbol: {args.symbol}. Use GC, SI, ES, or NQ.")
            sys.exit(1)

    print(f"\nFortress Historical Data Bootstrap")
    print(f"Target: {BAR_DIR}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}\n")

    for yf_sym, base_sym in targets:
        try:
            download_and_store(yf_sym, base_sym, verbose=args.verbose, dry_run=args.dry_run)
        except Exception as e:
            print(f"[ERROR] {base_sym}: {e}")

    print(f"\nDone. Bar files in {BAR_DIR}")
    if not args.dry_run:
        print("Run tick_runner_v678.py next to backtest V6/V7/V8 strategies.")


if __name__ == "__main__":
    main()
