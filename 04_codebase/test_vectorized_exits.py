#!/usr/bin/env python3
"""
Vectorized Exit Correctness Test
===================================
Verifies that the vectorized exit engine produces IDENTICAL results
to the original Python loop on a sample of real trades.

Run this BEFORE swapping the loop out of production strategies.

Usage:
    python test_vectorized_exits.py --input ..\01_data\raw\ES_1min.csv --source-tz utc --col-timestamp ts_event
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

from src.data.es_data_pipeline import ESDataLoader
import src.data.data_schema as S
from src.data.data_schema import INSTRUMENTS, InstrumentSpec


def load_sample(csv_path, source_tz, col_ts, n_days=200):
    """Load a small sample of data for testing."""
    loader = ESDataLoader(
        source="csv", data_path=csv_path,
        source_tz=source_tz,
        col_mapping={col_ts: "timestamp"},
    )
    df = loader.load()
    rth = loader.filter_rth(df)
    bars = loader.resample(rth, "5min")
    feat = loader.add_features(bars)
    # Use first n_days only for speed
    dates = sorted(feat[S.SESSION_DATE].unique())
    cutoff_date = dates[min(n_days, len(dates) - 1)]
    sample = feat[feat[S.SESSION_DATE] <= cutoff_date].copy()
    print(f"Sample: {len(sample)} bars, {sample[S.SESSION_DATE].nunique()} days")
    return sample


def loop_version_trades(data, signals, stop_atr=1.5, target_atr=1.0, timeout=12):
    """Original Python loop — reference implementation."""
    trades = []
    for idx in signals[signals != 0].index:
        try:
            entry_loc = data.index.get_loc(idx)
            if entry_loc + 1 >= len(data):
                continue
            entry_bar = data.iloc[entry_loc + 1]
            entry_price = entry_bar["open"]
            entry_time = entry_bar.name
            direction = int(signals[idx])
            atr_val = data["atr"].loc[idx]
            if np.isnan(atr_val) or atr_val == 0:
                continue
            target_pts = target_atr * atr_val
            stop_pts = stop_atr * atr_val
            target_price = entry_price + direction * target_pts
            stop_loss = entry_price - direction * stop_pts
            exit_price = None
            exit_time = None
            exit_type = "timeout"
            for i in range(1, timeout + 1):
                if entry_loc + 1 + i >= len(data):
                    break
                bar = data.iloc[entry_loc + 1 + i]
                if direction == 1:
                    if bar["low"] <= stop_loss:
                        exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                    if bar["high"] >= target_price:
                        exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
                else:
                    if bar["high"] >= stop_loss:
                        exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                    if bar["low"] <= target_price:
                        exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
            if exit_price is None:
                exit_index = entry_loc + 1 + timeout
                if exit_index < len(data):
                    exit_bar = data.iloc[exit_index]
                    exit_price = exit_bar["close"]
                    exit_time = exit_bar.name
                    exit_type = "timeout"
                else:
                    continue
            gross_pnl = (exit_price - entry_price) * direction
            trades.append({
                "entry_time": entry_time, "entry_price": round(entry_price, 4),
                "exit_time": exit_time, "exit_price": round(exit_price, 4),
                "direction": direction, "exit_type": exit_type,
                "gross_pnl": round(gross_pnl, 6),
            })
        except Exception as e:
            continue
    return trades


def compare_results(loop_trades, vec_trades, label=""):
    """Compare two lists of trade dicts. Returns True if identical."""
    if len(loop_trades) != len(vec_trades):
        print(f"  FAIL [{label}]: trade count mismatch: loop={len(loop_trades)}, vec={len(vec_trades)}")
        return False

    mismatches = 0
    for i, (lt, vt) in enumerate(zip(loop_trades, vec_trades)):
        ok = True
        for field in ["entry_price", "exit_price", "direction", "exit_type", "gross_pnl"]:
            lv = lt.get(field)
            vv = vt.get(field)
            if isinstance(lv, float) and isinstance(vv, float):
                if abs(lv - vv) > 1e-4:
                    print(f"    Trade {i} field '{field}': loop={lv}, vec={vv}")
                    ok = False
            else:
                if str(lv) != str(vv):
                    print(f"    Trade {i} field '{field}': loop={lv}, vec={vv}")
                    ok = False
        if not ok:
            mismatches += 1
            if mismatches >= 5:
                print(f"    ... (stopping at 5 mismatches)")
                break

    if mismatches == 0:
        print(f"  PASS [{label}]: {len(loop_trades)} trades, all identical")
        return True
    else:
        print(f"  FAIL [{label}]: {mismatches}/{len(loop_trades)} mismatches")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--n-days", type=int, default=200)
    args = parser.parse_args()

    print("=" * 70)
    print("  VECTORIZED EXIT CORRECTNESS TEST")
    print("=" * 70)

    data = load_sample(args.input, args.source_tz, args.col_timestamp, args.n_days)

    from src.backtesting.vectorized_exits import signals_to_trades_fast

    # Test 1: RSI signals
    rsi = data["rsi"]
    sig_rsi_long = pd.Series(0, index=data.index)
    sig_rsi_long[(rsi.shift(1) >= 25) & (rsi < 25)] = 1
    sig_rsi_long[(rsi.shift(1) <= 75) & (rsi > 75)] = -1

    print("\n  Test 1: RSI signals, long+short, timeout=12")
    loop_t = loop_version_trades(data, sig_rsi_long, stop_atr=1.5, target_atr=1.0, timeout=12)
    vec_t = signals_to_trades_fast(data, sig_rsi_long, "atr", 1.5, 1.0, 12, max_trades_per_day=999)
    compare_results(loop_t, vec_t, "RSI 25/75 1.0 tgt")

    # Test 2: Different params
    sig_rsi2 = pd.Series(0, index=data.index)
    sig_rsi2[(rsi.shift(1) >= 30) & (rsi < 30)] = 1
    sig_rsi2[(rsi.shift(1) <= 70) & (rsi > 70)] = -1

    print("\n  Test 2: RSI 30/70, target=0.75, timeout=8")
    loop_t2 = loop_version_trades(data, sig_rsi2, stop_atr=1.5, target_atr=0.75, timeout=8)
    vec_t2 = signals_to_trades_fast(data, sig_rsi2, "atr", 1.5, 0.75, 8, max_trades_per_day=999)
    compare_results(loop_t2, vec_t2, "RSI 30/70 0.75 tgt")

    # Test 3: Long only
    sig_long_only = pd.Series(0, index=data.index)
    sig_long_only[(rsi.shift(1) >= 25) & (rsi < 25)] = 1

    print("\n  Test 3: Long-only signals")
    loop_t3 = loop_version_trades(data, sig_long_only, stop_atr=1.5, target_atr=1.0, timeout=12)
    vec_t3 = signals_to_trades_fast(data, sig_long_only, "atr", 1.5, 1.0, 12, max_trades_per_day=999)
    compare_results(loop_t3, vec_t3, "Long only")

    # Test 4: Short only
    sig_short_only = pd.Series(0, index=data.index)
    sig_short_only[(rsi.shift(1) <= 75) & (rsi > 75)] = -1

    print("\n  Test 4: Short-only signals")
    loop_t4 = loop_version_trades(data, sig_short_only, stop_atr=1.5, target_atr=1.0, timeout=12)
    vec_t4 = signals_to_trades_fast(data, sig_short_only, "atr", 1.5, 1.0, 12, max_trades_per_day=999)
    compare_results(loop_t4, vec_t4, "Short only")

    # Speed test
    import time
    print("\n  Speed comparison (full dataset):")
    full_sig = pd.Series(0, index=data.index)
    full_sig[(rsi.shift(1) >= 25) & (rsi < 25)] = 1
    full_sig[(rsi.shift(1) <= 75) & (rsi > 75)] = -1

    t0 = time.time()
    _ = loop_version_trades(data, full_sig, 1.5, 1.0, 12)
    t_loop = time.time() - t0

    t0 = time.time()
    _ = signals_to_trades_fast(data, full_sig, "atr", 1.5, 1.0, 12, max_trades_per_day=999)
    t_vec = time.time() - t0

    print(f"    Loop: {t_loop:.3f}s")
    print(f"    Vec:  {t_vec:.3f}s")
    print(f"    Speedup: {t_loop/t_vec:.1f}x")

    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()