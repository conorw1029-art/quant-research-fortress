# Portfolio Netting and Execution Model

**Date**: 2026-05-18  
**Status**: DRY-RUN READY — NOT DEMO AUTO-TRADE READY — NOT LIVE READY

---

## Section 1 — The Problem

Treating strategy-level positions as independent broker positions is unsafe.

Real futures brokers maintain **one net position per contract per account**. There is no concept of "Strategy A's long" and "Strategy B's short" existing simultaneously at the broker level. The broker sees only the net quantity.

**Example of the failure mode**: Strategy A is long 1 MES. Strategy B goes short 1 MES on the same account. The broker sees net 0 — both positions are offset. The stop and target bracket orders attached to the original long may be cancelled or may fire incorrectly. P&L attribution breaks because neither strategy's internal position state matches the actual broker state.

Consequences of the naive independent-position assumption:

- Bracket orders attached to a position are invalidated when that position is netted away by an opposing signal from another strategy.
- P&L attribution cannot be determined: the broker filled one net order, but the system logged two strategy-level fills.
- Risk limits enforced per-strategy are bypassed. Two strategies each within limits can together exceed the account risk limit.
- StateManager position records diverge from actual broker positions silently.
- Kill-switch flattening operates at the account level, not the strategy level. A strategy-level flat does not guarantee account-level flat.

This is not a theoretical concern. It is the primary structural risk in any multi-strategy execution system sharing a single brokerage account.

---

## Section 2 — Three Concepts

### 1. Strategy-Level Virtual Position

A virtual position is **internal only**. It tracks which strategy generated a signal, what direction and quantity were intended, and the notional P&L attributable to that strategy. It is used for research, performance attribution, and per-strategy risk monitoring.

A virtual position is **never directly sent to the broker**. It exists only in the system's internal state.

### 2. Broker-Level Net Position

The broker-level net position is the **actual account exposure**. There is exactly one net quantity per contract per account at any moment. This is what the broker sees, what margin is calculated on, and what bracket orders are attached to.

The broker-level net position is the only position that is real from a risk perspective.

### 3. PortfolioCoordinator

The PortfolioCoordinator is the **mandatory gate** between strategy signals and broker orders.

It receives all candidate `SignalIntent` objects from all active strategies. It resolves conflicts between them. It maps virtual strategy intents to the required broker-level order delta. Only signals that receive `CoordinatorDecision(ok=True, action=ACCEPT_NEW)` are allowed to reach `place_bracket_order()`.

No strategy may communicate directly with the broker execution layer. All execution passes through the coordinator.

---

## Section 3 — Three Examples

### Example A: Opposing signals, broker is flat

**Setup**: Strategy 16 wants long GC +1. Strategy 17 wants short GC -1. Broker is currently flat GC.

**Wrong**: Submit both orders independently. Both are filled. They net out at the broker — net position is 0. Both bracket orders are now attached to a flat position. The stop for the long fires incorrectly. The stop for the short fires incorrectly. The broker has zero exposure but two bracket order sets active.

**Correct (a) — Net to zero, send no order**: The coordinator detects the conflict. Net delta = 0. No order is sent. Both strategies log a `REJECT_CONFLICT` decision. The conflict is recorded for audit.

**Correct (b) — Priority resolution**: The coordinator selects one signal by priority (e.g., higher Sharpe, earlier timestamp, or explicit rank). The rejected strategy receives `REJECT_CONFLICT`. One order is sent for the approved strategy only.

**Correct (c) — Human escalation**: The coordinator rejects both signals and raises a `HUMAN_REVIEW_REQUIRED` event. No order is sent until a human decision is received. Appropriate when no priority rule can resolve the conflict safely.

---

### Example B: Active bracket present when opposing signal arrives

**Setup**: Strategy 16 already has virtual long GC +1. The broker IS long GC +1 with an active bracket (stop + target orders). Strategy 17 now emits short GC -1.

**Wrong**: Blindly submit the short order. The broker receives the short. This closes the long position. However, the bracket stop order is still active. The broker may trigger the stop loss fill AND process the new short simultaneously. The account may end up net short with a dangling bracket, or may suffer double-fill. StateManager no longer reflects reality.

**Correct**: Before routing Strategy 17's signal, the coordinator checks for active bracket orders on GC. If a bracket is present, the signal is tagged `REVERSE_POSITION_BLOCKED`. The coordinator issues a `HUMAN_REVIEW_REQUIRED` event. No order is submitted until the existing bracket is explicitly cancelled and confirmed, and the decision to reverse is made deliberately.

---

### Example C: Same-direction signals, position limit reached

**Setup**: Strategy 16 is long GC +1. Strategy 20 also emits long GC +1. Broker max for this account is 1 micro contract per symbol.

**Wrong**: Submit both orders. Broker position goes to +2. Risk is doubled. Margin requirement doubles. Topstep max-contracts rule is violated.

**Correct (a) — REJECT_SYMBOL_LIMIT**: The coordinator detects that net position would exceed `max_net_contracts_per_symbol`. Strategy 20's signal is rejected with `REJECT_SYMBOL_LIMIT`. No additional order is sent.

**Correct (b) — MERGE_ATTRIBUTION_ONLY**: Strategy 20's signal is tracked in virtual position state only for P&L attribution purposes. No additional broker order is submitted. The coordinator records that Strategy 20 is "along for the ride" on the existing position, and its virtual P&L is tracked from the current price.

---

## Section 4 — What Breaks Without This

### Bracket Orders

Bracket orders (stop-loss + profit-target) are attached to the broker position at the time of submission. If a second strategy reduces or reverses that position without first cancelling the brackets, the bracket orders become orphaned or misaligned. This can result in the stop firing against a position that no longer exists, or a profit-target fill crediting the wrong P&L.

### P&L Attribution

The broker fills one net order at one price. If two strategy virtual positions claim ownership of that fill, P&L attribution is undefined. Without the coordinator, there is no authority to determine which strategy's virtual P&L is correct.

### Risk Limits

The RiskManager enforces limits per strategy: max daily loss, max drawdown, max contracts. But if two strategies each stay within their individual limits while their combined broker exposure violates account-level limits, the per-strategy RiskManager cannot catch this. Only a coordinator that tracks the aggregate broker-level exposure can enforce account-level limits.

### Reconciliation

The StateManager maintains a record of expected positions based on strategy signals. Without a coordinator, the StateManager's expected positions are the sum of all strategy virtual positions — which will diverge from the broker's actual net position whenever any netting or offsetting occurs. Reconciliation checks will fail silently or produce incorrect alerts.

### Kill-Switch Flattening

The kill-switch issues a flatten-all command at the account level. The broker flattens to net zero. If the system believes multiple strategy positions are open, it may attempt to send multiple flatten orders, or may not send enough to reach true flat, depending on how the flattening logic is implemented. Only a coordinator that tracks the broker-level net position can guarantee that one flatten order reaches actual flat.

---

## Section 5 — Current Classification

| Gate | Status |
|---|---|
| DRY-RUN READY | YES — coordinator logic implemented and testable |
| DEMO AUTO-TRADE READY | NO — coordinator not yet validated in live dry-run conditions |
| LIVE READY | NO — requires completion of all pre-live gates |
| SAFE FOR MULTI-STRATEGY AUTO-EXECUTION | NO — coordinator must pass all 15 tests and complete 30+ validated demo sessions with zero HUMAN_REVIEW_REQUIRED events before concurrent multi-strategy execution is permitted |

The system is classified **DRY-RUN READY ONLY**. No automated order submission is permitted until the coordinator has been validated end-to-end in demo mode per the criteria in `strategy_deployment_eligibility.md` (Rules R8–R10).
