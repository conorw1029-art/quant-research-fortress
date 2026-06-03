#!/usr/bin/env python3
"""
tick_databento_download_manager.py
===================================
Safe Databento download manager with cost gates, atomic file placement,
manifest logging, and post-download validation.

DEFAULT MODE: dry-run. Estimates cost only. Never downloads.

To actually download, you MUST explicitly pass:
    --approve-download --max-cost-usd <amount>

RULES:
    - Never overwrites existing raw files
    - Writes to a temp file first, then atomically moves to final location
    - Every completed download is logged to MANIFEST.jsonl
    - Validates file size and first record after download
    - Aborts if estimated cost exceeds --max-cost-usd

Usage:
    # Check cost only (default dry-run):
    python tick_databento_download_manager.py \\
        --symbols ES --schema trades --start 2024-01-02 --end 2024-01-03

    # Approve a download (must also set --max-cost-usd):
    python tick_databento_download_manager.py \\
        --symbols ES --schema trades \\
        --start 2024-01-02 --end 2024-01-03 \\
        --approve-download --max-cost-usd 5.00

Requirements:
    pip install databento
    DATABENTO_API_KEY environment variable must be set.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
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

COST_PER_1K_MSGS_FALLBACK = {
    "trades": 0.002,
    "mbp-1":  0.003,
    "mbp-10": 0.020,
    "mbo":    0.080,
}

# Root paths
PROJECT_ROOT = Path(__file__).parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "01_data" / "raw"
MANIFEST_PATH = RAW_DATA_DIR / "MANIFEST.jsonl"

TOTAL_BUDGET_USD = 125.00
SPENT_TO_DATE_USD = 120.14
REMAINING_BUDGET_USD = TOTAL_BUDGET_USD - SPENT_TO_DATE_USD


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Read API key from environment. Exit cleanly if missing."""
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY environment variable is not set.")
        print("       Set it with: set DATABENTO_API_KEY=your_key_here  (Windows)")
        print("       Or:          export DATABENTO_API_KEY=your_key_here  (Linux/Mac)")
        sys.exit(1)
    return key


def build_client(api_key: str) -> db.Historical:
    return db.Historical(key=api_key)


def resolve_output_path(symbol: str, schema: str, start: str, end: str) -> Path:
    """
    Build the canonical output path for a raw download.
    Pattern: RAW_DATA_DIR / {SYMBOL} / {schema} / {start}_{end}.dbn.zst
    """
    safe_start = start.replace(":", "").replace(" ", "")
    safe_end = end.replace(":", "").replace(" ", "")
    return RAW_DATA_DIR / symbol.upper() / schema / f"{safe_start}_{safe_end}.dbn.zst"


def get_estimated_cost(client: db.Historical, dbn_symbol: str, schema: str, start: str, end: str) -> tuple:
    """
    Returns (record_count, estimated_cost_usd, api_cost_available).
    Falls back to per-message rate estimate if the cost endpoint fails.
    """
    # Get record count
    try:
        count = client.metadata.get_record_count(
            dataset=DATASET,
            symbols=[dbn_symbol],
            schema=schema,
            start=start,
            end=end,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to get record count from Databento: {exc}") from exc

    # Try API cost endpoint
    try:
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[dbn_symbol],
            schema=schema,
            start=start,
            end=end,
        )
        return count, float(cost), True
    except Exception:
        fallback = (count / 1000.0) * COST_PER_1K_MSGS_FALLBACK.get(schema, 0.0)
        return count, round(fallback, 4), False


def append_manifest(entry: dict) -> None:
    """Append one JSON line to the manifest file."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def validate_download(file_path: Path, schema: str) -> tuple:
    """
    Validate a downloaded DBN file.

    Returns (ok: bool, message: str).
    """
    # Check file exists and is non-empty
    if not file_path.exists():
        return False, f"File does not exist: {file_path}"

    file_size = file_path.stat().st_size
    if file_size == 0:
        return False, f"File is empty (0 bytes): {file_path}"

    if file_size < 256:
        return False, f"File suspiciously small ({file_size} bytes): {file_path}"

    # Try to open and read first record
    try:
        store = db.DBNStore.from_file(str(file_path))
        df = store.to_df()
        if df is None or len(df) == 0:
            return False, "File opened but DataFrame is empty."
        return True, f"Validated: {len(df):,} records readable, {file_size / 1024 / 1024:.2f} MB"
    except Exception as exc:
        return False, f"Failed to read file: {exc}"


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def download_symbol(
    client: db.Historical,
    symbol: str,
    schema: str,
    start: str,
    end: str,
    max_cost_usd: float,
    dry_run: bool,
) -> dict:
    """
    Handle estimation and optionally download for a single symbol.

    Returns a result dict with outcome details.
    """
    dbn_symbol = SYMBOL_MAP.get(symbol.upper())
    if not dbn_symbol:
        return {
            "symbol": symbol,
            "status": "error",
            "message": f"Symbol '{symbol}' not in SYMBOL_MAP.",
        }

    output_path = resolve_output_path(symbol, schema, start, end)

    result = {
        "symbol": symbol.upper(),
        "dbn_symbol": dbn_symbol,
        "schema": schema,
        "start": start,
        "end": end,
        "output_path": str(output_path),
        "status": None,
        "message": None,
        "record_count": None,
        "estimated_cost_usd": None,
        "api_cost_available": None,
        "validated": False,
        "file_size_bytes": None,
    }

    print(f"\n  [{symbol}] Querying metadata...", end=" ", flush=True)
    try:
        count, cost, api_cost = get_estimated_cost(client, dbn_symbol, schema, start, end)
    except RuntimeError as exc:
        result["status"] = "error"
        result["message"] = str(exc)
        print(f"ERROR\n    {exc}")
        return result

    result["record_count"] = count
    result["estimated_cost_usd"] = cost
    result["api_cost_available"] = api_cost

    cost_method = "API" if api_cost else "Fallback estimate"
    print(f"done\n    Records: {count:,}  |  Est. cost: ${cost:.4f}  ({cost_method})")

    # Cost gate
    if cost > max_cost_usd:
        result["status"] = "aborted_cost"
        result["message"] = (
            f"Estimated cost ${cost:.4f} exceeds --max-cost-usd ${max_cost_usd:.2f}. Aborted."
        )
        print(f"  [{symbol}] ABORTED: {result['message']}")
        return result

    # Budget gate
    if cost > REMAINING_BUDGET_USD:
        result["status"] = "aborted_budget"
        result["message"] = (
            f"Estimated cost ${cost:.4f} exceeds remaining budget ${REMAINING_BUDGET_USD:.2f}."
        )
        print(f"  [{symbol}] ABORTED: {result['message']}")
        return result

    # Dry-run mode — stop here
    if dry_run:
        result["status"] = "dry_run"
        result["message"] = "Dry run — no download performed. Pass --approve-download to download."
        print(f"  [{symbol}] DRY RUN — would download to: {output_path}")
        return result

    # Check if output file already exists
    if output_path.exists():
        result["status"] = "skipped_exists"
        result["message"] = f"File already exists, skipping: {output_path}"
        print(f"  [{symbol}] SKIPPED: {result['message']}")
        return result

    # Create output directory
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Download to temp file, then atomic move
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".dbn.zst.tmp", dir=output_path.parent)
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    print(f"  [{symbol}] Downloading to temp file...", flush=True)
    download_started_at = datetime.now(timezone.utc).isoformat()

    try:
        client.timeseries.get_range(
            dataset=DATASET,
            symbols=[dbn_symbol],
            schema=schema,
            start=start,
            end=end,
            path=str(tmp_path),
        )
    except Exception as exc:
        # Clean up temp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        result["status"] = "error"
        result["message"] = f"Download failed: {exc}"
        print(f"  [{symbol}] DOWNLOAD ERROR: {exc}")
        return result

    # Atomic move: temp → final
    try:
        shutil.move(str(tmp_path), str(output_path))
    except OSError as exc:
        result["status"] = "error"
        result["message"] = f"Failed to move temp file to final location: {exc}"
        print(f"  [{symbol}] MOVE ERROR: {exc}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return result

    file_size = output_path.stat().st_size
    result["file_size_bytes"] = file_size
    print(f"  [{symbol}] Download complete. Size: {file_size / 1024 / 1024:.2f} MB")

    # Post-download validation
    print(f"  [{symbol}] Validating...", end=" ", flush=True)
    ok, validation_msg = validate_download(output_path, schema)
    result["validated"] = ok
    print(validation_msg if ok else f"FAILED — {validation_msg}")

    if ok:
        result["status"] = "success"
        result["message"] = validation_msg
    else:
        result["status"] = "validation_failed"
        result["message"] = validation_msg

    # Write manifest entry
    manifest_entry = {
        "timestamp": download_started_at,
        "symbol": symbol.upper(),
        "dbn_symbol": dbn_symbol,
        "schema": schema,
        "start": start,
        "end": end,
        "file_path": str(output_path),
        "record_count": count,
        "estimated_cost_usd": cost,
        "actual_cost_usd": None,   # populate manually from Databento billing dashboard
        "file_size_bytes": file_size,
        "validated": ok,
        "downloaded_by": "tick_databento_download_manager.py",
        "notes": "" if ok else f"Validation failed: {validation_msg}",
    }
    append_manifest(manifest_entry)
    print(f"  [{symbol}] Manifest updated: {MANIFEST_PATH}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Safe Databento download manager.\n"
            "Default mode is DRY RUN — estimates cost only.\n"
            "Use --approve-download to actually download data."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run — see cost estimate only:
  python tick_databento_download_manager.py \\
      --symbols ES --schema trades --start 2024-01-02 --end 2024-01-03

  # Approved download (must set max cost):
  python tick_databento_download_manager.py \\
      --symbols ES --schema trades --start 2024-01-02 --end 2024-01-03 \\
      --approve-download --max-cost-usd 5.00
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
        "--approve-download",
        action="store_true",
        default=False,
        help="Actually download data. Without this flag, only cost estimation is performed.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.0,
        help=(
            "Maximum allowable estimated cost in USD. Required when --approve-download is set. "
            "Download is aborted if estimated cost exceeds this value."
        ),
    )

    args = parser.parse_args()

    dry_run = not args.approve_download

    # Validate --max-cost-usd when downloading
    if args.approve_download and args.max_cost_usd <= 0.0:
        print("ERROR: --approve-download requires --max-cost-usd <positive number>.")
        print("       Example: --approve-download --max-cost-usd 5.00")
        sys.exit(1)

    # Effective max cost in dry-run: use a large number so estimation always runs
    max_cost = args.max_cost_usd if args.approve_download else 1_000_000.0

    # Parse symbols
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: --symbols must contain at least one symbol.")
        sys.exit(1)

    unknown = [s for s in symbols if s not in SYMBOL_MAP]
    if unknown:
        print(f"WARNING: Unknown symbols (will be skipped): {', '.join(unknown)}")
        symbols = [s for s in symbols if s in SYMBOL_MAP]
        if not symbols:
            print("ERROR: No valid symbols remain.")
            sys.exit(1)

    # Warn about mbo
    if args.schema == "mbo":
        print("\n  WARNING: MBO schema is extremely expensive.")
        print("           Cost estimates may be inaccurate. Contact Databento for a firm quote.")
        if args.approve_download:
            resp = input("  Type 'yes' to continue with mbo download attempt: ").strip().lower()
            if resp != "yes":
                print("  Aborted by user.")
                sys.exit(0)

    print(f"\n{'='*60}")
    mode_label = "DRY RUN (cost estimate only)" if dry_run else "DOWNLOAD MODE"
    print(f"  DATABENTO DOWNLOAD MANAGER — {mode_label}")
    print(f"{'='*60}")
    print(f"  Dataset:         {DATASET}")
    print(f"  Schema:          {args.schema}")
    print(f"  Start:           {args.start}")
    print(f"  End:             {args.end}  (exclusive)")
    print(f"  Symbols:         {', '.join(symbols)}")
    if not dry_run:
        print(f"  Max cost gate:   ${args.max_cost_usd:.2f}")
    print(f"  Budget remaining: ${REMAINING_BUDGET_USD:.2f}")
    print(f"  Raw data dir:    {RAW_DATA_DIR}")
    print(f"{'='*60}")

    api_key = get_api_key()
    client = build_client(api_key)

    results = []
    total_estimated = 0.0

    for symbol in symbols:
        res = download_symbol(
            client=client,
            symbol=symbol,
            schema=args.schema,
            start=args.start,
            end=args.end,
            max_cost_usd=max_cost,
            dry_run=dry_run,
        )
        results.append(res)
        if res.get("estimated_cost_usd") is not None:
            total_estimated += res["estimated_cost_usd"]

    # Summary
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for res in results:
        status = res.get("status", "unknown").upper()
        sym = res.get("symbol", "?")
        cost = res.get("estimated_cost_usd")
        cost_str = f"${cost:.4f}" if cost is not None else "N/A"
        print(f"  {sym:<8} | Status: {status:<24} | Est. cost: {cost_str}")

    print(f"{'='*60}")
    print(f"  Total estimated cost: ${total_estimated:.4f}")

    if dry_run:
        print("\n  This was a DRY RUN. No data was downloaded.")
        print("  To download: add --approve-download --max-cost-usd <amount>")

    success_count = sum(1 for r in results if r.get("status") == "success")
    if success_count > 0:
        print(f"\n  Successfully downloaded: {success_count} file(s).")
        print(f"  Manifest: {MANIFEST_PATH}")

    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
