#!/usr/bin/env python3
"""
tick_live_executor.py — L2 Strategy Live Alert Generator
=========================================================
Reads bar parquet files (updated by tick_processor.py on live data),
computes signals for all confirmed strategies, applies hours/session
filters, and outputs trade alerts when signals fire.

NOT an execution engine — generates structured alerts for manual or
API-driven execution.

CONFIRMED STRATEGY PORTFOLIO (38 strategies)
─────────────────────────────────────────────
V1 (from deep analysis + extended analysis):
  1. NQ/cvd_divergence_large_print/30m   Hours: UTC 14,19,20 only
  2. ES/cvd_divergence_large_print/15m   Avoid: UTC 4,8,11,15,18,23
  3. NQ/stop_hunt_reversal/3m            Session: Asian+US only (no London 7-13)
  4. GC/obi_threshold/1m                 All hours
  5. ES/tape_absorption/15m              Session: Asian+US only (no London 7-13)
  6. ES/cvd_divergence/15m               All hours

V2 (stress-tested, all hours):
  7. ES/prev_session_sweep/3m            All hours (100% TS, 1t-Sharpe=1.45)

V3 (stress-tested May 2026):
  8. NQ/range_contraction_break/30m      All hours (100% TS, 1t-Sharpe=5.63, WorstDay=$-3.4k)
  9. GC/session_momentum_follow/3m       All hours (100% TS, 1t-Sharpe=3.22, WorstDay=$-3.0k)

V4 — Deep microstructure / footprint (stress-tested May 2026):
 10. GC/trade_absorption_signal/30m      All hours (100% TS, 1t-Sharpe=4.65, WR=51.5%, WorstDay=$-4.5k)
 11. ES/avg_order_size_divergence/30m    All hours (100% TS, 1t-Sharpe=1.03, WorstDay=$-3.9k)
 12. NQ/trade_absorption_signal/30m      All hours (100% TS, 1t-Sharpe=6.45, WorstDay=$-3.2k, n=21)

V5 — Key-level CVD rejection (stress-tested May 2026):
 13. ES/key_level_cvd_rejection/15m      REVIEW_REQUIRED (5-month data)
 14. NQ/key_level_cvd_rejection/15m      REVIEW_REQUIRED (5-month data)
 15. GC/key_level_cvd_rejection/5m       DISABLED (worst-micro $1,623)

V6/V7/V8 — Pure OHLCV, no CVD (stress-tested 2026-05-18, 23 survivors):
 REVIEW_REQUIRED — long history (GC/SI 2020-2026):
 16. GC/vwap_mean_reversion/30m          1t-Sharpe=2.71, 7/7 yrs, worst-micro=$513
 17. GC/pivot_reversal/30m               1t-Sharpe=2.02, 6/7 yrs, worst-micro=$364
 18. SI/opening_range_fakeout/30m        1t-Sharpe=2.52, 6/7 yrs, worst-micro=$458
 19. SI/consecutive_close_momentum/3m    1t-Sharpe=2.28, 5/7 yrs, worst-micro=$883
 20. GC/pivot_reversal/15m               1t-Sharpe=1.85, 5/6 yrs, worst-micro=$671
 21. SI/ema_crossover/1m                 1t-Sharpe=1.80, 7/7 yrs, worst-micro=$952
 22. SI/vwap_mean_reversion/15m          1t-Sharpe=1.80, 5/7 yrs, worst-micro=$603
 23. SI/opening_range_fakeout/3m         1t-Sharpe=1.50, 5/7 yrs, worst-micro=$395
 DISABLED_FOR_LIVE — long history, worst-micro > $1,000 (re-enable at $5k+ equity):
 24. GC/donchian_breakout/15m            1t-Sharpe=1.93, 6/7 yrs, worst-micro=$1,796
 25. SI/consecutive_close_momentum/5m    1t-Sharpe=2.40, 7/7 yrs, worst-micro=$2,122
 26. SI/ema_crossover/30m                1t-Sharpe=1.95, 6/7 yrs, worst-micro=$1,366
 27. GC/consecutive_close_momentum/15m   1t-Sharpe=1.87, 6/7 yrs, worst-micro=$1,414
 28. SI/ma_slope_regime/30m              1t-Sharpe=1.79, 5/7 yrs, worst-micro=$1,743
 29. SI/ema_crossover/5m                 1t-Sharpe=1.72, 6/7 yrs, worst-micro=$1,202
 30. SI/consecutive_close_momentum/15m   1t-Sharpe=1.69, 6/7 yrs, worst-micro=$1,680
 31. SI/consecutive_close_momentum/1m    1t-Sharpe=1.68, 6/7 yrs, worst-micro=$3,486
 32. GC/close_position_momentum/15m      1t-Sharpe=1.68, 7/7 yrs, worst-micro=$1,088
 REVIEW_REQUIRED — short history (ES/NQ, Dec 2025+, regime check pending):
 33. ES/overnight_gap_fill/30m           1t-Sharpe=4.05, TS=100%, worst-micro=$222
 34. ES/overnight_gap_fill/15m           1t-Sharpe=3.16, TS=100%, worst-micro=$180
 35. NQ/ma_slope_regime/30m              1t-Sharpe=2.89, TS=100%, worst-micro=$311
 36. NQ/inside_bar_breakout/15m          1t-Sharpe=2.77, TS=100%, worst-micro=$413
 37. NQ/vwap_mean_reversion/30m          1t-Sharpe=2.35, TS=98.5%, worst-micro=$475
 38. ES/vwap_mean_reversion/30m          1t-Sharpe=1.93, TS=100%, worst-micro=$202

Usage:
  python tick_live_executor.py                 # single-shot check
  python tick_live_executor.py --poll 60       # poll every 60 seconds
  python tick_live_executor.py --strategy 1    # check single strategy
  python tick_live_executor.py --alert-file alerts.json  # log to file
  python tick_live_executor.py --disable-v2   # exclude v2 strategies
"""

import argparse
import json
import os
import sys
import time
import warnings
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, compute_atr
from tick_strategies import STRATEGY_MAP
from tick_strategies_v2 import STRAT_MAP
from tick_strategies_v3 import STRAT_MAP_V3
from tick_strategies_v4 import STRAT_MAP_V4
from tick_risk_manager import (RiskConfig, RiskManager,
                                recommended_contracts, format_entry_alert)
try:
    from tick_news_monitor import NewsMonitor
    _NEWS_AVAILABLE = True
except Exception:
    _NEWS_AVAILABLE = False

try:
    from tick_tradovate_client import TradovateClient, TradovateOrder
    _TRADOVATE_AVAILABLE = True
except Exception:
    _TRADOVATE_AVAILABLE = False

try:
    from tick_strategies_v5 import STRAT_MAP_V5
    _V5_AVAILABLE = True
except Exception:
    _V5_AVAILABLE = False
    STRAT_MAP_V5 = {}

try:
    from tick_key_levels import compute_key_levels, annotate_alert as _kl_annotate
    _KEY_LEVELS_AVAILABLE = True
except Exception:
    _KEY_LEVELS_AVAILABLE = False

try:
    from tick_state_manager import StateManager
    _STATE_MANAGER_AVAILABLE = True
except Exception:
    _STATE_MANAGER_AVAILABLE = False

try:
    from tick_broker_reconciliation import reconcile_state as _reconcile_state_fn
    _RECONCILIATION_AVAILABLE = True
except Exception:
    _RECONCILIATION_AVAILABLE = False

try:
    from tick_portfolio_coordinator import (
        PortfolioCoordinator, CoordinatorConfig,
        SignalIntent, Side, VirtualStrategyPosition,
    )
    _COORDINATOR_AVAILABLE = True
except Exception:
    _COORDINATOR_AVAILABLE = False
    PortfolioCoordinator = None
    CoordinatorConfig = None
    SignalIntent = None
    Side = None
    VirtualStrategyPosition = None

try:
    from tick_strategies_v6 import STRAT_MAP_V6
    _V6_AVAILABLE = True
except Exception:
    _V6_AVAILABLE = False
    STRAT_MAP_V6 = {}

try:
    from tick_strategies_v7 import STRAT_MAP_V7
    _V7_AVAILABLE = True
except Exception:
    _V7_AVAILABLE = False
    STRAT_MAP_V7 = {}

try:
    from tick_strategies_v8 import STRAT_MAP_V8
    _V8_AVAILABLE = True
except Exception:
    _V8_AVAILABLE = False
    STRAT_MAP_V8 = {}

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
LOG_DIR = ROOT / "06_live_trading" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Safety constants ──────────────────────────────────────────────────────────
KILL_SWITCH_PATH = ROOT / "KILL_SWITCH.txt"
ALLOWLIST_PATH   = Path(__file__).parent / "live_strategy_allowlist.yaml"

# Statuses eligible to run in each mode
_ALLOWED_IN_DRY_RUN = {"ENABLED_DRY_RUN", "DEMO_CANDIDATE", "REVIEW_REQUIRED"}
_ALLOWED_IN_DEMO    = {"DEMO_CANDIDATE"}

# Execution modes — set by CLI flags, never overridden at runtime
MODE_DRY_RUN = "DRY_RUN"   # Default. No orders placed. Alerts only.
MODE_DEMO    = "DEMO"       # Orders to Tradovate DEMO. Requires --demo-auto-trade.
MODE_LIVE    = "LIVE"       # Orders to LIVE account. Requires --live-auto-trade
                             # AND env var FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND.

LIVE_ENABLE_ENV   = "FORTRESS_LIVE_ENABLE"
LIVE_ENABLE_VALUE = "YES_I_UNDERSTAND"


def _has_bracket_orders() -> bool:
    """
    True only when TradovateClient implements place_bracket_order().
    Until that method exists, auto-trading is blocked — stops/targets must be
    broker-native, not only tracked in memory.
    """
    if not _TRADOVATE_AVAILABLE:
        return False
    return hasattr(TradovateClient, "place_bracket_order")


def _check_kill_switch() -> bool:
    """
    Return True if KILL_SWITCH.txt exists and its first non-comment,
    non-empty line is exactly 'STOP'.
    Lines starting with '#' are ignored (comments).
    """
    if not KILL_SWITCH_PATH.exists():
        return False
    try:
        for line in KILL_SWITCH_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return line.upper() == "STOP"
        return False
    except Exception:
        return False


def _load_allowlist() -> dict:
    """Load live_strategy_allowlist.yaml. Returns {strat_id: entry_dict}. Empty dict if not found."""
    if not ALLOWLIST_PATH.exists():
        return {}
    try:
        with open(ALLOWLIST_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {int(k): v for k, v in (data.get("strategies") or {}).items()}
    except Exception as e:
        print(f"WARNING: Could not load allowlist ({ALLOWLIST_PATH}): {e}")
        return {}


def _signal_log_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return LOG_DIR / f"signals_{today}.jsonl"


def _log_signal(record: dict) -> None:
    """Append one signal decision record to today's signal log."""
    try:
        with open(_signal_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # never let logging crash the executor


def _mode_banner(mode: str, tv_client, strategies_active: list,
                 cfg: "RiskConfig", allowlist: dict | None = None,
                 coordinator=None) -> str:
    width = 62
    lines = [
        "=" * width,
        f"  FORTRESS EXECUTOR — MODE: {mode}",
        "-" * width,
    ]
    if mode == MODE_DRY_RUN:
        lines.append("  ** DRY RUN — No orders will be placed **")
    elif mode == MODE_DEMO:
        lines.append("  ** DEMO MODE — Tradovate paper account **")
        if tv_client:
            try:
                lines.append(f"  {tv_client.status_line()}")
            except Exception:
                pass
    elif mode == MODE_LIVE:
        lines += [
            "  *** LIVE MODE — REAL MONEY AT RISK ***",
            "  *** REAL MONEY AT RISK — REAL MONEY AT RISK ***",
        ]
        if tv_client:
            try:
                lines.append(f"  {tv_client.status_line()}")
            except Exception:
                pass

    allowlist_line = ""
    if allowlist:
        disabled = sum(1 for e in allowlist.values() if e.get("status") == "DISABLED_FOR_LIVE")
        review   = sum(1 for e in allowlist.values() if e.get("status") == "REVIEW_REQUIRED")
        allowlist_line = f"  Allowlist: {len(strategies_active)} active | {disabled} disabled | {review} under review"

    lines += [
        "-" * width,
        f"  Kill switch: {KILL_SWITCH_PATH}",
        f"  Signal log:  {_signal_log_path()}",
        f"  Max trade risk:  ${cfg.max_trade_risk_usd:,.0f}",
        f"  Max contracts:   {MAX_CONTRACTS_PER_TRADE}",
        f"  Portfolio halt:  -${cfg.max_portfolio_daily_loss_usd:,.0f}/day",
        f"  Account halt:    -${cfg.max_account_trailing_dd_usd:,.0f} trailing DD",
        f"  Strategies:      {len(strategies_active)} active",
    ]
    if allowlist_line:
        lines.append(allowlist_line)
    if coordinator is not None:
        c = coordinator.config
        lines += [
            f"  Portfolio coordinator: ENABLED",
            f"  Coord max_net_contracts_per_symbol: {c.max_net_contracts_per_symbol}",
            f"  Coord max_total_open_symbols:       {c.max_total_open_symbols}",
            f"  Coord one_strategy_only_demo:       {c.one_strategy_only_demo}",
            f"  Coord demo_strategy_key:            {c.demo_strategy_key}",
        ]
    else:
        lines.append("  Portfolio coordinator: DISABLED (import failed)")
    lines.append("=" * width)
    return "\n".join(lines)


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Confirmed portfolio configuration ────────────────────────────────────────


# ── CONTRACT SIZING ───────────────────────────────────────────────────────────
# USE_MICROS: True  → trade MES/MNQ/MGC (1/10 the dollar risk)
#             False → trade full ES/NQ/GC
# STRONGLY RECOMMENDED: keep True until accounts recover to peak equity.
# At $1k remaining per account, full-contract worst days can be $3k-$10k.
# Micro worst days: $300-$1,000 — safe.
USE_MICROS = True

# Symbol → micro equivalent mapping
MICRO_MAP = {
    "GC": "MGC",   # $10/pt  (full GC = $100/pt)
    "ES": "MES",   # $5/pt   (full ES = $50/pt)
    "NQ": "MNQ",   # $2/pt   (full NQ = $20/pt)
    "SI": "SIL",   # $1000/pt (full SI = $5000/pt)
}

def resolve_symbol(base: str) -> str:
    """Return micro or full symbol based on USE_MICROS flag."""
    if USE_MICROS and base in MICRO_MAP:
        return MICRO_MAP[base]
    return base


# ── PORTFOLIO ─────────────────────────────────────────────────────────────────
# Signals computed on FULL-size bars (GC/ES/NQ parquet files).
# Execution uses micro contracts when USE_MICROS=True.
# id  base_symbol  bar  strategy_name  params  allowed_hours  session_block  version
PORTFOLIO = [
    (1, "GC", 1,  "obi_threshold",
     {"threshold": 0.3, "smooth_window": 1},
     None,            None,      "v1"),

    (2, "ES", 15, "cvd_divergence_large_print",
     {"price_window": 20, "cvd_window": 10, "min_large": 1},
     {0,1,2,3,5,6,7,9,10,12,13,14,16,17,19,20,21,22},  # exclude 4,8,11,15,18,23
     None,      "v1"),

    (3, "ES", 15, "cvd_divergence",
     {"price_window": 40, "cvd_window": 20, "threshold": 0.3},
     None,            None,      "v1"),

    (4, "ES", 15, "tape_absorption",
     {"price_window": 5, "vol_z_threshold": 1.5, "price_threshold": 0.001},
     None,            "london",  "v1"),    # avoid London 7-13 UTC

    (5, "NQ", 30, "cvd_divergence_large_print",
     {"price_window": 20, "cvd_window": 10, "min_large": 2},
     {14, 19, 20},   None,      "v1"),    # ONLY UTC 14,19,20

    (6, "NQ", 3,  "stop_hunt_reversal",
     {"spike_bars": 1, "spike_pct": 0.001, "cvd_flip_window": 5},
     None,            "london",  "v1"),   # avoid London 7-13 UTC

    (7, "ES", 3,  "prev_session_sweep",
     {"level_window": 20, "cvd_flip_window": 3, "sweep_buffer": 0.0001},
     None,            None,      "v2"),

    # ── V3 new strategies (stress-tested May 2026) ────────────────────────────
    (8, "NQ", 30, "range_contraction_break",
     {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5},
     None,            None,      "v3"),    # 1t-Sharpe=5.63, 100% TS comply, worst-day $-3,439

    (9, "GC",  3, "session_momentum_follow",
     {"bias_z": 1.0, "follow_bars": 8, "break_pct": 0.0002},
     None,            None,      "v3"),    # 1t-Sharpe=3.22, 100% TS comply, worst-day $-3,042

    # ── V4 — Deep microstructure strategies (stress-tested May 2026) ──────────
    (10, "GC", 30, "trade_absorption_signal",
     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},
     None,            None,      "v4"),    # 1t-Sharpe=4.65, WR=51.5%, 5/7 yrs, 100% TS — STAR

    (11, "ES", 30, "avg_order_size_divergence",
     {"window": 20, "z_thresh": 1.0, "price_thresh": 0.001},
     None,            None,      "v4"),    # 1t-Sharpe=1.03, institutional fingerprint, 100% TS

    (12, "NQ", 30, "trade_absorption_signal",
     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},
     None,            None,      "v4"),    # 1t-Sharpe=6.45, low trade count — monitor closely

    # ── V5 — Key-level CVD rejection (stress-tested May 2026) ────────────────
    # Fires when price tests rolling high/low AND CVD confirms rejection.
    # ES/NQ: 5-month data only — regime check pending. REVIEW_REQUIRED.
    # GC: 7/7 years positive (2020-2026), 99%+ TS compliance. REVIEW_REQUIRED.
    (13, "ES", 15, "key_level_cvd_rejection",
     {"key_level_window": 30, "cvd_window": 10, "rejection_atr_pct": 0.25},
     None,            None,      "v5"),    # 1t-Sharpe=1.89, 125 trades, TS=100% (5-month window)

    (14, "NQ", 15, "key_level_cvd_rejection",
     {"key_level_window": 30, "cvd_window": 20, "rejection_atr_pct": 0.5},
     None,            None,      "v5"),    # 1t-Sharpe=2.10, 135 trades, TS=100% (5-month window)

    (15, "GC",  5, "key_level_cvd_rejection",
     {"key_level_window": 10, "cvd_window": 10, "rejection_atr_pct": 1.0},
     None,            None,      "v5"),    # 1t-Sharpe=0.92, 7/7 yrs, TS=99.3%, borderline Sharpe

    # ── V6/V7/V8 — Pure OHLCV strategies (stress-tested 2026-05-18) ─────────────
    # 23 PASS survivors. Near-zero correlation to V1-V5 CVD strategies.
    # Long-history (GC/SI): 2020-2026. Short-history (ES/NQ): Dec 2025+ only.
    # Allowlist controls dry-run eligibility — see live_strategy_allowlist.yaml.

    # REVIEW_REQUIRED — long history, worst_micro <= $1,000
    (16, "GC", 30, "vwap_mean_reversion",
     {"z_thresh": 2.5, "vwap_window": 10},
     None,            None,      "v6"),    # 1t-Sharpe=2.71, 7/7 yrs, TS=99.5%, worst-micro=$513

    (17, "GC", 30, "pivot_reversal",
     {"pivot_bars": 10, "bounce_atr_mult": 0.2, "atr_win": 10},
     None,            None,      "v8"),    # 1t-Sharpe=2.02, 6/7 yrs, TS=100%, worst-micro=$364

    (18, "SI", 30, "opening_range_fakeout",
     {"orb_bars": 12, "reentry_atr_pct": 0.2, "atr_window": 14},
     None,            None,      "v6"),    # 1t-Sharpe=2.52, 6/7 yrs, TS=97.9%, worst-micro=$458

    (19, "SI",  3, "consecutive_close_momentum",
     {"n": 5},
     None,            None,      "v8"),    # 1t-Sharpe=2.28, 5/7 yrs, TS=98.4%, worst-micro=$883

    (20, "GC", 15, "pivot_reversal",
     {"pivot_bars": 20, "bounce_atr_mult": 0.2, "atr_win": 14},
     None,            None,      "v8"),    # 1t-Sharpe=1.85, 5/6 yrs, TS=98.4%, worst-micro=$671

    (21, "SI",  1, "ema_crossover",
     {"fast": 5, "slow": 34, "slope_bars": 5},
     None,            None,      "v7"),    # 1t-Sharpe=1.80, 7/7 yrs, TS=98.4%, worst-micro=$952

    (22, "SI", 15, "vwap_mean_reversion",
     {"z_thresh": 2.5, "vwap_window": 10},
     None,            None,      "v6"),    # 1t-Sharpe=1.80, 5/7 yrs, TS=97.9%, worst-micro=$603

    (23, "SI",  3, "opening_range_fakeout",
     {"orb_bars": 3, "reentry_atr_pct": 0.05, "atr_window": 14},
     None,            None,      "v6"),    # 1t-Sharpe=1.50, 5/7 yrs, TS=100%, worst-micro=$395

    # DISABLED_FOR_LIVE — long history, worst_micro > $1,000 (re-enable at $5k+ equity)
    (24, "GC", 15, "donchian_breakout",
     {"n": 40, "confirm": 1},
     None,            None,      "v7"),    # 1t-Sharpe=1.93, 6/7 yrs, TS=99.6%, worst-micro=$1,796

    (25, "SI",  5, "consecutive_close_momentum",
     {"n": 5},
     None,            None,      "v8"),    # 1t-Sharpe=2.40, 7/7 yrs, TS=96.6%, worst-micro=$2,122

    (26, "SI", 30, "ema_crossover",
     {"fast": 13, "slow": 34, "slope_bars": 3},
     None,            None,      "v7"),    # 1t-Sharpe=1.95, 6/7 yrs, TS=95.9%, worst-micro=$1,366

    (27, "GC", 15, "consecutive_close_momentum",
     {"n": 5},
     None,            None,      "v8"),    # 1t-Sharpe=1.87, 6/7 yrs, TS=99.6%, worst-micro=$1,414

    (28, "SI", 30, "ma_slope_regime",
     {"ma_win": 20, "slope_bars": 3, "entry_rsi_win": 14, "rsi_ob": 60, "rsi_os": 40},
     None,            None,      "v8"),    # 1t-Sharpe=1.79, 5/7 yrs, TS=96.7%, worst-micro=$1,743

    (29, "SI",  5, "ema_crossover",
     {"fast": 5, "slow": 34, "slope_bars": 5},
     None,            None,      "v7"),    # 1t-Sharpe=1.72, 6/7 yrs, TS=96.9%, worst-micro=$1,202

    (30, "SI", 15, "consecutive_close_momentum",
     {"n": 5},
     None,            None,      "v8"),    # 1t-Sharpe=1.69, 6/7 yrs, TS=96.1%, worst-micro=$1,680

    (31, "SI",  1, "consecutive_close_momentum",
     {"n": 5},
     None,            None,      "v8"),    # 1t-Sharpe=1.68, 6/7 yrs, TS=97.9%, worst-micro=$3,486

    (32, "GC", 15, "close_position_momentum",
     {"cp_window": 5, "cp_thresh": 0.75},
     None,            None,      "v8"),    # 1t-Sharpe=1.68, 7/7 yrs, TS=99.6%, worst-micro=$1,088

    # REVIEW_REQUIRED — short history (ES/NQ, Dec 2025+), regime check skipped
    (33, "ES", 30, "overnight_gap_fill",
     {"gap_atr_mult": 0.3, "atr_window": 14},
     None,            None,      "v6"),    # 1t-Sharpe=4.05, TS=100%, worst-micro=$222 — STAR short-window

    (34, "ES", 15, "overnight_gap_fill",
     {"gap_atr_mult": 0.3, "atr_window": 14},
     None,            None,      "v6"),    # 1t-Sharpe=3.16, TS=100%, worst-micro=$180 — lowest worst-micro

    (35, "NQ", 30, "ma_slope_regime",
     {"ma_win": 15, "slope_bars": 3, "entry_rsi_win": 14, "rsi_ob": 60, "rsi_os": 40},
     None,            None,      "v8"),    # 1t-Sharpe=2.89, TS=100%, worst-micro=$311

    (36, "NQ", 15, "inside_bar_breakout",
     {"n_inside": 2, "breakout_confirm": 0},
     None,            None,      "v8"),    # 1t-Sharpe=2.77, TS=100%, worst-micro=$413

    (37, "NQ", 30, "vwap_mean_reversion",
     {"z_thresh": 2.5, "vwap_window": 40},
     None,            None,      "v6"),    # 1t-Sharpe=2.35, TS=98.5%, worst-micro=$475

    (38, "ES", 30, "vwap_mean_reversion",
     {"z_thresh": 2.5, "vwap_window": 10},
     None,            None,      "v6"),    # 1t-Sharpe=1.93, TS=100%, worst-micro=$202
]

# ── Max contracts cap — HARD LIMIT to enforce $200 max risk per trade ─────────
# On micros with 1 contract: MES ~$45, MNQ ~$120, MGC ~$15 per trade.
# All safely under $200. Use 1 contract until account recovers to $5k+.
MAX_CONTRACTS_PER_TRADE = 1

# London session: UTC 7–13
LONDON_HOURS = set(range(7, 14))

# ATR-based stop/target multipliers (match backtest)
STOP_MULT  = 1.5
TP_MULT    = 3.0
ATR_WINDOW = 14

# ── Risk configuration ───────────────────────────────────────────────────────
# Micro mode: limits are 1/10 of full-contract levels.
# Full mode:  tighten limits further if accounts have little runway.
if USE_MICROS:
    RISK_CFG = RiskConfig(
        max_trade_risk_usd            = 200.0,   # MES ~$44, MNQ ~$50, MGC ~$162 — all pass
        max_hold_bars                 = 50,
        # Ratchet trailing stop: works with 1 contract (partial exits impossible at qty=1)
        # At +1.5R: lock 0.5R profit. At +2.5R: lock 1.5R profit. Full exit at +3R.
        use_ratchet                   = True,
        ratchet_1_r                   = 1.5,
        ratchet_1_lock_r              = 0.5,
        ratchet_2_r                   = 2.5,
        ratchet_2_lock_r              = 1.5,
        full_tp_r                     = 3.0,
        max_consecutive_losses        = 3,       # halt strategy after 3 losses in a row
        max_strategy_daily_loss_usd   = 250.0,   # halt strategy at -$250/day (micro)
        max_portfolio_daily_loss_usd  = 600.0,   # halt portfolio at -$600/day (micro)
        max_account_trailing_dd_usd   = 800.0,   # halt at $800 trailing DD (keeps $200 buffer)
        topstep_daily_limit_usd       = 450.0,   # Apex micro daily limit ~$500
    )
else:
    RISK_CFG = RiskConfig(
        max_trade_risk_usd            = 500.0,
        max_hold_bars                 = 50,
        use_ratchet                   = True,
        ratchet_1_r                   = 1.5,
        ratchet_1_lock_r              = 0.5,
        ratchet_2_r                   = 2.5,
        ratchet_2_lock_r              = 1.5,
        full_tp_r                     = 3.0,
        max_consecutive_losses        = 3,
        max_strategy_daily_loss_usd   = 800.0,
        max_portfolio_daily_loss_usd  = 1500.0,
        max_account_trailing_dd_usd   = 1500.0,
        topstep_daily_limit_usd       = 4000.0,
    )

# Total account equity across all accounts. Adjust to current real value.
# 10 accounts × ~$4,900 each after $1k DD = ~$49k combined.
ACCOUNT_EQUITY = 49_000.0

# ── Legacy constant ───────────────────────────────────────────────────────────
MAX_TRADE_RISK_USD = RISK_CFG.max_trade_risk_usd


# ── Micro-aware SPECS lookup ──────────────────────────────────────────────────
MICRO_SPECS = {
    "MGC": {"point_value": 10.0,   "tick_size": 0.10,  "commission": 2.0},
    "MES": {"point_value": 5.0,    "tick_size": 0.25,  "commission": 2.0},
    "MNQ": {"point_value": 2.0,    "tick_size": 0.25,  "commission": 2.0},
    "SIL": {"point_value": 1000.0, "tick_size": 0.005, "commission": 2.0},
}

# Tradovate-ready contract symbols — UPDATE EACH QUARTERLY ROLLOVER (June → Sep → Dec → Mar)
TV_CONTRACT_MAP = {
    "MGC": "MGCM5",   # micro gold
    "MES": "MESM5",   # micro S&P
    "MNQ": "MNQM5",   # micro NQ
    "SIL": "SILM5",   # micro silver
    "GC":  "GCM5",    # full gold (fallback)
    "ES":  "ESM5",    # full ES  (fallback)
    "NQ":  "NQM5",    # full NQ  (fallback)
}


def get_spec(base_symbol: str) -> dict:
    """Return the spec for the traded instrument (micro or full)."""
    if USE_MICROS and base_symbol in MICRO_MAP:
        micro = MICRO_MAP[base_symbol]
        return MICRO_SPECS.get(micro, SPECS[base_symbol])
    return SPECS[base_symbol]


# ── Bar loading ───────────────────────────────────────────────────────────────

def load_bars(symbol: str, bar_min: int, lookback: int = 500) -> pd.DataFrame | None:
    path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    return df.iloc[-lookback:] if len(df) > lookback else df


# ── Hours filter ──────────────────────────────────────────────────────────────

def hours_allowed(ts: pd.Timestamp, allowed_hours: set | None,
                  session_block: str | None) -> bool:
    h = ts.hour
    if session_block == "london" and h in LONDON_HOURS:
        return False
    if allowed_hours is not None and h not in allowed_hours:
        return False
    return True


# ── Signal computation ────────────────────────────────────────────────────────

def compute_signal(df: pd.DataFrame, strat_name: str,
                   params: dict, version: str) -> pd.Series:
    if version == "v1":
        strat = STRATEGY_MAP.get(strat_name)
    elif version == "v2":
        strat = STRAT_MAP.get(strat_name)
    elif version == "v3":
        strat = STRAT_MAP_V3.get(strat_name)
    elif version == "v5":
        strat = STRAT_MAP_V5.get(strat_name)
    elif version == "v6":
        strat = STRAT_MAP_V6.get(strat_name)
    elif version == "v7":
        strat = STRAT_MAP_V7.get(strat_name)
    elif version == "v8":
        strat = STRAT_MAP_V8.get(strat_name)
    else:
        strat = STRAT_MAP_V4.get(strat_name)
    if strat is None:
        raise KeyError(f"Strategy '{strat_name}' not found (version={version})")
    return strat["compute"](df, **params)


# ── ATR for stop/target ───────────────────────────────────────────────────────

def current_atr(df: pd.DataFrame) -> float:
    hi = df["high"].values
    lo = df["low"].values
    cl = df["close"].values
    atr = compute_atr(hi, lo, cl, ATR_WINDOW)
    last = atr[-1]
    return float(last) if not np.isnan(last) else float(np.nanmean(atr[-20:]))


# ── Alert builder ─────────────────────────────────────────────────────────────

def build_alert(strat_id: int, symbol: str, bar_min: int, strat_name: str,
                version: str, direction: int, bar_ts: pd.Timestamp,
                entry_px: float, atr: float) -> dict:
    # symbol here is already the traded instrument (may be micro)
    spec  = MICRO_SPECS.get(symbol) or SPECS.get(symbol) or SPECS["ES"]
    pv    = spec["point_value"]
    tick  = spec["tick_size"]
    stop  = entry_px - direction * STOP_MULT * atr
    tgt   = entry_px + direction * TP_MULT   * atr
    risk  = abs(entry_px - stop) * pv
    rrr   = abs(tgt - entry_px) / abs(entry_px - stop) if entry_px != stop else 0.0

    # Round to tick size
    def _round(px): return round(round(px / tick) * tick, 6)

    return {
        "alert_time":    datetime.now(timezone.utc).isoformat(),
        "bar_time":      bar_ts.isoformat(),
        "strategy_id":   strat_id,
        "strategy":      strat_name,
        "version":       version,
        "symbol":        symbol,
        "bar_minutes":   bar_min,
        "action":        "BUY" if direction == 1 else "SELL",
        "direction":     direction,
        "entry_px":      _round(entry_px),
        "stop_px":       _round(stop),
        "target_px":     _round(tgt),
        "atr":           round(atr, 4),
        "risk_per_contract": round(risk, 2),
        "rr_ratio":      round(rrr, 2),
        "point_value":   pv,
        "tick_size":     tick,
    }


def format_alert(a: dict) -> str:
    arrow = "↑ LONG " if a["direction"] == 1 else "↓ SHORT"
    return (
        f"  [{a['strategy_id']}] {a['symbol']}/{a['strategy']}/{a['bar_minutes']}m "
        f"({a['version'].upper()})  {arrow}\n"
        f"      Bar:    {a['bar_time']}\n"
        f"      Entry:  {a['entry_px']}\n"
        f"      Stop:   {a['stop_px']}  (risk ${a['risk_per_contract']:,.0f}/contract)\n"
        f"      Target: {a['target_px']}  (R:R = {a['rr_ratio']:.1f})\n"
        f"      ATR:    {a['atr']}"
    )


def format_close(strat_id: int, symbol: str, bar_min: int, strat_name: str,
                 version: str, reason: str, bar_ts: pd.Timestamp) -> str:
    return (
        f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m ({version.upper()})  "
        f"✕ CLOSE ({reason})  @ {bar_ts.isoformat()}"
    )


# ── State tracker ─────────────────────────────────────────────────────────────

class PositionTracker:
    """Tracks open positions per strategy to detect signal changes."""
    def __init__(self):
        self._state: dict[int, int] = {}   # strat_id → current_signal (-1/0/1)

    def update(self, strat_id: int, new_sig: int) -> tuple[str, int]:
        """
        Returns (event, direction):
          event: 'entry', 'close', 'flip', 'hold', 'flat'
        """
        prev = self._state.get(strat_id, 0)
        self._state[strat_id] = new_sig

        if prev == 0 and new_sig != 0:
            return "entry", new_sig
        if prev != 0 and new_sig == 0:
            return "close", prev
        if prev != 0 and new_sig != 0 and prev != new_sig:
            return "flip", new_sig
        if prev != 0 and new_sig == prev:
            return "hold", new_sig
        return "flat", 0

    def current(self, strat_id: int) -> int:
        return self._state.get(strat_id, 0)

    def to_dict(self) -> dict:
        return dict(self._state)


# ── Contract rollover warning ─────────────────────────────────────────────────

# Update these dates each time the front-month contract changes.
# Quarterly: Mar (H), Jun (M), Sep (U), Dec (Z).
_CONTRACT_EXPIRY = {
    "MESM5": "2026-06-20",
    "MGCM5": "2026-06-27",
    "MNQM5": "2026-06-20",
    "MESU5": "2026-09-19",  # next quarter, pre-populated for easy swap
    "MGCU5": "2026-09-26",
    "MNQU5": "2026-09-19",
}

def _contract_rollover_warning() -> list[str]:
    """Warn if any active contract expires within 7 days."""
    now  = datetime.now(timezone.utc).date()
    warns = []
    for sym, tv_sym in TV_CONTRACT_MAP.items():
        expiry_str = _CONTRACT_EXPIRY.get(tv_sym)
        if not expiry_str:
            continue
        from datetime import date as _date
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        days   = (expiry - now).days
        if days <= 7:
            warns.append(
                f"CONTRACT EXPIRY: {tv_sym} expires {expiry_str} "
                f"({days} days) — update TV_CONTRACT_MAP before rollover"
            )
    return warns


# ── Stale data detection ──────────────────────────────────────────────────────

def _check_stale(df: pd.DataFrame, symbol: str, bar_min: int,
                 max_stale_min: int = 20) -> str | None:
    """
    Return a warning string if the newest bar is more than max_stale_min old.
    Suppressed on weekends (Saturday UTC 00:00 – Monday UTC 00:00) since
    CME futures are closed and no new bars are expected.
    """
    if df is None or df.empty:
        return None
    now = pd.Timestamp.now(tz="UTC")
    # Saturday=5, Sunday=6
    if now.weekday() in (5, 6):
        return None
    last = df.index[-1]
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    age = (now - last).total_seconds() / 60
    if age > max_stale_min:
        return f"STALE: {symbol}/{bar_min}m last bar {age:.0f}min ago (>{max_stale_min}min)"
    return None


# ── Correlation groups ────────────────────────────────────────────────────────
# ES and NQ are ~90% correlated. Holding both long (or both short) at once
# effectively doubles the position size. Log a warning when this occurs.

_CORR_ES = frozenset({2, 3, 4, 7, 11, 13})    # ES-based strategy IDs
_CORR_NQ = frozenset({5, 6, 8, 12, 14})     # NQ-based strategy IDs
_CORR_GC = frozenset({1, 9, 10, 15})        # GC-based strategy IDs


def _correlation_warning(tracker: PositionTracker) -> str | None:
    """
    Return a warning if ES and NQ strategies are simultaneously in the same
    direction. GC is uncorrelated — not checked here.
    """
    es_dirs = [tracker.current(s) for s in _CORR_ES if tracker.current(s) != 0]
    nq_dirs = [tracker.current(s) for s in _CORR_NQ if tracker.current(s) != 0]
    if not es_dirs or not nq_dirs:
        return None
    es_dir = es_dirs[0]
    nq_dir = nq_dirs[0]
    if es_dir == nq_dir:
        d = "LONG" if es_dir == 1 else "SHORT"
        return (f"CORRELATION: ES strategies {d} + NQ strategies {d} "
                f"— correlated exposure doubled, risk is 2× a single position")
    return None


# ── Gate 7: Startup reconciliation + periodic broker sync ────────────────────

def _strip_contract_month(tv_symbol: str) -> str:
    """
    Strip contract month/year suffix to get the base symbol.
    "MESM5" → "MES", "MGCU5" → "MGC", "ESZ4" → "ES"
    Month codes: F G H J K M N Q U V X Z
    """
    _month_codes = set("FGHJKMNQUVXZ")
    s = tv_symbol.upper()
    # Walk backward: skip digits, then check for a month code letter
    i = len(s) - 1
    while i > 0 and s[i].isdigit():
        i -= 1
    if i > 0 and s[i] in _month_codes:
        return s[:i]
    return tv_symbol


def _build_sym_strat_map(portfolio: list) -> dict[str, list[int]]:
    """
    Build {traded_symbol: [strat_id, ...]} from the active PORTFOLIO.
    Uses the same micro-symbol resolution as the executor.
    """
    mapping: dict[str, list[int]] = {}
    for entry in portfolio:
        sid, base_sym = entry[0], entry[1]
        traded = MICRO_MAP.get(base_sym, base_sym) if USE_MICROS else base_sym
        mapping.setdefault(traded, []).append(sid)
    return mapping


def _reconcile_positions(tv_client, tracker: PositionTracker, rm: RiskManager,
                          portfolio: list, verbose: bool = True) -> list[dict]:
    """
    Gate 7 — Startup reconciliation.

    Fetch open positions from the broker and populate PositionTracker +
    RiskManager so the executor does not re-enter existing positions after
    a restart.

    How it works:
    • Calls tv_client.get_positions() (REST call — requires authentication)
    • For each non-zero broker position, resolves the base symbol to the
      matching active strategy IDs
    • Updates PositionTracker: strategies marked in-position cannot re-enter
    • Adds an approximate TradeRecord to RiskManager using current bar ATR
      (stops at broker OSO orders are the real protection; RM tracks lifecycle)

    Returns list of reconciled position dicts (empty if none found).
    """
    print("\n" + "=" * 60)
    print("  GATE 7 — STARTUP RECONCILIATION")
    print("=" * 60)

    try:
        positions = tv_client.get_positions()
    except Exception as e:
        print(f"\n  [Reconcile] WARNING: Could not fetch broker positions: {e}")
        print(f"  [Reconcile] Proceeding with empty state.")
        print(f"  [Reconcile] If positions are open at the broker they are UNTRACKED.")
        print(f"  [Reconcile] Monitor manually — do NOT let executor re-enter them.")
        print("=" * 60)
        return []

    if not positions:
        print("  [Reconcile] Broker reports no open positions — clean slate confirmed.")
        print("=" * 60)
        return []

    sym_map   = _build_sym_strat_map(portfolio)
    reconciled = []

    for pos in positions:
        symbol    = pos.symbol
        net_pos   = pos.net_pos
        avg_price = pos.avg_price

        if net_pos == 0:
            continue

        direction = 1 if net_pos > 0 else -1
        dir_str   = "LONG" if direction == 1 else "SHORT"
        base      = _strip_contract_month(symbol)
        strat_ids = sym_map.get(base, [])

        if not strat_ids:
            # Position in a symbol not covered by any active strategy
            print(f"\n  [Reconcile] WARNING: {symbol} ({dir_str} x{abs(net_pos)}) "
                  f"has no active strategies — cannot auto-track.")
            print(f"  [Reconcile]   Close manually: {symbol} x{abs(net_pos)} {dir_str} @ {avg_price:.4f}")
            continue

        # Load bar data for ATR estimation
        base_for_bars = next(
            (p[1] for p in portfolio if p[0] == strat_ids[0]), base
        )
        bar_min_for_atr = next(
            (p[2] for p in portfolio if p[0] == strat_ids[0]), 15
        )
        df  = load_bars(base_for_bars, bar_min_for_atr, lookback=50)
        atr = current_atr(df) if (df is not None and len(df) >= ATR_WINDOW + 2) else 10.0

        for sid in strat_ids:
            tracker.update(sid, direction)
            spec = get_spec(base_for_bars)
            rm.open_trade(
                strat_id=sid, symbol=symbol, direction=direction,
                entry_px=avg_price, atr=atr,
                point_value=spec["point_value"],
                commission=spec.get("commission", 2.0),
            )

        reconciled.append({
            "symbol":    symbol,
            "base":      base,
            "direction": direction,
            "avg_price": avg_price,
            "net_pos":   net_pos,
            "strat_ids": strat_ids,
        })
        print(f"  [Reconcile] {symbol:<10} {dir_str:<5} x{abs(net_pos)}  "
              f"avg={avg_price:.4f}  ATR~{atr:.2f}  "
              f"-> strategies {strat_ids}")

    if reconciled:
        print(f"\n  [Reconcile] {len(reconciled)} position(s) reconciled.")
        print(f"  [Reconcile] Stops/targets are held at the broker (OSO orders).")
        print(f"  [Reconcile] No new entries will be taken for reconciled strategies.")
    else:
        print("  [Reconcile] All non-zero positions mapped successfully.")

    print("=" * 60)
    return reconciled


def _sync_broker_state(tv_client, tracker: PositionTracker, rm: RiskManager,
                        portfolio: list, verbose: bool = False) -> list[int]:
    """
    Lightweight broker sync run on every pass in DEMO/LIVE mode.

    Detects positions that were closed at the broker (stop hit / target hit)
    while the executor was running, and updates the tracker so the strategy
    can re-enter on the next valid signal.

    Returns list of strategy IDs whose tracked positions were found to be
    closed at the broker.
    """
    try:
        broker_positions = tv_client.get_positions()
    except Exception:
        return []

    # Build {base_symbol: net_pos} from broker
    broker_open: dict[str, int] = {}
    for pos in broker_positions:
        if pos.net_pos != 0:
            base = _strip_contract_month(pos.symbol)
            broker_open[base] = pos.net_pos

    sym_map     = _build_sym_strat_map(portfolio)
    closed_ids  = []

    for base, strat_ids in sym_map.items():
        broker_is_flat = base not in broker_open
        for sid in strat_ids:
            if tracker.current(sid) != 0 and broker_is_flat:
                # We think we're in a position; broker says flat → closed by stop/target
                direction = tracker.current(sid)
                dir_str   = "LONG" if direction == 1 else "SHORT"
                if verbose:
                    print(f"  [Sync] Strategy {sid} ({base} {dir_str}): "
                          f"position closed at broker — updating tracker")
                tracker.update(sid, 0)
                if sid in rm._open:
                    rm.signal_close(sid, rm._open[sid].entry_px)
                closed_ids.append(sid)

    if closed_ids and verbose:
        print(f"  [Sync] Detected {len(closed_ids)} broker-side close(s): {closed_ids}")

    return closed_ids


# ── News bias helper ─────────────────────────────────────────────────────────

def _get_bias_for_symbol(symbol: str, news_bias: dict | None) -> int:
    """Return +1/0/-1 directional bias for `symbol` from the bias dict."""
    if not news_bias:
        return 0
    if symbol in ("ES", "NQ"):
        return int(news_bias.get("es_nq_bias", 0))
    if symbol == "GC":
        return int(news_bias.get("gc_bias", 0))
    return 0


# ── Main check loop ───────────────────────────────────────────────────────────

def check_all_strategies(tracker: PositionTracker, rm: RiskManager,
                          disable_v2: bool, verbose: bool = True,
                          tv_client=None, mode: str = MODE_DRY_RUN,
                          block_new_entries: bool = False,
                          news_bias: dict | None = None,
                          sm=None,
                          coordinator=None) -> list[dict]:
    """
    Run one check pass across all portfolio strategies.
    Returns list of alert dicts for any new signal entries.
    Every signal decision (accepted or rejected) is logged to JSONL.

    block_new_entries: if True (e.g. during a news window), bar updates
    and exit logic still run, but no new entries are opened.
    """
    fired = []

    for (strat_id, symbol, bar_min, strat_name, params,
         allowed_hours, session_block, version) in PORTFOLIO:

        if disable_v2 and version == "v2":
            continue

        df = load_bars(symbol, bar_min, lookback=500)
        if df is None or len(df) < ATR_WINDOW + 5:
            if verbose:
                print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m — no data")
            continue

        # Stale data warning — does not block execution
        stale = _check_stale(df, symbol, bar_min)
        if stale and verbose:
            print(f"  [{strat_id}] ⚠ {stale}")

        last_bar_ts = df.index[-1]
        if sm:
            sm.update_last_seen_bar(symbol, last_bar_ts.isoformat(), bar_min)
        spec        = get_spec(symbol)
        traded_sym  = resolve_symbol(symbol)
        atr         = current_atr(df)
        entry_p     = float(df["close"].iloc[-1])
        bar_high    = float(df["high"].iloc[-1])
        bar_low     = float(df["low"].iloc[-1])

        # ── Bar update for open trades (stop/target/timeout/ratchet) ────────
        bar_exits = rm.update_bar(strat_id, bar_high, bar_low, entry_p)
        for ex in bar_exits:
            reason = ex["reason"]
            if verbose:
                pnl = ex.get("pnl", 0.0)
                if reason == "ratchet_1":
                    print(f"  [{strat_id}] {symbol}  RATCHET 1: "
                          f"stop → {ex['new_stop']:.4f}  "
                          f"(+{ex['new_stop_r']}R locked in)")
                elif reason == "ratchet_2":
                    print(f"  [{strat_id}] {symbol}  RATCHET 2: "
                          f"stop → {ex['new_stop']:.4f}  "
                          f"(+{ex['new_stop_r']}R locked in)")
                elif reason == "partial_tp":
                    print(f"  [{strat_id}] {symbol}  PARTIAL TP "
                          f"@ {ex['exit_px']:.4f}  P&L ${pnl:+,.0f}  "
                          f"(50% closed, stop → B/E)")
                else:
                    print(f"  [{strat_id}] {symbol}  EXIT ({reason}) "
                          f"@ {ex['exit_px']:.4f}  "
                          f"trade P&L ${ex.get('total_trade_pnl', pnl):+,.0f}")
                    consec = ex.get("consecutive_losses", 0)
                    if consec >= 2:
                        print(f"  [{strat_id}] consecutive losses: {consec} "
                              f"(circuit breaker at {RISK_CFG.max_consecutive_losses})")
                if ex.get("account_halt"):
                    print(f"\n  *** ACCOUNT HALTED: {ex['account_halt']} ***")
            # Log every exit event to JSONL (entries, ratchets, and full closes)
            _log_signal({
                "event_type":   "exit",
                "timestamp":    datetime.now(timezone.utc).isoformat(),
                "mode":         mode,
                "strategy_id":  strat_id,
                "strategy":     strat_name,
                "symbol":       traded_sym,
                "bar_minutes":  bar_min,
                "version":      version,
                **{k: ex[k] for k in (
                    "reason", "exit_px", "pnl",
                    "direction", "entry_px", "r_multiple",
                    "bar_count", "ratchet_1_done", "ratchet_2_done",
                ) if k in ex},
                "total_trade_pnl": ex.get("total_trade_pnl", ex.get("pnl", 0.0)),
                "consecutive_losses": ex.get("consecutive_losses", 0),
                "account_halt":  ex.get("account_halt"),
            })
            # Only update signal tracker on actual closes (not ratchet/partial)
            if reason not in ("ratchet_1", "ratchet_2", "partial_tp"):
                tracker.update(strat_id, 0)
                if sm:
                    pnl = ex.get("total_trade_pnl", ex.get("pnl", 0.0))
                    sm.record_trade_pnl(str(strat_id), pnl)
                    sm.remove_bracket(str(strat_id))
                    current_positions = sm.load_positions()
                    current_positions.pop(traded_sym, None)
                    sm.save_positions(current_positions)

        # ── Hours filter ──────────────────────────────────────────────────
        if not hours_allowed(last_bar_ts, allowed_hours, session_block):
            if tracker.current(strat_id) != 0:
                # If in position, we let the position ride (stops handle it)
                if verbose:
                    print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  "
                          f"off-hours (position managed by stops)")
            continue

        # ── Compute signal ────────────────────────────────────────────────
        try:
            signals = compute_signal(df, strat_name, params, version)
        except Exception as e:
            if verbose:
                print(f"  [{strat_id}] {symbol}/{strat_name} error: {e}")
            continue

        last_sig = int(signals.iloc[-1]) if not signals.empty else 0
        last_sig = max(-1, min(1, last_sig))

        # ── Risk gate for new entries ─────────────────────────────────────
        trade_risk = 0.0
        gate_reason = ""
        if last_sig != 0 and tracker.current(strat_id) == 0:
            if sm and sm.is_strategy_halted(str(strat_id)):
                ok          = False
                gate_reason = f"STRATEGY_HALTED: strategy {strat_id} was halted in a prior session"
            elif block_new_entries:
                ok          = False
                gate_reason = "NEWS_WINDOW: new entries blocked"
            else:
                sym_bias = _get_bias_for_symbol(symbol, news_bias)
                if sym_bias != 0 and sym_bias != last_sig:
                    ok          = False
                    dir_str     = "LONG" if last_sig == 1 else "SHORT"
                    bias_label  = "BULL" if sym_bias == 1 else "BEAR"
                    gate_reason = (
                        f"NEWS_BIAS: {dir_str} blocked — {bias_label} "
                        f"bias (score={news_bias.get('score', 0)})"
                    )
                else:
                    stop_dist  = STOP_MULT * atr
                    trade_risk = stop_dist * spec["point_value"]
                    ok, gate_reason = rm.can_enter(strat_id, trade_risk)
            if not ok:
                if verbose:
                    print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  "
                          f"BLOCKED — {gate_reason}")
                _log_signal({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": mode, "strategy_id": strat_id,
                    "strategy": strat_name, "symbol": traded_sym,
                    "bar_minutes": bar_min, "version": version,
                    "signal": last_sig, "entry": round(entry_p, 4),
                    "stop": round(entry_p - last_sig * STOP_MULT * atr, 4),
                    "target": round(entry_p + last_sig * TP_MULT * atr, 4),
                    "atr": round(atr, 4), "risk_usd": round(trade_risk, 2),
                    "accepted": False, "rejection_reason": gate_reason,
                })
                last_sig = 0

        # ── Portfolio coordinator gate (dry-run: logs decisions, no orders blocked in dry-run)
        if last_sig != 0 and tracker.current(strat_id) == 0 and coordinator is not None:
            _coord_intent = SignalIntent(
                strategy_id=strat_id,
                strategy_key=f"{symbol}/{strat_name}/{bar_min}m",
                symbol=traded_sym,
                contract=f"{traded_sym}M5",
                side=Side.LONG if last_sig == 1 else Side.SHORT,
                desired_qty=1,
                entry_price=entry_p,
                stop_price=round(entry_p - last_sig * STOP_MULT * atr, 4),
                target_price=round(entry_p + last_sig * TP_MULT * atr, 4),
                estimated_risk_usd=trade_risk,
                timestamp=datetime.now(timezone.utc),
            )
            _virtual_pos = [
                VirtualStrategyPosition(
                    strategy_id=sid2,
                    strategy_key=f"{sym2}/{sn2}/{bm2}m",
                    symbol=sym2,
                    side=Side.LONG if tracker.current(sid2) == 1 else Side.SHORT,
                    qty=1,
                    entry_price=0.0,
                    stop_price=0.0,
                    target_price=0.0,
                    state="OPEN",
                )
                for (sid2, sym2, bm2, sn2, _, _, _, _) in PORTFOLIO
                if tracker.current(sid2) != 0
            ]
            _coord_dec = coordinator.evaluate_single_signal(
                signal=_coord_intent,
                virtual_positions=_virtual_pos,
                broker_positions=[],
                open_orders=[],
                kill_switch=_check_kill_switch(),
            )
            _log_signal({
                "event_type": "coordinator_decision",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode, "strategy_id": strat_id, "strategy": strat_name,
                "symbol": traded_sym, "bar_minutes": bar_min, "version": version,
                "coordinator_action": _coord_dec.action.value,
                "coordinator_ok": _coord_dec.ok,
                "coordinator_reason": _coord_dec.reason,
            })
            if not _coord_dec.ok:
                if verbose:
                    print(f"  [{strat_id}] COORDINATOR {_coord_dec.action.value}: {_coord_dec.reason}")
                last_sig = 0

        # Dedup: skip if this exact signal was already processed (prevents re-entry on restart)
        if last_sig != 0 and tracker.current(strat_id) == 0 and sm:
            sig_key = f"{strat_id}:{last_bar_ts.isoformat()}"
            if sm.is_signal_processed(sig_key):
                if verbose:
                    print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  "
                          f"signal already processed (restart dedup)")
                last_sig = 0

        event, direction = tracker.update(strat_id, last_sig)

        if event in ("entry", "flip"):
            # Open trade in risk manager
            trade = rm.open_trade(strat_id, traded_sym, direction, entry_p, atr,
                                  spec["point_value"], spec["commission"])
            if sm:
                sig_key = f"{strat_id}:{last_bar_ts.isoformat()}"
                sm.mark_signal_processed(sig_key)
                current_positions = sm.load_positions()
                current_positions[traded_sym] = {
                    "net_pos": direction,
                    "entry_px": entry_p,
                    "strategy_id": strat_id,
                }
                sm.save_positions(current_positions)
            contracts = min(recommended_contracts(trade.risk_usd, rm.account.equity),
                            MAX_CONTRACTS_PER_TRADE)
            # Build alert dict
            alert = build_alert(strat_id, traded_sym, bar_min, strat_name,
                                version, direction, last_bar_ts, entry_p, atr)
            rp = trade.ratchet_prices(RISK_CFG)
            alert["ratchet1_px"]         = round(rp["r1_trigger"], 6)
            alert["ratchet2_px"]         = round(rp["r2_trigger"], 6)
            alert["contracts"]           = contracts
            alert["portfolio_pnl_today"] = rm.ledger.portfolio_today()

            # Key level annotation — informational, never blocks
            if _KEY_LEVELS_AVAILABLE:
                try:
                    kl    = compute_key_levels(df, symbol)
                    alert = _kl_annotate(alert, kl)
                except Exception:
                    pass

            # Flag news-confirmed entries (signal direction matches daily bias)
            if news_bias:
                sym_bias = _get_bias_for_symbol(symbol, news_bias)
                if sym_bias != 0 and sym_bias == direction:
                    alert["news_confirmed"] = True
                    if verbose:
                        lbl = "BULL" if sym_bias == 1 else "BEAR"
                        print(f"  [NEWS] Signal aligns with {lbl} daily bias "
                              f"— news-confirmed entry (score={news_bias.get('score', 0)})")

            fired.append(alert)
            _log_signal({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode, "strategy_id": strat_id,
                "strategy": strat_name, "symbol": traded_sym,
                "bar_minutes": bar_min, "version": version,
                "signal": direction, "entry": alert["entry_px"],
                "stop": alert["stop_px"], "target": alert["target_px"],
                "atr": alert["atr"], "risk_usd": round(trade_risk, 2),
                "contracts": contracts, "accepted": True,
                "rejection_reason": "",
            })
            if verbose:
                print(f"\n{'!'*60}")
                print(format_entry_alert(trade, rm.account, rm.ledger, contracts, RISK_CFG))
                if alert.get("key_level_context"):
                    print(f"  Key levels: {alert['key_level_context']}")
                print(f"{'!'*60}")

            # ── Auto-execute on Tradovate ──────────────────────────────────
            tv_sym = TV_CONTRACT_MAP.get(traded_sym, traded_sym)
            action = "Buy" if direction == 1 else "Sell"

            if mode == MODE_DRY_RUN and _TRADOVATE_AVAILABLE:
                # DRY_RUN: validate and log what the bracket order payload would be.
                # Uses a minimal (unauthenticated) client with dry_run=True — no API call.
                try:
                    from tick_tradovate_client import TradovateClient as _TVC
                    _dry = _TVC.create_dry_run()
                    br = _dry.place_bracket_order(
                        symbol=tv_sym, side=action.upper(), quantity=contracts,
                        entry_type="Market", entry_price=entry_p,
                        stop_price=alert["stop_px"], target_price=alert["target_px"],
                        demo=True, dry_run=True,
                    )
                    if br.get("ok"):
                        print(f"  [DRY_RUN] Bracket payload validated: "
                              f"stop={alert['stop_px']}  target={alert['target_px']}")
                        alert["bracket_payload_dry_run"] = br.get("payload", {})
                    else:
                        print(f"  [DRY_RUN] Bracket validation warning: {br.get('reason')}")
                except Exception as e:
                    print(f"  [DRY_RUN] Bracket validation error: {e}")

            elif tv_client and mode in (MODE_DEMO, MODE_LIVE):
                is_demo = (mode == MODE_DEMO)
                try:
                    br = tv_client.place_bracket_order(
                        symbol=tv_sym, side=action.upper(), quantity=contracts,
                        entry_type="Market", entry_price=entry_p,
                        stop_price=alert["stop_px"], target_price=alert["target_px"],
                        demo=is_demo, dry_run=False,
                    )
                    if br.get("ok"):
                        print(f"  [Tradovate] Bracket placed: "
                              f"entry={br.get('entry_order_id')}  "
                              f"stop={br.get('stop_order_id')}  "
                              f"target={br.get('target_order_id')}")
                        if sm:
                            sm.add_bracket(str(strat_id), {
                                "symbol":           tv_sym,
                                "entry_order_id":   br.get("entry_order_id"),
                                "stop_order_id":    br.get("stop_order_id"),
                                "target_order_id":  br.get("target_order_id"),
                                "entry_filled":     False,
                            })
                    else:
                        print(f"  [Tradovate] Bracket REJECTED — {br.get('reason')}  "
                              f"(alert logged, manual entry required)")
                except Exception as e:
                    print(f"  [Tradovate] BRACKET FAILED — {e}  (alert logged, manual entry required)")

        elif event == "close":
            ex = rm.signal_close(strat_id, entry_p)
            if verbose and ex:
                print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  "
                      f"CLOSE (signal)  trade P&L ${ex['total_trade_pnl']:+,.0f}")
                if ex.get("account_halt"):
                    print(f"\n  *** ACCOUNT HALTED: {ex['account_halt']} ***")

            # ── Auto-close on Tradovate ────────────────────────────────────
            if tv_client:
                try:
                    tv_sym = TV_CONTRACT_MAP.get(traded_sym, traded_sym)
                    tv_client.close_position(tv_sym)
                except Exception as e:
                    print(f"  [Tradovate] CLOSE FAILED — {e}  (manual close required)")

        elif event == "hold":
            side_str = "LONG" if tracker.current(strat_id) == 1 else "SHORT"
            if verbose:
                print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  "
                      f"holding {side_str}")

        else:  # flat
            if verbose:
                print(f"  [{strat_id}] {symbol}/{strat_name}/{bar_min}m  flat")

    # ── Correlation check ─────────────────────────────────────────────────
    corr_warn = _correlation_warning(tracker)
    if corr_warn and verbose:
        print(f"\n  *** {corr_warn} ***")

    # ── Portfolio summary ─────────────────────────────────────────────────
    if verbose:
        print(f"\n{rm.status_report()}")

    return fired


# ── Alert file writer ─────────────────────────────────────────────────────────

def append_alerts(alerts: list[dict], path: Path) -> None:
    existing = []
    if path.exists():
        with open(path) as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = []
    existing.extend(alerts)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _is_friday_close_time() -> bool:
    """True when it's Friday 21:45-23:59 UTC -- time to flatten all positions."""
    now = datetime.now(timezone.utc)
    return now.weekday() == 4 and now.hour >= 21 and now.minute >= 45


def main():
    global KILL_SWITCH_PATH  # may be overridden via --kill-switch-file
    parser = argparse.ArgumentParser(
        description="Fortress L2 Strategy Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  (default)              Dry-run: alerts only, no orders placed.
  --demo-auto-trade      Place orders on Tradovate DEMO account.
  --live-auto-trade      Place orders on LIVE account.
                         Requires env var: FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND

Kill switch:
  Create C:\\Users\\conor\\Desktop\\quant-research\\KILL_SWITCH.txt
  containing the word STOP to halt the executor immediately.

Examples:
  python tick_live_executor.py --poll 60                    # dry-run
  python tick_live_executor.py --poll 60 --demo-auto-trade  # demo orders
        """,
    )
    # ── Core flags ────────────────────────────────────────────────────────────
    parser.add_argument("--poll",        type=int, default=0,
                        help="Poll interval in seconds (0=single shot)")
    parser.add_argument("--strategy",    type=int, default=None,
                        help="Restrict to one strategy ID (1-15)")
    parser.add_argument("--alert-file",  type=str, default=None,
                        help="JSON file to append alerts to")
    parser.add_argument("--disable-v2",  action="store_true",
                        help="Exclude v2 strategy (7)")
    parser.add_argument("--quiet",       action="store_true",
                        help="Only print new alerts, suppress hold/flat lines")
    parser.add_argument("--max-runtime-minutes", type=int, default=0,
                        help="Stop after N minutes (0=unlimited)")
    parser.add_argument("--kill-switch-file", type=str,
                        default=str(KILL_SWITCH_PATH),
                        help="Path to kill switch file (default: KILL_SWITCH.txt)")

    # ── Execution mode flags ──────────────────────────────────────────────────
    parser.add_argument("--demo-auto-trade", action="store_true",
                        help="Place orders on Tradovate DEMO account")
    parser.add_argument("--live-auto-trade", action="store_true",
                        help="Place orders on LIVE account (requires FORTRESS_LIVE_ENABLE env var)")
    parser.add_argument("--close-weekend",   action="store_true",
                        help="Auto-flatten all Tradovate positions Friday 21:45 UTC")

    # ── Credentials (read from env vars by default) ───────────────────────────
    parser.add_argument("--username", default=os.environ.get("TRADOVATE_USERNAME", ""),
                        help="Tradovate username")
    parser.add_argument("--password", default=os.environ.get("TRADOVATE_PASSWORD", ""),
                        help="Tradovate password")
    parser.add_argument("--cid",      type=int,
                        default=int(os.environ.get("TRADOVATE_CID", "0")),
                        help="Tradovate API CID")
    parser.add_argument("--secret",   default=os.environ.get("TRADOVATE_SECRET", ""),
                        help="Tradovate API secret")

    args = parser.parse_args()

    # ── Override kill switch path if specified ────────────────────────────────
    KILL_SWITCH_PATH = Path(args.kill_switch_file)

    # ── Determine execution mode ──────────────────────────────────────────────
    if args.live_auto_trade and args.demo_auto_trade:
        print("ERROR: Cannot use --demo-auto-trade and --live-auto-trade together.")
        sys.exit(1)

    if args.live_auto_trade:
        live_env = os.environ.get(LIVE_ENABLE_ENV, "")
        if live_env != LIVE_ENABLE_VALUE:
            print(f"\nERROR: --live-auto-trade requires environment variable:")
            print(f"  {LIVE_ENABLE_ENV}={LIVE_ENABLE_VALUE}")
            print(f"  Current value: '{live_env}'")
            print(f"\n  Set this only when you fully understand the risks.")
            sys.exit(1)
        mode = MODE_LIVE
    elif args.demo_auto_trade:
        mode = MODE_DEMO
    else:
        mode = MODE_DRY_RUN

    # ── Portfolio coordinator ─────────────────────────────────────────────────
    if _COORDINATOR_AVAILABLE:
        _coord_cfg = CoordinatorConfig(
            one_strategy_only_demo=(mode == MODE_DEMO),
            max_total_open_symbols=1 if mode == MODE_DEMO else 10,
            allow_opposite_strategy_signals_same_symbol=False,
            allow_position_increase_same_symbol=False,
            allow_reversal=False,
            dry_run_only=(mode == MODE_DRY_RUN),
        )
        coordinator = PortfolioCoordinator(_coord_cfg)
    else:
        coordinator = None
        print("  WARNING: tick_portfolio_coordinator.py not available — coordinator disabled.")
        print("           Multi-strategy netting protection is inactive.")

    # ── Bracket order gate (required for any auto-trade) ─────────────────────
    if mode in (MODE_DEMO, MODE_LIVE):
        if not _TRADOVATE_AVAILABLE:
            print("ERROR: tick_tradovate_client.py not importable.")
            sys.exit(1)
        if not _has_bracket_orders():
            print("\n" + "=" * 62)
            print("  BLOCKED: Auto-trading requires bracket/OCO order support.")
            print("  place_bracket_order() is not yet implemented in")
            print("  tick_tradovate_client.py.")
            print("")
            print("  Without broker-native stops/targets, positions are")
            print("  unprotected if this process crashes.")
            print("")
            print("  Implement place_bracket_order() first, then re-enable.")
            print("=" * 62)
            sys.exit(1)
        if not args.username or not args.password:
            print("ERROR: Tradovate credentials required for auto-trade mode.")
            print("  Set TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SECRET")
            sys.exit(1)

    # ── Initialise Tradovate client ───────────────────────────────────────────
    tv_client = None
    if mode in (MODE_DEMO, MODE_LIVE):
        tv_client = TradovateClient(
            username=args.username, password=args.password,
            cid=args.cid, secret=args.secret,
            demo=(mode == MODE_DEMO),
        )
        if not tv_client.authenticate():
            print(f"ERROR: Tradovate authentication failed ({mode})")
            sys.exit(1)

    # ── News monitor ──────────────────────────────────────────────────────────
    news_monitor = None
    if _NEWS_AVAILABLE:
        try:
            news_monitor = NewsMonitor(cache_minutes=60)
        except Exception as e:
            print(f"  [News monitor init failed: {e}]")

    # ── Filter portfolio ──────────────────────────────────────────────────────
    global PORTFOLIO
    if args.strategy is not None:
        PORTFOLIO = [p for p in PORTFOLIO if p[0] == args.strategy]
        if not PORTFOLIO:
            print(f"ERROR: strategy ID {args.strategy} not found (valid: 1-12)")
            sys.exit(1)

    # ── Allowlist filtering ───────────────────────────────────────────────────
    allowlist = _load_allowlist()
    if allowlist:
        allowed_statuses = _ALLOWED_IN_DEMO if mode in (MODE_DEMO, MODE_LIVE) else _ALLOWED_IN_DRY_RUN
        filtered = []
        for entry in PORTFOLIO:
            sid      = entry[0]
            al_entry = allowlist.get(sid)
            if al_entry is None:
                # Not in allowlist — allow in dry-run with warning, block in demo/live
                if mode == MODE_DRY_RUN:
                    print(f"  [allowlist] Strategy {sid} not in allowlist — running in dry-run")
                    filtered.append(entry)
                else:
                    print(f"  [allowlist] Strategy {sid} not in allowlist — skipped in {mode} mode")
                continue
            status = al_entry.get("status", "RESEARCH_ONLY")
            if status in allowed_statuses:
                if status == "REVIEW_REQUIRED":
                    print(f"  [allowlist] Strategy {sid} ({al_entry.get('key', '')}) — REVIEW_REQUIRED, "
                          f"running in dry-run but flagged for manual review")
                filtered.append(entry)
            else:
                key    = al_entry.get("key", f"strategy_{sid}")
                reason = al_entry.get("reason", "")
                if args.strategy == sid:
                    # Explicitly requested a disabled strategy — hard exit
                    print(f"\nERROR: Strategy {sid} ({key}) is {status} and cannot run in {mode} mode.")
                    print(f"  Reason: {reason}")
                    _log_signal({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "mode": mode, "strategy_id": sid,
                        "strategy": entry[3], "symbol": entry[1],
                        "bar_minutes": entry[2], "version": entry[7],
                        "signal": 0, "accepted": False,
                        "rejection_reason": f"allowlist:{status}",
                    })
                    sys.exit(1)
                print(f"  [allowlist] Strategy {sid} ({key}) — {status}, skipped")
                _log_signal({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": mode, "strategy_id": sid,
                    "strategy": entry[3], "symbol": entry[1],
                    "bar_minutes": entry[2], "version": entry[7],
                    "signal": 0, "accepted": False,
                    "rejection_reason": f"allowlist:{status}",
                })
        PORTFOLIO = filtered
        if not PORTFOLIO:
            print("ERROR: No active strategies after allowlist filtering.")
            print(f"  Mode '{mode}' requires status in {allowed_statuses}.")
            print(f"  Check {ALLOWLIST_PATH}")
            sys.exit(1)
    else:
        print(f"  [allowlist] {ALLOWLIST_PATH.name} not found — running all strategies (no allowlist control)")

    # ── Print mode banner ─────────────────────────────────────────────────────
    print("\n" + _mode_banner(mode, tv_client, PORTFOLIO, RISK_CFG,
                               allowlist=allowlist, coordinator=coordinator))

    alert_path  = Path(args.alert_file) if args.alert_file else None
    tracker     = PositionTracker()
    rm          = RiskManager(cfg=RISK_CFG, starting_equity=ACCOUNT_EQUITY)
    verbose     = not args.quiet
    start_time  = time.time()
    _weekend_closed = False

    # ── State persistence layer ───────────────────────────────────────────────
    sm = StateManager() if _STATE_MANAGER_AVAILABLE else None
    if sm:
        halts = sm.load_strategy_halts()
        restored = [sid for sid, h in halts.items()
                    if isinstance(h, dict) and h.get("active")]
        if restored:
            print(f"  [StateManager] Restoring {len(restored)} strategy halt(s) from prior session: {restored}")
        else:
            print(f"  [StateManager] No persistent strategy halts to restore.")
    else:
        print(f"  [StateManager] Not available — state will not be persisted this session.")

    # ── Contract rollover check ───────────────────────────────────────────────
    for warn in _contract_rollover_warning():
        print(f"\n  *** {warn} ***")

    # ── Gate 7: Startup reconciliation (demo/live only) ───────────────────────
    if tv_client and mode in (MODE_DEMO, MODE_LIVE):
        _reconcile_positions(tv_client, tracker, rm,
                             portfolio=PORTFOLIO, verbose=verbose)

    # ── Local state reconciliation (all modes) ────────────────────────────────
    _recon_log_path = LOG_DIR / "broker_reconciliation_log.jsonl"

    def _log_reconciliation(result: dict, trigger: str) -> None:
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger":   trigger,
                "mode":      mode,
                **{k: result[k] for k in
                   ("ok", "severity", "halt_new_entries", "requires_human_review",
                    "actions", "reason") if k in result},
            }
            with open(_recon_log_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # never let reconciliation logging crash the executor

    if sm and _RECONCILIATION_AVAILABLE:
        local_state = {
            "positions":    sm.load_positions(),
            "brackets":     sm.load_active_brackets(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        broker_state = {"reachable": True, "positions": {}, "orders": {}}
        recon = _reconcile_state_fn(local_state, broker_state)
        _log_reconciliation(recon, "startup")
        if not recon["ok"]:
            print(f"  [Reconcile] {recon['severity']}: {recon['reason']}")
        else:
            print(f"  [Reconcile] Local state clean.")

    def one_pass(pass_num: int):
        nonlocal _weekend_closed

        # ── Heartbeat ─────────────────────────────────────────────────────
        if sm:
            sm.update_heartbeat(mode=mode, bar_loop_count=pass_num)

        # ── Kill switch check ─────────────────────────────────────────────
        if _check_kill_switch():
            print(f"\n  *** KILL SWITCH ACTIVATED ({KILL_SWITCH_PATH}) ***")
            print(f"  Stopping new entries.")
            if tv_client and mode in (MODE_DEMO, MODE_LIVE):
                print("  Attempting to flatten all positions via Tradovate...")
                try:
                    results = tv_client.close_all_positions()
                    print(f"  Flattened {len(results)} position(s)")
                except Exception as e:
                    print(f"  Flatten FAILED: {e} — manual close required")
            print("  Exiting.")
            sys.exit(0)

        # ── Runtime limit ─────────────────────────────────────────────────
        if args.max_runtime_minutes > 0:
            elapsed = (time.time() - start_time) / 60
            if elapsed >= args.max_runtime_minutes:
                print(f"\n  Max runtime {args.max_runtime_minutes}min reached. Stopping.")
                sys.exit(0)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{'='*60}")
        print(f"  [{mode}] SIGNAL CHECK — {now_str}  (pass #{pass_num})")
        print(f"{'='*60}")

        # Weekend auto-flatten
        if args.close_weekend and tv_client and _is_friday_close_time() and not _weekend_closed:
            print("\n  *** FRIDAY CLOSE — flattening all Tradovate positions ***")
            try:
                results = tv_client.close_all_positions()
                print(f"  Closed {len(results)} position(s)")
                _weekend_closed = True
            except Exception as e:
                print(f"  [Tradovate] Weekend close FAILED: {e}")
            return []

        if datetime.now(timezone.utc).weekday() == 0:
            _weekend_closed = False

        # ── Broker state sync (demo/live only) ───────────────────────────
        if tv_client and mode in (MODE_DEMO, MODE_LIVE):
            closed = _sync_broker_state(tv_client, tracker, rm,
                                         PORTFOLIO, verbose=verbose)
            if closed and verbose:
                print(f"  [Sync] {len(closed)} position(s) closed at broker since last check")

        # ── News status ───────────────────────────────────────────────────
        news_blocked = False
        current_bias = None
        if news_monitor:
            try:
                news_monitor.refresh()
                print(f"  News: {news_monitor.get_status_line()}")
                in_window, evt = news_monitor.in_news_window()
                if in_window:
                    print(f"\n  *** NEWS WINDOW — NEW ENTRIES BLOCKED: {evt} ***")
                    news_blocked = True
                bias = news_monitor.get_daily_bias()
                if bias.get("score", 0) != 0:
                    current_bias = bias
                if bias["es_nq_bias"] != 0:
                    d  = "BULL" if bias["es_nq_bias"] > 0 else "BEAR"
                    gc = "BULL" if bias["gc_bias"] > 0 else "BEAR" if bias["gc_bias"] < 0 else "NEUTRAL"
                    print(f"  Bias: ES/NQ {d} | GC {gc} | {bias['reason']}")
            except Exception:
                pass

        alerts = check_all_strategies(tracker, rm, args.disable_v2,
                                      verbose=verbose, tv_client=tv_client,
                                      mode=mode,
                                      block_new_entries=news_blocked,
                                      news_bias=current_bias,
                                      sm=sm,
                                      coordinator=coordinator)

        if alerts:
            print(f"\n  >>> {len(alerts)} alert(s) fired <<<")
            if alert_path:
                append_alerts(alerts, alert_path)
                print(f"  Logged to {alert_path}")
        else:
            if verbose:
                print(f"\n  No new signals this pass.")

        print(f"\n  Positions: {tracker.to_dict()}")
        print(f"  Signal log: {_signal_log_path()}")
        return alerts

    if args.poll > 0:
        print(f"\n  Polling every {args.poll}s — Ctrl+C or create KILL_SWITCH.txt to stop")
        pass_num = 0
        try:
            while True:
                pass_num += 1
                one_pass(pass_num)
                time.sleep(args.poll)
        except KeyboardInterrupt:
            print("\n  Stopped by user.")
    else:
        one_pass(1)


if __name__ == "__main__":
    main()
