#!/usr/bin/env python3
"""
tick_correlation.py — Portfolio signal correlation analysis
===========================================================
Runs every active strategy on available bar data, computes pairwise
signal correlations, and flags dangerous pairs (correlation > 0.6 in
same direction on same instrument).

Why this matters: if GC/vwap_mean_reversion and GC/rolling_return_zscore
both fire long GC at the same time consistently, we're effectively doubling
position size in the same trade — defeating diversification.

Output: correlation matrix + dangerous pairs report
Usage:  python tick_correlation.py
"""
from __future__ import annotations

import sys, warnings
from itertools import combinations
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT       = Path(__file__).parent.parent
_LOCAL_BAR = ROOT / "01_data" / "tick_bars"
_VPS_BAR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR    = _LOCAL_BAR if _LOCAL_BAR.exists() and any(_LOCAL_BAR.glob("*.parquet")) else _VPS_BAR

sys.path.insert(0, str(Path(__file__).parent))
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8

ALL_STRATS = {**STRAT_MAP_V6, **STRAT_MAP_V7, **STRAT_MAP_V8}

HIGH_CORR_THRESHOLD = 0.60  # flag pairs above this

# Active strategies with their params (from executor LIVE_STRATEGIES)
# Only OHLCV strategies — V10 L2 excluded (different data)
ACTIVE = [
    # (label, sym, bar_min, strat_name, params)
    ("16", "GC", 30, "vwap_mean_reversion",       {"z_thresh": 2.5, "vwap_window": 40}),
    ("17", "GC", 30, "pivot_reversal",             {"pivot_window": 10, "atr_mult": 0.5}),
    ("18", "SI", 30, "opening_range_fakeout",      {"or_window": 4, "breakout_pct": 0.3, "atr_window": 14}),
    ("19", "SI",  3, "consecutive_close_momentum", {"n_closes": 3, "min_body_pct": 0.3}),
    ("20", "GC", 15, "pivot_reversal",             {"pivot_window": 10, "atr_mult": 0.5}),
    ("21", "SI",  1, "ema_crossover",              {"fast": 5, "slow": 20}),
    ("22", "SI", 15, "vwap_mean_reversion",        {"z_thresh": 2.0, "vwap_window": 20}),
    ("23", "SI",  3, "opening_range_fakeout",      {"or_window": 4, "breakout_pct": 0.3, "atr_window": 14}),
    ("33", "ES", 30, "overnight_gap_fill",         {"gap_atr_mult": 0.3, "atr_window": 14}),
    ("34", "ES", 15, "overnight_gap_fill",         {"gap_atr_mult": 0.3, "atr_window": 14}),
    ("37", "NQ", 30, "vwap_mean_reversion",        {"z_thresh": 2.5, "vwap_window": 40}),
    ("38", "ES", 30, "vwap_mean_reversion",        {"z_thresh": 2.5, "vwap_window": 10}),
    ("45", "NQ", 30, "donchian_breakout",          {"n": 40, "confirm": 2}),
    ("46", "NQ", 30, "overnight_gap_fill",         {"gap_atr_mult": 0.3, "atr_window": 14}),
]


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def compute_signal(sym: str, bar_min: int, strat_name: str, params: dict) -> pd.Series | None:
    df = load_bars(sym, bar_min)
    if df is None or len(df) < 50:
        return None
    fn = ALL_STRATS.get(strat_name, {}).get("compute")
    if fn is None:
        return None
    try:
        sig = fn(df, **params)
        sig.index = pd.to_datetime(sig.index, utc=True)
        return sig
    except Exception:
        return None


def main():
    print(f"\n{'='*60}")
    print(f"  Portfolio Signal Correlation Analysis")
    print(f"  High-correlation threshold: {HIGH_CORR_THRESHOLD:.0%}")
    print(f"{'='*60}\n")

    signals = {}
    for sid, sym, bar_min, strat_name, params in ACTIVE:
        label = f"ID{sid}:{sym}/{strat_name}/{bar_min}m"
        sig = compute_signal(sym, bar_min, strat_name, params)
        if sig is None:
            print(f"  {label}: no data — skipped")
            continue
        signals[label] = sig

    print(f"\n  Computed signals for {len(signals)} strategies\n")

    # Build aligned correlation matrix (resample all to 30m for alignment)
    # Group by instrument so we only correlate same-instrument strategies
    by_sym: dict[str, dict[str, pd.Series]] = {}
    for label, sig in signals.items():
        sym = label.split(":")[1].split("/")[0]
        by_sym.setdefault(sym, {})[label] = sig

    dangerous_pairs = []

    for sym, sym_signals in by_sym.items():
        if len(sym_signals) < 2:
            continue
        labels = list(sym_signals.keys())
        # Align on common index (inner join)
        df_align = pd.DataFrame(sym_signals).dropna(how="all").fillna(0)
        if len(df_align) < 10:
            continue

        print(f"  === {sym} ({len(labels)} strategies, {len(df_align)} common bars) ===")
        for a, b in combinations(labels, 2):
            if a not in df_align.columns or b not in df_align.columns:
                continue
            corr = df_align[a].corr(df_align[b])
            if abs(corr) >= HIGH_CORR_THRESHOLD:
                direction = "SAME DIR" if corr > 0 else "OPPOSITE DIR"
                flag = "WARNING" if corr > 0 else "OK"
                print(f"    {flag} {a} vs {b}: corr={corr:.2f} ({direction})")
                dangerous_pairs.append((sym, a, b, round(corr, 3)))
            else:
                print(f"    OK    {a} vs {b}: corr={corr:.2f}")
        print()

    print(f"\n{'='*60}")
    if dangerous_pairs:
        print(f"  DANGEROUS PAIRS ({len(dangerous_pairs)}) — same direction, high correlation:")
        for sym, a, b, c in sorted(dangerous_pairs, key=lambda x: -abs(x[3])):
            print(f"    [{sym}] {a} ↔ {b}  corr={c:.2f}")
        print(f"\n  Action: consider adding portfolio-level position exclusivity for these pairs.")
        print(f"  They may effectively double position size on the same trade.")
    else:
        print(f"  No dangerous high-correlation pairs found.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
