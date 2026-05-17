"""
tick_v5_stress.py -- Full stress test: key_level_cvd_rejection (V5)
====================================================================
Phase 1  Grid search -- 27 param combos x all symbol/timeframe combos.
         Metric: R-multiple Sharpe at 0.5t slippage (need >= 15 trades).
Phase 2  Full stress test on top Phase-1 survivors (GC/SI only, 6 yrs data).
         Same criteria as v1-v4: slippage sweep, annual regimes, Topstep.
         ES/NQ data only covers Dec 2025-May 2026 -- reported separately as
         "short-window validation" (not eligible for regime pass/fail).

Pass criteria (same as all prior survivors):
  1t-Sharpe >= 1.0  AND  >=70% years positive  AND  Topstep compliance >= 95%
  ES/NQ only: 1t-Sharpe >= 1.0 AND Topstep >= 95% (regime check skipped).

Usage:
  python tick_v5_stress.py              # full run
  python tick_v5_stress.py --quick      # Phase 1 only (grid search summary)
"""

import argparse, itertools, json, sys, warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v5  import STRAT_MAP_V5

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
STOP_MULT = 1.5
TP_MULT   = 3.0
TS_LIMIT  = 4500   # full-contract Topstep daily loss limit

PV = {"GC": 100.0, "SI": 5000.0, "ES": 50.0, "NQ": 20.0}

# Symbol/timeframe combos to test -- skip 1m for key-level (too noisy)
COMBOS_LONG_HISTORY  = [
    ("GC",  3), ("GC",  5), ("GC", 15), ("GC", 30),
    ("SI",  3), ("SI",  5), ("SI", 15), ("SI", 30),
]
COMBOS_SHORT_HISTORY = [
    ("ES",  3), ("ES",  5), ("ES", 15), ("ES", 30),
    ("NQ",  3), ("NQ",  5), ("NQ", 15), ("NQ", 30),
]
ALL_COMBOS = COMBOS_LONG_HISTORY + COMBOS_SHORT_HISTORY

PARAM_GRID  = STRAT_MAP_V5["key_level_cvd_rejection"]["param_grid"]
ALL_PARAMS  = [
    dict(zip(PARAM_GRID.keys(), combo))
    for combo in itertools.product(*PARAM_GRID.values())
]


# ── Data loading ─────────────────────────────────────────────────────────────

def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


# ── Core metrics ─────────────────────────────────────────────────────────────

def r_sharpe(tr: pd.DataFrame, symbol: str) -> float:
    """Annualised Sharpe of R-multiples (same metric used for all survivors)."""
    if tr.empty or len(tr) < 5:
        return 0.0
    pv = PV.get(symbol, 50.0)
    r  = tr["dollar_pnl"] / (pv * STOP_MULT)
    return float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0


# ── Phase 1: grid search ─────────────────────────────────────────────────────

def phase1_best(df: pd.DataFrame, fn, symbol: str, min_trades: int = 15):
    """Return the best (sharpe, n, wr, params) across all param combos."""
    best = (0.0, 0, 0.0, None)
    for params in ALL_PARAMS:
        try:
            sig = fn(df, **params)
            tr  = run_backtest_slippage(df, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=0.5)
        except Exception:
            continue
        if tr.empty or len(tr) < min_trades:
            continue
        sr = r_sharpe(tr, symbol)
        n  = len(tr)
        wr = (tr["dollar_pnl"] > 0).mean()
        if sr > best[0]:
            best = (sr, n, wr, params)
    return best


# ── Phase 2: full stress test ─────────────────────────────────────────────────

def slippage_sweep(df: pd.DataFrame, fn, params: dict, symbol: str) -> dict:
    results = {}
    for ticks in [0, 0.5, 1.0, 2.0]:
        try:
            sig = fn(df, **params)
            tr  = run_backtest_slippage(df, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=ticks)
        except Exception:
            results[ticks] = None
            continue
        if tr.empty or len(tr) < 5:
            results[ticks] = None
            continue
        results[ticks] = {
            "sharpe": r_sharpe(tr, symbol),
            "wr":     float((tr["dollar_pnl"] > 0).mean()),
            "n":      len(tr),
            "total":  float(tr["dollar_pnl"].sum()),
        }
    return results


def annual_regimes(df: pd.DataFrame, fn, params: dict, symbol: str) -> dict:
    reg = {}
    for yr in sorted(df.index.year.unique()):
        sub = df[df.index.year == yr]
        if len(sub) < 200:
            continue
        try:
            sig = fn(sub, **params)
            tr  = run_backtest_slippage(sub, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=0.5)
        except Exception:
            reg[yr] = None
            continue
        reg[yr] = float(tr["dollar_pnl"].sum()) if not tr.empty and len(tr) >= 3 else None
    return reg


def topstep_check(df: pd.DataFrame, fn, params: dict, symbol: str):
    try:
        sig = fn(df, **params)
        tr  = run_backtest_slippage(df, sig, symbol,
                                    stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                    extra_ticks=0.5)
    except Exception:
        return 0.0, 0.0
    if tr.empty or "entry_time" not in tr.columns:
        return 0.0, 0.0
    tr["date"]  = pd.to_datetime(tr["entry_time"]).dt.date
    daily       = tr.groupby("date")["dollar_pnl"].sum()
    return float((daily >= -TS_LIMIT).mean()), float(daily.min())


def full_stress(df: pd.DataFrame, fn, params: dict, symbol: str,
                has_regime_data: bool = True):
    slip                  = slippage_sweep(df, fn, params, symbol)
    reg                   = annual_regimes(df, fn, params, symbol) if has_regime_data else {}
    ts_pct, worst_day     = topstep_check(df, fn, params, symbol)

    sr1t = (slip.get(1.0) or {}).get("sharpe", 0.0)
    pos  = sum(1 for v in reg.values() if v and v > 0)
    tot  = len([v for v in reg.values() if v is not None])

    if has_regime_data:
        ok = sr1t >= 1.0 and (pos / max(tot, 1)) >= 0.70 and ts_pct >= 0.95
    else:
        ok = sr1t >= 1.0 and ts_pct >= 0.95  # regime check skipped for short history

    return {
        "slip": slip, "regimes": reg, "ts_pct": ts_pct, "worst_day": worst_day,
        "sr1t": sr1t, "pos_years": pos, "tot_years": tot, "ok": ok,
    }


# ── Reporting helpers ─────────────────────────────────────────────────────────

def print_slip_table(slip: dict):
    print(f"  {'Slip':>5} | {'Sharpe':>7} | {'WR%':>5} | {'Trades':>6} | Total P&L")
    print(f"  {'-----':>5}-+-{'-------':>7}-+-{'-----':>5}-+-{'------':>6}-+----------")
    for t, r in slip.items():
        if r is None:
            print(f"  {t:.1f}t    |  INSUFF |       |        |")
        else:
            print(f"  {t:.1f}t    | {r['sharpe']:>7.2f} | {r['wr']*100:>5.1f}% | {r['n']:>6} | ${r['total']:>+10,.0f}")


def print_regimes(reg: dict):
    if not reg:
        print("  (no regime data -- short history)")
        return
    pos = sum(1 for v in reg.values() if v and v > 0)
    tot = len([v for v in reg.values() if v is not None])
    print(f"  Annual regimes: {pos}/{tot} years positive")
    for yr, pnl in sorted(reg.items()):
        if pnl is None:
            print(f"    {yr}: no trades")
        else:
            flag = "+" if pnl > 0 else "-"
            print(f"    {yr}: {flag} ${pnl:>+10,.0f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(quick: bool = False):
    fn      = STRAT_MAP_V5["key_level_cvd_rejection"]["compute"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n{'='*72}")
    print(f"  V5 FULL STRESS TEST -- key_level_cvd_rejection")
    print(f"  {now_str}")
    print(f"  {len(ALL_COMBOS)} symbol/timeframe combos  x  {len(ALL_PARAMS)} param combos")
    print(f"  Pass: 1t-Sharpe >= 1.0 | >=70% years positive | Topstep >= 95%")
    print(f"  ES/NQ: regime check skipped (only Dec 2025-May 2026 data)")
    print(f"{'='*72}")

    # ── Phase 1: grid search ─────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  PHASE 1 -- Grid search (best params per symbol/timeframe)")
    print(f"{'='*72}\n")

    phase1_results = {}
    for symbol, bar_min in ALL_COMBOS:
        df = load_bars(symbol, bar_min)
        if df is None:
            print(f"  SKIP {symbol}/{bar_min}m -- no data")
            continue
        has_long = (symbol, bar_min) in COMBOS_LONG_HISTORY
        hist_tag  = f"{df.index[0].date()} -> {df.index[-1].date()} ({df.index.year.nunique()} yrs)"

        print(f"  {symbol}/{bar_min}m  [{hist_tag}]", end="", flush=True)
        sr, n, wr, best_params = phase1_best(df, fn, symbol)
        if best_params is None:
            print("  -- no viable combos (< 15 trades with any params)")
        else:
            short_flag = "" if has_long else "  [SHORT HISTORY]"
            print(f"  -> Sharpe={sr:.2f}  n={n}  WR={wr:.0%}  {best_params}{short_flag}")
            phase1_results[(symbol, bar_min)] = (sr, n, wr, best_params, has_long)

    if quick:
        print(f"\n  --quick flag set: stopping after Phase 1.")
        return

    # Rank candidates: long-history first (eligible for full pass), then short
    long_candidates  = sorted(
        [(sr, sym, bm, p, True)  for (sym, bm), (sr, n, wr, p, hl) in phase1_results.items() if hl and sr >= 0.5],
        key=lambda x: -x[0]
    )
    short_candidates = sorted(
        [(sr, sym, bm, p, False) for (sym, bm), (sr, n, wr, p, hl) in phase1_results.items() if not hl and sr >= 0.5],
        key=lambda x: -x[0]
    )
    stress_queue = (long_candidates[:6] + short_candidates[:4])  # top 6 long + 4 short

    # ── Phase 2: full stress test ────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  PHASE 2 -- Full stress test ({len(stress_queue)} candidates)")
    print(f"{'='*72}")

    passed = []
    for _, symbol, bar_min, params, has_long in stress_queue:
        df = load_bars(symbol, bar_min)
        label = f"{symbol}/key_level_cvd_rejection/{bar_min}m"
        print(f"\n  {label}")
        print(f"  Params: {params}")
        if not has_long:
            print(f"  NOTE: short data window (Dec 2025-May 2026) -- regime check skipped")
        print(f"  {'-'*66}")

        res = full_stress(df, fn, params, symbol, has_regime_data=has_long)

        print_slip_table(res["slip"])
        print()
        print_regimes(res["regimes"])
        print()
        print(f"  Topstep: {res['ts_pct']*100:.1f}%   Worst day: ${res['worst_day']:,.0f}")
        regime_str = f"{res['pos_years']}/{res['tot_years']} yrs" if has_long else "N/A (short)"
        print(f"\n  {'PASS' if res['ok'] else 'FAIL'}  "
              f"(1t-Sharpe={res['sr1t']:.2f}  Regimes={regime_str}  "
              f"TS={res['ts_pct']*100:.0f}%)")

        if res["ok"]:
            passed.append({
                "label":    label,
                "symbol":   symbol,
                "bar_min":  bar_min,
                "strategy": "key_level_cvd_rejection",
                "params":   params,
                "sr1t":     round(res["sr1t"], 3),
                "regimes":  regime_str,
                "ts_pct":   round(res["ts_pct"], 4),
                "worst_day": round(res["worst_day"], 0),
                "long_history": has_long,
            })

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  V5 FINAL RESULTS: {len(passed)} PASSED")
    print(f"{'='*72}")

    if passed:
        full_pass   = [r for r in passed if r["long_history"]]
        short_pass  = [r for r in passed if not r["long_history"]]

        if full_pass:
            print(f"\n  FULL PASS (multi-year data -- eligible for live portfolio):")
            for r in full_pass:
                print(f"    PASS: {r['label']}")
                print(f"          1t-Sharpe={r['sr1t']:.2f}  "
                      f"Regimes={r['regimes']}  "
                      f"TS={r['ts_pct']*100:.0f}%  "
                      f"Worst=${r['worst_day']:,.0f}")
                print(f"          Params: {r['params']}")

        if short_pass:
            print(f"\n  SHORT-WINDOW PASS (ES/NQ, 5-month data -- needs more history before live):")
            for r in short_pass:
                print(f"    PASS*: {r['label']}")
                print(f"           1t-Sharpe={r['sr1t']:.2f}  "
                      f"TS={r['ts_pct']*100:.0f}%  "
                      f"Worst=${r['worst_day']:,.0f}")
                print(f"           Params: {r['params']}")

        out = ROOT / "02_analysis" / "v5_stress_results.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(passed, f, indent=2)
        print(f"\n  Results saved -> {out}")
    else:
        print(f"\n  No candidates passed the stress test.")
        print(f"  key_level_cvd_rejection is NOT ready for the live portfolio.")

    print()
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="Phase 1 only (grid search, no full stress test)")
    args = parser.parse_args()
    main(quick=args.quick)
