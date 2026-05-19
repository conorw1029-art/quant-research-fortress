"""
test_broker_position_sync.py — Broker position sync + coordinator integration tests.
No API calls. No credentials. No orders.

Run:
  C:\\Users\\conor\\Desktop\\quant-research\\venv_new\\Scripts\\python.exe -X utf8 test_broker_position_sync.py
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from tick_broker_position_sync import (
    bracket_ids_from_orders,
    build_broker_net_positions,
    micro_to_base,
    strip_month_code,
    tv_contract_to_coordinator_symbol,
)
from tick_portfolio_coordinator import (
    BrokerNetPosition,
    CoordinatorAction,
    CoordinatorConfig,
    PortfolioCoordinator,
    Side,
    SignalIntent,
    VirtualStrategyPosition,
)
from tick_tradovate_client import Position, TradovateClient

# ── Hand-rolled test runner ───────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []


def _test(name: str, fn) -> None:
    try:
        fn()
        _results.append((name, True, ""))
        print(f"  PASS  {name}")
    except AssertionError as e:
        _results.append((name, False, str(e)))
        print(f"  FAIL  {name}")
        print(f"        {e}")
    except Exception as e:
        _results.append((name, False, f"EXCEPTION: {e}"))
        print(f"  ERROR {name}")
        traceback.print_exc()


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _pos(symbol: str, net_pos: int, avg_price: float = 2000.0,
         account_id: int = 1) -> Position:
    return Position(
        account_id=account_id, contract_id=99,
        symbol=symbol, net_pos=net_pos,
        avg_price=avg_price, open_pnl=0.0, closed_pnl=0.0,
    )


def _order(order_id: int, symbol: str, status: str) -> dict:
    return {"id": order_id, "symbol": symbol, "ordStatus": status}


def _intent(strategy_id: int = 1, symbol: str = "GC",
            side: Side = Side.LONG) -> SignalIntent:
    return SignalIntent(
        strategy_id=strategy_id,
        strategy_key=f"{symbol}/test/1m",
        symbol=symbol, contract=symbol + "M5",
        side=side, desired_qty=1,
        entry_price=2000.0, stop_price=1990.0, target_price=2020.0,
        estimated_risk_usd=100.0,
        timestamp=datetime.now(timezone.utc),
    )


def _virtual(strategy_id: int = 1, symbol: str = "GC",
             side: Side = Side.LONG) -> VirtualStrategyPosition:
    return VirtualStrategyPosition(
        strategy_id=strategy_id, strategy_key=f"{symbol}/test/1m",
        symbol=symbol, side=side, qty=1,
        entry_price=2000.0, stop_price=1990.0, target_price=2020.0,
        state="OPEN",
    )


def _coord(one_strategy_only_demo: bool = False,
           max_total_open_symbols: int = 10) -> PortfolioCoordinator:
    return PortfolioCoordinator(CoordinatorConfig(
        one_strategy_only_demo=one_strategy_only_demo,
        max_net_contracts_per_symbol=1,
        max_total_open_symbols=max_total_open_symbols,
        allow_reversal=False,
        allow_position_increase_same_symbol=False,
        dry_run_only=True,
    ))


# ── T01–T04: strip_month_code ────────────────────────────────────────────────

def test_01_strip_mes():
    assert strip_month_code("MESM5") == "MES", "MESM5 → MES"
    assert strip_month_code("mesm5") == "MES", "lower-case input"


def test_02_strip_mgc():
    assert strip_month_code("MGCM5") == "MGC"
    assert strip_month_code("MGCU5") == "MGC"
    assert strip_month_code("MGCZ4") == "MGC"


def test_03_strip_full_size():
    assert strip_month_code("ESM5") == "ES", "full-size ES"
    assert strip_month_code("GCJ6") == "GC", "full-size GC"
    assert strip_month_code("MNQZ4") == "MNQ"


def test_04_strip_two_digit_year():
    """Two-digit year suffix (e.g. M25) should also strip correctly."""
    # "MESM25": walk back from '5' (digit) → '2' (digit) → 'M' (month code) → strip
    assert strip_month_code("MESM25") == "MES", f"got {strip_month_code('MESM25')!r}"
    assert strip_month_code("MGCU25") == "MGC", f"got {strip_month_code('MGCU25')!r}"


# ── T05–T07: tv_contract_to_coordinator_symbol ───────────────────────────────

def test_05_coord_sym_mes():
    assert tv_contract_to_coordinator_symbol("MESM5") == "ES"
    assert tv_contract_to_coordinator_symbol("MESZ4") == "ES"


def test_06_coord_sym_mgc():
    assert tv_contract_to_coordinator_symbol("MGCM5") == "GC"
    assert tv_contract_to_coordinator_symbol("MGCU5") == "GC"


def test_07_coord_sym_mnq():
    assert tv_contract_to_coordinator_symbol("MNQM5") == "NQ"
    assert tv_contract_to_coordinator_symbol("SILM5") == "SI"


# ── T08–T10: bracket_ids_from_orders ─────────────────────────────────────────

def test_08_bracket_ids_empty():
    assert bracket_ids_from_orders([]) == {}
    assert bracket_ids_from_orders(None) == {}


def test_09_bracket_ids_active_statuses():
    orders = [
        _order(101, "MGCM5", "ContingencyOrder"),   # bracket stop
        _order(102, "MGCM5", "Working"),              # bracket target
        _order(103, "MESM5", "ContingencyOrder"),    # bracket stop ES
    ]
    result = bracket_ids_from_orders(orders)
    assert set(result.get("MGC", [])) == {101, 102}, f"got {result}"
    assert result.get("MES") == [103], f"got {result}"


def test_10_bracket_ids_excludes_filled():
    orders = [
        _order(101, "MGCM5", "Filled"),     # entry order — already filled
        _order(102, "MGCM5", "Cancelled"),  # cancelled — exclude
        _order(103, "MGCM5", "Rejected"),   # rejected — exclude
        _order(104, "MGCM5", "Working"),    # active bracket leg — include
    ]
    result = bracket_ids_from_orders(orders)
    assert result == {"MGC": [104]}, f"got {result}"


# ── T11–T15: build_broker_net_positions ──────────────────────────────────────

def test_11_build_empty_positions():
    result = build_broker_net_positions([], {})
    assert result == [], "empty positions → empty list"


def test_12_build_flat_positions_skipped():
    positions = [_pos("MGCM5", 0), _pos("MESM5", 0)]
    result = build_broker_net_positions(positions, {})
    assert result == [], "flat positions (net_pos=0) must be excluded"


def test_13_build_long_with_bracket():
    positions = [_pos("MGCM5", 1, avg_price=2100.0, account_id=42)]
    bracket_ids = {"MGC": [201, 202]}
    result = build_broker_net_positions(positions, bracket_ids)
    assert len(result) == 1
    bp = result[0]
    assert bp.symbol == "GC", f"expected GC, got {bp.symbol}"
    assert bp.contract == "MGCM5"
    assert bp.net_qty == 1
    assert bp.avg_price == 2100.0
    assert bp.state == "LONG"
    assert set(bp.active_bracket_ids) == {"201", "202"}, f"got {bp.active_bracket_ids}"
    assert bp.account_id == "42"


def test_14_build_short_with_bracket():
    positions = [_pos("MESM5", -1, avg_price=5200.0)]
    bracket_ids = {"MES": [301, 302]}
    result = build_broker_net_positions(positions, bracket_ids)
    assert len(result) == 1
    bp = result[0]
    assert bp.symbol == "ES"
    assert bp.net_qty == -1
    assert bp.state == "SHORT"
    assert set(bp.active_bracket_ids) == {"301", "302"}


def test_15_build_no_bracket_ids_empty():
    """Broker position with no matching bracket IDs → active_bracket_ids=[]."""
    positions = [_pos("MGCM5", 1)]
    result = build_broker_net_positions(positions, {})
    assert len(result) == 1
    assert result[0].active_bracket_ids == [], \
        f"expected [], got {result[0].active_bracket_ids}"


def test_16_build_sm_bracket_fallback():
    """StateManager brackets used as fallback when API returns no bracket IDs."""
    positions = [_pos("MGCM5", 1)]
    bracket_ids_api = {}  # API returned nothing (bracket not yet activated)
    sm_brackets = {
        "16": {
            "symbol":          "MGCM5",
            "stop_order_id":   401,
            "target_order_id": 402,
        }
    }
    result = build_broker_net_positions(positions, bracket_ids_api, sm_brackets)
    assert len(result) == 1
    bp = result[0]
    assert set(bp.active_bracket_ids) == {"401", "402"}, \
        f"SM fallback failed: {bp.active_bracket_ids}"


def test_17_build_api_bracket_takes_priority_over_sm():
    """Live API bracket IDs take priority over SM fallback."""
    positions = [_pos("MGCM5", 1)]
    bracket_ids_api = {"MGC": [501, 502]}   # fresh API data
    sm_brackets = {
        "16": {
            "symbol":          "MGCM5",
            "stop_order_id":   999,          # stale SM data — should not be used
            "target_order_id": 998,
        }
    }
    result = build_broker_net_positions(positions, bracket_ids_api, sm_brackets)
    assert len(result) == 1
    bp = result[0]
    assert set(bp.active_bracket_ids) == {"501", "502"}, \
        f"API should take priority: {bp.active_bracket_ids}"


def test_18_build_multi_symbol():
    """Two open positions with brackets → two BrokerNetPosition objects."""
    positions = [
        _pos("MGCM5",  1, avg_price=2100.0),
        _pos("MESM5", -1, avg_price=5200.0),
    ]
    bracket_ids = {"MGC": [601, 602], "MES": [603, 604]}
    result = build_broker_net_positions(positions, bracket_ids)
    assert len(result) == 2
    syms = {bp.symbol for bp in result}
    assert syms == {"GC", "ES"}, f"got {syms}"


# ── T19–T22: full coordinator chain with build_broker_net_positions ───────────

def test_19_coordinator_reverse_blocked_via_real_broker_pos():
    """
    Broker holds GC long (from live position + bracket).
    New short signal → REVERSE_POSITION_BLOCKED.
    """
    positions    = [_pos("MGCM5", 1)]
    bracket_ids  = {"MGC": [701, 702]}
    broker_pos   = build_broker_net_positions(positions, bracket_ids)
    virtual      = [_virtual(strategy_id=16, symbol="GC", side=Side.LONG)]

    coord = _coord()
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=17, symbol="GC", side=Side.SHORT),
        virtual_positions=virtual,
        broker_positions=broker_pos,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "reverse should be blocked"
    assert dec.action == CoordinatorAction.REVERSE_POSITION_BLOCKED, dec.action


def test_20_coordinator_human_review_missing_bracket():
    """
    Broker holds GC long but bracket_ids is empty → HUMAN_REVIEW_REQUIRED.
    Rule 4: broker has position with no active_bracket_ids.
    """
    positions   = [_pos("MGCM5", 1)]
    broker_pos  = build_broker_net_positions(positions, {})  # no brackets
    virtual     = [_virtual(strategy_id=16, symbol="GC", side=Side.LONG)]

    coord = _coord()
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG),
        virtual_positions=virtual,
        broker_positions=broker_pos,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok
    assert dec.action == CoordinatorAction.HUMAN_REVIEW_REQUIRED, dec.action
    assert dec.requires_human_review


def test_21_coordinator_human_review_no_virtual():
    """
    Broker has position + bracket but no virtual tracking → HUMAN_REVIEW_REQUIRED.
    Rule 6: broker has position, no virtual tracks it.
    """
    positions   = [_pos("MGCM5", 1)]
    bracket_ids = {"MGC": [801, 802]}
    broker_pos  = build_broker_net_positions(positions, bracket_ids)
    virtual     = []  # no tracking

    coord = _coord()
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG),
        virtual_positions=virtual,
        broker_positions=broker_pos,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok
    assert dec.action == CoordinatorAction.HUMAN_REVIEW_REQUIRED, dec.action


def test_22_coordinator_accept_new_clean_state():
    """
    Broker flat for GC, no virtual position, clean signal → ACCEPT_NEW.
    """
    positions   = [_pos("MESM5", 0)]  # flat ES position — not GC
    bracket_ids = {}
    broker_pos  = build_broker_net_positions(positions, bracket_ids)
    # broker_pos is [] because all positions are flat

    coord = _coord()
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=16, symbol="GC", side=Side.LONG),
        virtual_positions=[],
        broker_positions=broker_pos,
        open_orders=[],
        kill_switch=False,
    )
    assert dec.ok, f"expected ACCEPT_NEW, got {dec.action}: {dec.reason}"
    assert dec.action == CoordinatorAction.ACCEPT_NEW


# ── T23: confirm_bracket_alive (unit — no real API) ──────────────────────────

class _MockClientForConfirm(TradovateClient):
    """Subclass that overrides get_order_status to avoid real API calls."""

    def __init__(self, stop_statuses: list[str], target_statuses: list[str]):
        super().__init__(username="", password="", cid=0, secret="", demo=True)
        self._stop_statuses   = stop_statuses
        self._target_statuses = target_statuses
        self._stop_call   = 0
        self._target_call = 0

    def get_order_status(self, order_id: int) -> dict:
        if order_id == 101:
            idx = min(self._stop_call, len(self._stop_statuses) - 1)
            self._stop_call += 1
            return {"ordStatus": self._stop_statuses[idx]}
        else:
            idx = min(self._target_call, len(self._target_statuses) - 1)
            self._target_call += 1
            return {"ordStatus": self._target_statuses[idx]}


def test_23_confirm_bracket_alive_success():
    """confirm_bracket_alive returns alive=True when both legs are Working."""
    client = _MockClientForConfirm(
        stop_statuses=["ContingencyOrder"],
        target_statuses=["ContingencyOrder"],
    )
    result = client.confirm_bracket_alive(
        stop_order_id=101, target_order_id=102,
        max_wait_seconds=5.0, poll_interval=0.0,
    )
    assert result.get("alive") is True, f"expected alive, got {result}"
    assert result["stop_status"] == "ContingencyOrder"
    assert result["target_status"] == "ContingencyOrder"


def test_24_confirm_bracket_alive_timeout():
    """confirm_bracket_alive times out when legs never become active."""
    client = _MockClientForConfirm(
        stop_statuses=["PendingNew"],  # never becomes Working
        target_statuses=["PendingNew"],
    )
    # max_wait_seconds=0 → loop never runs → immediate timeout
    result = client.confirm_bracket_alive(
        stop_order_id=101, target_order_id=102,
        max_wait_seconds=0.0, poll_interval=0.0,
    )
    assert result.get("alive") is False, f"expected timeout, got {result}"
    assert result.get("reason") == "timeout"


def test_25_confirm_bracket_terminal_state():
    """confirm_bracket_alive returns immediately if a leg is in a terminal state."""
    client = _MockClientForConfirm(
        stop_statuses=["Filled"],    # stop was hit — terminal
        target_statuses=["Cancelled"],
    )
    result = client.confirm_bracket_alive(
        stop_order_id=101, target_order_id=102,
        max_wait_seconds=5.0, poll_interval=0.0,
    )
    assert result.get("alive") is False
    assert result.get("reason") == "bracket_leg_terminal_state"


# ── T26: get_bracket_order_ids_by_symbol (dry-run client, no API) ────────────

def test_26_get_bracket_ids_no_auth_returns_empty():
    """
    Unauthenticated dry-run client: get_bracket_order_ids_by_symbol() must
    return {} without raising (exception is caught internally).
    """
    client = TradovateClient.create_dry_run()
    result = client.get_bracket_order_ids_by_symbol()
    assert isinstance(result, dict), f"expected dict, got {type(result)}"
    # No credentials → API call fails → returns {}
    assert result == {}, f"expected {{}}, got {result}"


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    ("T01 strip_month_code MESM5→MES",                          test_01_strip_mes),
    ("T02 strip_month_code MGCM5→MGC variants",                 test_02_strip_mgc),
    ("T03 strip_month_code full-size contracts",                 test_03_strip_full_size),
    ("T04 strip_month_code two-digit year (M25)",               test_04_strip_two_digit_year),
    ("T05 tv_contract_to_coordinator_symbol MES→ES",            test_05_coord_sym_mes),
    ("T06 tv_contract_to_coordinator_symbol MGC→GC",            test_06_coord_sym_mgc),
    ("T07 tv_contract_to_coordinator_symbol MNQ/SIL",           test_07_coord_sym_mnq),
    ("T08 bracket_ids_from_orders empty input",                  test_08_bracket_ids_empty),
    ("T09 bracket_ids_from_orders active statuses included",    test_09_bracket_ids_active_statuses),
    ("T10 bracket_ids_from_orders Filled/Cancelled excluded",   test_10_bracket_ids_excludes_filled),
    ("T11 build_broker_net_positions empty input",              test_11_build_empty_positions),
    ("T12 build_broker_net_positions flat positions skipped",   test_12_build_flat_positions_skipped),
    ("T13 build_broker_net_positions long+bracket→BNP",        test_13_build_long_with_bracket),
    ("T14 build_broker_net_positions short+bracket→BNP",       test_14_build_short_with_bracket),
    ("T15 build_broker_net_positions no brackets→empty ids",   test_15_build_no_bracket_ids_empty),
    ("T16 build_broker_net_positions SM fallback",              test_16_build_sm_bracket_fallback),
    ("T17 build_broker_net_positions API priority over SM",     test_17_build_api_bracket_takes_priority_over_sm),
    ("T18 build_broker_net_positions multi-symbol",             test_18_build_multi_symbol),
    ("T19 coordinator REVERSE_POSITION_BLOCKED via real BNP",  test_19_coordinator_reverse_blocked_via_real_broker_pos),
    ("T20 coordinator HUMAN_REVIEW missing bracket via BNP",   test_20_coordinator_human_review_missing_bracket),
    ("T21 coordinator HUMAN_REVIEW no virtual via BNP",        test_21_coordinator_human_review_no_virtual),
    ("T22 coordinator ACCEPT_NEW clean state via BNP",         test_22_coordinator_accept_new_clean_state),
    ("T23 confirm_bracket_alive success",                       test_23_confirm_bracket_alive_success),
    ("T24 confirm_bracket_alive timeout",                       test_24_confirm_bracket_alive_timeout),
    ("T25 confirm_bracket_alive terminal state",                test_25_confirm_bracket_terminal_state),
    ("T26 get_bracket_order_ids dry-run client returns {}",    test_26_get_bracket_ids_no_auth_returns_empty),
]


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  Broker Position Sync + Coordinator Integration — 26 Tests")
    print("  No API calls. No credentials. No orders.")
    print("=" * 72)

    for name, fn in TESTS:
        _test(name, fn)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed

    print("\n" + "=" * 72)
    print(f"  RESULTS: {passed}/{len(_results)} PASS  |  {failed} FAIL")
    print("=" * 72)

    if failed:
        print("\nFailed tests:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  FAIL  {name}")
                if detail:
                    print(f"        {detail}")
        sys.exit(1)
    else:
        print("\n  ALL TESTS PASS")
        sys.exit(0)
