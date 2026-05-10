"""
Data Schema & Instrument Specifications
=========================================
Central source of truth for all instrument specs, column constants,
and cost parameters.

Adding a new instrument:
  1. Create the InstrumentSpec instance below
  2. Add it to the INSTRUMENTS dict
  3. Add a DATA_PATHS entry if you have data for it
  4. Register strategies in registry.py pointing to the new instrument

Exchange fees: CME non-member rates as of April 2026.
Commission: Conservative retail (Topstep/AMP/Optimus style).
Slippage: Default 0 ticks in base spec; run_strategy.py overrides via --cost-scenario.

RTH sessions are the liquid "pit" hours used for signal generation.
Energy/metals trade nearly 23h but the RTH window is where volume concentrates.
"""

from dataclasses import dataclass, field
from typing import Dict


# ======================================================================
# COLUMN CONSTANTS
# ======================================================================

# ══════════════════════════════════════════════════════════════════
# COLUMN CONSTANTS (new COL_ style)
# ══════════════════════════════════════════════════════════════════
COL_TIMESTAMP = "timestamp"
COL_OPEN = "open"
COL_HIGH = "high"
COL_LOW = "low"
COL_CLOSE = "close"
COL_VOLUME = "volume"
COL_SESSION_DATE = "session_date"
COL_ATR = "atr"
COL_RSI = "rsi"
OHLCV_COLS = [COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOLUME]

# ══════════════════════════════════════════════════════════════════
# BACKWARD‑COMPATIBLE ALIASES (pipeline still uses these names)
# ══════════════════════════════════════════════════════════════════
TIMESTAMP = COL_TIMESTAMP
OPEN = COL_OPEN
HIGH = COL_HIGH
LOW = COL_LOW
CLOSE = COL_CLOSE
VOLUME = COL_VOLUME

# Derived columns
DATE = "date"
TIME = "time"
IS_RTH = "is_rth"
SESSION_DATE = "session_date"

# Feature columns (all causal – no lookahead)
PRIOR_CLOSE = "prior_close"
GAP_RAW = "gap_raw"
GAP_PCT = "gap_pct"
ATR = "atr"
ATR_PERIOD = "atr_period"
DAILY_RANGE = "daily_range"
SESSION_RETURN_PCT = "session_return_pct"
SESSION_HIGH = "session_high"
SESSION_LOW = "session_low"
SESSION_VWAP = "session_vwap"
SESSION_VWAP_STD = "session_vwap_std"
RSI = "rsi"
VOLUME_AVG = "volume_avg"

# Parquet metadata keys
PARQUET_METADATA_KEY = "quant_research"
PARQUET_VERSION_KEY = "data_hash"
PARQUET_SOURCE_KEY = "source"
PARQUET_CREATED_KEY = "created_at"


# ======================================================================
# INSTRUMENT SPECIFICATION
# ======================================================================

@dataclass(frozen=True)
class InstrumentSpec:
    """Immutable instrument specification."""
    symbol: str
    tick_size: float              # minimum price increment
    tick_value: float             # dollar value per tick
    point_value: float            # dollar value per 1.0 point move
    commission_per_side: float    # broker commission per side ($)
    exchange_fee_per_side: float  # CME/CBOT/NYMEX/COMEX fee per side ($)
    slippage_ticks_per_side: int  # market impact (default 0; overridden by scenario)
    rth_start: str                # HH:MM Eastern Time
    rth_end: str                  # HH:MM Eastern Time
    asset_class: str = "equity_index"  # equity_index, energy, metal, currency, rate, crypto
    notes: str = ""

    @property
    def total_cost_per_side(self) -> float:
        """Total dollar cost per side (commission + exchange fee)."""
        return self.commission_per_side + self.exchange_fee_per_side

    @property
    def cost_per_rt_pts(self) -> float:
        """Round-trip cost in points (commission + exchange fees + slippage)."""
        fee_pts = (2 * self.total_cost_per_side) / self.point_value
        slippage_pts = 2 * self.slippage_ticks_per_side * self.tick_size
        return fee_pts + slippage_pts


# ======================================================================
# EQUITY INDEX FUTURES
# ======================================================================

ES = InstrumentSpec(
    symbol="ES", tick_size=0.25, tick_value=12.50, point_value=50.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="E-mini S&P 500. CME.",
)

MES = InstrumentSpec(
    symbol="MES", tick_size=0.25, tick_value=1.25, point_value=5.0,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="Micro E-mini S&P 500. CME. 1/10th ES.",
)

NQ = InstrumentSpec(
    symbol="NQ", tick_size=0.25, tick_value=5.00, point_value=20.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="E-mini Nasdaq 100. CME.",
)

MNQ = InstrumentSpec(
    symbol="MNQ", tick_size=0.25, tick_value=0.50, point_value=2.0,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="Micro E-mini Nasdaq 100. CME. 1/10th NQ.",
)

RTY = InstrumentSpec(
    symbol="RTY", tick_size=0.10, tick_value=5.00, point_value=50.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="E-mini Russell 2000. CME.",
)

M2K = InstrumentSpec(
    symbol="M2K", tick_size=0.10, tick_value=0.50, point_value=5.0,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="Micro E-mini Russell 2000. CME. 1/10th RTY.",
)

YM = InstrumentSpec(
    symbol="YM", tick_size=1.0, tick_value=5.00, point_value=5.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="E-mini Dow Jones ($5). CBOT.",
)

MYM = InstrumentSpec(
    symbol="MYM", tick_size=1.0, tick_value=0.50, point_value=0.50,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="equity_index",
    notes="Micro E-mini Dow Jones ($0.50). CBOT. 1/10th YM.",
)


# ======================================================================
# ENERGY FUTURES
# ======================================================================

CL = InstrumentSpec(
    symbol="CL", tick_size=0.01, tick_value=10.00, point_value=1000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.52,
    slippage_ticks_per_side=0,
    rth_start="09:00", rth_end="14:30",
    asset_class="energy",
    notes="WTI Crude Oil. NYMEX. RTH = open outcry pit hours (ET).",
)

MCL = InstrumentSpec(
    symbol="MCL", tick_size=0.01, tick_value=1.00, point_value=100.0,
    commission_per_side=0.62, exchange_fee_per_side=0.47,
    slippage_ticks_per_side=0,
    rth_start="09:00", rth_end="14:30",
    asset_class="energy",
    notes="Micro WTI Crude Oil. NYMEX. 1/10th CL.",
)

NG = InstrumentSpec(
    symbol="NG", tick_size=0.001, tick_value=10.00, point_value=10000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.52,
    slippage_ticks_per_side=0,
    rth_start="09:00", rth_end="14:30",
    asset_class="energy",
    notes="Henry Hub Natural Gas. NYMEX. Highly volatile.",
)

QG = InstrumentSpec(
    symbol="QG", tick_size=0.005, tick_value=12.50, point_value=2500.0,
    commission_per_side=0.62, exchange_fee_per_side=0.47,
    slippage_ticks_per_side=0,
    rth_start="09:00", rth_end="14:30",
    asset_class="energy",
    notes="E-mini Natural Gas. NYMEX. 1/4th NG (2,500 MMBtu).",
)


# ======================================================================
# PRECIOUS METALS (COMEX) — GC/SI currently suspended; specs ready
# ======================================================================

GC = InstrumentSpec(
    symbol="GC", tick_size=0.10, tick_value=10.00, point_value=100.0,
    commission_per_side=2.50, exchange_fee_per_side=1.52,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="13:30",
    asset_class="metal",
    notes="Gold 100 oz. COMEX. RTH = COMEX floor hours (ET). SUSPENDED as of Apr 2026.",
)

MGC = InstrumentSpec(
    symbol="MGC", tick_size=0.10, tick_value=1.00, point_value=10.0,
    commission_per_side=0.62, exchange_fee_per_side=0.47,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="13:30",
    asset_class="metal",
    notes="Micro Gold 10 oz. COMEX. 1/10th GC. SUSPENDED.",
)

SI = InstrumentSpec(
    symbol="SI", tick_size=0.005, tick_value=25.00, point_value=5000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.52,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="13:30",
    asset_class="metal",
    notes="Silver 5000 oz. COMEX. High tick value — dangerous. SUSPENDED.",
)

SIL = InstrumentSpec(
    symbol="SIL", tick_size=0.005, tick_value=2.50, point_value=500.0,
    commission_per_side=0.62, exchange_fee_per_side=0.47,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="13:30",
    asset_class="metal",
    notes="Micro Silver 1000 oz. COMEX. 1/5th SI (NOT 1/10). SUSPENDED.",
)


# ======================================================================
# CURRENCY FUTURES (CME)
# ======================================================================

_6E = InstrumentSpec(
    symbol="6E", tick_size=0.00005, tick_value=6.25, point_value=125000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Euro FX. CME. 125,000 EUR. RTH roughly matches London+NY overlap.",
)

M6E = InstrumentSpec(
    symbol="M6E", tick_size=0.0001, tick_value=1.25, point_value=12500.0,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Micro Euro FX. CME. 12,500 EUR. 1/10th 6E.",
)

_6B = InstrumentSpec(
    symbol="6B", tick_size=0.0001, tick_value=6.25, point_value=62500.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="British Pound. CME. 62,500 GBP.",
)

M6B = InstrumentSpec(
    symbol="M6B", tick_size=0.0001, tick_value=0.625, point_value=6250.0,
    commission_per_side=0.62, exchange_fee_per_side=0.35,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Micro British Pound. CME. 6,250 GBP. 1/10th 6B.",
)

_6J = InstrumentSpec(
    symbol="6J", tick_size=0.0000005, tick_value=6.25, point_value=12500000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Japanese Yen. CME. 12,500,000 JPY. No micro available.",
)

_6C = InstrumentSpec(
    symbol="6C", tick_size=0.00005, tick_value=5.00, point_value=100000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Canadian Dollar. CME. 100,000 CAD. No micro available.",
)

_6A = InstrumentSpec(
    symbol="6A", tick_size=0.0001, tick_value=10.00, point_value=100000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Australian Dollar. CME. 100,000 AUD. Commodity-correlated.",
)

_6S = InstrumentSpec(
    symbol="6S", tick_size=0.0001, tick_value=12.50, point_value=125000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.18,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="currency",
    notes="Swiss Franc. CME. 125,000 CHF. Safe-haven currency.",
)


# ======================================================================
# INTEREST RATE FUTURES (CBOT)
# ======================================================================

ZB = InstrumentSpec(
    symbol="ZB", tick_size=0.03125, tick_value=31.25, point_value=1000.0,
    commission_per_side=2.50, exchange_fee_per_side=1.02,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="rate",
    notes="30-Year Treasury Bond. CBOT. High tick value ($31.25). Quoted in 32nds.",
)

ZN = InstrumentSpec(
    symbol="ZN", tick_size=0.015625, tick_value=15.625, point_value=1000.0,
    commission_per_side=2.50, exchange_fee_per_side=0.83,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="rate",
    notes="10-Year Treasury Note. CBOT. Quoted in half-32nds.",
)

ZF = InstrumentSpec(
    symbol="ZF", tick_size=0.0078125, tick_value=7.8125, point_value=1000.0,
    commission_per_side=2.50, exchange_fee_per_side=0.60,
    slippage_ticks_per_side=0,
    rth_start="08:20", rth_end="15:00",
    asset_class="rate",
    notes="5-Year Treasury Note. CBOT. Quoted in quarter-32nds.",
)


# ======================================================================
# CRYPTO FUTURES (CME)
# ======================================================================

MBT = InstrumentSpec(
    symbol="MBT", tick_size=5.0, tick_value=0.50, point_value=0.10,
    commission_per_side=2.50, exchange_fee_per_side=1.25,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="16:00",
    asset_class="crypto",
    notes="Micro Bitcoin. CME. 0.10 BTC. Trades nearly 24h but use equity RTH for overlap.",
)


# ======================================================================
# AGRICULTURAL FUTURES (CBOT) — added for regime diversity
# ======================================================================

ZC = InstrumentSpec(
    symbol="ZC", tick_size=0.25, tick_value=12.50, point_value=50.0,
    commission_per_side=2.50, exchange_fee_per_side=1.02,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="14:20",
    asset_class="agriculture",
    notes="Corn. CBOT. 5,000 bushels. Restricted at many prop firms.",
)

ZW = InstrumentSpec(
    symbol="ZW", tick_size=0.25, tick_value=12.50, point_value=50.0,
    commission_per_side=2.50, exchange_fee_per_side=1.02,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="14:20",
    asset_class="agriculture",
    notes="Wheat. CBOT. 5,000 bushels. Restricted at many prop firms.",
)

ZS = InstrumentSpec(
    symbol="ZS", tick_size=0.25, tick_value=12.50, point_value=50.0,
    commission_per_side=2.50, exchange_fee_per_side=1.02,
    slippage_ticks_per_side=0,
    rth_start="09:30", rth_end="14:20",
    asset_class="agriculture",
    notes="Soybeans. CBOT. 5,000 bushels. Restricted at many prop firms.",
)


# ======================================================================
# MASTER INSTRUMENT DICTIONARY
# ======================================================================

INSTRUMENTS: Dict[str, InstrumentSpec] = {
    # Equity indices
    "ES": ES, "MES": MES, "NQ": NQ, "MNQ": MNQ,
    "RTY": RTY, "M2K": M2K, "YM": YM, "MYM": MYM,
    # Energy
    "CL": CL, "MCL": MCL, "NG": NG, "QG": QG,
    # Metals (suspended — specs ready for when they return)
    "GC": GC, "MGC": MGC, "SI": SI, "SIL": SIL,
    # Currencies
    "6E": _6E, "M6E": M6E, "6B": _6B, "M6B": M6B,
    "6J": _6J, "6C": _6C, "6A": _6A, "6S": _6S,
    # Interest rates
    "ZB": ZB, "ZN": ZN, "ZF": ZF,
    # Crypto
    "MBT": MBT,
    # Agriculture (low priority — many prop firms restrict)
    "ZC": ZC, "ZW": ZW, "ZS": ZS,
}


# ======================================================================
# DATA FILE PATHS (relative to 01_data/raw/)
# ======================================================================
# Maps instrument root symbol to the expected CSV filename.
# Micro contracts share data with their full-size parent (same price, different multiplier).

DATA_PATHS: Dict[str, str] = {
    "ES":  "ES_1min.csv",   "MES": "ES_1min.csv",
    "NQ":  "NQ_1min.csv",   "MNQ": "NQ_1min.csv",
    "RTY": "RTY_1min.csv",  "M2K": "RTY_1min.csv",
    "YM":  "YM_1min.csv",   "MYM": "YM_1min.csv",
    "CL":  "CL_1min.csv",   "MCL": "CL_1min.csv",
    "NG":  "NG_1min.csv",   "QG":  "NG_1min.csv",
    "GC":  "GC_1min.csv",   "MGC": "GC_1min.csv",
    "SI":  "SI_1min.csv",   "SIL": "SI_1min.csv",
    "6E":  "6E_1min.csv",   "M6E": "6E_1min.csv",
    "6B":  "6B_1min.csv",   "M6B": "6B_1min.csv",
    "6J":  "6J_1min.csv",
    "6C":  "6C_1min.csv",
    "6A":  "6A_1min.csv",
    "6S":  "6S_1min.csv",
    "ZB":  "ZB_1min.csv",
    "ZN":  "ZN_1min.csv",
    "ZF":  "ZF_1min.csv",
    "MBT": "MBT_1min.csv",
    "ZC":  "ZC_1min.csv",
    "ZW":  "ZW_1min.csv",
    "ZS":  "ZS_1min.csv",
}


# ======================================================================
# DATABENTO DOWNLOAD SYMBOLS
# ======================================================================
# Continuous front-month symbology for Databento GLBX.MDP3 dataset.

DATABENTO_SYMBOLS: Dict[str, str] = {
    "ES":  "ES.c.0",
    "NQ":  "NQ.c.0",
    "RTY": "RTY.c.0",
    "YM":  "YM.c.0",
    "CL":  "CL.c.0",
    "NG":  "NG.c.0",
    "GC":  "GC.c.0",
    "SI":  "SI.c.0",
    "6E":  "6E.c.0",
    "6B":  "6B.c.0",
    "6J":  "6J.c.0",
    "6C":  "6C.c.0",
    "6A":  "6A.c.0",
    "6S":  "6S.c.0",
    "ZB":  "ZB.c.0",
    "ZN":  "ZN.c.0",
    "ZF":  "ZF.c.0",
    "MBT": "MBT.c.0",
    "ZC":  "ZC.c.0",
    "ZW":  "ZW.c.0",
    "ZS":  "ZS.c.0",
}


# Backward-compatible column aliases
TIMESTAMP = COL_TIMESTAMP
SESSION_DATE = COL_SESSION_DATE
OPEN = COL_OPEN
HIGH = COL_HIGH
LOW = COL_LOW
CLOSE = COL_CLOSE
VOLUME = COL_VOLUME