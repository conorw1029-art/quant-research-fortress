"""
strategy_librarian.py — Strategy universe reader and status manager
===================================================================
StrategyLibrarian manages the strategy_universe_exhaustive.json file —
loading, querying by status, updating status, and recording test results.

It provides the single source of truth for what every strategy's current
state is. It does NOT run backtests or deploy strategies.

Valid statuses (in rough lifecycle order):
  BACKLOG → TESTING → REJECTED | RESEARCH_ONLY | RETEST_WITH_MORE_DATA
                              → WATCHLIST → PAPER_CANDIDATE | DEMO_CANDIDATE
                              → LIVE_BLOCKED (if gated out at deployment review)

Usage:
    from ai_brain.strategy_librarian import StrategyLibrarian

    lib = StrategyLibrarian(decision_log=log)
    universe = lib.load_universe(Path("strategy_universe_exhaustive.json"))
    candidates = lib.get_deployment_candidates(universe)
    lib.update_status(universe, "vwap_reclaim_gc", "WATCHLIST")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)


class StrategyLibrarian:
    """
    Manages the strategy universe JSON — loads, queries, and updates entries.

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    VALID_STATUSES = [
        "BACKLOG",
        "TESTING",
        "REJECTED",
        "RESEARCH_ONLY",
        "RETEST_WITH_MORE_DATA",
        "WATCHLIST",
        "PAPER_CANDIDATE",
        "DEMO_CANDIDATE",
        "LIVE_BLOCKED",
    ]

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Core interface ─────────────────────────────────────────────────────────

    def load_universe(self, universe_path: Path) -> dict:
        """
        Load the strategy universe JSON file.

        The universe file is expected to be a dict keyed by strategy_key,
        where each value is a strategy metadata dict. It may also be a
        list of strategy dicts — both formats are normalised to a dict.

        Args:
            universe_path: Path to strategy_universe_exhaustive.json.

        Returns:
            Dict of {strategy_key: strategy_dict}. Empty dict if not found.
        """
        if not universe_path.exists():
            logger.warning("[StrategyLibrarian] Universe file not found: %s", universe_path)
            self.dlog.log(
                agent="StrategyLibrarian",
                observation=f"Universe file not found: {universe_path}",
                recommendation="Create or locate strategy_universe_exhaustive.json.",
                action_taken="universe_load_failed",
                human_approval_required=False,
                risk_level="MEDIUM",
            )
            return {}

        try:
            raw = json.loads(universe_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[StrategyLibrarian] Cannot parse universe: %s", e)
            self.dlog.log(
                agent="StrategyLibrarian",
                observation=f"Failed to parse universe JSON: {e}",
                recommendation="Inspect strategy_universe_exhaustive.json for syntax errors.",
                action_taken="universe_parse_failed",
                human_approval_required=False,
                risk_level="HIGH",
            )
            return {}

        # Normalise to dict
        if isinstance(raw, list):
            universe: Dict[str, dict] = {}
            for entry in raw:
                if isinstance(entry, dict):
                    key = entry.get("strategy_key") or entry.get("key") or f"unknown_{len(universe)}"
                    universe[key] = entry
        elif isinstance(raw, dict):
            universe = raw
        else:
            logger.error("[StrategyLibrarian] Unexpected universe format: %s", type(raw))
            return {}

        # Ensure each entry has a strategy_key field
        for key, entry in universe.items():
            entry.setdefault("strategy_key", key)

        status_counts: Dict[str, int] = {}
        for entry in universe.values():
            s = entry.get("status", "UNKNOWN")
            status_counts[s] = status_counts.get(s, 0) + 1

        self.dlog.log(
            agent="StrategyLibrarian",
            observation=(
                f"Loaded universe: {len(universe)} strategies from {universe_path.name}. "
                f"Status breakdown: {status_counts}"
            ),
            recommendation="Universe loaded successfully.",
            action_taken="universe_loaded",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"n_strategies": len(universe), "status_counts": status_counts},
        )

        return universe

    def save_universe(self, universe: dict, universe_path: Path) -> None:
        """
        Save the universe dict back to disk with indent=2 formatting.

        Args:
            universe:      Dict of {strategy_key: strategy_dict}.
            universe_path: Output path.
        """
        universe_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            universe_path.write_text(
                json.dumps(universe, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info("[StrategyLibrarian] Universe saved: %s", universe_path)
        except OSError as e:
            logger.error("[StrategyLibrarian] Cannot save universe: %s", e)

        self.dlog.log(
            agent="StrategyLibrarian",
            observation=f"Universe saved to {universe_path} ({len(universe)} entries).",
            recommendation="Commit changes to version control.",
            action_taken="universe_saved",
            human_approval_required=False,
            risk_level="LOW",
        )

    def get_by_status(self, universe: dict, status: str) -> List[dict]:
        """
        Return all strategy entries with the given status.

        Args:
            universe: Loaded universe dict.
            status:   Status string (must be in VALID_STATUSES).

        Returns:
            List of strategy dicts matching the status.
        """
        if status not in self.VALID_STATUSES:
            logger.warning(
                "[StrategyLibrarian] Unknown status '%s'. Valid: %s",
                status,
                self.VALID_STATUSES,
            )

        return [
            entry for entry in universe.values()
            if entry.get("status") == status
        ]

    def update_status(
        self,
        universe: dict,
        strategy_key: str,
        new_status: str,
        evidence_path: Optional[str] = None,
    ) -> bool:
        """
        Update the status field of a strategy entry in the universe dict.

        Does NOT save to disk — caller must call save_universe() after.

        Args:
            universe:      Loaded universe dict (mutated in place).
            strategy_key:  Strategy to update.
            new_status:    New status value (must be in VALID_STATUSES).
            evidence_path: Optional path to supporting evidence file.

        Returns:
            True if update was applied, False if key not found or status invalid.
        """
        if strategy_key not in universe:
            logger.warning(
                "[StrategyLibrarian] Strategy '%s' not found in universe.",
                strategy_key,
            )
            return False

        if new_status not in self.VALID_STATUSES:
            logger.warning(
                "[StrategyLibrarian] Invalid status '%s'. Valid: %s",
                new_status,
                self.VALID_STATUSES,
            )
            return False

        old_status = universe[strategy_key].get("status", "UNKNOWN")
        universe[strategy_key]["status"] = new_status
        universe[strategy_key]["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        if evidence_path:
            universe[strategy_key]["evidence_path"] = str(evidence_path)

        self.dlog.log(
            agent="StrategyLibrarian",
            observation=(
                f"Status updated for '{strategy_key}': "
                f"{old_status} → {new_status}"
                + (f" (evidence: {evidence_path})" if evidence_path else "")
            ),
            recommendation=(
                "Deployment candidate created — run through DeploymentGatekeeper before any trading."
                if new_status in ("PAPER_CANDIDATE", "DEMO_CANDIDATE")
                else f"Strategy marked as {new_status}."
            ),
            action_taken="status_updated",
            human_approval_required=new_status in ("PAPER_CANDIDATE", "DEMO_CANDIDATE"),
            risk_level="MEDIUM" if new_status in ("PAPER_CANDIDATE", "DEMO_CANDIDATE") else "LOW",
            evidence_file=str(evidence_path) if evidence_path else None,
            metadata={
                "strategy_key": strategy_key,
                "old_status": old_status,
                "new_status": new_status,
            },
        )

        return True

    def get_deployment_candidates(self, universe: dict) -> List[dict]:
        """
        Return strategies with PAPER_CANDIDATE or DEMO_CANDIDATE status.

        These are candidates that have passed evidence gates and are awaiting
        human approval to begin paper/demo trading.

        Args:
            universe: Loaded universe dict.

        Returns:
            List of candidate strategy dicts, sorted by status then priority.
        """
        candidates = [
            entry for entry in universe.values()
            if entry.get("status") in ("PAPER_CANDIDATE", "DEMO_CANDIDATE")
        ]

        candidates.sort(
            key=lambda e: (
                0 if e.get("status") == "PAPER_CANDIDATE" else 1,
                e.get("priority", 9999),
            )
        )

        self.dlog.log(
            agent="StrategyLibrarian",
            observation=f"Found {len(candidates)} deployment candidates.",
            recommendation=(
                f"Review candidates with DeploymentGatekeeper: "
                f"{[c.get('strategy_key') for c in candidates]}"
                if candidates
                else "No deployment candidates at this time."
            ),
            action_taken="deployment_candidates_retrieved",
            human_approval_required=len(candidates) > 0,
            risk_level="MEDIUM" if candidates else "LOW",
        )

        return candidates

    def add_test_result(
        self,
        universe: dict,
        strategy_key: str,
        result: dict,
    ) -> None:
        """
        Append a test result to a strategy's test_history list.

        Does NOT save to disk — caller must call save_universe() after.

        Args:
            universe:     Loaded universe dict (mutated in place).
            strategy_key: Strategy to update.
            result:       Result dict to append (should include at minimum:
                          {"date": str, "type": str, "sharpe": float, ...}).
        """
        if strategy_key not in universe:
            logger.warning(
                "[StrategyLibrarian] Cannot add test result — '%s' not found.",
                strategy_key,
            )
            return

        # Ensure test_history list exists
        if "test_history" not in universe[strategy_key]:
            universe[strategy_key]["test_history"] = []

        # Add timestamp if not present
        result.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())

        universe[strategy_key]["test_history"].append(result)

        self.dlog.log(
            agent="StrategyLibrarian",
            observation=(
                f"Test result added to '{strategy_key}': "
                f"type={result.get('type', 'N/A')}, "
                f"sharpe={result.get('sharpe', result.get('wf_sharpe', 'N/A'))}"
            ),
            recommendation="Save universe after adding results.",
            action_taken="test_result_added",
            human_approval_required=False,
            risk_level="LOW",
            metadata={"strategy_key": strategy_key, "result_type": result.get("type", "unknown")},
        )
