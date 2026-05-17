#!/usr/bin/env python3
"""
L2 Tick Strategy — Full Analysis Runner
========================================
Runs all 30 strategies across all symbols × bar sizes with walk-forward
optimisation, stress tests, and full ranking report.

Architecture:
  1. Load parquet bar files for each symbol
  2. For each (symbol, bar_size, strategy):
       a. Grid-search parameters on rolling WFO folds (in-sample)
       b. Apply best params to out-of-sample test folds
       c. Compute DSR, Sharpe, Calmar, Win-Rate on OOS trades
  3. DSR filter: keep DSR >= 1.0
  4. Stress-test survivors (random-entry baseline, trade-shuffle MC)
  5. Print full ranked report, save JSON + CSV

Usage:
  python tick_full_run.py                      # all symbols, 5-min bars
  python tick_full_run.py --bar-minutes 1      # 1-min bars
  python tick_full_run.py --symbol GC          # single symbol
  python tick_full_run.py --strategy cvd_divergence  # single strategy
  python tick_full_run.py --fast               # small grid, quick run

Runtime estimate: ~15-45 min for full run (all 4 symbols, 5-min bars)
"""

import argparse
import itertools
import json
import os
import sys
import time
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import run_backtest, compute_metrics, SPECS
from tick_strategies import STRATEGIES, STRATEGY_MAP

BAR_DIR    = Path(__file__).parent.parent / "01_data" / "tick_bars"
RESULT_DIR = Path(__file__).parent.parent / "05_backtests"

DSR_THRESHOLD   = 1.0
MIN_TRADES_OOS  = 30       # minimum OOS trades to be considered
MIN_WIN_RATE    = 0.30
MC_SIMS         = 500      # Monte Carlo paths for stress test
MAX_GRID_COMBOS = 12       # cap param search per fold (random sample if > this)

# WFO parameters (in bars) — sized for 5-10 meaningful OOS folds
# GC 1-min:  77k bars  → train=8k,  test=8k  → 8 folds
# GC 3-min:  50k bars  → train=5k,  test=5k  → 9 folds
# GC 5-min:  41k bars  → train=5k,  test=5k  → 7 folds
# GC 15-min: 26k bars  → train=2k,  test=2k  → 12 folds
# GC 30-min: 19k bars  → train=1.5k,test=1.5k→ 11 folds
WFO_TRAIN_BARS = {1: 8_000, 3: 5_000, 5: 5_000, 15: 2_000, 30: 1_000}
WFO_TEST_BARS  = {1: 8_000, 3: 5_000, 5: 5_000, 15: 2_000, 30: 1_000}

# Default backtest exit params
EXIT_PARAMS = {
    "stop_atr_mult":  1.5,
    "tp_atr_mult":    3.0,
    "max_hold_bars":  50,
}


# ── Data loading ─────────────────────────────────────────────────────────────

def load_bars(symbol: str, bar_minutes: int) -> pd.DataFrame | None:
    path = BAR_DIR / f"{symbol}_bars_{bar_minutes}m.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def has_mbp(df: pd.DataFrame) -> bool:
    return "obi_5" in df.columns and df["obi_5"].notna().any()


# ── Parameter grid expansion ─────────────────────────────────────────────────

def expand_grid(param_grid: dict, fast: bool = False) -> list[dict]:
    if fast:
        reduced = {k: [v[len(v) // 2]] for k, v in param_grid.items()}
    else:
        reduced = param_grid
    keys   = list(reduced.keys())
    values = list(reduced.values())
    all_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    # Cap grid size — random sample without replacement for fairness
    if len(all_combos) > MAX_GRID_COMBOS and not fast:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(all_combos), size=MAX_GRID_COMBOS, replace=False)
        all_combos = [all_combos[i] for i in idx]
    return all_combos


# ── Walk-forward optimisation ─────────────────────────────────────────────────

def run_wfo(
    df: pd.DataFrame,
    strategy: dict,
    symbol: str,
    bar_minutes: int,
    fast: bool = False,
) -> dict:
    """
    Anchored WFO: slide test window forward, re-optimise each fold.
    Returns OOS aggregate metrics + best params per fold.
    """
    train_n = WFO_TRAIN_BARS[bar_minutes]
    test_n  = WFO_TEST_BARS[bar_minutes]
    n       = len(df)
    fn      = strategy["compute"]

    if n < train_n + test_n:
        return {"skip": f"insufficient bars ({n})"}

    param_combos = expand_grid(strategy["param_grid"], fast=fast)
    n_params     = len(param_combos)

    all_oos_trades = []
    fold_results   = []

    fold_start = train_n
    fold = 0

    while fold_start + test_n <= n:
        fold += 1
        train_df = df.iloc[:fold_start]
        test_df  = df.iloc[fold_start: fold_start + test_n]

        # ── In-sample: find best params by Sharpe ──────────────────────
        best_sharpe = -np.inf
        best_params = param_combos[0]

        for params in param_combos:
            try:
                sig = fn(train_df, **params)
            except Exception:
                continue
            trades = run_backtest(train_df, sig, symbol, **EXIT_PARAMS)
            if trades.empty or len(trades) < 5:
                continue
            m = compute_metrics(trades, n_params)
            if m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_params = params

        # ── Out-of-sample: apply best params ───────────────────────────
        try:
            sig_oos = fn(test_df, **best_params)
        except Exception as e:
            fold_start += test_n
            continue

        oos_trades = run_backtest(test_df, sig_oos, symbol, **EXIT_PARAMS)
        if not oos_trades.empty:
            all_oos_trades.append(oos_trades)
            fold_m = compute_metrics(oos_trades, n_params)
            fold_results.append({
                "fold":       fold,
                "best_params": best_params,
                "is_sharpe":  best_sharpe,
                **{f"oos_{k}": v for k, v in fold_m.items()},
            })

        fold_start += test_n

    if not all_oos_trades:
        return {"skip": "no OOS trades"}

    combined   = pd.concat(all_oos_trades, ignore_index=True)
    agg_metrics = compute_metrics(combined, n_params)

    # Parameter stability: how often same param wins across folds
    param_keys = list(strategy["param_grid"].keys())
    stability  = {}
    if fold_results and param_keys:
        for k in param_keys:
            vals = [f["best_params"].get(k) for f in fold_results if "best_params" in f]
            if vals:
                from collections import Counter
                most_common = Counter(vals).most_common(1)[0]
                stability[k] = most_common[1] / len(vals)  # fraction picking same value

    return {
        "n_folds":          fold,
        "n_oos_trades":     len(combined),
        "param_stability":  stability,
        "fold_results":     fold_results,
        "_oos_trades_df":   combined,   # kept for stress test, stripped before JSON save
        **agg_metrics,
    }


# ── Stress tests ─────────────────────────────────────────────────────────────

def random_entry_baseline(
    df: pd.DataFrame,
    symbol: str,
    n_trades: int,
    n_sims: int = MC_SIMS,
) -> dict:
    """Compare strategy to random 50/50 entries with same hold time."""
    rng = np.random.default_rng(42)
    spec = SPECS[symbol]
    pv   = spec["point_value"]
    comm = spec["commission"]
    cl   = df["close"].values
    n    = len(cl)
    hold = EXIT_PARAMS["max_hold_bars"]

    sharpes = []
    for _ in range(n_sims):
        entries = rng.integers(0, n - hold - 1, size=n_trades)
        dirs    = rng.choice([-1, 1], size=n_trades)
        pnls    = []
        for e, d in zip(entries, dirs):
            raw = d * (cl[e + hold] - cl[e]) * pv - 2 * comm
            pnls.append(raw)
        pnls = np.array(pnls)
        s = pnls.mean() / (pnls.std() + 1e-9) * np.sqrt(252)
        sharpes.append(s)

    return {
        "random_sharpe_p50": float(np.percentile(sharpes, 50)),
        "random_sharpe_p95": float(np.percentile(sharpes, 95)),
    }


def trade_shuffle_stress(trades: pd.DataFrame, n_sims: int = MC_SIMS) -> dict:
    """
    Shuffle trade P&L order 1000 times. Check what fraction of paths
    stay profitable and what max drawdown looks like.
    """
    rng   = np.random.default_rng(42)
    pnls  = trades["dollar_pnl"].values.copy()
    max_dds = []
    final_pnls = []
    for _ in range(n_sims):
        shuffled = rng.permutation(pnls)
        cum = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        dd = (peak - cum).max()
        max_dds.append(dd)
        final_pnls.append(cum[-1])

    return {
        "stress_max_dd_p50": float(np.percentile(max_dds, 50)),
        "stress_max_dd_p95": float(np.percentile(max_dds, 95)),
        "stress_pct_profitable": float(np.mean(np.array(final_pnls) > 0)),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _grade(dsr: float, sharpe: float, n_trades: int) -> str:
    if dsr >= 2.0 and sharpe >= 1.5 and n_trades >= 50: return "EXCELLENT"
    if dsr >= 1.5 and sharpe >= 1.0 and n_trades >= 30: return "GOOD"
    if dsr >= 1.0 and n_trades >= 30:                    return "MARGINAL"
    return "FAIL"


def print_report(results: list[dict], bar_minutes: int) -> None:
    survivors = [r for r in results if r.get("grade") in ("EXCELLENT", "GOOD", "MARGINAL")]
    survivors.sort(key=lambda r: r.get("dsr", -999), reverse=True)

    print(f"\n{'='*90}")
    print(f"  L2 TICK STRATEGY RESULTS — {bar_minutes}-MINUTE BARS")
    print(f"  {len(results)} strategies tested | {len(survivors)} survivors (DSR >= {DSR_THRESHOLD})")
    print(f"{'='*90}")
    print(f"  {'Symbol':<6} {'Strategy':<32} {'DSR':>6} {'Sharpe':>7} {'WR%':>6} {'Trades':>7} {'TotPnL':>9} {'Grade'}")
    print(f"  {'-'*85}")

    for r in survivors:
        print(
            f"  {r['symbol']:<6} {r['strategy']:<32} "
            f"{r.get('dsr', 0):>6.2f} {r.get('sharpe', 0):>7.2f} "
            f"{r.get('win_rate', 0)*100:>5.1f}% {r.get('n_oos_trades', 0):>7} "
            f"{r.get('total_pnl', 0):>9,.0f} {r.get('grade','')}"
        )

    print(f"\n  REJECTED ({len(results) - len(survivors)}):")
    rejected = sorted(
        [r for r in results if r.get("grade") == "FAIL"],
        key=lambda r: r.get("dsr", -999), reverse=True
    )
    for r in rejected[:20]:
        reason = r.get("skip", f"DSR={r.get('dsr',0):.2f}")
        print(f"  {r['symbol']:<6} {r['strategy']:<32} {reason}")

    print(f"\n{'='*90}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="L2 tick strategy full analysis")
    parser.add_argument("--symbol",      type=str,  help="Single symbol: GC, SI, ES, NQ")
    parser.add_argument("--strategy",    type=str,  help="Single strategy name")
    parser.add_argument("--strategy-list", type=str, nargs="+", help="Run only these strategies")
    parser.add_argument("--bar-minutes", type=int,  default=5, help="Bar size: 1,3,5,15,30")
    parser.add_argument("--fast",        action="store_true", help="Reduced grid for quick test")
    parser.add_argument("--stop-mult",   type=float, default=1.5, help="Stop ATR multiplier")
    parser.add_argument("--tp-mult",     type=float, default=3.0, help="TP ATR multiplier")
    parser.add_argument("--max-hold",    type=int,   default=50,  help="Max hold bars")
    args = parser.parse_args()

    bar_minutes = args.bar_minutes
    if bar_minutes not in WFO_TRAIN_BARS:
        print(f"ERROR: --bar-minutes must be one of {list(WFO_TRAIN_BARS.keys())}. Got {bar_minutes}")
        sys.exit(1)

    EXIT_PARAMS["stop_atr_mult"] = args.stop_mult
    EXIT_PARAMS["tp_atr_mult"]   = args.tp_mult
    EXIT_PARAMS["max_hold_bars"] = args.max_hold

    symbols = [args.symbol.upper()] if args.symbol else ["GC", "SI", "ES", "NQ"]
    if args.strategy:
        strats = [STRATEGY_MAP[args.strategy]]
    elif args.strategy_list:
        strats = [STRATEGY_MAP[s] for s in args.strategy_list if s in STRATEGY_MAP]
    else:
        strats = STRATEGIES

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    all_results = []
    total = len(symbols) * len(strats)
    done  = 0
    t0    = time.time()

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"  Loading {symbol} {bar_minutes}m bars...")
        df = load_bars(symbol, bar_minutes)
        if df is None:
            print(f"  SKIP: {BAR_DIR / f'{symbol}_bars_{bar_minutes}m.parquet'} not found")
            done += len(strats)
            continue

        mbp_available = has_mbp(df)
        print(f"  {len(df):,} bars  |  MBP features: {mbp_available}")
        print(f"  Range: {df.index[0]}  to  {df.index[-1]}")

        for strat in strats:
            done += 1
            elapsed = time.time() - t0
            eta     = elapsed / done * (total - done) if done > 0 else 0
            print(f"\n  [{done}/{total}]  {symbol} / {strat['name']}"
                  f"  (eta {eta/60:.1f} min)", end="", flush=True)

            row = {"symbol": symbol, "strategy": strat["name"],
                   "bar_minutes": bar_minutes,
                   "description": strat["description"]}

            # Skip mbp strategies if data not available
            if strat["requires_mbp"] and not mbp_available:
                row.update({"grade": "FAIL", "skip": "no MBP data"})
                all_results.append(row)
                print("  [SKIP — no MBP]")
                continue

            # Run WFO
            try:
                wfo = run_wfo(df, strat, symbol, bar_minutes, fast=args.fast)
            except Exception as e:
                row.update({"grade": "FAIL", "skip": str(e)})
                all_results.append(row)
                print(f"  [ERROR: {e}]")
                continue

            if "skip" in wfo:
                row.update({"grade": "FAIL", **wfo})
                all_results.append(row)
                print(f"  [SKIP: {wfo['skip']}]")
                continue

            row.update(wfo)
            dsr    = wfo.get("dsr", 0) or 0
            sharpe = wfo.get("sharpe", 0) or 0
            n_oos  = wfo.get("n_oos_trades", 0) or 0
            wr     = wfo.get("win_rate", 0) or 0

            grade = _grade(dsr, sharpe, n_oos)
            row["grade"] = grade

            # Stress test survivors using OOS trades already collected in WFO
            if grade in ("EXCELLENT", "GOOD", "MARGINAL"):
                try:
                    oos_trades = wfo.get("_oos_trades_df")
                    if oos_trades is not None and len(oos_trades) >= 10:
                        stress   = trade_shuffle_stress(oos_trades)
                        baseline = random_entry_baseline(df, symbol, len(oos_trades))
                        row.update(stress)
                        row.update(baseline)
                        row["sharpe_vs_random"] = sharpe - baseline["random_sharpe_p95"]
                except Exception:
                    pass

            print(f"  DSR={dsr:.2f}  Sharpe={sharpe:.2f}  Trades={n_oos}  [{grade}]")
            all_results.append(row)

    # ── Save results ────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    json_path = RESULT_DIR / f"tick_results_{bar_minutes}m_{ts}.json"
    csv_path  = RESULT_DIR / f"tick_results_{bar_minutes}m_{ts}.csv"

    # Strip internal DataFrames before serialisation
    for r in all_results:
        r.pop("_oos_trades_df", None)

    # JSON: serialise carefully (convert numpy types)
    def _serial(obj):
        if isinstance(obj, (np.integer,)):  return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.bool_,)):    return bool(obj)
        if isinstance(obj, (np.ndarray,)):  return obj.tolist()
        if pd.isna(obj):                    return None
        raise TypeError(f"Not serializable: {type(obj)}")

    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=_serial)

    # CSV: flat summary
    rows_flat = []
    for r in all_results:
        flat = {k: v for k, v in r.items()
                if not isinstance(v, (dict, list))}
        rows_flat.append(flat)
    pd.DataFrame(rows_flat).to_csv(csv_path, index=False)

    print_report(all_results, bar_minutes)

    survivors = [r for r in all_results if r.get("grade") in ("EXCELLENT", "GOOD", "MARGINAL")]
    print(f"\n  Results saved:")
    print(f"    {json_path}")
    print(f"    {csv_path}")
    print(f"\n  Survivors: {len(survivors)} / {len(all_results)}")

    # Summary by grade
    from collections import Counter
    grade_counts = Counter(r.get("grade", "FAIL") for r in all_results)
    for g in ("EXCELLENT", "GOOD", "MARGINAL", "FAIL"):
        print(f"    {g:<10}: {grade_counts.get(g, 0)}")

    elapsed_min = (time.time() - t0) / 60
    print(f"\n  Total runtime: {elapsed_min:.1f} minutes")


if __name__ == "__main__":
    main()
