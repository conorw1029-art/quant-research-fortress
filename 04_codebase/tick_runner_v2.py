#!/usr/bin/env python3
"""
L2 Tick Strategy v2 Runner
===========================
Runs all 8 new strategies (tick_strategies_v2.py) across all symbols × bar
sizes with walk-forward optimisation.  Same WFO/DSR logic as tick_full_run.py.

Usage:
  python tick_runner_v2.py                        # all symbols, all bar sizes
  python tick_runner_v2.py --bar-minutes 5        # single bar size
  python tick_runner_v2.py --symbol GC            # single symbol
  python tick_runner_v2.py --fast                 # reduced grid, quick test

Results saved to:  05_backtests/tick_results_v2_<bar>m_<ts>.json
"""

import argparse
import itertools
import json
import sys
import time
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import run_backtest, compute_metrics, SPECS
from tick_strategies_v2 import STRATEGIES_V2

BAR_DIR    = Path(__file__).parent.parent / "01_data" / "tick_bars"
RESULT_DIR = Path(__file__).parent.parent / "05_backtests"

DSR_THRESHOLD   = 1.0
MIN_TRADES_OOS  = 30
MC_SIMS         = 500
MAX_GRID_COMBOS = 12

WFO_TRAIN_BARS = {1: 8_000, 3: 5_000, 5: 5_000, 15: 2_000, 30: 1_000}
WFO_TEST_BARS  = {1: 8_000, 3: 5_000, 5: 5_000, 15: 2_000, 30: 1_000}

EXIT_PARAMS = {
    "stop_atr_mult": 1.5,
    "tp_atr_mult":   3.0,
    "max_hold_bars": 50,
}

ALL_BAR_SIZES = [1, 3, 5, 15, 30]


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_bars(symbol: str, bar_minutes: int) -> pd.DataFrame | None:
    path = BAR_DIR / f"{symbol}_bars_{bar_minutes}m.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def has_mbp(df: pd.DataFrame) -> bool:
    return "obi_5" in df.columns and df["obi_5"].notna().any()


def expand_grid(param_grid: dict, fast: bool = False) -> list[dict]:
    if fast:
        reduced = {k: [v[len(v) // 2]] for k, v in param_grid.items()}
    else:
        reduced = param_grid
    keys   = list(reduced.keys())
    values = list(reduced.values())
    all_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    if len(all_combos) > MAX_GRID_COMBOS and not fast:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(all_combos), size=MAX_GRID_COMBOS, replace=False)
        all_combos = [all_combos[i] for i in idx]
    return all_combos


# ── WFO ──────────────────────────────────────────────────────────────────────

def run_wfo(df: pd.DataFrame, strategy: dict, symbol: str,
            bar_minutes: int, fast: bool = False) -> dict:
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

        try:
            sig_oos = fn(test_df, **best_params)
        except Exception:
            fold_start += test_n
            continue

        oos_trades = run_backtest(test_df, sig_oos, symbol, **EXIT_PARAMS)
        if not oos_trades.empty:
            all_oos_trades.append(oos_trades)
            fold_m = compute_metrics(oos_trades, n_params)
            fold_results.append({
                "fold": fold,
                "best_params": best_params,
                "is_sharpe": best_sharpe,
                **{f"oos_{k}": v for k, v in fold_m.items()},
            })

        fold_start += test_n

    if not all_oos_trades:
        return {"skip": "no OOS trades"}

    combined    = pd.concat(all_oos_trades, ignore_index=True)
    agg_metrics = compute_metrics(combined, n_params)

    param_keys = list(strategy["param_grid"].keys())
    stability  = {}
    if fold_results and param_keys:
        for k in param_keys:
            vals = [f["best_params"].get(k) for f in fold_results if "best_params" in f]
            if vals:
                most_common = Counter(vals).most_common(1)[0]
                stability[k] = most_common[1] / len(vals)

    return {
        "n_folds":         fold,
        "n_oos_trades":    len(combined),
        "param_stability": stability,
        "fold_results":    fold_results,
        "_oos_trades_df":  combined,
        **agg_metrics,
    }


# ── Stress tests ─────────────────────────────────────────────────────────────

def trade_shuffle_stress(trades: pd.DataFrame, n_sims: int = MC_SIMS) -> dict:
    rng  = np.random.default_rng(42)
    pnls = trades["dollar_pnl"].values.copy()
    max_dds    = []
    final_pnls = []
    for _ in range(n_sims):
        shuffled = rng.permutation(pnls)
        cum  = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        max_dds.append((peak - cum).max())
        final_pnls.append(cum[-1])
    return {
        "stress_max_dd_p50":    float(np.percentile(max_dds, 50)),
        "stress_max_dd_p95":    float(np.percentile(max_dds, 95)),
        "stress_pct_profitable": float(np.mean(np.array(final_pnls) > 0)),
    }


def random_entry_baseline(df: pd.DataFrame, symbol: str,
                           n_trades: int, n_sims: int = MC_SIMS) -> dict:
    rng  = np.random.default_rng(42)
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
        pnls    = np.array([d * (cl[e + hold] - cl[e]) * pv - 2 * comm
                            for e, d in zip(entries, dirs)])
        sharpes.append(pnls.mean() / (pnls.std() + 1e-9) * np.sqrt(252))
    return {
        "random_sharpe_p50": float(np.percentile(sharpes, 50)),
        "random_sharpe_p95": float(np.percentile(sharpes, 95)),
    }


# ── Grading ───────────────────────────────────────────────────────────────────

def _grade(dsr: float, sharpe: float, n_trades: int) -> str:
    if dsr >= 2.0 and sharpe >= 1.5 and n_trades >= 50: return "EXCELLENT"
    if dsr >= 1.5 and sharpe >= 1.0 and n_trades >= 30: return "GOOD"
    if dsr >= 1.0 and n_trades >= 30:                    return "MARGINAL"
    return "FAIL"


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(results: list[dict], bar_minutes: int) -> None:
    survivors = [r for r in results if r.get("grade") in ("EXCELLENT", "GOOD", "MARGINAL")]
    survivors.sort(key=lambda r: r.get("dsr", -999), reverse=True)
    print(f"\n{'='*90}")
    print(f"  L2 v2 RESULTS — {bar_minutes}-MINUTE BARS  |  "
          f"{len(results)} tested  |  {len(survivors)} survivors")
    print(f"{'='*90}")
    print(f"  {'Symbol':<6} {'Strategy':<32} {'DSR':>6} {'Sharpe':>7} {'WR%':>6} {'Trades':>7} {'TotPnL':>9} Grade")
    print(f"  {'-'*85}")
    for r in survivors:
        print(f"  {r['symbol']:<6} {r['strategy']:<32} "
              f"{r.get('dsr',0):>6.2f} {r.get('sharpe',0):>7.2f} "
              f"{r.get('win_rate',0)*100:>5.1f}% {r.get('n_oos_trades',0):>7} "
              f"{r.get('total_pnl',0):>9,.0f} {r.get('grade','')}")
    print(f"\n  FAILED ({len(results)-len(survivors)}):")
    for r in sorted([r for r in results if r.get("grade")=="FAIL"],
                    key=lambda r: r.get("dsr", -999), reverse=True)[:20]:
        skip_str = r.get("skip") or f"DSR={r.get('dsr',0):.2f}"
        print(f"  {r['symbol']:<6} {r['strategy']:<32} {skip_str}")
    print(f"\n{'='*90}")


# ── Save ──────────────────────────────────────────────────────────────────────

def save_results(all_results: list[dict], bar_minutes: int) -> tuple[Path, Path]:
    ts        = datetime.now().strftime("%Y%m%d_%H%M")
    json_path = RESULT_DIR / f"tick_results_v2_{bar_minutes}m_{ts}.json"
    csv_path  = RESULT_DIR / f"tick_results_v2_{bar_minutes}m_{ts}.csv"

    def _serial(obj):
        if isinstance(obj, (np.integer,)):  return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.bool_,)):    return bool(obj)
        if isinstance(obj, (np.ndarray,)):  return obj.tolist()
        if pd.isna(obj):                    return None
        raise TypeError(f"Not serializable: {type(obj)}")

    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=_serial)

    rows_flat = [{k: v for k, v in r.items() if not isinstance(v, (dict, list))}
                 for r in all_results]
    pd.DataFrame(rows_flat).to_csv(csv_path, index=False)
    return json_path, csv_path


# ── Main ─────────────────────────────────────────────────────────────────────

def run_bar_size(bar_minutes: int, symbols: list[str],
                 strats: list[dict], fast: bool) -> list[dict]:
    """Run all symbol × strategy combos for a given bar size."""
    all_results = []
    total = len(symbols) * len(strats)
    done  = 0
    t0    = time.time()

    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"  Loading {symbol} {bar_minutes}m bars...")
        df = load_bars(symbol, bar_minutes)
        if df is None:
            print(f"  SKIP: no file found")
            done += len(strats)
            continue

        mbp_available = has_mbp(df)
        print(f"  {len(df):,} bars  |  MBP: {mbp_available}  |  "
              f"{df.index[0]}  →  {df.index[-1]}")

        for strat in strats:
            done += 1
            elapsed = time.time() - t0
            eta = elapsed / done * (total - done) if done else 0
            print(f"\n  [{done}/{total}]  {symbol}/{strat['name']}"
                  f"  (eta {eta/60:.1f}m)", end="", flush=True)

            row = {
                "symbol": symbol, "strategy": strat["name"],
                "bar_minutes": bar_minutes,
                "description": strat["description"],
            }

            if strat["requires_mbp"] and not mbp_available:
                row.update({"grade": "FAIL", "skip": "no MBP data"})
                all_results.append(row)
                print("  [SKIP — no MBP]")
                continue

            try:
                wfo = run_wfo(df, strat, symbol, bar_minutes, fast=fast)
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
            grade  = _grade(dsr, sharpe, n_oos)
            row["grade"] = grade

            if grade in ("EXCELLENT", "GOOD", "MARGINAL"):
                try:
                    oos_trades = wfo.get("_oos_trades_df")
                    if oos_trades is not None and len(oos_trades) >= 10:
                        row.update(trade_shuffle_stress(oos_trades))
                        base = random_entry_baseline(df, symbol, len(oos_trades))
                        row.update(base)
                        row["sharpe_vs_random"] = sharpe - base["random_sharpe_p95"]
                except Exception:
                    pass

            print(f"  DSR={dsr:.2f}  Sharpe={sharpe:.2f}  Trades={n_oos}  [{grade}]")
            row.pop("_oos_trades_df", None)
            all_results.append(row)

    return all_results


def main():
    parser = argparse.ArgumentParser(description="L2 v2 strategy runner")
    parser.add_argument("--symbol",      type=str,  help="GC / SI / ES / NQ")
    parser.add_argument("--bar-minutes", type=int,  help="Single bar size (1/3/5/15/30)")
    parser.add_argument("--fast",        action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    symbols = [args.symbol.upper()] if args.symbol else ["GC", "SI", "ES", "NQ"]
    bar_sizes = [args.bar_minutes] if args.bar_minutes else ALL_BAR_SIZES
    strats  = STRATEGIES_V2

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    grand_total_start = time.time()
    all_survivors = []

    print(f"\n{'='*70}")
    print(f"  L2 TICK STRATEGIES V2 — FULL RUN")
    print(f"  Strategies: {len(strats)}  |  Symbols: {symbols}  |  Bar sizes: {bar_sizes}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    for bar_minutes in bar_sizes:
        if bar_minutes not in WFO_TRAIN_BARS:
            print(f"  SKIP unsupported bar size {bar_minutes}")
            continue

        print(f"\n\n{'#'*70}")
        print(f"  BAR SIZE: {bar_minutes} minutes")
        print(f"{'#'*70}")

        results = run_bar_size(bar_minutes, symbols, strats, args.fast)

        # Strip DataFrames before save
        for r in results:
            r.pop("_oos_trades_df", None)

        json_path, csv_path = save_results(results, bar_minutes)
        print_report(results, bar_minutes)
        print(f"\n  Saved: {json_path}")
        print(f"  Saved: {csv_path}")

        survivors = [r for r in results if r.get("grade") in ("EXCELLENT", "GOOD", "MARGINAL")]
        all_survivors.extend(survivors)

    # Final cross-bar summary
    total_min = (time.time() - grand_total_start) / 60
    print(f"\n\n{'='*70}")
    print(f"  V2 RUN COMPLETE — {total_min:.1f} min total")
    print(f"  Total survivors across all bar sizes: {len(all_survivors)}")
    print(f"\n  TOP 15 by DSR:")
    all_survivors.sort(key=lambda r: r.get("dsr", 0), reverse=True)
    print(f"  {'Symbol':<6} {'Strategy':<32} {'Bar':>4} {'DSR':>6} {'Sharpe':>7} {'Trades':>7} Grade")
    print(f"  {'-'*72}")
    for r in all_survivors[:15]:
        print(f"  {r['symbol']:<6} {r['strategy']:<32} "
              f"{r['bar_minutes']:>4}m "
              f"{r.get('dsr',0):>6.2f} {r.get('sharpe',0):>7.2f} "
              f"{r.get('n_oos_trades',0):>7} {r.get('grade','')}")
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
