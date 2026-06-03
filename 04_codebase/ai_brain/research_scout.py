"""
research_scout.py — Strategy backlog navigator and research prioritiser
=======================================================================
ResearchScout reads the strategy universe JSON, filters BACKLOG entries,
and recommends the highest-priority strategies that can be tested with
currently available data. It also scans completed evidence reports.

ResearchScout CANNOT:
  - Deploy strategies
  - Modify the strategy universe file
  - Place trades or submit orders

Usage:
    from ai_brain.research_scout import ResearchScout

    scout = ResearchScout(decision_log=log)
    backlog = scout.load_backlog(Path("strategy_universe_exhaustive.json"))
    suggestions = scout.suggest_next_tests(available_data=["GC", "SI"], n=3)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)


class ResearchScout:
    """
    Navigates the strategy backlog and surfaces the highest-priority
    test candidates given currently available data.

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()
        self._backlog_cache: Optional[List[dict]] = None

    # ── Read-only property guard ───────────────────────────────────────────────

    @property
    def cannot_deploy(self) -> str:
        """
        Reminder that ResearchScout cannot deploy strategies.

        Returns:
            A string explaining the restriction.
        """
        return "ResearchScout cannot deploy strategies. Use DeploymentGatekeeper."

    # ── Core interface ─────────────────────────────────────────────────────────

    def load_backlog(self, backlog_path: Path) -> List[dict]:
        """
        Load the strategy universe JSON and return entries with status BACKLOG,
        sorted by priority (lower priority number = higher urgency).

        Args:
            backlog_path: Path to strategy_universe_exhaustive.json.

        Returns:
            List of strategy dicts with status == "BACKLOG", sorted by priority.
            Each dict includes at minimum: strategy_key, status, priority,
            data_required fields.
        """
        if not backlog_path.exists():
            logger.warning("[ResearchScout] Backlog file not found: %s", backlog_path)
            self.dlog.log(
                agent="ResearchScout",
                observation=f"Backlog file not found at {backlog_path}",
                recommendation="Check that strategy_universe_exhaustive.json exists.",
                action_taken="backlog_load_failed",
                human_approval_required=False,
                risk_level="MEDIUM",
            )
            return []

        try:
            raw = json.loads(backlog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[ResearchScout] Cannot parse backlog: %s", e)
            self.dlog.log(
                agent="ResearchScout",
                observation=f"Failed to parse backlog JSON: {e}",
                recommendation="Inspect strategy_universe_exhaustive.json for syntax errors.",
                action_taken="backlog_parse_failed",
                human_approval_required=False,
                risk_level="MEDIUM",
            )
            return []

        # The file may be a dict of {key: entry} or a list of entries
        if isinstance(raw, dict):
            entries = []
            for key, val in raw.items():
                if isinstance(val, dict):
                    val.setdefault("strategy_key", key)
                    entries.append(val)
        elif isinstance(raw, list):
            entries = raw
        else:
            logger.error("[ResearchScout] Unexpected backlog format: %s", type(raw))
            return []

        backlog = [e for e in entries if e.get("status") == "BACKLOG"]
        backlog.sort(key=lambda e: e.get("priority", 9999))

        self._backlog_cache = backlog

        self.dlog.log(
            agent="ResearchScout",
            observation=f"Loaded backlog: {len(backlog)} BACKLOG entries from {backlog_path.name}.",
            recommendation=f"Top priority strategy: {backlog[0].get('strategy_key', 'N/A')}."
            if backlog
            else "Backlog is empty.",
            action_taken="backlog_loaded",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"total_entries": len(entries), "backlog_count": len(backlog)},
        )

        return backlog

    def suggest_next_tests(
        self,
        available_data: List[str],
        n: int = 3,
        backlog_path: Optional[Path] = None,
    ) -> List[dict]:
        """
        Return the top N BACKLOG strategies whose data_required is fully
        covered by available_data.

        Args:
            available_data: List of symbol/data identifiers available locally
                            (e.g. ["GC", "SI", "GC_L2"]).
            n:              Number of suggestions to return.
            backlog_path:   Optional path to reload the backlog if not cached.

        Returns:
            List of up to N strategy dicts, highest priority first.
        """
        available_set = {d.upper().strip() for d in available_data}

        backlog = self._backlog_cache
        if backlog is None:
            if backlog_path:
                backlog = self.load_backlog(backlog_path)
            else:
                logger.warning("[ResearchScout] No backlog loaded. Call load_backlog first.")
                return []

        eligible = []
        for entry in backlog:
            required = entry.get("data_required", [])
            if isinstance(required, str):
                required = [required]
            required_upper = {r.upper().strip() for r in required}
            if required_upper.issubset(available_set):
                eligible.append(entry)

        suggestions = eligible[:n]

        self.dlog.log(
            agent="ResearchScout",
            observation=(
                f"suggest_next_tests: {len(eligible)} eligible from "
                f"{len(backlog)} backlog entries with data {available_data}."
            ),
            recommendation=(
                f"Suggested tests: {[s.get('strategy_key') for s in suggestions]}"
            ),
            action_taken="suggestions_generated",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"n_requested": n, "n_eligible": len(eligible)},
        )

        return suggestions

    def generate_hypothesis_card(
        self,
        strategy_key: str,
        backlog: List[dict],
    ) -> str:
        """
        Format a human-readable hypothesis card for a given strategy.

        Args:
            strategy_key: Unique identifier for the strategy.
            backlog:      Loaded backlog list (from load_backlog).

        Returns:
            Formatted multi-line string suitable for review in a terminal
            or email. Returns an error message if strategy_key not found.
        """
        entry = next(
            (e for e in backlog if e.get("strategy_key") == strategy_key),
            None,
        )

        if entry is None:
            return (
                f"[ResearchScout] Strategy '{strategy_key}' not found in backlog.\n"
                f"Available keys: {[e.get('strategy_key') for e in backlog[:10]]}"
            )

        lines = [
            "=" * 60,
            f"HYPOTHESIS CARD — {strategy_key}",
            "=" * 60,
            f"Status:        {entry.get('status', 'N/A')}",
            f"Priority:      {entry.get('priority', 'N/A')}",
            f"Category:      {entry.get('category', 'N/A')}",
            f"Symbols:       {entry.get('symbols', entry.get('data_required', 'N/A'))}",
            f"Timeframe:     {entry.get('timeframe', 'N/A')}",
            "",
            "HYPOTHESIS:",
            f"  {entry.get('hypothesis', entry.get('description', 'No hypothesis recorded.'))}",
            "",
            "ENTRY LOGIC:",
            f"  {entry.get('entry_logic', 'Not specified.')}",
            "",
            "EXIT LOGIC:",
            f"  {entry.get('exit_logic', 'Not specified.')}",
            "",
            "RISK PARAMETERS:",
            f"  Stop:   {entry.get('stop', 'Not specified.')}",
            f"  Target: {entry.get('target', 'Not specified.')}",
            "",
            "DATA REQUIRED:",
            f"  {entry.get('data_required', 'Not specified.')}",
            "",
            "NOTES:",
            f"  {entry.get('notes', 'None.')}",
            "=" * 60,
        ]

        self.dlog.log(
            agent="ResearchScout",
            observation=f"Hypothesis card generated for {strategy_key}.",
            recommendation="Review card before initiating backtest.",
            action_taken="hypothesis_card_generated",
            human_approval_required=False,
            risk_level="LOW",
        )

        return "\n".join(lines)

    def scan_evidence_reports(self, report_dir: Path) -> List[dict]:
        """
        Scan report_dir for *_evidence_report.json files and return a
        summary of top strategies sorted by walk-forward Sharpe.

        Args:
            report_dir: Directory containing evidence report JSON files.

        Returns:
            List of summary dicts, sorted descending by wf_sharpe.
            Each dict: {strategy_key, wf_sharpe, n_trades, slippage_1tick,
                        bootstrap_p, report_file, meets_deployment_gates}.
        """
        if not report_dir.exists():
            logger.warning("[ResearchScout] Report directory not found: %s", report_dir)
            return []

        summaries = []
        for report_file in sorted(report_dir.glob("*_evidence_report.json")):
            summary = self._parse_evidence_file(report_file)
            if summary:
                summaries.append(summary)

        summaries.sort(key=lambda s: s.get("wf_sharpe", -999), reverse=True)

        self.dlog.log(
            agent="ResearchScout",
            observation=f"Scanned {len(summaries)} evidence reports in {report_dir}.",
            recommendation=(
                f"Top strategy: {summaries[0]['strategy_key']} "
                f"(WF Sharpe={summaries[0]['wf_sharpe']:.2f})"
                if summaries
                else "No evidence reports found."
            ),
            action_taken="evidence_reports_scanned",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"n_reports": len(summaries)},
        )

        return summaries

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _parse_evidence_file(self, report_file: Path) -> Optional[dict]:
        """Parse a single evidence report JSON file into a summary dict."""
        try:
            data = json.loads(report_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[ResearchScout] Cannot read %s: %s", report_file, e)
            return None

        # Evidence reports may have different structures; try common patterns
        strategy_key = (
            data.get("strategy_key")
            or data.get("strategy")
            or report_file.stem.replace("_evidence_report", "")
        )

        # Walk-forward stats may be nested under different keys
        wf_section = data.get("walk_forward", data.get("wf", data))
        n_trades = (
            data.get("n_trades")
            or wf_section.get("n_trades")
            or data.get("total_trades", 0)
        )
        wf_sharpe = (
            data.get("wf_sharpe")
            or wf_section.get("sharpe")
            or data.get("sharpe", 0.0)
        )
        bootstrap_p = (
            data.get("bootstrap_p")
            or data.get("p_value")
            or data.get("bootstrap", {}).get("p_value", 1.0)
        )
        slippage_1tick = (
            data.get("slippage_1tick_sharpe")
            or data.get("slippage_1tick")
            or data.get("slippage", {}).get("1_tick_sharpe", -999.0)
        )

        meets_gates = (
            float(wf_sharpe) > 1.5
            and float(bootstrap_p) < 0.05
            and float(slippage_1tick) > 0
            and int(n_trades) >= 200
        )

        return {
            "strategy_key": strategy_key,
            "wf_sharpe": float(wf_sharpe),
            "n_trades": int(n_trades),
            "slippage_1tick": float(slippage_1tick),
            "bootstrap_p": float(bootstrap_p),
            "report_file": str(report_file),
            "meets_deployment_gates": meets_gates,
        }
