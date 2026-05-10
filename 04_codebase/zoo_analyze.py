#!/usr/bin/env python3
"""
Zoo Analyzer
=============
Query and analyze the strategy zoo database.

Usage:
    # Overview
    python zoo_analyze.py

    # List all tests of a specific strategy
    python zoo_analyze.py --strategy rsi_meanrev

    # Show only survivors (passed quality filters)
    python zoo_analyze.py --survivors

    # Export to CSV
    python zoo_analyze.py --export results.csv

    # Show raw records
    python zoo_analyze.py --strategy fomc_drift --raw
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

from src.zoo.database import ZooDatabase


def print_strategy_history(df: pd.DataFrame, strategy_name: str):
    """Print all tests for a given strategy."""
    sub = df[df["strategy_name"] == strategy_name].sort_values("timestamp")
    if sub.empty:
        print(f"No tests found for strategy: {strategy_name}")
        return

    print(f"\n{'='*70}")
    print(f"  HISTORY: {strategy_name} ({len(sub)} tests)")
    print(f"{'='*70}")

    for _, row in sub.iterrows():
        ts = row["timestamp"][:19]
        verdict = row["verdict"]
        dsr = row["dsr"]
        pf = row["oos_profit_factor"]
        n = row["n_oos_trades"]
        sharpe = row["oos_sharpe_ann"]
        params = row["best_params_str"]
        cost_notes = row.get("notes", "")[:50]

        print(f"\n  {ts}  verdict={verdict}")
        print(f"    DSR={dsr:+.3f}  PF={pf:.3f}  Sharpe={sharpe:+.3f}  n={n}")
        print(f"    Best params: {params}")
        print(f"    Notes: {cost_notes}")


def print_all_overview(db: ZooDatabase):
    """Overview of entire zoo."""
    print(db.summary())

    df = db.to_dataframe()
    if df.empty:
        return

    print(f"\n\n  STRATEGY RANKING BY BEST DSR:")
    print(f"  {'-'*70}")
    by_strategy = db.summary_by_strategy()
    print(by_strategy.to_string())


def print_survivors(db: ZooDatabase):
    """Print only strategies that pass quality filters."""
    survivors = db.find_survivors()
    if survivors.empty:
        print("No survivors in zoo yet. Quality filters:")
        print("  - DSR >= 1.0")
        print("  - Profit factor >= 1.25")
        print("  - OOS trades >= 30")
        print("  - Max drawdown <= 400 pts")
        print("  - Mean P&L > 0")
        return

    print(f"\n{'='*70}")
    print(f"  SURVIVORS ({len(survivors)})")
    print(f"{'='*70}\n")

    for _, row in survivors.iterrows():
        print(f"  Strategy: {row['strategy_name']}")
        print(f"    Verdict:  {row['verdict']}")
        print(f"    DSR:      {row['dsr']:+.3f}  ({row['dsr_interpretation']})")
        print(f"    PSR:      {row['psr']:.3f}")
        print(f"    PF:       {row['oos_profit_factor']:.3f}")
        print(f"    Sharpe:   {row['oos_sharpe_ann']:+.3f}")
        print(f"    Total PnL: {row['oos_total_pnl']:+.1f} pts")
        print(f"    Max DD:   {row['oos_max_drawdown']:.1f} pts")
        print(f"    Trades:   {row['n_oos_trades']} ({row['oos_win_rate']*100:.1f}% WR)")
        print(f"    Params:   {row['best_params_str']}")
        print()


def print_raw(db: ZooDatabase, strategy_name: str = None):
    """Print raw JSON records."""
    records = db.load()
    if strategy_name:
        records = [r for r in records if r.get("strategy_name") == strategy_name]

    for r in records:
        print(json.dumps(r, indent=2, default=str))
        print("---")


def main():
    parser = argparse.ArgumentParser(description="Zoo analysis")
    parser.add_argument("--zoo-path", default=None,
                        help="Path to zoo JSONL")
    parser.add_argument("--strategy", help="Filter to specific strategy")
    parser.add_argument("--survivors", action="store_true",
                        help="Show only passing strategies")
    parser.add_argument("--export", help="Export all records to CSV")
    parser.add_argument("--raw", action="store_true",
                        help="Print raw JSON records")
    args = parser.parse_args()

    project_root = THIS_DIR.parent
    default_zoo = project_root / "05_backtests" / "zoo.jsonl"
    zoo_path = args.zoo_path or str(default_zoo)

    db = ZooDatabase(zoo_path)

    if args.export:
        df = db.to_dataframe()
        df.to_csv(args.export, index=False)
        print(f"Exported {len(df)} records to {args.export}")
        return

    if args.raw:
        print_raw(db, args.strategy)
        return

    if args.survivors:
        print_survivors(db)
        return

    if args.strategy:
        df = db.to_dataframe()
        if df.empty:
            print("Zoo is empty.")
            return
        print_strategy_history(df, args.strategy)
        return

    print_all_overview(db)


if __name__ == "__main__":
    main()