#!/usr/bin/env python3
"""
Databento Tick Data Downloader — L2 Phase
==========================================
Downloads tick-level trade data for L2 strategy development.

Schema options:
  trades  — every trade with aggressor side (buy/sell). Used for:
             cumulative delta, speed of tape, large print detection
  mbp-1   — top-of-book bid/ask at every update. Used for:
             spread analysis, basic order book pressure
  mbp-10  — top 10 bid/ask levels. Full order book imbalance.

Usage:
  # ALWAYS check cost before downloading:
  python tick_data_downloader.py --cost-only

  # Download single symbol:
  python tick_data_downloader.py --symbol GC --schema trades

  # Download all with confirmation prompts:
  python tick_data_downloader.py --all --schema trades

Requires: pip install databento
API key: set DATABENTO_KEY env var or pass --key
"""

import argparse
import os
import sys
from datetime import datetime

# Fix Windows console encoding for any non-ASCII chars
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import databento as db
except ImportError:
    print("ERROR: pip install databento")
    sys.exit(1)

DATASET   = "GLBX.MDP3"
STYPE_IN  = "continuous"

# Date range — 5 years is enough for meaningful WFO folds (3-4 folds)
# Shorter = cheaper; adjust if budget is tight
START_DATE = "2020-01-01"
from datetime import timedelta
END_DATE   = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# Priority order: Gold/Silver first (cheaper, proven edge), then indices
SYMBOLS = {
    "GC":  "GC.c.0",   # Gold continuous — highest priority
    "SI":  "SI.c.0",   # Silver continuous
    "ES":  "ES.c.0",   # S&P 500 E-mini — expensive, check cost first
    "NQ":  "NQ.c.0",   # Nasdaq E-mini — expensive, check cost first
}

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "01_data", "tick"
)


def get_client(api_key: str) -> db.Historical:
    return db.Historical(api_key)


def check_cost(client, symbol_key: str, schema: str) -> float:
    return client.metadata.get_cost(
        dataset=DATASET,
        symbols=[SYMBOLS[symbol_key]],
        stype_in=STYPE_IN,
        schema=schema,
        start=START_DATE,
        end=END_DATE,
    )


def download(client, symbol_key: str, schema: str, output_dir: str) -> str:
    dbn_symbol = SYMBOLS[symbol_key]
    fname = f"{symbol_key}_tick_{schema.replace('-', '')}.csv"
    output_path = os.path.join(output_dir, fname)

    print(f"  Downloading {symbol_key} ({dbn_symbol}) schema={schema}...")
    print(f"  Date range: {START_DATE} to {END_DATE}")
    print(f"  Output: {output_path}")

    data = client.timeseries.get_range(
        dataset=DATASET,
        symbols=[dbn_symbol],
        stype_in=STYPE_IN,
        schema=schema,
        start=START_DATE,
        end=END_DATE,
    )

    data.to_csv(output_path)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  Saved: {size_mb:.1f} MB  →  {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Download Databento tick data for L2 strategies")
    parser.add_argument("--symbol", type=str, help="Single symbol: GC, SI, ES, NQ")
    parser.add_argument("--all", action="store_true", help="Check/download all symbols")
    parser.add_argument("--schema", type=str, default="trades",
                        choices=["trades", "mbp-1", "mbp-10"],
                        help="Data schema (default: trades)")
    parser.add_argument("--cost-only", action="store_true", help="Check costs, don't download")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts, download immediately")
    parser.add_argument("--start", type=str, default=None, help="Override start date YYYY-MM-DD")
    parser.add_argument("--key", type=str, default=None, help="Databento API key")
    args = parser.parse_args()

    global START_DATE
    if args.start:
        START_DATE = args.start

    api_key = args.key or os.environ.get("DATABENTO_KEY")
    if not api_key:
        print("ERROR: Set DATABENTO_KEY env var or pass --key <your-key>")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    client = get_client(api_key)

    if args.symbol:
        symbols_to_do = [args.symbol.upper()]
    elif args.all:
        symbols_to_do = list(SYMBOLS.keys())
    else:
        parser.print_help()
        sys.exit(1)

    schema = args.schema
    budget_eur = 250.0
    usd_per_eur = 1.09  # approximate
    budget_usd = budget_eur * usd_per_eur

    print(f"\n{'='*65}")
    print(f"  DATABENTO TICK DATA — COST CHECK")
    print(f"  Dataset: {DATASET}  Schema: {schema}")
    print(f"  Range:   {START_DATE} to {END_DATE}")
    print(f"  Budget:  EUR{budget_eur:.0f} (~${budget_usd:.0f})")
    print(f"{'='*65}\n")

    total_cost_usd = 0.0
    costs = {}

    for sym in symbols_to_do:
        if sym not in SYMBOLS:
            print(f"  SKIP: {sym} — not in symbol list")
            continue
        try:
            cost = check_cost(client, sym, schema)
            costs[sym] = cost
            total_cost_usd += cost
            budget_pct = cost / budget_usd * 100
            flag = "  *** EXPENSIVE ***" if cost > budget_usd * 0.4 else ""
            print(f"  {sym:<4s}  ${cost:>8.2f}  ({budget_pct:.0f}% of budget){flag}")
        except Exception as e:
            print(f"  {sym:<4s}  ERROR: {e}")
            costs[sym] = None

    print(f"\n  TOTAL: ${total_cost_usd:.2f}  vs budget ${budget_usd:.2f}")

    if total_cost_usd > budget_usd:
        print(f"  *** OVER BUDGET — need to prioritize ***")
        affordable = {s: c for s, c in costs.items() if c is not None and c <= budget_usd}
        cumulative = 0
        within_budget = []
        for sym in ["GC", "SI", "ES", "NQ"]:
            if sym in costs and costs[sym] is not None:
                if cumulative + costs[sym] <= budget_usd:
                    cumulative += costs[sym]
                    within_budget.append(sym)
        print(f"  Recommended subset within budget: {within_budget}  (${cumulative:.2f})")
    else:
        print(f"  All within budget [OK]")

    print(f"\n{'='*65}")

    if args.cost_only:
        print("\n  Dry run complete. Re-run without --cost-only to download.")
        return

    # Download with confirmation
    spent = 0.0
    for sym in symbols_to_do:
        if sym not in costs or costs[sym] is None:
            continue
        cost = costs[sym]
        remaining = budget_usd - spent
        print(f"\n  {sym}: ${cost:.2f}  (remaining budget: ${remaining:.2f})")
        if cost > remaining:
            print(f"  SKIP: would exceed budget")
            continue
        if args.yes:
            resp = "y"
        else:
            resp = input(f"  Download {sym} (schema={schema})? [y/N] ").strip().lower()
        if resp == "y":
            download(client, sym, schema, OUTPUT_DIR)
            spent += cost
            print(f"  Spent so far: ${spent:.2f}")
        else:
            print(f"  Skipped.")

    print(f"\n  Total spent: ${spent:.2f}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
