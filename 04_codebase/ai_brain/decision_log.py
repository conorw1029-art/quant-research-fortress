"""
decision_log.py — Persistent audit trail for all AI agent decisions
====================================================================
Every observation, recommendation, and action taken by an AI agent is logged
here. This provides:
  - Full audit trail for review and compliance
  - Data for measuring agent accuracy over time
  - Input for the daily report
  - Evidence if something goes wrong

Log format: JSONL (one JSON object per line)
Log location: 06_live_trading/ai_logs/ai_decisions_YYYYMMDD.jsonl

Design:
  - DecisionLog is a singleton-safe write-only logger
  - No reads happen during normal operation (reports read separately)
  - All entries are append-only and immutable once written
  - human_approval_required=True entries MUST be reviewed before action
  - risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"

Usage:
    from ai_brain.decision_log import DecisionLog

    log = DecisionLog()
    log.log(
        agent="MonitorAgent",
        observation="No bars received for GC in past 45 minutes",
        recommendation="Check data feed connection",
        action_taken="alert_sent",
        human_approval_required=False,
        risk_level="MEDIUM",
    )

    summary = log.get_today_summary()
    print(summary)
    # {'total': 3, 'by_agent': {'MonitorAgent': 2, ...}, 'by_risk': {'MEDIUM': 2, ...}}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid risk levels (ordered from lowest to highest)
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# Valid agent names — add new agents here when creating them
KNOWN_AGENTS = {
    "MonitorAgent",
    "RiskSentinel",
    "ResearchScout",
    "ValidationAgent",
    "DeploymentGatekeeper",
    "DailyReportAgent",
    "DataLibrarian",
    "StrategyLibrarian",
    "AlertBot",
    "System",     # for system-level entries
}

_LOG_DIR = Path(__file__).parents[2] / "06_live_trading" / "ai_logs"


class DecisionLog:
    """
    Append-only audit log for AI agent decisions and observations.

    Thread-safety: writes are done with open() + write() which is
    generally safe for single-process append-only use. For multi-process
    safety, use a lock or a dedicated logging service.

    Args:
        log_dir: Override the default log directory.
    """

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or _LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        agent:                   str,
        observation:             str,
        recommendation:          str,
        action_taken:            str,
        human_approval_required: bool,
        risk_level:              str,
        evidence_file:           Optional[str] = None,
        metadata:                Optional[dict] = None,
    ) -> dict:
        """
        Log an agent decision event.

        Args:
            agent:                   Name of the agent (use KNOWN_AGENTS constants)
            observation:             What the agent observed (factual, no opinion)
            recommendation:          What the agent recommends be done
            action_taken:            What was actually done (may differ from recommendation
                                     if human_approval_required was True and human declined)
            human_approval_required: True if a human must approve before action is taken.
                                     The DeploymentGatekeeper always sets this to True.
            risk_level:              "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
            evidence_file:           Optional path to supporting evidence (e.g. report JSON)
            metadata:                Optional extra fields for this entry

        Returns:
            The logged entry dict.
        """
        if risk_level not in RISK_LEVELS:
            raise ValueError(
                f"Invalid risk_level '{risk_level}'. Must be one of {RISK_LEVELS}."
            )

        entry = {
            "timestamp":               datetime.now(timezone.utc).isoformat(),
            "agent":                   agent,
            "observation":             observation,
            "recommendation":          recommendation,
            "action_taken":            action_taken,
            "human_approval_required": human_approval_required,
            "risk_level":              risk_level,
            "evidence_file":           evidence_file,
        }
        if metadata:
            entry["metadata"] = metadata

        self._write(entry)

        if human_approval_required:
            logger.warning(
                "[DecisionLog] HUMAN APPROVAL REQUIRED — agent=%s risk=%s: %s",
                agent, risk_level, recommendation
            )
        elif risk_level in ("HIGH", "CRITICAL"):
            logger.error(
                "[DecisionLog] %s — agent=%s: %s",
                risk_level, agent, observation
            )
        else:
            logger.info(
                "[DecisionLog] agent=%s risk=%s action=%s",
                agent, risk_level, action_taken
            )

        return entry

    def log_system(
        self,
        observation:             str,
        action_taken:            str,
        risk_level:              str = "LOW",
        human_approval_required: bool = False,
    ) -> dict:
        """Convenience method for system-level log entries."""
        return self.log(
            agent                   = "System",
            observation             = observation,
            recommendation          = action_taken,
            action_taken            = action_taken,
            human_approval_required = human_approval_required,
            risk_level              = risk_level,
        )

    # ── Reading / summarisation ────────────────────────────────────────────────

    def get_today_summary(self) -> dict:
        """
        Return a count summary of today's decision log entries.

        Returns:
            {
                "total":    int,
                "by_agent": {agent_name: count},
                "by_risk":  {risk_level: count},
                "human_approval_pending": int,  # requires=True, action_taken="pending"
                "log_path": str,
            }
        """
        entries   = self._read_today()
        by_agent: Dict[str, int] = {}
        by_risk:  Dict[str, int] = {}
        pending   = 0

        for entry in entries:
            agent = entry.get("agent", "unknown")
            risk  = entry.get("risk_level", "unknown")
            by_agent[agent] = by_agent.get(agent, 0) + 1
            by_risk[risk]   = by_risk.get(risk, 0) + 1
            if entry.get("human_approval_required") and entry.get("action_taken") == "pending":
                pending += 1

        today_path = self._today_path()
        return {
            "total":                   len(entries),
            "by_agent":                by_agent,
            "by_risk":                 by_risk,
            "human_approval_pending":  pending,
            "log_path":                str(today_path),
        }

    def get_entries(
        self,
        date_str:   Optional[str] = None,
        agent:      Optional[str] = None,
        risk_level: Optional[str] = None,
    ) -> List[dict]:
        """
        Read log entries, optionally filtered.

        Args:
            date_str:   "YYYYMMDD" — defaults to today
            agent:      Filter by agent name
            risk_level: Filter by risk level

        Returns:
            List of entry dicts, oldest first.
        """
        all_entries = self._read_date(date_str)
        if agent:
            all_entries = [e for e in all_entries if e.get("agent") == agent]
        if risk_level:
            all_entries = [e for e in all_entries if e.get("risk_level") == risk_level]
        return all_entries

    # ── Internal ───────────────────────────────────────────────────────────────

    def _today_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self.log_dir / f"ai_decisions_{today}.jsonl"

    def _date_path(self, date_str: Optional[str]) -> Path:
        if date_str is None:
            return self._today_path()
        return self.log_dir / f"ai_decisions_{date_str}.jsonl"

    def _write(self, entry: dict) -> None:
        path = self._today_path()
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("DecisionLog: failed to write entry: %s", e)

    def _read_today(self) -> List[dict]:
        return self._read_date(None)

    def _read_date(self, date_str: Optional[str]) -> List[dict]:
        path    = self._date_path(date_str)
        results = []
        if not path.exists():
            return results
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.error("DecisionLog: failed to read %s: %s", path, e)
        return results
