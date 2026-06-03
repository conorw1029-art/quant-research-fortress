# Manual to Automation Transition Plan
**Generated:** 2026-06-03  
**Authority:** This is the definitive staged plan for transitioning from the current dry-run state to fully automated personal broker trading. Each stage has explicit quantitative gate criteria. No stage may be skipped.

---

## Guiding Principle

The path from backtest to automation is a one-way ratchet: you earn the right to proceed by demonstrating real-world evidence at each stage, not by confidence in the backtest. The backtest is a necessary but not sufficient condition for live trading. Every stage produces evidence that either confirms the edge exists in the real world or reveals that it does not — and both outcomes are valuable.

The single greatest risk in automated trading is moving too fast. A system that works for six months before failing catastrophically is worse than a system that fails small and fast, because the slow failure encourages overconfidence and scale-up. Every gate exists to force a pause and look honestly at what the data says.

---

## System Classification at Each Stage

| Stage | Classification | Orders? | Capital at Risk? |
|-------|---------------|:-------:|:----------------:|
| Stage 1 | RESEARCH | No | No |
| Stage 2 | DATA_READY / DRY_RUN | No | No |
| Stage 3 | MANUAL_SIGNAL | Manual only | Personal choice |
| Stage 4 | SIM_AUTO | Simulated | No |
| Stage 5 | LIVE_TINY | Automated | YES — real money |
| Stage 6 | LIVE_PORTFOLIO | Automated | YES — real money |
| Stage 7 | AI_ASSISTED | AI monitors | YES — real money |

---

## Stage 1 — Historical Evidence (Current State, Partly Done)

**Status as of 2026-06-03:** Substantially complete for GC and SI. Incomplete for ES/NQ.

### What This Stage Produces

Historical evidence is the foundation. Without it, no subsequent stage has meaning. The evidence produced here is what Stage 3 manual trading and Stage 4 simulation will be measured against.

### Completed Components

**Multi-year data with L2 features:**
- GC: 77,270 bars at 1m resolution (2020–2026), 18 L2 columns including CVD, OFI, book pressure, bid/ask depth, spread
- SI: 37,920 bars at 1m resolution (2020–2026), same 18 columns
- ES/NQ: 5.5 months only (December 2025–May 2026), 13 columns (no spread or depth features)

**Evidence tests completed:**

*OHLCV Strategies (Batches 1–13):*
- Walk-forward optimisation with IS/OOS splits
- Deflated Sharpe Ratio (DSR) accounting for multiple trials
- Step 2 stress suite: 2x cost, 20% missed fills, Topstep daily regime
- Monte Carlo on final portfolio: P(drawdown breach) = 0.15%
- 9 survivors, 12/12 positive years 2014–2025, portfolio DSR +14.64

*L2 Strategies (GC 48/48 pass, SI 16/16 pass):*
- Full 5-year walk-forward on GC and SI (2020–2026)
- Bootstrap p-values for each surviving strategy
- Slippage ladder: 1-tick, 2-tick, 3-tick Sharpe computed for all survivors
- Corrected DSR formula (Cornish-Fisher sign flip fixed 2026-06-02)
- Commission included in cost model (bug fixed 2026-06-02)
- Key finding: SI CVD_Microprice (mp_ticks=1.0) survives 3-tick slippage — most resilient strategy found
- Key finding: Sweep_Absorption_Reversal GC is a recency artifact — not in 5-year hardened set

**Portfolio correlation matrix:**
- OHLCV survivors: max intra-family correlation 0.488 (vol_adj_momentum vs donchian_intraday, both GC); all other pairs below 0.22
- Cross-instrument (GC vs SI): near-zero correlation even for same strategy family (vol_adj_momentum GC vs SI correlation = 0.037)
- L2 survivors: not yet portfolio-backtested — pending

**Deployment eligibility review:**
- `08_docs/strategy_deployment_eligibility.md` — 15 V1–V5 strategies classified across 5 tiers
- `04_codebase/live_strategy_allowlist.yaml` — 38 strategies, 1 DEMO_CANDIDATE, 4 ENABLED_DRY_RUN, 17 REVIEW_REQUIRED, 16 DISABLED_FOR_LIVE

### Remaining Stage 1 Work

| Item | Priority | Notes |
|------|:--------:|-------|
| Fix Depth_Imbalance_Momentum position-limit bug | HIGH | 10k-30k trades/6yr = overlapping positions; re-run with 1-contract enforcement |
| ES/NQ 5-year historical data (Databento) | HIGH | Requires explicit budget approval ~$50-100; remaining budget ~$5 |
| Portfolio backtest combining OHLCV + L2 survivors | MEDIUM | Select top 3-5 L2 strategies; compute combined DSR and max DD |
| News-filtered backtest | MEDIUM | Re-run key GC/SI strategies excluding trades in news windows; confirm edge holds |
| PBO (Probability of Backtest Overfitting) tests | MEDIUM | Bailey CSCV framework; post-data-expansion task |
| Test 7 new L2 strategy hypotheses from backlog | LOW | cvd_divergence_vwap, ofi_multi_level_confirmation, sweep_no_replenishment_continuation etc. |

### Stage 1 Exit Gate

Stage 1 is considered complete when ALL of the following are true:

- [ ] GC 5-year evidence: 48/48 pass (DONE)
- [ ] SI 5-year evidence: 16/16 pass (DONE)
- [ ] Depth_Imbalance_Momentum GC position-limit bug fixed and strategy re-evaluated
- [ ] At least one viable combination of OHLCV + L2 strategies has a computed portfolio DSR > 5.0 and max drawdown < $12,000
- [ ] ES/NQ either: (a) have 2+ years of data and pass regime-robust evidence tests, OR (b) are explicitly excluded from deployment planning on current 5-month data
- [ ] All deployment-eligible strategies have: trade count ≥ 100, DSR > 0.5, 1-tick Sharpe > 1.0, worst-day micro < $1,000

**Stage 1 is not a blocker for Stages 2 or 3** — they can run in parallel. Stage 1 work runs continuously alongside infrastructure and execution work.

---

## Stage 2 — Live Data Dry-Run

**Status as of 2026-06-03:** BLOCKED — no Tradovate credentials, bar builder not connected to live feed.

### What This Stage Tests

Stage 2 answers: does the signal code produce the same signals on live bars as it produces on historical bars, and does the live data feed operate reliably enough to support a trading system?

It does not test execution. It does not test fill quality. It does not place orders. It is purely a data and signal-path validation.

### Prerequisites

- Tradovate demo/paper account created (or alternative broker bar feed configured)
- Tradovate API credentials available: TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SECRET
- Set in `.env` (never in code): TRADOVATE_ENABLED=true, TRADOVATE_ENV=demo

### Components Required

**Databento live bar feed (primary option):**
- `tick_bar_builder_databento.py` — connects to Databento live feed using existing API key
- Outputs bars to parquet in same format as historical data
- This does not require Tradovate credentials

**Tradovate bar builder (alternative):**
- `tick_bar_builder.py --rest` — REST polling mode against Tradovate MD endpoint
- `tick_bar_builder.py` — WebSocket mode (primary, preferred for lower latency)
- Requires Tradovate credentials

**Signal generation (already works in dry-run):**
- `tick_live_executor.py` (no flags) — dry-run mode, signals logged to JSONL, no orders
- News monitor active: `tick_news_monitor.py` called at each poll cycle
- Stale-bar detection must be active: executor blocks entries if newest bar is > 5 minutes old during market hours

**Monitoring:**
- `tick_startup_checklist.py --quick` — before each session
- `tick_signal_log_reader.py --days 1` — after each session

### What to Verify

For each RTH session run:
1. Bar builder produces bars continuously during market hours with no gaps > 5 minutes
2. Bars appear in parquet with correct timestamps and all required columns
3. CVD continuity seeds correctly from the last historical bar on session start
4. Signal log shows alerts at times consistent with backtest expectations (similar hour distribution)
5. No duplicate signals for the same bar timestamp
6. No uncaught exceptions in any component over a full 6.5-hour RTH session
7. Data freshness check in executor correctly triggers STALE_DATA when bar builder is paused

### Stale-Bar Detection Specification

This feature must be built before Stage 2 begins. Specification:

- Executor tracks timestamp of newest bar for each symbol at each poll cycle
- If newest bar age > `STALE_BAR_THRESHOLD_MINUTES` (default: 5) during RTH hours (08:00–16:00 CT), log STALE_DATA warning and block all new entries
- RTH hours check uses a configurable session calendar; do not block during overnight hours when bars are naturally sparse
- Resume entries automatically once fresh bars arrive (no manual reset required)
- Write stale-bar events to `06_live_trading/logs/data_quality.jsonl`

### Stage 2 Gate Criteria

| Criterion | Threshold | Measurement |
|-----------|:---------:|-------------|
| Bar builder sessions without crash | Minimum 3 full RTH sessions | Manual inspection of process logs |
| CVD continuity after restart | No discontinuity jump > 2x typical bar CVD | Check first 5 bars after restart vs prior session end |
| Dry-run signals fire | At least 1 signal from eligible GC/SI strategies per session | `tick_signal_log_reader.py --days 3` |
| Signal hour distribution match | Within ±30% of backtest expectations | Compare signal hour histogram vs backtest timing |
| No duplicate signals | Zero duplicate (same symbol, same bar timestamp) | JSONL inspection |
| Stale-bar detection works | Correctly blocks entries when bar builder paused for 10 minutes | Manual test: pause bar builder, confirm no entries |
| Kill switch tested | Halt within one poll cycle | Write STOP to KILL_SWITCH.txt during running session |
| No uncaught exceptions | Zero ERROR or CRITICAL log entries | `scripts/check_fortress_status.ps1` output |

**Stage 2 must pass before Stage 3 manual trading produces any broker fills.** It can overlap with Stage 1 research work.

---

## Stage 3 — Manual Signal Trading (First Real-World Validation)

**Status as of 2026-06-03:** NOT STARTED — requires Stage 2 to pass first.

### What This Stage Tests

Stage 3 answers the most important question in the entire transition: does the signal quality hold up under real market conditions when a human is trying to execute it?

Backtests assume you can always fill at the signal price. Reality disagrees. The market moves between the time a signal fires and the time a human (or system) gets an order to the exchange. Stage 3 quantifies this gap for the first time using real executions.

This stage requires no broker automation. The human looks at a signal alert and decides whether to manually place the trade. The system generates the alert; the human executes.

### Setup

**Alert delivery (pick one or both):**
- Console: executor prints formatted alert line to terminal — `[14:32:15 CT] LONG SIL | Strategy: CVD_Microprice | Entry: 23.41 | Stop: 23.32 | Target: 23.59 | R: $48 | Note: CVD above threshold, microprice confirming`
- JSONL: signals logged to `06_live_trading/logs/signals_YYYYMMDD.jsonl` for review after session
- Optional Telegram: Alert Bot (Agent 7 from AI brain design) pushes to Telegram channel — useful for monitoring from phone when away from desk

**Execution tracking spreadsheet** (create `06_live_trading/logs/manual_fills.csv`):

```
date, strategy_id, symbol, direction, alert_time, alert_entry_px, alert_stop_px, alert_target_px,
traded (Y/N), reason_not_traded, fill_entry_px, fill_entry_time, slippage_ticks,
exit_px, exit_time, exit_type (stop/target/manual), pnl_dollars, notes
```

**Eligible strategies for manual signals (must have 5-year evidence):**
- GC: CVD_VWAP (best variant), Repeated_Replenishment (best variant)
- SI: CVD_Microprice (cvd=60, mp=1.0), Sweep_Continuation (hold=5)
- OHLCV: vwap_reclaim_gc, vwap_reclaim_si, bollinger_rsi_gc, vol_adj_momentum_gc
- OHLCV event-driven: fomc_drift (act on FOMC dates only)

**Not eligible for manual trading (evidence insufficient):**
- Any ES strategy
- Any NQ strategy
- GC Sweep_Absorption_Reversal (recency artifact)
- GC Depth_Imbalance_Momentum (position inflation bug)

### Human Decision Protocol

When a signal fires, the human asks four questions before placing the trade:

1. Is the market in an active session (RTH hours, normal liquidity)? If overnight and liquidity is thin, skip.
2. Is there a scheduled high-impact news event in the next 30 minutes? If yes, skip — the news monitor should have already blocked it, but human confirms.
3. Is the spread currently abnormal (> 2x typical)? If yes, skip — wider spread means the fill assumption is wrong.
4. Does this signal make contextual sense given current market behaviour? (Optional gut check — log the reason either way.)

If all four pass, place the trade manually at market or limit at the signal entry price, with the bracket stop and target at the signal's specified levels.

### Minimum Run Period

**30 trading days before advancing to Stage 4.** This is a minimum. If 30 days produces fewer than 20 total trades across all eligible strategies, extend Stage 3 until 20 fills are logged.

The 30-day minimum exists because a smaller sample cannot distinguish luck from skill. At 20 trades, the confidence interval on the observed Sharpe still spans a wide range — but 20 trades at least gives a directional signal.

### Stage 3 Gate Criteria

All gate criteria are measured from the `manual_fills.csv` log.

| Criterion | Pass Threshold | Fail Threshold | Action on Fail |
|-----------|:--------------:|:--------------:|----------------|
| Minimum trading days | 30 days | < 30 days | Extend Stage 3 |
| Minimum fills logged | 20 fills | < 20 fills | Extend Stage 3 |
| Average fill slippage | ≤ 2 ticks vs alert entry price | > 2 ticks average | Investigate execution method; may need limit orders only |
| Worst single-fill slippage | ≤ 4 ticks | > 6 ticks on any fill | Investigate that specific fill; flag the strategy |
| Signal frequency vs backtest | Within ±30% of expected rate | < 50% of expected rate | Strategy may not be firing in current regime |
| Alerts during news windows | Zero alerts should fire during blocked news windows | Any alert fires during news window | Bug in news monitor; fix before advancing |
| Kill switch confirmed | Kill switch test passed in Stage 2 | Not tested | Do Stage 2 kill-switch test before this check |
| No system crashes during Stage 3 | Zero crashes | Any crash | Investigate crash; add error handling; restart Stage 3 timer |

**On passing Stage 3 Gate:**
- Document: average slippage per strategy, worst fill, signal frequency observed vs expected, win rate and expectancy on the 20+ fills (directional, not definitive)
- Write this to `08_docs/stage3_manual_trading_report_YYYYMMDD.md`
- Decision: proceed to Stage 4 if gate passes, or extend Stage 3 if slippage is above threshold but fixable

---

## Stage 4 — Personal Broker Simulator

**Status as of 2026-06-03:** NOT STARTED — requires Stage 3 gate pass and demo/sim account creation.

### What This Stage Tests

Stage 4 answers: does the automation infrastructure work correctly when connected to a real broker interface, even if that interface is simulated? The execution quality, bracket order attachment, reconciliation logic, and state persistence are all tested here under conditions as close to live as possible — but without real capital at risk.

### Prerequisites

- Stage 3 gate passed (30+ days, slippage within 2 ticks)
- Personal broker simulator account created (Tradovate paper trading, NinjaTrader simulator, or equivalent)
- Tradovate demo credentials set in `.env` (TRADOVATE_ENV=demo)
- OSO bracket order exchange-verified on demo account: all three legs confirmed in broker's order system
- `_OSO_EXCHANGE_VERIFIED = True` flag set after human confirmation

### Configuration

- One strategy only: select the highest-quality 5-year survivor with the lowest worst-day micro loss from Stage 1 results. Recommended: SI CVD_Microprice (cvd=60, mp=1.0) — 3-tick slippage survivor, worst-day micro within acceptable range
- One micro contract (SIL for Silver, MGC for Gold)
- MAX_CONTRACTS_PER_TRADE = 1 enforced in `tick_live_executor.py`
- CoordinatorConfig: `one_strategy_only_demo=True`, `demo_strategy_key=<selected_strategy>`, `max_total_open_symbols=1`, `max_net_contracts_per_symbol=1`, `allow_reversal=False`

### Execution Safety Checks Before Stage 4 Start

Run this checklist before starting simulator auto-trade:

1. `tick_credentials_preflight.py` — all 5 gates pass: auth, account lookup, contract lookup, quotes, positions
2. `tick_credentials_test.py --test-order` — bracket order test: far-below-market order placed and immediately cancelled on demo account
3. `tick_startup_checklist.py --quick` — 0 FAIL items
4. Manual kill switch test: write STOP to KILL_SWITCH.txt with executor in demo mode and at least one simulated position open; confirm position closes within one poll cycle
5. Reconciliation startup test: manually modify `06_live_trading/state/positions.json` to add a ghost position; confirm executor detects mismatch on startup and halts entries

### Daily Operation Protocol

```
# Step 1: Credential pre-flight (run before market open)
venv_new\Scripts\python.exe -X utf8 04_codebase\tick_credentials_preflight.py

# Step 2: Start bar builder (RTH only or 24/6 for metals)
venv_new\Scripts\python.exe -X utf8 04_codebase\tick_bar_builder.py --rest

# Step 3: Start executor in demo mode (one strategy only)
venv_new\Scripts\python.exe -X utf8 04_codebase\tick_live_executor.py --demo-auto-trade --poll 60

# Step 4: After session — review fills
venv_new\Scripts\python.exe -X utf8 04_codebase\tick_signal_log_reader.py --days 1 --trades

# Step 5: Check reconciliation log
type 06_live_trading\logs\broker_reconciliation_log.jsonl | python -m json.tool
```

### What to Monitor Every Day

- Fill quality: compare `entry_px` in signal log vs signal's intended entry price
- Bracket attachment: confirm stop and target legs appear in broker's order management UI
- Reconciliation: no CRITICAL events in `broker_reconciliation_log.jsonl`
- State file integrity: `06_live_trading/state/positions.json` matches broker's reported position
- Slippage distribution: log each fill slippage in a daily summary

### Stage 4 Gate Criteria

| Criterion | Pass Threshold | Fail Threshold | Action on Fail |
|-----------|:--------------:|:--------------:|----------------|
| Minimum trading days | 30 days | < 30 days | Extend Stage 4 |
| Minimum completed bracket orders | 10 complete round trips (entry + stop or target fill) | < 10 fills in 30 days | Extend Stage 4 |
| System crashes | Zero | Any crash | Fix and restart 30-day timer |
| CRITICAL reconciliation events | Zero | Any CRITICAL event | Halt, investigate, fix, restart 30-day timer |
| Unexplained positions at broker | Zero | Any unexplained position | Halt, investigate, fix |
| Kill switch end-to-end | Confirmed working | Not confirmed | Test before advancing |
| Average fill slippage | ≤ 2 ticks vs signal entry | > 2 ticks average | Investigate order routing; adjust limit order logic |
| Worst single fill | ≤ 4 ticks | > 6 ticks | Flag the event; document cause (news spike, illiquid hour) |
| Partial-TP disabled | TP = full exit at +3R only | Any partial TP attempt logged | Fix code before advancing |
| State persistence across restart | Position recognised after deliberate restart | Not recognised | Fix StateManager integration before advancing |

**On passing Stage 4 Gate:**
- Write `08_docs/stage4_simulator_report_YYYYMMDD.md`: fill count, average slippage per strategy, worst fill, reconciliation event log (should be empty), crash log (should be empty), bracket order attachment confirmation
- Decision: proceed to Stage 5 if all criteria pass

---

## Stage 5 — Personal Broker Tiny Live

**Status as of 2026-06-03:** NOT STARTED — requires Stage 4 gate pass.

### What This Stage Tests

Stage 5 is the first time real personal capital is at risk. The objectives are: confirm that execution quality observed in simulation holds with real money, confirm that the system handles the psychological and operational differences of live trading, and confirm that slippage and fill quality in live markets matches the simulator.

**This stage is NOT about making money.** It is about proving the system is reliable. P&L is a data point, not the goal.

### Prerequisites

- Stage 4 gate fully passed
- Stage 4 report reviewed and signed off with date
- Personal broker account with live capital funded (minimum funding sufficient for margin on 1 micro contract + 2x expected max drawdown from Stage 1 analysis)
- All execution safety features confirmed working: brackets, reconciliation, stale-bar detection, kill switch, state persistence

### Configuration (Non-Negotiable Constraints)

| Parameter | Value | Override? |
|-----------|:-----:|:---------:|
| Strategies active | 1 only | No |
| Contracts per signal | 1 micro | No |
| Maximum dollar risk per trade | $200 | No — enforced in code |
| Maximum daily strategy loss | $250 | No — enforced in RiskManager |
| Maximum portfolio daily loss | $600 | No — enforced in RiskManager |
| Account trailing drawdown halt | $800 | No — enforced in RiskManager |
| Scaling start | Not before Day 61 | No |
| Weekend position | Flat by Friday 21:45 UTC | No |
| Live environment variable | FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND | Must be set manually each session |

### Starting Stage 5 Safely

Before the first live order is placed, complete this checklist in writing:

1. All Stage 4 criteria met — confirmed with date
2. Stage 4 simulator report reviewed and signed — confirmed with date
3. Personal broker account funded and verified — confirmed with account number
4. Kill switch location confirmed: `C:\Users\conor\Desktop\quant-research\KILL_SWITCH.txt`
5. Manual kill procedure confirmed: write "STOP" to KILL_SWITCH.txt; system halts and flattens within one poll cycle
6. Emergency manual procedure documented: if system crash with open position, log into broker manually and close position at market
7. Slippage expectation documented: based on Stage 3 and Stage 4 results, expected average slippage is N ticks; if live slippage exceeds 2N ticks consistently, halt and investigate

### Stage 5 Gate Criteria

All criteria measured from live trading logs over the first 60 days.

| Criterion | Pass Threshold | Fail Threshold | Action on Fail |
|-----------|:--------------:|:--------------:|----------------|
| Minimum live days | 60 days | < 60 days | Extend Stage 5 |
| System crashes | Zero | Any crash | Fix and extend Stage 5 by 30 days |
| CRITICAL reconciliation events | Zero | Any CRITICAL event | Halt live trading; investigate; resolve; extend by 30 days |
| Kill switch confirmed | Tested at least once during Stage 5 | Not tested | Test before advancing |
| Average live fill slippage | ≤ 2 ticks vs backtest assumption | > 2 ticks average | Investigate; do not advance to Stage 6 until resolved |
| Cumulative P&L | Positive or within 1x max expected drawdown of zero | More than 2x max expected drawdown negative | Halt and review strategy viability |
| No scaling violations | Contract count stays at 1 throughout | Any increase to 2+ contracts | Halt scaling; this is a hard rule violation |
| Partial-TP enforcement | No partial-TP attempts | Any partial-TP attempt | Fix immediately |

**On passing Stage 5 Gate:**
- Write `08_docs/stage5_live_tiny_report_YYYYMMDD.md`
- Include: 60-day P&L curve, slippage distribution, fill count, win rate vs backtest, worst single day, maximum drawdown experienced, comparison to Stage 1 backtest expectations
- Explicit decision: proceed to Stage 6 (add second strategy), continue Stage 5 (need more data), or halt (system not performing as expected)

---

## Stage 6 — Small Automated Portfolio

**Status as of 2026-06-03:** NOT STARTED — requires Stage 5 gate pass.

### What This Stage Tests

Stage 6 adds a second strategy to the live account and tests whether the portfolio coordinator correctly prevents conflicts, whether the combined drawdown stays within acceptable bounds, and whether execution quality holds with two active signal streams.

**The second strategy must be different from the first in both instrument and signal source.** Running two correlated strategies in a live account provides little diversification while doubling operational complexity.

### Strategy Selection for Stage 6

The second strategy is chosen after Stage 5 completes based on actual live evidence from Stage 3 and Stage 4. The selection criteria:

1. Different instrument from Strategy 1 (if Strategy 1 is SI, Strategy 2 should be GC or OHLCV survivor)
2. Different signal source (if Strategy 1 is L2 CVD, Strategy 2 should be OHLCV or a different signal family)
3. Strategy must have passed Stage 1 evidence criteria (5-year walk-forward, DSR > 0.5, 1-tick Sharpe > 1.0, worst-day micro < $1,000)
4. Strategy must have been running in dry-run mode throughout Stages 3–5 with valid signal logs

### Portfolio Coordinator Configuration

When adding Strategy 2, update `CoordinatorConfig`:
- `one_strategy_only_demo = False` (no longer in Stage 4 isolation)
- `max_total_open_symbols = 2`
- `allow_opposite_strategy_signals_same_symbol = False` (hard rule)
- `max_net_contracts_per_symbol = 1` (still 1 contract per symbol)

### Stage 6 Gate Criteria

| Criterion | Pass Threshold | Fail Threshold |
|-----------|:--------------:|:--------------:|
| Zero same-symbol conflicts | No CoordinatorAction.REJECT_CONFLICT events in 60 days | Any unresolved conflict | Investigate coordinator logic |
| No combined drawdown breach | Combined portfolio drawdown stays < $2,000 (personal personal max DD) | Any breach | Halt Stage 6; reduce to 1 strategy |
| Both strategies generating valid signals | Both show signals at expected frequency | Either strategy shows < 25% of expected signal rate | Investigate data or strategy in dry-run |
| Slippage for both strategies | ≤ 2 ticks average for each strategy independently | > 2 ticks for either strategy | Investigate that strategy's execution |
| No system crashes in 30 days | Zero crashes | Any crash | Fix and restart 30-day clock |
| Manual sign-off after 30 days | Explicit dated written confirmation to proceed | Not completed | Do not advance without this |

---

## Stage 7 — AI-Assisted Operations

**Status as of 2026-06-03:** NOT STARTED — requires Stage 6 gate pass and AI agent implementation.

### What This Stage Adds

Stage 7 activates AI monitoring and reporting agents alongside the live trading system. The AI brain can observe, report, and recommend — but it cannot trade, enable strategies, increase size, or override risk controls. These constraints are permanent.

### Agents to Activate in Stage 7

**Monitor Agent (Agent 1) — First priority:**
- Watches heartbeat file age, log error entries, data freshness per symbol, process health
- Writes `06_live_trading/state/status.json` on every executor pass
- Triggers Telegram alert on anomalies: heartbeat stale > 2x expected interval, ERROR or CRITICAL log entry, data stale during RTH, process not running
- Cannot take corrective action — detect and report only

**Risk Sentinel (Agent 2) — Second priority:**
- Monitors daily PnL vs daily limit: triggers PAUSE recommendation at 80% consumed
- Monitors trailing drawdown vs limit: triggers alert at 80% consumed
- Monitors per-strategy circuit breakers
- Can: issue PAUSE recommendation (human must confirm), log risk events to `risk_events.jsonl`
- Cannot: increase risk limits, re-enable halted strategies, override RiskManager

**Daily Report Agent (Agent 6) — Third priority:**
- Produces end-of-day report at configurable time (default: 17:00 CT)
- Content: daily PnL per strategy, signal log summary, risk events, execution quality, error summary, system uptime
- Writes Markdown report to `06_live_trading/reports/daily/YYYYMMDD.md`
- Sends daily summary to Telegram

**Alert Bot (Agent 7) — Implemented alongside Monitor Agent:**
- Telegram push for: signal fired, trade skipped with reason, strategy halted, drawdown threshold hit, broker mismatch, data stale, kill switch triggered, daily summary, broker disconnected, unexpected position
- Read-only alerts only — no commands that modify system state

### What the AI Brain Permanently Cannot Do

The following actions are forbidden regardless of any instruction, configuration, or "emergency" situation:

- Increase position size or contract count
- Enable a disabled or halted strategy
- Override RiskManager decisions
- Override the kill switch
- Override the strategy allowlist
- Trade live without explicit human approval
- Deploy a new strategy without validation
- Modify compliance or risk rules
- Chase news events autonomously
- Remove or modify loss limits
- Edit historical backtest results
- Hide or suppress rejected strategies
- Transition demo mode to live mode

If any AI agent code has a pathway to any of the above, it is a bug. Treat it as a security issue, not a feature.

### AI Deployment Gate Criteria

| Criterion | Pass Threshold |
|-----------|:--------------:|
| Monitor Agent running without crashes | 7 consecutive days |
| Alert Bot delivering Telegram messages | Test message confirmed received |
| Risk Sentinel PAUSE recommendations require human confirmation | Confirmed in code: `human_confirmation_required: true` in all risk events |
| AI cannot re-enable halted strategies | Confirmed: no code path from any agent to allowlist modification |
| Daily reports generated accurately | Reports match manual inspection of signal logs for 5 consecutive days |
| AI decisions logged | All recommendations logged to `06_live_trading/logs/ai_decisions.jsonl` |

### Stage 7 Ongoing Operations

Once Stage 7 is active, the standard operating cadence is:

**Daily:**
- Review daily report (Telegram notification + Markdown file)
- Review any AI risk recommendations: confirm or reject each with dated written record
- Review signal log for anomalies: unexpected signal frequency, fills in unusual hours, slippage outliers

**Weekly:**
- Review per-strategy performance: is each strategy within ±30% degradation from backtest expectations?
- Review drawdown trajectory: is the combined portfolio drawdown within expected parameters?
- Review AI decision log: were all recommendations appropriate? Any false positives (strategy paused unnecessarily)?

**Monthly:**
- Full strategy performance review: compare live metrics to Stage 1 backtest over the same calendar period
- Portfolio correlation check: are correlations between strategies creeping up (strategies converging)?
- Regime assessment: has the market regime shifted from the training regime?
- Decision: maintain current portfolio, add strategy (must pass full Stage 6 process), or reduce (no gate required to reduce — can always scale down)

**Human required for all deployment decisions.** The AI brain produces reports and recommendations. A human reviews them and makes decisions. No automated escalation path exists.

---

## Transition Summary Table

| Stage | Name | Status | Capital | Minimum Duration | Next Blocker |
|-------|------|--------|:-------:|:----------------:|--------------|
| 1 | Historical Evidence | Partly done | None | Ongoing | Fix Depth_Imbalance bug; get ES/NQ data |
| 2 | Live Data Dry-Run | BLOCKED | None | 3+ RTH sessions | Tradovate credentials + stale-bar detection |
| 3 | Manual Signal Trading | Not started | Personal choice | 30 trading days | Stage 2 pass |
| 4 | Personal Broker Simulator | Not started | None | 30 trading days | Stage 3 pass + demo account |
| 5 | Personal Broker Tiny Live | Not started | Real money | 60 days | Stage 4 pass + live account |
| 6 | Small Automated Portfolio | Not started | Real money | 30 days | Stage 5 pass |
| 7 | AI-Assisted Operations | Not started | Real money | Ongoing | Stage 6 pass + AI agent build |

---

*Update this document when a stage gate changes state. The status column in the Transition Summary Table must always reflect the current date's actual situation, not aspirational status.*
