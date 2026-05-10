"""
ES Data Pipeline
=================
Production-grade data loading, cleaning, feature engineering, and caching
for ES/MES futures research.

Design principles:
  - Immutable: all methods return new DataFrames, never mutate internal state.
  - Causal: all features use .shift() or expanding windows — zero lookahead.
  - Pluggable: source interface supports CSV (Databento), yfinance, Parquet.
  - Versioned: saved Parquet files include content hash for reproducibility.

Usage:
    loader = ESDataLoader(
        source="csv",
        data_path="01_data/raw/ES_1min.csv",
        source_tz="utc",
        col_mapping={"ts_event": "timestamp"},
    )
    data = loader.load()              # raw 1-min bars
    data = loader.filter_rth(data)    # RTH only
    data = loader.resample(data, "5min")
    data = loader.add_features(data)
    loader.save_parquet(data, "01_data/processed/ES_5min_rth.parquet")
"""

import sys
from pathlib import Path

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from time_utils import ensure_eastern, filter_rth, resample_ohlcv
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import data_schema as S

logger = logging.getLogger(__name__)


class ESDataLoader:
    """
    Unified data loader for ES futures.
    Supports CSV (Databento format), yfinance, and cached Parquet.
    """

    def __init__(
        self,
        source: str = "csv",
        data_path: Optional[str] = None,
        source_tz: str = "utc",
        col_mapping: Optional[Dict[str, str]] = None,
        instrument: str = "ES",
    ):
        """
        Args:
            source: "csv", "yfinance", or "parquet".
            data_path: Path to CSV or Parquet file.
            source_tz: Timezone of raw timestamps (for CSV sources).
            col_mapping: Dict mapping source column names to standard names.
                         e.g. {"ts_event": "timestamp"}
            instrument: Instrument symbol for spec lookup.
        """
        self.source = source
        self.data_path = Path(data_path) if data_path else None
        self.source_tz = source_tz
        self.col_mapping = col_mapping or {}
        self.instrument = S.INSTRUMENTS.get(instrument, S.ES)

        if source in ("csv", "parquet") and not self.data_path:
            raise ValueError(f"data_path required for source='{source}'")

    # ── Loading ────────────────────────────────────────────────
    def load(self) -> pd.DataFrame:
        """
        Load raw data from configured source.
        Returns DataFrame with standardized columns:
            [timestamp, open, high, low, close, volume]
        Timestamp is tz-aware US/Eastern.
        """
        if self.source == "csv":
            return self._load_csv()
        elif self.source == "parquet":
            return self._load_parquet()
        elif self.source == "yfinance":
            return self._load_yfinance()
        else:
            raise ValueError(f"Unknown source: {self.source}")

    def _load_csv(self) -> pd.DataFrame:
        """Load from Databento-style CSV."""
        logger.info(f"Loading CSV: {self.data_path}")

        # Determine which columns to read
        # Try reading header first to find available columns
        sample = pd.read_csv(self.data_path, nrows=0)
        available_cols = set(sample.columns)

        # Build column list: timestamp col + OHLCV
        ts_source_col = self._get_source_ts_col()
        read_cols = [ts_source_col] + [
            c for c in ["open", "high", "low", "close", "volume"]
            if c in available_cols
        ]

        df = pd.read_csv(
            self.data_path,
            usecols=read_cols,
            parse_dates=[ts_source_col],
        )

        # Apply column mapping
        rename_map = {v: k for k, v in self.col_mapping.items()}
        # Also handle the reverse: source col -> standard col
        if ts_source_col != S.TIMESTAMP:
            rename_map[ts_source_col] = S.TIMESTAMP
        df = df.rename(columns=rename_map)

        # Standardize
        df = df.sort_values(S.TIMESTAMP).reset_index(drop=True)
        df = ensure_eastern(df, S.TIMESTAMP, self.source_tz)

        n = len(df)
        d0 = df[S.TIMESTAMP].iloc[0].date()
        d1 = df[S.TIMESTAMP].iloc[-1].date()
        logger.info(f"  Loaded {n:,} bars  {d0} -> {d1}")

        return df

    def _load_parquet(self) -> pd.DataFrame:
        """Load from processed Parquet file."""
        logger.info(f"Loading Parquet: {self.data_path}")
        df = pd.read_parquet(self.data_path)

        # Ensure timestamp is tz-aware Eastern
        if S.TIMESTAMP in df.columns and df[S.TIMESTAMP].dt.tz is None:
            df = ensure_eastern(df, S.TIMESTAMP, self.source_tz)

        return df

    def _load_yfinance(self) -> pd.DataFrame:
        """Load from yfinance (for SPY or ES=F)."""
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("pip install yfinance to use source='yfinance'")

        logger.info(f"Loading from yfinance: {self.instrument.symbol}")
        ticker = yf.Ticker("ES=F")
        df = ticker.history(period="max", interval="1m")

        df = df.reset_index()
        df = df.rename(columns={
            "Datetime": S.TIMESTAMP,
            "Open": S.OPEN, "High": S.HIGH,
            "Low": S.LOW, "Close": S.CLOSE,
            "Volume": S.VOLUME,
        })
        df = df[[S.TIMESTAMP, S.OPEN, S.HIGH, S.LOW, S.CLOSE, S.VOLUME]]
        df = ensure_eastern(df, S.TIMESTAMP)

        return df

    def _get_source_ts_col(self) -> str:
        """Get the source timestamp column name."""
        # Check if there's a mapping that maps TO "timestamp"
        for source_col, target_col in self.col_mapping.items():
            if target_col == S.TIMESTAMP:
                return source_col
        # Check reverse mapping
        for target_col, source_col in self.col_mapping.items():
            if target_col == S.TIMESTAMP:
                return source_col
        # Default: look for common names
        return self.col_mapping.get("timestamp", "ts_event")

    # ── Filtering ──────────────────────────────────────────────
    def filter_rth(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter to Regular Trading Hours for this instrument.
        Returns new DataFrame.
        """
        return filter_rth(
            df,
            ts_col=S.TIMESTAMP,
            rth_start=self.instrument.rth_start,
            rth_end=self.instrument.rth_end,
        )

    # ── Resampling ─────────────────────────────────────────────
    def resample(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """
        Resample to higher timeframe.
        Preserves OHLCV accuracy. Filters out partial bars at RTH edges.

        Args:
            df: RTH-filtered DataFrame with timestamp column.
            timeframe: "5min", "15min", "1h", "4h", "1D".

        Returns:
            Resampled DataFrame with timestamp as index.
        """
        resampled = resample_ohlcv(df, timeframe, ts_col=S.TIMESTAMP)

        # Re-filter RTH (resampling can create edge bars outside session)
        if timeframe != "1D":
            start = dt.time(*map(int, self.instrument.rth_start.split(":")))
            end = dt.time(*map(int, self.instrument.rth_end.split(":")))
            t = resampled.index.time
            resampled = resampled[(t >= start) & (t < end)].copy()

        # Add session_date
        resampled[S.SESSION_DATE] = resampled.index.date

        return resampled

    # ── Feature Engineering ────────────────────────────────────
    def add_features(
        self,
        df: pd.DataFrame,
        atr_period: int = 20,
        vol_avg_period: int = 20,
        rsi_period: int = 14,
    ) -> pd.DataFrame:
        """
        Add causal technical features. All use shifted/lagged data only.
        No lookahead bias.

        Features added:
            - prior_close: previous bar's close
            - atr: Average True Range (causal)
            - daily_range: high - low
            - session_return_pct: (close - open) / open * 100 [daily only]
            - session_high/low: expanding session high/low
            - session_vwap: volume-weighted average price (resets daily)
            - rsi: Wilder's RSI
            - volume_avg: rolling average volume

        Args:
            df: DataFrame with OHLCV + session_date. Index = timestamp.
            atr_period: Lookback for ATR calculation.
            vol_avg_period: Lookback for volume moving average.
            rsi_period: Lookback for RSI.

        Returns:
            DataFrame with all features added (warmup rows contain NaN).
        """
        out = df.copy()

        # ── Prior close ────────────────────────────────────────
        out[S.PRIOR_CLOSE] = out[S.CLOSE].shift(1)

        # ── True Range & ATR ───────────────────────────────────
        tr = _true_range(out[S.HIGH], out[S.LOW], out[S.PRIOR_CLOSE])
        out[S.ATR] = tr.rolling(atr_period, min_periods=atr_period).mean()

        # ── Daily range ────────────────────────────────────────
        out[S.DAILY_RANGE] = out[S.HIGH] - out[S.LOW]

        # ── Session expanding high/low ─────────────────────────
        if S.SESSION_DATE in out.columns:
            out[S.SESSION_HIGH] = out.groupby(S.SESSION_DATE)[S.HIGH].cummax()
            out[S.SESSION_LOW] = out.groupby(S.SESSION_DATE)[S.LOW].cummin()

            # ── VWAP (reset daily) ─────────────────────────────
            out = _add_session_vwap(out)

        # ── RSI (Wilder's) ─────────────────────────────────────
        out[S.RSI] = _wilder_rsi(out[S.CLOSE], rsi_period)

        # ── Volume average ─────────────────────────────────────
        out[S.VOLUME_AVG] = out[S.VOLUME].rolling(
            vol_avg_period, min_periods=vol_avg_period
        ).mean()

        # ── Gap (for daily data or first bar of session) ───────
        if S.SESSION_DATE in out.columns:
            out = _add_gap_features(out)

        return out

    # ── Daily features (for daily-resampled data) ──────────────
    def add_daily_features(self, daily: pd.DataFrame) -> pd.DataFrame:
        """
        Add features specific to daily bars.
        Expects index = date, columns = OHLCV.
        """
        out = daily.copy()
        out[S.PRIOR_CLOSE] = out[S.CLOSE].shift(1)
        out[S.GAP_RAW] = out[S.OPEN] - out[S.PRIOR_CLOSE]
        out[S.GAP_PCT] = out[S.GAP_RAW] / out[S.PRIOR_CLOSE] * 100
        out[S.SESSION_RETURN_PCT] = (out[S.CLOSE] - out[S.OPEN]) / out[S.OPEN] * 100
        out[S.DAILY_RANGE] = out[S.HIGH] - out[S.LOW]

        tr = _true_range(out[S.HIGH], out[S.LOW], out[S.PRIOR_CLOSE])
        out[S.ATR] = tr.rolling(20, min_periods=20).mean()

        return out

    # ── Train/Test Split ───────────────────────────────────────
    def split(
        self,
        df: pd.DataFrame,
        train_end: str,
        test_start: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Split data into train and test sets by date.

        Args:
            df: DataFrame with DatetimeIndex or session_date column.
            train_end: Last date of training period (inclusive). "YYYY-MM-DD".
            test_start: First date of test period (inclusive). "YYYY-MM-DD".

        Returns:
            (train_df, test_df) — copies, not views.
        """
        train_end_ts = pd.Timestamp(train_end, tz="US/Eastern")
        test_start_ts = pd.Timestamp(test_start, tz="US/Eastern")

        if isinstance(df.index, pd.DatetimeIndex):
            train = df[df.index <= train_end_ts].copy()
            test = df[df.index >= test_start_ts].copy()
        elif S.SESSION_DATE in df.columns:
            dates = pd.to_datetime(df[S.SESSION_DATE])
            train = df[dates <= train_end_ts].copy()
            test = df[dates >= test_start_ts].copy()
        else:
            raise ValueError("DataFrame needs DatetimeIndex or session_date column")

        logger.info(f"  Split: train={len(train):,} bars, test={len(test):,} bars")
        return train, test

    # ── Parquet I/O ────────────────────────────────────────────
    def save_parquet(
        self,
        df: pd.DataFrame,
        path: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Save DataFrame as Parquet with version hash in metadata.
        Returns the content hash.
        """
        filepath = Path(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Compute content hash
        content_hash = _compute_hash(df)

        # Build metadata
        meta = {
            S.PARQUET_VERSION_KEY: content_hash,
            S.PARQUET_SOURCE_KEY: str(self.data_path or "unknown"),
            S.PARQUET_CREATED_KEY: dt.datetime.now().isoformat(),
            "instrument": self.instrument.symbol,
            "n_rows": str(len(df)),
        }
        if metadata:
            meta.update(metadata)

        # Save with metadata
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df)
        existing_meta = table.schema.metadata or {}
        existing_meta[S.PARQUET_METADATA_KEY.encode()] = json.dumps(meta).encode()
        table = table.replace_schema_metadata(existing_meta)

        pq.write_table(table, filepath)
        logger.info(f"  Saved {filepath} ({len(df):,} rows, hash={content_hash[:12]})")

        return content_hash

    def load_parquet_cached(self, path: str) -> Tuple[pd.DataFrame, Dict]:
        """
        Load Parquet and return (DataFrame, metadata_dict).
        """
        import pyarrow.parquet as pq

        filepath = Path(path)
        table = pq.read_table(filepath)

        meta = {}
        if table.schema.metadata and S.PARQUET_METADATA_KEY.encode() in table.schema.metadata:
            meta = json.loads(table.schema.metadata[S.PARQUET_METADATA_KEY.encode()])

        df = table.to_pandas()
        logger.info(f"  Loaded {filepath} ({len(df):,} rows, hash={meta.get(S.PARQUET_VERSION_KEY, 'N/A')[:12]})")

        return df, meta


# ══════════════════════════════════════════════════════════════════
# PRIVATE HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════

def _true_range(
    high: pd.Series,
    low: pd.Series,
    prev_close: pd.Series,
) -> pd.Series:
    """Compute True Range (handles gaps)."""
    return np.maximum(
        high - low,
        np.maximum(
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        )
    )


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI using exponential smoothing."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _add_session_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add VWAP that resets each session.
    VWAP = cumulative(typical_price * volume) / cumulative(volume)
    """
    out = df.copy()
    typical_price = (out[S.HIGH] + out[S.LOW] + out[S.CLOSE]) / 3
    out["_tp_vol"] = typical_price * out[S.VOLUME]

    # Cumulative sums within each session
    cum_tp_vol = out.groupby(S.SESSION_DATE)["_tp_vol"].cumsum()
    cum_vol = out.groupby(S.SESSION_DATE)[S.VOLUME].cumsum()

    out[S.SESSION_VWAP] = cum_tp_vol / cum_vol.replace(0, np.nan)

    # VWAP standard deviation (expanding within session)
    # Using squared deviation from VWAP, volume-weighted
    out["_dev_sq"] = ((typical_price - out[S.SESSION_VWAP]) ** 2) * out[S.VOLUME]
    cum_dev_sq = out.groupby(S.SESSION_DATE)["_dev_sq"].cumsum()
    out[S.SESSION_VWAP_STD] = np.sqrt(cum_dev_sq / cum_vol.replace(0, np.nan))

    out = out.drop(columns=["_tp_vol", "_dev_sq"])
    return out


def _add_gap_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add gap features: raw gap and gap percentage.
    Gap = first bar's open - previous session's last close.
    Only meaningful for first bar of each session.
    """
    out = df.copy()

    # Get last close of each session
    session_closes = out.groupby(S.SESSION_DATE)[S.CLOSE].last()

    # Get first bar of each session
    first_bar_idx = out.groupby(S.SESSION_DATE).head(1).index

    # For each session, gap = open of first bar - close of previous session
    out[S.GAP_RAW] = np.nan
    out[S.GAP_PCT] = np.nan

    sessions = sorted(out[S.SESSION_DATE].unique())
    for i in range(1, len(sessions)):
        prev_session = sessions[i - 1]
        curr_session = sessions[i]

        prev_close = session_closes.loc[prev_session]
        curr_mask = (out[S.SESSION_DATE] == curr_session)
        first_idx = out.loc[curr_mask].index[0]

        out.loc[first_idx, S.GAP_RAW] = out.loc[first_idx, S.OPEN] - prev_close
        if prev_close != 0:
            out.loc[first_idx, S.GAP_PCT] = (
                (out.loc[first_idx, S.OPEN] - prev_close) / prev_close * 100
            )

    return out


def _compute_hash(df: pd.DataFrame) -> str:
    """Compute SHA-256 hash of DataFrame content for versioning."""
    # Hash based on shape + first/last values + dtypes
    content = f"{df.shape}|{df.dtypes.to_dict()}|"
    if len(df) > 0:
        content += f"{df.iloc[0].to_dict()}|{df.iloc[-1].to_dict()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]