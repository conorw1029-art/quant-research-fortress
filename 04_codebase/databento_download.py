#!/usr/bin/env python3
"""
Databento Multi-Market Downloader
===================================
Downloads 1-min OHLCV continuous front-month data for all new markets.
Run one symbol at a time to track costs individually.

Usage:
  # Check cost first (dry run):
  python databento_download.py --symbol CL --cost-only

  # Download:
  python databento_download.py --symbol CL

  # Download all (will prompt before each):
  python databento_download.py --all

Requires: pip install databento
API key: set DATABENTO_KEY env var or pass --key
"""

import argparse
import os
import sys
from datetime import datetime

try:
    import databento as db
except ImportError:
    print("ERROR: pip install databento")
    sys.exit(1)


DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
STYPE_IN = "continuous"
START_DATE = "2010-06-06"
END_DATE = datetime.now().strftime("%Y-%m-%d")

# Symbol -> Databento continuous symbol
SYMBOLS = {
    "YM":  "YM.c.0",
    "CL":  "CL.c.0",
    "NG":  "NG.c.0",
    "GC":  "GC.c.0",
    "SI":  "SI.c.0",
    "6E":  "6E.c.0",
    "6B":  "6B.c.0",
    "6J":  "6J.c.0",
    "6C":  "6C.c.0",
    "6A":  "6A.c.0",
    "6S":  "6S.c.0",
    "ZB":  "ZB.c.0",
    "ZN":  "ZN.c.0",
    "ZF":  "ZF.c.0",
    "MBT": "MBT.c.0",
    "ZC":  "ZC.c.0",
    "ZW":  "ZW.c.0",
    "ZS":  "ZS.c.0",
}

# Already downloaded -- skip these
ALREADY_HAVE = {"ES", "NQ", "RTY"}

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "01_data", "raw"
)


def get_client(api_key: str) -> db.Historical:
    return db.Historical(api_key)


def check_cost(client, symbol_key: str) -> float:
    """Check download cost in USD without downloading."""
    dbn_symbol = SYMBOLS[symbol_key]
    cost = client.metadata.get_cost(
        dataset=DATASET,
        symbols=[dbn_symbol],
        stype_in=STYPE_IN,
        schema=SCHEMA,
        start=START_DATE,
        end=END_DATE,
    )
    return cost


def download(client, symbol_key: str, output_dir: str) -> str:
    """Download and save as CSV. Returns output path."""
    dbn_symbol = SYMBOLS[symbol_key]
    output_path = os.path.join(output_dir, f"{symbol_key}_1min.csv")

    print(f"  Downloading {symbol_key} ({dbn_symbol})...")
    print(f"  Date range: {START_DATE} to {END_DATE}")
    print(f"  Output: {output_path}")

    data = client.timeseries.get_range(
        dataset=DATASET,
        symbols=[dbn_symbol],
        stype_in=STYPE_IN,
        schema=SCHEMA,
        start=START_DATE,
        end=END_DATE,
    )

    data.to_csv(output_path)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  Saved: {size_mb:.1f} MB")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Download Databento futures data")
    parser.add_argument("--symbol", type=str, help="Single symbol to download (e.g. CL)")
    parser.add_argument("--all", action="store_true", help="Download all symbols")
    parser.add_argument("--cost-only", action="store_true", help="Check cost without downloading")
    parser.add_argument("--key", type=str, default=None, help="Databento API key")
    parser.add_argument("--output-dir", type=str, default=None, help="Override output directory")
    args = parser.parse_args()

    api_key = args.key or os.environ.get("DATABENTO_KEY")
    if not api_key:
        print("ERROR: Set DATABENTO_KEY env var or pass --key")
        sys.exit(1)

    out_dir = args.output_dir or OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    client = get_client(api_key)

    if args.symbol:
        symbols_to_do = [args.symbol.upper()]
    elif args.all:
        symbols_to_do = [k for k in SYMBOLS if k not in ALREADY_HAVE]
    else:
        print("Specify --symbol X or --all")
        print(f"Available: {', '.join(sorted(SYMBOLS.keys()))}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  DATABENTO MULTI-MARKET DOWNLOAD")
    print(f"  Dataset: {DATASET} | Schema: {SCHEMA}")
    print(f"  Range: {START_DATE} to {END_DATE}")
    print(f"{'='*60}\n")

    total_cost = 0.0
    for sym in symbols_to_do:
        if sym not in SYMBOLS:
            print(f"  SKIP: {sym} not in symbol list")
            continue

        cost = check_cost(client, sym)
        total_cost += cost
        print(f"  {sym:<4s} ({SYMBOLS[sym]:<8s}): ${cost:.2f}")

        if not args.cost_only:
            resp = input(f"    Download {sym}? [y/N] ").strip().lower()
            if resp == 'y':
                download(client, sym, out_dir)
                print()
            else:
                print(f"    Skipped.\n")

    print(f"\n  {'ESTIMATED' if args.cost_only else ''} TOTAL COST: ${total_cost:.2f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()