#!/usr/bin/env python3
"""
tick_promote_15m.py — Evaluate top 15m V678 survivors for executor promotion
=============================================================================
Runs session-hour optimizer + worst-day check on 15m strategies that passed
WFO with DSR >= 1.5 but aren't yet in the executor.

Data quality note: 15m data ≈ 70 trading days (marginal). These will be
re-validated after NT8 import provides multi-year 15m history.
Current use: add as REVIEW_REQUIRED (dry-run only) if worst_day passes.

Usage:
    python3 tick_promote_15m.py
"""
from __future__ import annotations

import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT       = Path(__file__).parent.parent
_VPS_BAR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR    = _VPS_BAR

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8

ALL_STRATS = {**STRAT_MAP_V6, **STRAT_MAP_V7, **STRAT_MAP_V8}

STOP_MULT = 1.5
TP_MULT   = 3.0
TOPSTEP_DAILY_LIMIT = 500.0

# Top 15m survivors from V678 WFO not yet in executor
# Format: (next_id, sym, strat_name, best_params, dsr, n_trades_wfo, version)
CANDIDATES_15M = [
    (47, "SI", "rolling_return_zscore", {"ret_bars": 3, "zscore_win": 50, "z_thresh": 1.8}, 2.75, 116, "v8"),
    (48, "SI", "ma_slope_regime",       {"ma_win": 20, "slope_bars": 3, "entry_rsi_win": 14, "rsi_ob": 60, "rsi_os": 40}, 2.67, 72, "v8"),
    (49, "ES", "bollinger_rsi_reversal", {"window": 15, "std_mult": 1.8, "rsi_win": 14, "rsi_ob": 70, "rsi_os": 30}, 2.57, 83, "v7"),
    (50, "NQ", "keltner_breakout",      {"ema_span": 20, "atr_win": 14, "mult": 2.5}, 2.06, 53, "v7"),
    (51, "SI", "keltner_breakout",      {"ema_span": 15, "atr_win": 10, "mult": 2.0}, 1.91, 61, "v7"),
    (52, "NQ", "consecutive_close_momentum", {"n": 4}, 1.82, 120, "v6"),
    (53, "ES", "wick_reversal",         {"wick_ratio": 0.6, "lookback": 20, "atr_window": 14}, 1.78, 56, "v7"),
    (54, "GC", "opening_range_breakout", {"orb_bars": 3, "buffer_atr_pct": 0.0, "atr_window": 14}, 1.75, 156, "v7"),
]

BAR_MIN = 15


def load_bars(sym: str) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{BAR_MIN}m.parquet"
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


def filter_hours(tr: pd.DataFrame, blocked: set) -> pd.DataFrame:
    if tr.empty or "entry_time" not in tr.columns:
        return tr
    h = pd.to_datetime(tr["entry_time"], utc=True).dt.hour
    return tr[~h.isin(blocked)]


def session_optimize(tr_full: pd.DataFrame) -> tuple[set, float]:
    """Greedy UTC-hour removal with min_trades guard."""
    base_sharpe = compute_sharpe(tr_full)
    min_trades = max(40, int(len(tr_full) * 0.30))
    blocked = set()
    current_sharpe = base_sharpe

    while True:
        best_gain, best_h = 0.0, None
        for h in set(range(24)) - blocked:
            tr_f = filter_hours(tr_full, blocked | {h})
            if len(tr_f) < min_trades:
                continue
            s = compute_sharpe(tr_f)
            if s - current_sharpe > best_gain:
                best_gain = s - current_sharpe
                best_h = h
        if best_h is None:
            break
        blocked.add(best_h)
        current_sharpe = compute_sharpe(filter_hours(tr_full, blocked))

    return blocked, current_sharpe


def worst_day_check(tr: pd.DataFrame, sym: str) -> dict:
    micro_mult = 0.1
    pnl = tr["dollar_pnl"] * micro_mult
    if "entry_time" in tr.columns:
        dates = pd.to_datetime(tr["entry_time"], utc=True).dt.date
    else:
        dates = tr.index.date
    daily = pnl.groupby(dates).sum()
    worst = float(daily.min())
    n_breach = int((daily < -TOPSTEP_DAILY_LIMIT).sum())
    topstep_pct = round(1.0 - n_breach / max(len(daily), 1), 4)
    return {
        "worst_micro": round(worst, 0),
        "topstep_pct": topstep_pct,
        "safe": worst >= -1000,
    }


def main():
    print(f"\n{'='*70}")
    print(f"  15m Strategy Promotion Evaluation")
    print(f"  Session-hour optimizer + worst-day check")
    print(f"  Data: ~70 days (marginal; mark as REVIEW_REQUIRED pending NT8)")
    print(f"{'='*70}\n")

    promotable = []

    for sid, sym, strat_name, params, wfo_dsr, wfo_n, version in CANDIDATES_15M:
        df = load_bars(sym)
        if df is None or len(df) < 50:
            print(f"  ID {sid} {sym}/{strat_name}/15m — NO DATA")
            continue

        fn = ALL_STRATS.get(strat_name, {}).get("compute")
        if fn is None:
            print(f"  ID {sid} {sym}/{strat_name}/15m — STRATEGY NOT FOUND in {version}")
            continue

        try:
            sig = fn(df, **params)
            tr_full = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
        except Exception as e:
            print(f"  ID {sid} {sym}/{strat_name}/15m — ERROR: {e}")
            continue

        if tr_full.empty or len(tr_full) < 20:
            print(f"  ID {sid} {sym}/{strat_name}/15m — too few trades ({len(tr_full)})")
            continue

        base_sharpe = compute_sharpe(tr_full)
        blocked, filtered_sharpe = session_optimize(tr_full)
        tr_filtered = filter_hours(tr_full, blocked)
        n_filtered = len(tr_filtered)

        wd = worst_day_check(tr_filtered, sym)
        impr = (filtered_sharpe - base_sharpe) / abs(base_sharpe) * 100 if base_sharpe != 0 else 0

        safe_marker = "OK" if wd["safe"] else "UNSAFE"
        print(f"\n  ID {sid:2d} {sym}/{strat_name}/15m")
        print(f"    WFO: DSR={wfo_dsr:.2f}  n_wfo={wfo_n}")
        print(f"    Base Sharpe: {base_sharpe:.3f} ({len(tr_full)} trades)")
        print(f"    Session opt: {filtered_sharpe:.3f} ({n_filtered} trades)  +{impr:.1f}%")
        if blocked:
            print(f"    Block UTC:   {sorted(blocked)}")
        print(f"    Worst micro: ${wd['worst_micro']:.0f}  Topstep: {wd['topstep_pct']:.1%}  [{safe_marker}]")

        if wd["safe"] and filtered_sharpe >= 1.5 and n_filtered >= 30:
            status = "PROMOTABLE"
            promotable.append({
                "id": sid, "sym": sym, "strat_name": strat_name,
                "params": params, "bar_min": BAR_MIN, "version": version,
                "wfo_dsr": wfo_dsr, "filtered_sharpe": round(filtered_sharpe, 3),
                "blocked": sorted(blocked), "worst_micro": wd["worst_micro"],
                "n_trades": n_filtered,
            })
        elif not wd["safe"]:
            status = "UNSAFE (worst_micro < -$1000)"
        elif filtered_sharpe < 1.5:
            status = f"WEAK Sharpe ({filtered_sharpe:.2f} < 1.5)"
        else:
            status = f"FEW TRADES ({n_filtered} < 30)"

        print(f"    => {status}")

    print(f"\n{'='*70}")
    print(f"  PROMOTABLE TO ALLOWLIST (REVIEW_REQUIRED): {len(promotable)}")
    print(f"  (These are dry-run eligible; re-validate after NT8 import)")
    print(f"{'='*70}\n")

    if promotable:
        print("  EXECUTOR ENTRIES (copy-paste into tick_live_executor.py):")
        print()
        for r in promotable:
            blocked_py = "{" + ",".join(str(h) for h in r["blocked"]) + "}" if r["blocked"] else "None"
            print(f"    ({r['id']}, \"{r['sym']}\", {r['bar_min']}, \"{r['strat_name']}\",")
            print(f"     {r['params']},")
            print(f"     {blocked_py}, None, \"{r['version']}\"),  "
                  f"# DSR={r['wfo_dsr']:.2f} filt_Sharpe={r['filtered_sharpe']:.2f}")
            print()

        print("  ALLOWLIST ENTRIES (copy-paste into live_strategy_allowlist.yaml):")
        print()
        for r in promotable:
            print(f"    {r['id']}:")
            print(f"      key: \"{r['sym']}/{r['strat_name']}/{r['bar_min']}m\"")
            print(f"      status: REVIEW_REQUIRED")
            print(f"      worst_day_usd: {int(r['worst_micro'] * 10)}")
            print(f"      worst_micro: {int(r['worst_micro'])}")
            print(f"      trade_count: {r['n_trades']}")
            print(f"      dsr: {r['wfo_dsr']}")
            print(f"      added_date: \"2026-06-24\"")
            print()


if __name__ == "__main__":
    main()
