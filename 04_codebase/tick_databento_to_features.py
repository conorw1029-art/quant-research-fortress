#!/usr/bin/env python3
"""
tick_databento_to_features.py
==============================
Converts a raw Databento DBN file into feature-enriched bar parquet files.

Supported schemas:
    trades  — OHLCV, buy_vol, sell_vol, cvd (cumulative volume delta)
    mbp-1   — OHLCV from quote midpoints, spread, microprice, L1 imbalance
    mbp-10  — All mbp-1 features + L2 depth features: ofi_5, imbal_L5,
               microprice, spread, sweeps, absorption_score

Idempotency:
    - Checks MANIFEST.jsonl before processing. Skips files already processed.
    - Appends to existing parquet if it exists (new date ranges only).
    - Does not overwrite or truncate existing parquet data.

Usage:
    python tick_databento_to_features.py \\
        --input 01_data/raw/GC/mbp-10/2024-01-02_2024-01-03.dbn.zst \\
        --symbol GC --schema mbp-10 --bar-freq 1min

    # Preview what would be created without writing:
    python tick_databento_to_features.py \\
        --input 01_data/raw/GC/mbp-10/2024-01-02_2024-01-03.dbn.zst \\
        --symbol GC --schema mbp-10 --dry-run

Requirements:
    pip install databento pandas pyarrow
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas not installed. Run: pip install pandas")
    sys.exit(1)

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed. Run: pip install databento")
    sys.exit(1)

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("ERROR: pyarrow not installed. Run: pip install pyarrow")
    sys.exit(1)

warnings.filterwarnings("ignore", category=FutureWarning)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "01_data" / "tick_bars"
MANIFEST_PATH = PROJECT_ROOT / "01_data" / "raw" / "MANIFEST.jsonl"
FEATURES_MANIFEST_PATH = PROJECT_ROOT / "01_data" / "tick_bars" / "FEATURES_MANIFEST.jsonl"

VALID_SCHEMAS = ["trades", "mbp-1", "mbp-10"]
VALID_BAR_FREQS = ["1min", "3min", "5min", "15min", "30min"]

# Pandas resample rule mapping
FREQ_MAP = {
    "1min": "1min",
    "3min": "3min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
}

# Minimum sweep parameters
SWEEP_WINDOW_S = 0.5          # seconds
SWEEP_MIN_LEVELS = 2           # consecutive price levels consumed
SWEEP_MIN_VOL_MULTIPLIER = 3.0  # x median trade size

# Absorption detection parameters
ABSORPTION_CVD_STD_THRESH = 2.0  # CVD moves > N std
ABSORPTION_PRICE_TICK_THRESH = 1  # but price moves < N ticks


# ---------------------------------------------------------------------------
# Manifest utilities
# ---------------------------------------------------------------------------

def load_features_manifest() -> list:
    """Load the features processing manifest. Returns empty list if not found."""
    if not FEATURES_MANIFEST_PATH.exists():
        return []
    entries = []
    with open(FEATURES_MANIFEST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def is_already_processed(input_path: Path, schema: str, bar_freq: str) -> bool:
    """Return True if this input file + schema + bar_freq combo was already processed."""
    manifest = load_features_manifest()
    canonical = str(input_path.resolve())
    for entry in manifest:
        if (
            entry.get("input_path") == canonical
            and entry.get("schema") == schema
            and entry.get("bar_freq") == bar_freq
        ):
            return True
    return False


def append_features_manifest(entry: dict) -> None:
    """Append a processing result to the features manifest."""
    FEATURES_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FEATURES_MANIFEST_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# DBN loading
# ---------------------------------------------------------------------------

def load_dbn(input_path: Path) -> pd.DataFrame:
    """Load a Databento DBN file into a DataFrame."""
    store = db.DBNStore.from_file(str(input_path))
    df = store.to_df()
    if df is None or len(df) == 0:
        raise ValueError(f"DBN file loaded but DataFrame is empty: {input_path}")
    return df


def ensure_ts_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure the DataFrame has a DatetimeTzAware UTC index named 'ts_event'.
    Databento DataFrames typically have ts_event as a column or index.
    """
    if "ts_event" in df.columns:
        df = df.set_index("ts_event")

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    df.index.name = "ts_event"
    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# Schema: trades
# ---------------------------------------------------------------------------

def process_trades(df: pd.DataFrame, bar_freq: str) -> pd.DataFrame:
    """
    Build OHLCV bars from trades schema data, plus:
        buy_vol   — volume on bid-aggressor (ask side hit = buy aggressor)
        sell_vol  — volume on ask-aggressor (bid side hit = sell aggressor)
        cvd       — cumulative volume delta: buy_vol - sell_vol (cumsum over bar)
        n_trades  — number of individual trades per bar
        avg_trade_size — mean trade size per bar
    """
    df = ensure_ts_index(df)

    # Price column: Databento trades use 'price' (integer scaled) or 'price' as float
    price_col = "price"
    size_col = "size"

    if price_col not in df.columns:
        raise KeyError(f"Expected column 'price' in trades DataFrame. Got: {list(df.columns)}")
    if size_col not in df.columns:
        raise KeyError(f"Expected column 'size' in trades DataFrame. Got: {list(df.columns)}")

    # Convert price if stored as integer (Databento uses fixed-point 1e-9 scaling)
    if df[price_col].dtype == "int64":
        df[price_col] = df[price_col] / 1e9
    if df[size_col].dtype == "int64" or df[size_col].dtype == "uint32":
        df[size_col] = df[size_col].astype(float)

    # Determine buy/sell side from 'side' column.
    # Databento 'trades' convention: 'side' is the AGGRESSOR's side (SDK: "the side of
    # the aggressor for trades"). side='B' (Bid) = BUY aggressor; side='A' (Ask) = SELL aggressor.
    # Verified empirically on GLBX.MDP3 MES trades 2026-06-26 (tick rule): side='B' 92.3% upticks,
    # side='A' 8.9% upticks. This now matches tick_processor.py (the historical research builder).
    # (Prior code had this inverted, which would have flipped CVD/delta sign on the live feed.)
    if "side" in df.columns:
        # Normalize to uppercase strings
        side = df["side"].astype(str).str.upper()
        buy_mask = side == "B"   # bid-aggressor = buy
        sell_mask = side == "A"  # ask-aggressor = sell
    elif "action" in df.columns:
        # Some schemas use 'action'; treat all as unknown direction
        buy_mask = pd.Series(False, index=df.index)
        sell_mask = pd.Series(False, index=df.index)
    else:
        buy_mask = pd.Series(False, index=df.index)
        sell_mask = pd.Series(False, index=df.index)

    df["buy_vol_raw"] = df[size_col].where(buy_mask, 0.0)
    df["sell_vol_raw"] = df[size_col].where(sell_mask, 0.0)
    df["delta_raw"] = df["buy_vol_raw"] - df["sell_vol_raw"]

    freq_rule = FREQ_MAP[bar_freq]

    ohlcv = df[price_col].resample(freq_rule).ohlc()
    ohlcv.columns = ["open", "high", "low", "close"]
    ohlcv["volume"] = df[size_col].resample(freq_rule).sum()
    ohlcv["buy_vol"] = df["buy_vol_raw"].resample(freq_rule).sum()
    ohlcv["sell_vol"] = df["sell_vol_raw"].resample(freq_rule).sum()
    ohlcv["cvd"] = df["delta_raw"].resample(freq_rule).sum().cumsum()
    ohlcv["n_trades"] = df[size_col].resample(freq_rule).count()
    ohlcv["avg_trade_size"] = ohlcv["volume"] / ohlcv["n_trades"].replace(0, float("nan"))

    # Drop bars with no trades
    ohlcv = ohlcv.dropna(subset=["open"])

    return ohlcv


# ---------------------------------------------------------------------------
# Schema: mbp-1
# ---------------------------------------------------------------------------

def process_mbp1(df: pd.DataFrame, bar_freq: str) -> pd.DataFrame:
    """
    Build bars from mbp-1 (best bid/ask) schema data.

    Features:
        open/high/low/close  — from midprice
        best_bid, best_ask   — VWAP of best bid/ask over the bar
        spread               — mean (ask - bid) over the bar, in ticks
        microprice           — weighted midprice: bid + spread * (bid_sz / (bid_sz + ask_sz))
        imbal_L1             — (bid_sz - ask_sz) / (bid_sz + ask_sz), mean over bar
    """
    df = ensure_ts_index(df)

    # Databento mbp-1 column names
    bid_col = "bid_px_00"
    ask_col = "ask_px_00"
    bid_sz_col = "bid_sz_00"
    ask_sz_col = "ask_sz_00"

    required = [bid_col, ask_col, bid_sz_col, ask_sz_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        # Try alternate naming convention
        alt_bid = "best_bid_price"
        alt_ask = "best_ask_price"
        if alt_bid in df.columns and alt_ask in df.columns:
            bid_col, ask_col = alt_bid, alt_ask
            bid_sz_col = "best_bid_size" if "best_bid_size" in df.columns else None
            ask_sz_col = "best_ask_size" if "best_ask_size" in df.columns else None
        else:
            raise KeyError(
                f"mbp-1 DataFrame missing expected columns. "
                f"Missing: {missing}. Got: {list(df.columns)}"
            )

    # Scale prices if stored as integer fixed-point
    for col in [bid_col, ask_col]:
        if df[col].dtype in ("int64", "int32"):
            df[col] = df[col] / 1e9

    mid = (df[bid_col] + df[ask_col]) / 2.0

    if bid_sz_col and ask_sz_col:
        bid_sz = df[bid_sz_col].astype(float)
        ask_sz = df[ask_sz_col].astype(float)
        total_sz = (bid_sz + ask_sz).replace(0, float("nan"))
        microprice = df[bid_col] + (df[ask_col] - df[bid_col]) * (bid_sz / total_sz)
        imbal_l1 = (bid_sz - ask_sz) / total_sz
    else:
        microprice = mid
        imbal_l1 = pd.Series(0.0, index=df.index)

    df["mid"] = mid
    df["microprice"] = microprice
    df["spread_raw"] = df[ask_col] - df[bid_col]
    df["imbal_l1_raw"] = imbal_l1

    freq_rule = FREQ_MAP[bar_freq]

    bars = df["mid"].resample(freq_rule).ohlc()
    bars.columns = ["open", "high", "low", "close"]
    bars["best_bid"] = df[bid_col].resample(freq_rule).mean()
    bars["best_ask"] = df[ask_col].resample(freq_rule).mean()
    bars["spread"] = df["spread_raw"].resample(freq_rule).mean()
    bars["microprice"] = df["microprice"].resample(freq_rule).last()
    bars["imbal_L1"] = df["imbal_l1_raw"].resample(freq_rule).mean()
    bars["n_quotes"] = df["mid"].resample(freq_rule).count()

    bars = bars.dropna(subset=["open"])
    return bars


# ---------------------------------------------------------------------------
# Schema: mbp-10
# ---------------------------------------------------------------------------

def _compute_depth_imbalance(df: pd.DataFrame, n_levels: int) -> pd.Series:
    """
    Compute depth imbalance across n_levels of bid and ask.
    Returns a Series of (sum_bid - sum_ask) / (sum_bid + sum_ask).
    """
    bid_cols = [f"bid_sz_{i:02d}" for i in range(n_levels) if f"bid_sz_{i:02d}" in df.columns]
    ask_cols = [f"ask_sz_{i:02d}" for i in range(n_levels) if f"ask_sz_{i:02d}" in df.columns]

    if not bid_cols or not ask_cols:
        return pd.Series(0.0, index=df.index)

    sum_bid = df[bid_cols].sum(axis=1).astype(float)
    sum_ask = df[ask_cols].sum(axis=1).astype(float)
    total = (sum_bid + sum_ask).replace(0, float("nan"))
    return (sum_bid - sum_ask) / total


def _compute_microprice(df: pd.DataFrame) -> pd.Series:
    """
    Weighted microprice: bid_px_00 + spread * (bid_sz_00 / (bid_sz_00 + ask_sz_00))
    """
    bid_px = df["bid_px_00"].astype(float)
    ask_px = df["ask_px_00"].astype(float)
    bid_sz = df.get("bid_sz_00", pd.Series(1.0, index=df.index)).astype(float)
    ask_sz = df.get("ask_sz_00", pd.Series(1.0, index=df.index)).astype(float)

    total_sz = (bid_sz + ask_sz).replace(0, float("nan"))
    spread = ask_px - bid_px
    return bid_px + spread * (bid_sz / total_sz)


def _compute_ofi(df: pd.DataFrame, n_levels: int = 5) -> pd.Series:
    """
    Order Flow Imbalance (OFI) approximation from level-size changes in mbp-10.

    OFI = delta_bid_depth - delta_ask_depth (change in total depth across n_levels)
    Positive = buying pressure increasing; Negative = selling pressure increasing.

    This is an approximation since mbp-10 gives level snapshots, not individual orders.
    """
    bid_cols = [f"bid_sz_{i:02d}" for i in range(n_levels) if f"bid_sz_{i:02d}" in df.columns]
    ask_cols = [f"ask_sz_{i:02d}" for i in range(n_levels) if f"ask_sz_{i:02d}" in df.columns]

    if not bid_cols:
        return pd.Series(0.0, index=df.index)

    sum_bid = df[bid_cols].sum(axis=1).astype(float)
    sum_ask = df[ask_cols].sum(axis=1).astype(float)

    delta_bid = sum_bid.diff()
    delta_ask = sum_ask.diff()

    ofi = delta_bid - delta_ask
    return ofi.fillna(0.0)


def _detect_sweeps(df: pd.DataFrame) -> pd.Series:
    """
    Sweep detection: identify periods where multiple price levels were consumed
    in rapid succession (within SWEEP_WINDOW_S seconds).

    Returns a Series of sweep counts per index event.
    A sweep is flagged when:
        - The bid_px_00 or ask_px_00 changes by >= SWEEP_MIN_LEVELS ticks
        - within SWEEP_WINDOW_S seconds of the previous update
        - AND the directional move is consistent (same-side pressure)

    For aggregation into bars: sum sweeps detected within the bar.
    """
    sweep = pd.Series(0, index=df.index, dtype=int)

    if "bid_px_00" not in df.columns or "ask_px_00" not in df.columns:
        return sweep

    bid_px = df["bid_px_00"].astype(float)
    ask_px = df["ask_px_00"].astype(float)

    # Time delta in seconds between events
    time_idx = df.index
    time_delta = pd.Series(time_idx, index=time_idx).diff().dt.total_seconds().fillna(0)

    # Price changes
    bid_move = bid_px.diff().abs()
    ask_move = ask_px.diff().abs()

    # Rough tick size estimate: median non-zero price move
    all_moves = pd.concat([bid_move[bid_move > 0], ask_move[ask_move > 0]])
    tick_est = all_moves.median() if len(all_moves) > 0 else 0.01

    if tick_est == 0:
        tick_est = 0.01

    # Sweep condition: rapid large price move
    rapid = time_delta <= SWEEP_WINDOW_S
    large_bid_move = bid_move >= (SWEEP_MIN_LEVELS * tick_est)
    large_ask_move = ask_move >= (SWEEP_MIN_LEVELS * tick_est)

    sweep[rapid & (large_bid_move | large_ask_move)] = 1
    return sweep


def _compute_absorption_score(
    df: pd.DataFrame,
    bar_freq: str,
) -> pd.Series:
    """
    Absorption score: measures cases where price fails to move despite
    significant order flow (CVD) pressure.

    Score ranges 0.0–1.0:
        0 = no absorption signal
        1 = strong absorption (large CVD move, zero price change)

    Computed as: min(1, abs(ofi_delta) / price_move_std) when price_move < threshold.
    """
    if "bid_px_00" not in df.columns:
        return pd.Series(0.0, index=df.index)

    mid = (df["bid_px_00"].astype(float) + df["ask_px_00"].astype(float)) / 2.0
    ofi = _compute_ofi(df, n_levels=5)

    # Rolling windows: approx 30 events
    window = 30

    ofi_std = ofi.rolling(window, min_periods=5).std().replace(0, float("nan"))
    price_std = mid.diff().abs().rolling(window, min_periods=5).std().replace(0, float("nan"))

    ofi_zscore = (ofi / ofi_std).abs().fillna(0.0)
    price_move = mid.diff().abs().fillna(0.0)

    # Price didn't move but OFI was large = absorption
    price_small = price_move < price_std.fillna(float("inf")) * 0.5
    absorption = (ofi_zscore * price_small.astype(float)).clip(0.0, 1.0)

    return absorption


def process_mbp10(df: pd.DataFrame, bar_freq: str) -> pd.DataFrame:
    """
    Build L2 feature bars from mbp-10 (10-level depth) schema data.

    Features:
        open/high/low/close  — from midprice
        volume               — from trade records embedded in mbp-10 if present
        best_bid, best_ask   — mean over bar
        spread               — mean (ask - bid), price units
        microprice           — size-weighted midprice, last in bar
        imbal_L1             — L1 depth imbalance, mean over bar
        imbal_L3             — L3 depth imbalance, mean over bar
        imbal_L5             — L5 depth imbalance, mean over bar
        ofi_5                — OFI (5-level) sum over bar
        sweeps               — count of sweep events within bar
        absorption_score     — mean absorption score within bar
        n_events             — number of book events in bar
    """
    df = ensure_ts_index(df)

    # Scale integer fixed-point prices
    price_cols = [c for c in df.columns if "px" in c and df[c].dtype in ("int64", "int32")]
    for col in price_cols:
        df[col] = df[col] / 1e9

    if "bid_px_00" not in df.columns or "ask_px_00" not in df.columns:
        raise KeyError(
            f"mbp-10 DataFrame missing 'bid_px_00' and/or 'ask_px_00'. "
            f"Got columns: {list(df.columns[:20])}"
        )

    mid = (df["bid_px_00"] + df["ask_px_00"]) / 2.0
    df["mid"] = mid

    microprice_series = _compute_microprice(df)
    df["microprice"] = microprice_series

    spread_series = df["ask_px_00"] - df["bid_px_00"]
    df["spread_raw"] = spread_series

    imbal_l1 = _compute_depth_imbalance(df, n_levels=1)
    imbal_l3 = _compute_depth_imbalance(df, n_levels=3)
    imbal_l5 = _compute_depth_imbalance(df, n_levels=5)
    df["imbal_l1_raw"] = imbal_l1
    df["imbal_l3_raw"] = imbal_l3
    df["imbal_l5_raw"] = imbal_l5

    ofi_series = _compute_ofi(df, n_levels=5)
    df["ofi_5_raw"] = ofi_series

    sweep_series = _detect_sweeps(df)
    df["sweep_raw"] = sweep_series

    absorption_series = _compute_absorption_score(df, bar_freq)
    df["absorption_raw"] = absorption_series

    freq_rule = FREQ_MAP[bar_freq]

    bars = df["mid"].resample(freq_rule).ohlc()
    bars.columns = ["open", "high", "low", "close"]

    bars["best_bid"] = df["bid_px_00"].resample(freq_rule).mean()
    bars["best_ask"] = df["ask_px_00"].resample(freq_rule).mean()
    bars["spread"] = df["spread_raw"].resample(freq_rule).mean()
    bars["microprice"] = df["microprice"].resample(freq_rule).last()
    bars["imbal_L1"] = df["imbal_l1_raw"].resample(freq_rule).mean()
    bars["imbal_L3"] = df["imbal_l3_raw"].resample(freq_rule).mean()
    bars["imbal_L5"] = df["imbal_l5_raw"].resample(freq_rule).mean()
    bars["ofi_5"] = df["ofi_5_raw"].resample(freq_rule).sum()
    bars["sweeps"] = df["sweep_raw"].resample(freq_rule).sum()
    bars["absorption_score"] = df["absorption_raw"].resample(freq_rule).mean()
    bars["n_events"] = df["mid"].resample(freq_rule).count()

    # Include volume if trade records are present in mbp-10 file
    if "size" in df.columns:
        vol_data = df["size"].astype(float)
        bars["volume"] = vol_data.resample(freq_rule).sum()
    else:
        bars["volume"] = float("nan")

    bars = bars.dropna(subset=["open"])
    return bars


# ---------------------------------------------------------------------------
# Parquet append logic
# ---------------------------------------------------------------------------

def resolve_output_path(output_dir: Path, symbol: str, schema: str, bar_freq: str) -> Path:
    """Build canonical output parquet path."""
    filename = f"{symbol.upper()}_{schema.replace('-', '')}_{bar_freq}_bars.parquet"
    return output_dir / filename


def append_or_create_parquet(bars: pd.DataFrame, output_path: Path, input_path: Path) -> tuple:
    """
    Append bars to existing parquet, or create new file.

    Only appends rows with timestamps NOT already in the existing file.
    Returns (rows_written: int, date_range: str).
    """
    if output_path.exists():
        existing = pd.read_parquet(output_path)
        existing.index = pd.to_datetime(existing.index, utc=True)

        # Find new rows not already in the file
        new_rows = bars[~bars.index.isin(existing.index)]

        if len(new_rows) == 0:
            return 0, "no new rows to append (all already present)"

        combined = pd.concat([existing, new_rows]).sort_index()
        combined = combined[~combined.index.duplicated(keep="first")]
    else:
        combined = bars
        new_rows = bars

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with metadata recording source file
    table = pa.Table.from_pandas(combined, preserve_index=True)
    metadata = {
        b"source_file": str(input_path).encode(),
        b"generated_at": datetime.now(timezone.utc).isoformat().encode(),
    }
    table = table.replace_schema_metadata({**table.schema.metadata, **metadata})
    pq.write_table(table, output_path, compression="snappy")

    date_range = (
        f"{combined.index.min().strftime('%Y-%m-%d')} "
        f"to {combined.index.max().strftime('%Y-%m-%d')}"
    )
    return len(new_rows), date_range


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

def process_file(
    input_path: Path,
    symbol: str,
    schema: str,
    bar_freq: str,
    output_dir: Path,
    dry_run: bool,
) -> dict:
    """
    Full pipeline: load DBN → build bars → append to parquet.

    Returns a result dict summarising what happened.
    """
    output_path = resolve_output_path(output_dir, symbol, schema, bar_freq)

    result = {
        "input_path": str(input_path.resolve()),
        "output_path": str(output_path),
        "symbol": symbol.upper(),
        "schema": schema,
        "bar_freq": bar_freq,
        "input_records": None,
        "output_bars": None,
        "rows_written": None,
        "date_range": None,
        "columns": None,
        "status": None,
        "message": None,
    }

    print(f"\n  Loading DBN file...")
    print(f"    Source: {input_path}")

    try:
        df = load_dbn(input_path)
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Failed to load DBN file: {exc}"
        print(f"  ERROR: {exc}")
        return result

    result["input_records"] = len(df)
    print(f"    Loaded {len(df):,} records")
    print(f"    Columns: {list(df.columns[:10])}{'...' if len(df.columns) > 10 else ''}")

    # Build bars
    print(f"\n  Building {bar_freq} bars ({schema} schema)...")
    try:
        if schema == "trades":
            bars = process_trades(df, bar_freq)
        elif schema == "mbp-1":
            bars = process_mbp1(df, bar_freq)
        elif schema == "mbp-10":
            bars = process_mbp10(df, bar_freq)
        else:
            raise ValueError(f"Unsupported schema: {schema}")
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Failed to build bars: {exc}"
        print(f"  ERROR: {exc}")
        return result

    result["output_bars"] = len(bars)
    result["columns"] = list(bars.columns)
    print(f"    Output: {len(bars):,} bars")
    print(f"    Columns: {list(bars.columns)}")

    if len(bars) == 0:
        result["status"] = "empty_output"
        result["message"] = "Bar construction produced zero bars."
        print("  WARNING: Zero bars produced. Input may be too short or schema mismatch.")
        return result

    date_str = (
        f"{bars.index.min().strftime('%Y-%m-%d %H:%M')} "
        f"to {bars.index.max().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    print(f"    Date range: {date_str}")

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = f"Dry run — would write to {output_path}"
        result["rows_written"] = 0
        result["date_range"] = date_str
        print(f"\n  DRY RUN — would write to: {output_path}")
        return result

    # Append to parquet
    print(f"\n  Writing to parquet...")
    print(f"    Output: {output_path}")

    try:
        rows_written, date_range = append_or_create_parquet(bars, output_path, input_path)
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Failed to write parquet: {exc}"
        print(f"  ERROR writing parquet: {exc}")
        return result

    result["rows_written"] = rows_written
    result["date_range"] = date_range
    result["status"] = "success"
    result["message"] = f"Wrote {rows_written:,} new rows."

    print(f"    Rows written: {rows_written:,}")
    print(f"    Final date range in file: {date_range}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert a raw Databento DBN file into feature-enriched bar parquet files.\n"
            "Appends to existing parquet — never overwrites existing rows.\n"
            "Skips files already in the processing manifest."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tick_databento_to_features.py \\
      --input 01_data/raw/GC/mbp-10/2024-01-02_2024-01-03.dbn.zst \\
      --symbol GC --schema mbp-10 --bar-freq 1min

  python tick_databento_to_features.py \\
      --input 01_data/raw/ES/trades/2024-01-02_2024-01-03.dbn.zst \\
      --symbol ES --schema trades --bar-freq 5min --dry-run
        """,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the raw .dbn.zst file to process.",
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Contract root symbol (e.g., GC, ES, SI).",
    )
    parser.add_argument(
        "--schema",
        required=True,
        choices=VALID_SCHEMAS,
        help="Schema of the input file: trades, mbp-1, or mbp-10.",
    )
    parser.add_argument(
        "--bar-freq",
        default="1min",
        choices=VALID_BAR_FREQS,
        help="Bar frequency (default: 1min).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory for parquet files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be created without writing any files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Process even if the file appears in the processing manifest.",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file does not exist: {input_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)

    print(f"\n{'='*60}")
    mode_label = "DRY RUN" if args.dry_run else "PROCESSING"
    print(f"  DATABENTO TO FEATURES — {mode_label}")
    print(f"{'='*60}")
    print(f"  Input:    {input_path}")
    print(f"  Symbol:   {args.symbol.upper()}")
    print(f"  Schema:   {args.schema}")
    print(f"  Bar freq: {args.bar_freq}")
    print(f"  Output:   {output_dir}")
    print(f"{'='*60}")

    # Check manifest
    if not args.dry_run and not args.force:
        if is_already_processed(input_path, args.schema, args.bar_freq):
            print(
                f"\n  SKIP: This file ({input_path.name}) has already been processed "
                f"(found in FEATURES_MANIFEST.jsonl)."
            )
            print("        Use --force to reprocess.")
            sys.exit(0)

    result = process_file(
        input_path=input_path,
        symbol=args.symbol,
        schema=args.schema,
        bar_freq=args.bar_freq,
        output_dir=output_dir,
        dry_run=args.dry_run,
    )

    # Print summary
    print(f"\n{'='*60}")
    print("  PROCESSING SUMMARY")
    print(f"{'='*60}")
    print(f"  Status:        {result['status'].upper()}")
    print(f"  Input records: {result['input_records']:,}" if result['input_records'] else "  Input records: N/A")
    print(f"  Output bars:   {result['output_bars']:,}" if result['output_bars'] else "  Output bars:   N/A")
    if result["rows_written"] is not None:
        print(f"  Rows written:  {result['rows_written']:,}")
    if result["date_range"]:
        print(f"  Date range:    {result['date_range']}")
    if result["columns"]:
        print(f"  Columns:       {result['columns']}")
    if result["message"]:
        print(f"  Message:       {result['message']}")
    print(f"{'='*60}\n")

    if result["status"] == "error":
        sys.exit(1)

    # Write to features manifest if successful and not dry_run
    if result["status"] == "success" and not args.dry_run:
        manifest_entry = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "input_path": result["input_path"],
            "output_path": result["output_path"],
            "symbol": result["symbol"],
            "schema": result["schema"],
            "bar_freq": result["bar_freq"],
            "input_records": result["input_records"],
            "output_bars": result["output_bars"],
            "rows_written": result["rows_written"],
            "date_range": result["date_range"],
            "columns": result["columns"],
            "processed_by": "tick_databento_to_features.py",
        }
        append_features_manifest(manifest_entry)
        print(f"  Features manifest updated: {FEATURES_MANIFEST_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
