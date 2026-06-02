"""
L2 Feature Engine — master processor.

Takes raw mbp-10 CSV files (from Databento GLBX.MDP3) and outputs
1-minute bar DataFrames enriched with L2 order book features.

Usage:
    from src.l2 import build_l2_bars
    bars = build_l2_bars("GC", chunksize=500_000)
    bars.to_parquet("01_data/tick_bars/GC_bars_l2_1m.parquet")
"""
from __future__ import annotations

import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent.parent

sys.path.insert(0, str(ROOT / "04_codebase"))

from src.l2.ofi        import ofi_by_minute
from src.l2.imbalance  import imbalance_snapshots, depth_imbalance
from src.l2.microprice import microprice_features_1m, compute_microprice, compute_spread
from src.l2.sweeps     import sweep_features_1m
from src.l2.absorption import absorption_features_1m

TICK_DIR = ROOT / "01_data" / "tick"
BAR_DIR  = ROOT / "01_data" / "tick_bars"

_TICK_FILE_MAP = {
    "GC": "GC_tick_mbp10.csv",
    "SI": "SI_tick_mbp10.csv",
}


def _parse_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """Parse raw CSV chunk: set DatetimeIndex, cast dtypes."""
    if "ts_event" in chunk.columns:
        chunk["ts_event"] = pd.to_datetime(chunk["ts_event"], utc=True)
        chunk = chunk.set_index("ts_event")
    elif not isinstance(chunk.index, pd.DatetimeIndex):
        chunk.index = pd.to_datetime(chunk.index, utc=True)

    if chunk.index.tz is None:
        chunk.index = chunk.index.tz_localize("UTC")

    chunk = chunk.sort_index()
    # Keep last update at each nanosecond-duplicate timestamp (book snapshots)
    if chunk.index.duplicated().any():
        chunk = chunk[~chunk.index.duplicated(keep="last")]
    return chunk


def _process_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """Compute all L2 features for one chunk, return 1-minute bars."""
    chunk = _parse_chunk(chunk)
    if chunk.empty:
        return pd.DataFrame()

    frames = {}

    # --- Core OHLCV from trade prices ---
    if "action" in chunk.columns and "price" in chunk.columns:
        trades = chunk[chunk["action"] == "T"]
        if not trades.empty:
            r_t = trades["price"].resample("1min")
            frames["open"]    = r_t.first()
            frames["high"]    = r_t.max()
            frames["low"]     = r_t.min()
            frames["close"]   = r_t.last()
            frames["n_trades"] = r_t.count()
            frames["volume"]  = trades["size"].resample("1min").sum() if "size" in trades.columns else 0

    # --- OFI ---
    try:
        frames["ofi_1"]  = ofi_by_minute(chunk, levels=1)
        frames["ofi_5"]  = ofi_by_minute(chunk, levels=5)
    except Exception:
        pass

    # --- Depth imbalance ---
    try:
        imbal_df = imbalance_snapshots(chunk, freq="1min", levels=5, weighted=True)
        for col in imbal_df.columns:
            frames[col] = imbal_df[col]
    except Exception:
        pass

    # --- Microprice & spread ---
    try:
        mp_df = microprice_features_1m(chunk)
        for col in mp_df.columns:
            frames[col] = mp_df[col]
    except Exception:
        pass

    # --- Sweeps ---
    try:
        sw_df = sweep_features_1m(chunk)
        for col in sw_df.columns:
            frames[col] = sw_df[col]
    except Exception:
        pass

    # --- Absorption ---
    try:
        abs_df = absorption_features_1m(chunk)
        for col in abs_df.columns:
            frames[col] = abs_df[col]
    except Exception:
        pass

    if not frames:
        return pd.DataFrame()

    result = pd.DataFrame(frames)
    result = result[~result.index.duplicated(keep="last")]
    return result


class L2FeatureEngine:
    """
    Streaming processor for large mbp-10 CSV files.

    Processes the file in chunks to keep memory usage manageable.
    """

    def __init__(
        self,
        symbol: str,
        chunksize: int = 500_000,
        tick_file: Optional[Path] = None,
    ):
        self.symbol    = symbol
        self.chunksize = chunksize
        self.tick_file = tick_file or (TICK_DIR / _TICK_FILE_MAP.get(symbol, f"{symbol}_tick_mbp10.csv"))

        if not self.tick_file.exists():
            raise FileNotFoundError(f"Tick file not found: {self.tick_file}")

    def build(self, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        """
        Stream the tick file chunk by chunk, build 1-minute L2 bars.

        Args:
            start: Optional ISO date string to filter from.
            end:   Optional ISO date string to filter to.

        Returns:
            Full 1-minute bar DataFrame with all L2 features.
        """
        all_bars: list[pd.DataFrame] = []

        print(f"[L2Engine:{self.symbol}] Processing {self.tick_file.name} ...")

        reader = pd.read_csv(
            self.tick_file,
            chunksize=self.chunksize,
            low_memory=False,
        )

        n_chunks = 0
        for chunk in reader:
            bars = _process_chunk(chunk)
            if bars.empty:
                continue

            if start:
                bars = bars[bars.index >= pd.Timestamp(start, tz="UTC")]
            if end:
                bars = bars[bars.index <= pd.Timestamp(end, tz="UTC")]

            if not bars.empty:
                all_bars.append(bars)
            n_chunks += 1
            if n_chunks % 10 == 0:
                print(f"  ... processed {n_chunks} chunks")

        if not all_bars:
            print(f"[L2Engine:{self.symbol}] No bars produced.")
            return pd.DataFrame()

        combined = pd.concat(all_bars)
        combined = combined.groupby(combined.index).last()
        combined = combined.sort_index()

        print(f"[L2Engine:{self.symbol}] Done. {len(combined):,} bars "
              f"({combined.index.min().date()} to {combined.index.max().date()})")
        return combined


def build_l2_bars(
    symbol: str,
    chunksize: int = 500_000,
    start: Optional[str] = None,
    end:   Optional[str] = None,
    save:  bool = True,
) -> pd.DataFrame:
    """
    Build and optionally save 1-minute L2 feature bars for a symbol.

    Saves to: 01_data/tick_bars/{symbol}_bars_l2_1m.parquet
    """
    engine = L2FeatureEngine(symbol, chunksize=chunksize)
    bars = engine.build(start=start, end=end)

    if save and not bars.empty:
        out_path = BAR_DIR / f"{symbol}_bars_l2_1m.parquet"
        bars.to_parquet(out_path)
        print(f"[L2Engine:{symbol}] Saved → {out_path}")

    return bars
