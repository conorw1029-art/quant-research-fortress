"""
deployment_gatekeeper.py — Strictest guard before any live trading deployment
=============================================================================
DeploymentGatekeeper is the last line of defence before a strategy can be
considered for paper or live trading. It enforces all hard eligibility gates
and ALWAYS requires explicit human approval for any deployment action.

KEY INVARIANTS (enforced in code):
  - approve_for_paper() NEVER auto-approves. It only logs the request with
    human_approval_required=True and prints a hard warning.
  - approve_for_live() ALWAYS raises PermissionError. No AI agent can approve
    live trading deployment under any circumstance.
  - All actions are logged to DecisionLog with full audit trail.

Deployment gates (all must pass):
  - n_trades >= 200
  - wf_sharpe > 1.5
  - bootstrap_p < 0.05
  - slippage_1tick_sharpe > 0
  - evidence must contain a news-filtered result
  - data covers >= 4 years
  - bracket orders have been tested
  - state persistence has been verified

Usage:
    from ai_brain.deployment_gatekeeper import DeploymentGatekeeper

    gk = DeploymentGatekeeper(decision_log=log)
    eligible, blockers = gk.check_eligibility(strategy, evidence, system_state)
    if eligible:
        gk.approve_for_paper(strategy_key, evidence, log)  # still needs human!
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)


class DeploymentGatekeeper:
    """
    Enforces all hard gates before any deployment decision.

    This class is intentionally conservative. When in doubt it blocks.
    Only a human can override a block — no agent may do so.

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Eligibility check ──────────────────────────────────────────────────────

    def check_eligibility(
        self,
        strategy: dict,
        evidence: dict,
        system_state: dict,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate a strategy against all deployment gates.

        Args:
            strategy:     Strategy metadata dict (from strategy universe).
            evidence:     Evidence report dict (from validation agent).
            system_state: Current system state dict. Expected keys:
                          - "bracket_orders_tested": bool
                          - "state_persistence_verified": bool
                          - "data_years": float
                          - Any other state indicators.

        Returns:
            (eligible, blockers)
            eligible: True only if ALL gates pass. False if any blocker found.
            blockers: List of human-readable blocker strings explaining failures.
        """
        blockers: List[str] = []

        strategy_key = strategy.get("strategy_key", evidence.get("strategy_key", "UNKNOWN"))

        # Gate 1: Trade count
        n_trades = self._get_val(evidence, ["n_trades", "total_trades", "trade_count"], 0)
        try:
            if int(n_trades) < 200:
                blockers.append(f"n_trades={n_trades} < 200 minimum")
        except (ValueError, TypeError):
            blockers.append(f"n_trades='{n_trades}' is not a valid integer")

        # Gate 2: Walk-forward Sharpe
        wf_sharpe = self._get_val(evidence, ["wf_sharpe", "walk_forward_sharpe", "sharpe"], None)
        if wf_sharpe is None:
            blockers.append("wf_sharpe missing from evidence")
        else:
            try:
                if float(wf_sharpe) <= 1.5:
                    blockers.append(f"wf_sharpe={wf_sharpe} <= 1.5 minimum")
            except (ValueError, TypeError):
                blockers.append(f"wf_sharpe='{wf_sharpe}' is not a valid float")

        # Gate 3: Bootstrap p-value
        bootstrap_p = self._get_val(evidence, ["bootstrap_p", "p_value", "bootstrap_pvalue"], None)
        if bootstrap_p is None:
            blockers.append("bootstrap_p missing from evidence")
        else:
            try:
                if float(bootstrap_p) >= 0.05:
                    blockers.append(f"bootstrap_p={bootstrap_p} >= 0.05 (not statistically significant)")
            except (ValueError, TypeError):
                blockers.append(f"bootstrap_p='{bootstrap_p}' is not a valid float")

        # Gate 4: 1-tick slippage Sharpe must be positive
        slip_1tick = self._get_val(
            evidence,
            ["slippage_1tick_sharpe", "slippage_1tick", "slip_1tick_sharpe"],
            None,
        )
        if slip_1tick is None:
            blockers.append("slippage_1tick_sharpe missing from evidence")
        else:
            try:
                if float(slip_1tick) <= 0:
                    blockers.append(
                        f"slippage_1tick_sharpe={slip_1tick} <= 0 "
                        "(strategy fails at 1-tick slippage)"
                    )
            except (ValueError, TypeError):
                blockers.append(f"slippage_1tick_sharpe='{slip_1tick}' is not a valid float")

        # Gate 5: News-filtered result must exist in evidence
        has_news_filter = (
            evidence.get("news_filtered") is not None
            or evidence.get("news_filter_result") is not None
            or evidence.get("news_filtered_sharpe") is not None
            or evidence.get("news_exclusion_tested", False)
        )
        if not has_news_filter:
            blockers.append(
                "No news-filtered backtest result in evidence. "
                "Strategy must be tested excluding high-impact news periods."
            )

        # Gate 6: Data must cover >= 4 years
        data_years = self._get_val(
            system_state,
            ["data_years", "years_of_data", "history_years"],
            None,
        )
        if data_years is None:
            # Also check strategy or evidence
            data_years = self._get_val(strategy, ["data_years"], None)
        if data_years is None:
            blockers.append("data_years not provided in system_state or strategy")
        else:
            try:
                if float(data_years) < 4.0:
                    blockers.append(
                        f"data_years={data_years} < 4.0 — "
                        "at least 4 years of data required for robust validation"
                    )
            except (ValueError, TypeError):
                blockers.append(f"data_years='{data_years}' is not a valid float")

        # Gate 7: Bracket orders tested
        bracket_tested = system_state.get("bracket_orders_tested", False)
        if not bracket_tested:
            blockers.append(
                "bracket_orders_tested=False — bracket order execution must be "
                "verified in simulation before deployment"
            )

        # Gate 8: State persistence verified
        state_verified = system_state.get("state_persistence_verified", False)
        if not state_verified:
            blockers.append(
                "state_persistence_verified=False — strategy state persistence "
                "across restarts must be verified before deployment"
            )

        eligible = len(blockers) == 0

        risk = "HIGH" if not eligible else "MEDIUM"
        self.dlog.log(
            agent="DeploymentGatekeeper",
            observation=(
                f"Eligibility check for '{strategy_key}': "
                f"{'ELIGIBLE' if eligible else 'BLOCKED'}. "
                f"Blockers ({len(blockers)}): {blockers[:3]}"
                + (" ..." if len(blockers) > 3 else "")
            ),
            recommendation=(
                "Strategy may be considered for paper trading. "
                "Human approval is still required."
                if eligible
                else f"Strategy blocked from deployment. Fix all {len(blockers)} blockers first."
            ),
            action_taken="eligibility_checked",
            human_approval_required=eligible,  # eligible strategies need human sign-off
            risk_level=risk,
            metadata={
                "strategy_key": strategy_key,
                "eligible": eligible,
                "n_blockers": len(blockers),
                "blockers": blockers,
            },
        )

        return eligible, blockers

    # ── Approval methods ───────────────────────────────────────────────────────

    def approve_for_paper(
        self,
        strategy_key: str,
        evidence: dict,
        decision_log: Optional[DecisionLog] = None,
    ) -> None:
        """
        Log a paper trading approval request. NEVER auto-approves.

        This method ALWAYS:
          1. Logs the request with human_approval_required=True
          2. Prints a hard warning to stdout
          3. Does NOT enable paper trading — a human must act on the log entry

        Args:
            strategy_key:  Unique strategy identifier.
            evidence:      Evidence dict for this strategy.
            decision_log:  Optional override for the decision log instance.
        """
        log = decision_log or self.dlog

        print(
            "\n"
            "=" * 70 + "\n"
            "  HUMAN APPROVAL REQUIRED before paper trading.\n"
            f"  Strategy: {strategy_key}\n"
            "  This agent has logged the request but has NOT enabled anything.\n"
            "  A human must review the evidence and explicitly authorise paper trading.\n"
            + "=" * 70
        )

        log.log(
            agent="DeploymentGatekeeper",
            observation=(
                f"Paper trading approval requested for strategy '{strategy_key}'. "
                f"Evidence keys present: {list(evidence.keys())[:8]}"
            ),
            recommendation=(
                f"Human must review evidence for '{strategy_key}' and "
                "explicitly enable paper trading in the system configuration. "
                "This agent has NOT done so."
            ),
            action_taken="pending",  # pending = requires human action
            human_approval_required=True,
            risk_level="HIGH",
            metadata={
                "strategy_key": strategy_key,
                "auto_approved": False,
                "note": "HUMAN APPROVAL REQUIRED — agent cannot auto-approve",
            },
        )

    def approve_for_live(self, strategy_key: str) -> None:
        """
        Attempt to approve a strategy for live trading.

        This method ALWAYS raises PermissionError. No AI agent may approve
        live trading deployment. This is a hard architectural constraint.

        Args:
            strategy_key: Unique strategy identifier (logged before raising).

        Raises:
            PermissionError: Always. Live trading requires human approval.
        """
        self.dlog.log(
            agent="DeploymentGatekeeper",
            observation=(
                f"Live trading deployment attempted for '{strategy_key}'. "
                "BLOCKED — agent cannot approve live trading."
            ),
            recommendation=(
                "Human must explicitly configure live trading. "
                "Automated approval is not permitted under any circumstances."
            ),
            action_taken="live_deployment_blocked",
            human_approval_required=True,
            risk_level="CRITICAL",
            metadata={
                "strategy_key": strategy_key,
                "auto_approved": False,
            },
        )

        raise PermissionError(
            f"Live trading deployment requires explicit human approval. "
            f"This agent cannot approve live trading. Strategy: {strategy_key}"
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_val(self, source: dict, keys: List[str], default):
        """Try each key in order and return the first match, or default."""
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
        return default
