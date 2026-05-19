"""
test_portfolio_coordinator.py — 15-case portfolio coordinator test suite.
No external dependencies. No API calls. No credentials. No orders.

Run:
  C:\\Users\\conor\\Desktop\\quant-research\\venv_new\\Scripts\\python.exe -X utf8 test_portfolio_coordinator.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tick_portfolio_coordinator import (
    BrokerNetPosition,
    CoordinatorAction,
    CoordinatorConfig,
    PortfolioCoordinator,
    Side,
    SignalIntent,
    VirtualStrategyPosition,
)

# ── Minimal hand-rolled test runner ──────────────────────────────────────────

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


# ── Factory helpers ───────────────────────────────────────────────────────────

def _intent(
    strategy_id: int = 1,
    strategy_key: str = "GC/test/1m",
    symbol: str = "GC",
    side: Side = Side.LONG,
    desired_qty: int = 1,
    estimated_risk_usd: float = 200.0,
) -> SignalIntent:
    return SignalIntent(
        strategy_id=strategy_id,
        strategy_key=strategy_key,
        symbol=symbol,
        contract=symbol + "M5",
        side=side,
        desired_qty=desired_qty,
        entry_price=2000.0,
        stop_price=1990.0,
        target_price=2020.0,
        estimated_risk_usd=estimated_risk_usd,
        timestamp=datetime.now(timezone.utc),
    )


def _broker_pos(
    symbol: str = "GC",
    net_qty: int = 1,
    brackets: list | None = None,
) -> BrokerNetPosition:
    return BrokerNetPosition(
        account_id="test_account",
        symbol=symbol,
        contract=symbol + "M5",
        net_qty=net_qty,
        avg_price=2000.0,
        active_bracket_ids=brackets if brackets is not None else ["brk_001"],
        state="LONG" if net_qty > 0 else ("SHORT" if net_qty < 0 else "FLAT"),
    )


def _virtual_pos(
    strategy_id: int = 1,
    symbol: str = "GC",
    side: Side = Side.LONG,
) -> VirtualStrategyPosition:
    return VirtualStrategyPosition(
        strategy_id=strategy_id,
        strategy_key=f"{symbol}/test/1m",
        symbol=symbol,
        side=side,
        qty=1,
        entry_price=2000.0,
        stop_price=1990.0,
        target_price=2020.0,
        state="OPEN",
    )


def _permissive_cfg(**kwargs) -> CoordinatorConfig:
    """Base config that passes dry-run signals freely."""
    defaults = dict(
        one_strategy_only_demo=False,
        max_net_contracts_per_symbol=1,
        max_total_open_symbols=10,
        allow_position_increase_same_symbol=False,
        allow_reversal=False,
        dry_run_only=True,
        max_portfolio_risk_usd=3200.0,
    )
    defaults.update(kwargs)
    return CoordinatorConfig(**defaults)


# ── T01 ───────────────────────────────────────────────────────────────────────

def test_01_accept_new_no_position():
    """One allowed strategy, broker flat → ACCEPT_NEW."""
    coord = PortfolioCoordinator(_permissive_cfg())
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=1),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    assert dec.ok, f"expected ok=True, got action={dec.action} reason={dec.reason}"
    assert dec.action == CoordinatorAction.ACCEPT_NEW, dec.action


# ── T02 ───────────────────────────────────────────────────────────────────────

def test_02_demo_mode_rejects_wrong_strategy():
    """one_strategy_only_demo=True rejects any key that is not the demo key."""
    coord = PortfolioCoordinator(CoordinatorConfig(
        one_strategy_only_demo=True,
        demo_strategy_key="ES/cvd_divergence_large_print/15m",
        max_total_open_symbols=10,
    ))
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=99, strategy_key="GC/test/1m"),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "non-demo strategy should be rejected"
    assert dec.action == CoordinatorAction.REJECT_CONFLICT, dec.action
    assert "demo mode" in dec.reason.lower(), dec.reason


# ── T03 ───────────────────────────────────────────────────────────────────────

def test_03_opposite_signals_same_symbol_batch_reject():
    """evaluate_signals with LONG + SHORT on same symbol → both REJECT_CONFLICT."""
    coord = PortfolioCoordinator(_permissive_cfg())
    intents = [
        _intent(strategy_id=16, symbol="GC", side=Side.LONG,  strategy_key="GC/strat_a/30m"),
        _intent(strategy_id=17, symbol="GC", side=Side.SHORT, strategy_key="GC/strat_b/30m"),
    ]
    decisions = coord.evaluate_signals(
        signal_intents=intents,
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    for sid in [16, 17]:
        d = decisions[sid]
        assert not d.ok, f"strategy {sid} should be rejected"
        assert d.action == CoordinatorAction.REJECT_CONFLICT, \
            f"strategy {sid}: expected REJECT_CONFLICT, got {d.action}"


# ── T04 ───────────────────────────────────────────────────────────────────────

def test_04_long_and_short_gc_same_account_no_independent_orders():
    """Long GC + Short GC same account → coordinator rejects both; no independent orders sent."""
    coord = PortfolioCoordinator(_permissive_cfg())
    intents = [
        _intent(strategy_id=1, symbol="GC", side=Side.LONG,  strategy_key="GC/a/15m"),
        _intent(strategy_id=2, symbol="GC", side=Side.SHORT, strategy_key="GC/b/15m"),
    ]
    decisions = coord.evaluate_signals(
        signal_intents=intents,
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
    )
    accepted = [sid for sid, d in decisions.items() if d.ok]
    assert len(accepted) == 0, \
        f"no orders should reach broker; accepted: {accepted}"


# ── T05 ───────────────────────────────────────────────────────────────────────

def test_05_two_long_signals_max_qty_one_rejects_second():
    """Strategy 16 holds long GC at broker; strategy 20 also wants long GC → REJECT_SYMBOL_LIMIT."""
    coord = PortfolioCoordinator(_permissive_cfg(
        max_net_contracts_per_symbol=1,
        allow_position_increase_same_symbol=False,
    ))
    broker  = [_broker_pos("GC", net_qty=1, brackets=["brk_01"])]
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG, strategy_key="GC/strat_b/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "second long should be rejected"
    assert dec.action == CoordinatorAction.REJECT_SYMBOL_LIMIT, dec.action


# ── T06 ───────────────────────────────────────────────────────────────────────

def test_06_broker_long_new_short_reverse_blocked():
    """Broker is long GC +1 with bracket; new short signal → REVERSE_POSITION_BLOCKED."""
    coord = PortfolioCoordinator(_permissive_cfg(allow_reversal=False))
    broker  = [_broker_pos("GC", net_qty=1, brackets=["brk_01"])]
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=17, symbol="GC", side=Side.SHORT, strategy_key="GC/strat_b/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "reversal should be blocked"
    assert dec.action == CoordinatorAction.REVERSE_POSITION_BLOCKED, dec.action


# ── T07 ───────────────────────────────────────────────────────────────────────

def test_07_virtual_open_broker_flat_human_review():
    """Virtual position shows OPEN for GC but broker is flat → HUMAN_REVIEW_REQUIRED."""
    coord = PortfolioCoordinator(_permissive_cfg())
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]
    broker  = []  # broker flat (no entry for GC)

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG, strategy_key="GC/c/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "virtual/broker mismatch should require human review"
    assert dec.action == CoordinatorAction.HUMAN_REVIEW_REQUIRED, dec.action
    assert dec.requires_human_review


# ── T08 ───────────────────────────────────────────────────────────────────────

def test_08_broker_long_no_virtual_tracking_human_review():
    """Broker has long GC position but no virtual position tracks it → HUMAN_REVIEW_REQUIRED."""
    coord = PortfolioCoordinator(_permissive_cfg())
    broker  = [_broker_pos("GC", net_qty=1, brackets=["brk_01"])]
    virtual = []  # no virtual tracking for GC

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG, strategy_key="GC/c/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "untracked broker position requires human review"
    assert dec.action == CoordinatorAction.HUMAN_REVIEW_REQUIRED, dec.action
    assert dec.requires_human_review


# ── T09 ───────────────────────────────────────────────────────────────────────

def test_09_broker_bracket_missing_human_review():
    """Broker holds GC with empty active_bracket_ids → HUMAN_REVIEW_REQUIRED."""
    coord = PortfolioCoordinator(_permissive_cfg())
    broker  = [_broker_pos("GC", net_qty=1, brackets=[])]  # no bracket
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=20, symbol="GC", side=Side.LONG, strategy_key="GC/c/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "missing bracket requires human review"
    assert dec.action == CoordinatorAction.HUMAN_REVIEW_REQUIRED, dec.action
    assert dec.requires_human_review


# ── T10 ───────────────────────────────────────────────────────────────────────

def test_10_risk_above_limit_rejected():
    """estimated_risk_usd > max_portfolio_risk_usd → REJECT_RISK_LIMIT."""
    coord = PortfolioCoordinator(_permissive_cfg(max_portfolio_risk_usd=500.0))
    dec = coord.evaluate_single_signal(
        _intent(strategy_id=1, estimated_risk_usd=1500.0),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "over-risk signal should be rejected"
    assert dec.action == CoordinatorAction.REJECT_RISK_LIMIT, dec.action


# ── T11 ───────────────────────────────────────────────────────────────────────

def test_11_second_symbol_exceeds_max_open_symbols():
    """GC already open; SI new signal; max_total_open_symbols=1 → REJECT_SYMBOL_LIMIT."""
    coord = PortfolioCoordinator(_permissive_cfg(max_total_open_symbols=1))
    broker  = [_broker_pos("GC", net_qty=1, brackets=["brk_01"])]
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=30, symbol="SI", side=Side.LONG, strategy_key="SI/c/30m"),
        virtual_positions=virtual,
        broker_positions=broker,
        open_orders=[],
        kill_switch=False,
    )
    assert not dec.ok, "second symbol should be rejected"
    assert dec.action == CoordinatorAction.REJECT_SYMBOL_LIMIT, dec.action
    assert "max_total_open_symbols" in dec.reason


# ── T12 ───────────────────────────────────────────────────────────────────────

def test_12_only_demo_key_allowed_in_demo_mode():
    """Demo mode: correct key passes rule 2; incorrect key is rejected with REJECT_CONFLICT."""
    cfg = CoordinatorConfig(
        one_strategy_only_demo=True,
        demo_strategy_key="ES/cvd_divergence_large_print/15m",
        max_total_open_symbols=10,
    )
    coord = PortfolioCoordinator(cfg)

    # Correct demo key → should not be blocked by rule 2
    dec_ok = coord.evaluate_single_signal(
        _intent(strategy_id=2, strategy_key="ES/cvd_divergence_large_print/15m", symbol="ES"),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    assert dec_ok.action == CoordinatorAction.ACCEPT_NEW, \
        f"demo key should reach ACCEPT_NEW, got: {dec_ok.action} / {dec_ok.reason}"

    # Wrong key → REJECT_CONFLICT from rule 2
    dec_wrong = coord.evaluate_single_signal(
        _intent(strategy_id=99, strategy_key="NQ/other/30m", symbol="NQ"),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=False,
    )
    assert not dec_wrong.ok, "non-demo key must be rejected"
    assert dec_wrong.action == CoordinatorAction.REJECT_CONFLICT, dec_wrong.action
    assert "demo mode" in dec_wrong.reason.lower(), dec_wrong.reason


# ── T13 ───────────────────────────────────────────────────────────────────────

def test_13_attribution_stored_without_broker_change():
    """
    calculate_net_desired_exposure() computes desired attribution without touching
    broker state. decide_order_delta() returns 0 when broker is already at desired net.
    This validates that virtual P&L tracking is decoupled from order submission.
    """
    coord = PortfolioCoordinator(_permissive_cfg())
    virtual = [_virtual_pos(strategy_id=16, symbol="GC", side=Side.LONG)]  # virtual +1
    intent  = _intent(strategy_id=20, symbol="GC", side=Side.LONG, strategy_key="GC/c/30m")

    # desired exposure = virtual +1 + new signal +1 = +2 (attribution only)
    net = coord.calculate_net_desired_exposure(
        signal_intents=[intent],
        virtual_positions=virtual,
    )
    assert net.get("GC", 0) == 2, f"expected desired net=2 for GC, got {net}"

    # Broker already at +1 and desired net for this signal = +1 → delta = 0
    broker_pos = _broker_pos("GC", net_qty=1)
    delta = coord.decide_order_delta(broker_pos, desired_net_qty=1)
    assert delta == 0, f"no broker order if already at desired qty; got delta={delta}"


# ── T14 ───────────────────────────────────────────────────────────────────────

def test_14_flatten_is_broker_level_not_strategy_level():
    """
    decide_order_delta() produces a single net flatten delta, not one per strategy.
    Broker at +1, desired 0 → delta -1 (one order regardless of how many strategies
    held virtual long).
    """
    coord = PortfolioCoordinator(_permissive_cfg())

    broker_long1 = _broker_pos("GC", net_qty=1)
    assert coord.decide_order_delta(broker_long1, desired_net_qty=0) == -1, \
        "flatten from +1 to 0 should produce delta=-1"

    broker_long2 = _broker_pos("GC", net_qty=2)
    assert coord.decide_order_delta(broker_long2, desired_net_qty=0) == -2, \
        "flatten from +2 to 0 should produce delta=-2"

    broker_at_desired = _broker_pos("GC", net_qty=1)
    assert coord.decide_order_delta(broker_at_desired, desired_net_qty=1) == 0, \
        "broker already at desired → no order"

    broker_short1 = _broker_pos("GC", net_qty=-1)
    assert coord.decide_order_delta(broker_short1, desired_net_qty=0) == 1, \
        "flatten from -1 to 0 should produce delta=+1"


# ── T15 ───────────────────────────────────────────────────────────────────────

def test_15_kill_switch_rejects_every_intent():
    """Kill switch active → every signal rejected regardless of other conditions."""
    coord = PortfolioCoordinator(_permissive_cfg())

    dec = coord.evaluate_single_signal(
        _intent(strategy_id=1),
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=True,
    )
    assert not dec.ok, "kill switch should reject single signal"
    assert dec.action == CoordinatorAction.REJECT_CONFLICT, dec.action
    assert "kill switch" in dec.reason.lower(), dec.reason

    # All signals rejected in batch too
    intents = [
        _intent(strategy_id=1, symbol="GC", strategy_key="GC/a/30m"),
        _intent(strategy_id=2, symbol="ES", strategy_key="ES/b/15m"),
        _intent(strategy_id=3, symbol="NQ", strategy_key="NQ/c/30m"),
    ]
    decisions = coord.evaluate_signals(
        signal_intents=intents,
        virtual_positions=[],
        broker_positions=[],
        open_orders=[],
        kill_switch=True,
    )
    for sid, d in decisions.items():
        assert not d.ok, f"strategy {sid}: kill switch should reject"
        assert d.action == CoordinatorAction.REJECT_CONFLICT, \
            f"strategy {sid}: expected REJECT_CONFLICT, got {d.action}"
        assert "kill switch" in d.reason.lower(), d.reason


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    ("T01 one allowed strategy no position → ACCEPT_NEW",              test_01_accept_new_no_position),
    ("T02 demo mode rejects wrong strategy key",                        test_02_demo_mode_rejects_wrong_strategy),
    ("T03 opposite signals same symbol in batch → both REJECT_CONFLICT", test_03_opposite_signals_same_symbol_batch_reject),
    ("T04 long+short GC same account → no independent orders",          test_04_long_and_short_gc_same_account_no_independent_orders),
    ("T05 two longs max_qty=1 → second REJECT_SYMBOL_LIMIT",           test_05_two_long_signals_max_qty_one_rejects_second),
    ("T06 broker long new short → REVERSE_POSITION_BLOCKED",            test_06_broker_long_new_short_reverse_blocked),
    ("T07 virtual open broker flat → HUMAN_REVIEW_REQUIRED",            test_07_virtual_open_broker_flat_human_review),
    ("T08 broker long no virtual tracking → HUMAN_REVIEW_REQUIRED",     test_08_broker_long_no_virtual_tracking_human_review),
    ("T09 broker bracket missing → HUMAN_REVIEW_REQUIRED",              test_09_broker_bracket_missing_human_review),
    ("T10 risk above limit → REJECT_RISK_LIMIT",                        test_10_risk_above_limit_rejected),
    ("T11 second symbol exceeds max_total_open_symbols → reject",       test_11_second_symbol_exceeds_max_open_symbols),
    ("T12 only demo key allowed in demo mode",                          test_12_only_demo_key_allowed_in_demo_mode),
    ("T13 attribution tracked without broker order delta",              test_13_attribution_stored_without_broker_change),
    ("T14 flatten decision is broker-level not strategy-level",         test_14_flatten_is_broker_level_not_strategy_level),
    ("T15 kill switch rejects every intent",                            test_15_kill_switch_rejects_every_intent),
]


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  Portfolio Coordinator — 15 Tests")
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
