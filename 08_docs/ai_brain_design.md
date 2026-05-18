# AI Brain Design

**Status**: Planning  
**Core Mandate**: The AI brain monitors, recommends, and reports. It never trades autonomously.

---

## 1. Core Principle

The AI brain is an intelligent observer and advisor, not an actor. Every consequential action in the trading system — enabling a strategy, increasing size, disabling the kill switch, deploying a new strategy — requires explicit human approval. The AI brain can recommend, alert, summarise, and generate research, but it cannot execute.

This principle is not a temporary restriction. It is a permanent design constraint. An AI that can enable strategies or override risk controls without human approval is a liability, not an asset. The value of the AI brain comes from its analysis quality, not its autonomy.

---

## 2. Allowed Functions

The AI brain is permitted to perform the following without human approval:

### Monitoring
- Monitor system heartbeat and detect process failures
- Detect stale data (bars older than expected age for the session)
- Detect broker position mismatch vs internal state (read-only detection, not correction)
- Detect strategy performance drift vs backtest expectations
- Detect regime drift (current market conditions diverging from training regime)
- Detect unusual slippage patterns (actual fills worse than expected)
- Detect missing bars or data gaps during market hours

### Reporting
- Summarise daily P&L at market close
- Summarise per-strategy performance (fills, signals, P&L, skipped trades)
- Recommend pausing specific strategies with documented reasoning
- Generate research reports on candidate strategy ideas
- Create candidate strategy specification cards (hypothesis, entry logic, regime conditions, expected metrics)
- Produce daily EOD review documents
- Produce weekly performance review documents

### Alerting
- Send Telegram/Discord/email alerts for critical events (see Alert Bot section)
- Notify on risk threshold approaches
- Notify on data or connectivity issues

---

## 3. Forbidden Functions

The AI brain is **permanently prohibited** from performing the following, regardless of circumstances:

| Forbidden Action | Reason |
|----------------|--------|
| Increase position size or contract count | Size changes require human risk assessment |
| Enable a disabled strategy | Only the human can re-enable a halted strategy |
| Override RiskManager decisions | RiskManager is a circuit breaker, not a suggestion |
| Override the kill switch | Kill switch is a hard stop; AI cannot lift it |
| Override the strategy allowlist | Allowlist is a compliance control |
| Trade live without explicit approval | No autonomous live trading ever |
| Deploy a new strategy without full validation | Validation gates exist for this reason |
| Modify prop-firm compliance rules | Compliance rules are legal constraints, not config |
| Chase news events autonomously | News reactions require human judgment |
| Remove or modify loss limits | Loss limits are non-negotiable |
| Edit historical backtest results | Historical results are immutable records |
| Hide or suppress rejected strategies | All demotions must be visible in the record |
| Transition demo mode to live mode | Mode transitions require human action only |

If the AI brain's code ever has a pathway to perform any of the above, it is a bug.

---

## 4. Agent Architecture

The AI brain is composed of seven specialised agents. Each agent has a clearly bounded scope.

---

### Agent 1: Monitor Agent

**Role**: System health watchdog.

**Watches**:
- Heartbeat file age (alert if > 2x expected interval)
- Log files for ERROR or CRITICAL entries
- Data freshness per symbol (last bar timestamp vs current time)
- Process health (executor process running, no crash loops)
- State file consistency (last known state matches broker)

**Outputs**: Structured health status dict; alerts to Alert Bot on anomalies.

**Cannot**: Take any corrective action. Detect only, report only.

---

### Agent 2: Risk Sentinel

**Role**: Real-time risk monitoring with one limited intervention power.

**Watches**:
- Daily P&L vs daily loss limit (threshold: 80% consumed)
- Trailing drawdown vs drawdown limit
- Per-strategy halt conditions (circuit breakers triggered)
- Broker position mismatch vs internal state

**Can**:
- Issue a PAUSE recommendation for a specific strategy
- Trigger an alert when thresholds are breached
- Log risk events to `06_live_trading/logs/risk_events.jsonl`

**Cannot**:
- Increase risk limits
- Enable a strategy that the RiskManager has halted
- Override the RiskManager's halt decision
- Reduce margin requirements or drawdown thresholds

**Important**: Risk Sentinel can recommend pausing a strategy based on real-time data, but a strategy that has been halted by RiskManager or manually disabled cannot be re-enabled by Risk Sentinel. Human re-enable only.

---

### Agent 3: Research Scout

**Role**: Hypothesis generation and candidate strategy ideation.

**Does**:
- Monitors market structure reports, regime indicators, and performance data
- Generates candidate strategy hypothesis cards with: entry logic description, target regime, expected trade frequency, suggested validation approach, known risks
- Writes cards to `02_research/candidates/`
- Tags cards with source (AI-generated) and date

**Cannot**:
- Run backtests directly
- Deploy or enable any strategy
- Add strategies to the allowlist
- Modify any existing strategy code

---

### Agent 4: Validation Agent

**Role**: Run the full statistical validation suite against candidate and existing strategies.

**Does**:
- Runs backtests using the existing backtesting engine
- Runs WFO (Walk-Forward Optimisation)
- Computes DSR, PBO, CSCV metrics
- Runs regime slicing analysis
- Runs slippage stress tests
- Produces structured validation reports in `05_backtests/reports/`

**Cannot**:
- Move a strategy from `RESEARCH` to `SURVIVOR` status — that requires human review of the report
- Add a strategy to the allowlist
- Modify strategy parameters to make them pass (no p-hacking)

---

### Agent 5: Deployment Gatekeeper

**Role**: Gate-check before any strategy transitions to demo or live.

**Does**:
- Reads validation reports and checks all demo-eligibility gates (from `evidence_upgrade_plan.md`)
- Checks that strategy is on the allowlist
- Checks that evidence package is complete (DSR, PBO, regime slices, slippage tests)
- Checks that dry-run signal path has been validated
- Checks that broker bracket support is confirmed
- Produces a gate-check report: PASS / FAIL per gate, with failure reasons

**Cannot**:
- Grant demo eligibility directly — it only checks and reports
- Enable a strategy — human must review gate-check report and enable manually
- Skip or waive any gate

---

### Agent 6: Daily Report Agent

**Role**: End-of-day automated reporting.

**Produces at EOD** (configurable time, default 17:00 ET):
- Daily P&L summary (portfolio + per strategy)
- Signal log: all signals fired, fills received, trades skipped, rejection reasons
- Risk events: any threshold breaches, halts, alerts fired
- Execution quality: slippage vs expected, fill timing
- Error log summary: any ERROR/CRITICAL log entries
- System status: uptime, data quality, broker connection events

**Output format**: Markdown report + JSON data file, written to `06_live_trading/reports/daily/YYYYMMDD.md`

**Alert**: Sends daily summary to Alert Bot for Telegram/Discord distribution.

---

### Agent 7: Alert Bot

**Role**: Real-time notification delivery.

**Sends alerts for** (see full list in `monitoring_dashboard_plan.md`):
- Signal fired
- Trade skipped (with reason)
- Strategy halted by circuit breaker
- Drawdown threshold hit (> 80% consumed)
- Broker mismatch detected
- Data stale during market hours
- Kill switch triggered
- Daily summary ready
- Broker disconnected
- Unexpected position found

**Channels**: Telegram (primary), Discord webhook (secondary), email (tertiary for critical only).

**Design constraints**:
- One message per event
- No commands that can modify the system state (read-only alert delivery)
- Messages include: timestamp, event type, severity, relevant values, no action required by system

---

## 5. Decision Logging

All AI brain recommendations and any system actions must be logged.

### Log Format: `06_live_trading/logs/ai_decisions.jsonl`

Every entry is a JSON line:
```json
{
  "timestamp": "2026-05-18T14:32:00Z",
  "agent": "RiskSentinel",
  "action_type": "RECOMMENDATION",
  "action": "PAUSE_STRATEGY",
  "target": "strategy_1",
  "reason": "Daily loss limit 83% consumed. 3 consecutive losing trades in last 45 min.",
  "human_confirmation_required": true,
  "confirmation_status": "PENDING",
  "confirmed_by": null,
  "confirmed_at": null
}
```

### Human Confirmation Protocol

Any AI recommendation classified as `human_confirmation_required: true` must have an explicit approve/reject response logged before action is taken.

| Recommendation Type | Confirmation Required |
|--------------------|----------------------|
| PAUSE_STRATEGY | Yes — human confirms before strategy is disabled |
| RE_ENABLE_STRATEGY | Yes — always |
| INCREASE_SIZE | Forbidden — AI cannot make this recommendation |
| DEPLOY_NEW_STRATEGY | Yes — after gate-check report reviewed |
| ALERT_ONLY | No — informational, no action |

The AI can recommend pausing a strategy. The human must confirm before the strategy is re-enabled. This asymmetry is intentional: the AI can be cautious (recommend pausing), but it cannot be optimistic (re-enable without human review).

---

## 6. Implementation Sequence

Build in this order. Do not jump ahead.

```
Phase 1: Monitor Agent + Alert Bot
  Why first: before automating anything, you need to know when things break.
  Deliverables:
  - Heartbeat file monitoring
  - Log error detection
  - Data freshness checks
  - Telegram alert delivery for critical events

Phase 2: Risk Sentinel
  Why second: risk monitoring needs to work before demo trading begins.
  Deliverables:
  - Real-time daily P&L tracking
  - Drawdown threshold alerts
  - PAUSE recommendation with human confirmation gate
  - risk_events.jsonl logging

Phase 3: Daily Report Agent
  Why third: daily reporting creates accountability and a paper trail before adding strategy intelligence.
  Deliverables:
  - EOD report generation
  - Signal log summary
  - Daily Telegram summary message

Phase 4: Validation Agent
  Why fourth: only needed once candidate strategies exist and the backtest pipeline is stable.
  Deliverables:
  - Automated WFO + DSR + PBO runs
  - Regime slice reports
  - Slippage stress test reports

Phase 5: Research Scout + Deployment Gatekeeper
  Why last: these are forward-looking; build them after the core monitoring/reporting stack is proven.
  Deliverables:
  - Hypothesis card generation
  - Gate-check report automation
```
