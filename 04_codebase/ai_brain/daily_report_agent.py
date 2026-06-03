"""
daily_report_agent.py — End-of-day signal and P&L summary
==========================================================
DailyReportAgent reads the signals_YYYYMMDD.jsonl log produced by the live
signal system and computes a concise daily performance summary.

It reads only — it does not modify logs, place trades, or change state.

Usage:
    from ai_brain.daily_report_agent import DailyReportAgent

    agent = DailyReportAgent(decision_log=log)
    report = agent.generate_report("20260603", log_dir, report_dir)
    print(agent.format_report(report))
    saved_path = agent.save_report(report, report_dir)
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)


class DailyReportAgent:
    """
    Reads today's signal log and produces a structured daily report.

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Core methods ───────────────────────────────────────────────────────────

    def generate_report(
        self,
        date_str: str,
        log_dir: Path,
        report_dir: Path,
    ) -> dict:
        """
        Read signals_YYYYMMDD.jsonl and compute the daily summary.

        The signal log is expected to contain one JSON object per line.
        Relevant fields per signal entry:
          - "timestamp" / "ts": ISO datetime
          - "strategy" / "strategy_key": strategy name
          - "action": "FIRE", "BLOCK", "ERROR", etc.
          - "block_reason": why signal was blocked (if action=="BLOCK")
          - "hypo_outcome": hypothetical P&L outcome if the signal fired
          - "error_msg": error message (if action=="ERROR")

        Args:
            date_str:   Date in YYYYMMDD format.
            log_dir:    Directory containing signal JSONL logs.
            report_dir: Directory where reports will be saved (created if needed).

        Returns:
            Full report dict with all computed metrics.
        """
        log_file = log_dir / f"signals_{date_str}.jsonl"

        entries = self._read_jsonl(log_file)

        # Aggregate metrics
        signals_fired = 0
        signals_blocked = 0
        errors_count = 0
        block_reasons: Counter = Counter()
        strategies_seen: set = set()
        hypo_pnl_values = []

        for entry in entries:
            action = str(entry.get("action", "")).upper().strip()
            strategy = entry.get("strategy") or entry.get("strategy_key") or "UNKNOWN"
            strategies_seen.add(strategy)

            if action in ("FIRE", "FIRED", "SIGNAL", "ENTRY"):
                signals_fired += 1
            elif action in ("BLOCK", "BLOCKED", "REJECTED", "FILTERED"):
                signals_blocked += 1
                reason = (
                    entry.get("block_reason")
                    or entry.get("reason")
                    or "unspecified"
                )
                block_reasons[str(reason)] += 1
            elif action in ("ERROR", "EXCEPTION", "FAULT"):
                errors_count += 1

            # Hypothetical P&L outcome (if tracked)
            hypo = entry.get("hypo_outcome") or entry.get("hypothetical_pnl")
            if hypo is not None:
                try:
                    hypo_pnl_values.append(float(hypo))
                except (ValueError, TypeError):
                    pass

        # Win rate on hypothetical outcomes
        if hypo_pnl_values:
            total_hypo_trades = len(hypo_pnl_values)
            winners = sum(1 for v in hypo_pnl_values if v > 0)
            win_rate = winners / total_hypo_trades
            hypothetical_pnl = sum(hypo_pnl_values)
        else:
            win_rate = 0.0
            hypothetical_pnl = 0.0
            total_hypo_trades = 0

        report = {
            "date": date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "log_file": str(log_file),
            "log_file_found": log_file.exists(),
            "total_entries": len(entries),
            "signals_fired": signals_fired,
            "signals_blocked": signals_blocked,
            "errors_count": errors_count,
            "block_reasons": dict(block_reasons),
            "hypothetical_pnl": round(hypothetical_pnl, 2),
            "win_rate": round(win_rate, 4),
            "total_hypo_trades": total_hypo_trades,
            "strategies_active": sorted(strategies_seen),
            "n_strategies_active": len(strategies_seen),
        }

        risk = "LOW" if errors_count == 0 else "MEDIUM"
        self.dlog.log(
            agent="DailyReportAgent",
            observation=(
                f"Daily report generated for {date_str}: "
                f"fired={signals_fired}, blocked={signals_blocked}, "
                f"errors={errors_count}, hypo_pnl={hypothetical_pnl:.2f}, "
                f"win_rate={win_rate:.1%}"
            ),
            recommendation=(
                "Review error count and block reasons."
                if errors_count > 0
                else "Report generated successfully."
            ),
            action_taken="daily_report_generated",
            human_approval_required=False,
            risk_level=risk,
            metadata={"date": date_str, "signals_fired": signals_fired, "errors": errors_count},
        )

        return report

    def format_report(self, report: dict) -> str:
        """
        Format a report dict into a readable 20-line text summary.

        Args:
            report: Dict returned by generate_report().

        Returns:
            Multi-line string suitable for console output or email.
        """
        date = report.get("date", "UNKNOWN")
        gen_at = report.get("generated_at", "UNKNOWN")

        # Block reasons — top 3
        block_reasons = report.get("block_reasons", {})
        if block_reasons:
            top_blocks = sorted(block_reasons.items(), key=lambda x: x[1], reverse=True)[:3]
            block_str = "\n".join(f"      {reason}: {count}" for reason, count in top_blocks)
        else:
            block_str = "      (none)"

        strategies = report.get("strategies_active", [])
        strat_str = ", ".join(strategies) if strategies else "(none)"
        if len(strat_str) > 60:
            strat_str = strat_str[:57] + "..."

        win_rate_pct = report.get("win_rate", 0.0) * 100

        lines = [
            "=" * 60,
            f"  DAILY REPORT — {date}",
            f"  Generated: {gen_at}",
            "=" * 60,
            "",
            "  SIGNAL ACTIVITY",
            f"    Signals fired:    {report.get('signals_fired', 0)}",
            f"    Signals blocked:  {report.get('signals_blocked', 0)}",
            f"    Errors:           {report.get('errors_count', 0)}",
            f"    Total log entries:{report.get('total_entries', 0)}",
            "",
            "  TOP BLOCK REASONS",
            block_str,
            "",
            "  HYPOTHETICAL PERFORMANCE",
            f"    Hypo P&L:   ${report.get('hypothetical_pnl', 0.0):,.2f}",
            f"    Win rate:   {win_rate_pct:.1f}% ({report.get('total_hypo_trades', 0)} trades)",
            "",
            "  ACTIVE STRATEGIES",
            f"    {strat_str}",
            "",
            "  STATUS",
            f"    Log file found: {report.get('log_file_found', False)}",
            "=" * 60,
        ]

        return "\n".join(lines)

    def save_report(self, report: dict, report_dir: Path) -> Path:
        """
        Save the report dict as a JSON file in report_dir/daily_YYYYMMDD.json.

        Args:
            report:     Report dict from generate_report().
            report_dir: Directory to save the report. Created if not exists.

        Returns:
            Path to the saved report file.
        """
        report_dir.mkdir(parents=True, exist_ok=True)

        date_str = report.get("date", datetime.now(timezone.utc).strftime("%Y%m%d"))
        output_path = report_dir / f"daily_{date_str}.json"

        try:
            output_path.write_text(
                json.dumps(report, indent=2),
                encoding="utf-8",
            )
            logger.info("[DailyReportAgent] Report saved to %s", output_path)
        except OSError as e:
            logger.error("[DailyReportAgent] Failed to save report: %s", e)

        self.dlog.log(
            agent="DailyReportAgent",
            observation=f"Daily report saved: {output_path}",
            recommendation="Archive report for monthly review.",
            action_taken="daily_report_saved",
            human_approval_required=False,
            risk_level="LOW",
            evidence_file=str(output_path),
        )

        return output_path

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _read_jsonl(self, path: Path) -> list:
        """Read a JSONL file, skipping malformed lines. Returns [] if not found."""
        results = []
        if not path.exists():
            logger.warning("[DailyReportAgent] Log file not found: %s", path)
            return results

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
            logger.error("[DailyReportAgent] Cannot read %s: %s", path, e)

        return results
