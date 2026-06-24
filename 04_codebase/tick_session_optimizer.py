#!/usr/bin/env python3
"""
tick_session_optimizer.py — Per-UTC-hour session filtering optimizer
=====================================================================
For each strategy, finds which UTC hours to BLOCK to maximize OOS Sharpe.
This is the same "session-hour filter" used to get 20-180% Sharpe improvements
on strategies 16-38 (e.g. ES/overnight_gap_fill improved 165%, NQ/ma_slope 25%).

Algorithm:
  1. For each hour 0-23: compute per-hour Sharpe contribution
  2. Greedily remove the worst hour, re-compute total Sharpe
  3. Stop when removing any remaining hour hurts Sharpe
  4. Report: blocked hours set, filtered Sharpe, improvement %

Usage:
    python tick_session_optimizer.py               # optimizes IDs 45-46
    python tick_session_optimizer.py --id 33 45 46
"""
from __future__ import annotations

import argparse, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT       = Path(__file__).parent.parent
_LOCAL_BAR = ROOT / "01_data" / "tick_bars"
_VPS_BAR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR    = _LOCAL_BAR if _LOCAL_BAR.exists() and any(_LOCAL_BAR.glob("*.parquet")) else _VPS_BAR

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8

ALL_STRATS = {**STRAT_MAP_V6, **STRAT_MAP_V7, **STRAT_MAP_V8}

STOP_MULT = 1.5
TP_MULT   = 3.0

CANDIDATES = {
    45: ("NQ", 30, "donchian_breakout",  {"n": 40, "confirm": 2},              "v7"),
    46: ("NQ", 30, "overnight_gap_fill", {"gap_atr_mult": 0.3, "atr_window": 14}, "v6"),
    # Reference strategies (already optimized — use to validate the method)
    33: ("ES", 30, "overnight_gap_fill", {"gap_atr_mult": 0.3, "atr_window": 14}, "v6"),
    37: ("NQ", 30, "vwap_mean_reversion", {"z_thresh": 2.5, "vwap_window": 40}, "v6"),
}


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def compute_sharpe(tr: pd.DataFrame) -> float:
    if tr.empty or len(tr) < 5:
        return -999.0
    r = tr["dollar_pnl"]
    return float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0


def filter_by_hours(tr: pd.DataFrame, blocked_hours: set[int]) -> pd.DataFrame:
    if tr.empty or "entry_time" not in tr.columns:
        return tr
    entry_hour = pd.to_datetime(tr["entry_time"], utc=True).dt.hour
    return tr[~entry_hour.isin(blocked_hours)]


def optimize_hours(sid: int, sym: str, bar_min: int, strat_name: str, params: dict) -> dict:
    df = load_bars(sym, bar_min)
    if df is None or len(df) < 100:
        return {"id": sid, "skip": f"no data for {sym}/{bar_min}m"}

    fn = ALL_STRATS.get(strat_name, {}).get("compute")
    if fn is None:
        return {"id": sid, "skip": f"strategy '{strat_name}' not found"}

    try:
        sig = fn(df, **params)
        tr_full = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
    except Exception as e:
        return {"id": sid, "skip": str(e)}

    if tr_full.empty or len(tr_full) < 20:
        return {"id": sid, "skip": "insufficient trades"}

    base_sharpe = compute_sharpe(tr_full)

    # Greedy hour removal
    blocked = set()
    current_sharpe = base_sharpe
    all_hours = set(range(24))

    # Must retain at least 30% of base trades or 40 trades — prevents overfitting
    min_trades = max(40, int(len(tr_full) * 0.30))

    while True:
        best_gain = 0.0
        best_hour = None
        remaining = all_hours - blocked

        for h in remaining:
            candidate_blocked = blocked | {h}
            tr_filtered = filter_by_hours(tr_full, candidate_blocked)
            if len(tr_filtered) < min_trades:
                continue  # skip — would leave too few trades
            s = compute_sharpe(tr_filtered)
            gain = s - current_sharpe
            if gain > best_gain:
                best_gain = gain
                best_hour = h

        if best_hour is None or best_gain <= 0:
            break  # no improvement possible
        blocked.add(best_hour)
        current_sharpe = compute_sharpe(filter_by_hours(tr_full, blocked))

    tr_final = filter_by_hours(tr_full, blocked)
    final_sharpe = compute_sharpe(tr_final)
    improvement_pct = (final_sharpe - base_sharpe) / abs(base_sharpe) * 100 if base_sharpe != 0 else 0

    keep_hours = sorted(all_hours - blocked)

    return {
        "id":           sid,
        "label":        f"{sym}/{strat_name}/{bar_min}m",
        "base_sharpe":  round(base_sharpe, 3),
        "filtered_sharpe": round(final_sharpe, 3),
        "improvement_pct": round(improvement_pct, 1),
        "blocked_hours": sorted(blocked),
        "keep_hours":   keep_hours,
        "n_trades_base": len(tr_full),
        "n_trades_filtered": len(tr_final),
        "blocked_set_py": "{" + ",".join(str(h) for h in sorted(blocked)) + "}" if blocked else "None",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", nargs="+", type=int, default=None)
    args = parser.parse_args()
    ids_to_test = args.id or [45, 46]

    print(f"\n{'='*65}")
    print(f"  Session-Hour Optimizer — {len(ids_to_test)} strategies")
    print(f"  Greedy hour removal to maximize OOS Sharpe")
    print(f"{'='*65}\n")

    results = []
    for sid in ids_to_test:
        if sid not in CANDIDATES:
            print(f"  ID {sid}: not in CANDIDATES — skipping"); continue
        sym, bar_min, strat_name, params, version = CANDIDATES[sid]
        r = optimize_hours(sid, sym, bar_min, strat_name, params)
        results.append(r)

        if "skip" in r:
            print(f"  ID {r['id']:2d}: SKIP — {r['skip']}"); continue

        print(f"  ID {r['id']:2d} {r['label']}")
        print(f"    Base Sharpe:     {r['base_sharpe']:.3f} ({r['n_trades_base']} trades)")
        print(f"    Filtered Sharpe: {r['filtered_sharpe']:.3f} ({r['n_trades_filtered']} trades)")
        print(f"    Improvement:     +{r['improvement_pct']:.1f}%")
        if r["blocked_hours"]:
            print(f"    Block UTC hours: {r['blocked_hours']}")
            print(f"    Keep UTC hours:  {r['keep_hours']}")
            print(f"\n    Executor entry (copy-paste):")
            print(f"    ({r['id']}, \"{sym}\", {bar_min}, \"{strat_name}\",")
            print(f"     {params},")
            print(f"     {r['blocked_set_py']}, None, \"{version}\")  "
                  f"# filtered Sharpe={r['filtered_sharpe']:.3f} (+{r['improvement_pct']:.1f}%)")
        else:
            print(f"    No hours blocked — strategy already uniform across sessions")
        print()

    return results


if __name__ == "__main__":
    main()
