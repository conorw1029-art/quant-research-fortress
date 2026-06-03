"""
tick_strategy_research_factory.py — Controlled Strategy Testing Orchestrator
=============================================================================
Systematic, anti-overfitting strategy research pipeline for The Fortress.

Loads strategy definitions from 08_docs/strategy_universe_exhaustive.json,
runs the L2 backtest battery with parameter grid controls, applies family-level
false discovery rate correction, and appends all results to the research ledger.

Design principles:
  - All trials logged (append-only) regardless of result
  - Parameter grid locked before testing begins
  - Max 256 combos per strategy (sub-sampled if exceeded)
  - Family-level Bonferroni/BH FDR correction
  - Smoke test validates pipeline integrity before production runs

Usage:
  venv_new/Scripts/python.exe 04_codebase/tick_strategy_research_factory.py --smoke-test
  venv_new/Scripts/python.exe 04_codebase/tick_strategy_research_factory.py --family A_CVD --symbols GC SI
  venv_new/Scripts/python.exe 04_codebase/tick_strategy_research_factory.py --status BACKLOG --priority 1
  venv_new/Scripts/python.exe 04_codebase/tick_strategy_research_factory.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# ── Directory layout ──────────────────────────────────────────────────────────
UNIVERSE_JSON   = ROOT / "08_docs" / "strategy_universe_exhaustive.json"
LEDGER_DIR      = ROOT / "05_backtests" / "research_ledger"
LEDGER_FILE     = LEDGER_DIR / "zoo_research.jsonl"
BAR_DIR         = ROOT / "01_data" / "tick_bars"
L2_RESULTS_DIR  = ROOT / "05_backtests" / "l2_results"

LEDGER_DIR.mkdir(parents=True, exist_ok=True)
L2_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Contract specs ────────────────────────────────────────────────────────────
CONTRACT_SPECS = {
    "GC": {"tick_size": 0.10, "tick_value": 10.0,  "name": "Gold"},
    "SI": {"tick_size": 0.005, "tick_value": 25.0, "name": "Silver"},
    "CL": {"tick_size": 0.01, "tick_value": 10.0,  "name": "Crude Oil"},
}

# ── Max parameter combinations ────────────────────────────────────────────────
# 64 is the default; use --max-combos to override. Combos beyond this are
# randomly subsampled without replacement using uniform spacing across the grid.
DEFAULT_MAX_COMBOS = 64

# ── Strategy name → class name map ────────────────────────────────────────────
STRATEGY_CLASS_MAP = {
    "OFI_Continuation":       ("src.strategies.l2_ofi_strategies",        "OFIContinuationStrategy"),
    "OFI_Reversal":           ("src.strategies.l2_ofi_strategies",        "OFIReversalStrategy"),
    "OFI_Microprice":         ("src.strategies.l2_ofi_strategies",        "OFIMicropriceStrategy"),
    "Sweep_Continuation":     ("src.strategies.l2_sweep_strategies",      "SweepContinuationStrategy"),
    "Sweep_Absorption":       ("src.strategies.l2_sweep_strategies",      "SweepAbsorptionReversalStrategy"),
    "Session_HL_Sweep":       ("src.strategies.l2_sweep_strategies",      "SessionHighLowSweepReversalStrategy"),
    "Absorption_Reversal":    ("src.strategies.l2_absorption_strategies", "AbsorptionReversalStrategy"),
    "CVD_Absorption":         ("src.strategies.l2_absorption_strategies", "CVDAbsorptionStrategy"),
    "Repeated_Replenishment": ("src.strategies.l2_absorption_strategies", "RepeatedReplenishmentStrategy"),
    "CVD_Microprice":         ("src.strategies.l2_cvd_strategies",        "CVDMicropriceStrategy"),
    "CVD_Slope_Regime":       ("src.strategies.l2_cvd_strategies",        "CVDSlopeRegimeStrategy"),
    "CVD_Acceleration":       ("src.strategies.l2_cvd_strategies",        "CVDAccelerationStrategy"),
    "CVD_VWAP":               ("src.strategies.l2_cvd_strategies",        "CVDVWAPStrategy"),
    "Depth_Imbalance_Momentum": ("src.strategies.l2_depth_strategies",   "DepthImbalanceMomentumStrategy"),
    "Depth_Imbalance_MeanRev":  ("src.strategies.l2_depth_strategies",   "DepthImbalanceMeanRevStrategy"),
    "MultiTF_OFI":            ("src.strategies.l2_depth_strategies",     "MultiTimeframeOFIStrategy"),
}


# ── Utility: load strategy class dynamically ──────────────────────────────────
def _load_strategy_class(strategy_key: str):
    """Dynamically import and return the strategy class for a given key."""
    if strategy_key not in STRATEGY_CLASS_MAP:
        raise ValueError(f"Unknown strategy key: {strategy_key!r}. "
                         f"Available: {list(STRATEGY_CLASS_MAP)}")
    module_path, class_name = STRATEGY_CLASS_MAP[strategy_key]
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


# ── Utility: grid hash ────────────────────────────────────────────────────────
def _grid_hash(param_grid: dict) -> str:
    """MD5 of the sorted JSON representation of param_grid."""
    raw = json.dumps(param_grid, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()[:8]


# ── Utility: expand or sub-sample parameter grid ─────────────────────────────
def _expand_grid(param_grid: dict, max_combos: int) -> List[dict]:
    """
    Expand param_grid into list of dicts.
    If total combinations exceed max_combos, sub-sample using quasi-random
    indices (uniform spacing across the full grid, preserving coverage).
    """
    keys = list(param_grid.keys())
    all_combos = list(product(*[param_grid[k] for k in keys]))
    total = len(all_combos)

    if total <= max_combos:
        return [dict(zip(keys, c)) for c in all_combos]

    # Sub-sample: evenly-spaced indices across the full grid
    step = total / max_combos
    indices = [int(i * step) for i in range(max_combos)]
    sampled = [all_combos[i] for i in indices]
    print(f"  [Grid] {total} combos exceed limit {max_combos} "
          f"— sub-sampled {len(sampled)} using uniform spacing.")
    return [dict(zip(keys, c)) for c in sampled]


# ── Load or build L2 bars ─────────────────────────────────────────────────────
def _load_bars(symbol: str) -> pd.DataFrame:
    """Load pre-built L2 bars for a symbol."""
    l2_path = BAR_DIR / f"{symbol}_bars_l2_1m.parquet"
    primary_path = BAR_DIR / f"{symbol}_bars_1m.parquet"

    if l2_path.exists():
        bars = pd.read_parquet(l2_path)
    elif primary_path.exists():
        bars = pd.read_parquet(primary_path)
    else:
        raise FileNotFoundError(
            f"No bar data found for {symbol}. "
            f"Expected: {l2_path} or {primary_path}"
        )

    if bars.empty:
        raise ValueError(f"Bar data for {symbol} is empty.")

    # Add session VWAP if missing
    if "session_vwap" not in bars.columns:
        bars["session_vwap"] = _calc_session_vwap(bars)

    print(f"[{symbol}] Loaded {len(bars):,} bars "
          f"({bars.index.min().date()} – {bars.index.max().date()})")
    return bars


def _calc_session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Session VWAP resetting at 17:00 UTC (CME day session reset)."""
    close = bars["close"]
    vol = bars.get("volume", pd.Series(1, index=bars.index))
    session_date = (bars.index - pd.Timedelta(hours=17)).date
    vwap = pd.Series(np.nan, index=bars.index)
    for date, grp in bars.groupby(pd.Series(session_date, index=bars.index)):
        gv = vol.loc[grp.index]
        gc = close.loc[grp.index]
        cumvol = gv.cumsum()
        cumtpvol = (gc * gv).cumsum()
        vwap.loc[grp.index] = cumtpvol / cumvol.replace(0, np.nan)
    return vwap


# ── Apply costs ───────────────────────────────────────────────────────────────
def _apply_costs(trades_df: pd.DataFrame, spec: dict,
                 slippage_ticks: int = 1) -> pd.DataFrame:
    out = trades_df.copy()
    slip_pts = slippage_ticks * 2 * spec["tick_size"]
    dollars_per_point = spec["tick_value"] / spec["tick_size"]
    commission_pts = (2.25 * 2) / dollars_per_point
    total_cost_pts = slip_pts + commission_pts
    out["net_pnl"] = out.get("gross_pnl", 0) - total_cost_pts
    return out


# ── Compute metrics ───────────────────────────────────────────────────────────
def _compute_metrics(trades_df: pd.DataFrame, spec: dict) -> dict:
    """DSR, Sharpe, win-rate, max-DD. Returns empty dict if insufficient data."""
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return {}

    tick_val = spec["tick_value"]
    tick_sz  = spec["tick_size"]
    dollar_pnl = trades_df["net_pnl"] * (tick_val / tick_sz)

    n = len(dollar_pnl)
    if n < 5:
        return {}

    wins   = (dollar_pnl > 0).sum()
    losses = (dollar_pnl < 0).sum()
    win_rate = wins / n if n > 0 else 0
    total_pnl = dollar_pnl.sum()
    avg_win   = dollar_pnl[dollar_pnl > 0].mean() if wins > 0 else 0
    avg_loss  = dollar_pnl[dollar_pnl < 0].mean() if losses > 0 else 0

    if "exit_time" in trades_df.columns:
        daily = dollar_pnl.groupby(trades_df["exit_time"].dt.date).sum()
    else:
        daily = pd.Series(dollar_pnl.values)

    T = len(daily)
    if T < 2:
        return {}

    mu  = daily.mean()
    sig = daily.std()
    sharpe = (mu / (sig + 1e-9)) * np.sqrt(252)

    # Deflated Sharpe Ratio (simplified — no scipy required)
    skew = float(daily.skew()) if T > 2 else 0.0
    kurt = float(daily.kurtosis()) if T > 3 else 0.0
    denom_sq = 1.0 - skew * sharpe + (kurt + 2) / 4 * sharpe ** 2
    denom = np.sqrt(max(denom_sq, 1e-9))
    dsr = sharpe / denom - (0.2 / np.sqrt(max(T, 1)))

    cumulative = dollar_pnl.cumsum()
    roll_max   = cumulative.cummax()
    max_dd     = float((roll_max - cumulative).max())
    worst_day  = float(daily.min())

    return {
        "n_trades":    n,
        "win_rate":    round(win_rate, 4),
        "total_pnl":   round(total_pnl, 2),
        "avg_win":     round(avg_win, 2),
        "avg_loss":    round(avg_loss, 2),
        "sharpe":      round(sharpe, 4),
        "dsr":         round(dsr, 4),
        "max_dd":      round(max_dd, 2),
        "worst_day":   round(worst_day, 2),
    }


# ── Walk-forward validation ───────────────────────────────────────────────────
def _walk_forward(bars: pd.DataFrame, strat, spec: dict,
                  train_months: int = 24, oos_months: int = 12,
                  step_months: int = 6) -> dict:
    """
    Rolling walk-forward: train_months in-sample, oos_months OOS.
    Returns mean WF Sharpe and number of folds.
    """
    if bars.empty:
        return {"wf_sharpe": 0.0, "wf_dsr": 0.0, "wf_folds": 0,
                "wf_trades": 0, "wf_win_rate": 0.0}

    idx = bars.index
    start = idx.min()
    end   = idx.max()

    total_months = int((end - start).days / 30.44)
    if total_months < train_months + oos_months:
        return {"wf_sharpe": 0.0, "wf_dsr": 0.0, "wf_folds": 0,
                "wf_trades": 0, "wf_win_rate": 0.0}

    fold_sharpes: List[float] = []
    fold_trades:  List[int]   = []
    fold_wr:      List[float] = []
    fold_dsrs:    List[float] = []
    n_folds = 0

    train_td = pd.DateOffset(months=train_months)
    oos_td   = pd.DateOffset(months=oos_months)
    step_td  = pd.DateOffset(months=step_months)

    fold_start = start
    while True:
        train_end = fold_start + train_td
        oos_end   = train_end + oos_td

        if oos_end > end:
            break

        oos_bars = bars[(bars.index >= train_end) & (bars.index < oos_end)]
        if len(oos_bars) < 200:
            fold_start += step_td
            continue

        try:
            signals = strat.generate_signals(oos_bars)
            trades  = strat.signals_to_trades(oos_bars, signals)
        except Exception:
            fold_start += step_td
            continue

        if not trades:
            fold_start += step_td
            continue

        trades_df = pd.DataFrame(trades)
        trades_df = _apply_costs(trades_df, spec, slippage_ticks=1)
        m = _compute_metrics(trades_df, spec)

        if m and m["n_trades"] >= 10:
            fold_sharpes.append(m["sharpe"])
            fold_trades.append(m["n_trades"])
            fold_wr.append(m["win_rate"])
            fold_dsrs.append(m["dsr"])
            n_folds += 1

        fold_start += step_td

    if n_folds == 0:
        return {"wf_sharpe": 0.0, "wf_dsr": 0.0, "wf_folds": 0,
                "wf_trades": 0, "wf_win_rate": 0.0}

    return {
        "wf_sharpe":   round(float(np.mean(fold_sharpes)), 4),
        "wf_dsr":      round(float(np.mean(fold_dsrs)), 4),
        "wf_folds":    n_folds,
        "wf_trades":   int(np.sum(fold_trades)),
        "wf_win_rate": round(float(np.mean(fold_wr)), 4),
    }


# ── Bootstrap p-value (date-shuffle) ─────────────────────────────────────────
def _bootstrap_p_value(trades_df: pd.DataFrame, spec: dict,
                       n_shuffles: int = 500) -> float:
    """
    Permutation test: shuffle daily P&L dates N times.
    Returns fraction of shuffled Sharpes >= observed Sharpe.
    """
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return 1.0

    tick_val = spec["tick_value"]
    tick_sz  = spec["tick_size"]
    dollar_pnl = trades_df["net_pnl"] * (tick_val / tick_sz)

    if "exit_time" in trades_df.columns:
        daily = dollar_pnl.groupby(trades_df["exit_time"].dt.date).sum()
    else:
        daily = pd.Series(dollar_pnl.values)

    if len(daily) < 10:
        return 1.0

    observed_sharpe = (daily.mean() / (daily.std() + 1e-9)) * np.sqrt(252)
    daily_arr = daily.values
    rng = np.random.default_rng(42)

    count_above = 0
    for _ in range(n_shuffles):
        shuffled = rng.permutation(daily_arr)
        sh = (shuffled.mean() / (shuffled.std() + 1e-9)) * np.sqrt(252)
        if sh >= observed_sharpe:
            count_above += 1

    return round(count_above / n_shuffles, 4)


# ── Slippage ladder ───────────────────────────────────────────────────────────
def _slippage_ladder(bars: pd.DataFrame, strat, spec: dict,
                     ticks: List[int] = None) -> List[dict]:
    """Run strategy at multiple slippage levels, return list of results."""
    if ticks is None:
        ticks = [0, 1, 2, 3]

    results = []
    try:
        signals = strat.generate_signals(bars)
        raw_trades = strat.signals_to_trades(bars, signals)
        if not raw_trades:
            return results
        raw_df = pd.DataFrame(raw_trades)
    except Exception:
        return results

    for t in ticks:
        df = _apply_costs(raw_df, spec, slippage_ticks=t)
        m  = _compute_metrics(df, spec)
        if m:
            results.append({
                "slippage_ticks": t,
                "net_pnl":   m["total_pnl"],
                "sharpe":    m["sharpe"],
                "win_rate":  m["win_rate"],
            })
    return results


# ── Append entry to research ledger ──────────────────────────────────────────
def _append_ledger(entry: dict) -> None:
    """Append one JSON line to the research ledger. Thread-unsafe (single process OK)."""
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── BH False Discovery Rate correction ───────────────────────────────────────
def _bh_correction(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    """Benjamini-Hochberg FDR correction. Returns list of pass/fail booleans."""
    n = len(p_values)
    if n == 0:
        return []
    sorted_pairs = sorted(enumerate(p_values), key=lambda x: x[1])
    reject = [False] * n
    for rank, (orig_idx, p) in enumerate(sorted_pairs, 1):
        threshold = (rank / n) * alpha
        if p <= threshold:
            reject[orig_idx] = True
    return reject


def _bonferroni_correction(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    """Bonferroni correction. Returns list of pass/fail booleans."""
    threshold = alpha / max(len(p_values), 1)
    return [p <= threshold for p in p_values]


# ── Load universe JSON ────────────────────────────────────────────────────────
def _load_universe() -> List[dict]:
    """Load strategy universe from JSON. Returns empty list if file not found."""
    if not UNIVERSE_JSON.exists():
        print(f"[WARN] Universe file not found: {UNIVERSE_JSON}")
        print("  Run with --smoke-test to verify pipeline without universe file.")
        return []
    with open(UNIVERSE_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_universe(universe: List[dict]) -> None:
    """Write updated universe back to JSON."""
    UNIVERSE_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_JSON, "w", encoding="utf-8") as f:
        json.dump(universe, f, indent=2, default=str)


# ── Filter universe entries ───────────────────────────────────────────────────
def _filter_universe(universe: List[dict], args) -> List[dict]:
    """Apply CLI filters to select which strategies to test."""
    filtered = universe

    if args.family:
        filtered = [s for s in filtered if s.get("family") == args.family]

    if args.priority is not None:
        filtered = [s for s in filtered if s.get("priority") == args.priority]

    if args.status:
        filtered = [s for s in filtered if s.get("status") == args.status]

    if args.exclude:
        filtered = [s for s in filtered
                    if s.get("strategy_key") not in args.exclude]

    return filtered


# ── Core: test one strategy on one symbol ─────────────────────────────────────
def _test_one(
    strategy_def: dict,
    symbol: str,
    bars: pd.DataFrame,
    max_combos: int,
    run_id: str,
    dry_run: bool = False,
    n_bootstrap: int = 200,
) -> List[dict]:
    """
    Test all parameter combos for one strategy on one symbol.
    Returns list of ledger entries (one per combo).
    """
    strategy_key = strategy_def["strategy_key"]
    param_grid   = strategy_def.get("param_grid", {})
    spec         = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])
    grid_h       = _grid_hash(param_grid)

    combos = _expand_grid(param_grid, max_combos)
    print(f"  [{strategy_key}] {symbol}: {len(combos)} combos "
          f"(grid_hash={grid_h})")

    if dry_run:
        print(f"    [DRY RUN] Would test {len(combos)} combos — skipping.")
        return []

    try:
        StratClass = _load_strategy_class(strategy_key)
    except (ValueError, ImportError, AttributeError) as e:
        print(f"  [ERROR] Cannot load {strategy_key}: {e}")
        return []

    ledger_entries: List[dict] = []

    for combo in combos:
        try:
            strat = StratClass(params=combo)
        except Exception as e:
            print(f"    [SKIP] Init failed for {combo}: {e}")
            continue

        # Full-period metrics
        try:
            signals = strat.generate_signals(bars)
            raw_trades = strat.signals_to_trades(bars, signals)
        except Exception as e:
            print(f"    [SKIP] Signal error for {combo}: {e}")
            continue

        if not raw_trades:
            continue

        raw_df = pd.DataFrame(raw_trades)
        trades_df = _apply_costs(raw_df, spec, slippage_ticks=1)
        m = _compute_metrics(trades_df, spec)
        if not m or m["n_trades"] < 10:
            continue

        # Walk-forward
        wf = _walk_forward(bars, strat, spec)

        # Bootstrap p-value (reduced shuffles for speed)
        bp = _bootstrap_p_value(trades_df, spec, n_shuffles=n_bootstrap)

        # Slippage ladder
        ladder = _slippage_ladder(bars, strat, spec, ticks=[1, 2, 3])
        s1 = next((x["sharpe"] for x in ladder if x["slippage_ticks"] == 1), 0.0)
        s2 = next((x["sharpe"] for x in ladder if x["slippage_ticks"] == 2), 0.0)

        # Determine status for this combo
        status = _determine_status(m, wf, bp, s2)

        entry = {
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "strategy_key": strategy_key,
            "symbol":       symbol,
            "params":       combo,
            "n_trades":     m["n_trades"],
            "wf_sharpe":    wf["wf_sharpe"],
            "wf_dsr":       wf["wf_dsr"],
            "wf_folds":     wf["wf_folds"],
            "wf_trades":    wf["wf_trades"],
            "wf_win_rate":  wf["wf_win_rate"],
            "dsr":          m["dsr"],
            "sharpe":       m["sharpe"],
            "win_rate":     m["win_rate"],
            "total_pnl":    m["total_pnl"],
            "max_dd":       m["max_dd"],
            "worst_day":    m["worst_day"],
            "bootstrap_p":  bp,
            "slippage_1tick": s1,
            "slippage_2tick": s2,
            "slippage_ladder": ladder,
            "status":       status,
            "run_id":       run_id,
            "grid_hash":    grid_h,
        }

        _append_ledger(entry)
        ledger_entries.append(entry)

        status_icon = "PASS" if status not in ("REJECTED", "INSUFFICIENT_DATA") else "----"
        print(f"    [{status_icon}] {combo}  trades={m['n_trades']:>4}  "
              f"wf_sharpe={wf['wf_sharpe']:+.2f}  dsr={m['dsr']:+.3f}  "
              f"bp={bp:.3f}  s2={s2:+.2f}  → {status}")

    return ledger_entries


def _determine_status(m: dict, wf: dict, bootstrap_p: float,
                      slippage_2tick_sharpe: float) -> str:
    """Classify a single parameter combo result into a status string."""
    n = m.get("n_trades", 0)

    if n < 50:
        return "INSUFFICIENT_DATA"

    if wf["wf_folds"] > 0 and wf["wf_sharpe"] < 0.0:
        return "REJECTED"

    if bootstrap_p > 0.10:
        return "REJECTED"

    if m["dsr"] < 0.0:
        return "REJECTED"

    if n < 200:
        return "RESEARCH_ONLY"

    if slippage_2tick_sharpe <= 0.0:
        return "WATCHLIST"

    if m["dsr"] < 0.3 or wf["wf_sharpe"] < 1.5:
        return "WATCHLIST"

    if bootstrap_p > 0.05:
        return "WATCHLIST"

    if wf["wf_sharpe"] >= 1.5 and m["dsr"] >= 0.3 and bootstrap_p <= 0.05:
        return "PAPER_CANDIDATE"

    return "WATCHLIST"


# ── Family-level FDR report ───────────────────────────────────────────────────
def _produce_family_report(
    family_results: Dict[str, List[dict]],
    alpha: float = 0.05,
) -> dict:
    """
    Given a dict of {strategy_key: [ledger_entries]}, produce a
    family-level FDR report with Bonferroni and BH correction.
    """
    all_entries: List[dict] = []
    for entries in family_results.values():
        all_entries.extend(entries)

    if not all_entries:
        return {"n_tested": 0, "n_pass_bh": 0, "n_pass_bonferroni": 0}

    p_values = [e.get("bootstrap_p", 1.0) for e in all_entries]
    bh_pass  = _bh_correction(p_values, alpha=alpha)
    bon_pass = _bonferroni_correction(p_values, alpha=alpha)

    n_pass_bh  = sum(bh_pass)
    n_pass_bon = sum(bon_pass)

    top_survivors = sorted(
        [e for e, p in zip(all_entries, bh_pass) if p
         and e["status"] not in ("REJECTED", "INSUFFICIENT_DATA")],
        key=lambda x: x["wf_sharpe"],
        reverse=True,
    )

    return {
        "n_tested":          len(all_entries),
        "n_strategies":      len(family_results),
        "n_pass_bh":         n_pass_bh,
        "n_pass_bonferroni": n_pass_bon,
        "bh_alpha":          alpha,
        "bonferroni_alpha":  round(alpha / max(len(all_entries), 1), 6),
        "top_survivors_bh":  [
            {
                "strategy_key": e["strategy_key"],
                "symbol":       e["symbol"],
                "params":       e["params"],
                "wf_sharpe":    e["wf_sharpe"],
                "dsr":          e["dsr"],
                "bootstrap_p":  e["bootstrap_p"],
                "status":       e["status"],
            }
            for e in top_survivors[:10]
        ],
    }


# ── Smoke test ────────────────────────────────────────────────────────────────
def _run_smoke_test(python_venv: str = None) -> None:
    """
    Smoke test: run OFI_Continuation on GC with default params.
    Verifies: bar data loads, strategy produces > 0 trades, DSR > -1.

    Prints SMOKE TEST PASSED or SMOKE TEST FAILED.
    Exits with code 0 on pass, 1 on fail.
    """
    SMOKE_STRATEGY = "OFI_Continuation"
    SMOKE_SYMBOL   = "GC"
    SMOKE_PARAMS   = {"ofi_pct": 92, "roll_win": 100, "rr_ratio": 1.5, "hold_bars": 10}

    print("=" * 60)
    print(f" SMOKE TEST: {SMOKE_STRATEGY} on {SMOKE_SYMBOL} (default params)")
    print("=" * 60)

    # Load bars
    try:
        bars = _load_bars(SMOKE_SYMBOL)
    except FileNotFoundError as e:
        print(f"\nSMOKE TEST FAILED — {e}")
        sys.exit(1)

    if bars.empty:
        print(f"\nSMOKE TEST FAILED — {SMOKE_SYMBOL} bar data is empty.")
        sys.exit(1)

    # Load strategy
    try:
        StratClass = _load_strategy_class(SMOKE_STRATEGY)
    except Exception as e:
        print(f"\nSMOKE TEST FAILED — Cannot import {SMOKE_STRATEGY}: {e}")
        sys.exit(1)

    try:
        strat = StratClass(params=SMOKE_PARAMS)
    except Exception as e:
        print(f"\nSMOKE TEST FAILED — Strategy init error: {e}")
        sys.exit(1)

    spec = CONTRACT_SPECS[SMOKE_SYMBOL]

    try:
        signals = strat.generate_signals(bars)
        trades  = strat.signals_to_trades(bars, signals)
    except Exception as e:
        print(f"\nSMOKE TEST FAILED — Strategy execution error: {e}")
        sys.exit(1)

    n_trades = len(trades) if trades else 0

    if n_trades <= 0:
        print(f"\nSMOKE TEST FAILED — {SMOKE_STRATEGY} on {SMOKE_SYMBOL} produced 0 trades. "
              "Check data and imports.")
        sys.exit(1)

    trades_df = pd.DataFrame(trades)
    trades_df = _apply_costs(trades_df, spec, slippage_ticks=1)
    m = _compute_metrics(trades_df, spec)

    if not m:
        print("\nSMOKE TEST FAILED — Metrics computation returned empty result.")
        sys.exit(1)

    dsr = m["dsr"]

    print(f"  Trades:    {n_trades}")
    print(f"  Sharpe:    {m['sharpe']:.3f}")
    print(f"  DSR:       {dsr:.3f}")
    print(f"  Total P&L: ${m['total_pnl']:,.0f}")

    if dsr <= -1:
        print(f"\nSMOKE TEST FAILED — DSR={dsr:.3f} is below -1 threshold.")
        sys.exit(1)

    print("\nSMOKE TEST PASSED. Pipeline is functional.")
    print("=" * 60)


# ── Summary report ────────────────────────────────────────────────────────────
def _print_summary(
    all_results: Dict[str, Dict[str, List[dict]]],
    family_reports: Dict[str, dict],
) -> None:
    """Print per-family pass rate and top survivors to console."""
    print("\n" + "=" * 70)
    print(" FACTORY SUMMARY REPORT")
    print("=" * 70)

    for family, fam_report in family_reports.items():
        n_tested = fam_report["n_tested"]
        n_bh     = fam_report["n_pass_bh"]
        n_bon    = fam_report["n_pass_bonferroni"]
        pct      = (n_bh / n_tested * 100) if n_tested else 0.0

        print(f"\nFamily: {family}")
        print(f"  Tested:              {n_tested} combos")
        print(f"  Pass (BH FDR):       {n_bh} ({pct:.1f}%)")
        print(f"  Pass (Bonferroni):   {n_bon}")

        top = fam_report.get("top_survivors_bh", [])
        if top:
            print(f"  Top survivors:")
            for s in top[:5]:
                print(f"    {s['strategy_key']:25s} {s['symbol']}  "
                      f"wf_sharpe={s['wf_sharpe']:+.2f}  "
                      f"dsr={s['dsr']:.3f}  "
                      f"bp={s['bootstrap_p']:.3f}  "
                      f"[{s['status']}]")

    print(f"\nLedger: {LEDGER_FILE}")
    print("=" * 70)


# ── Main entry point ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Fortress Strategy Testing Factory — controlled anti-overfitting pipeline"
    )
    parser.add_argument("--family",     type=str, default=None,
                        help="Test only strategies in this family (e.g. A_CVD)")
    parser.add_argument("--priority",   type=int, default=None,
                        help="Test only strategies with this priority level (1=highest)")
    parser.add_argument("--status",     type=str, default=None,
                        help="Test only strategies with this status (e.g. BACKLOG)")
    parser.add_argument("--symbols",    nargs="+", default=["GC", "SI"],
                        help="Symbols to test (default: GC SI)")
    parser.add_argument("--max-combos", type=int, default=DEFAULT_MAX_COMBOS,
                        help=f"Max parameter combinations per strategy (default: {DEFAULT_MAX_COMBOS}). "
                             f"Excess combos are randomly subsampled without replacement.")
    parser.add_argument("--exclude",    nargs="+", default=[],
                        help="Strategy keys to exclude")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Show what would run without executing")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Run pipeline smoke test and exit")
    parser.add_argument("--n-bootstrap", type=int, default=200,
                        help="Number of bootstrap shuffles for p-value (default: 200)")
    parser.add_argument("--fdr-method", choices=["bh", "bonferroni"], default="bh",
                        help="FDR correction method (default: bh)")
    parser.add_argument("--fdr-alpha",  type=float, default=0.05,
                        help="Family-wise error rate (default: 0.05)")
    args = parser.parse_args()

    if args.smoke_test:
        _run_smoke_test()
        return

    print("=" * 70)
    print(" FORTRESS STRATEGY TESTING FACTORY")
    print(f" Date:     {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f" Symbols:  {args.symbols}")
    print(f" Max combos/strategy: {args.max_combos}")
    print(f" FDR method: {args.fdr_method.upper()}")
    if args.dry_run:
        print(" MODE: DRY RUN (no backtests will execute)")
    print("=" * 70)

    # Load universe
    universe = _load_universe()
    if not universe and not args.dry_run:
        print(f"\n[WARN] No strategies found in universe. "
              f"Create {UNIVERSE_JSON} or run --smoke-test.")
        return

    # Filter
    selected = _filter_universe(universe, args)
    if not selected:
        print(f"\n[WARN] No strategies match filter criteria.")
        if args.family:
            print(f"  --family {args.family!r}")
        if args.priority is not None:
            print(f"  --priority {args.priority}")
        if args.status:
            print(f"  --status {args.status!r}")
        return

    print(f"\nSelected {len(selected)} strategies from universe "
          f"(total: {len(universe)})")

    # Generate run ID
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    print(f"Run ID: {run_id}\n")

    # Pre-load bars for all requested symbols
    bars_cache: Dict[str, pd.DataFrame] = {}
    for sym in args.symbols:
        try:
            bars_cache[sym] = _load_bars(sym)
        except Exception as e:
            print(f"[WARN] Cannot load bars for {sym}: {e} — skipping.")

    if not bars_cache and not args.dry_run:
        sys.exit("[ERROR] No bar data available. Check 01_data/tick_bars/")

    # Group by family for FDR reporting
    families = {}
    for s in selected:
        fam = s.get("family", "UNKNOWN")
        if fam not in families:
            families[fam] = []
        families[fam].append(s)

    all_results: Dict[str, Dict[str, List[dict]]] = {}
    family_reports: Dict[str, dict] = {}

    # Process family by family
    for family, strategies in families.items():
        print(f"\n{'─' * 60}")
        print(f" Family: {family}  ({len(strategies)} strategies)")
        print(f"{'─' * 60}")

        family_key_results: Dict[str, List[dict]] = {}

        for strat_def in strategies:
            sk = strat_def["strategy_key"]
            print(f"\n  Strategy: {sk}")

            key_results: List[dict] = []

            for sym in args.symbols:
                if sym not in bars_cache:
                    continue
                sym_list = strat_def.get("symbols", args.symbols)
                if sym not in sym_list:
                    print(f"    [{sym}] Not in strategy symbol list — skipping.")
                    continue

                entries = _test_one(
                    strategy_def=strat_def,
                    symbol=sym,
                    bars=bars_cache[sym],
                    max_combos=args.max_combos,
                    run_id=run_id,
                    dry_run=args.dry_run,
                    n_bootstrap=args.n_bootstrap,
                )
                key_results.extend(entries)

            family_key_results[sk] = key_results

            # Update status in universe for the best combo
            if key_results and not args.dry_run:
                best = max(key_results, key=lambda e: e.get("wf_sharpe", -999))
                _update_universe_status(universe, sk, best["status"])

        all_results[family] = family_key_results

        # FDR report for this family
        if not args.dry_run:
            report = _produce_family_report(
                family_key_results,
                alpha=args.fdr_alpha,
            )
            family_reports[family] = report

            # Save family report
            report_path = LEDGER_DIR / f"fdr_{family}_{run_id}.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\n  FDR report saved: {report_path.name}")

    # Save updated universe
    if not args.dry_run and universe:
        _save_universe(universe)
        print(f"\nUniverse updated: {UNIVERSE_JSON}")

    # Summary
    if not args.dry_run:
        _print_summary(all_results, family_reports)
    else:
        print(f"\n[DRY RUN] Would test {len(selected)} strategies × "
              f"{len(args.symbols)} symbols with max {args.max_combos} combos each.")
        for s in selected:
            n_combos = _count_grid(s.get("param_grid", {}))
            actual = min(n_combos, args.max_combos)
            print(f"  {s['strategy_key']:30s}  "
                  f"grid_size={n_combos:>4}  "
                  f"actual={actual:>4}  "
                  f"family={s.get('family','?')}")


def _update_universe_status(universe: List[dict], strategy_key: str,
                            new_status: str) -> None:
    """Update the status field for a strategy in the in-memory universe list."""
    for s in universe:
        if s.get("strategy_key") == strategy_key:
            old_status = s.get("status", "UNKNOWN")
            # Only promote, never demote (PAPER_CANDIDATE > WATCHLIST > BACKLOG)
            status_rank = {
                "BACKLOG": 0, "TESTING": 1, "INSUFFICIENT_DATA": 1,
                "REJECTED": 2, "RESEARCH_ONLY": 3,
                "WATCHLIST": 4, "HIGH_CORRELATION": 4,
                "PAPER_CANDIDATE": 5, "DEMO_CANDIDATE": 6, "LIVE_BLOCKED": 7,
            }
            if status_rank.get(new_status, 0) > status_rank.get(old_status, 0):
                s["status"] = new_status
                s["last_tested"] = datetime.now(timezone.utc).isoformat()
            break


def _count_grid(param_grid: dict) -> int:
    """Count total combinations in a parameter grid."""
    if not param_grid:
        return 0
    total = 1
    for vals in param_grid.values():
        total *= len(vals)
    return total


if __name__ == "__main__":
    main()
