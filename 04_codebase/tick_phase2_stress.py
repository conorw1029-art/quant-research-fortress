#!/usr/bin/env python3
"""
tick_phase2_stress.py — Phase 2 stress test for allowlist candidates
=====================================================================
Runs the same checks used when strategies 16-38 were stress-tested:
  1. Year-by-year P&L (every year must be profitable or marginal)
  2. Topstep daily drawdown compliance ($500 limit on micro)
  3. Worst-day micro exposure
  4. Total trade count
  5. Win rate and Sharpe

Usage:
    python tick_phase2_stress.py                  # test IDs 45-46 (default)
    python tick_phase2_stress.py --id 33 45 46    # test specific IDs
"""
from __future__ import annotations

import argparse, json, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT       = Path(__file__).parent.parent
_LOCAL_BAR = ROOT / "01_data" / "tick_bars"
_VPS_BAR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR    = _LOCAL_BAR if _LOCAL_BAR.exists() and any(_LOCAL_BAR.glob("*.parquet")) else _VPS_BAR
OUT_DIR    = ROOT / "05_backtests"; OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8
from tick_strategies_v9 import STRAT_MAP_V9

STOP_MULT = 1.5
TP_MULT   = 3.0

TOPSTEP_DAILY_LIMIT = 500.0  # micro: $500/day max drawdown


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def get_strat_fn(name: str, version: str):
    for mp in [STRAT_MAP_V6, STRAT_MAP_V7, STRAT_MAP_V8, STRAT_MAP_V9]:
        if name in mp:
            return mp[name]["compute"]
    return None


def stress_test(sid: int, sym: str, bar_min: int, strat_name: str, params: dict, version: str) -> dict:
    df = load_bars(sym, bar_min)
    if df is None or len(df) < 100:
        return {"id": sid, "skip": f"no data for {sym}/{bar_min}m"}

    fn = get_strat_fn(strat_name, version)
    if fn is None:
        return {"id": sid, "skip": f"strategy '{strat_name}' not found in {version}"}

    try:
        sig = fn(df, **params)
        tr  = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
    except Exception as e:
        return {"id": sid, "skip": str(e)}

    if tr.empty or len(tr) < 5:
        return {"id": sid, "skip": "insufficient trades"}

    spec = SPECS.get(sym, SPECS["GC"])
    pv   = spec["point_value"]
    micro_mult = 0.1  # micro = 1/10 full

    pnl = tr["dollar_pnl"] * micro_mult

    # Daily P&L (by calendar date)
    if "entry_time" in tr.columns:
        dates = pd.to_datetime(tr["entry_time"], utc=True).dt.date
    else:
        dates = tr.index.date
    daily = pnl.groupby(dates).sum()

    # Year-by-year
    if "entry_time" in tr.columns:
        years = pd.to_datetime(tr["entry_time"], utc=True).dt.year
    else:
        years = pd.Series([d.year for d in tr.index.date], index=tr.index)

    yearly = {}
    for yr, grp in tr.groupby(years):
        yd_pnl = grp["dollar_pnl"].sum() * micro_mult
        yd_wr  = (grp["dollar_pnl"] > 0).mean()
        yearly[int(yr)] = {"pnl": round(yd_pnl, 2), "wr": round(float(yd_wr), 3), "n": len(grp)}

    n_positive_years = sum(1 for v in yearly.values() if v["pnl"] > 0)
    n_years = len(yearly)

    # Topstep compliance
    daily_breach_days = int((daily < -TOPSTEP_DAILY_LIMIT).sum())
    topstep_pct = round(1.0 - daily_breach_days / max(len(daily), 1), 4)
    topstep_ok  = topstep_pct >= 0.99

    worst_day  = float(daily.min())
    total_pnl  = float(pnl.sum())
    win_rate   = float((tr["dollar_pnl"] > 0).mean())
    n_trades   = len(tr)

    r_s = tr["dollar_pnl"] * micro_mult / (pv * micro_mult * STOP_MULT)
    sr  = float(r_s.mean() / r_s.std() * np.sqrt(252)) if r_s.std() > 0 else 0

    grade = "PASS" if (n_positive_years >= n_years * 0.7 and topstep_ok and worst_day >= -1000) else "FAIL"

    return {
        "id":              sid,
        "label":           f"{sym}/{strat_name}/{bar_min}m",
        "grade":           grade,
        "n_trades":        n_trades,
        "win_rate":        round(win_rate, 3),
        "sharpe_micro":    round(sr, 3),
        "total_pnl_micro": round(total_pnl, 2),
        "worst_day_micro": round(worst_day, 2),
        "topstep_pct":     topstep_pct,
        "topstep_ok":      topstep_ok,
        "years_positive":  f"{n_positive_years}/{n_years}",
        "yearly":          yearly,
    }


# ── Strategy registry — IDs to test ─────────────────────────────────────────
CANDIDATES = {
    45: ("NQ", 30, "donchian_breakout",  {"n": 40, "confirm": 2},              "v7"),
    46: ("NQ", 30, "overnight_gap_fill", {"gap_atr_mult": 0.3, "atr_window": 14}, "v6"),
    # Add others here as needed, e.g.:
    # 33: ("ES", 30, "overnight_gap_fill", {"gap_atr_mult": 0.3, "atr_window": 14}, "v6"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", nargs="+", type=int, default=None,
                        help="Strategy IDs to test (default: all in CANDIDATES)")
    args = parser.parse_args()

    ids_to_test = args.id or list(CANDIDATES.keys())
    results = []

    print(f"\n{'='*65}")
    print(f"  Phase 2 Stress Test — {len(ids_to_test)} strategies")
    print(f"  Topstep limit: ${TOPSTEP_DAILY_LIMIT}/day | worst_micro threshold: -$1,000")
    print(f"{'='*65}\n")

    for sid in ids_to_test:
        if sid not in CANDIDATES:
            print(f"  ID {sid}: not in CANDIDATES dict — skipping")
            continue
        sym, bar_min, strat_name, params, version = CANDIDATES[sid]
        r = stress_test(sid, sym, bar_min, strat_name, params, version)
        results.append(r)

        if "skip" in r:
            print(f"  ID {r['id']:2d} {r.get('label','?'):<40}  SKIP: {r['skip']}")
            continue

        g = "✓ PASS" if r["grade"] == "PASS" else "✗ FAIL"
        print(f"  ID {r['id']:2d} {r['label']:<40}  {g}")
        print(f"       n={r['n_trades']}  WR={r['win_rate']:.1%}  sr={r['sharpe_micro']:.2f}"
              f"  PnL=${r['total_pnl_micro']:,.0f}  worst_day=${r['worst_day_micro']:,.0f}")
        print(f"       Topstep={r['topstep_pct']:.1%} ({'OK' if r['topstep_ok'] else 'FAIL'})"
              f"  yrs_pos={r['years_positive']}")
        if r["yearly"]:
            for yr in sorted(r["yearly"]):
                yd = r["yearly"][yr]
                flag = "✓" if yd["pnl"] > 0 else "✗"
                print(f"       {yr}: {flag} PnL=${yd['pnl']:,.0f}  WR={yd['wr']:.0%}  n={yd['n']}")
        print()

    out = OUT_DIR / "phase2_stress_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results → {out}")

    passed = [r for r in results if r.get("grade") == "PASS"]
    failed = [r for r in results if r.get("grade") == "FAIL"]
    print(f"\nPASS: {len(passed)}  |  FAIL: {len(failed)}")


if __name__ == "__main__":
    main()
