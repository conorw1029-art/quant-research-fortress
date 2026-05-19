"""
tick_portfolio_coordinator.py — Fortress Portfolio Coordinator
==============================================================
Sits as a mandatory gate between strategy signal computation and actual
order submission. Ensures that no conflicting, redundant, or limit-breaching
orders reach the broker.

Architecture:
  tick_live_executor.py
       |
       v  (SignalIntent[])
  PortfolioCoordinator.evaluate_signals()
       |
       v  (CoordinatorDecision per strategy)
  Broker / tick_tradovate_client.py

Key invariants enforced:
  - Only one broker-level net position per symbol at a time (configurable)
  - Opposite-direction signals on the same symbol from different strategies
    are rejected before any order is sent
  - Position/virtual-tracking mismatches trigger human review
  - Missing bracket stops trigger human review
  - Hard risk limit checked before any acceptance

No external dependencies. Pure Python stdlib only.

Run:
  python -X utf8 test_portfolio_coordinator.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class Side(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"
    FLAT  = "FLAT"


class CoordinatorAction(Enum):
    ACCEPT_NEW                = "ACCEPT_NEW"
    REJECT_CONFLICT           = "REJECT_CONFLICT"
    REJECT_SYMBOL_LIMIT       = "REJECT_SYMBOL_LIMIT"
    REJECT_RISK_LIMIT         = "REJECT_RISK_LIMIT"
    NET_TO_ZERO               = "NET_TO_ZERO"
    MERGE_ATTRIBUTION_ONLY    = "MERGE_ATTRIBUTION_ONLY"
    CLOSE_EXISTING            = "CLOSE_EXISTING"
    REVERSE_POSITION_BLOCKED  = "REVERSE_POSITION_BLOCKED"
    HUMAN_REVIEW_REQUIRED     = "HUMAN_REVIEW_REQUIRED"


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class SignalIntent:
    strategy_id:        int
    strategy_key:       str
    symbol:             str
    contract:           str
    side:               Side
    desired_qty:        int
    entry_price:        float
    stop_price:         float
    target_price:       float
    estimated_risk_usd: float
    timestamp:          datetime
    confidence_score:   Optional[float] = None
    status:             str = "PENDING"


@dataclass
class VirtualStrategyPosition:
    strategy_id:     int
    strategy_key:    str
    symbol:          str
    side:            Side
    qty:             int
    entry_price:     float
    stop_price:      float
    target_price:    float
    broker_order_ids: List[str] = field(default_factory=list)
    attribution_pnl: float = 0.0
    state:           str = "OPEN"   # OPEN or CLOSED


@dataclass
class BrokerNetPosition:
    account_id:        str
    symbol:            str
    contract:          str
    net_qty:           int    # positive=long, negative=short, 0=flat
    avg_price:         float
    open_order_ids:    List[str] = field(default_factory=list)
    active_bracket_ids: List[str] = field(default_factory=list)
    state:             str = "FLAT"


@dataclass
class CoordinatorDecision:
    ok:                   bool
    action:               CoordinatorAction
    reason:               str
    affected_strategies:  List[int] = field(default_factory=list)
    broker_order_delta:   int = 0     # net qty delta to send to broker
    requires_bracket_change: bool = False
    requires_human_review:   bool = False
    signal_intent:        Optional[SignalIntent] = None


@dataclass
class CoordinatorConfig:
    max_net_contracts_per_symbol:            int   = 1
    max_total_open_symbols:                  int   = 1
    allow_opposite_strategy_signals_same_symbol: bool = False
    allow_position_increase_same_symbol:     bool  = False
    allow_reversal:                          bool  = False
    one_strategy_only_demo:                  bool  = True
    demo_strategy_key:                       str   = "ES/cvd_divergence_large_print/15m"
    dry_run_only:                            bool  = True
    max_portfolio_risk_usd:                  float = 3200.0


# ── PortfolioCoordinator ───────────────────────────────────────────────────────

class PortfolioCoordinator:
    """
    Mandatory gate between strategy signals and broker order submission.

    Call evaluate_signals() each poll cycle with all pending SignalIntents.
    Returns a dict of strategy_id → CoordinatorDecision. Only decisions with
    ok=True and action=ACCEPT_NEW should proceed to the broker.
    """

    def __init__(self, config: CoordinatorConfig = None):
        self.config = config or CoordinatorConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def evaluate_signals(
        self,
        signal_intents:   List[SignalIntent],
        virtual_positions: List[VirtualStrategyPosition],
        broker_positions:  List[BrokerNetPosition],
        open_orders:       List[dict],
        kill_switch:       bool = False,
    ) -> Dict[int, CoordinatorDecision]:
        """
        Batch evaluation of all pending signals.

        Steps:
          1. If kill_switch, reject every intent immediately.
          2. Detect same-symbol opposite-direction conflicts among the intents.
          3. Reject conflicting strategy IDs.
          4. For each non-conflicting intent call evaluate_single_signal().

        Returns dict of strategy_id → CoordinatorDecision.
        """
        decisions: Dict[int, CoordinatorDecision] = {}

        if kill_switch:
            for intent in signal_intents:
                decisions[intent.strategy_id] = CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REJECT_CONFLICT,
                    reason="kill switch active — all signals rejected",
                    affected_strategies=[intent.strategy_id],
                    signal_intent=intent,
                )
            return decisions

        # Detect batch-level conflicts
        conflicting_ids = self.detect_same_symbol_conflicts(signal_intents)

        # Build a quick lookup: symbol → conflicting sides (for reason string)
        symbol_sides: Dict[str, set] = {}
        for intent in signal_intents:
            symbol_sides.setdefault(intent.symbol, set()).add(intent.side)

        for intent in signal_intents:
            if intent.strategy_id in conflicting_ids:
                # Find which symbol caused the conflict
                conflict_symbol = intent.symbol
                decisions[intent.strategy_id] = CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REJECT_CONFLICT,
                    reason=f"opposite-direction conflict on {conflict_symbol}",
                    affected_strategies=list(conflicting_ids),
                    signal_intent=intent,
                )
            else:
                decisions[intent.strategy_id] = self.evaluate_single_signal(
                    signal=intent,
                    virtual_positions=virtual_positions,
                    broker_positions=broker_positions,
                    open_orders=open_orders,
                    kill_switch=False,
                )

        return decisions

    def evaluate_single_signal(
        self,
        signal:            SignalIntent,
        virtual_positions: List[VirtualStrategyPosition],
        broker_positions:  List[BrokerNetPosition],
        open_orders:       List[dict],
        kill_switch:       bool = False,
    ) -> CoordinatorDecision:
        """
        Evaluate a single SignalIntent against all coordinator rules.

        Rule order (first match wins):
          1. Kill switch
          2. Demo mode strategy filter
          3. Broker has opposite-side position → REVERSE_POSITION_BLOCKED
          4. Broker has position with no bracket stop → HUMAN_REVIEW_REQUIRED
          5. Virtual open but broker flat → HUMAN_REVIEW_REQUIRED
          6. Broker has position but no virtual tracking → HUMAN_REVIEW_REQUIRED
          7. Same-direction would exceed max_net_contracts_per_symbol
          8. New symbol would exceed max_total_open_symbols
          9. Estimated risk exceeds max_portfolio_risk_usd
         10. All clear → ACCEPT_NEW
        """
        cfg = self.config

        # ── Rule 1: Kill switch ────────────────────────────────────────────────
        if kill_switch:
            return CoordinatorDecision(
                ok=False,
                action=CoordinatorAction.REJECT_CONFLICT,
                reason="kill switch active — all signals rejected",
                affected_strategies=[signal.strategy_id],
                signal_intent=signal,
            )

        # ── Rule 2: Demo mode strategy filter ─────────────────────────────────
        if cfg.one_strategy_only_demo:
            if signal.strategy_key != cfg.demo_strategy_key:
                return CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REJECT_CONFLICT,
                    reason=(
                        f"demo mode: only {cfg.demo_strategy_key} allowed, "
                        f"got {signal.strategy_key}"
                    ),
                    affected_strategies=[signal.strategy_id],
                    signal_intent=signal,
                )

        # Helper: find broker position for this symbol
        broker_pos: Optional[BrokerNetPosition] = None
        for bp in broker_positions:
            if bp.symbol == signal.symbol:
                broker_pos = bp
                break

        # ── Rule 3: Broker has existing opposite-side position ─────────────────
        if broker_pos is not None and broker_pos.net_qty != 0:
            is_opposite = (
                (broker_pos.net_qty > 0 and signal.side == Side.SHORT) or
                (broker_pos.net_qty < 0 and signal.side == Side.LONG)
            )
            if is_opposite and not cfg.allow_reversal:
                return CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REVERSE_POSITION_BLOCKED,
                    reason=(
                        f"broker already holds {'LONG' if broker_pos.net_qty > 0 else 'SHORT'} "
                        f"{signal.symbol} (net_qty={broker_pos.net_qty}); "
                        f"reversal blocked by config"
                    ),
                    affected_strategies=[signal.strategy_id],
                    signal_intent=signal,
                )

        # ── Rule 4: Broker position exists but has no bracket stop ─────────────
        if broker_pos is not None and broker_pos.net_qty != 0:
            if len(broker_pos.active_bracket_ids) == 0:
                return self.require_human_review(
                    reason=(
                        f"broker holds {signal.symbol} (net_qty={broker_pos.net_qty}) "
                        f"but active_bracket_ids=[] — no bracket stop attached"
                    ),
                    signal=signal,
                )

        # ── Rule 5: Virtual open but broker is flat ────────────────────────────
        open_virtuals_same_symbol = [
            vp for vp in virtual_positions
            if vp.symbol == signal.symbol and vp.state == "OPEN"
        ]
        broker_flat_for_symbol = (broker_pos is None or broker_pos.net_qty == 0)
        if open_virtuals_same_symbol and broker_flat_for_symbol:
            return self.require_human_review(
                reason=(
                    f"virtual position shows OPEN for {signal.symbol} "
                    f"but broker is flat — state mismatch"
                ),
                signal=signal,
            )

        # ── Rule 6: Broker has position but no virtual tracking ────────────────
        if broker_pos is not None and broker_pos.net_qty != 0:
            if not open_virtuals_same_symbol:
                return self.require_human_review(
                    reason=(
                        f"broker has {signal.symbol} net_qty={broker_pos.net_qty} "
                        f"but no virtual position tracks it — state mismatch"
                    ),
                    signal=signal,
                )

        # ── Rule 7: Same-direction would exceed max_net_contracts_per_symbol ───
        current_net = broker_pos.net_qty if broker_pos else 0
        if signal.side == Side.LONG:
            desired_net = current_net + signal.desired_qty
        elif signal.side == Side.SHORT:
            desired_net = current_net - signal.desired_qty
        else:
            desired_net = 0

        if not cfg.allow_position_increase_same_symbol:
            # Only allow net moves from zero to one (new entry); no stacking
            already_open_same_direction = (
                (signal.side == Side.LONG  and current_net > 0) or
                (signal.side == Side.SHORT and current_net < 0)
            )
            if already_open_same_direction:
                return CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REJECT_SYMBOL_LIMIT,
                    reason=(
                        f"broker already has {signal.symbol} net_qty={current_net}; "
                        f"position increase blocked by config"
                    ),
                    affected_strategies=[signal.strategy_id],
                    signal_intent=signal,
                )

        if abs(desired_net) > cfg.max_net_contracts_per_symbol:
            decision = self.enforce_symbol_limits(signal.symbol, desired_net)
            if decision is not None:
                decision.affected_strategies = [signal.strategy_id]
                decision.signal_intent = signal
                return decision

        # ── Rule 8: New symbol would exceed max_total_open_symbols ─────────────
        currently_open_symbols = set()
        for bp in broker_positions:
            if bp.net_qty != 0:
                currently_open_symbols.add(bp.symbol)

        symbol_already_open = signal.symbol in currently_open_symbols
        if not symbol_already_open:
            if len(currently_open_symbols) >= cfg.max_total_open_symbols:
                return CoordinatorDecision(
                    ok=False,
                    action=CoordinatorAction.REJECT_SYMBOL_LIMIT,
                    reason=(
                        f"adding {signal.symbol} would exceed max_total_open_symbols="
                        f"{cfg.max_total_open_symbols} "
                        f"(currently open: {sorted(currently_open_symbols)})"
                    ),
                    affected_strategies=[signal.strategy_id],
                    signal_intent=signal,
                )

        # ── Rule 9: Portfolio risk limit ───────────────────────────────────────
        risk_decision = self.enforce_portfolio_risk_limit(
            signal_intents=[signal],
            existing_risk_usd=0.0,
        )
        if risk_decision is not None:
            risk_decision.affected_strategies = [signal.strategy_id]
            risk_decision.signal_intent = signal
            return risk_decision

        # ── Rule 10: All clear — ACCEPT_NEW ───────────────────────────────────
        broker_order_delta = self.decide_order_delta(
            current_broker_position=broker_pos,
            desired_net_qty=desired_net,
        )

        return CoordinatorDecision(
            ok=True,
            action=CoordinatorAction.ACCEPT_NEW,
            reason=f"all checks passed for {signal.symbol} {signal.side.value}",
            affected_strategies=[signal.strategy_id],
            broker_order_delta=broker_order_delta,
            signal_intent=signal,
        )

    # ── Helper methods ─────────────────────────────────────────────────────────

    def detect_same_symbol_conflicts(
        self, signal_intents: List[SignalIntent]
    ) -> set:
        """
        Returns set of strategy_ids that are part of a conflict — i.e., they
        are sending opposite-direction signals on the same symbol in the same
        batch.
        """
        # symbol → list of (strategy_id, side)
        symbol_signals: Dict[str, List[tuple]] = {}
        for intent in signal_intents:
            symbol_signals.setdefault(intent.symbol, []).append(
                (intent.strategy_id, intent.side)
            )

        conflicting_ids: set = set()
        for symbol, entries in symbol_signals.items():
            sides_seen = set(side for _, side in entries)
            # Conflict if both LONG and SHORT are present on the same symbol
            has_long  = Side.LONG  in sides_seen
            has_short = Side.SHORT in sides_seen
            if has_long and has_short:
                for strat_id, _ in entries:
                    conflicting_ids.add(strat_id)

        return conflicting_ids

    def calculate_net_desired_exposure(
        self,
        signal_intents:    List[SignalIntent],
        virtual_positions: List[VirtualStrategyPosition],
    ) -> Dict[str, int]:
        """
        Returns dict of symbol → net desired qty (positive=long, negative=short).

        Sums:
          - Open virtual positions (OPEN state only, signed by side)
          - New signal intents (signed by side)

        Does NOT place any orders. Pure computation.
        """
        net: Dict[str, int] = {}

        # Sum existing virtual positions
        for vp in virtual_positions:
            if vp.state != "OPEN":
                continue
            signed_qty = vp.qty if vp.side == Side.LONG else -vp.qty
            net[vp.symbol] = net.get(vp.symbol, 0) + signed_qty

        # Add new signal intents
        for intent in signal_intents:
            if intent.side == Side.LONG:
                signed_qty = intent.desired_qty
            elif intent.side == Side.SHORT:
                signed_qty = -intent.desired_qty
            else:
                signed_qty = 0
            net[intent.symbol] = net.get(intent.symbol, 0) + signed_qty

        return net

    def enforce_symbol_limits(
        self, symbol: str, desired_net_qty: int
    ) -> Optional[CoordinatorDecision]:
        """
        Returns a REJECT_SYMBOL_LIMIT decision if abs(desired_net_qty) exceeds
        max_net_contracts_per_symbol. Returns None if ok.
        """
        if abs(desired_net_qty) > self.config.max_net_contracts_per_symbol:
            return CoordinatorDecision(
                ok=False,
                action=CoordinatorAction.REJECT_SYMBOL_LIMIT,
                reason=(
                    f"desired net qty {desired_net_qty} for {symbol} exceeds "
                    f"max_net_contracts_per_symbol={self.config.max_net_contracts_per_symbol}"
                ),
            )
        return None

    def enforce_portfolio_risk_limit(
        self,
        signal_intents:    List[SignalIntent],
        existing_risk_usd: float = 0.0,
    ) -> Optional[CoordinatorDecision]:
        """
        Returns a REJECT_RISK_LIMIT decision if sum of estimated_risk_usd
        (across all incoming intents plus existing_risk_usd) exceeds
        max_portfolio_risk_usd. Returns None if ok.
        """
        total_new_risk = sum(i.estimated_risk_usd for i in signal_intents)
        total_risk = existing_risk_usd + total_new_risk

        if total_risk > self.config.max_portfolio_risk_usd:
            return CoordinatorDecision(
                ok=False,
                action=CoordinatorAction.REJECT_RISK_LIMIT,
                reason=(
                    f"total estimated risk ${total_risk:.2f} exceeds "
                    f"max_portfolio_risk_usd=${self.config.max_portfolio_risk_usd:.2f}"
                ),
            )
        return None

    def decide_order_delta(
        self,
        current_broker_position: Optional[BrokerNetPosition],
        desired_net_qty: int,
    ) -> int:
        """
        Returns the net qty delta to send to the broker.

        Examples:
          current=0,   desired=+1 →  +1 (open long)
          current=+1,  desired=0  →  -1 (close long)
          current=0,   desired=-1 →  -1 (open short)
          current=-1,  desired=0  →  +1 (close short)
          current=+1,  desired=+1 →   0 (already there)
        """
        current_net = current_broker_position.net_qty if current_broker_position else 0
        return desired_net_qty - current_net

    def require_human_review(
        self,
        reason: str,
        signal: SignalIntent = None,
    ) -> CoordinatorDecision:
        """Builds a HUMAN_REVIEW_REQUIRED decision."""
        affected = [signal.strategy_id] if signal else []
        return CoordinatorDecision(
            ok=False,
            action=CoordinatorAction.HUMAN_REVIEW_REQUIRED,
            reason=reason,
            affected_strategies=affected,
            requires_human_review=True,
            signal_intent=signal,
        )
