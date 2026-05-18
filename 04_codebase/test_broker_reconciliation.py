"""
test_broker_reconciliation.py — Tests for tick_broker_reconciliation.py
========================================================================
All tests use pure mocked data only.
No broker connection. No API calls. No orders.

Run:
  venv_new\Scripts\python.exe -X utf8 test_broker_reconciliation.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tick_broker_reconciliation import (
    reconcile_state, reconcile_positions, reconcile_brackets,
    reconcile_duplicate_orders, reconcile_unknown_orders, reconcile_stale_local,
    reconcile_broker_unreachable, CRITICAL, WARNING, INFO,
)

PASS_COUNT = 0
FAIL_COUNT = 0


def _check(name: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}  {detail}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _local(positions=None, brackets=None, ts=None):
    return {
        "positions": positions or {},
        "brackets":  brackets or {},
        "last_updated": ts or _now(),
    }


def _broker(positions=None, orders=None, reachable=True):
    return {
        "reachable":  reachable,
        "positions":  positions or {},
        "orders":     orders or {},
    }


# ── Scenario 1: Clean state (both flat) ───────────────────────────────────────

def test_s1_clean_state():
    r = reconcile_state(_local(), _broker())
    _check("S1: clean state is ok", r["ok"] is True, str(r))
    _check("S1: clean state no halt", r["halt_new_entries"] is False)
    _check("S1: clean state no human review", r["requires_human_review"] is False)
    _check("S1: clean state severity INFO", r["severity"] == INFO)


# ── Scenario 2: Ghost position at broker (local flat, broker has position) ────

def test_s2_ghost_position():
    local  = _local(positions={})
    broker = _broker(positions={"MESM5": 1})
    r = reconcile_state(local, broker)
    _check("S2: ghost position not ok", r["ok"] is False)
    _check("S2: ghost position is CRITICAL", r["severity"] == CRITICAL)
    _check("S2: ghost position halts entries", r["halt_new_entries"] is True)
    _check("S2: ghost position requires human", r["requires_human_review"] is True)
    _check("S2: GHOST_POSITION in reason", "GHOST_POSITION" in r["reason"], r["reason"])


# ── Scenario 3: Position lost at broker (local has position, broker flat) ─────

def test_s3_position_lost():
    local  = _local(positions={"MESM5": {"net_pos": 1, "entry_px": 5320.0}})
    broker = _broker(positions={})
    r = reconcile_state(local, broker)
    _check("S3: position lost not ok", r["ok"] is False)
    _check("S3: position lost is WARNING", r["severity"] == WARNING)
    _check("S3: POSITION_LOST in reason", "POSITION_LOST" in r["reason"], r["reason"])


# ── Scenario 4: Missing stop order at broker ──────────────────────────────────

def test_s4_missing_stop():
    local = _local(
        positions={"MESM5": {"net_pos": 1}},
        brackets={"2": {
            "symbol": "MESM5",
            "entry_order_id": "1001",
            "stop_order_id": "1002",
            "target_order_id": "1003",
            "entry_filled": True,
        }}
    )
    # Only target order exists at broker, stop is missing
    broker = _broker(
        positions={"MESM5": 1},
        orders={"1003": {"symbol": "MESM5", "action": "Sell", "ordStatus": "ContingencyOrder"}}
    )
    r = reconcile_state(local, broker)
    _check("S4: missing stop not ok", r["ok"] is False)
    _check("S4: missing stop is CRITICAL", r["severity"] == CRITICAL)
    _check("S4: MISSING_STOP in reason", "MISSING_STOP" in r["reason"], r["reason"])


# ── Scenario 5: Missing target order at broker ────────────────────────────────

def test_s5_missing_target():
    local = _local(
        positions={"MESM5": {"net_pos": 1}},
        brackets={"2": {
            "symbol": "MESM5",
            "entry_order_id": "1001",
            "stop_order_id": "1002",
            "target_order_id": "1003",
            "entry_filled": True,
        }}
    )
    # Only stop exists at broker, target is missing
    broker = _broker(
        positions={"MESM5": 1},
        orders={"1002": {"symbol": "MESM5", "action": "Sell", "ordStatus": "Working"}}
    )
    r = reconcile_state(local, broker)
    _check("S5: missing target not ok", r["ok"] is False)
    _check("S5: missing target is CRITICAL", r["severity"] == CRITICAL)
    _check("S5: MISSING_TARGET in reason", "MISSING_TARGET" in r["reason"], r["reason"])


# ── Scenario 6: Duplicate broker orders ───────────────────────────────────────

def test_s6_duplicate_orders():
    local = _local()
    broker = _broker(orders={
        "2001": {"symbol": "MESM5", "action": "Sell", "orderType": "Limit", "ordStatus": "Working"},
        "2002": {"symbol": "MESM5", "action": "Sell", "orderType": "Limit", "ordStatus": "Working"},
    })
    r = reconcile_state(local, broker)
    _check("S6: duplicate orders not ok", r["ok"] is False)
    _check("S6: duplicate orders is CRITICAL", r["severity"] == CRITICAL)
    _check("S6: DUPLICATE_ORDERS in reason", "DUPLICATE_ORDERS" in r["reason"], r["reason"])


# ── Scenario 7: Unknown broker order ─────────────────────────────────────────

def test_s7_unknown_order():
    local = _local(brackets={})   # no brackets tracked locally
    broker = _broker(orders={
        "9999": {"symbol": "MESM5", "action": "Buy", "ordStatus": "Working"},
    })
    r = reconcile_state(local, broker)
    _check("S7: unknown order not ok", r["ok"] is False)
    _check("S7: unknown order is WARNING", r["severity"] == WARNING)
    _check("S7: UNKNOWN_BROKER_ORDER in reason",
           "UNKNOWN_BROKER_ORDER" in r["reason"], r["reason"])


# ── Scenario 8: Broker unreachable ───────────────────────────────────────────

def test_s8_broker_unreachable():
    r = reconcile_state(_local(), _broker(reachable=False))
    _check("S8: unreachable not ok", r["ok"] is False)
    _check("S8: unreachable is CRITICAL", r["severity"] == CRITICAL)
    _check("S8: unreachable halts entries", r["halt_new_entries"] is True)
    _check("S8: BROKER_UNREACHABLE in reason",
           "BROKER_UNREACHABLE" in r["reason"], r["reason"])


# ── Scenario 9: Quantity mismatch ────────────────────────────────────────────

def test_s9_quantity_mismatch():
    local  = _local(positions={"MESM5": {"net_pos": 2}})  # local says 2
    broker = _broker(positions={"MESM5": 1})               # broker says 1
    r = reconcile_state(local, broker)
    _check("S9: quantity mismatch not ok", r["ok"] is False)
    _check("S9: quantity mismatch is CRITICAL", r["severity"] == CRITICAL)
    _check("S9: QUANTITY_MISMATCH in reason",
           "QUANTITY_MISMATCH" in r["reason"], r["reason"])


# ── Scenario 10: Stale local state ───────────────────────────────────────────

def test_s10_stale_local_state():
    local = _local(ts="2020-01-01T00:00:00+00:00")   # Very old timestamp
    broker = _broker()
    r = reconcile_state(local, broker)
    _check("S10: stale state not ok", r["ok"] is False)
    _check("S10: stale state is WARNING", r["severity"] == WARNING)
    _check("S10: STALE_LOCAL_STATE in reason",
           "STALE_LOCAL_STATE" in r["reason"], r["reason"])


# ── Scenario: Entry not yet filled — bracket legs not expected ────────────────

def test_entry_not_filled_no_false_positive():
    local = _local(
        positions={},
        brackets={"2": {
            "symbol": "MESM5",
            "entry_order_id": "3001",
            "stop_order_id": "3002",
            "target_order_id": "3003",
            "entry_filled": False,  # Entry not filled — don't expect stop/target at broker
        }}
    )
    broker = _broker(positions={}, orders={})
    r = reconcile_state(local, broker)
    _check("Entry not filled: no false MISSING_STOP", "MISSING_STOP" not in r.get("reason", ""))
    _check("Entry not filled: no false MISSING_TARGET", "MISSING_TARGET" not in r.get("reason", ""))


# ── Scenario: Both bracket legs present ──────────────────────────────────────

def test_full_bracket_present():
    local = _local(
        positions={"MESM5": {"net_pos": 1}},
        brackets={"2": {
            "symbol": "MESM5",
            "entry_order_id": "4001",
            "stop_order_id": "4002",
            "target_order_id": "4003",
            "entry_filled": True,
        }}
    )
    broker = _broker(
        positions={"MESM5": 1},
        orders={
            "4002": {"symbol": "MESM5", "action": "Sell", "orderType": "Stop",  "ordStatus": "Working"},
            "4003": {"symbol": "MESM5", "action": "Sell", "orderType": "Limit", "ordStatus": "ContingencyOrder"},
        }
    )
    r = reconcile_state(local, broker)
    _check("Full bracket present: ok=True", r["ok"] is True, r.get("reason", ""))


# ── Standalone reconcile_positions tests ─────────────────────────────────────

def test_reconcile_positions_direct():
    findings = reconcile_positions({"MESM5": 1}, {"MESM5": 1})
    _check("Matching positions: no findings", len(findings) == 0)

    findings2 = reconcile_positions({}, {"MESM5": 1})
    _check("Ghost position detected", any("GHOST_POSITION" in f["reason"] for f in findings2))

    findings3 = reconcile_positions({"MESM5": 1}, {})
    _check("Lost position detected", any("POSITION_LOST" in f["reason"] for f in findings3))


# ── Run all tests ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*62}")
    print(f"  BROKER RECONCILIATION TEST SUITE")
    print(f"  Pure mocked data — No API calls")
    print(f"{'='*62}")

    tests = [
        test_s1_clean_state,
        test_s2_ghost_position,
        test_s3_position_lost,
        test_s4_missing_stop,
        test_s5_missing_target,
        test_s6_duplicate_orders,
        test_s7_unknown_order,
        test_s8_broker_unreachable,
        test_s9_quantity_mismatch,
        test_s10_stale_local_state,
        test_entry_not_filled_no_false_positive,
        test_full_bracket_present,
        test_reconcile_positions_direct,
    ]

    for test_fn in tests:
        print(f"\n  [{test_fn.__name__}]")
        try:
            test_fn()
        except Exception as e:
            global FAIL_COUNT
            FAIL_COUNT += 1
            print(f"  FAIL  {test_fn.__name__} raised exception: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*62}")
    print(f"  Results: {PASS_COUNT} PASS  {FAIL_COUNT} FAIL")
    if FAIL_COUNT == 0:
        print(f"  All reconciliation tests passed.")
    else:
        print(f"  {FAIL_COUNT} test(s) failed — review before proceeding.")
    print(f"{'='*62}\n")
    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
