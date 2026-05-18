"""
tick_broker_reconciliation.py — Broker State Reconciliation
============================================================
Pure functions that compare local state vs a broker snapshot.

NO broker connections. NO API calls. NO orders.
These functions take data dicts as input and return structured results.
The caller is responsible for fetching broker data and passing it in.

Usage:
  from tick_broker_reconciliation import reconcile_state, ReconcileResult

  local  = state_manager.load_positions()
  broker = tradovate_client.get_positions_dict()   # called by executor, not here
  result = reconcile_state(local_state, broker_state)
  if not result["ok"]:
      log_critical(result["reason"])
      halt_new_entries()
"""

from datetime import datetime, timezone
from typing import Optional


# ── Result severity levels ────────────────────────────────────────────────────

INFO     = "INFO"
WARNING  = "WARNING"
CRITICAL = "CRITICAL"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(ok: bool, severity: str, halt: bool, human: bool,
            actions: list, reason: str) -> dict:
    return {
        "ok":                   ok,
        "severity":             severity,
        "halt_new_entries":     halt,
        "requires_human_review": human,
        "actions":              actions,
        "reason":               reason,
        "timestamp":            _now(),
    }


def _ok_result(reason: str = "reconciled") -> dict:
    return _result(True, INFO, False, False, ["CONTINUE"], reason)


def _warn_result(reason: str, actions: list = None) -> dict:
    return _result(False, WARNING, False, True,
                   actions or ["ALERT_USER", "INVESTIGATE"], reason)


def _critical_result(reason: str, actions: list = None) -> dict:
    return _result(False, CRITICAL, True, True,
                   actions or ["HALT_NEW_ENTRIES", "ALERT_USER", "WAIT_FOR_HUMAN"],
                   reason)


# ── Local state helpers ───────────────────────────────────────────────────────

def _local_positions(local_state: dict) -> dict:
    """Extract {symbol: net_pos} from local positions state."""
    raw = local_state.get("positions", {})
    if isinstance(raw, dict):
        result = {}
        for sym, p in raw.items():
            if isinstance(p, dict):
                result[sym] = p.get("net_pos", 0)
            elif isinstance(p, (int, float)):
                result[sym] = int(p)
        return {s: n for s, n in result.items() if n != 0}
    return {}


def _local_brackets(local_state: dict) -> dict:
    """Extract {strategy_id: bracket} from active_brackets state."""
    return local_state.get("brackets", {})


# ── Core reconciliation ───────────────────────────────────────────────────────

def reconcile_positions(local_positions: dict, broker_positions: dict) -> list[dict]:
    """
    Compare local position dict vs broker position dict.
    Both dicts: {symbol: net_pos_int}

    Returns list of reconciliation findings (may be empty = clean).
    """
    findings = []
    all_symbols = set(local_positions) | set(broker_positions)

    for sym in all_symbols:
        local_pos  = local_positions.get(sym, 0)
        broker_pos = broker_positions.get(sym, 0)

        if local_pos == 0 and broker_pos != 0:
            # Scenario 1 — ghost position at broker
            findings.append(_critical_result(
                reason=f"GHOST_POSITION: local flat but broker has {sym} net_pos={broker_pos}",
                actions=["HALT_NEW_ENTRIES", "ALERT_USER", "DO_NOT_AUTO_FLATTEN",
                         "WAIT_FOR_HUMAN"]
            ))

        elif local_pos != 0 and broker_pos == 0:
            # Scenario 2 — position lost at broker
            findings.append(_warn_result(
                reason=f"POSITION_LOST: local has {sym} net_pos={local_pos} but broker is flat",
                actions=["HALT_STRATEGY_FOR_SYMBOL", "UPDATE_LOCAL_STATE_TO_FLAT",
                         "ALERT_USER", "RECONCILE_PNL"]
            ))

        elif local_pos != 0 and broker_pos != 0 and local_pos != broker_pos:
            # Scenario 9 — quantity mismatch
            findings.append(_critical_result(
                reason=f"QUANTITY_MISMATCH: {sym} local={local_pos} broker={broker_pos}",
                actions=["HALT_NEW_ENTRIES", "ALERT_USER", "WAIT_FOR_HUMAN"]
            ))

    return findings


def reconcile_brackets(local_brackets: dict, broker_orders: dict) -> list[dict]:
    """
    Check that every local active bracket has its stop and target orders at broker.
    broker_orders: {order_id: {"symbol", "ordStatus", ...}}

    Returns list of findings.
    """
    findings = []
    broker_ids = set(str(oid) for oid in broker_orders.keys())

    for strategy_id, bracket in local_brackets.items():
        if not isinstance(bracket, dict):
            continue

        stop_id   = str(bracket.get("stop_order_id", "")) if bracket.get("stop_order_id") else None
        target_id = str(bracket.get("target_order_id", "")) if bracket.get("target_order_id") else None
        sym       = bracket.get("symbol", "?")

        if not bracket.get("entry_filled", False):
            # Entry not yet filled — bracket legs not expected at broker yet
            continue

        if stop_id and stop_id not in broker_ids:
            findings.append(_critical_result(
                reason=f"MISSING_STOP: strategy {strategy_id} {sym} stop_order {stop_id} not found at broker",
                actions=["HALT_NEW_ENTRIES", "ALERT_USER",
                         "DO_NOT_ASSUME_PROTECTION_EXISTS", "WAIT_FOR_HUMAN"]
            ))

        if target_id and target_id not in broker_ids:
            findings.append(_critical_result(
                reason=f"MISSING_TARGET: strategy {strategy_id} {sym} target_order {target_id} not found at broker",
                actions=["HALT_NEW_ENTRIES", "ALERT_USER", "WAIT_FOR_HUMAN"]
            ))

    return findings


def reconcile_duplicate_orders(broker_orders: dict) -> list[dict]:
    """
    Detect duplicate broker orders for the same symbol+side+type combination.
    broker_orders: {order_id: {"symbol", "action", "orderType", "ordStatus", ...}}

    Note: A valid bracket always has one Limit (target) + one Stop for the same
    symbol/action — this is an OCO pair, not a duplicate. Only flag as duplicate
    when multiple orders share the same symbol + action + orderType.
    """
    findings = []
    # Group working orders by (symbol, action, orderType)
    working: dict = {}
    for oid, order in broker_orders.items():
        status = order.get("ordStatus", "")
        if status not in ("Working", "Accepted", "PendingNew", "ContingencyOrder"):
            continue
        key = (order.get("symbol", ""),
               order.get("action", ""),
               order.get("orderType", "Unknown"))
        working.setdefault(key, []).append(oid)

    for (sym, action, order_type), ids in working.items():
        if len(ids) > 1:
            findings.append(_critical_result(
                reason=(f"DUPLICATE_ORDERS: {sym} {action} {order_type} "
                        f"has {len(ids)} working orders {ids}"),
                actions=["HALT_AFFECTED_STRATEGY", "ALERT_USER",
                         "DO_NOT_AUTO_CANCEL", "WAIT_FOR_HUMAN"]
            ))

    return findings


def reconcile_unknown_orders(broker_orders: dict, local_brackets: dict) -> list[dict]:
    """
    Detect broker orders that are not tracked in any local bracket.
    broker_orders: {order_id: {symbol, action, ordStatus}}
    """
    findings = []
    known_ids = set()
    for bracket in local_brackets.values():
        if isinstance(bracket, dict):
            for key in ("entry_order_id", "stop_order_id", "target_order_id"):
                oid = bracket.get(key)
                if oid:
                    known_ids.add(str(oid))

    for oid, order in broker_orders.items():
        status = order.get("ordStatus", "")
        if status not in ("Working", "Accepted", "PendingNew", "ContingencyOrder"):
            continue
        if str(oid) not in known_ids:
            findings.append(_warn_result(
                reason=f"UNKNOWN_BROKER_ORDER: order {oid} ({order.get('symbol', '?')} "
                       f"{order.get('action', '?')}) not tracked locally",
                actions=["ALERT_USER", "INVESTIGATE", "DO_NOT_AUTO_CANCEL"]
            ))

    return findings


def reconcile_stale_local(local_state: dict, max_age_seconds: int = 600) -> list[dict]:
    """
    Check whether local state is too old to be trusted.
    local_state should contain a 'last_updated' ISO timestamp.
    """
    last_updated = local_state.get("last_updated")
    if not last_updated:
        return [_warn_result(
            reason="STALE_LOCAL_STATE: last_updated is None — state was never written",
            actions=["RECONCILE_WITH_BROKER_BEFORE_TRADING"]
        )]
    try:
        ts  = datetime.fromisoformat(last_updated)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > max_age_seconds:
            return [_warn_result(
                reason=f"STALE_LOCAL_STATE: last_updated {age:.0f}s ago (max {max_age_seconds}s)",
                actions=["RECONCILE_WITH_BROKER_BEFORE_TRADING"]
            )]
    except Exception:
        return [_warn_result(
            reason=f"STALE_LOCAL_STATE: cannot parse last_updated={last_updated!r}",
        )]
    return []


def reconcile_broker_unreachable() -> dict:
    """Snapshot to use when broker API is unreachable."""
    return _critical_result(
        reason="BROKER_UNREACHABLE: cannot fetch broker state",
        actions=["HALT_NEW_ENTRIES", "KEEP_BROKER_NATIVE_STOPS_ACTIVE",
                 "ALERT_USER", "RETRY_WITH_BACKOFF"]
    )


# ── Master reconciliation entry point ─────────────────────────────────────────

def reconcile_state(local_state: dict, broker_state: dict) -> dict:
    """
    Master reconciliation: compare all local state dimensions against broker snapshot.

    Args:
        local_state:  Combined dict with keys: positions, brackets, last_updated
                      (from StateManager.load_positions() + load_active_brackets())
        broker_state: {"positions": {sym: net_pos}, "orders": {id: order_dict},
                       "reachable": True/False}

    Returns structured result:
        {ok, severity, halt_new_entries, requires_human_review, actions, reason}
    """
    # Broker unreachable?
    if not broker_state.get("reachable", True):
        return reconcile_broker_unreachable()

    all_findings = []

    # Position checks
    local_pos  = _local_positions(local_state)
    broker_pos = broker_state.get("positions", {})
    all_findings.extend(reconcile_positions(local_pos, broker_pos))

    # Bracket checks
    local_brackets = _local_brackets(local_state)
    broker_orders  = broker_state.get("orders", {})
    all_findings.extend(reconcile_brackets(local_brackets, broker_orders))

    # Duplicate order checks
    all_findings.extend(reconcile_duplicate_orders(broker_orders))

    # Unknown order checks
    all_findings.extend(reconcile_unknown_orders(broker_orders, local_brackets))

    # Stale local state check
    all_findings.extend(reconcile_stale_local(local_state))

    # Aggregate: most severe finding wins
    if not all_findings:
        return _ok_result("All local state matches broker state")

    criticals = [f for f in all_findings if f["severity"] == CRITICAL]
    warnings  = [f for f in all_findings if f["severity"] == WARNING]

    if criticals:
        reasons = " | ".join(f["reason"] for f in criticals)
        return _critical_result(reason=reasons)
    if warnings:
        reasons = " | ".join(f["reason"] for f in warnings)
        return _warn_result(reason=reasons)

    return _ok_result()


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Scenario 1: Clean state
    local = {"positions": {}, "brackets": {},
             "last_updated": datetime.now(timezone.utc).isoformat()}
    broker = {"reachable": True, "positions": {}, "orders": {}}
    r = reconcile_state(local, broker)
    assert r["ok"] and not r["halt_new_entries"], f"Clean test failed: {r}"
    print("PASS — clean state")

    # Scenario 2: Ghost position at broker
    local2 = {"positions": {}, "brackets": {},
              "last_updated": datetime.now(timezone.utc).isoformat()}
    broker2 = {"reachable": True, "positions": {"MESM5": 1}, "orders": {}}
    r2 = reconcile_state(local2, broker2)
    assert not r2["ok"] and r2["severity"] == CRITICAL and r2["halt_new_entries"], f"Ghost test failed: {r2}"
    print("PASS — ghost position at broker")

    # Scenario 3: Position lost at broker
    local3 = {"positions": {"MESM5": {"net_pos": 1, "entry_px": 5320.0}},
              "brackets": {},
              "last_updated": datetime.now(timezone.utc).isoformat()}
    broker3 = {"reachable": True, "positions": {}, "orders": {}}
    r3 = reconcile_state(local3, broker3)
    assert not r3["ok"], f"Position lost test failed: {r3}"
    print("PASS — position lost at broker")

    # Scenario 4: Broker unreachable
    r4 = reconcile_state({}, {"reachable": False})
    assert not r4["ok"] and r4["severity"] == CRITICAL, f"Unreachable test failed: {r4}"
    print("PASS — broker unreachable")

    # Scenario 5: Duplicate orders
    broker5 = {
        "reachable": True, "positions": {},
        "orders": {
            "1001": {"symbol": "MESM5", "action": "Sell", "ordStatus": "Working"},
            "1002": {"symbol": "MESM5", "action": "Sell", "ordStatus": "Working"},
        }
    }
    r5 = reconcile_state({"positions": {}, "brackets": {},
                           "last_updated": datetime.now(timezone.utc).isoformat()},
                          broker5)
    assert not r5["ok"] and r5["severity"] == CRITICAL, f"Duplicate test failed: {r5}"
    print("PASS — duplicate orders")

    print("All standalone tests passed")
