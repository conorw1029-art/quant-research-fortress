# Prop vs Broker Execution Plan

**Status**: Planning  
**Decision Required Before**: any live or demo automated trading begins

---

## Overview

This document defines the three possible execution routes, their constraints, compliance requirements, and the recommended sequencing. The central principle: compliance confirmation must precede automation, not follow it.

---

## Route A — TopstepX API

### Description
TopstepX exposes a trading API. This makes controlled automation technically possible for Topstep-funded accounts.

### Firm Rules That Must Be Obeyed

Topstep explicitly prohibits or restricts the following. Any automated system must be designed to never trigger these violations:

| Rule | Requirement |
|------|------------|
| No HFT | No strategies that rely on speed advantage, latency arbitrage, or sub-second order flow |
| No market manipulation | No spoofing, layering, or wash trading — even unintentional patterns |
| No prohibited strategy behaviour | Review Topstep's current prohibited list before deployment |
| No account abuse | No coordinated behaviour across multiple Topstep accounts |
| No mass order entry | No strategies that blast large numbers of orders to probe liquidity |
| No latency/arbitrage exploitation | Do not exploit price discrepancies between venues or data feeds |
| Daily flat requirement | System must be flat by the required daily cutoff — this must be hardcoded |

### Automation Constraints

- **Start with one strategy only** — not the full portfolio. One strategy, one account, monitored manually for the first 30+ days.
- **Use demo/sim first** — TopstepX has sim mode. Run there for at least 30 trading days before touching a funded account.
- **Do not assume all prop firms allow bots** — API access does not mean automation is permitted. API access is for platform connectivity, not blanket bot approval. Read the trader agreement.
- **Verify compliance before any automation** — get written or explicit documented confirmation from Topstep support that the specific strategy type (signal-based, rule-based, non-HFT) is permitted.

### Status
- Route A is the primary demo target once execution stack is proven in dry-run.
- Blocked until: compliance confirmed, dry-run validation complete, bracket orders confirmed working in sim, kill switch tested.

---

## Route B — Apex / Rithmic / NinjaTrader

### Default Assumption: Automation Prohibited

Unless written approval exists from Apex (or the relevant prop firm using Rithmic), treat full automation as **prohibited**. This is the conservative default, not an assumption that it is definitely banned — it is an assumption that it requires explicit confirmation.

### Permitted Uses Without Written Approval

- Alert assistant only: system generates a signal, sends a Telegram/Discord alert, human places the trade manually
- Human-confirmed trade assistant: system prepares an order, displays it in a UI, human clicks Confirm before execution
- Research and backtesting only

### Prohibited Without Written Approval

- Autonomous order submission on Apex accounts
- Account stacking (running the same strategy across multiple funded accounts simultaneously to multiply exposure)
- Copy trading (replicating trades across accounts automatically)
- Coordinated multi-account behaviour of any kind

### NinjaTrader Bridge

If a NinjaTrader bridge is built to interface with Rithmic or Apex:
- Design it as alert/human-confirmation by default
- The bridge should generate an OrderCandidate object that a human must approve in a UI before submission
- Do not wire the bridge directly to funded account execution without documented written approval from the prop firm
- Treat NinjaTrader automated execution on funded accounts as a future gate, not a current capability

### Status
- Route B is usable now as alert/confirmation assistant only.
- Full automation: blocked until written approval obtained from each firm.

---

## Route C — Personal Broker Account

### Description
Trading with personal capital through a retail broker (e.g., NinjaTrader Brokerage, Interactive Brokers, Tradovate personal account). No prop firm rules apply. Full legal autonomy.

### Constraints

| Constraint | Detail |
|-----------|--------|
| Capital at risk | Real money losses, no reset. Financial risk is personal. |
| Instrument scope | Micros only (MES, MNQ, MGC). Full-size contracts not permitted until extensive demo proof. |
| Position sizing | Smallest possible size. One micro contract per strategy max initially. |
| Entry condition | Only after demo proof — at least 30+ days of live demo results matching backtest expectations. |
| Pre-conditions (all required) | Bracket orders working, reconciliation working, state persistence working, kill switch tested, live-vs-backtest degradation report complete. |

### Why This Route Is Long-Term Best
- No prop firm rules constraining strategy design
- No account reset risk
- True autonomy over parameters and deployment
- Can scale naturally as edge is proven

### Why This Route Is Not First
- Real losses if system has bugs
- No cushion from funded account structure
- Must not be attempted until the system proves itself in demo with documented degradation report

### Status
- Route C is the long-term target.
- Blocked until: all pre-conditions met, demo results documented, degradation report produced.

---

## 4. Compliance Checklist

Complete this checklist before any execution route transitions from planning to active.

### For Each Route

| Check | TopstepX | Apex/Rithmic | Personal |
|-------|----------|--------------|---------|
| Written API/automation permission obtained | Required | Required | N/A |
| Prop firm trader agreement reviewed in full | Required | Required | N/A |
| Trading style confirmed as non-prohibited (no scalping rule violations, no HFT) | Required | Required | N/A |
| Account stacking explicitly prohibited — confirm single-account only | Required | Required | N/A |
| Maximum contracts per account confirmed | Required | Required | Check margin |
| Manual confirmation gates documented and tested | Required | Required | Required |
| Kill switch tested end-to-end | Required | Required | Required |
| Bracket order attach confirmed in sim | Required | Required | Required |
| Daily flat cutoff hardcoded and tested | Required | Required | N/A |
| Reconciliation logic verified | Required | Required | Required |

---

## 5. Recommended Sequence

Follow this sequence. Do not jump ahead.

```
Phase 1 (Now — Execution Safety)
  Build and test:
  - Bracket order attach (entry + stop + target in single submission)
  - Position reconciliation (broker state vs internal state)
  - State persistence across restarts
  - Kill switch (immediate flatten, disable all strategies)
  - Dry-run mode (signals logged, no orders sent)

Phase 2 (TopstepX Demo — Only If Compliance Confirmed)
  - Contact Topstep, confirm API automation permitted for rule-based signals
  - Run one strategy only in TopstepX sim for 30+ trading days
  - Document: fill quality, slippage vs backtest, signal-to-execution delay
  - Do not enable additional strategies until Phase 2 results reviewed

Phase 3 (Apex as Manual-Confirmation Only — Unless Written Approval)
  - Build alert assistant: signal fires → Telegram/Discord alert → human places trade
  - Only escalate to automated if Apex sends written approval
  - Never use account stacking across Apex accounts

Phase 4 (Personal Broker — Only After Demo Results Documented)
  - Requires: 30+ demo days with live-vs-backtest degradation report
  - Micros only, minimum size
  - Full pre-condition checklist above complete

Phase 5 (Scale)
  - Only after Phase 4 proves execution quality and live edge
  - Increase size incrementally
  - Re-run pre-condition checklist at each size increase
```

### Hard Rule

**No fully autonomous prop-firm trading until compliance is confirmed in writing.** API access is not permission. Sim access is not permission. The trader agreement is the source of truth.
