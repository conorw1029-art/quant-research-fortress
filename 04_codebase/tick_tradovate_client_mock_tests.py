"""
tick_tradovate_client_mock_tests.py — Bracket Order Safety Mock Tests
======================================================================
Tests all 19 safety requirements for place_bracket_order().

NO real Tradovate connection.
NO credentials.
NO API calls.
NO live orders.
NO demo orders.

All tests use dry_run=True or test the validation gates that block
non-dry-run execution.

Run:
  venv_new\Scripts\python.exe -X utf8 tick_tradovate_client_mock_tests.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tick_tradovate_client as _mod
from tick_tradovate_client import TradovateClient, _read_kill_switch

PASS_COUNT = 0
FAIL_COUNT = 0

# Valid baseline order params — all tests start from here and mutate one field
_VALID = dict(
    symbol       = "MESM5",
    side         = "BUY",
    quantity     = 1,
    entry_type   = "Limit",
    entry_price  = 5320.25,
    stop_price   = 5308.00,
    target_price = 5340.00,
    dry_run      = True,
    demo         = True,
)


def _client() -> TradovateClient:
    return TradovateClient.create_dry_run()


def _place(**overrides) -> dict:
    kwargs = dict(_VALID)
    kwargs.update(overrides)
    return _client().place_bracket_order(**kwargs)


def _check(name: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}  {detail}")


# ── Test 1: dry_run=True returns ok=True, mode=DRY_RUN, no API call ──────────

def test_01_dry_run_returns_ok():
    r = _place(dry_run=True)
    _check("T01: dry_run=True returns ok=True", r["ok"] is True, str(r))
    _check("T01: dry_run mode is DRY_RUN", r["mode"] == "DRY_RUN", r["mode"])
    _check("T01: dry_run has payload", bool(r.get("payload")), str(r))
    _check("T01: dry_run has no entry_order_id", r["entry_order_id"] is None)
    _check("T01: dry_run has no stop_order_id", r["stop_order_id"] is None)
    _check("T01: dry_run has no target_order_id", r["target_order_id"] is None)


# ── Test 2: invalid side rejected ─────────────────────────────────────────────

def test_02_invalid_side_rejected():
    r = _place(side="LONG")
    _check("T02: invalid side rejected", r["ok"] is False, r.get("reason"))
    _check("T02: reason mentions INVALID_SIDE", "INVALID_SIDE" in r.get("reason", ""), r.get("reason"))


# ── Test 3: invalid symbol rejected ───────────────────────────────────────────

def test_03_invalid_symbol_rejected():
    r = _place(symbol="")
    _check("T03: empty symbol rejected", r["ok"] is False, r.get("reason"))


# ── Test 4: quantity <= 0 rejected ────────────────────────────────────────────

def test_04_quantity_zero_rejected():
    r = _place(quantity=0)
    _check("T04: quantity=0 rejected", r["ok"] is False, r.get("reason"))
    _check("T04: reason mentions INVALID_QUANTITY", "INVALID_QUANTITY" in r.get("reason", ""), r.get("reason"))


def test_04b_quantity_negative_rejected():
    r = _place(quantity=-1)
    _check("T04b: quantity=-1 rejected", r["ok"] is False)


# ── Test 5: quantity above max rejected ───────────────────────────────────────

def test_05_quantity_above_max_rejected():
    r = _place(quantity=_mod.MAX_BRACKET_CONTRACTS + 1)
    _check("T05: quantity above max rejected", r["ok"] is False, r.get("reason"))
    _check("T05: reason mentions QUANTITY_EXCEEDS_MAX",
           "QUANTITY_EXCEEDS_MAX" in r.get("reason", ""), r.get("reason"))


# ── Test 6: stop on wrong side for BUY ────────────────────────────────────────

def test_06_stop_above_entry_for_buy():
    r = _place(side="BUY", entry_price=5320.25, stop_price=5330.00, target_price=5340.00)
    _check("T06: stop above entry for BUY rejected", r["ok"] is False, r.get("reason"))
    _check("T06: reason mentions STOP_ABOVE_ENTRY_FOR_BUY",
           "STOP_ABOVE_ENTRY_FOR_BUY" in r.get("reason", ""), r.get("reason"))


# ── Test 7: stop on wrong side for SELL ───────────────────────────────────────

def test_07_stop_below_entry_for_sell():
    r = _place(side="SELL", entry_price=5320.25, stop_price=5310.00, target_price=5300.00)
    _check("T07: stop below entry for SELL rejected", r["ok"] is False, r.get("reason"))
    _check("T07: reason mentions STOP_BELOW_ENTRY_FOR_SELL",
           "STOP_BELOW_ENTRY_FOR_SELL" in r.get("reason", ""), r.get("reason"))


# ── Test 8: target on wrong side for BUY ──────────────────────────────────────

def test_08_target_below_entry_for_buy():
    r = _place(side="BUY", entry_price=5320.25, stop_price=5308.00, target_price=5310.00)
    _check("T08: target below entry for BUY rejected", r["ok"] is False, r.get("reason"))
    _check("T08: reason mentions TARGET_BELOW_ENTRY_FOR_BUY",
           "TARGET_BELOW_ENTRY_FOR_BUY" in r.get("reason", ""), r.get("reason"))


# ── Test 9: target on wrong side for SELL ─────────────────────────────────────

def test_09_target_above_entry_for_sell():
    r = _place(side="SELL", entry_price=5320.25, stop_price=5332.00, target_price=5330.00)
    _check("T09: target above entry for SELL rejected", r["ok"] is False, r.get("reason"))
    _check("T09: reason mentions TARGET_ABOVE_ENTRY_FOR_SELL",
           "TARGET_ABOVE_ENTRY_FOR_SELL" in r.get("reason", ""), r.get("reason"))


# ── Test 10: zero stop distance rejected ──────────────────────────────────────

def test_10_zero_stop_distance_rejected():
    # stop == entry: directional check fires first (STOP_ABOVE_ENTRY_FOR_BUY)
    # before zero-distance check; both are correct rejections
    r = _place(entry_price=5320.25, stop_price=5320.25, target_price=5340.00)
    _check("T10: zero stop distance rejected", r["ok"] is False, r.get("reason"))
    _check("T10: reason is directional or zero-distance",
           any(k in r.get("reason", "") for k in
               ("ZERO_STOP_DISTANCE", "STOP_ABOVE_ENTRY_FOR_BUY")), r.get("reason"))


# ── Test 11: zero target distance rejected ────────────────────────────────────

def test_11_zero_target_distance_rejected():
    # target == entry: directional check fires first (TARGET_BELOW_ENTRY_FOR_BUY)
    r = _place(entry_price=5320.25, stop_price=5308.00, target_price=5320.25)
    _check("T11: zero target distance rejected", r["ok"] is False, r.get("reason"))
    _check("T11: reason is directional or zero-distance",
           any(k in r.get("reason", "") for k in
               ("ZERO_TARGET_DISTANCE", "TARGET_BELOW_ENTRY_FOR_BUY")), r.get("reason"))


# ── Test 12: tick rounding validation ─────────────────────────────────────────

def test_12_off_tick_entry_rejected():
    # MES tick = 0.25; 5320.30 is not on tick
    r = _place(entry_price=5320.30, stop_price=5308.00, target_price=5340.00)
    _check("T12: off-tick entry price rejected", r["ok"] is False, r.get("reason"))
    _check("T12: reason mentions OFF_TICK_PRICE",
           "OFF_TICK_PRICE" in r.get("reason", ""), r.get("reason"))


def test_12b_off_tick_stop_rejected():
    r = _place(entry_price=5320.25, stop_price=5308.10, target_price=5340.00)
    _check("T12b: off-tick stop price rejected", r["ok"] is False, r.get("reason"))


def test_12c_on_tick_accepted():
    r = _place(entry_price=5320.25, stop_price=5308.00, target_price=5340.00)
    _check("T12c: on-tick prices accepted", r["ok"] is True, str(r))


# ── Test 13: estimated dollar risk > $200 rejected ───────────────────────────

def test_13_dollar_risk_exceeded():
    # MES point value = $5, stop distance 50 pts = $250 risk
    r = _place(
        symbol      = "MESM5",
        side        = "BUY",
        entry_price = 5320.25,
        stop_price  = 5270.25,   # 50 pts below = $250 risk (>$200)
        target_price = 5370.25,
    )
    _check("T13: dollar risk > $200 rejected", r["ok"] is False, r.get("reason"))
    _check("T13: reason mentions ESTIMATED_RISK_EXCEEDS_LIMIT",
           "ESTIMATED_RISK_EXCEEDS_LIMIT" in r.get("reason", ""), r.get("reason"))


def test_13b_dollar_risk_ok():
    # MES point value = $5, stop distance 20 pts = $100 risk (< $200)
    r = _place(
        symbol      = "MESM5",
        side        = "BUY",
        entry_price = 5320.25,
        stop_price  = 5300.25,   # 20 pts = $100 risk
        target_price = 5360.25,
    )
    _check("T13b: acceptable risk accepted", r["ok"] is True, str(r))


# ── Test 14: kill switch STOP rejects order ───────────────────────────────────

def test_14_kill_switch_stop():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("STOP")
        ks_path = f.name
    try:
        old_path = _mod._KILL_SWITCH_PATH
        _mod._KILL_SWITCH_PATH = Path(ks_path)
        r = _place(dry_run=True)
        _check("T14: kill switch STOP rejects order", r["ok"] is False, r.get("reason"))
        _check("T14: reason mentions KILL_SWITCH_STOP",
               "KILL_SWITCH_STOP" in r.get("reason", ""), r.get("reason"))
    finally:
        _mod._KILL_SWITCH_PATH = old_path
        os.unlink(ks_path)


def test_14b_kill_switch_run_allows():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("RUN")
        ks_path = f.name
    try:
        old_path = _mod._KILL_SWITCH_PATH
        _mod._KILL_SWITCH_PATH = Path(ks_path)
        r = _place(dry_run=True)
        _check("T14b: kill switch RUN allows order", r["ok"] is True, str(r))
    finally:
        _mod._KILL_SWITCH_PATH = old_path
        os.unlink(ks_path)


# ── Test 15: session closed rejects order ─────────────────────────────────────

def test_15_session_closed_rejected():
    r = _place(session_open=False)
    _check("T15: session_open=False rejects order", r["ok"] is False, r.get("reason"))
    _check("T15: reason mentions SESSION_CLOSED",
           "SESSION_CLOSED" in r.get("reason", ""), r.get("reason"))


def test_15b_session_open_allows():
    r = _place(session_open=True)
    _check("T15b: session_open=True allows order", r["ok"] is True, str(r))


# ── Test 16: BRACKET_OSO_UNVERIFIED blocks non-dry-run ────────────────────────

def test_16_demo_blocked_oso_unverified():
    # non-dry-run demo order must fail with BRACKET_OSO_UNVERIFIED
    # (OSO exchange-verified flag is False by default)
    assert _mod._OSO_EXCHANGE_VERIFIED is False, "Test assumes OSO is NOT verified"

    # We can't actually call authenticate on a real client, so we test via
    # a client where account_id is manually set to simulate authenticated state.
    client = TradovateClient.create_dry_run()
    client.account_id = 99999   # fake auth
    client.access_token = "fake-token"
    r = client.place_bracket_order(
        symbol="MESM5", side="BUY", quantity=1,
        entry_type="Limit", entry_price=5320.25,
        stop_price=5308.00, target_price=5340.00,
        demo=True, dry_run=False,
    )
    _check("T16: non-dry-run blocked until OSO verified", r["ok"] is False, r.get("reason"))
    _check("T16: reason mentions BRACKET_OSO_UNVERIFIED",
           "BRACKET_OSO_UNVERIFIED" in r.get("reason", ""), r.get("reason"))


# ── Test 17: duplicate client_order_id rejected ───────────────────────────────

def test_17_duplicate_client_order_id():
    _mod._issued_client_order_ids.clear()
    coid = "fortress-test-dedup-001"
    r1 = _place(client_order_id=coid)
    _check("T17: first use of client_order_id accepted", r1["ok"] is True, str(r1))
    r2 = _place(client_order_id=coid)
    _check("T17: second use of same client_order_id rejected", r2["ok"] is False, r2.get("reason"))
    _check("T17: reason mentions DUPLICATE_CLIENT_ORDER_ID",
           "DUPLICATE_CLIENT_ORDER_ID" in r2.get("reason", ""), r2.get("reason"))
    _mod._issued_client_order_ids.clear()


# ── Test 18: result struct always contains all required fields ────────────────

def test_18_result_struct_complete():
    _mod._issued_client_order_ids.clear()
    r = _place(dry_run=True)
    required_fields = [
        "ok", "mode", "entry_order_id", "stop_order_id", "target_order_id",
        "oco_id", "oso_id", "client_order_id", "reason", "payload",
    ]
    for field in required_fields:
        _check(f"T18: result has field '{field}'", field in r, str(list(r.keys())))
    _mod._issued_client_order_ids.clear()


def test_18b_fail_result_struct_complete():
    r = _place(side="INVALID")
    required_fields = [
        "ok", "mode", "entry_order_id", "stop_order_id", "target_order_id",
        "oco_id", "oso_id", "client_order_id", "reason", "payload",
    ]
    for field in required_fields:
        _check(f"T18b: fail result has field '{field}'", field in r, str(list(r.keys())))


# ── Test 19: demo order refuses if OSO/OCO support is unverified ──────────────

def test_19_demo_refuses_unverified_oso():
    # This is the same as T16 but explicitly named after requirement 19
    assert _mod._OSO_EXCHANGE_VERIFIED is False
    client = TradovateClient.create_dry_run()
    client.account_id = 99999
    client.access_token = "fake-token"
    r = client.place_bracket_order(
        symbol="MESM5", side="BUY", quantity=1,
        entry_type="Limit", entry_price=5320.25,
        stop_price=5308.00, target_price=5340.00,
        demo=True, dry_run=False,
    )
    _check("T19: demo mode blocked when OSO unverified", r["ok"] is False)
    _check("T19: mode is DRY_RUN or DEMO (not LIVE)",
           r["mode"] in ("DRY_RUN", "DEMO"), r["mode"])


# ── Run all tests ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*62}")
    print(f"  BRACKET ORDER MOCK TEST SUITE")
    print(f"  No API calls — No credentials — No orders")
    print(f"{'='*62}")

    tests = [
        test_01_dry_run_returns_ok,
        test_02_invalid_side_rejected,
        test_03_invalid_symbol_rejected,
        test_04_quantity_zero_rejected,
        test_04b_quantity_negative_rejected,
        test_05_quantity_above_max_rejected,
        test_06_stop_above_entry_for_buy,
        test_07_stop_below_entry_for_sell,
        test_08_target_below_entry_for_buy,
        test_09_target_above_entry_for_sell,
        test_10_zero_stop_distance_rejected,
        test_11_zero_target_distance_rejected,
        test_12_off_tick_entry_rejected,
        test_12b_off_tick_stop_rejected,
        test_12c_on_tick_accepted,
        test_13_dollar_risk_exceeded,
        test_13b_dollar_risk_ok,
        test_14_kill_switch_stop,
        test_14b_kill_switch_run_allows,
        test_15_session_closed_rejected,
        test_15b_session_open_allows,
        test_16_demo_blocked_oso_unverified,
        test_17_duplicate_client_order_id,
        test_18_result_struct_complete,
        test_18b_fail_result_struct_complete,
        test_19_demo_refuses_unverified_oso,
    ]

    for test_fn in tests:
        section = test_fn.__name__
        print(f"\n  [{section}]")
        _mod._issued_client_order_ids.clear()
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
        print(f"  All bracket mock tests passed.")
    else:
        print(f"  {FAIL_COUNT} test(s) failed — review before proceeding.")
    print(f"{'='*62}\n")
    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
