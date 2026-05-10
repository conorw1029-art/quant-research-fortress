"""
Data Schema Constants
======================
Central definition of column names, dtypes, and instrument specs.
All other modules import from here — no string literals for column names elsewhere.
"""

from dataclasses import dataclass, field
from typing import Dict


# ── Column Name Constants ──────────────────────────────────────────
# Raw input columns (after standardization)
TIMESTAMP = "timestamp"
OPEN = "open"
HIGH = "high"
LOW = "low"
CLOSE = "close"
VOLUME = "volume"

# Derived columns
DATE = "date"
TIME = "time"
IS_RTH = "is_rth"
SESSION_DATE = "session_date"  # trading date (handles overnight)

# Feature columns (all causal — no lookahead)
PRIOR_CLOSE = "prior_close"
GAP_RAW = "gap_raw"
GAP_PCT = "gap_pct"
ATR = "atr"
ATR_PERIOD = "atr_period"  # metadata, not a column
DAILY_RANGE = "daily_range"
SESSION_RETURN_PCT = "session_return_pct"
SESSION_HIGH = "session_high"
SESSION_LOW = "session_low"
SESSION_VWAP = "session_vwap"
SESSION_VWAP_STD = "session_vwap_std"
RSI = "rsi"
VOLUME_AVG = "volume_avg"

# OHLCV tuple for resampling
OHLCV_COLS = [OPEN, HIGH, LOW, CLOSE, VOLUME]


# ── Instrument Specifications ─────────────────────────────────────
@dataclass(frozen=True)
class InstrumentSpec:
    """Immutable instrument specification."""
    symbol: str
    tick_size: float
    tick_value: float  # dollar value per tick
    point_value: float  # dollar value per point
    commission_per_side: float  # default retail
    slippage_ticks_per_side: int  # conservative default
    rth_start: str  # HH:MM Eastern
    rth_end: str    # HH:MM Eastern

    @property
    def cost_per_rt_pts(self) -> float:
        """Round-trip cost in points (commission + slippage)."""
        commission_pts = (2 * self.commission_per_side) / self.point_value
        slippage_pts = 2 * self.slippage_ticks_per_side * self.tick_size
        return commission_pts + slippage_pts


ES = InstrumentSpec(
    symbol="ES",
    tick_size=0.25,
    tick_value=12.50,
    point_value=50.0,
    commission_per_side=2.50,
    slippage_ticks_per_side=2,
    rth_start="09:30",
    rth_end="16:00",
)

MES = InstrumentSpec(
    symbol="MES",
    tick_size=0.25,
    tick_value=1.25,
    point_value=5.0,
    commission_per_side=0.62,
    slippage_ticks_per_side=0,  # MES has tight spread; set 0 for optimistic, 1 for conservative
    rth_start="09:30",
    rth_end="16:00",
    # NOTE: 0.52 pts/RT = commission-only ($2.60/RT / $5.00/pt).
    # With 1-tick slippage/side: 1.02 pts/RT.
    # All prior feasibility scripts used 0.52 (optimistic).
    # The fortress cost_model.py will support both scenarios.
)

NQ = InstrumentSpec(
    symbol="NQ",
    tick_size=0.25,
    tick_value=5.00,
    point_value=20.0,
    commission_per_side=2.50,
    slippage_ticks_per_side=2,
    rth_start="09:30",
    rth_end="16:00",
)

MNQ = InstrumentSpec(
    symbol="MNQ",
    tick_size=0.25,
    tick_value=0.50,
    point_value=2.0,
    commission_per_side=0.62,
    slippage_ticks_per_side=1,
    rth_start="09:30",
    rth_end="16:00",
)

# Registry for lookup by symbol
INSTRUMENTS: Dict[str, InstrumentSpec] = {
    "ES": ES, "MES": MES, "NQ": NQ, "MNQ": MNQ,
}


# ── Parquet Schema Metadata ───────────────────────────────────────
PARQUET_METADATA_KEY = "quant_research"
PARQUET_VERSION_KEY = "data_hash"
PARQUET_SOURCE_KEY = "source"
PARQUET_CREATED_KEY = "created_at"