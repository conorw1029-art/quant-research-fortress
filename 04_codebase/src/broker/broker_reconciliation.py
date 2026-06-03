"""
broker_reconciliation.py — Position reconciliation between internal state and broker
=====================================================================================
The reconciler answers: "Does our internal book match what the broker sees?"

This is critical for live trading safety:
  1. On startup: check that no phantom positions exist from a prior crash
  2. After any connectivity gap: verify broker state matches our records
  3. Periodic checks: detect any silent discrepancies

Reconciliation policy:
  - Any mismatch → logged immediately to reconciliation_YYYYMMDD.jsonl
  - If is_clean=False → the system should NOT place new orders until resolved
  - The reconciler NEVER modifies positions — it only reports discrepancies
  - A human (or DeploymentGatekeeper) must approve any corrective action

Output file:
  06_live_trading/logs/reconciliation_YYYYMMDD.jsonl

Usage:
    from src.broker.broker_reconciliation import BrokerReconciler

    reconciler = BrokerReconciler()
    result = reconciler.compare(
        internal_state={"GC": 1, "SI": -1},
        broker_positions=[
            BrokerPosition(symbol="GC", qty=1, avg_price=2000.0),
        ]
    )
    if not result.is_clean:
        for m in result.mismatches:
            print(m)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from .broker_models import BrokerPosition, ReconciliationResult

if TYPE_CHECKING:
    from .base import BrokerAdapter

logger = logging.getLogger(__name__)

# Path to reconciliation log directory
_LOG_DIR = Path(__file__).parents[3] / "06_live_trading" / "logs"


class BrokerReconciler:
    """
    Compares internal system position state against live broker positions.

    The reconciler is stateless — each call to compare() or
    reconcile_on_startup() is independent.

    Args:
        log_dir: Override the default log directory for reconciliation logs.
                 Default: <repo_root>/06_live_trading/logs/
    """

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or _LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ── Core comparison ────────────────────────────────────────────────────────

    def compare(
        self,
        internal_state:   Dict[str, int],
        broker_positions: List[BrokerPosition],
    ) -> ReconciliationResult:
        """
        Compare the system's internal position book against broker-reported positions.

        Args:
            internal_state:   {symbol: signed_qty} from our state manager.
                              Only include non-zero positions.
            broker_positions: List of BrokerPosition objects from get_positions().

        Returns:
            ReconciliationResult with is_clean=True only if no mismatches.

        Mismatch types detected:
            - broker_only:    Broker reports a position we have no record of
            - internal_only:  We think we have a position the broker doesn't see
            - qty_mismatch:   Both sides agree on symbol but disagree on quantity
            - side_mismatch:  Both sides agree on symbol but disagree on direction
        """
        # Build broker state dict, excluding flat positions
        broker_state: Dict[str, int] = {}
        for pos in broker_positions:
            if pos.qty != 0:
                broker_state[pos.symbol] = pos.qty

        result = ReconciliationResult(
            internal_positions = dict(internal_state),
            broker_positions   = broker_state,
        )

        all_symbols = set(internal_state) | set(broker_state)

        for sym in sorted(all_symbols):
            internal_qty = internal_state.get(sym, 0)
            broker_qty   = broker_state.get(sym, 0)

            if internal_qty == broker_qty:
                continue  # Clean

            if internal_qty == 0 and broker_qty != 0:
                # Broker has a position we know nothing about — dangerous
                result.add_mismatch({
                    "type":        "broker_only",
                    "symbol":      sym,
                    "internal":    0,
                    "broker":      broker_qty,
                    "delta":       broker_qty,
                    "severity":    "HIGH",
                    "description": (
                        f"Broker reports {sym} position of {broker_qty:+d} "
                        f"but our internal state shows flat. "
                        f"Possible: unrecorded fill or stale state."
                    ),
                })

            elif internal_qty != 0 and broker_qty == 0:
                # We think we're positioned but broker says flat — also dangerous
                result.add_mismatch({
                    "type":        "internal_only",
                    "symbol":      sym,
                    "internal":    internal_qty,
                    "broker":      0,
                    "delta":       -internal_qty,
                    "severity":    "HIGH",
                    "description": (
                        f"Internal state shows {sym} position of {internal_qty:+d} "
                        f"but broker reports flat. "
                        f"Possible: missed fill, cancelled bracket, or state corruption."
                    ),
                })

            else:
                # Both sides see a position, but quantities differ
                if (internal_qty > 0) != (broker_qty > 0):
                    mismatch_type = "side_mismatch"
                    severity      = "CRITICAL"
                    description   = (
                        f"{sym}: internal={internal_qty:+d} ({_side(internal_qty)}) "
                        f"vs broker={broker_qty:+d} ({_side(broker_qty)}) — "
                        f"SIDES DISAGREE. This is a critical state error."
                    )
                else:
                    mismatch_type = "qty_mismatch"
                    severity      = "MEDIUM"
                    description   = (
                        f"{sym}: internal={internal_qty:+d} vs broker={broker_qty:+d} "
                        f"(delta={broker_qty - internal_qty:+d})"
                    )

                result.add_mismatch({
                    "type":        mismatch_type,
                    "symbol":      sym,
                    "internal":    internal_qty,
                    "broker":      broker_qty,
                    "delta":       broker_qty - internal_qty,
                    "severity":    severity,
                    "description": description,
                })

        self._log_result(result)
        return result

    def reconcile_on_startup(
        self,
        adapter:       "BrokerAdapter",
        state_manager,           # duck-typed — must have get_positions() -> Dict[str, int]
    ) -> ReconciliationResult:
        """
        Run reconciliation at system startup before any orders are placed.

        This is the primary safety check to ensure the internal state matches
        the broker after any outage, crash, or restart.

        Args:
            adapter:       Connected BrokerAdapter (will call get_positions())
            state_manager: Object with get_positions() -> Dict[str, int]
                           (e.g. src.execution.StateManager or similar)

        Returns:
            ReconciliationResult. If is_clean=False, the caller should halt
            order placement until discrepancies are investigated.
        """
        logger.info("Starting reconciliation on startup...")

        # Get internal state
        try:
            if hasattr(state_manager, "get_positions"):
                internal = state_manager.get_positions()
            elif isinstance(state_manager, dict):
                internal = state_manager
            else:
                internal = {}
                logger.warning("state_manager has no get_positions() — assuming empty")
        except Exception as e:
            logger.error("Failed to read internal state: %s", e)
            internal = {}

        # Get broker positions
        try:
            broker_positions = adapter.get_positions()
        except Exception as e:
            logger.error("Failed to read broker positions: %s", e)
            # Return a special result indicating we couldn't check
            result = ReconciliationResult(
                internal_positions = internal,
                broker_positions   = {},
            )
            result.add_mismatch({
                "type":        "connection_error",
                "symbol":      "ALL",
                "internal":    0,
                "broker":      0,
                "delta":       0,
                "severity":    "CRITICAL",
                "description": f"Could not read broker positions: {e}",
            })
            self._log_result(result)
            return result

        result = self.compare(internal, broker_positions)

        if result.is_clean:
            logger.info("Startup reconciliation CLEAN — positions match")
        else:
            logger.error(
                "Startup reconciliation FAILED — %d mismatches. "
                "Do NOT place orders until resolved. See logs.",
                len(result.mismatches),
            )
            for m in result.mismatches:
                logger.error(
                    "  [%s] %s: internal=%s broker=%s",
                    m.get("severity", "?"), m.get("symbol", "?"),
                    m.get("internal", "?"), m.get("broker", "?"),
                )

        return result

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_result(self, result: ReconciliationResult) -> None:
        """Append reconciliation result to daily JSONL log."""
        today     = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path  = self.log_dir / f"reconciliation_{today}.jsonl"

        entry = {
            "checked_at":         result.checked_at.isoformat(),
            "is_clean":           result.is_clean,
            "mismatch_count":     len(result.mismatches),
            "internal_positions": result.internal_positions,
            "broker_positions":   result.broker_positions,
            "mismatches":         result.mismatches,
        }

        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("Failed to write reconciliation log: %s", e)

    def get_today_log(self) -> List[dict]:
        """Read today's reconciliation log entries."""
        today    = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = self.log_dir / f"reconciliation_{today}.jsonl"
        results  = []
        if not log_path.exists():
            return results
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _side(qty: int) -> str:
    if qty > 0:
        return "LONG"
    if qty < 0:
        return "SHORT"
    return "FLAT"
