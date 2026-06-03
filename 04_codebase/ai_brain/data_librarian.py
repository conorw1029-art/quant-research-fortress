"""
data_librarian.py — Inventory and coverage tracker for market data
===================================================================
DataLibrarian provides a read-only view of what raw and processed data
is available locally. It reads MANIFEST.jsonl records and scans bar
parquet directories.

It never downloads data, never modifies files, and never places orders.

Usage:
    from ai_brain.data_librarian import DataLibrarian

    lib = DataLibrarian(decision_log=log)
    summary = lib.get_data_summary(raw_dir=Path("01_data/raw"),
                                   bar_dir=Path("01_data/bars"))
    cov = lib.check_coverage(raw_dir, symbol="GC", schema="ohlcv",
                             required_start="2020-01-01",
                             required_end="2026-01-01")
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)


class DataLibrarian:
    """
    Provides read-only inventory of market data (raw and processed bars).

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Core interface ─────────────────────────────────────────────────────────

    def get_manifest(self, raw_dir: Path) -> List[dict]:
        """
        Read MANIFEST.jsonl from raw_dir and return all download records.

        Each record typically contains: symbol, schema, start, end,
        download_time, file_path, row_count, etc.

        Args:
            raw_dir: Root directory for raw data containing MANIFEST.jsonl.

        Returns:
            List of manifest entry dicts. Empty if file not found or unreadable.
        """
        manifest_path = raw_dir / "MANIFEST.jsonl"

        if not manifest_path.exists():
            logger.info("[DataLibrarian] No MANIFEST.jsonl found at %s", manifest_path)
            return []

        records = self._read_jsonl(manifest_path)

        self.dlog.log(
            agent="DataLibrarian",
            observation=f"Read {len(records)} manifest records from {manifest_path}.",
            recommendation="Use check_coverage to verify specific symbol/date requirements.",
            action_taken="manifest_read",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"n_records": len(records), "manifest_path": str(manifest_path)},
        )

        return records

    def check_coverage(
        self,
        raw_dir: Path,
        symbol: str,
        schema: str,
        required_start: str,
        required_end: str,
    ) -> dict:
        """
        Check whether raw data covers the required date range for a symbol.

        Args:
            raw_dir:        Directory containing MANIFEST.jsonl.
            symbol:         Symbol string (e.g. "GC", "SI").
            schema:         Data schema identifier (e.g. "ohlcv", "trades", "mbp-10").
            required_start: Required start date as "YYYY-MM-DD".
            required_end:   Required end date as "YYYY-MM-DD".

        Returns:
            {
                "covered":         bool,
                "gaps":            list[str],     # date ranges not covered
                "available_files": list[str],     # matching file paths from manifest
                "total_rows":      int,
                "actual_start":    str | None,
                "actual_end":      str | None,
            }
        """
        records = self.get_manifest(raw_dir)

        try:
            req_start = date.fromisoformat(required_start)
            req_end = date.fromisoformat(required_end)
        except ValueError as e:
            logger.error("[DataLibrarian] Invalid date format: %s", e)
            return {
                "covered": False,
                "gaps": [f"Invalid date format: {e}"],
                "available_files": [],
                "total_rows": 0,
                "actual_start": None,
                "actual_end": None,
            }

        # Filter records matching symbol and schema (case-insensitive)
        matching = [
            r for r in records
            if str(r.get("symbol", "")).upper() == symbol.upper()
            and str(r.get("schema", r.get("data_type", ""))).lower() == schema.lower()
        ]

        available_files = []
        total_rows = 0
        starts = []
        ends = []

        for rec in matching:
            fp = rec.get("file_path") or rec.get("path") or rec.get("file")
            if fp:
                available_files.append(str(fp))

            rows = rec.get("row_count") or rec.get("rows") or 0
            try:
                total_rows += int(rows)
            except (ValueError, TypeError):
                pass

            for date_key in ("start", "date_from", "start_date"):
                if date_key in rec and rec[date_key]:
                    try:
                        starts.append(date.fromisoformat(str(rec[date_key])[:10]))
                    except ValueError:
                        pass
                    break

            for date_key in ("end", "date_to", "end_date"):
                if date_key in rec and rec[date_key]:
                    try:
                        ends.append(date.fromisoformat(str(rec[date_key])[:10]))
                    except ValueError:
                        pass
                    break

        actual_start = min(starts).isoformat() if starts else None
        actual_end = max(ends).isoformat() if ends else None

        # Simple coverage check: does the actual range cover the required range?
        if not starts or not ends:
            covered = False
            gaps = [f"{required_start} to {required_end} — no data found for {symbol}/{schema}"]
        else:
            start_ok = min(starts) <= req_start
            end_ok = max(ends) >= req_end
            covered = start_ok and end_ok
            gaps = []
            if not start_ok:
                gaps.append(f"{required_start} to {min(starts).isoformat()} — missing start coverage")
            if not end_ok:
                gaps.append(f"{max(ends).isoformat()} to {required_end} — missing end coverage")

        result = {
            "covered": covered,
            "gaps": gaps,
            "available_files": available_files,
            "total_rows": total_rows,
            "actual_start": actual_start,
            "actual_end": actual_end,
        }

        self.dlog.log(
            agent="DataLibrarian",
            observation=(
                f"Coverage check for {symbol}/{schema}: "
                f"covered={covered}, actual={actual_start} to {actual_end}, "
                f"required={required_start} to {required_end}"
            ),
            recommendation=(
                "Data coverage gap detected. Download missing data before backtesting."
                if not covered
                else "Data coverage requirement met."
            ),
            action_taken="coverage_checked",
            human_approval_required=False,
            risk_level="MEDIUM" if not covered else "LOW",
            metadata=result,
        )

        return result

    def get_bar_summary(self, bar_dir: Path) -> Dict[str, dict]:
        """
        Scan bar_dir for parquet files and return a summary by symbol.

        Expects files named like SYMBOL_TIMEFRAME.parquet
        (e.g. GC_1m.parquet, SI_5m.parquet).

        Args:
            bar_dir: Directory containing bar parquet files.

        Returns:
            {
                symbol: {
                    "timeframes":   list[str],
                    "row_counts":   {timeframe: int},
                    "date_ranges":  {timeframe: {"start": str, "end": str}},
                    "files":        list[str],
                }
            }
        """
        if not bar_dir.exists():
            logger.warning("[DataLibrarian] Bar directory not found: %s", bar_dir)
            return {}

        summary: Dict[str, dict] = {}

        for parquet_file in sorted(bar_dir.glob("*.parquet")):
            name = parquet_file.stem  # e.g. "GC_1m"
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                symbol, timeframe = parts[0].upper(), parts[1]
            else:
                symbol, timeframe = name.upper(), "unknown"

            if symbol not in summary:
                summary[symbol] = {
                    "timeframes": [],
                    "row_counts": {},
                    "date_ranges": {},
                    "files": [],
                }

            summary[symbol]["timeframes"].append(timeframe)
            summary[symbol]["files"].append(str(parquet_file))

            # Try to read row count and date range
            row_count, date_start, date_end = self._get_parquet_stats(parquet_file)
            summary[symbol]["row_counts"][timeframe] = row_count
            summary[symbol]["date_ranges"][timeframe] = {
                "start": date_start,
                "end": date_end,
            }

        self.dlog.log(
            agent="DataLibrarian",
            observation=(
                f"Bar directory scanned: {len(summary)} symbols found in {bar_dir}."
            ),
            recommendation="Review symbols for expected timeframes.",
            action_taken="bar_summary_generated",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"n_symbols": len(summary), "bar_dir": str(bar_dir)},
        )

        return summary

    def get_data_summary(self, raw_dir: Path, bar_dir: Path) -> dict:
        """
        Return a combined summary of raw manifest records and processed bars.

        Args:
            raw_dir: Directory containing raw data and MANIFEST.jsonl.
            bar_dir: Directory containing bar parquet files.

        Returns:
            {
                "generated_at":    str,
                "raw_manifest":    {symbol: list[manifest_record]},
                "bars":            {symbol: bar_summary_dict},
                "symbols_in_raw":  list[str],
                "symbols_in_bars": list[str],
                "symbols_both":    list[str],
            }
        """
        manifest = self.get_manifest(raw_dir)
        bars = self.get_bar_summary(bar_dir)

        # Group manifest by symbol
        raw_by_symbol: Dict[str, list] = {}
        for rec in manifest:
            sym = str(rec.get("symbol", "UNKNOWN")).upper()
            raw_by_symbol.setdefault(sym, []).append(rec)

        symbols_raw = sorted(raw_by_symbol.keys())
        symbols_bars = sorted(bars.keys())
        symbols_both = sorted(set(symbols_raw) & set(symbols_bars))

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "raw_manifest": raw_by_symbol,
            "bars": bars,
            "symbols_in_raw": symbols_raw,
            "symbols_in_bars": symbols_bars,
            "symbols_both": symbols_both,
        }

        return summary

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _read_jsonl(self, path: Path) -> List[dict]:
        """Read JSONL, skip malformed lines."""
        results = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.error("[DataLibrarian] Cannot read %s: %s", path, e)
        return results

    def _get_parquet_stats(self, path: Path) -> tuple:
        """
        Return (row_count, date_start_str, date_end_str) from a parquet file.
        Falls back to (0, None, None) if pandas is unavailable or file is empty.
        """
        try:
            import pandas as pd  # type: ignore

            df = pd.read_parquet(path)
            if df.empty:
                return 0, None, None

            row_count = len(df)

            # Find a datetime column or index
            ts_col = None
            for col in ("timestamp", "ts", "time", "datetime", "date"):
                if col in df.columns:
                    ts_col = col
                    break

            if ts_col:
                series = pd.to_datetime(df[ts_col], utc=True, errors="coerce").dropna()
                if len(series) > 0:
                    return (
                        row_count,
                        series.min().isoformat(),
                        series.max().isoformat(),
                    )
            elif isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
                idx = df.index
                if idx.tzinfo is None:
                    idx = idx.tz_localize("UTC")
                return (
                    row_count,
                    idx.min().isoformat(),
                    idx.max().isoformat(),
                )

            return row_count, None, None

        except ImportError:
            # Estimate row count from file size as rough fallback
            try:
                size_bytes = path.stat().st_size
                est_rows = max(0, size_bytes // 100)  # very rough
                return est_rows, None, None
            except OSError:
                return 0, None, None
        except Exception as e:
            logger.debug("[DataLibrarian] Cannot read parquet stats for %s: %s", path, e)
            return 0, None, None
