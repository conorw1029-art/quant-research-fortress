"""
tick_live_bar_reader.py — NinjaTrader Live Bar Reader
=====================================================
Reads JSONL bar files written by FortressBarWriter.cs (NinjaTrader 8)
and appends new completed bars to the existing parquet files used by
tick_live_executor.py.

Run this as a background thread OR as a standalone process in parallel
with the executor. The executor auto-imports this if the live/ directory
exists.

File layout:
  01_data/tick_bars/live/GC_1m_live.jsonl   <- written by NinjaTrader
  01_data/tick_bars/live/GC_3m_live.jsonl
  01_data/tick_bars/live/ES_1m_live.jsonl
  ...

  01_data/tick_bars/GC_bars_1m.parquet      <- appended by this script
  01_data/tick_bars/ES_bars_1m.parquet
  ...

Usage (standalone, runs forever):
  python 04_codebase/tick_live_bar_reader.py

Usage (one-shot, called from executor before each poll):
  from tick_live_bar_reader import append_live_bars
  append_live_bars()  # no-ops gracefully if live/ dir doesn't exist
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
LIVE_DIR = BAR_DIR / "live"

# Columns expected by the executor (must match parquet schema)
REQUIRED_COLS = [
    "open", "high", "low", "close", "volume",
    "buy_vol", "sell_vol", "cvd_delta", "cvd",
    "n_trades",
]

OPTIONAL_COLS = [
    "spread", "bid_sz_00", "ask_sz_00", "book_pressure", "obi_5", "microprice",
    "bid_px_00", "ask_px_00", "bid_sz_01", "ask_sz_01",
    "bid_px_01", "ask_px_01", "bid_sz_02", "ask_sz_02",
    "bid_px_02", "ask_px_02", "bid_sz_03", "ask_sz_03",
    "bid_px_03", "ask_px_03", "bid_sz_04", "ask_sz_04",
    "bid_px_04", "ask_px_04",
    # L2 aliases used by V10 strategies
    "imbal_L5_last", "microprice_last", "spread_mean", "bid_sz_mean", "ask_sz_mean",
]


# ── Core function ──────────────────────────────────────────────────────────────

def append_live_bars(live_dir: Path = LIVE_DIR, bar_dir: Path = BAR_DIR,
                     verbose: bool = False) -> dict[str, int]:
    """
    Scan the live/ directory for JSONL files, read any bars not yet in the
    corresponding parquet, and append them.

    Returns dict of {filename: rows_appended}.
    No-ops gracefully if the live/ directory doesn't exist.
    """
    if not live_dir.exists():
        return {}

    appended: dict[str, int] = {}

    for jsonl_path in sorted(live_dir.glob("*_live.jsonl")):
        name = jsonl_path.stem  # e.g. "GC_1m_live"
        parts = name.replace("_live", "").split("_")  # ["GC", "1m"]
        if len(parts) != 2:
            continue
        symbol, bar_label = parts[0], parts[1]
        bar_min = bar_label.replace("m", "")
        if not bar_min.isdigit():
            continue

        parquet_path = bar_dir / f"{symbol}_bars_{bar_min}m.parquet"
        n = _merge_jsonl_into_parquet(jsonl_path, parquet_path, symbol, verbose=verbose)
        if n > 0:
            appended[jsonl_path.name] = n
            if verbose:
                print(f"  [LiveReader] {symbol} {bar_min}m: +{n} bars -> {parquet_path.name}")

    return appended


def _merge_jsonl_into_parquet(jsonl_path: Path, parquet_path: Path,
                               symbol: str, verbose: bool = False) -> int:
    """Read JSONL, find rows newer than the parquet, append. Returns rows added."""

    # ── Read the JSONL file ────────────────────────────────────────────────────
    rows = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        if verbose:
            print(f"  [LiveReader] Cannot read {jsonl_path.name}: {e}")
        return 0

    if not rows:
        return 0

    # ── Parse into DataFrame ───────────────────────────────────────────────────
    new_df = pd.DataFrame(rows)
    new_df["ts"] = pd.to_datetime(new_df["ts"], utc=True)
    new_df = new_df.set_index("ts").sort_index()

    # Ensure numeric columns
    for col in new_df.columns:
        try:
            new_df[col] = pd.to_numeric(new_df[col], errors="coerce")
        except Exception:
            pass

    # Add L2 aliases if underlying columns exist
    if "obi_5" in new_df.columns:
        new_df["imbal_L5_last"] = new_df["obi_5"]
    if "microprice" in new_df.columns:
        new_df["microprice_last"] = new_df["microprice"]
    if "spread" in new_df.columns:
        new_df["spread_mean"] = new_df["spread"]
    if "bid_sz_00" in new_df.columns:
        new_df["bid_sz_mean"] = new_df["bid_sz_00"].astype(float)
    if "ask_sz_00" in new_df.columns:
        new_df["ask_sz_mean"] = new_df["ask_sz_00"].astype(float)

    # ── Find the cutoff timestamp ──────────────────────────────────────────────
    last_ts = None
    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path, columns=[])
            existing.index = pd.to_datetime(existing.index, utc=True)
            if len(existing) > 0:
                last_ts = existing.index.max()
        except Exception:
            pass

    # Filter to only new bars
    if last_ts is not None:
        to_add = new_df[new_df.index > last_ts]
    else:
        to_add = new_df

    if len(to_add) == 0:
        return 0

    # ── Append to parquet ──────────────────────────────────────────────────────
    if parquet_path.exists():
        try:
            existing_full = pd.read_parquet(parquet_path)
            existing_full.index = pd.to_datetime(existing_full.index, utc=True)
            # Align columns — add missing columns as NaN
            for col in to_add.columns:
                if col not in existing_full.columns:
                    existing_full[col] = np.nan
            for col in existing_full.columns:
                if col not in to_add.columns:
                    to_add[col] = np.nan
            combined = pd.concat([existing_full, to_add]).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
        except Exception as e:
            if verbose:
                print(f"  [LiveReader] Parquet read error ({parquet_path.name}): {e}")
            combined = to_add
    else:
        combined = to_add

    try:
        combined.to_parquet(parquet_path, engine="pyarrow", compression="snappy")
    except Exception as e:
        if verbose:
            print(f"  [LiveReader] Parquet write error ({parquet_path.name}): {e}")
        return 0

    return len(to_add)


# ── Stale data check ───────────────────────────────────────────────────────────

def get_stale_feeds(live_dir: Path = LIVE_DIR, max_age_minutes: int = 10) -> list[str]:
    """
    Return list of feed names that haven't been updated in max_age_minutes.
    Only relevant during market hours (Mon-Fri, not 22:00-23:00 UTC gap).
    Returns empty list if live/ doesn't exist.
    """
    if not live_dir.exists():
        return []

    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour    = now.hour

    # Skip check outside market hours (Sat, Sun, or the daily break)
    if weekday in (5, 6):
        return []
    if hour == 22:  # Daily 60-min close window
        return []

    stale = []
    for f in live_dir.glob("*_live.jsonl"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            age   = (now - mtime).total_seconds() / 60
            if age > max_age_minutes:
                stale.append(f"{f.stem.replace('_live','')} ({age:.0f}m old)")
        except Exception:
            pass
    return stale


# ── CLI: standalone poller ─────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live bar reader — JSONL -> parquet")
    parser.add_argument("--interval", type=int, default=30,
                        help="Poll interval in seconds (default 30)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"[LiveReader] Watching {LIVE_DIR}")
    print(f"[LiveReader] Appending to parquets in {BAR_DIR}")
    print(f"[LiveReader] Poll interval: {args.interval}s  (Ctrl+C to stop)")
    print()

    while True:
        try:
            added = append_live_bars(verbose=args.verbose)
            if added:
                total = sum(added.values())
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                      f"Appended {total} bars: {list(added.keys())}")
            stale = get_stale_feeds()
            if stale:
                print(f"  WARNING: Stale feeds: {stale}")
        except KeyboardInterrupt:
            print("[LiveReader] Stopped.")
            break
        except Exception as e:
            print(f"[LiveReader] Error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
