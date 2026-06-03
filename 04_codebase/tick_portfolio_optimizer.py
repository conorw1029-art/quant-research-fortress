"""
tick_portfolio_optimizer.py — Fortress Portfolio Analysis Tool
==============================================================
Loads all evidence-passed strategy results, reconstructs trade ledgers,
computes correlation matrices, drawdown overlaps, and produces portfolio
construction recommendations using multiple weighting methods.

Usage:
  venv_new/Scripts/python.exe 04_codebase/tick_portfolio_optimizer.py
  venv_new/Scripts/python.exe 04_codebase/tick_portfolio_optimizer.py --top-n 5
  venv_new/Scripts/python.exe 04_codebase/tick_portfolio_optimizer.py --max-dd-per-account 1000
  venv_new/Scripts/python.exe 04_codebase/tick_portfolio_optimizer.py --output-report
  venv_new/Scripts/python.exe 04_codebase/tick_portfolio_optimizer.py --exclude Depth_Imbalance_Momentum
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
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

# ── Paths ─────────────────────────────────────────────────────────────────────
L2_DIR    = ROOT / "05_backtests" / "l2_results"
BAR_DIR   = ROOT / "01_data" / "tick_bars"
DOCS_DIR  = ROOT / "08_docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

REPORT_PATH = DOCS_DIR / "portfolio_candidate_report.json"

# ── Contract specs ────────────────────────────────────────────────────────────
CONTRACT_SPECS = {
    "GC": {"tick_size": 0.10, "tick_value": 10.0,  "name": "Gold"},
    "SI": {"tick_size": 0.005, "tick_value": 25.0, "name": "Silver"},
    "CL": {"tick_size": 0.01, "tick_value": 10.0,  "name": "Crude Oil"},
}

# ── Flagged strategies (position-overlap issues) ──────────────────────────────
FLAGGED_STRATEGIES: set = set()  # Depth_Imbalance_Momentum rehabilitated 2026-06-03

# ── OHLCV survivors (from 05_backtests/) ─────────────────────────────────────
OHLCV_SURVIVORS = [
    {"strategy_key": "vwap_reclaim_gc",    "symbol": "GC",
     "module": "src.strategies.vwap_reclaim",    "class": "VWAPReclaimStrategy"},
    {"strategy_key": "bollinger_rsi_gc",   "symbol": "GC",
     "module": "src.strategies.bollinger_rsi",   "class": "BollingerRSIStrategy"},
    {"strategy_key": "vwap_reclaim_si",    "symbol": "SI",
     "module": "src.strategies.vwap_reclaim",    "class": "VWAPReclaimStrategy"},
    {"strategy_key": "donchian_breakout_cl","symbol": "CL",
     "module": "src.strategies.donchian_breakout","class": "DonchianBreakoutStrategy"},
    {"strategy_key": "fomc_drift",         "symbol": "GC",
     "module": "src.strategies.fomc_drift",      "class": "FOMCDriftStrategy"},
]

# ── L2 strategy class map ─────────────────────────────────────────────────────
L2_CLASS_MAP = {
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


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_bars(symbol: str) -> Optional[pd.DataFrame]:
    """Load L2 or primary bar data for a symbol. Returns None if not found."""
    l2_path      = BAR_DIR / f"{symbol}_bars_l2_1m.parquet"
    primary_path = BAR_DIR / f"{symbol}_bars_1m.parquet"

    for path in (l2_path, primary_path):
        if path.exists():
            bars = pd.read_parquet(path)
            if not bars.empty:
                if "session_vwap" not in bars.columns:
                    bars["session_vwap"] = _calc_session_vwap(bars)
                return bars

    print(f"  [WARN] No bars found for {symbol}")
    return None


def _calc_session_vwap(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    vol   = bars.get("volume", pd.Series(1, index=bars.index))
    session_date = (bars.index - pd.Timedelta(hours=17)).date
    vwap = pd.Series(np.nan, index=bars.index)
    for date, grp in bars.groupby(pd.Series(session_date, index=bars.index)):
        gv = vol.loc[grp.index]
        gc = close.loc[grp.index]
        cumvol = gv.cumsum()
        cumtpvol = (gc * gv).cumsum()
        vwap.loc[grp.index] = cumtpvol / cumvol.replace(0, np.nan)
    return vwap


def _load_evidence_json(path: Path) -> List[dict]:
    """Load a passed_evidence.json file. Returns empty list if not found."""
    if not path.exists():
        print(f"  [WARN] Not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_strategy_class(strategy_key: str, module_path: str, class_name: str):
    """Dynamically import strategy class."""
    import importlib
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    except Exception as e:
        print(f"  [WARN] Cannot import {class_name} from {module_path}: {e}")
        return None


# ── Apply costs and compute daily P&L ─────────────────────────────────────────

def _apply_costs(trades_df: pd.DataFrame, spec: dict,
                 slippage_ticks: int = 1) -> pd.DataFrame:
    out = trades_df.copy()
    slip_pts = slippage_ticks * 2 * spec["tick_size"]
    dollars_per_point = spec["tick_value"] / spec["tick_size"]
    commission_pts = (2.25 * 2) / dollars_per_point
    out["net_pnl"] = out.get("gross_pnl", 0) - slip_pts - commission_pts
    return out


def _trades_to_daily_pnl(trades_df: pd.DataFrame, spec: dict) -> pd.Series:
    """Convert trade-level P&L to daily dollar P&L series."""
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return pd.Series(dtype=float)

    tick_val = spec["tick_value"]
    tick_sz  = spec["tick_size"]
    dollar_pnl = trades_df["net_pnl"] * (tick_val / tick_sz)

    if "exit_time" in trades_df.columns:
        daily = dollar_pnl.groupby(trades_df["exit_time"].dt.date).sum()
        daily.index = pd.to_datetime(daily.index)
    else:
        daily = pd.Series(dollar_pnl.values,
                          index=pd.date_range("2021-01-01", periods=len(dollar_pnl), freq="D"))
    return daily


# ── Reconstruct trade ledger for a strategy ───────────────────────────────────

def _reconstruct_trades(
    strategy_key: str,
    params: dict,
    symbol: str,
    bars: pd.DataFrame,
    module_path: str,
    class_name: str,
    slippage_ticks: int = 1,
) -> Optional[pd.DataFrame]:
    """
    Run a strategy on the full bar data and return a trade DataFrame
    with net P&L in price points.
    """
    StratClass = _load_strategy_class(strategy_key, module_path, class_name)
    if StratClass is None:
        return None

    spec = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])

    try:
        strat   = StratClass(params=params)
        signals = strat.generate_signals(bars)
        trades  = strat.signals_to_trades(bars, signals)
    except Exception as e:
        print(f"  [WARN] {strategy_key} signal error: {e}")
        return None

    if not trades:
        return None

    trades_df = _apply_costs(pd.DataFrame(trades), spec, slippage_ticks)
    return trades_df


# ── Metrics ───────────────────────────────────────────────────────────────────

def _compute_full_metrics(daily_pnl: pd.Series) -> dict:
    """Comprehensive metrics from a daily dollar P&L series."""
    if daily_pnl.empty or len(daily_pnl) < 5:
        return {}

    n_days   = len(daily_pnl)
    total    = float(daily_pnl.sum())
    mu       = float(daily_pnl.mean())
    sig      = float(daily_pnl.std())
    sharpe   = (mu / (sig + 1e-9)) * np.sqrt(252)
    win_rate = float((daily_pnl > 0).sum() / n_days)

    cumulative = daily_pnl.cumsum()
    roll_max   = cumulative.cummax()
    drawdown   = roll_max - cumulative
    max_dd     = float(drawdown.max())

    # Annualised volatility (dollar)
    ann_vol = sig * np.sqrt(252)

    # Monthly P&L
    monthly = daily_pnl.resample("ME").sum() if hasattr(daily_pnl.index, "freq") else \
              daily_pnl.groupby(daily_pnl.index.to_period("M")).sum()
    monthly_wr = float((monthly > 0).sum() / max(len(monthly), 1))
    expected_monthly = float(monthly.mean()) if len(monthly) > 0 else 0.0

    worst_day = float(daily_pnl.min())

    return {
        "n_days":          n_days,
        "total_pnl":       round(total, 2),
        "sharpe":          round(sharpe, 4),
        "ann_vol":         round(ann_vol, 2),
        "max_dd":          round(max_dd, 2),
        "daily_win_rate":  round(win_rate, 4),
        "monthly_win_rate":round(monthly_wr, 4),
        "expected_monthly":round(expected_monthly, 2),
        "worst_day":       round(worst_day, 2),
    }


def _drawdown_overlap(daily_a: pd.Series, daily_b: pd.Series) -> float:
    """
    Compute the fraction of trading days where both strategies are
    simultaneously in a drawdown from their respective equity peaks.
    """
    common_idx = daily_a.index.intersection(daily_b.index)
    if len(common_idx) < 10:
        return 0.0

    a = daily_a.loc[common_idx].cumsum()
    b = daily_b.loc[common_idx].cumsum()

    a_dd = (a.cummax() - a) > 0
    b_dd = (b.cummax() - b) > 0

    both_in_dd = (a_dd & b_dd).sum()
    total_days  = len(common_idx)

    return round(float(both_in_dd / total_days), 4)


# ── Correlation matrix ────────────────────────────────────────────────────────

def _build_correlation_matrix(
    strategy_pnls: Dict[str, pd.Series],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build Pearson correlation matrix and drawdown overlap matrix
    from a dict of {strategy_label: daily_pnl_series}.

    Returns: (correlation_df, dd_overlap_df)
    """
    labels = list(strategy_pnls.keys())
    n = len(labels)

    # Align all series to common date range
    all_dates = sorted(set().union(*[set(s.index) for s in strategy_pnls.values()]))
    aligned = pd.DataFrame(index=all_dates)
    for label, series in strategy_pnls.items():
        aligned[label] = series.reindex(all_dates).fillna(0.0)

    corr_matrix = aligned.corr(method="pearson")

    # Drawdown overlap
    dd_data = {}
    for i, label_a in enumerate(labels):
        row = {}
        for j, label_b in enumerate(labels):
            if i == j:
                row[label_b] = 1.0
            elif j < i:
                row[label_b] = dd_data[label_b][label_a]
            else:
                row[label_b] = _drawdown_overlap(
                    aligned[label_a], aligned[label_b]
                )
        dd_data[label_a] = row

    dd_matrix = pd.DataFrame(dd_data).T.reindex(labels, axis=0).reindex(labels, axis=1)

    return corr_matrix, dd_matrix


# ── Portfolio construction methods ────────────────────────────────────────────

def _equal_weight(strategies: List[dict]) -> Dict[str, float]:
    """Equal weight: 1 contract per strategy."""
    w = 1.0 / max(len(strategies), 1)
    return {s["label"]: w for s in strategies}


def _risk_parity(strategies: List[dict]) -> Dict[str, float]:
    """Weight inversely proportional to annualised dollar volatility."""
    vols = {}
    for s in strategies:
        ann_vol = s["metrics"].get("ann_vol", 1.0)
        vols[s["label"]] = max(ann_vol, 1.0)

    inv_vol = {k: 1.0 / v for k, v in vols.items()}
    total_inv = sum(inv_vol.values())
    if total_inv == 0:
        return _equal_weight(strategies)
    return {k: round(v / total_inv, 4) for k, v in inv_vol.items()}


def _max_dd_constrained(
    strategies: List[dict],
    max_dd_per_account: float,
    corr_matrix: pd.DataFrame,
) -> Dict[str, float]:
    """
    Maximum weight such that each strategy's contribution to combined DD
    stays below max_dd_per_account.

    Simple approximation: weight = max_dd_per_account / individual_max_dd,
    capped at 1.0 (1 contract max per strategy in this framework).
    """
    weights = {}
    for s in strategies:
        ind_dd = s["metrics"].get("max_dd", max_dd_per_account)
        if ind_dd <= 0:
            weights[s["label"]] = 1.0
        else:
            weights[s["label"]] = min(1.0, round(max_dd_per_account / ind_dd, 4))
    return weights


def _top_n_by_wf_sharpe(
    strategies: List[dict],
    n: int,
) -> Dict[str, float]:
    """Select the top-N by WF Sharpe, equal weight among them."""
    sorted_strats = sorted(strategies,
                           key=lambda s: s.get("wf_sharpe", 0.0),
                           reverse=True)
    selected = sorted_strats[:n]
    w = 1.0 / max(len(selected), 1)
    return {s["label"]: w for s in selected}


def _min_correlation_portfolio(
    strategies: List[dict],
    n: int,
    corr_matrix: pd.DataFrame,
) -> Dict[str, float]:
    """
    Select N strategies that minimise the average pairwise correlation.
    Uses a greedy approach: start with the strategy that has the lowest
    average correlation to all others, then greedily add the strategy
    that adds the least correlation to the current portfolio.
    """
    labels = [s["label"] for s in strategies]
    available = set(labels)

    if not labels or corr_matrix.empty:
        return _equal_weight(strategies[:n])

    # Start: pick strategy with lowest average correlation to others
    avg_corr = {}
    for label in labels:
        if label in corr_matrix.index:
            others = [l for l in labels if l != label and l in corr_matrix.columns]
            if others:
                avg_corr[label] = float(corr_matrix.loc[label, others].mean())
            else:
                avg_corr[label] = 0.0
        else:
            avg_corr[label] = 0.0

    selected = [min(avg_corr, key=avg_corr.get)]
    available.discard(selected[0])

    while len(selected) < n and available:
        # Find the available strategy with lowest mean correlation to selected set
        best_label = None
        best_avg_corr = float("inf")

        for candidate in available:
            if candidate not in corr_matrix.index:
                corr_to_selected = 0.0
            else:
                sel_in_matrix = [s for s in selected if s in corr_matrix.columns]
                if sel_in_matrix:
                    corr_to_selected = float(
                        corr_matrix.loc[candidate, sel_in_matrix].mean()
                    )
                else:
                    corr_to_selected = 0.0

            if corr_to_selected < best_avg_corr:
                best_avg_corr = corr_to_selected
                best_label = candidate

        if best_label:
            selected.append(best_label)
            available.discard(best_label)

    w = 1.0 / max(len(selected), 1)
    return {label: w for label in selected}


# ── Portfolio P&L simulation ──────────────────────────────────────────────────

def _simulate_portfolio(
    weights: Dict[str, float],
    strategy_pnls: Dict[str, pd.Series],
) -> pd.Series:
    """
    Simulate portfolio daily P&L given weights (fractional contracts)
    and per-strategy daily dollar P&L series.
    """
    all_dates = sorted(set().union(*[set(s.index) for s in strategy_pnls.values()
                                     if s is not None and not s.empty]))
    if not all_dates:
        return pd.Series(dtype=float)

    portfolio = pd.Series(0.0, index=all_dates)
    for label, weight in weights.items():
        if label in strategy_pnls and strategy_pnls[label] is not None:
            portfolio += strategy_pnls[label].reindex(all_dates).fillna(0.0) * weight

    return portfolio


def _portfolio_sharpe_improvement(
    portfolio_pnl: pd.Series,
    best_individual_pnl: pd.Series,
) -> float:
    """
    Sharpe improvement of portfolio over best individual strategy.
    Positive = portfolio is better.
    """
    def sharpe(s):
        s = s.reindex(s.index).fillna(0.0)
        mu = s.mean()
        sig = s.std()
        return (mu / (sig + 1e-9)) * np.sqrt(252)

    port_sharpe = sharpe(portfolio_pnl)
    ind_sharpe  = sharpe(best_individual_pnl)
    return round(port_sharpe - ind_sharpe, 4)


# ── Build portfolio summary table ─────────────────────────────────────────────

def _build_summary_table(
    strategies: List[dict],
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame,
    strategy_pnls: Dict[str, pd.Series],
) -> List[dict]:
    """Build one row per strategy for the summary table."""
    rows = []
    labels = list(strategy_pnls.keys())

    for s in strategies:
        label = s["label"]
        weight = weights.get(label, 0.0)
        m = s["metrics"]

        # Correlation rank: average correlation to all others
        if label in corr_matrix.index and len(corr_matrix) > 1:
            others = [l for l in labels if l != label and l in corr_matrix.columns]
            if others:
                avg_corr = float(corr_matrix.loc[label, others].abs().mean())
            else:
                avg_corr = 0.0
        else:
            avg_corr = 0.0

        # Contribution to portfolio max DD (approx: weight × individual max DD)
        dd_contribution = weight * m.get("max_dd", 0.0)

        rows.append({
            "label":              label,
            "strategy_key":       s["strategy_key"],
            "symbol":             s["symbol"],
            "weight":             round(weight, 4),
            "expected_monthly_pnl": round(m.get("expected_monthly", 0.0) * weight, 2),
            "max_dd_contribution": round(dd_contribution, 2),
            "individual_max_dd":  m.get("max_dd", 0.0),
            "sharpe":             m.get("sharpe", 0.0),
            "wf_sharpe":          s.get("wf_sharpe", 0.0),
            "monthly_win_rate":   m.get("monthly_win_rate", 0.0),
            "n_trades_5yr":       s.get("n_trades", 0),
            "avg_correlation":    round(avg_corr, 4),
            "correlation_rank":   0,  # filled below
        })

    # Rank by average correlation (lower = better for diversification)
    rows_sorted = sorted(rows, key=lambda r: r["avg_correlation"])
    for rank, row in enumerate(rows_sorted, 1):
        label = row["label"]
        for orig_row in rows:
            if orig_row["label"] == label:
                orig_row["correlation_rank"] = rank
                break

    return rows


# ── Main pipeline ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fortress Portfolio Optimizer — evidence-based portfolio construction"
    )
    parser.add_argument("--top-n",              type=int,   default=5,
                        help="Number of strategies in portfolio recommendation (default: 5)")
    parser.add_argument("--max-dd-per-account", type=float, default=1000.0,
                        help="DD constraint per Topstep account in dollars (default: 1000)")
    parser.add_argument("--exclude",            nargs="+",  default=[],
                        help="Strategy keys to exclude (e.g. Depth_Imbalance_Momentum)")
    parser.add_argument("--output-report",      action="store_true",
                        help="Write portfolio_candidate_report.json to 08_docs/")
    parser.add_argument("--slippage-ticks",     type=int,   default=1,
                        help="Slippage in ticks for trade reconstruction (default: 1)")
    parser.add_argument("--include-ohlcv",      action="store_true",
                        help="Include OHLCV survivors in analysis")
    parser.add_argument("--min-trades",         type=int,   default=200,
                        help="Minimum trades in 5-year period (default: 200)")
    parser.add_argument("--max-day-loss",       type=float, default=2000.0,
                        help="Maximum single-day loss allowed before filtering strategy (default: 2000)")
    parser.add_argument("--news-filtered",      action="store_true",
                        help="Use news-filtered evidence files (GC/SI_newsfiltered_passed_evidence.json)")
    args = parser.parse_args()

    # Combine exclude lists
    excluded = set(args.exclude) | FLAGGED_STRATEGIES
    if excluded:
        print(f"Excluded strategies: {excluded}")

    print("=" * 70)
    print(" FORTRESS PORTFOLIO OPTIMIZER")
    print(f" Top-N:               {args.top_n}")
    print(f" Max DD per account:  ${args.max_dd_per_account:,.0f}")
    print(f" Min trades (5yr):    {args.min_trades}")
    print(f" Slippage:            {args.slippage_ticks} tick(s)")
    print("=" * 70)

    # ── Step 1: Load evidence-passed results ──────────────────────────────────
    print("\n[Step 1] Loading evidence-passed strategy results...")

    if args.news_filtered:
        gc_file = "GC_newsfiltered_passed_evidence.json"
        si_file = "SI_newsfiltered_passed_evidence.json"
        print("  [NEWS-FILTERED mode] Using news-filtered evidence files.")
    else:
        gc_file = "GC_passed_evidence.json"
        si_file = "SI_passed_evidence.json"

    gc_evidence = _load_evidence_json(L2_DIR / gc_file)
    si_evidence = _load_evidence_json(L2_DIR / si_file)
    all_evidence = gc_evidence + si_evidence

    print(f"  GC: {len(gc_evidence)} passed-evidence entries")
    print(f"  SI: {len(si_evidence)} passed-evidence entries")

    # Select best param combo per strategy (highest wf_sharpe)
    best_by_strategy: Dict[str, dict] = {}
    for entry in all_evidence:
        strat_name = entry.get("strategy", "")
        symbol     = entry.get("symbol", "")
        key = f"{strat_name}_{symbol}"

        if strat_name in excluded:
            continue

        params = json.loads(entry.get("params", "{}")) if isinstance(
            entry.get("params"), str) else entry.get("params", {})
        wf_sharpe = entry.get("wf_sharpe", 0.0)

        if key not in best_by_strategy or wf_sharpe > best_by_strategy[key].get("wf_sharpe", -999):
            best_by_strategy[key] = {
                "label":        key,
                "strategy_key": strat_name,
                "symbol":       symbol,
                "params":       params,
                "wf_sharpe":    wf_sharpe,
                "n_trades":     entry.get("stress_trades", entry.get("wf_trades", 0)),
                "bootstrap_p":  entry.get("bootstrap_p", 1.0),
                "slippage_ladder": entry.get("slippage_ladder", []),
                "evidence_entry": entry,
            }

    # ── Step 2: Load bar data ─────────────────────────────────────────────────
    print("\n[Step 2] Loading bar data...")
    bars_cache: Dict[str, pd.DataFrame] = {}
    symbols_needed = set(v["symbol"] for v in best_by_strategy.values())

    if args.include_ohlcv:
        symbols_needed.update(s["symbol"] for s in OHLCV_SURVIVORS
                              if s["strategy_key"] not in excluded)

    for sym in sorted(symbols_needed):
        b = _load_bars(sym)
        if b is not None:
            bars_cache[sym] = b
            print(f"  [{sym}] {len(b):,} bars loaded")

    # ── Step 3: Reconstruct trade ledgers ─────────────────────────────────────
    print("\n[Step 3] Reconstructing trade ledgers...")

    strategies: List[dict] = []
    strategy_pnls: Dict[str, pd.Series] = {}

    for key, strat_def in best_by_strategy.items():
        symbol = strat_def["symbol"]
        if symbol not in bars_cache:
            print(f"  [SKIP] {key} — no bars for {symbol}")
            continue

        strat_name = strat_def["strategy_key"]
        if strat_name not in L2_CLASS_MAP:
            print(f"  [SKIP] {key} — no class map entry for {strat_name}")
            continue

        module_path, class_name = L2_CLASS_MAP[strat_name]
        bars = bars_cache[symbol]
        spec = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])

        trades_df = _reconstruct_trades(
            strategy_key=strat_name,
            params=strat_def["params"],
            symbol=symbol,
            bars=bars,
            module_path=module_path,
            class_name=class_name,
            slippage_ticks=args.slippage_ticks,
        )

        if trades_df is None or trades_df.empty:
            print(f"  [SKIP] {key} — no trades reconstructed")
            continue

        n_trades = len(trades_df)
        strat_def["n_trades_reconstructed"] = n_trades

        # Hard filter: minimum trade count
        if n_trades < args.min_trades:
            print(f"  [FILTER] {key} — {n_trades} trades < min {args.min_trades}")
            continue

        # Hard filter: worst day loss > $2,000 (Topstep runway constraint)
        daily_pnl = _trades_to_daily_pnl(trades_df, spec)
        if daily_pnl.empty:
            print(f"  [SKIP] {key} — empty daily P&L")
            continue

        worst_day = float(daily_pnl.min())
        max_day_loss = -abs(args.max_day_loss)
        if worst_day < max_day_loss:
            print(f"  [FILTER] {key} — worst day ${worst_day:,.0f} exceeds ${abs(max_day_loss):,.0f} limit")
            continue

        m = _compute_full_metrics(daily_pnl)
        strat_def["metrics"] = m

        print(f"  [OK] {key:40s}  trades={n_trades:>4}  "
              f"sharpe={m.get('sharpe',0):+.2f}  "
              f"max_dd=${m.get('max_dd',0):>7,.0f}  "
              f"monthly_wr={m.get('monthly_win_rate',0):.1%}")

        strategies.append(strat_def)
        strategy_pnls[key] = daily_pnl

    # Optionally include OHLCV survivors
    if args.include_ohlcv:
        print("\n[Step 3b] Adding OHLCV survivors...")
        for ohlcv_def in OHLCV_SURVIVORS:
            sk = ohlcv_def["strategy_key"]
            if sk in excluded:
                continue
            symbol = ohlcv_def["symbol"]
            if symbol not in bars_cache:
                continue

            trades_df = _reconstruct_trades(
                strategy_key=sk,
                params={},
                symbol=symbol,
                bars=bars_cache[symbol],
                module_path=ohlcv_def["module"],
                class_name=ohlcv_def["class"],
                slippage_ticks=args.slippage_ticks,
            )
            if trades_df is None or trades_df.empty:
                continue

            spec = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])
            daily_pnl = _trades_to_daily_pnl(trades_df, spec)
            if daily_pnl.empty:
                continue

            if len(trades_df) < args.min_trades:
                continue
            if float(daily_pnl.min()) < -2000.0:
                continue

            m = _compute_full_metrics(daily_pnl)
            ohlcv_entry = {
                "label":        sk,
                "strategy_key": sk,
                "symbol":       symbol,
                "params":       {},
                "wf_sharpe":    m.get("sharpe", 0.0),
                "n_trades":     len(trades_df),
                "metrics":      m,
                "source":       "OHLCV",
            }
            strategies.append(ohlcv_entry)
            strategy_pnls[sk] = daily_pnl
            print(f"  [OK] {sk:40s}  trades={len(trades_df):>4}  sharpe={m.get('sharpe',0):+.2f}")

    if not strategies:
        print("\n[ERROR] No strategies survived all filters. "
              "Check --min-trades, --max-dd-per-account, and bar data availability.")
        return

    print(f"\n{len(strategies)} strategies eligible for portfolio analysis.")

    # ── Step 4: Correlation matrix ────────────────────────────────────────────
    print("\n[Step 4] Computing correlation matrix and drawdown overlaps...")

    corr_matrix, dd_matrix = _build_correlation_matrix(strategy_pnls)

    # Print correlation matrix summary
    labels = list(strategy_pnls.keys())
    print(f"\n  Correlation matrix ({len(labels)} x {len(labels)}):")
    if len(labels) <= 10:
        # Print full matrix for small sets
        corr_data = {}
        for la in labels:
            corr_data[la] = {}
            for lb in labels:
                if la in corr_matrix.index and lb in corr_matrix.columns:
                    corr_data[la][lb] = round(float(corr_matrix.loc[la, lb]), 3)
                else:
                    corr_data[la][lb] = 1.0 if la == lb else 0.0
        # Print header
        max_label = max(len(l) for l in labels)
        header = " " * (max_label + 2) + "  ".join(f"{l[:8]:>8}" for l in labels)
        print(f"  {header}")
        for la in labels:
            row_str = f"  {la:<{max_label}}  "
            for lb in labels:
                val = corr_data[la].get(lb, 0.0)
                row_str += f"{val:>8.3f}  "
            print(row_str)
    else:
        print("  (matrix too large to display inline — see output report)")

    # Flag high-correlation pairs
    print("\n  High-correlation pairs (|r| > 0.70):")
    high_corr_found = False
    for i, la in enumerate(labels):
        for j, lb in enumerate(labels):
            if j <= i:
                continue
            if la in corr_matrix.index and lb in corr_matrix.columns:
                r = float(corr_matrix.loc[la, lb])
                if abs(r) > 0.70:
                    print(f"    {la} <-> {lb}:  r={r:+.3f}  [HIGH CORRELATION]")
                    high_corr_found = True

    if not high_corr_found:
        print("    None (all pairs below 0.70 threshold)")

    # ── Step 5: Monthly stability ─────────────────────────────────────────────
    print("\n[Step 5] Monthly stability summary:")
    for s in strategies:
        m = s.get("metrics", {})
        print(f"  {s['label']:40s}  "
              f"monthly_wr={m.get('monthly_win_rate', 0):.1%}  "
              f"expected_monthly=${m.get('expected_monthly', 0):>8,.0f}")

    # ── Step 6: Portfolio construction ───────────────────────────────────────
    print("\n[Step 6] Portfolio construction...")

    portfolios: Dict[str, dict] = {}

    # a) Equal weight
    ew = _equal_weight(strategies)
    portfolios["equal_weight"] = ew

    # b) Risk parity
    rp = _risk_parity(strategies)
    portfolios["risk_parity"] = rp

    # c) Max-DD constrained
    mdc = _max_dd_constrained(strategies, args.max_dd_per_account, corr_matrix)
    portfolios["max_dd_constrained"] = mdc

    # d) Top-N by WF Sharpe
    topn = _top_n_by_wf_sharpe(strategies, args.top_n)
    portfolios["top_n_wf_sharpe"] = topn

    # e) Min correlation portfolio
    mcp = _min_correlation_portfolio(strategies, args.top_n, corr_matrix)
    portfolios["min_correlation"] = mcp

    # Simulate portfolio P&L for each method
    print("\n  Portfolio performance vs best individual:")
    best_ind_label = max(strategies, key=lambda s: s["metrics"].get("sharpe", 0))["label"]
    best_ind_pnl   = strategy_pnls[best_ind_label]
    best_ind_metrics = _compute_full_metrics(best_ind_pnl)

    print(f"  Best individual: {best_ind_label}  Sharpe={best_ind_metrics.get('sharpe',0):.3f}")

    portfolio_results: Dict[str, dict] = {}
    for port_name, weights in portfolios.items():
        port_pnl = _simulate_portfolio(weights, strategy_pnls)
        port_m   = _compute_full_metrics(port_pnl)
        sharpe_improvement = _portfolio_sharpe_improvement(port_pnl, best_ind_pnl)

        portfolio_results[port_name] = {
            "weights":   weights,
            "metrics":   port_m,
            "sharpe_improvement_over_best_individual": sharpe_improvement,
        }

        n_strats = sum(1 for w in weights.values() if w > 0)
        print(f"  {port_name:25s}  n={n_strats}  "
              f"sharpe={port_m.get('sharpe',0):+.3f}  "
              f"max_dd=${port_m.get('max_dd',0):>8,.0f}  "
              f"improvement={sharpe_improvement:+.3f}")

    # ── Step 7: Recommended 5-account portfolio ───────────────────────────────
    print(f"\n[Step 7] Recommended {args.top_n}-account portfolio (min-correlation method):")
    rec_weights = mcp
    rec_labels  = [label for label, w in rec_weights.items() if w > 0]

    rec_strategies = [s for s in strategies if s["label"] in rec_labels]
    summary_table  = _build_summary_table(
        rec_strategies, rec_weights, corr_matrix, strategy_pnls
    )

    # Print summary table
    col_w = 42
    print(f"\n  {'Strategy':<{col_w}} {'Wt':>6} {'Monthly$':>10} {'MaxDD$':>10} "
          f"{'MonthWR':>8} {'CorrRank':>8}")
    print("  " + "-" * (col_w + 50))
    for row in sorted(summary_table, key=lambda r: r["correlation_rank"]):
        print(f"  {row['label']:<{col_w}} "
              f"{row['weight']:>6.3f} "
              f"{row['expected_monthly_pnl']:>10,.0f} "
              f"{row['max_dd_contribution']:>10,.0f} "
              f"{row['monthly_win_rate']:>8.1%} "
              f"{row['correlation_rank']:>8}")

    # ── Step 8: Build output report ───────────────────────────────────────────
    # Correlation matrix as JSON (nested dict of floats)
    corr_json: Dict[str, Dict[str, float]] = {}
    dd_json:   Dict[str, Dict[str, float]] = {}
    for la in labels:
        corr_json[la] = {}
        dd_json[la]   = {}
        for lb in labels:
            corr_json[la][lb] = round(float(corr_matrix.loc[la, lb]), 4) \
                if la in corr_matrix.index and lb in corr_matrix.columns else 0.0
            dd_json[la][lb]   = round(float(dd_matrix.loc[la, lb]), 4) \
                if la in dd_matrix.index and lb in dd_matrix.columns else 0.0

    # ── Build warnings list ───────────────────────────────────────────────────
    warnings_list: List[str] = []
    for la in labels:
        for lb in labels:
            if lb <= la:
                continue
            if la in corr_matrix.index and lb in corr_matrix.columns:
                r = float(corr_matrix.loc[la, lb])
                if abs(r) > 0.70:
                    warnings_list.append(
                        f"HIGH_CORRELATION: {la} <-> {lb} r={r:+.3f}"
                    )

    for s in strategies:
        m = s.get("metrics", {})
        if m.get("monthly_win_rate", 1.0) < 0.55:
            warnings_list.append(
                f"LOW_MONTHLY_WIN_RATE: {s['label']} monthly_wr={m.get('monthly_win_rate',0):.1%}"
            )
        if m.get("max_dd", 0.0) > args.max_dd_per_account * 2:
            warnings_list.append(
                f"LARGE_DRAWDOWN: {s['label']} max_dd=${m.get('max_dd',0):,.0f}"
            )

    # Monthly P&L estimate for the recommended portfolio
    monthly_pnl_estimates = {
        s["label"]: round(s.get("metrics", {}).get("expected_monthly", 0.0), 2)
        for s in strategies
    }

    report = {
        "date":               pd.Timestamp.now().isoformat(),
        "generated_at":       pd.Timestamp.now().isoformat(),
        "n_strategies_input": len(all_evidence),
        "n_strategies_eligible": len(strategies),
        "filters_applied": {
            "min_trades":          args.min_trades,
            "max_dd_per_account":  args.max_dd_per_account,
            "excluded":            list(excluded),
        },
        "strategies": [
            {
                "label":             s["label"],
                "strategy_key":      s["strategy_key"],
                "symbol":            s["symbol"],
                "params":            s.get("params", {}),
                "wf_sharpe":         s.get("wf_sharpe", 0.0),
                "n_trades_5yr":      s.get("n_trades", 0),
                **s.get("metrics", {}),
            }
            for s in strategies
        ],
        "correlation_matrix": corr_json,
        "drawdown_overlap_matrix": dd_json,
        "monthly_pnl_estimates": monthly_pnl_estimates,
        "portfolio_constructions": {
            name: {
                "weights": result["weights"],
                "sharpe":  result["metrics"].get("sharpe", 0.0),
                "max_dd":  result["metrics"].get("max_dd", 0.0),
                "expected_monthly": result["metrics"].get("expected_monthly", 0.0),
                "sharpe_improvement_over_best_individual":
                    result["sharpe_improvement_over_best_individual"],
            }
            for name, result in portfolio_results.items()
        },
        "recommended_portfolio": {
            "method":         "min_correlation",
            "n_strategies":   args.top_n,
            "strategies":     summary_table,
            "weights":        rec_weights,
            "combined_expected_monthly": round(
                sum(s.get("metrics", {}).get("expected_monthly", 0.0) * rec_weights.get(s["label"], 0.0)
                    for s in rec_strategies), 2
            ),
            "combined_max_dd_estimate": round(
                sum(s.get("metrics", {}).get("max_dd", 0.0) * rec_weights.get(s["label"], 0.0)
                    for s in rec_strategies) * 0.7, 2
            ),
        },
        "warnings": warnings_list,
    }

    # Always write the report to 08_docs/portfolio_candidate_report.json
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved: {REPORT_PATH}")

    if not args.output_report:
        pass  # report is always written; --output-report flag is now a no-op kept for backward compat

    print("\n" + "=" * 70)
    print(" PORTFOLIO OPTIMIZER COMPLETE")
    print("=" * 70)

    return report


if __name__ == "__main__":
    main()
