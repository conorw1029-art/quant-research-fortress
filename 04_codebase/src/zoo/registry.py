"""
Strategy Registry
==================
Central catalog of every strategy in the research program.

Adding a new strategy:
  1. Write the class in src/strategies/your_strategy.py
  2. Add a StrategyEntry to _STRATEGIES below
  3. Run `python run_strategy.py --key your_key` and it picks up automatically

Status meanings:
  ACTIVE       -- in the test queue, run regularly
  REJECTED     -- tested and failed OOS; keep for history
  SURVIVOR     -- passed full criteria, candidate for paper trading
  DEPRECATED   -- replaced by a better version
  EXPERIMENTAL -- new, untested (strategy file exists)
  PLANNED      -- cataloged for future testing (NO strategy file yet)
"""

import importlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class Status(Enum):
    ACTIVE = "active"
    REJECTED = "rejected"
    SURVIVOR = "survivor"
    DEPRECATED = "deprecated"
    EXPERIMENTAL = "experimental"
    PLANNED = "planned"


class TestMethod(Enum):
    WALK_FORWARD = "walk_forward"
    ONE_SHOT_IS_OOS = "one_shot_is_oos"
    FIXED_PARAM = "fixed_param"


@dataclass
class StrategyEntry:
    """Metadata for a single strategy in the registry."""
    key: str
    module_path: str
    class_name: str
    category: str
    status: Status = Status.ACTIVE
    test_method: TestMethod = TestMethod.WALK_FORWARD
    timeframe: str = "5min"
    instrument: str = "MES"
    data_path_key: str = "ES"
    requires_features: List[str] = field(default_factory=list)
    notes: str = ""

    def load_class(self) -> Type:
        """Dynamically import and return the strategy class."""
        if self.status == Status.PLANNED:
            raise RuntimeError(
                f"Strategy '{self.key}' is PLANNED -- no code file exists yet. "
                f"Build the strategy before running."
            )
        try:
            module = importlib.import_module(self.module_path)
            return getattr(module, self.class_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to load {self.module_path}.{self.class_name}: {e}")
            raise


# -- Default data paths (overridable via --data-path on CLI) -------
DEFAULT_DATA_PATHS = {
    "ES":  r"..\01_data\raw\ES_1min.csv",
    "NQ":  r"..\01_data\raw\NQ_1min.csv",
    "RTY": r"..\01_data\raw\RTY_1min.csv",
    "YM":  r"..\01_data\raw\YM_1min.csv",
    "CL":  r"..\01_data\raw\CL_1min.csv",
    "NG":  r"..\01_data\raw\NG_1min.csv",
    "GC":  r"..\01_data\raw\GC_1min.csv",
    "SI":  r"..\01_data\raw\SI_1min.csv",
    "6E":  r"..\01_data\raw\6E_1min.csv",
    "6B":  r"..\01_data\raw\6B_1min.csv",
    "6J":  r"..\01_data\raw\6J_1min.csv",
    "6C":  r"..\01_data\raw\6C_1min.csv",
    "6A":  r"..\01_data\raw\6A_1min.csv",
    "6S":  r"..\01_data\raw\6S_1min.csv",
    "ZB":  r"..\01_data\raw\ZB_1min.csv",
    "ZN":  r"..\01_data\raw\ZN_1min.csv",
    "ZF":  r"..\01_data\raw\ZF_1min.csv",
    "MBT": r"..\01_data\raw\MBT_1min.csv",
    "ZC":  r"..\01_data\raw\ZC_1min.csv",
    "ZW":  r"..\01_data\raw\ZW_1min.csv",
    "ZS":  r"..\01_data\raw\ZS_1min.csv",
}


# ======================================================================
# HELPER: generate multi-market entries from a template
# ======================================================================
_MICRO_MAP = {
    "ES": "MES", "NQ": "MNQ", "RTY": "M2K", "YM": "MYM",
    "CL": "MCL", "NG": "QG",
    "GC": "MGC", "SI": "SIL",
    "6E": "M6E", "6B": "M6B",
    "6J": "6J", "6C": "6C", "6A": "6A", "6S": "6S",
    "ZB": "ZB", "ZN": "ZN", "ZF": "ZF",
    "MBT": "MBT",
    "ZC": "ZC", "ZW": "ZW", "ZS": "ZS",
}


def _multi(
    base_key: str,
    module_path: str,
    class_name: str,
    category: str,
    status: Status,
    test_method: TestMethod,
    timeframe: str,
    markets: List[str],
    requires_features: List[str],
    notes: str,
) -> List[StrategyEntry]:
    """Generate one StrategyEntry per market from a template."""
    entries = []
    for mkt in markets:
        suffix = mkt.lower().replace("6", "fx")  # 6E -> fxe
        entries.append(StrategyEntry(
            key=f"{base_key}_{suffix}",
            module_path=module_path,
            class_name=class_name,
            category=category,
            status=status,
            test_method=test_method,
            timeframe=timeframe,
            instrument=_MICRO_MAP.get(mkt, mkt),
            data_path_key=mkt,
            requires_features=requires_features,
            notes=f"{notes} [{mkt}]",
        ))
    return entries


# ======================================================================
# MARKET GROUPS
# ======================================================================
EQUITY_MKTS = ["ES", "NQ", "RTY", "YM"]
ENERGY_MKTS = ["CL", "NG"]
METAL_MKTS = ["GC", "SI"]
CURRENCY_MKTS = ["6E", "6B", "6J"]
RATE_MKTS = ["ZB", "ZN"]
CRYPTO_MKTS = ["MBT"]

PATH2_MKTS = ENERGY_MKTS + METAL_MKTS + CURRENCY_MKTS + RATE_MKTS + CRYPTO_MKTS
ALL_MKTS = EQUITY_MKTS + PATH2_MKTS


# ======================================================================
# THE REGISTRY (built with explicit extend to avoid unpacking issues)
# ======================================================================
_STRATEGIES: List[StrategyEntry] = []

# ----- BATCH 0: ORIGINAL ES-ONLY STRATEGIES -----
_STRATEGIES.extend([
    StrategyEntry(
        key="rsi_meanrev",
        module_path="src.strategies.rsi_meanrev",
        class_name="RSIMeanRevStrategy",
        category="mean_reversion",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=["rsi", "atr"],
        notes="H5b. REJECTED: fails at realistic MES costs. [ES]",
    ),
    StrategyEntry(
        key="bollinger_rsi",
        module_path="src.strategies.bollinger_rsi",
        class_name="BollingerRSIStrategy",
        category="mean_reversion",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=["rsi", "atr"],
        notes="A1. REJECTED: DSR -10.1, PF 0.81. [ES]",
    ),
    StrategyEntry(
        key="fomc_drift",
        module_path="src.strategies.fomc_drift",
        class_name="FOMCDriftStrategy",
        category="calendar",
        status=Status.SURVIVOR,
        test_method=TestMethod.ONE_SHOT_IS_OOS,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="H6. SURVIVOR: DSR 1.67, PF 2.95, Sharpe 1.08. 8 trades/yr. [ES]",
    ),
    StrategyEntry(
        key="opex_week",
        module_path="src.strategies.opex_week",
        class_name="OPEXWeekStrategy",
        category="calendar",
        status=Status.REJECTED,
        test_method=TestMethod.ONE_SHOT_IS_OOS,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="C3. REJECTED: DSR -1.14, PF 0.98. [ES]",
    ),
    StrategyEntry(
        key="holiday_effect",
        module_path="src.strategies.holiday_effect",
        class_name="HolidayEffectStrategy",
        category="calendar",
        status=Status.REJECTED,
        test_method=TestMethod.ONE_SHOT_IS_OOS,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="C4. REJECTED: DSR -0.71, PF 1.30. [ES]",
    ),
    StrategyEntry(
        key="esnq_pairs",
        module_path="src.strategies.esnq_pairs",
        class_name="ESNQPairsStrategy",
        category="stat_arb",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="1h",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="A4. REJECTED: DSR -4.15, PF 0.82. ES/NQ too correlated. [ES+NQ]",
    ),
    StrategyEntry(
        key="donchian_breakout",
        module_path="src.strategies.donchian_breakout",
        class_name="DonchianBreakoutStrategy",
        category="trend",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="1D",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="B2. REJECTED: DSR -3.88, PF 0.80. Daily trend dead on ES. [ES]",
    ),
    StrategyEntry(
        key="fomc_drift_nq",
        module_path="src.strategies.fomc_drift_nq",
        class_name="FOMCDriftNQStrategy",
        category="calendar",
        status=Status.REJECTED,
        test_method=TestMethod.ONE_SHOT_IS_OOS,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="H6-NQ. REJECTED: DSR 0.77, PF 1.96. FOMC drift ES-specific. [NQ]",
    ),
    StrategyEntry(
        key="fomc_drift_rty",
        module_path="src.strategies.fomc_drift_rty",
        class_name="FOMCDriftRTYStrategy",
        category="calendar",
        status=Status.REJECTED,
        test_method=TestMethod.ONE_SHOT_IS_OOS,
        timeframe="5min",
        instrument="M2K",
        data_path_key="RTY",
        requires_features=[],
        notes="H6-RTY. REJECTED: DSR 0.97, PF 1.94. Near-miss. [RTY]",
    ),
])

# ----- BATCH 1: ES+NQ LEVEL STRATEGIES (all rejected) -----
_STRATEGIES.extend([
    StrategyEntry(
        key="prior_day_breakout_es",
        module_path="src.strategies.prior_day_breakout",
        class_name="PriorDayBreakoutStrategy",
        category="level_breakout",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="L1-ES. REJECTED: DSR -5.01, PF 0.87. [ES]",
    ),
    StrategyEntry(
        key="prior_day_breakout_nq",
        module_path="src.strategies.prior_day_breakout",
        class_name="PriorDayBreakoutStrategy",
        category="level_breakout",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="L1-NQ. REJECTED: DSR -1.80, PF 1.03. [NQ]",
    ),
    StrategyEntry(
        key="round_number_es",
        module_path="src.strategies.round_number",
        class_name="RoundNumberStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="L2-ES. REJECTED: DSR -14.37, PF 0.58. [ES]",
    ),
    StrategyEntry(
        key="round_number_nq",
        module_path="src.strategies.round_number",
        class_name="RoundNumberStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="L2-NQ. REJECTED: DSR -13.95, PF 0.63. [NQ]",
    ),
    StrategyEntry(
        key="pivot_reaction_es",
        module_path="src.strategies.pivot_reaction",
        class_name="PivotReactionStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="L3-ES. REJECTED: DSR -6.06, PF 0.85. [ES]",
    ),
    StrategyEntry(
        key="pivot_reaction_nq",
        module_path="src.strategies.pivot_reaction",
        class_name="PivotReactionStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="L3-NQ. REJECTED: DSR -2.40, PF 0.99. [NQ]",
    ),
    StrategyEntry(
        key="camarilla_breakout_es",
        module_path="src.strategies.camarilla_breakout",
        class_name="CamarillaBreakoutStrategy",
        category="level_breakout",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="L4-ES. REJECTED: DSR -2.00, PF 1.02. [ES]",
    ),
    StrategyEntry(
        key="camarilla_breakout_nq",
        module_path="src.strategies.camarilla_breakout",
        class_name="CamarillaBreakoutStrategy",
        category="level_breakout",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="L4-NQ. REJECTED: DSR 0.48, PF 1.18. Recent folds positive but not enough. [NQ]",
    ),
    StrategyEntry(
        key="overnight_fade_es",
        module_path="src.strategies.overnight_fade",
        class_name="OvernightFadeStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="L5-ES. REJECTED: DSR -7.10, PF 0.44. Anti-edge. [ES]",
    ),
    StrategyEntry(
        key="overnight_fade_nq",
        module_path="src.strategies.overnight_fade",
        class_name="OvernightFadeStrategy",
        category="level_reaction",
        status=Status.REJECTED,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MNQ",
        data_path_key="NQ",
        requires_features=[],
        notes="L5-NQ. REJECTED: DSR -4.45, PF 0.62. Anti-edge. [NQ]",
    ),
])

# ----- PATH 2: MULTI-MARKET EXPANSION (EXPERIMENTAL) -----
_STRATEGIES.extend(_multi(
    "rsi_meanrev", "src.strategies.rsi_meanrev", "RSIMeanRevStrategy",
    "mean_reversion", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, ["rsi", "atr"],
    "H5b multi-market. RSI(14) mean-reversion.",
))
_STRATEGIES.extend(_multi(
    "fomc_drift", "src.strategies.fomc_drift", "FOMCDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", PATH2_MKTS, [],
    "H6 multi-market. FOMC announcement drift.",
))
_STRATEGIES.extend(_multi(
    "prior_day_breakout", "src.strategies.prior_day_breakout", "PriorDayBreakoutStrategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, [],
    "L1 multi-market. Prior day H/L breakout.",
))
_STRATEGIES.extend(_multi(
    "round_number", "src.strategies.round_number", "RoundNumberStrategy",
    "level_reaction", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, [],
    "L2 multi-market. Mean-reversion at round numbers.",
))
_STRATEGIES.extend(_multi(
    "pivot_reaction", "src.strategies.pivot_reaction", "PivotReactionStrategy",
    "level_reaction", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, [],
    "L3 multi-market. S1/R1 daily pivot bounce.",
))
_STRATEGIES.extend(_multi(
    "camarilla_breakout", "src.strategies.camarilla_breakout", "CamarillaBreakoutStrategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, [],
    "L4 multi-market. Camarilla H4/L4 breakout.",
))
_STRATEGIES.extend(_multi(
    "overnight_fade", "src.strategies.overnight_fade", "OvernightFadeStrategy",
    "level_reaction", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, [],
    "L5 multi-market. Fade prior session extremes.",
))
_STRATEGIES.extend(_multi(
    "donchian_breakout", "src.strategies.donchian_breakout", "DonchianBreakoutStrategy",
    "trend", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", PATH2_MKTS, [],
    "B2 multi-market. Daily Donchian channel breakout.",
))
_STRATEGIES.extend(_multi(
    "bollinger_rsi", "src.strategies.bollinger_rsi", "BollingerRSIStrategy",
    "mean_reversion", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", PATH2_MKTS, ["rsi", "atr"],
    "A1 multi-market. Bollinger + RSI mean-reversion.",
))

# YM expansion
_STRATEGIES.extend(_multi(
    "rsi_meanrev", "src.strategies.rsi_meanrev", "RSIMeanRevStrategy",
    "mean_reversion", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["YM"], ["rsi", "atr"], "H5b on YM.",
))
_STRATEGIES.extend(_multi(
    "fomc_drift", "src.strategies.fomc_drift", "FOMCDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["YM"], [], "H6 on YM.",
))
_STRATEGIES.extend(_multi(
    "prior_day_breakout", "src.strategies.prior_day_breakout", "PriorDayBreakoutStrategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["YM"], [], "L1 on YM.",
))
_STRATEGIES.extend(_multi(
    "donchian_breakout", "src.strategies.donchian_breakout", "DonchianBreakoutStrategy",
    "trend", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", ["YM"], [], "B2 on YM.",
))

# ----- CALENDAR EVENT STRATEGIES (EXPERIMENTAL) -----
_STRATEGIES.extend(_multi(
    "nfp_drift", "src.strategies.calendar_events", "NFPDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES","NQ","ZN","ZB","6E","GC","CL"], [],
    "NFP release drift. 12/yr.",
))
_STRATEGIES.extend(_multi(
    "cpi_drift", "src.strategies.calendar_events", "CPIDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES","NQ","ZN","ZB","6E","GC","CL"], [],
    "CPI release drift. 12/yr.",
))
_STRATEGIES.extend(_multi(
    "ecb_drift", "src.strategies.calendar_events", "ECBDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["6E","6B","ZB","ZN","ES","GC"], [],
    "ECB rate decision drift. 8/yr.",
))
_STRATEGIES.extend(_multi(
    "eia_inventory", "src.strategies.calendar_events", "EIAInventoryStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["CL","NG"], [],
    "EIA crude inventory. 52/yr.",
))
_STRATEGIES.extend(_multi(
    "fed_minutes", "src.strategies.calendar_events", "FedMinutesDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES","NQ","ZN","ZB","6E","GC"], [],
    "Fed minutes release drift. 8/yr.",
))

# ----- BATCH 2: OHLCV STRATEGIES (EXPERIMENTAL) -----
_STRATEGIES.extend(_multi(
    "orb", "src.strategies.orb", "ORBStrategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ALL_MKTS, [], "Batch2. ORB parameterized duration.",
))
_STRATEGIES.extend(_multi(
    "gap_fill", "src.strategies.batch2_strategies", "GapFillStrategy",
    "mean_reversion", Status.REJECTED, TestMethod.WALK_FORWARD,
    "5min", ALL_MKTS, ["atr","prior_close"], "Batch2. Fade overnight gap.",
))
_STRATEGIES.extend(_multi(
    "fib_retracement", "src.strategies.batch2_strategies", "FibRetracementStrategy",
    "level_reaction", Status.REJECTED, TestMethod.WALK_FORWARD,
    "5min", ALL_MKTS, [], "Batch2. Fib retracement of prior day.",
))
_STRATEGIES.extend(_multi(
    "inside_bar", "src.strategies.bar_patterns", "InsideBarStrategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1h", ALL_MKTS, [], "Batch2. Inside bar breakout.",
))
_STRATEGIES.extend(_multi(
    "outside_bar", "src.strategies.bar_patterns", "OutsideBarStrategy",
    "momentum", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1h", ALL_MKTS, [], "Batch2. Outside bar momentum.",
))
_STRATEGIES.extend(_multi(
    "pin_bar", "src.strategies.bar_patterns", "PinBarStrategy",
    "mean_reversion", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1h", ALL_MKTS, [], "Batch2. Pin bar reversal.",
))
_STRATEGIES.extend(_multi(
    "nr7_breakout", "src.strategies.batch2_strategies", "NR7Strategy",
    "level_breakout", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", ALL_MKTS, [], "Batch2. NR7 narrow range breakout.",
))
_STRATEGIES.extend(_multi(
    "ib_fade", "src.strategies.batch2_strategies", "IBFadeStrategy",
    "mean_reversion", Status.REJECTED, TestMethod.WALK_FORWARD,
    "5min", ALL_MKTS, ["atr"], "Batch2. Fade IB extremes.",
))
_STRATEGIES.extend(_multi(
    "vol_macd", "src.strategies.batch2_strategies", "VolMACDStrategy",
    "momentum", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "15min", ALL_MKTS, [], "Batch2. Volume-weighted MACD.",
))
_STRATEGIES.extend(_multi(
    "connors_rsi", "src.strategies.batch2_strategies", "ConnorsRSIStrategy",
    "mean_reversion", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", ALL_MKTS, [], "Batch2. Connors RSI(2).",
))


# ======================================================================
# PUBLIC API
# ======================================================================

def get_all() -> List[StrategyEntry]:
    """All registered strategies."""
    return list(_STRATEGIES)


def get_by_key(key: str) -> Optional[StrategyEntry]:
    """Look up a strategy by its unique key."""
    for s in _STRATEGIES:
        if s.key == key:
            return s
    return None


def get_by_status(status: Status) -> List[StrategyEntry]:
    """Filter strategies by status."""
    return [s for s in _STRATEGIES if s.status == status]


def get_by_category(category: str) -> List[StrategyEntry]:
    """Filter strategies by category."""
    return [s for s in _STRATEGIES if s.category == category]


def get_active() -> List[StrategyEntry]:
    """Get all ACTIVE and EXPERIMENTAL strategies (the test queue)."""
    return [s for s in _STRATEGIES
            if s.status in (Status.ACTIVE, Status.EXPERIMENTAL)]


def get_runnable() -> List[StrategyEntry]:
    """Get strategies that have code and can be executed."""
    return [s for s in _STRATEGIES if s.status != Status.PLANNED]


def get_by_market(data_path_key: str) -> List[StrategyEntry]:
    """Filter strategies by market data source."""
    return [s for s in _STRATEGIES if s.data_path_key == data_path_key]


def summary() -> str:
    """Human-readable registry summary."""
    lines = [
        "=" * 70,
        f"  STRATEGY REGISTRY ({len(_STRATEGIES)} entries)",
        "=" * 70,
    ]
    by_status = {}
    for s in _STRATEGIES:
        by_status.setdefault(s.status.value, []).append(s)

    for status in Status:
        entries = by_status.get(status.value, [])
        if not entries:
            continue
        lines.append(f"\n  [{status.value.upper()}] ({len(entries)}):")
        for s in entries:
            lines.append(
                f"    {s.key:<30s} {s.category:<16s} {s.instrument:<6s}"
                f" {s.data_path_key:<4s} {s.test_method.value:<18s}"
                f" {s.timeframe:<8s}"
            )
    lines.append(f"\n  Total: {len(_STRATEGIES)}")
    lines.append(f"  By status: " + ", ".join(
        f"{s.value}={len(by_status.get(s.value, []))}" for s in Status
    ))
    return "\n".join(lines)

# ----- BATCH 3: TIME-SERIES MOMENTUM (CTA-style) -----
# Baltas-Kosowski (2017), Moskowitz-Ooi-Pedersen (2012)
_STRATEGIES.extend(_multi(
    "tsm", "src.strategies.tsm", "TimeSeriesMomentumStrategy",
    "trend", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", ALL_MKTS, [],
    "Batch3. Time-series momentum. Baltas-Kosowski 2017.",
))

# ----- BATCH 3: BONDARENKO OVERNIGHT DRIFT -----
# Bondarenko-Muravyev (JFQA 2023): 100% of S&P futures return earned
# in 4-hour window around European open. Sharpe 1.6 after costs.
_STRATEGIES.extend(_multi(
    "overnight_drift", "src.strategies.overnight_drift",
    "BondarenkoOvernightDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min",
    ["ES", "NQ", "RTY", "YM", "ZN", "ZB"],
    [],
    "Batch3. Bondarenko-Muravyev overnight drift. JFQA 2023.",
))


# ----- BATCH 4: VIX OVERLAY ON SURVIVORS -----
_STRATEGIES.extend([
    # VIX overlay wrapping each survivor strategy
    StrategyEntry(
        key="vix_overlay_bollinger_rsi_fxe",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="mean_reversion",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="M6E",
        data_path_key="6E",
        requires_features=["atr", "rsi"],
        notes="Batch4. VIX overlay on Bollinger RSI 6E.",
    ),
    StrategyEntry(
        key="vix_overlay_bollinger_rsi_gc",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="mean_reversion",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MGC",
        data_path_key="GC",
        requires_features=["atr", "rsi"],
        notes="Batch4. VIX overlay on Bollinger RSI GC.",
    ),
    StrategyEntry(
        key="vix_overlay_donchian_cl",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="trend",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MCL",
        data_path_key="CL",
        requires_features=["atr"],
        notes="Batch4. VIX overlay on Donchian breakout CL.",
    ),
    StrategyEntry(
        key="vix_overlay_fomc_es",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="calendar",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="Batch4. VIX overlay on FOMC drift ES.",
    ),
    StrategyEntry(
        key="vix_overlay_fomc_zn",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="calendar",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="ZN",
        data_path_key="ZN",
        requires_features=[],
        notes="Batch4. VIX overlay on FOMC drift ZN.",
    ),
])

# ----- BATCH 4: CALENDAR EVENT EXPANSION -----
_STRATEGIES.extend(_multi(
    "boj", "src.strategies.calendar_events_batch4", "BOJStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["6J", "NQ"], [],
    "Batch4. Bank of Japan policy decision drift.",
))
_STRATEGIES.extend(_multi(
    "boe", "src.strategies.calendar_events_batch4", "BOEStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["6B"], [],
    "Batch4. Bank of England policy decision drift.",
))
_STRATEGIES.extend(_multi(
    "ism", "src.strategies.calendar_events_batch4", "ISMStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. ISM Manufacturing PMI drift.",
))
_STRATEGIES.extend(_multi(
    "ppi", "src.strategies.calendar_events_batch4", "PPIStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. PPI release drift.",
))
_STRATEGIES.extend(_multi(
    "gdp", "src.strategies.calendar_events_batch4", "GDPStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. GDP release drift.",
))
_STRATEGIES.extend(_multi(
    "retail_sales", "src.strategies.calendar_events_batch4", "RetailSalesStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. Retail Sales release drift.",
))
