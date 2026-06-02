"""
tick_databento_download_es_nq.py
Downloads ES and NQ ohlcv-1m from 2020-01-01 to 2025-11-30,
converts to parquet, and appends to existing bar files.

Cost estimate confirmed: ~$15.13 total (within $125 budget).
Run: venv_new/Scripts/python.exe 04_codebase/tick_databento_download_es_nq.py
"""
from __future__ import annotations
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import databento as db
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT     = Path(__file__).parent.parent
BAR_DIR  = ROOT / "01_data" / "tick_bars"
RAW_DIR  = ROOT / "01_data" / "raw_db"
RAW_DIR.mkdir(parents=True, exist_ok=True)

KEY      = os.environ.get("DATABENTO_API_KEY", "db-aNjTGW3AEvvdKn7fUtNyDktFAmrv5")
client   = db.Historical(key=KEY)

SYMBOLS   = {"ES": "ES.c.0", "NQ": "NQ.c.0"}
START     = "2020-01-01"
END       = "2025-12-01"
DATASET   = "GLBX.MDP3"
SCHEMA    = "ohlcv-1m"
STYPE     = "continuous"

BAR_TIMEFRAMES = [1, 3, 5, 15, 30]


def _resample(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    r = df1m.resample(rule, closed="left", label="left")
    out = pd.DataFrame({
        "open":   r["open"].first(),
        "high":   r["high"].max(),
        "low":    r["low"].min(),
        "close":  r["close"].last(),
        "volume": r["volume"].sum(),
        "buy_vol":   r["buy_vol"].sum()    if "buy_vol"   in df1m.columns else 0,
        "sell_vol":  r["sell_vol"].sum()   if "sell_vol"  in df1m.columns else 0,
        "cvd_delta": r["cvd_delta"].sum()  if "cvd_delta" in df1m.columns else 0,
        "cvd":       r["cvd_delta"].sum()  if "cvd_delta" in df1m.columns else 0,  # approx
        "n_trades":  r["n_trades"].sum()   if "n_trades"  in df1m.columns else 0,
        "trade_rate": r["n_trades"].mean() if "n_trades"  in df1m.columns else 0,
        "large_buys":  0,
        "large_sells": 0,
    })
    return out.dropna(subset=["open"])


def _build_hist_bars_1m(data: db.DBNStore) -> pd.DataFrame:
    """Convert ohlcv DBNStore to 1m DataFrame matching existing schema."""
    df = data.to_df()
    if df.empty:
        return df

    # Rename Databento columns to our schema
    col_map = {"open": "open", "high": "high", "low": "low",
               "close": "close", "volume": "volume"}
    df = df[[c for c in col_map if c in df.columns]].rename(columns=col_map)

    # Ensure index is UTC datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    df.index.name = "ts_event"

    # Add empty tick-derived columns so schema matches
    for col in ("buy_vol", "sell_vol", "cvd_delta", "cvd",
                "n_trades", "trade_rate", "large_buys", "large_sells"):
        df[col] = 0

    return df.sort_index()


def _merge_and_save(sym: str, hist_1m: pd.DataFrame) -> None:
    for tf in BAR_TIMEFRAMES:
        fname   = f"{sym}_bars_{tf}m.parquet"
        fpath   = BAR_DIR / fname

        hist = hist_1m if tf == 1 else _resample(hist_1m, tf)

        if fpath.exists():
            existing = pd.read_parquet(fpath)
            # Align columns
            for col in hist.columns:
                if col not in existing.columns:
                    existing[col] = 0
            for col in existing.columns:
                if col not in hist.columns:
                    hist[col] = 0

            # Only keep history that predates existing data
            existing_start = existing.index.min()
            hist_pre = hist[hist.index < existing_start]

            if hist_pre.empty:
                print(f"  {fname}: no new rows before {existing_start.date()}, skipping")
                continue

            combined = pd.concat([hist_pre[existing.columns], existing])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = hist

        combined.to_parquet(fpath)
        print(f"  {fname}: saved {len(combined):,} rows "
              f"({combined.index.min().date()} to {combined.index.max().date()})")


def main() -> None:
    print("=" * 60)
    print(f" Databento download — {SCHEMA} — {START} to {END}")
    print("=" * 60)

    for short, sym in SYMBOLS.items():
        print(f"\n[{short}] Downloading {sym} ...")

        # Show cost first
        cost = client.metadata.get_cost(
            dataset=DATASET, symbols=[sym], schema=SCHEMA,
            stype_in=STYPE, start=START, end=END,
        )
        print(f"  Cost: ${cost:.4f}")

        # Download
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[sym],
            schema=SCHEMA,
            stype_in=STYPE,
            start=START,
            end=END,
        )

        hist_1m = _build_hist_bars_1m(data)
        print(f"  Downloaded {len(hist_1m):,} 1m bars "
              f"({hist_1m.index.min().date()} to {hist_1m.index.max().date()})")

        _merge_and_save(short, hist_1m)

    print("\n[DONE] All bar files updated.")


if __name__ == "__main__":
    main()
