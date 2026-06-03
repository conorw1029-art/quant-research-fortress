# AI Brain — Full System Design

## Design Philosophy: AI Advises, Never Trades

The AI brain is a read-and-recommend layer, not an execution layer. Every agent
in this module can observe, compute, and report — but no agent can place an
order, enable live trading, or override a human decision without explicit
approval. This is enforced at the code level, not just by convention.

The guiding principle: **a fast wrong decision by an AI is worse than a slow
correct decision by a human**. Trading capital is finite. The AI helps the
human move faster, not replace them.

---

## Agent Roster

### MonitorAgent (`monitor_agent.py`)
Watches three things on a recurring schedule:

1. **Bar freshness** — reads the newest timestamp from each symbol's parquet
   file and flags stale bars (1m threshold: 3 min; 5m: 10 min; 30m: 45 min).
2. **Signal log activity** — reads `signals_YYYYMMDD.jsonl`, measures time since
   last signal, raises a silence alert if > 30 minutes with no signals.
3. **Kill switch** — reads `KILL_SWITCH.txt`; if the file contains "STOP",
   logs a CRITICAL entry with `human_approval_required=True` and returns True.

MonitorAgent never sends orders. It alerts and logs.

### RiskSentinel (`risk_sentinel.py`)
Enforces real-time P&L and drawdown boundaries:

- **Daily P&L** — compares current day's P&L against `daily_limit`. Emits
  `CRITICAL` log entry and sets `human_approval_required=True` on breach.
- **Trailing drawdown** — compares `(peak_equity - current_equity)` against
  `dd_limit`. Same escalation on breach.
- **Correlation exposure** — detects when ≥2 open positions share a symbol
  family (e.g. GC + MGC are both `metals_gold`). Flags as `MEDIUM` risk.
- **Size gate** — `can_increase_size()` always returns `(False, reason)`.
  Position sizing is exclusively a human decision.
- **Pause gate** — `can_pause_strategy()` always returns `(True, reason)`.
  Pausing is considered a safe defensive action.

### ResearchScout (`research_scout.py`)
Navigates the strategy backlog:

- Loads `strategy_universe_exhaustive.json`, filters to `BACKLOG` entries,
  sorts by priority field.
- `suggest_next_tests()` filters candidates by data availability — only
  surfaces strategies whose `data_required` is fully satisfied locally.
- `generate_hypothesis_card()` formats a human-readable review card.
- `scan_evidence_reports()` aggregates completed `*_evidence_report.json`
  files sorted by walk-forward Sharpe.
- `cannot_deploy` property returns a reminder string — ResearchScout has
  no deployment capability.

### ValidationAgent (`validation_agent.py`)
Runs the evidence upgrade subprocess and interprets results:

- Calls `tick_evidence_upgrade.py` via `subprocess.run` with `-X utf8`.
- Parses stdout for pass/fail counts.
- `check_deployment_eligibility()` applies four hard gates:
  wf_sharpe > 1.5, bootstrap_p < 0.05, slippage_1tick_sharpe > 0,
  n_trades >= 200.
- `produce_validation_report()` writes a gate-by-gate JSON report for
  human review.

### DeploymentGatekeeper (`deployment_gatekeeper.py`)
The strictest guard before any deployment action:

- `check_eligibility()` applies **eight** hard gates (trade count, Sharpe,
  bootstrap, slippage, news filter, data years, bracket orders tested,
  state persistence verified). Any missing field is treated as a failure.
- `approve_for_paper()` logs with `human_approval_required=True`, prints a
  hard warning, and does **nothing** else. It cannot enable paper trading.
- `approve_for_live()` always raises `PermissionError`. This is unconditional.

### DailyReportAgent (`daily_report_agent.py`)
Reads `signals_YYYYMMDD.jsonl` and computes:

- Signals fired / blocked / errors
- Block reason breakdown
- Hypothetical P&L (from `hypo_outcome` fields) and win rate
- Active strategy list

Saves to `reports/daily_YYYYMMDD.json`. Formats a 20-line text summary
suitable for email or console.

### AlertBot (`alert_bot.py`)
Routes alert messages to Telegram, Discord, and/or console:

- Level-based routing: INFO → console; WARNING → console + Telegram;
  CRITICAL → all destinations.
- All network failures return `False` (never raise). The system must never
  crash because an alert failed to deliver.
- Reads credentials from environment variables if not injected at init.

### DataLibrarian (`data_librarian.py`)
Read-only inventory of local market data:

- `get_manifest()` reads `MANIFEST.jsonl` from the raw data directory.
- `check_coverage()` determines whether a required date range is satisfied
  by existing manifest records.
- `get_bar_summary()` scans bar parquet files for row counts and date ranges.
- `get_data_summary()` combines raw manifest and bars into one overview dict.

### StrategyLibrarian (`strategy_librarian.py`)
Single source of truth for strategy metadata and lifecycle state:

- Loads / saves `strategy_universe_exhaustive.json`.
- `get_by_status()` filters by any of the nine valid status values.
- `update_status()` transitions a strategy through the lifecycle,
  logging HUMAN_APPROVAL_REQUIRED for PAPER_CANDIDATE or DEMO_CANDIDATE moves.
- `get_deployment_candidates()` retrieves all paper/demo candidates for
  human review.
- `add_test_result()` appends to the strategy's `test_history` list.

### DecisionLog (`decision_log.py`)
Append-only JSONL audit log used by every other agent:

- Every observation, recommendation, and action is recorded with timestamp,
  agent name, risk level, and `human_approval_required` flag.
- Log format: `06_live_trading/ai_logs/ai_decisions_YYYYMMDD.jsonl`.
- `get_today_summary()` aggregates counts by agent and risk level.
- Entries are immutable once written. No agent may delete or modify them.

---

## Decision Log Format

Each log entry is a JSON object on a single line:

```json
{
  "timestamp":               "2026-06-03T14:22:01.123456+00:00",
  "agent":                   "RiskSentinel",
  "observation":             "Daily P&L at 87% of limit.",
  "recommendation":          "Monitor closely. Prepare to halt if limit reached.",
  "action_taken":            "daily_pnl_assessed",
  "human_approval_required": false,
  "risk_level":              "HIGH",
  "evidence_file":           null,
  "metadata":                {"current_pnl": -870.0, "daily_limit": 1000.0}
}
```

`human_approval_required: true` entries must be reviewed before any action
is taken on the associated recommendation.

---

## Allowed vs Forbidden Actions

| Action | Allowed | Agent |
|--------|---------|-------|
| Read bar data | Yes | MonitorAgent, DataLibrarian |
| Read signal logs | Yes | MonitorAgent, DailyReportAgent |
| Read kill switch | Yes | MonitorAgent |
| Send alerts | Yes | AlertBot |
| Recommend pausing a strategy | Yes | RiskSentinel |
| Run evidence upgrade subprocess | Yes | ValidationAgent |
| Read strategy universe | Yes | StrategyLibrarian, ResearchScout |
| Update strategy status | Yes (with logging) | StrategyLibrarian |
| Place orders | **Never** | — |
| Enable live trading | **Never** | — |
| Increase position size | **Never** | — |
| Override kill switch | **Never** | — |
| Hide losses in reports | **Never** | — |
| Deploy to paper without human | **Never** | DeploymentGatekeeper enforces |
| Deploy to live | **Never** | DeploymentGatekeeper raises PermissionError |

---

## Integration with the Manual Signal System

The live signal system writes `signals_YYYYMMDD.jsonl`. The AI brain reads
this file but never writes to it. Signals are generated by rule-based strategy
code — the AI brain cannot inject, suppress, or modify signals.

The kill switch (`KILL_SWITCH.txt`) is checked by MonitorAgent and reported.
The actual halt mechanism is in the signal generation layer — the AI brain
detects and reports the switch state, it does not operate it.

---

## Future Roadmap

1. **Automated daily report delivery** — schedule DailyReportAgent + AlertBot
   to email a formatted summary each evening.
2. **Walk-forward monitoring** — extend ValidationAgent to run rolling
   out-of-sample checks on live strategy signals.
3. **Strategy correlation monitor** — add a module that tracks live signal
   correlation across running strategies and flags drift.
4. **Paper trade reconciliation** — compare hypothetical signal outcomes to
   actual fill data; flag slippage discrepancies to RiskSentinel.
5. **Retest trigger** — ResearchScout watches for `RETEST_WITH_MORE_DATA`
   strategies and surfaces them when new data crosses a threshold.
