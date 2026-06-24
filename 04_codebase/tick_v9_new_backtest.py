#!/usr/bin/env python3
"""
tick_v9_new_backtest.py — Quick backtest of new V9 calendar strategies
======================================================================
Tests nfp_eve_drift, cpi_eve_gold, month_end_equity on available data.
Compares to the existing fomc_drift baseline.
"""
from __future__ import annotations

import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT       = Path(__file__).parent.parent
_LOCAL_BAR = ROOT / "01_data" / "tick_bars"
_VPS_BAR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR    = _LOCAL_BAR if _LOCAL_BAR.exists() and any(_LOCAL_BAR.glob("*.parquet")) else _VPS_BAR

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest
from tick_strategies_v9 import STRAT_MAP_V9

STOP_MULT = 1.5
TP_MULT   = 3.0

CANDIDATES = [
    # (label, sym, bar_min, strat_name, params)
    ("BASELINE fomc_drift",  "ES", 30, "fomc_drift",      {}),
    ("nfp_eve_drift ES",     "ES", 30, "nfp_eve_drift",   {}),
    ("nfp_eve_drift NQ",     "NQ", 30, "nfp_eve_drift",   {}),
    ("cpi_eve_gold GC",      "GC", 30, "cpi_eve_gold",    {}),
    ("month_end_equity ES",  "ES", 30, "month_end_equity", {"n_days": 2}),
    ("month_end_equity NQ",  "NQ", 30, "month_end_equity", {"n_days": 2}),
    # Try 3-day window too
    ("month_end_equity3 ES", "ES", 30, "month_end_equity", {"n_days": 3}),
]


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def compute_dsr(tr: pd.DataFrame, n_params: int = 1) -> float:
    if tr.empty or len(tr) < 10:
        return 0.0
    r = tr["dollar_pnl"]
    sr = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return sr / max(np.sqrt(np.log(n_params)), 0.01)


def main():
    print(f"\n{'='*68}")
    print(f"  V9 New Calendar Strategies Backtest")
    print(f"{'='*68}\n")

    results = []

    for label, sym, bar_min, strat_name, params in CANDIDATES:
        df = load_bars(sym, bar_min)
        if df is None or len(df) < 100:
            print(f"  {label:<30}  NO DATA for {sym}/{bar_min}m")
            continue

        fn = STRAT_MAP_V9.get(strat_name, {}).get("compute")
        if fn is None:
            print(f"  {label:<30}  STRAT NOT FOUND: {strat_name}")
            continue

        try:
            sig = fn(df, **params)
            n_signals = int((sig != 0).sum())
            if n_signals < 5:
                print(f"  {label:<30}  SKIP — only {n_signals} signal bars in data")
                continue

            tr = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
        except Exception as e:
            print(f"  {label:<30}  ERROR: {e}")
            continue

        if tr.empty or len(tr) < 3:
            print(f"  {label:<30}  SKIP — {len(tr)} trades")
            continue

        micro_mult = 0.1
        pnl        = tr["dollar_pnl"] * micro_mult
        win_rate   = float((tr["dollar_pnl"] > 0).mean())
        total_pnl  = float(pnl.sum())
        dsr        = compute_dsr(tr)

        # Year-by-year
        if "entry_time" in tr.columns:
            years = pd.to_datetime(tr["entry_time"], utc=True).dt.year
        else:
            years = pd.Series([d.year for d in tr.index.date], index=tr.index)

        yearly = {}
        for yr, grp in tr.groupby(years):
            yearly[int(yr)] = round(grp["dollar_pnl"].sum() * micro_mult, 0)

        data_start = df.index[0].strftime("%Y-%m-%d")
        data_end   = df.index[-1].strftime("%Y-%m-%d")

        grade = "PROMISING" if (dsr >= 1.2 and win_rate >= 0.55 and len(tr) >= 20) else \
                "MARGINAL"  if (dsr >= 0.8 and win_rate >= 0.50) else "WEAK"

        results.append({
            "label": label, "sym": sym, "strat": strat_name,
            "n": len(tr), "wr": win_rate, "dsr": dsr,
            "total_micro": total_pnl, "grade": grade, "yearly": yearly,
            "data": f"{data_start} → {data_end}",
        })

        print(f"  {label:<30}  {grade}  n={len(tr):3d}  WR={win_rate:.0%}"
              f"  DSR={dsr:.2f}  PnL=${total_pnl:,.0f}")
        yr_str = "  ".join(f"{y}:${v:+.0f}" for y, v in sorted(yearly.items()))
        print(f"    Data: {data_start}→{data_end}  {yr_str}")
        print()

    print(f"\n{'='*68}")
    promising = [r for r in results if r["grade"] == "PROMISING"]
    if promising:
        print(f"  PROMISING ({len(promising)}) — candidate for allowlist:")
        for r in promising:
            print(f"    {r['label']:<30}  DSR={r['dsr']:.2f}  WR={r['wr']:.0%}")
        print()
        print("  Next step: add to LIVE_STRATEGIES in tick_live_executor.py")
        print("  Then: run tick_phase2_stress.py to confirm Topstep compliance")
    else:
        print("  No new strategies meet the PROMISING threshold (DSR≥1.2, WR≥55%, n≥20)")
        print("  Consider: run on longer data after NT8 import, or adjust hold windows")
    print(f"{'='*68}\n")


if __name__ == "__main__":
    main()
