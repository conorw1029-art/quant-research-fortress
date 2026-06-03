"""
monitor_agent.py — System health monitoring for the quant trading pipeline
===========================================================================
MonitorAgent watches bar freshness, signal logs, and the kill switch.
It never modifies data or sends orders — it only reads and reports.

Stale thresholds:
  - 1m bars  → stale if last bar is > 3 minutes old
  - 5m bars  → stale if last bar is > 10 minutes old
  - 30m bars → stale if last bar is > 45 minutes old

Usage:
    from ai_brain.monitor_agent import MonitorAgent
    from ai_brain.decision_log import DecisionLog

    log = DecisionLog()
    agent = MonitorAgent(decision_log=log)
    summary = agent.run_check(bar_dir, log_dir, base_dir)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)

# Stale threshold in minutes per timeframe
STALE_THRESHOLDS: Dict[str, float] = {
    "1m":  3.0,
    "5m":  10.0,
    "30m": 45.0,
}


class MonitorAgent:
    """
    Monitors data feed freshness, signal log activity, and kill switch state.

    Args:
        decision_log: DecisionLog instance for audit trail. If None, a default
                      instance is created.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Public interface ───────────────────────────────────────────────────────

    def check_bar_freshness(
        self,
        bar_dir: Path,
        symbols: List[str],
        timeframe: str = "1m",
    ) -> Dict[str, dict]:
        """
        Read the newest bar timestamp from each symbol's parquet file and
        determine whether bars are fresh.

        Args:
            bar_dir:   Directory containing parquet files.
            symbols:   List of symbol strings (e.g. ["GC", "SI"]).
            timeframe: One of "1m", "5m", "30m".

        Returns:
            {
                symbol: {
                    "is_fresh":      bool,
                    "age_minutes":   float,
                    "newest_bar":    str,   # ISO timestamp or "UNKNOWN"
                    "file_found":    bool,
                }
            }
        """
        threshold = STALE_THRESHOLDS.get(timeframe, 3.0)
        results: Dict[str, dict] = {}
        now_utc = datetime.now(timezone.utc)

        for symbol in symbols:
            result = self._check_single_symbol(
                bar_dir, symbol, timeframe, threshold, now_utc
            )
            results[symbol] = result

            if not result["is_fresh"]:
                self.dlog.log(
                    agent="MonitorAgent",
                    observation=(
                        f"Stale bars for {symbol} ({timeframe}): "
                        f"age={result['age_minutes']:.1f}min, "
                        f"threshold={threshold}min"
                    ),
                    recommendation="Check data feed connection and bar builder process.",
                    action_taken="stale_bar_alert_logged",
                    human_approval_required=False,
                    risk_level="MEDIUM",
                    metadata=result,
                )

        return results

    def check_logs(
        self,
        log_dir: Path,
        max_silence_minutes: float = 30.0,
    ) -> dict:
        """
        Read today's signal JSONL log and report on activity.

        Looks for files matching signals_YYYYMMDD.jsonl in log_dir.

        Args:
            log_dir:               Directory containing signal log files.
            max_silence_minutes:   Silence threshold — if no signal in this
                                   many minutes, is_silent=True.

        Returns:
            {
                "last_signal_age_minutes": float,
                "is_silent":               bool,
                "signal_count_today":      int,
                "log_file_found":          bool,
            }
        """
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_file = log_dir / f"signals_{today_str}.jsonl"
        now_utc = datetime.now(timezone.utc)

        if not log_file.exists():
            # Try to find any recent signals file
            found_files = sorted(log_dir.glob("signals_*.jsonl")) if log_dir.exists() else []
            if not found_files:
                result = {
                    "last_signal_age_minutes": float("inf"),
                    "is_silent": True,
                    "signal_count_today": 0,
                    "log_file_found": False,
                }
                self.dlog.log(
                    agent="MonitorAgent",
                    observation=f"No signal log file found at {log_file}",
                    recommendation="Verify signal generation process is running.",
                    action_taken="missing_log_alert_logged",
                    human_approval_required=False,
                    risk_level="HIGH",
                )
                return result
            log_file = found_files[-1]

        entries = self._read_jsonl(log_file)
        count = len(entries)

        # Find most recent timestamp
        last_ts: Optional[datetime] = None
        for entry in entries:
            ts_str = entry.get("timestamp") or entry.get("ts") or entry.get("time")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                except (ValueError, TypeError):
                    continue

        if last_ts is None:
            age_minutes = float("inf")
        else:
            age_minutes = (now_utc - last_ts).total_seconds() / 60.0

        is_silent = age_minutes > max_silence_minutes

        result = {
            "last_signal_age_minutes": round(age_minutes, 2),
            "is_silent": is_silent,
            "signal_count_today": count,
            "log_file_found": True,
        }

        if is_silent:
            self.dlog.log(
                agent="MonitorAgent",
                observation=(
                    f"Signal log silent for {age_minutes:.1f} minutes "
                    f"(threshold={max_silence_minutes}min). Count today: {count}."
                ),
                recommendation="Check strategy signal generation. May be outside trading hours.",
                action_taken="silence_alert_logged",
                human_approval_required=False,
                risk_level="MEDIUM",
                metadata=result,
            )

        return result

    def check_kill_switch(self, base_dir: Path) -> bool:
        """
        Read KILL_SWITCH.txt from base_dir.

        Returns:
            True if the file exists and contains "STOP" (case-insensitive).
            False otherwise (including if file does not exist).
        """
        kill_file = base_dir / "KILL_SWITCH.txt"
        if not kill_file.exists():
            return False

        try:
            content = kill_file.read_text(encoding="utf-8").strip().upper()
            is_active = "STOP" in content
        except Exception as e:
            logger.error("[MonitorAgent] Cannot read KILL_SWITCH.txt: %s", e)
            return False

        if is_active:
            self.dlog.log(
                agent="MonitorAgent",
                observation=f"KILL_SWITCH.txt contains STOP. Content: {content[:50]}",
                recommendation="All trading should halt immediately. Do not restart without human review.",
                action_taken="kill_switch_active_logged",
                human_approval_required=True,
                risk_level="CRITICAL",
            )

        return is_active

    def run_check(
        self,
        bar_dir: Path,
        log_dir: Path,
        base_dir: Path,
        symbols: Optional[List[str]] = None,
        timeframe: str = "1m",
    ) -> dict:
        """
        Run all health checks and return a combined summary dict.

        Args:
            bar_dir:    Directory containing parquet bar files.
            log_dir:    Directory containing signal JSONL log files.
            base_dir:   Root directory to look for KILL_SWITCH.txt.
            symbols:    Symbols to check. If None, auto-discovers from bar_dir.
            timeframe:  Bar timeframe for freshness check.

        Returns:
            {
                "kill_switch_active": bool,
                "bar_freshness":      {symbol: {...}},
                "log_status":         {...},
                "all_ok":             bool,
                "check_time":         str,   # ISO UTC
            }
        """
        check_time = datetime.now(timezone.utc).isoformat()

        kill_active = self.check_kill_switch(base_dir)

        # Auto-discover symbols from parquet files if not provided
        if symbols is None:
            symbols = self._discover_symbols(bar_dir, timeframe)

        bar_status = self.check_bar_freshness(bar_dir, symbols, timeframe)
        log_status = self.check_logs(log_dir)

        bars_ok = all(v["is_fresh"] for v in bar_status.values()) if bar_status else True
        log_ok = not log_status["is_silent"]
        all_ok = (not kill_active) and bars_ok and log_ok

        summary = {
            "kill_switch_active": kill_active,
            "bar_freshness": bar_status,
            "log_status": log_status,
            "all_ok": all_ok,
            "check_time": check_time,
        }

        risk = "CRITICAL" if kill_active else ("HIGH" if not all_ok else "LOW")
        self.dlog.log(
            agent="MonitorAgent",
            observation=(
                f"Health check complete. kill_switch={kill_active}, "
                f"bars_ok={bars_ok}, log_ok={log_ok}"
            ),
            recommendation=(
                "Investigate failing components."
                if not all_ok
                else "System appears healthy."
            ),
            action_taken="health_check_completed",
            human_approval_required=kill_active,
            risk_level=risk,
            metadata={"all_ok": all_ok, "symbols_checked": symbols},
        )

        return summary

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _check_single_symbol(
        self,
        bar_dir: Path,
        symbol: str,
        timeframe: str,
        threshold: float,
        now_utc: datetime,
    ) -> dict:
        """Check freshness for a single symbol parquet file."""
        # Try common naming patterns
        candidates = [
            bar_dir / f"{symbol}_{timeframe}.parquet",
            bar_dir / f"{symbol.lower()}_{timeframe}.parquet",
            bar_dir / symbol / f"{timeframe}.parquet",
            bar_dir / f"{symbol}.parquet",
        ]

        parquet_file: Optional[Path] = None
        for candidate in candidates:
            if candidate.exists():
                parquet_file = candidate
                break

        if parquet_file is None:
            return {
                "is_fresh": False,
                "age_minutes": float("inf"),
                "newest_bar": "UNKNOWN",
                "file_found": False,
            }

        newest_bar, age_minutes = self._get_bar_age(parquet_file, now_utc)
        is_fresh = age_minutes <= threshold

        return {
            "is_fresh": is_fresh,
            "age_minutes": round(age_minutes, 2),
            "newest_bar": newest_bar,
            "file_found": True,
        }

    def _get_bar_age(self, parquet_file: Path, now_utc: datetime) -> tuple:
        """
        Read the newest bar timestamp from a parquet file.

        Returns (newest_bar_str, age_minutes).
        Falls back to file mtime if pandas/pyarrow not available.
        """
        try:
            import pandas as pd  # type: ignore

            df = pd.read_parquet(parquet_file, columns=None)
            if df.empty:
                return "EMPTY", float("inf")

            # Try common timestamp column names
            ts_col = None
            for col in ("timestamp", "ts", "time", "datetime", "date"):
                if col in df.columns:
                    ts_col = col
                    break

            if ts_col is None and isinstance(df.index, pd.DatetimeIndex):
                newest = df.index.max()
                if newest.tzinfo is None:
                    newest = newest.tz_localize("UTC")
                else:
                    newest = newest.tz_convert("UTC")
                age = (now_utc - newest.to_pydatetime()).total_seconds() / 60.0
                return newest.isoformat(), round(age, 2)

            if ts_col:
                series = pd.to_datetime(df[ts_col], utc=True)
                newest = series.max()
                age = (now_utc - newest.to_pydatetime()).total_seconds() / 60.0
                return newest.isoformat(), round(age, 2)

            # Fall back to file mtime
            return self._mtime_age(parquet_file, now_utc)

        except ImportError:
            # pandas/pyarrow not available — use file mtime
            return self._mtime_age(parquet_file, now_utc)
        except Exception as e:
            logger.warning("[MonitorAgent] Cannot read parquet %s: %s", parquet_file, e)
            return self._mtime_age(parquet_file, now_utc)

    def _mtime_age(self, path: Path, now_utc: datetime) -> tuple:
        """Return (mtime_str, age_minutes) based on file modification time."""
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            age = (now_utc - mtime).total_seconds() / 60.0
            return mtime.isoformat(), round(age, 2)
        except Exception:
            return "UNKNOWN", float("inf")

    def _discover_symbols(self, bar_dir: Path, timeframe: str) -> List[str]:
        """Auto-discover symbols from parquet files in bar_dir."""
        if not bar_dir.exists():
            return []
        symbols = set()
        for f in bar_dir.glob(f"*_{timeframe}.parquet"):
            symbol = f.stem.replace(f"_{timeframe}", "")
            symbols.add(symbol.upper())
        for f in bar_dir.glob("*.parquet"):
            # Plain symbol.parquet files
            name = f.stem
            if "_" not in name:
                symbols.add(name.upper())
        return sorted(symbols)

    def _read_jsonl(self, path: Path) -> List[dict]:
        """Read a JSONL file, skipping malformed lines."""
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
        except Exception as e:
            logger.error("[MonitorAgent] Cannot read %s: %s", path, e)
        return results
