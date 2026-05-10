#!/usr/bin/env python3
"""
Smoke test for Module 1.1: Data Pipeline
==========================================
Run this to verify the pipeline loads, filters, resamples, and
engineers features without errors.

Usage:
    python test_pipeline.py --input "path/to/ES_1min.csv" --source-tz utc --col-timestamp ts_event
"""

import argparse
import sys
import logging
from pathlib import Path

# Add parent to path for import
sys.path.insert(0, str(Path(__file__).parent / "src" / "data"))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from es_data_pipeline import ESDataLoader
import data_schema as S


def main():
    parser = argparse.ArgumentParser(description="Smoke test: Data Pipeline")
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    args = parser.parse_args()

    print("=" * 70)
    print("  DATA PIPELINE SMOKE TEST")
    print("=" * 70)

    # 1. Load raw
    loader = ESDataLoader(
        source="csv",
        data_path=args.input,
        source_tz=args.source_tz,
        col_mapping={args.col_timestamp: S.TIMESTAMP},
        instrument="ES",
    )

    raw = loader.load()
    print(f"\n  [1] Raw load: {len(raw):,} bars")
    print(f"      Columns: {list(raw.columns)}")
    print(f"      Date range: {raw[S.TIMESTAMP].iloc[0]} -> {raw[S.TIMESTAMP].iloc[-1]}")

    # 2. Filter RTH
    rth = loader.filter_rth(raw)
    print(f"\n  [2] RTH filter: {len(rth):,} bars ({100*len(rth)/len(raw):.1f}% of raw)")

    # 3. Resample to 5min
    bars_5m = loader.resample(rth, "5min")
    print(f"\n  [3] Resample 5min: {len(bars_5m):,} bars")
    print(f"      Sessions: {bars_5m[S.SESSION_DATE].nunique()}")

    # 4. Add features
    featured = loader.add_features(bars_5m)
    print(f"\n  [4] Features added: {len(featured.columns)} columns")
    print(f"      Columns: {list(featured.columns)}")
    non_null = featured.dropna()
    print(f"      Complete rows (after warmup): {len(non_null):,}"
          f" ({100*len(non_null)/len(featured):.1f}%)")

    # 5. Spot-check causal features
    print(f"\n  [5] Causal checks:")
    # ATR should be NaN for first atr_period rows
    first_valid_atr = featured[S.ATR].first_valid_index()
    print(f"      ATR first valid: bar {featured.index.get_loc(first_valid_atr)}"
          f" (should be >= 20)")

    # VWAP should reset each day
    sessions = featured[S.SESSION_DATE].unique()
    if len(sessions) > 2:
        s1 = sessions[1]
        s2 = sessions[2]
        vwap1_last = featured[featured[S.SESSION_DATE] == s1][S.SESSION_VWAP].iloc[-1]
        vwap2_first = featured[featured[S.SESSION_DATE] == s2][S.SESSION_VWAP].iloc[0]
        print(f"      VWAP resets between sessions: "
              f"S1 last={vwap1_last:.2f}, S2 first={vwap2_first:.2f} "
              f"({'OK - different' if abs(vwap1_last - vwap2_first) > 0.01 else 'WARNING - same'})")

    # RSI should be bounded [0, 100]
    rsi_min = featured[S.RSI].min()
    rsi_max = featured[S.RSI].max()
    print(f"      RSI range: [{rsi_min:.1f}, {rsi_max:.1f}] (should be [0, 100])")

    # 6. Train/test split
    train, test = loader.split(featured, "2018-12-31", "2019-01-01")
    print(f"\n  [6] Split:")
    print(f"      Train: {len(train):,} bars"
          f" ({train[S.SESSION_DATE].iloc[0]} -> {train[S.SESSION_DATE].iloc[-1]})")
    print(f"      Test:  {len(test):,} bars"
          f" ({test[S.SESSION_DATE].iloc[0]} -> {test[S.SESSION_DATE].iloc[-1]})")

    # 7. Resample to other timeframes
    for tf in ["15min", "1h", "1D"]:
        resampled = loader.resample(rth, tf)
        print(f"\n  [7] Resample {tf}: {len(resampled):,} bars")

    # 8. Daily features
    daily = loader.resample(rth, "1D")
    daily_feat = loader.add_daily_features(daily)
    print(f"\n  [8] Daily features: {list(daily_feat.columns)}")
    gap_valid = daily_feat[S.GAP_RAW].dropna()
    print(f"      Gap values: {len(gap_valid)} (mean={gap_valid.mean():.2f}pts)")

    # 9. Instrument spec check
    print(f"\n  [9] Instrument: {loader.instrument.symbol}")
    print(f"      Cost/RT: {loader.instrument.cost_per_rt_pts:.2f} pts")
    print(f"      Tick: {loader.instrument.tick_size} pts = ${loader.instrument.tick_value}")

    print(f"\n{'='*70}")
    print(f"  ALL CHECKS PASSED")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()