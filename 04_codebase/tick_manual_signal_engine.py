"""
tick_manual_signal_engine.py
============================
Manual Signal System — generates trade alerts without placing orders.

Reads live L2 bar files (parquet), runs approved strategies, produces structured
signal records, applies blockers (news, stale bars, cooldown), routes to
tick_alert_router.py, and logs every signal (fired + blocked) to JSONL.

Usage:
    python tick_manual_signal_engine.py --watch
    python tick_manual_signal_engine.py --once --symbols GC SI
    python tick_manual_signal_engine.py --dry-run --strategy-allowlist CVD_VWAP CVD_Microprice
    python tick_manual_signal_engine.py --watch --telegram --stale-threshold-minutes 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — allow running from any directory
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CODEBASE   = Path(__file__).resolve().parent
sys.path.insert(0, str(_CODEBASE))

# ---------------------------------------------------------------------------
# Strategy imports
# ---------------------------------------------------------------------------
try:
    from src.strategies.l2_cvd_strategies import CVDMicropriceStrategy, CVDVWAPStrategy
    from src.strategies.l2_sweep_strategies import SweepContinuationStrategy
    _STRATEGIES_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] Strategy import failed: {e}. Using stub strategies for dry-run.", file=sys.stderr)
    _STRATEGIES_AVAILABLE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR   = _REPO_ROOT / "01_data" / "tick_bars"
LOG_DIR    = _REPO_ROOT / "06_live_trading" / "logs"
REPORT_DIR = _REPO_ROOT / "06_live_trading" / "reports"

LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SignalEngine")


# ===========================================================================
# Contract Specifications
# ===========================================================================
CONTRACT_SPECS: Dict[str, Dict[str, float]] = {
    "GC": {
        "tick_size":    0.10,
        "tick_value":   10.0,    # $10/tick
        "point_value":  100.0,   # $100/point (oz)
    },
    "SI": {
        "tick_size":    0.005,
        "tick_value":   25.0,    # $25/tick
        "point_value":  5000.0,  # $5000/point (oz)
    },
}

# Stale-bar thresholds per timeframe (minutes)
STALE_THRESHOLDS: Dict[str, int] = {
    "1m":  3,
    "3m":  6,
    "5m":  10,
    "15m": 25,
    "30m": 45,
}

# Cooldown bars after a signal fires (per symbol)
SIGNAL_COOLDOWN_BARS = 30


# ===========================================================================
# News Calendar — blocking windows
# ===========================================================================

# FOMC 2026 dates (ET → blocks 30 min before and after)
_FOMC_DATES_2026 = [
    "2026-01-29", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-16",
]


def _first_friday_of_month(year: int, month: int) -> datetime:
    """Return first Friday of given month as a datetime (no tz)."""
    import calendar
    first_day = datetime(year, month, 1)
    # weekday(): Monday=0, Friday=4
    offset = (4 - first_day.weekday()) % 7
    return first_day + timedelta(days=offset)


def build_news_windows(year: int = 2026) -> List[Tuple[datetime, datetime]]:
    """
    Build list of (window_start_utc, window_end_utc) tuples for major releases.
    Blocks 30 min before and 30 min after each event.

    Events:
      - NFP:  First Friday of each month, 8:30 AM ET (13:30 UTC)
      - FOMC: 8 times/year, 2:00 PM ET (19:00 UTC)
      - CPI:  Approx 2nd Tuesday of month, 8:30 AM ET (13:30 UTC)
    """
    from zoneinfo import ZoneInfo
    et_tz  = ZoneInfo("America/New_York")
    utc_tz = timezone.utc
    buffer = timedelta(minutes=30)
    windows: List[Tuple[datetime, datetime]] = []

    def add_window(dt_naive_et: datetime) -> None:
        dt_et  = dt_naive_et.replace(tzinfo=et_tz)
        dt_utc = dt_et.astimezone(utc_tz).replace(tzinfo=None)
        windows.append((dt_utc - buffer, dt_utc + buffer))

    # NFP — first Friday each month at 8:30 ET
    for month in range(1, 13):
        nfp_date = _first_friday_of_month(year, month)
        add_window(nfp_date.replace(hour=8, minute=30))

    # FOMC — 2:00 PM ET
    for date_str in _FOMC_DATES_2026:
        if str(year) in date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            add_window(dt.replace(hour=14, minute=0))

    # CPI — approximate 2nd Tuesday of each month at 8:30 ET
    for month in range(1, 13):
        first_day = datetime(year, month, 1)
        # First Tuesday
        first_tue_offset = (1 - first_day.weekday()) % 7
        second_tuesday   = first_day + timedelta(days=first_tue_offset + 7)
        add_window(second_tuesday.replace(hour=8, minute=30))

    return windows


# Module-level news windows for current year
_NEWS_WINDOWS = build_news_windows(datetime.now(timezone.utc).year)


def is_news_window(dt_utc: datetime) -> Tuple[bool, str]:
    """
    Return (True, reason_string) if dt_utc falls inside any news block window.
    dt_utc must be naive UTC.
    """
    for (start, end) in _NEWS_WINDOWS:
        if start <= dt_utc <= end:
            return True, f"News window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} UTC"
    return False, ""


# ===========================================================================
# ATR Utility
# ===========================================================================

def compute_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range ATR, same as strategy codebase helpers."""
    high  = data["high"]
    low   = data["low"]
    close = data["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def market_regime(data: pd.DataFrame, atr: pd.Series) -> str:
    """
    Simple regime classifier based on current ATR vs 20-bar mean ATR.
    Returns "TRENDING" | "RANGING" | "VOLATILE"
    """
    if len(atr) < 5:
        return "RANGING"
    current_atr  = float(atr.iloc[-1])
    mean_atr_20  = float(atr.rolling(20, min_periods=5).mean().iloc[-1])
    ratio        = current_atr / mean_atr_20 if mean_atr_20 > 0 else 1.0

    if ratio > 1.4:
        return "VOLATILE"
    elif ratio < 0.8:
        return "RANGING"
    else:
        return "TRENDING"


# ===========================================================================
# Strategy Registry
# ===========================================================================

class StrategyConfig:
    """Config for a deployed strategy instance."""
    def __init__(
        self,
        name:        str,
        symbol:      str,
        strategy_cls,
        params:      Dict[str, Any],
        rr_ratio:    float,
        hold_bars:   int,
        timeframe:   str = "1m",
    ):
        self.name         = name
        self.symbol       = symbol
        self.strategy_cls = strategy_cls
        self.params       = params
        self.rr_ratio     = rr_ratio
        self.hold_bars    = hold_bars
        self.timeframe    = timeframe
        self._instance    = None

    def get_instance(self):
        if self._instance is None:
            self._instance = self.strategy_cls(params=self.params)
        return self._instance


def _make_stub_strategy(signal_name: str):
    """Fallback stub when real strategies cannot be imported."""
    class _Stub:
        name = signal_name
        def __init__(self, params=None):
            self.params = params or {}
        def generate_signals(self, data: pd.DataFrame) -> pd.Series:
            return pd.Series(0, index=data.index)
    return _Stub


def build_default_registry() -> List[StrategyConfig]:
    """Build the default allowlisted strategy registry."""
    if _STRATEGIES_AVAILABLE:
        cvd_mp_cls    = CVDMicropriceStrategy
        sweep_cls     = SweepContinuationStrategy
        cvd_vwap_cls  = CVDVWAPStrategy
    else:
        cvd_mp_cls    = _make_stub_strategy("CVD_Microprice")
        sweep_cls     = _make_stub_strategy("Sweep_Continuation")
        cvd_vwap_cls  = _make_stub_strategy("CVD_VWAP")

    return [
        StrategyConfig(
            name         = "CVD_Microprice",
            symbol       = "SI",
            strategy_cls = cvd_mp_cls,
            params       = {"cvd_pct": 60, "mp_ticks": 1.0, "rr_ratio": 2.0, "hold_bars": 5},
            rr_ratio     = 2.0,
            hold_bars    = 5,
            timeframe    = "1m",
        ),
        StrategyConfig(
            name         = "Sweep_Continuation",
            symbol       = "SI",
            strategy_cls = sweep_cls,
            params       = {"min_sweeps": 3, "confirm_bars": 2, "rr_ratio": 1.5, "hold_bars": 5},
            rr_ratio     = 1.5,
            hold_bars    = 5,
            timeframe    = "1m",
        ),
        StrategyConfig(
            name         = "CVD_VWAP",
            symbol       = "GC",
            strategy_cls = cvd_vwap_cls,
            params       = {"vwap_band": 0.5, "cvd_pct": 60, "rr_ratio": 2.0, "hold_bars": 8},
            rr_ratio     = 2.0,
            hold_bars    = 8,
            timeframe    = "1m",
        ),
    ]


# ===========================================================================
# Bar loader
# ===========================================================================

def load_l2_bars(symbol: str, timeframe: str = "1m") -> Optional[pd.DataFrame]:
    """Load L2 bar parquet for symbol. Falls back to standard bars if L2 not found."""
    l2_path  = DATA_DIR / f"{symbol}_bars_l2_{timeframe}.parquet"
    std_path = DATA_DIR / f"{symbol}_bars_{timeframe}.parquet"

    path = l2_path if l2_path.exists() else std_path
    if not path.exists():
        logger.warning(f"No bar file found for {symbol} at {path}")
        return None

    try:
        df = pd.read_parquet(path)
        if df.index.tzinfo is None:
            df.index = pd.to_datetime(df.index, utc=True)
        else:
            df.index = df.index.tz_convert("UTC")
        df.sort_index(inplace=True)
        logger.debug(f"Loaded {len(df)} bars for {symbol} from {path.name}")
        return df
    except Exception as e:
        logger.error(f"Failed to load bars for {symbol}: {e}")
        return None


def check_stale(df: pd.DataFrame, timeframe: str, override_minutes: Optional[int] = None) -> Tuple[bool, str]:
    """
    Returns (is_stale, reason).
    Stale if newest bar is older than threshold.
    """
    threshold_minutes = override_minutes if override_minutes is not None else STALE_THRESHOLDS.get(timeframe, 5)
    threshold         = timedelta(minutes=threshold_minutes)
    now_utc           = datetime.now(timezone.utc).replace(tzinfo=None)
    newest_bar        = df.index[-1].to_pydatetime().replace(tzinfo=None)
    age               = now_utc - newest_bar

    if age > threshold:
        return True, f"Newest bar is {int(age.total_seconds()/60)}m old (threshold={threshold_minutes}m)"
    return False, ""


# ===========================================================================
# Signal Builder
# ===========================================================================

def build_signal_record(
    cfg:         StrategyConfig,
    side:        int,             # +1 LONG, -1 SHORT
    bar_ts:      pd.Timestamp,
    data:        pd.DataFrame,
    atr:         pd.Series,
    is_blocked:  bool  = False,
    block_reason: str  = "",
) -> Dict[str, Any]:
    """
    Construct a complete signal record dict.
    """
    specs       = CONTRACT_SPECS.get(cfg.symbol, {"point_value": 1.0, "tick_size": 0.1})
    point_value = specs["point_value"]
    tick_size   = specs["tick_size"]

    last_bar  = data.iloc[-1]
    entry_ref = float(last_bar["close"])
    atr_val   = float(atr.iloc[-1]) if len(atr) > 0 else tick_size * 10

    side_str  = "LONG" if side > 0 else "SHORT"
    direction = 1.0 if side > 0 else -1.0

    # Entry zone: ±0.5 ATR around current close
    entry_low  = round(entry_ref - 0.5 * atr_val, 4)
    entry_high = round(entry_ref + 0.5 * atr_val, 4)
    entry_zone = f"{entry_low:.4f}–{entry_high:.4f}"

    # Stop: 1.0 ATR from entry in opposite direction
    stop_price   = round(entry_ref - direction * atr_val, 4)
    # Target: rr_ratio × ATR from entry in signal direction
    target_price = round(entry_ref + direction * cfg.rr_ratio * atr_val, 4)

    risk_points = abs(entry_ref - stop_price)
    risk_dollars = round(risk_points * point_value, 2)

    # Confidence: based on signal frequency / ATR magnitude vs mean
    mean_atr_20 = float(atr.rolling(20, min_periods=5).mean().iloc[-1]) if len(atr) >= 5 else atr_val
    atr_ratio   = atr_val / mean_atr_20 if mean_atr_20 > 0 else 1.0
    if atr_ratio < 0.8:
        confidence = "HIGH"      # quiet market, cleaner signal
    elif atr_ratio > 1.4:
        confidence = "LOW"       # volatile, wider spreads
    else:
        confidence = "MEDIUM"

    # Context: key L2 values at signal bar
    context_cols = [
        "cvd", "cvd_delta", "ofi_1", "ofi_5", "microprice_last",
        "microprice_mean", "imbal_L5_last", "imbal_L5_mean",
        "buy_sweeps", "sell_sweeps", "net_sweeps", "absorption_score",
        "session_vwap", "volume", "buy_vol", "sell_vol",
    ]
    context: Dict[str, Any] = {}
    for col in context_cols:
        if col in last_bar.index:
            val = last_bar[col]
            context[col] = None if pd.isna(val) else round(float(val), 6)

    # Invalidation condition
    if side > 0:
        inv_level = round(entry_ref - 0.5 * atr_val, 4)
        invalidation = f"Cancel if price trades below {inv_level:.4f} before entry"
    else:
        inv_level = round(entry_ref + 0.5 * atr_val, 4)
        invalidation = f"Cancel if price trades above {inv_level:.4f} before entry"

    regime = market_regime(data, atr)
    now_utc = datetime.now(timezone.utc)

    return {
        "timestamp":              now_utc.isoformat(),
        "strategy_name":          cfg.name,
        "symbol":                 cfg.symbol,
        "side":                   side_str,
        "entry_zone":             entry_zone,
        "entry_low":              entry_low,
        "entry_high":             entry_high,
        "stop_price":             stop_price,
        "target_price":           target_price,
        "risk_points":            round(risk_points, 4),
        "risk_dollars":           risk_dollars,
        "rr_ratio":               cfg.rr_ratio,
        "confidence":             confidence,
        "context":                context,
        "invalidation_condition": invalidation,
        "market_regime":          regime,
        "is_blocked":             is_blocked,
        "block_reason":           block_reason,
        "bar_timestamp":          bar_ts.isoformat(),
        "atr":                    round(atr_val, 4),
        "entry_ref":              round(entry_ref, 4),
        # Hypothetical fill tracking (populated when outcome known)
        "hypo_fill_price":        None,
        "hypo_exit_price":        None,
        "hypo_outcome":           None,   # "WIN" | "LOSS" | "OPEN"
        "hypo_pnl_dollars":       None,
        "hypo_r_achieved":        None,
    }


# ===========================================================================
# Hypothetical Fill Tracker
# ===========================================================================

class HypotheticalTracker:
    """
    Tracks outcome of signals against subsequent bars.
    For each open signal, checks if target or stop was hit.
    """
    def __init__(self):
        self.open_signals: List[Dict[str, Any]] = []

    def add(self, signal: Dict[str, Any]) -> None:
        if not signal["is_blocked"]:
            self.open_signals.append(dict(signal))

    def update(self, symbol: str, data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Scan data forward from bar_timestamp. Update and return closed signals.
        """
        closed = []
        still_open = []
        bar_ts_cutoff = pd.Timestamp(data.index[-1])

        for sig in self.open_signals:
            if sig["symbol"] != symbol:
                still_open.append(sig)
                continue

            try:
                bar_ts     = pd.Timestamp(sig["bar_timestamp"]).tz_localize("UTC") \
                             if pd.Timestamp(sig["bar_timestamp"]).tzinfo is None \
                             else pd.Timestamp(sig["bar_timestamp"]).tz_convert("UTC")
                future_bars = data[data.index > bar_ts].head(sig.get("hold_bars", 8) + 5)
            except Exception:
                still_open.append(sig)
                continue

            if len(future_bars) == 0:
                still_open.append(sig)
                continue

            target = sig["target_price"]
            stop   = sig["stop_price"]
            side   = 1 if sig["side"] == "LONG" else -1

            outcome   = None
            exit_price = None
            for _, bar in future_bars.iterrows():
                if side > 0:  # LONG: target = high enough, stop = low enough
                    if bar["high"] >= target:
                        outcome    = "WIN"
                        exit_price = target
                        break
                    if bar["low"] <= stop:
                        outcome    = "LOSS"
                        exit_price = stop
                        break
                else:          # SHORT
                    if bar["low"] <= target:
                        outcome    = "WIN"
                        exit_price = target
                        break
                    if bar["high"] >= stop:
                        outcome    = "LOSS"
                        exit_price = stop
                        break

            if outcome is None:
                if bar_ts_cutoff > pd.Timestamp(future_bars.index[-1]):
                    # Hold bars elapsed without target/stop — mark as timed out
                    outcome    = "LOSS"
                    exit_price = float(future_bars.iloc[-1]["close"])
                else:
                    still_open.append(sig)
                    continue

            specs       = CONTRACT_SPECS.get(symbol, {"point_value": 1.0})
            point_value = specs["point_value"]
            pnl = (exit_price - sig["entry_ref"]) * side * point_value
            r_achieved = pnl / sig["risk_dollars"] if sig["risk_dollars"] > 0 else 0.0

            sig["hypo_fill_price"]  = sig["entry_ref"]
            sig["hypo_exit_price"]  = round(exit_price, 4)
            sig["hypo_outcome"]     = outcome
            sig["hypo_pnl_dollars"] = round(pnl, 2)
            sig["hypo_r_achieved"]  = round(r_achieved, 3)
            closed.append(sig)

        self.open_signals = still_open
        return closed


# ===========================================================================
# Cooldown Tracker
# ===========================================================================

class CooldownTracker:
    """Per-symbol cooldown counter. Blocks new signals for N bars after firing."""
    def __init__(self, cooldown_bars: int = SIGNAL_COOLDOWN_BARS):
        self.cooldown_bars = cooldown_bars
        self._last_signal_bar: Dict[str, Optional[pd.Timestamp]] = {}
        self._last_bar_count:  Dict[str, int] = {}

    def record_signal(self, symbol: str, bar_ts: pd.Timestamp) -> None:
        self._last_signal_bar[symbol] = bar_ts
        self._last_bar_count[symbol]  = 0

    def update_bar_count(self, symbol: str) -> None:
        if symbol in self._last_bar_count:
            self._last_bar_count[symbol] += 1

    def is_in_cooldown(self, symbol: str) -> Tuple[bool, str]:
        count = self._last_bar_count.get(symbol, self.cooldown_bars + 1)
        if count < self.cooldown_bars:
            remaining = self.cooldown_bars - count
            return True, f"Symbol cooldown: {remaining} bars remaining after last signal"
        return False, ""


# ===========================================================================
# Main Engine
# ===========================================================================

class ManualSignalEngine:
    """
    Orchestrates bar loading, strategy execution, blocker checks, and routing.
    """

    def __init__(
        self,
        symbols:             List[str],
        strategy_allowlist:  List[str],
        dry_run:             bool = False,
        telegram:            bool = False,
        stale_override:      Optional[int] = None,
    ):
        self.symbols            = symbols
        self.strategy_allowlist = strategy_allowlist
        self.dry_run            = dry_run
        self.telegram           = telegram
        self.stale_override     = stale_override

        self.registry    = build_default_registry()
        self.cooldown    = CooldownTracker()
        self.hypo        = HypotheticalTracker()
        self._signal_log: Optional[Path] = None

        # Import router here to avoid circular issues at module level
        self._init_router()

    def _init_router(self) -> None:
        try:
            from tick_alert_router import AlertRouter
            self.router = AlertRouter(config={
                "console":  True,
                "jsonl":    not self.dry_run,
                "telegram": self.telegram,
                "discord":  False,
                "dry_run":  self.dry_run,
            })
        except ImportError:
            logger.warning("tick_alert_router.py not found — console only")
            self.router = None

    def _signal_log_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        return LOG_DIR / f"signals_{today}.jsonl"

    def _write_signal_jsonl(self, signal: Dict[str, Any]) -> None:
        if self.dry_run:
            return
        path = self._signal_log_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(signal, default=str) + "\n")

    def _active_strategies(self) -> List[StrategyConfig]:
        """Return registry entries matching symbol filter + allowlist filter."""
        out = []
        for cfg in self.registry:
            if cfg.symbol not in self.symbols:
                continue
            if self.strategy_allowlist and cfg.name not in self.strategy_allowlist:
                continue
            out.append(cfg)
        return out

    def run_once(self) -> List[Dict[str, Any]]:
        """
        Execute one full pass: load bars, run strategies, emit signals.
        Returns list of all signal records (fired + blocked).
        """
        all_signals: List[Dict[str, Any]] = []

        for cfg in self._active_strategies():
            logger.info(f"Checking {cfg.name} on {cfg.symbol} [{cfg.timeframe}]")

            # --- Load bars ---
            df = load_l2_bars(cfg.symbol, cfg.timeframe)
            if df is None or len(df) < 60:
                logger.warning(f"Insufficient bars for {cfg.symbol} — skipping")
                continue

            # --- Stale bar check ---
            is_stale, stale_reason = check_stale(df, cfg.timeframe, self.stale_override)
            if is_stale:
                logger.warning(f"[STALE] {cfg.symbol}: {stale_reason}")
                # Emit a blocked signal with stale reason so it appears in log
                dummy_signal = {
                    "timestamp":     datetime.now(timezone.utc).isoformat(),
                    "strategy_name": cfg.name,
                    "symbol":        cfg.symbol,
                    "side":          "N/A",
                    "is_blocked":    True,
                    "block_reason":  f"STALE_BAR: {stale_reason}",
                    "bar_timestamp": str(df.index[-1]),
                }
                self._write_signal_jsonl(dummy_signal)
                continue

            # --- Compute ATR ---
            atr = compute_atr(df, period=14)

            # --- Cooldown bar count ---
            self.cooldown.update_bar_count(cfg.symbol)

            # --- Run strategy ---
            try:
                strategy  = cfg.get_instance()
                signals   = strategy.generate_signals(df)
            except Exception as e:
                logger.error(f"Strategy {cfg.name} error: {e}")
                logger.debug(traceback.format_exc())
                continue

            # Only look at the most recent bar signal
            if len(signals) == 0:
                continue

            last_signal_val = int(signals.iloc[-1])
            bar_ts          = df.index[-1]

            if last_signal_val == 0:
                logger.debug(f"  No signal on latest bar for {cfg.name}/{cfg.symbol}")
                continue

            # --- Resolve blockers ---
            is_blocked   = False
            block_reason = ""

            # News window blocker
            now_utc = datetime.now(timezone.utc)
            blocked_news, news_reason = is_news_window(now_utc)
            if blocked_news:
                is_blocked   = True
                block_reason = f"NEWS: {news_reason}"

            # Cooldown blocker
            if not is_blocked:
                blocked_cd, cd_reason = self.cooldown.is_in_cooldown(cfg.symbol)
                if blocked_cd:
                    is_blocked   = True
                    block_reason = cd_reason

            # --- Build signal record ---
            signal = build_signal_record(
                cfg          = cfg,
                side         = last_signal_val,
                bar_ts       = bar_ts,
                data         = df,
                atr          = atr,
                is_blocked   = is_blocked,
                block_reason = block_reason,
            )

            # --- Record cooldown if signal fired ---
            if not is_blocked:
                self.cooldown.record_signal(cfg.symbol, bar_ts)

            # --- Route signal ---
            if self.router:
                if is_blocked:
                    self.router.send_blocked(signal)
                else:
                    self.router.send_signal(signal)
            else:
                # Fallback: print to console
                _print_signal_console(signal)

            # --- Log to JSONL ---
            self._write_signal_jsonl(signal)

            # --- Track hypothetical fill ---
            if not is_blocked:
                self.hypo.add(signal)

            all_signals.append(signal)

            logger.info(
                f"  {'BLOCKED' if is_blocked else 'FIRED'} "
                f"{cfg.name}/{cfg.symbol} "
                f"{'LONG' if last_signal_val > 0 else 'SHORT'} "
                f"{'| ' + block_reason if is_blocked else ''}"
            )

        # Update hypothetical fills for any open signals
        for sym in self.symbols:
            df_sym = load_l2_bars(sym, "1m")
            if df_sym is not None:
                closed = self.hypo.update(sym, df_sym)
                for sig in closed:
                    self._write_signal_jsonl(sig)  # update log with outcome
                    if self.router:
                        logger.info(
                            f"[HYPO] {sig['strategy_name']}/{sig['symbol']} "
                            f"{sig['hypo_outcome']} "
                            f"R={sig['hypo_r_achieved']:.2f} "
                            f"PnL=${sig['hypo_pnl_dollars']:.0f}"
                        )

        return all_signals

    def watch(self, interval_seconds: int = 60) -> None:
        """Continuously run passes every interval_seconds."""
        logger.info(
            f"ManualSignalEngine WATCH mode | symbols={self.symbols} | "
            f"strategies={self.strategy_allowlist or 'all'} | "
            f"dry_run={self.dry_run} | interval={interval_seconds}s"
        )
        if self.dry_run:
            logger.info("[DRY-RUN MODE] Signals will NOT be written to disk")

        while True:
            try:
                signals = self.run_once()
                logger.info(f"Pass complete. {len(signals)} signal events processed.")
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt — shutting down")
                break
            except Exception as e:
                logger.error(f"Unhandled error in run_once: {e}")
                logger.debug(traceback.format_exc())

            try:
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt during sleep — shutting down")
                break


# ===========================================================================
# Fallback console printer (used if router is unavailable)
# ===========================================================================

def _print_signal_console(signal: Dict[str, Any]) -> None:
    """Minimal console print when router is unavailable."""
    blocked = signal.get("is_blocked", False)
    prefix  = "[BLOCKED] " if blocked else "[SIGNAL] "
    print(
        f"{prefix}{signal['strategy_name']} | {signal['symbol']} | {signal['side']} | "
        f"Entry: {signal.get('entry_zone')} | Stop: {signal.get('stop_price')} | "
        f"Target: {signal.get('target_price')} | Risk: ${signal.get('risk_dollars')} | "
        f"Confidence: {signal.get('confidence')}"
    )
    if blocked:
        print(f"  Block reason: {signal.get('block_reason')}")


# ===========================================================================
# CLI Entry Point
# ===========================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual Signal Engine — generates trade alerts, no orders placed",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--watch", action="store_true", default=True,
                            help="Run continuously, checking bars every 60 seconds")
    mode_group.add_argument("--once",  action="store_true",
                            help="Run one pass and exit")
    mode_group.add_argument("--dry-run", action="store_true",
                            help="Print signals but do not write to any log files")

    parser.add_argument("--symbols", nargs="+", default=["GC", "SI"],
                        help="Symbols to monitor")
    parser.add_argument("--strategy-allowlist", nargs="+", default=[],
                        dest="strategy_allowlist",
                        help="Strategies to run (default: all deployed)")
    parser.add_argument("--telegram", action="store_true",
                        help="Enable Telegram alerts (reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env)")
    parser.add_argument("--stale-threshold-minutes", type=int, default=None,
                        dest="stale_threshold_minutes",
                        help="Override stale bar threshold (minutes)")
    parser.add_argument("--interval", type=int, default=60,
                        help="Watch mode poll interval in seconds")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve mode
    dry_run   = args.dry_run
    run_once  = args.once
    # --watch is True by default unless --once or --dry-run overrides it

    engine = ManualSignalEngine(
        symbols            = args.symbols,
        strategy_allowlist = args.strategy_allowlist,
        dry_run            = dry_run,
        telegram           = args.telegram,
        stale_override     = args.stale_threshold_minutes,
    )

    if run_once or dry_run:
        signals = engine.run_once()
        fired   = [s for s in signals if not s["is_blocked"]]
        blocked = [s for s in signals if s["is_blocked"]]
        print(f"\nDone. {len(fired)} signal(s) fired, {len(blocked)} blocked.")
    else:
        engine.watch(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
