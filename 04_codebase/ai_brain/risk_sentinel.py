"""
risk_sentinel.py — Real-time risk boundary enforcement
=======================================================
RiskSentinel checks whether current P&L, drawdown, and correlation exposure
are within acceptable limits. It never places trades or modifies positions.

Allowed actions:
  - Report limit breaches
  - Recommend pausing a strategy (still requires system to act)
  - Log all assessments

Forbidden actions:
  - Increasing position size (always blocked — requires human)
  - Enabling new live trading
  - Overriding the kill switch

Usage:
    from ai_brain.risk_sentinel import RiskSentinel

    sentinel = RiskSentinel(decision_log=log)
    ok, pct = sentinel.assess_daily_pnl(current_pnl=-800.0, daily_limit=1000.0)
    report = sentinel.generate_risk_report(...)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ai_brain.decision_log import DecisionLog

logger = logging.getLogger(__name__)

# Symbol families for correlation exposure check
# Symbols in the same family are treated as correlated
SYMBOL_FAMILIES: Dict[str, str] = {
    "GC":  "metals_gold",
    "MGC": "metals_gold",
    "SI":  "metals_silver",
    "MSI": "metals_silver",
    "HG":  "metals_copper",
    "PL":  "metals_platinum",
    "PA":  "metals_palladium",
    "CL":  "energy_crude",
    "QM":  "energy_crude",
    "NG":  "energy_gas",
    "ES":  "equity_sp500",
    "MES": "equity_sp500",
    "NQ":  "equity_nasdaq",
    "MNQ": "equity_nasdaq",
    "YM":  "equity_dow",
    "RTY": "equity_russell",
    "ZB":  "bonds_treasury",
    "ZN":  "bonds_treasury",
    "ZF":  "bonds_treasury",
    "ZT":  "bonds_treasury",
}


class RiskSentinel:
    """
    Monitors daily P&L, trailing drawdown, and correlation exposure.

    Args:
        decision_log: DecisionLog instance. Created with defaults if None.
    """

    def __init__(self, decision_log: Optional[DecisionLog] = None):
        self.dlog = decision_log or DecisionLog()

    # ── Core assessments ───────────────────────────────────────────────────────

    def assess_daily_pnl(
        self,
        current_pnl: float,
        daily_limit: float,
    ) -> Tuple[bool, float]:
        """
        Check whether daily P&L is within the allowed daily loss limit.

        Args:
            current_pnl:  Today's realized + unrealized P&L (negative = loss).
            daily_limit:  Maximum allowed daily loss expressed as a positive number.
                          E.g. 1000.0 means max $1,000 daily loss.

        Returns:
            (is_ok, pct_of_limit_used)
            is_ok:              True if the limit has not been breached.
            pct_of_limit_used:  How much of the daily limit has been consumed.
                                0.0 = no loss, 1.0 = at limit, >1.0 = breached.
        """
        if daily_limit <= 0:
            raise ValueError("daily_limit must be positive.")

        loss = max(0.0, -current_pnl)  # loss is positive number
        pct = loss / daily_limit
        is_ok = pct < 1.0

        risk_level = (
            "CRITICAL" if pct >= 1.0
            else "HIGH" if pct >= 0.8
            else "MEDIUM" if pct >= 0.5
            else "LOW"
        )

        self.dlog.log(
            agent="RiskSentinel",
            observation=(
                f"Daily P&L assessment: pnl={current_pnl:.2f}, "
                f"limit={daily_limit:.2f}, loss={loss:.2f}, "
                f"pct_used={pct:.1%}, is_ok={is_ok}"
            ),
            recommendation=(
                "HALT TRADING — daily loss limit breached."
                if not is_ok
                else f"Daily loss at {pct:.1%} of limit."
                if pct >= 0.5
                else "Daily P&L within normal range."
            ),
            action_taken="daily_pnl_assessed",
            human_approval_required=not is_ok,
            risk_level=risk_level,
            metadata={"current_pnl": current_pnl, "daily_limit": daily_limit, "pct_used": round(pct, 4)},
        )

        return is_ok, round(pct, 4)

    def assess_trailing_dd(
        self,
        peak_equity: float,
        current_equity: float,
        dd_limit: float,
    ) -> Tuple[bool, float, float]:
        """
        Check whether trailing drawdown from peak equity exceeds the limit.

        Args:
            peak_equity:    Highest equity value reached (watermark).
            current_equity: Current account equity.
            dd_limit:       Maximum allowed drawdown as a positive dollar amount.

        Returns:
            (is_ok, dd_amount, pct_of_limit)
            is_ok:         True if drawdown is within the limit.
            dd_amount:     Current drawdown in dollars (positive = loss from peak).
            pct_of_limit:  dd_amount / dd_limit.
        """
        if dd_limit <= 0:
            raise ValueError("dd_limit must be positive.")

        dd_amount = max(0.0, peak_equity - current_equity)
        pct = dd_amount / dd_limit
        is_ok = pct < 1.0

        risk_level = (
            "CRITICAL" if pct >= 1.0
            else "HIGH" if pct >= 0.8
            else "MEDIUM" if pct >= 0.5
            else "LOW"
        )

        self.dlog.log(
            agent="RiskSentinel",
            observation=(
                f"Trailing drawdown assessment: peak={peak_equity:.2f}, "
                f"current={current_equity:.2f}, dd={dd_amount:.2f}, "
                f"limit={dd_limit:.2f}, pct={pct:.1%}"
            ),
            recommendation=(
                "HALT TRADING — trailing drawdown limit breached."
                if not is_ok
                else f"Drawdown at {pct:.1%} of limit — monitor closely."
                if pct >= 0.5
                else "Drawdown within acceptable range."
            ),
            action_taken="trailing_dd_assessed",
            human_approval_required=not is_ok,
            risk_level=risk_level,
            metadata={
                "peak_equity": peak_equity,
                "current_equity": current_equity,
                "dd_amount": round(dd_amount, 2),
                "pct_of_limit": round(pct, 4),
            },
        )

        return is_ok, round(dd_amount, 2), round(pct, 4)

    def assess_correlation_exposure(
        self,
        positions: List[dict],
    ) -> dict:
        """
        Check for multiple open positions within the same symbol family.

        Correlated positions amplify risk beyond what any single position shows.
        This check flags whenever >= 2 positions share a symbol family.

        Args:
            positions: List of position dicts. Each should have at minimum:
                       {"symbol": str, "size": float} — size can be 0 for flat.

        Returns:
            {
                "has_correlation_risk": bool,
                "family_exposure": {family_name: [symbol, ...]},
                "correlated_families": [family_name, ...],  # those with >1 symbol
                "recommendation": str,
            }
        """
        family_map: Dict[str, List[str]] = defaultdict(list)

        for pos in positions:
            symbol = str(pos.get("symbol", "")).upper().strip()
            size = pos.get("size", 0)
            # Only count non-flat positions
            if size != 0 and symbol:
                family = SYMBOL_FAMILIES.get(symbol, f"unknown_{symbol}")
                family_map[family].append(symbol)

        correlated = {f: syms for f, syms in family_map.items() if len(syms) > 1}
        has_risk = len(correlated) > 0

        recommendation = (
            f"Correlated exposure detected in families: {list(correlated.keys())}. "
            "Consider reducing position count in the same symbol family."
            if has_risk
            else "No correlated family exposure detected."
        )

        result = {
            "has_correlation_risk": has_risk,
            "family_exposure": dict(family_map),
            "correlated_families": list(correlated.keys()),
            "recommendation": recommendation,
        }

        if has_risk:
            self.dlog.log(
                agent="RiskSentinel",
                observation=(
                    f"Correlated exposure in {len(correlated)} symbol families: "
                    + str(correlated)
                ),
                recommendation=recommendation,
                action_taken="correlation_risk_flagged",
                human_approval_required=False,
                risk_level="MEDIUM",
                metadata=result,
            )

        return result

    # ── Capability gates ───────────────────────────────────────────────────────

    def can_pause_strategy(
        self,
        strategy_name: str,
    ) -> Tuple[bool, str]:
        """
        Check whether the risk sentinel is permitted to pause a strategy.

        This is always allowed — pausing is a safe defensive action.

        Returns:
            (True, reason_str)
        """
        reason = "Risk sentinel may pause strategies in response to limit breaches."
        self.dlog.log(
            agent="RiskSentinel",
            observation=f"Pause eligibility checked for strategy: {strategy_name}",
            recommendation=reason,
            action_taken="pause_eligibility_confirmed",
            human_approval_required=False,
            risk_level="LOW",
        )
        return True, reason

    def can_increase_size(self) -> Tuple[bool, str]:
        """
        Check whether the risk sentinel is permitted to increase position size.

        This is ALWAYS False. Size increases require explicit human approval.

        Returns:
            (False, reason_str)
        """
        reason = "Risk sentinel cannot increase position size. Human approval required."
        self.dlog.log(
            agent="RiskSentinel",
            observation="Size increase requested — denied at agent level.",
            recommendation=reason,
            action_taken="size_increase_blocked",
            human_approval_required=True,
            risk_level="HIGH",
        )
        return False, reason

    # ── Aggregated report ──────────────────────────────────────────────────────

    def generate_risk_report(
        self,
        daily_pnl: float,
        peak_equity: float,
        current_equity: float,
        daily_limit: float,
        dd_limit: float,
        positions: Optional[List[dict]] = None,
    ) -> dict:
        """
        Run all risk assessments and return a unified risk status report.

        Args:
            daily_pnl:       Today's net P&L.
            peak_equity:     Session or rolling peak equity.
            current_equity:  Current account equity.
            daily_limit:     Daily loss limit (positive dollars).
            dd_limit:        Trailing drawdown limit (positive dollars).
            positions:       List of open position dicts (optional).

        Returns:
            Full risk report dict including all individual assessment results
            and an overall "system_ok" flag.
        """
        pnl_ok, pnl_pct = self.assess_daily_pnl(daily_pnl, daily_limit)
        dd_ok, dd_amount, dd_pct = self.assess_trailing_dd(peak_equity, current_equity, dd_limit)
        corr = self.assess_correlation_exposure(positions or [])

        system_ok = pnl_ok and dd_ok and not corr["has_correlation_risk"]

        report = {
            "system_ok": system_ok,
            "daily_pnl": {
                "is_ok": pnl_ok,
                "current_pnl": daily_pnl,
                "daily_limit": daily_limit,
                "pct_of_limit_used": pnl_pct,
            },
            "trailing_drawdown": {
                "is_ok": dd_ok,
                "peak_equity": peak_equity,
                "current_equity": current_equity,
                "dd_amount": dd_amount,
                "dd_limit": dd_limit,
                "pct_of_limit": dd_pct,
            },
            "correlation_exposure": corr,
            "can_increase_size": False,
            "actions_available": ["pause_strategy", "alert_human", "log_observation"],
            "actions_forbidden": [
                "place_orders",
                "increase_size",
                "enable_live",
                "override_kill_switch",
            ],
        }

        return report
