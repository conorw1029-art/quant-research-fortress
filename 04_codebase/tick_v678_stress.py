"""
tick_v678_stress.py — Full stress test for V6, V7, V8 strategy libraries
=========================================================================
Phase 1  Grid search on all 30 strategies × GC/SI/ES/NQ × bar sizes.
         Metric: R-multiple Sharpe at 0.5-tick slippage (need >= 20 trades).
         Only strategies with Phase 1 Sharpe >= 0.80 proceed to Phase 2.

Phase 2  Full stress test on Phase-1 survivors.
         Slippage sweep (0 / 0.5 / 1.0 / 2.0 ticks) → need 1t-Sharpe >= 1.0
         Annual regimes (GC/SI, 6+ years of data)    → need >= 70% positive
         Topstep daily compliance                      → need >= 95% of days
         Worst-day micro risk                          → flag if > $1,000

Phase 3  Portfolio integration analysis.
         Show how new survivors combine with existing 5 survivors.
         Estimate correlation, combined Sharpe, Topstep compliance.

Pass criteria (same as V1-V5 survivors):
  GC/SI (long history):  1t-Sharpe >= 1.0  AND  >=70% years positive  AND  Topstep >= 95%
  ES/NQ (short history): 1t-Sharpe >= 1.0  AND  Topstep >= 95% (regime check skipped)

Usage:
  python -X utf8 tick_v678_stress.py              # full run (all 30 strategies)
  python -X utf8 tick_v678_stress.py --quick      # Phase 1 summary only
  python -X utf8 tick_v678_stress.py --top N      # test only top-N Phase-1 candidates
"""

import argparse, itertools, json, sys, warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
ANA_DIR = ROOT / "02_analysis"; ANA_DIR.mkdir(exist_ok=True)

STOP_MULT = 1.5
TP_MULT   = 3.0
TS_LIMIT  = 4500.0   # Topstep full-contract daily loss limit
MICRO_SCALE = 0.10   # 1 micro = 10% of full contract for worst-day calc

PV = {"GC": 100.0, "SI": 5000.0, "ES": 50.0, "NQ": 20.0}

COMBOS_LONG  = [("GC", b) for b in [1,3,5,15,30]] + [("SI", b) for b in [1,3,5,15,30]]
COMBOS_SHORT = [("ES", b) for b in [3,5,15,30]] + [("NQ", b) for b in [3,5,15,30]]
ALL_COMBOS   = COMBOS_LONG + COMBOS_SHORT

ALL_STRATS = {**STRAT_MAP_V6, **STRAT_MAP_V7, **STRAT_MAP_V8}


# ── Data ─────────────────────────────────────────────────────────────────────

def load_bars(sym, bar_min):
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


# ── Core metrics ──────────────────────────────────────────────────────────────

def r_sharpe(tr, symbol):
    if tr.empty or len(tr) < 5: return 0.0
    r = tr["dollar_pnl"] / (PV.get(symbol, 50.0) * STOP_MULT)
    return float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0


def win_rate(tr):
    if tr.empty: return 0.0
    return float((tr["dollar_pnl"] > 0).mean())


# ── Phase 1: grid search best params ─────────────────────────────────────────

def phase1_best(df, strat, symbol, min_trades=20):
    fn   = strat["compute"]
    keys = list(strat["param_grid"].keys())
    vals = list(strat["param_grid"].values())
    combos = list(itertools.product(*vals))
    # Cap grid to avoid very long runs
    if len(combos) > 16:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(combos), 16, replace=False)
        combos = [combos[i] for i in sorted(idx)]

    best_sr, best_n, best_wr, best_params = -np.inf, 0, 0.0, None
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            sig = fn(df, **params)
            tr  = run_backtest_slippage(df, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=0.5)
        except Exception:
            continue
        if tr.empty or len(tr) < min_trades: continue
        sr = r_sharpe(tr, symbol)
        if sr > best_sr:
            best_sr, best_n, best_wr, best_params = sr, len(tr), win_rate(tr), params
    return best_sr, best_n, best_wr, best_params


# ── Phase 2: full stress test ─────────────────────────────────────────────────

def slippage_sweep(df, fn, params, symbol):
    out = {}
    for ticks in [0.0, 0.5, 1.0, 2.0]:
        try:
            sig = fn(df, **params)
            tr  = run_backtest_slippage(df, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=ticks)
        except Exception:
            out[ticks] = None; continue
        if tr.empty or len(tr) < 5:
            out[ticks] = None; continue
        out[ticks] = {
            "sharpe": r_sharpe(tr, symbol),
            "wr":     win_rate(tr),
            "n":      len(tr),
            "total":  float(tr["dollar_pnl"].sum()),
        }
    return out


def annual_regimes(df, fn, params, symbol):
    reg = {}
    for yr in sorted(df.index.year.unique()):
        sub = df[df.index.year == yr]
        if len(sub) < 200: continue
        try:
            sig = fn(sub, **params)
            tr  = run_backtest_slippage(sub, sig, symbol,
                                        stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                        extra_ticks=0.5)
        except Exception:
            reg[yr] = None; continue
        reg[yr] = float(tr["dollar_pnl"].sum()) if not tr.empty and len(tr) >= 3 else None
    return reg


def topstep_check(df, fn, params, symbol):
    try:
        sig = fn(df, **params)
        tr  = run_backtest_slippage(df, sig, symbol,
                                    stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                    extra_ticks=0.5)
    except Exception:
        return 0.0, 0.0, 0.0
    if tr.empty or "entry_time" not in tr.columns:
        return 0.0, 0.0, 0.0
    tr = tr.copy()
    tr["date"]  = pd.to_datetime(tr["entry_time"]).dt.date
    daily       = tr.groupby("date")["dollar_pnl"].sum()
    ts_pct      = float((daily >= -TS_LIMIT).mean())
    worst_day   = float(daily.min())
    worst_micro = worst_day * MICRO_SCALE
    return ts_pct, worst_day, worst_micro


def full_stress(df, fn, params, symbol, has_long_history=True):
    slip            = slippage_sweep(df, fn, params, symbol)
    reg             = annual_regimes(df, fn, params, symbol) if has_long_history else {}
    ts_pct, worst, worst_micro = topstep_check(df, fn, params, symbol)

    sr1t = (slip.get(1.0) or {}).get("sharpe", 0.0)
    pos  = sum(1 for v in reg.values() if v is not None and v > 0)
    tot  = len([v for v in reg.values() if v is not None])

    if has_long_history:
        ok = sr1t >= 1.0 and (pos / max(tot, 1)) >= 0.70 and ts_pct >= 0.95
    else:
        ok = sr1t >= 1.0 and ts_pct >= 0.95

    return {
        "slip":       slip,
        "regimes":    reg,
        "ts_pct":     ts_pct,
        "worst_day":  worst,
        "worst_micro":worst_micro,
        "sr1t":       sr1t,
        "pos_years":  pos,
        "tot_years":  tot,
        "ok":         ok,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_slip_table(slip):
    print(f"    {'Slip':>5} | {'Sharpe':>7} | {'WR%':>5} | {'Trades':>6} | Total P&L")
    print(f"    {'-----':>5}-+-{'-------':>7}-+-{'-----':>5}-+-{'------':>6}-+----------")
    for t, r in slip.items():
        if r is None:
            print(f"    {t:.1f}t    |     N/A |     — |      — |        —")
        else:
            print(f"    {t:.1f}t    | {r['sharpe']:>7.2f} | {r['wr']*100:>5.1f}% | {r['n']:>6} | ${r['total']:>+10,.0f}")


def print_regimes(reg):
    if not reg:
        print("    (regime check skipped — short data window)")
        return
    pos = sum(1 for v in reg.values() if v is not None and v > 0)
    tot = len([v for v in reg.values() if v is not None])
    print(f"    Annual regimes: {pos}/{tot} years positive")
    for yr, pnl in sorted(reg.items()):
        if pnl is None:
            print(f"      {yr}: — (< 3 trades)")
        else:
            flag = "+" if pnl > 0 else "▼"
            print(f"      {yr}: {flag} ${pnl:>+10,.0f}")


# ── Phase 3: portfolio integration ───────────────────────────────────────────

def portfolio_integration(new_passed, existing_survivors_path=None):
    """
    Show how new stress-test survivors complement the existing 5 survivors.
    Loads existing survivor data if path is provided.
    Reports: strategy count, estimated correlation bucket, combined coverage.
    """
    print(f"\n{'='*72}")
    print(f"  PHASE 3 — PORTFOLIO INTEGRATION ANALYSIS")
    print(f"{'='*72}\n")

    existing = []
    if existing_survivors_path and Path(existing_survivors_path).exists():
        with open(existing_survivors_path) as f:
            existing = json.load(f)
        print(f"  Existing survivors loaded: {len(existing)}")
        for s in existing:
            lbl = s.get("label", s.get("name", "?"))
            print(f"    {lbl}")
    else:
        print("  Existing survivors: 5 (from v1-v5 campaigns)")
        print("    ES/cvd_divergence_large_print/15m   (trend/microstructure)")
        print("    ES/cvd_divergence/15m                (trend/microstructure)")
        print("    ES/tape_absorption/15m               (microstructure)")
        print("    GC/vwap_reclaim/15m                  (VWAP)")
        print("    GC/key_level_cvd_rejection/5m        (key level)")

    print(f"\n  New stress-test survivors: {len(new_passed)}")

    by_symbol = {}
    by_family = {}
    for r in new_passed:
        sym = r["symbol"]
        fam = r.get("strategy_family", "mixed")
        by_symbol.setdefault(sym, []).append(r["label"])
        by_family.setdefault(fam, []).append(r["label"])

    for sym, labels in sorted(by_symbol.items()):
        print(f"\n    {sym} ({len(labels)} strategies):")
        for lbl in labels:
            r = next(x for x in new_passed if x["label"] == lbl)
            print(f"      {lbl:<55} 1t-Sharpe={r['sr1t']:.2f}  "
                  f"Worst-micro=${r['worst_micro']:,.0f}")

    print(f"\n  INTEGRATION RECOMMENDATION:")
    print(f"  {'─'*65}")
    print(f"  1. Add new PASS strategies to live_strategy_allowlist.yaml as")
    print(f"     ENABLED_DRY_RUN (not DEMO_CANDIDATE — that requires live data).")
    print(f"  2. Import their V6/V7/V8 modules in tick_live_executor.py.")
    print(f"  3. Run 2+ dry-run sessions per strategy before demo promotion.")
    print(f"  4. Max portfolio size: 8 strategies (Topstep multi-account limit).")
    print(f"  5. Prioritise strategies with low correlation to existing 5:")
    print(f"     — GC/SI pure price-action (Donchian, momentum) are NEW signal types")
    print(f"     — They do NOT use CVD/delta/OBI → additive, not redundant")
    print(f"  6. Worst-micro-day check: only strategies with worst micro < $200")
    print(f"     are eligible for the $200/trade risk cap in tick_tradovate_client.py")
    print(f"     (others need smaller position sizing).")

    eligible = [r for r in new_passed if abs(r.get("worst_micro", -9999)) <= 200]
    borderline = [r for r in new_passed if 200 < abs(r.get("worst_micro", 0)) <= 500]
    over_limit = [r for r in new_passed if abs(r.get("worst_micro", 0)) > 500]

    print(f"\n  Micro worst-day breakdown (using 1 micro contract = 10% of full):")
    print(f"    Within $200/day:  {len(eligible)} strategies  ← safe to add directly")
    print(f"    $200–$500/day:    {len(borderline)} strategies ← reduce size to 0.5 micro")
    print(f"    Over $500/day:    {len(over_limit)} strategies ← exclude until more data")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(quick=False, top_n=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_strats = len(ALL_STRATS)

    print(f"\n{'='*72}")
    print(f"  V678 FULL STRESS TEST — {n_strats} strategies")
    print(f"  {ts}")
    print(f"  Combos: {len(ALL_COMBOS)} symbol/timeframe × {n_strats} strategies")
    print(f"  Pass: 1t-Sharpe >= 1.0 | >=70% yrs positive (GC/SI) | Topstep >= 95%")
    print(f"{'='*72}")

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  PHASE 1 — Grid search (best params per strategy/symbol/timeframe)")
    print(f"  Threshold for Phase 2: Sharpe >= 0.80 at 0.5t slippage")
    print(f"{'='*72}\n")

    phase1_results = []

    for strat_name, strat in ALL_STRATS.items():
        print(f"\n  ── {strat_name} ──")
        for symbol, bar_min in ALL_COMBOS:
            df = load_bars(symbol, bar_min)
            if df is None: continue
            has_long = (symbol, bar_min) in COMBOS_LONG
            hist_tag = f"{df.index[0].date().year}–{df.index[-1].date().year}"

            sr, n, wr, best_params = phase1_best(df, strat, symbol)
            if best_params is None:
                print(f"    {symbol}/{bar_min}m [{hist_tag}]  SKIP (no viable params)")
                continue

            short_flag = " [SHORT]" if not has_long else ""
            gate = "→ Phase2" if sr >= 0.80 else "  (below threshold)"
            print(f"    {symbol}/{bar_min}m [{hist_tag}]  Sharpe={sr:.2f}  n={n}  "
                  f"WR={wr:.0%}{short_flag}  {gate}")

            if sr >= 0.80:
                phase1_results.append({
                    "strat_name":  strat_name,
                    "strat":       strat,
                    "symbol":      symbol,
                    "bar_min":     bar_min,
                    "sr_phase1":   sr,
                    "n_phase1":    n,
                    "wr_phase1":   wr,
                    "best_params": best_params,
                    "has_long":    has_long,
                })

    print(f"\n  Phase 1 complete: {len(phase1_results)} candidates qualify for Phase 2")

    if quick:
        print(f"\n  --quick flag: stopping after Phase 1.")
        return [], []

    # Sort by phase1 Sharpe descending; optionally cap to top_n
    phase1_results.sort(key=lambda x: -x["sr_phase1"])
    if top_n:
        phase1_results = phase1_results[:top_n]
        print(f"  --top {top_n}: testing top {top_n} candidates only")

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  PHASE 2 — Full stress test ({len(phase1_results)} candidates)")
    print(f"{'='*72}")

    passed = []
    failed = []

    for cand in phase1_results:
        strat_name = cand["strat_name"]
        symbol     = cand["symbol"]
        bar_min    = cand["bar_min"]
        params     = cand["best_params"]
        has_long   = cand["has_long"]
        fn         = cand["strat"]["compute"]
        label      = f"{symbol}/{strat_name}/{bar_min}m"

        df = load_bars(symbol, bar_min)
        if df is None: continue

        print(f"\n  {label}")
        print(f"  Params: {params}")
        if not has_long:
            print(f"  NOTE: short data window — regime check skipped")
        print(f"  {'─'*66}")

        res = full_stress(df, fn, params, symbol, has_long_history=has_long)

        print_slip_table(res["slip"])
        print()
        print_regimes(res["regimes"])
        print()
        print(f"  Topstep: {res['ts_pct']*100:.1f}%   "
              f"Worst day (full): ${res['worst_day']:,.0f}   "
              f"Worst day (micro 1x): ${res['worst_micro']:,.0f}")
        regime_str = (f"{res['pos_years']}/{res['tot_years']} yrs"
                      if has_long else "N/A (short)")
        verdict = "PASS" if res["ok"] else "FAIL"
        print(f"\n  {verdict}  "
              f"(1t-Sharpe={res['sr1t']:.2f}  "
              f"Regimes={regime_str}  "
              f"TS={res['ts_pct']*100:.0f}%)")

        record = {
            "label":           label,
            "symbol":          symbol,
            "bar_min":         bar_min,
            "strategy":        strat_name,
            "strategy_family": ("vwap_session" if "vwap" in strat_name or "session" in strat_name
                                 else "price_action" if any(x in strat_name for x in ["breakout","reversal","fakeout","wick","gap","sweep"])
                                 else "trend_momentum"),
            "params":          params,
            "sr1t":            round(res["sr1t"], 3),
            "regimes":         regime_str,
            "ts_pct":          round(res["ts_pct"], 4),
            "worst_day":       round(res["worst_day"], 0),
            "worst_micro":     round(res["worst_micro"], 0),
            "long_history":    has_long,
        }
        (passed if res["ok"] else failed).append(record)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  PHASE 2 RESULTS: {len(passed)} PASSED / {len(failed)} FAILED")
    print(f"{'='*72}")

    full_pass  = [r for r in passed if r["long_history"]]
    short_pass = [r for r in passed if not r["long_history"]]

    if full_pass:
        print(f"\n  FULL PASS (multi-year data — eligible for live portfolio):")
        for r in sorted(full_pass, key=lambda x: -x["sr1t"]):
            print(f"    PASS: {r['label']}")
            print(f"          1t-Sharpe={r['sr1t']:.2f}  "
                  f"Regimes={r['regimes']}  "
                  f"TS={r['ts_pct']*100:.0f}%  "
                  f"Worst(micro)=${r['worst_micro']:,.0f}")
            print(f"          Params: {r['params']}")

    if short_pass:
        print(f"\n  SHORT-WINDOW PASS (ES/NQ — needs more history before live):")
        for r in sorted(short_pass, key=lambda x: -x["sr1t"]):
            print(f"    PASS*: {r['label']}")
            print(f"           1t-Sharpe={r['sr1t']:.2f}  "
                  f"TS={r['ts_pct']*100:.0f}%  "
                  f"Worst(micro)=${r['worst_micro']:,.0f}")
            print(f"           Params: {r['params']}")

    # Save results
    out_path = ANA_DIR / f"v678_stress_results_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(out_path, "w") as f:
        json.dump({"passed": passed, "failed": failed}, f, indent=2)
    print(f"\n  Results saved -> {out_path}")

    # ── Phase 3: portfolio integration ────────────────────────────────────────
    if passed:
        # Find existing survivors file
        existing_path = None
        existing_files = sorted((ROOT / "02_analysis").glob("v*_stress_results.json"))
        if existing_files:
            existing_path = str(existing_files[-1])

        portfolio_integration(passed, existing_path)

        # ── Recommended next steps ────────────────────────────────────────────
        print(f"\n{'='*72}")
        print(f"  RECOMMENDED ALLOWLIST ADDITIONS")
        print(f"{'='*72}")
        print(f"""
  Add the following to live_strategy_allowlist.yaml.
  Status: ENABLED_DRY_RUN (not DEMO_CANDIDATE until 2+ live dry-run sessions).
  Only add those with worst_micro <= $200 immediately.
  Others: set REVIEW_REQUIRED with note on position sizing.
        """)
        for r in sorted(full_pass, key=lambda x: -x["sr1t"]):
            status = "ENABLED_DRY_RUN" if abs(r["worst_micro"]) <= 200 else "REVIEW_REQUIRED"
            pz_note = "" if abs(r["worst_micro"]) <= 200 else "  # reduce to 0.5 micro first"
            print(f"  - id: TBD")
            print(f"    symbol: {r['symbol']}")
            print(f"    strategy: {r['strategy']}")
            print(f"    bar_min: {r['bar_min']}")
            print(f"    params: {r['params']}")
            print(f"    status: {status}{pz_note}")
            print(f"    1t_sharpe: {r['sr1t']:.2f}")
            print(f"    worst_micro: {r['worst_micro']:.0f}")
            print()

    print(f"\n  Done. {len(passed)} strategies passed full stress test.")
    return passed, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Phase 1 only")
    parser.add_argument("--top",   type=int, default=None,
                        help="Only run Phase 2 on top-N Phase-1 candidates")
    args = parser.parse_args()
    main(quick=args.quick, top_n=args.top)
