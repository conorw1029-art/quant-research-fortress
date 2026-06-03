# AI Brain Safety Policy

**Version:** 1.0  
**Date:** 2026-06-03  
**Applies to:** All code in `04_codebase/ai_brain/`

---

## What the AI Brain Can Do

- **Summarise** — read market data, signal logs, and evidence reports, and
  produce structured summaries for human review.
- **Alert** — send notifications to console, Telegram, and Discord when
  thresholds are breached. Alerts are advisory; they do not trigger any action.
- **Recommend** — log recommendations with a human-readable explanation.
  Recommendations are logged in DecisionLog and acted on only by a human.
- **Pause (safe mode only)** — RiskSentinel may flag a strategy for pause
  when a risk limit is breached. The actual pause is executed by the live
  system, not by the AI brain. The AI brain cannot inject halt signals.
- **Read and update strategy status** — StrategyLibrarian may update the
  status field in `strategy_universe_exhaustive.json`. Status moves that
  create deployment candidates are always logged with
  `human_approval_required=True`.

---

## What the AI Brain Cannot Do

These actions are **prohibited** and enforced at the code level:

| Prohibited Action | Enforcement Mechanism |
|-------------------|-----------------------|
| Place, modify, or cancel orders | No broker API connection exists in ai_brain |
| Enable live trading | `DeploymentGatekeeper.approve_for_live()` always raises `PermissionError` |
| Increase position sizing | `RiskSentinel.can_increase_size()` always returns `(False, reason)` |
| Override or reset the kill switch | MonitorAgent reads KILL_SWITCH.txt; it cannot write to it |
| Hide, delete, or modify losses | DecisionLog is append-only; no agent has write access to P&L records |
| Deploy a strategy to paper or demo without human action | `approve_for_paper()` logs only; it never enables anything |
| Auto-approve any deployment | All deployment-adjacent log entries carry `human_approval_required=True` |
| Edit backtest results or evidence files | ValidationAgent runs the upgrade script but cannot modify its output |
| Delete DecisionLog entries | DecisionLog has no delete method; all entries are permanent |

---

## How These Restrictions Are Enforced

### Code-Level (Hard Constraints)

**`DeploymentGatekeeper.approve_for_live()`**  
Raises `PermissionError` unconditionally, regardless of input, evidence
quality, or calling context. There is no code path that bypasses this.

**`DeploymentGatekeeper.approve_for_paper()`**  
Calls `dlog.log(..., action_taken="pending", human_approval_required=True)`
and prints a warning to stdout. It does not write any config, toggle any flag,
or signal any downstream system. The word "approve" in the name is intentional —
it records an approval *request*, not an approval *grant*.

**`RiskSentinel.can_increase_size()`**  
Returns `(False, reason)` unconditionally. There is no override flag or
environment variable that can change this return value.

**No broker connection in `ai_brain/`**  
None of the nine agent modules import any broker library (IB API, Tradovate,
Rithmic, NinjaTrader). There is no mechanism for the AI brain to reach the
market, even if an agent contained malicious logic.

### Audit Trail (DecisionLog)

Every significant AI brain action produces a `DecisionLog` entry that includes:
- Timestamp (UTC)
- Agent name
- Observation (what was seen — factual)
- Recommendation (what was suggested)
- Action taken (what the agent actually did)
- `human_approval_required` flag
- Risk level (LOW / MEDIUM / HIGH / CRITICAL)

Entries are written to:  
`06_live_trading/ai_logs/ai_decisions_YYYYMMDD.jsonl`

Log files are append-only. No agent has a mechanism to overwrite or delete
existing entries. The daily report reads these files for its summary.

### Review Cadence

1. **Daily** — DailyReportAgent generates `reports/daily_YYYYMMDD.json`.
   Review `human_approval_required=True` entries before market open.
2. **Per deployment attempt** — Any strategy moving to PAPER_CANDIDATE or
   DEMO_CANDIDATE must pass all eight DeploymentGatekeeper gates AND receive
   explicit human confirmation before paper trading begins.
3. **Post-incident** — If the kill switch is activated, the CRITICAL-level
   DecisionLog entry must be reviewed and resolved before the switch can be
   cleared.

---

## Summary

The AI brain is a decision-support tool, not a decision-making tool. It
is architecturally incapable of trading, enabling trading, or hiding
information. All consequential actions require a human to read a recommendation,
verify the evidence, and take deliberate action outside the AI brain.
