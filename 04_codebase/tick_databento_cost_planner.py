#!/usr/bin/env python3
"""
tick_databento_cost_planner.py
==============================
Estimates Databento download costs without downloading anything.

This script is PERMANENTLY in dry-run mode. It never downloads data.
It only calls the Databento metadata endpoints to estimate message count and cost.

Usage:
    # Estimate cost for ES mbp-10 for one day:
    python tick_databento_cost_planner.py --symbols ES --schema mbp-10 \
        --start 2024-01-02 --end 2024-01-03

    # Estimate multiple symbols:
    python tick_databento_cost_planner.py --symbols ES,NQ,CL --schema trades \
        --start 2024-01-01 --end 2024-07-01

    # Save estimate to JSON:
    python tick_databento_cost_planner.py --symbols ES --schema mbp-10 \
        --start 2024-01-02 --end 2024-01-03 --save

Requirements:
    pip install databento
    DATABENTO_API_KEY environment variable must be set.

IMPORTANT:
    This script never downloads data. It is safe to run at any time.
    It only calls metadata.get_record_count() and metadata.get_cost().
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed. Run: pip install databento")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET = "GLBX.MDP3"

# Continuous front-month symbol format for each root symbol
SYMBOL_MAP = {
    "ES":  "ES.c.0",
    "NQ":  "NQ.c.0",
    "GC":  "GC.c.0",
    "SI":  "SI.c.0",
    "CL":  "CL.c.0",
    "MES": "MES.c.0",
    "MNQ": "MNQ.c.0",
    "MGC": "MGC.c.0",
    "SIL": "SIL.c.0",
    "MCL": "MCL.c.0",
    "ZN":  "ZN.c.0",
    "ZB":  "ZB.c.0",
    "6E":  "6E.c.0",
    "6J":  "6J.c.0",
    "6B":  "6B.c.0",
    "RTY": "RTY.c.0",
    "M2K": "M2K.c.0",
    "YM":  "YM.c.0",
    "MYM": "MYM.c.0",
}

VALID_SCHEMAS = ["trades", "mbp-1", "mbp-10", "mbo"]

# Approximate price per 1000 messages by schema (mid-range estimates)
# Use these as fallback if the API cost endpoint returns zero or fails.
COST_PER_1K_MSGS = {
    "trades": 0.002,
    "mbp-1":  0.003,
    "mbp-10": 0.020,
    "mbo":    0.080,   # rough estimate — actual varies, contact Databento
}

OUTPUT_DIR = Path(__file__).parent.parent / "08_docs" / "cost_estimates"

# Budget tracking
TOTAL_BUDGET_USD = 125.00
SPENT_TO_DATE_USD = 120.14
REMAINING_BUDGET_USD = TOTAL_BUDGET_USD - SPENT_TO_DATE_USD


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Read API key from environment. Exit if missing."""
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY environment variable is not set.")
        print("       Set it with: set DATABENTO_API_KEY=your_key_here  (Windows)")
        print("       Or:          export DATABENTO_API_KEY=your_key_here  (Linux/Mac)")
        sys.exit(1)
    return key


def build_client(api_key: str) -> db.Historical:
    """Instantiate the Databento Historical client."""
    return db.Historical(key=api_key)


def estimate_cost(
    client: db.Historical,
    symbol: str,
    schema: str,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Call Databento metadata endpoints to get record count and cost estimate.

    Returns a dict with:
        symbol, dbn_symbol, schema, start, end,
        record_count, estimated_cost_usd, cost_per_1k_msgs,
        api_cost_available (bool), fallback_used (bool)
    """
    dbn_symbol = SYMBOL_MAP.get(symbol.upper())
    if not dbn_symbol:
        return {
            "symbol": symbol,
            "dbn_symbol": None,
            "schema": schema,
            "start": start_date,
            "end": end_date,
            "record_count": None,
            "estimated_cost_usd": None,
            "cost_per_1k_msgs": None,
            "api_cost_available": False,
            "fallback_used": False,
            "error": f"Symbol '{symbol}' not in SYMBOL_MAP. Add it if needed.",
        }

    result = {
        "symbol": symbol.upper(),
        "dbn_symbol": dbn_symbol,
        "schema": schema,
        "start": start_date,
        "end": end_date,
        "record_count": None,
        "estimated_cost_usd": None,
        "cost_per_1k_msgs": COST_PER_1K_MSGS.get(schema, 0.0),
        "api_cost_available": False,
        "fallback_used": False,
        "error": None,
    }

    # Step 1: Get record count
    try:
        count = client.metadata.get_record_count(
            dataset=DATASET,
            symbols=[dbn_symbol],
            schema=schema,
            start=start_date,
            end=end_date,
        )
        result["record_count"] = count
    except Exception as exc:
        result["error"] = f"get_record_count failed: {exc}"
        return result

    # Step 2: Try to get cost from the API directly
    try:
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[dbn_symbol],
            schema=schema,
            start=start_date,
            end=end_date,
        )
        result["estimated_cost_usd"] = float(cost)
        result["api_cost_available"] = True
    except Exception:
        # Fallback: estimate from record count × price per 1K
        fallback_cost = (result["record_count"] / 1000.0) * COST_PER_1K_MSGS.get(schema, 0.0)
        result["estimated_cost_usd"] = round(fallback_cost, 4)
        result["fallback_used"] = True

    return result


def format_table(estimates: list, total_cost: float) -> str:
    """Format estimates as a readable ASCII table."""
    lines = []
    header = f"{'Symbol':<8} {'Schema':<10} {'DBN Symbol':<12} {'Records':>12} {'Est. Cost':>12} {'Method':<12}"
    sep = "-" * len(header)
    lines.append(sep)
    lines.append(header)
    lines.append(sep)

    for e in estimates:
        if e.get("error"):
            lines.append(f"{'':8} ERROR: {e['symbol']} — {e['error']}")
            continue

        record_str = f"{e['record_count']:,}" if e["record_count"] is not None else "N/A"
        cost_str = f"${e['estimated_cost_usd']:.4f}" if e["estimated_cost_usd"] is not None else "N/A"
        method = "API" if e.get("api_cost_available") else "Fallback"
        lines.append(
            f"{e['symbol']:<8} {e['schema']:<10} {e['dbn_symbol']:<12} "
            f"{record_str:>12} {cost_str:>12} {method:<12}"
        )

    lines.append(sep)
    lines.append(f"{'TOTAL ESTIMATED COST':>44} ${total_cost:.4f}")
    lines.append(sep)
    return "\n".join(lines)


def print_budget_warning(total_cost: float) -> None:
    """Print budget context and warning if cost exceeds remaining budget."""
    print(f"\n  Budget Status:")
    print(f"    Total budget:       ${TOTAL_BUDGET_USD:.2f}")
    print(f"    Spent to date:      ${SPENT_TO_DATE_USD:.2f}")
    print(f"    Remaining:          ${REMAINING_BUDGET_USD:.2f}")
    print(f"    This estimate:      ${total_cost:.4f}")

    if total_cost > REMAINING_BUDGET_USD:
        print(f"\n  WARNING: Estimated cost ${total_cost:.4f} exceeds remaining budget "
              f"${REMAINING_BUDGET_USD:.2f}.")
        print("           A new budget allocation is required before this download can proceed.")
    elif total_cost > 0.0:
        remaining_after = REMAINING_BUDGET_USD - total_cost
        print(f"    Remaining after:    ${remaining_after:.2f}")
        if remaining_after < 5.00:
            print("  WARNING: Budget will be nearly exhausted after this purchase.")


def save_estimate(estimates: list, total_cost: float, args: argparse.Namespace) -> Path:
    """Save estimate results to a JSON file in 08_docs/cost_estimates/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp_str}_estimate.json"
    output_path = OUTPUT_DIR / filename

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": {
            "symbols": args.symbols,
            "schema": args.schema,
            "start": args.start,
            "end": args.end,
        },
        "budget_status": {
            "total_budget_usd": TOTAL_BUDGET_USD,
            "spent_to_date_usd": SPENT_TO_DATE_USD,
            "remaining_usd": REMAINING_BUDGET_USD,
        },
        "total_estimated_cost_usd": total_cost,
        "estimates": estimates,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Estimate Databento download costs without downloading any data.\n"
            "This script is permanently in dry-run mode."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tick_databento_cost_planner.py --symbols ES --schema mbp-10 --start 2024-01-02 --end 2024-01-03
  python tick_databento_cost_planner.py --symbols ES,NQ,GC --schema trades --start 2024-01-01 --end 2024-07-01 --save
        """,
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols (e.g., 'ES,NQ,GC')",
    )
    parser.add_argument(
        "--schema",
        required=True,
        choices=VALID_SCHEMAS,
        help="Databento schema: trades, mbp-1, mbp-10, or mbo",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date inclusive (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End date exclusive (YYYY-MM-DD). The final day is NOT included.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help="Save estimate to JSON in 08_docs/cost_estimates/",
    )

    args = parser.parse_args()

    # Parse and validate symbols
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: --symbols must contain at least one symbol.")
        sys.exit(1)

    unknown = [s for s in symbols if s not in SYMBOL_MAP]
    if unknown:
        print(f"WARNING: The following symbols are not in SYMBOL_MAP and will be skipped: {unknown}")
        print(f"         Known symbols: {', '.join(sorted(SYMBOL_MAP.keys()))}")
        symbols = [s for s in symbols if s in SYMBOL_MAP]
        if not symbols:
            print("ERROR: No valid symbols remain after filtering.")
            sys.exit(1)

    # Warn about mbo
    if args.schema == "mbo":
        print("\n  WARNING: MBO schema is extremely expensive.")
        print("           Contact Databento support for a firm quote before running this estimate.")
        print("           Cost estimates for MBO may be inaccurate.\n")

    print(f"\n{'='*60}")
    print("  DATABENTO COST PLANNER  (DRY RUN — NO DOWNLOAD)")
    print(f"{'='*60}")
    print(f"  Dataset:   {DATASET}")
    print(f"  Schema:    {args.schema}")
    print(f"  Start:     {args.start}")
    print(f"  End:       {args.end}  (exclusive)")
    print(f"  Symbols:   {', '.join(symbols)}")
    print(f"{'='*60}\n")

    api_key = get_api_key()
    client = build_client(api_key)

    estimates = []
    total_cost = 0.0

    for symbol in symbols:
        print(f"  Querying metadata for {symbol} ({SYMBOL_MAP[symbol]})...", end=" ", flush=True)
        result = estimate_cost(client, symbol, args.schema, args.start, args.end)
        estimates.append(result)
        if result.get("estimated_cost_usd") is not None:
            total_cost += result["estimated_cost_usd"]
        if result.get("error"):
            print(f"ERROR — {result['error']}")
        else:
            print("done")

    print()
    print(format_table(estimates, total_cost))
    print_budget_warning(total_cost)

    if args.save:
        saved_path = save_estimate(estimates, total_cost, args)
        print(f"\n  Estimate saved to: {saved_path}")

    print(f"\n{'='*60}")
    print("  This was a DRY RUN. No data was downloaded.")
    print("  To download: use tick_databento_download_manager.py")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
