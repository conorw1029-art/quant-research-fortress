# Master Fortress Roadmap — Personal Broker Target
**Generated:** 2026-06-03  
**Authority:** This document supersedes all informal "next step" notes and prior roadmap fragments. Update it whenever a gate changes state or a major research result arrives.

---

## Classification at Top

| Decision | Status |
|----------|--------|
| Continue research | **YES** |
| Build manual signal system | **YES** |
| Build personal-broker automation architecture | **YES** |
| Use prop firm automation | **NO** |
| Touch live money now | **NO** |

---

## 1. Current State

**Date assessed:** 2026-06-03

The Fortress is a quantitative futures trading research system operating entirely on a Windows 10 desktop at `C:\Users\conor\Desktop\quant-research\`. The system has completed its full research-and-stress-test phase across OHLCV strategies (Batches 1–13), a first-generation L2 tick microstructure campaign (V1–V8 covering 720+ combinations across ES, NQ, GC, SI), and a second-generation deep L2 evidence upgrade producing 48 GC and 16 SI survivors with full 5-year walk-forward validation.

The system has never placed a real order. No demo account exists. The broker client library exists and has been mock-tested but is not connected. The execution layer is at the "dry-run signals verified, mock broker tests passing, broker connection still blocked" state.

**System classification as of today:**
- DATA_READY
- BROKER_MOCK_ONLY
- DRY_RUN_READY
- NOT_DEMO_READY
- NOT_LIVE_READY

---

## 2. What Is Genuinely Built

The following items exist as running code with passing tests.

### Research Pipeline
- `04_codebase/run_strategy.py` — OHLCV walk-forward optimisation (WFO) engine with DSR, PBO scaffold, Go/No-Go evaluator, and cost model builder
- `04_codebase/src/backtesting/` — Metrics engine: DSR, profit factor, Calmar, max drawdown, annual returns
- `04_codebase/tick_backtest_engine.py` — L2 bar backtest engine with ATR-based stops, slippage parameter, commission model ($6 round trip), and causal signal computation (no look-ahead detected)
- `04_codebase/tick_evidence_upgrade.py` — 5-year walk-forward + bootstrap + slippage ladder (DSR and Cornish-Fisher bugs fixed 2026-06-02)
- `04_codebase/tick_l2_backtest.py` — L2 strategy battery runner (commission bug fixed 2026-06-02)
- `04_codebase/tick_deep_analysis.py` — Regime slicing, hour-of-day analysis, param perturbation, Monte Carlo worst-day analysis
- Zoo database: `05_backtests/zoo.jsonl` (1,096 records, 18 PASS across OHLCV batches)
- L2 result files: `05_backtests/l2_results/GC_passed_evidence.json` (48 strategies), `SI_passed_evidence.json` (16 strategies)

### Strategy Files
- `04_codebase/tick_strategies.py` through `tick_strategies_v8.py` — 36+ L2 strategy implementations across V1–V8
- `04_codebase/src/zoo/` — OHLCV strategy registry with all 9 confirmed OHLCV survivors
- `04_codebase/src/strategies/` — Individual OHLCV strategy modules

### Risk Management
- `04_codebase/src/risk/risk_manager.py` — Full RiskManager: daily loss limits, trailing drawdown, consecutive-loss circuit breaker, per-strategy halts, account-level halt, ratchet trailing stop
- `04_codebase/src/risk/account_state.py` — Account state tracker: equity, daily PnL, drawdown state
- `04_codebase/src/risk/position_sizer.py` — ATR-based position sizer
- `04_codebase/src/risk/risk_config.py` — Config dataclass with all thresholds
- `04_codebase/tick_risk_manager.py` — Live executor risk manager (per-trade $200 cap, $250/strategy/day, $600 portfolio/day, $800 trailing DD on micros)

### Execution Infrastructure (Mock-Verified)
- `04_codebase/tick_live_executor.py` — 38-strategy signal engine: allowlist enforcement, news bias gate, kill-switch check, PortfolioCoordinator gate, StateManager integration, JSONL signal logging
- `04_codebase/tick_tradovate_client.py` — Tradovate REST API wrapper: 19 bracket safety validations, kill-switch check, OSO-unverified gate (blocks real orders), `dry_run=True` default
- `04_codebase/tick_tradovate_client_mock_tests.py` — 66/66 PASS mock test suite
- `04_codebase/tick_state_manager.py` — Full state persistence with atomic writes: positions, active brackets, daily PnL, strategy halts, heartbeat, processed signals
- `04_codebase/test_state_manager.py` — 44/44 PASS
- `04_codebase/tick_broker_reconciliation.py` — 10 reconciliation scenarios (ghost positions, missing brackets, stale state, broker unreachable, quantity mismatch)
- `04_codebase/test_broker_reconciliation.py` — 40/40 PASS
- `04_codebase/tick_portfolio_coordinator.py` — 10-rule coordinator: virtual vs broker position separation, same-symbol conflict detection, max-contracts enforcement, one-strategy demo isolation
- `04_codebase/test_portfolio_coordinator.py` — 15/15 PASS
- `04_codebase/tick_broker_position_sync.py` — Coordinator sees real broker position chain (MESM5 → MES → ES symbol normalisation)
- `04_codebase/test_broker_position_sync.py` — 26/26 PASS
- `04_codebase/tick_mock_broker.py` — Full mock broker: account state, positions, market/limit/stop orders, bracket simulation, fill simulation, reconciliation mismatch detection — 7/7 smoke tests PASS

### Supporting Tools
- `04_codebase/tick_bar_builder.py` — WebSocket + REST fallback bar builder, CVD continuity on restart, parquet append-only writes
- `04_codebase/tick_bar_builder_databento.py` — Databento-based bar builder
- `04_codebase/tick_news_monitor.py` — ForexFactory calendar + RSS headline monitor, in_news_window() gate
- `04_codebase/tick_startup_checklist.py` — Pre-flight: data freshness, allowlist, bracket test, contract expiry — 29 PASS, 11 WARN (data stale), 0 FAIL
- `04_codebase/tick_credentials_preflight.py` — System safety classification: shows DATA_READY, TRADOVATE_DISABLED
- `04_codebase/tick_dry_run_validation.py` — 10/10 PASS end-to-end dry-run check
- `04_codebase/tick_signal_log_reader.py` — Closed trade analysis from JSONL signal logs
- `04_codebase/tick_contract_rollover.py` — Quarterly rollover helper, UTF-8 safe, no hardcoded suffixes
- `04_codebase/portfolio_backtest.py` — Portfolio backtest with correlation matrix
- `04_codebase/live_strategy_allowlist.yaml` — 38-entry runtime allowlist: 1 DEMO_CANDIDATE, 4 ENABLED_DRY_RUN, 17 REVIEW_REQUIRED, 16 DISABLED_FOR_LIVE
- `06_live_trading/state/*.json` — 9 state skeleton files
- `KILL_SWITCH.txt` — File-based hard stop: write "STOP" to immediately halt and flatten
- `scripts/check_fortress_status.ps1` — Remote read-only status check

### Monitoring Architecture (Designed, Not Yet Deployed)
- `08_docs/ai_brain_design.md` — 7-agent AI brain architecture with Monitor, Risk Sentinel, Research Scout, Validation Agent, Deployment Gatekeeper, Daily Report, Alert Bot
- `08_docs/monitoring_dashboard_plan.md` — Status.json + heartbeat.json schema + Telegram alert design

---

## 3. What Is Not Built

The following items are planned, partially designed, or explicitly blocked.

| Gap | Priority | Blocker |
|-----|----------|---------|
| Exchange-verified OSO bracket orders | CRITICAL | No demo account |
| Live data dry-run (bar builder connected to live feed) | HIGH | No Tradovate credentials |
| Stale-bar detection in executor | HIGH | Development work pending |
| News filter baked into backtest engine | HIGH | Development work pending |
| Demo account (Tradovate paper trading) | CRITICAL | Must create account manually |
| State persistence wired into executor (code exists, not integrated) | HIGH | Development work pending |
| Reconciliation integrated into demo startup sequence | HIGH | Development work pending |
| Personal broker account (NinjaTrader Brokerage, IBKR, or Tradovate) | LONG-TERM | Deliberate — requires demo proof first |
| Telegram/Discord alert bot | MEDIUM | Credentials not configured |
| HTML monitoring dashboard | MEDIUM | Secondary to execution safety |
| ES/NQ historical data (2020+) from Databento | HIGH | Needs cost estimate + approval (~$50-100) |
| Depth_Imbalance_Momentum position-limit analysis | HIGH | Bug — overlapping positions inflate metrics |
| PBO / CSCV overfitting tests | MEDIUM | Post-data-expansion task |
| ML meta-labeling layer | LOW | Phase 6 — after live proof |
| AI brain agents (Monitor, Risk Sentinel, etc.) | MEDIUM | Phase 7 |
| Portfolio Sharpe optimisation with all 48+16 L2 survivors | MEDIUM | Requires strategy selection |
| Forward-look risk audit for L2 strategies | MEDIUM | Not yet run on new strategies |

---

## 4. What Is Statistically Credible

Strategies that have passed multi-year walk-forward validation with evidence-grade statistical testing. These are the only strategies that should ever be considered for deployment.

### OHLCV Batch Survivors — 9 Confirmed ALL-CLEAR (2014–2025, 12 years)

Tested on continuous back-adjusted futures with realistic costs ($6 RT commission, slippage sweep), full WFO (IS/OOS splits), DSR accounting for multiple trials, and Step 2 stress suite (2x cost, 20% missed fills, Topstep regime).

| # | Strategy | Instrument | Portfolio DSR | Trades | Notes |
|---|----------|-----------|:------------:|:------:|-------|
| 1 | bollinger_rsi_gc | MGC | 5.74 | 2,314 | Gold mean-reversion |
| 2 | donchian_breakout_cl | MCL | 3.46 | 236 | Oil daily trend — low freq |
| 3 | fomc_drift | MES | 1.63 | 57 | FOMC drift — borderline trade count |
| 4 | vwap_reclaim_gc | MGC | 12.27 | 1,408 | Standout performer; 2014-2025 |
| 5 | vwap_reclaim_si | SIL | 5.12 | 241 | Silver VWAP |
| 6 | vol_adj_momentum_gc | MGC | 6.39 | 1,016 | Z-score momentum |
| 7 | donchian_intraday_gc | MGC | 6.59 | 1,327 | Intraday Donchian |
| 8 | rth_orb_gc | MGC | 5.39 | 1,023 | RTH opening range |
| 9 | vol_adj_momentum_si | SIL | 4.44 | 435 | Silver momentum |

**Portfolio result (9 survivors, unit sizing):** DSR +14.64, PF 2.034, max DD $5,827, 12/12 positive years, Topstep PASS ($433k simulated P&L 2014–2025).

### L2 GC Survivors — 48 Confirmed (2020–2026, 6 years)

Full walk-forward + bootstrap + slippage ladder with corrected DSR formula and commission included. All 48 pass DSR > 0.50 threshold. Key deployment-grade strategies:

| Strategy | WF Sharpe | 1-tick Sharpe | 2-tick Sharpe | 3-tick Sharpe | Trade Count | Deployable? |
|----------|:---------:|:-------------:|:-------------:|:-------------:|:-----------:|:-----------:|
| CVD_VWAP (best variant) | ~3.5 | ~1.5 | **1.112** | 0.201 | 4k-7k | YES |
| Repeated_Replenishment (best) | ~3.2 | ~1.4 | ~0.8 | ~0.2 | 3k-6k | YES |
| Depth_Imbalance_Momentum (best) | 4.4 | ~1.8 | ~1.2 | 0.843 | 10k-30k | NOT YET — position inflation bug |

### L2 SI Survivors — 16 Confirmed (2020–2026, 6 years)

| Strategy | WF Sharpe | 1-tick Sharpe | 2-tick Sharpe | 3-tick Sharpe | Trade Count | Deployable? |
|----------|:---------:|:-------------:|:-------------:|:-------------:|:-----------:|:-----------:|
| Sweep_Continuation (hold=5) | 5.109 | 4.010 | **1.582** | -0.647 | ~200/yr | YES — ultra-selective |
| CVD_Microprice (cvd=60, mp=1.0) | 2.354 | 1.659 | **0.933** | **0.198** | 1k-1.4k | YES — most resilient |
| CVD_Microprice (cvd=70, mp=1.0) | 2.636 | 1.465 | **0.751** | **0.035** | 1k-1.4k | YES |
| CVD_VWAP (band=0.5, cvd=60) | 3.629 | 1.564 | 0.367 | -0.809 | 2.7k-4k | YES |

**Portfolio standout:** SI CVD_Microprice (mp_ticks=1.0) is the only strategy in the entire GC+SI portfolio that survives 3-tick slippage. Priority for conservative live deployment.

---

## 5. What Is Not Statistically Credible

These strategies have real backtested metrics but lack sufficient evidence for deployment decisions.

### ES and NQ — Single Regime, Short History

All ES and NQ data covers only December 2025 through May 2026: 5.5 months, one macro regime (tariff-volatility bull market). No bear market, no rate-hike cycle, no low-volatility ranging period. Any Sharpe ratio computed on this data reflects one market environment and cannot be generalised.

- ES/NQ strategies IDs 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 33–38: all have this limitation
- The only valid use is dry-run signal monitoring until 2+ years of data exist
- Required additional data: ES/NQ Databento mbp-10 from 2020–2026 (~$50-100, needs approval)

### Low Trade Count Strategies

Statistical confidence in Sharpe ratios collapses below approximately 100 trades. The confidence interval on Sharpe at n=21 spans from deeply negative to extremely positive — the point estimate is meaningless.

| Strategy | Trade Count | Status |
|----------|:-----------:|--------|
| NQ/trade_absorption_signal/30m (ID 12) | 21 | DISABLED — n=21 is statistically void |
| NQ/cvd_divergence_large_print/30m (ID 5) | 81 | DISABLED — n=81 insufficient |
| fomc_drift | 57 | BORDERLINE — accepted only because event-based strategies inherently low-frequency |

### Sweep_Absorption_Reversal GC

This strategy showed WF Sharpe > 5 in quick-mode backtesting (2024–2025 sample) but is absent from the full 5-year hardened set. It is a confirmed recency artifact driven by 2024–2025 market conditions. Deploying it based on quick-mode results would be a category error.

**Never deploy Sweep_Absorption_Reversal GC.**

### Depth_Imbalance_Momentum GC

This strategy shows 10,000–30,000 trades in 77,270 GC bars — approximately 39% of all bars trigger entries. This implies overlapping positions that massively inflate single-contract P&L metrics. The backtest result is unreliable until a position-limit enforcement fix is implemented and the strategy is re-run under single-contract discipline.

**Do not deploy Depth_Imbalance_Momentum until position-limit analysis is complete.**

---

## 6. Data Still Needed

Listed in priority order with cost context.

| Data | Cost Estimate | Priority | Approval Required | Notes |
|------|:------------:|:--------:|:-----------------:|-------|
| ES/NQ Databento mbp-10 (2020–2026) | ~$50-100 | CRITICAL | YES — explicit | Unlocks regime-robust ES/NQ validation; remaining budget from $125 is approximately $5 |
| Additional Databento budget allocation | N/A (budget decision) | CRITICAL | YES — explicit | Current remaining budget ~$5 from $120.14 spent |
| SI additional bar resolutions (3m, 5m) | Free — already downloaded | MEDIUM | No | Data exists, need bar construction only |
| GC MBO (order-level L3 data) | Very expensive | LOW | YES — explicit | Only if queue-position market-making is pursued |
| CL/crude historical L2 (MBP-10) | ~$30-50 | LOW | YES | donchian_breakout_cl is OHLCV-only — L2 not needed yet |

**Current Databento budget status:** $120.14 spent of $125 allocated. Any additional Databento download requires a new explicit budget approval. Do not download paid data without a cost estimate reviewed and approved first.

---

## 7. Strategy Families Still to Test

The following families have not yet been backtested in a systematic walk-forward campaign. These represent the next 6–24 months of research work, in approximate priority order.

### L2 / Microstructure (Priority: HIGH — Data Exists for GC/SI)

1. **CVD Divergence at VWAP** — explicit CVD-vs-price divergence computed at VWAP band boundaries, not just absolute levels
2. **OFI Multi-Level Confirmation** — L1 + L3 + L5 OFI alignment filter to reduce false signals from single-level noise
3. **Sweep-No-Replenishment Continuation** — trend-follow after a sweep that shows no absorption, as opposed to the absorption reversal already tested
4. **Failed Breakout Absorption** — fade false breakouts where absorption score is high after the breakout bar
5. **VWAP Deviation + CVD Divergence** — VWAP stretch combined with CVD reversal (two-signal confirmation)
6. **Multi-Timeframe OFI** — 30m context for trend direction, 1m OFI entry for timing
7. **GC/SI Cross-Market Confirmation** — both metals' OFI must agree before entry; removes single-instrument noise
8. **Depth Wall Rejection** — price approaches large L5/L10 depth level and bounces; requires level-by-level depth columns not currently in bar files
9. **OFI Shock Post-Sweep** — OFI reversal on the bar immediately after a sweep; fade the sweep reaction
10. **Repeat Sweep Exhaustion** — multiple sweeps in same direction with diminishing price progress signal exhaustion reversal

### OHLCV Session / Overnight (Priority: MEDIUM — Data Exists)

11. **London Open Breakout** — 30m opening range from London open (08:00 GMT), directional trade into NY session
12. **London Open Fakeout Reversal** — fade the initial London open direction when it reverses by 08:45 GMT
13. **Asia Range into London Continuation** — if price breaks out of Asia session range in London open direction, continuation trade
14. **US Pre-Market Gap Fill** — fade overnight gap when futures gap > 0.5 ATR from prior close
15. **Overnight High/Low Sweep Reversal** — entry when prior session's overnight high or low is swept and fails to hold

### Cross-Market (Priority: MEDIUM — Requires ES/NQ Data)

16. **GC/SI Ratio Mean Reversion** — gold/silver ratio deviates from rolling mean; fade the ratio divergence
17. **ES/GC Risk-On-Off** — equities down + gold up continuation; equities up + gold down reversal
18. **NQ Leadership into ES** — NQ makes new high before ES; short-term ES continuation trade

### Commodity Carry / Term Structure (Priority: LOW — New Data Required)

19. **CL Roll Yield Filter** — only trade Donchian breakout CL when curve is in backwardation (positive roll yield)
20. **GC Term Structure Momentum** — front vs deferred spread momentum as market-regime filter

### VWAP / Volume Profile (Priority: LOW — Already Have Core VWAP Strategies)

21. **Value Area High/Low Rejection** — price returns to value area high or low and rejects; continuation trade from prior day's value area acceptance
22. **Poor High / Poor Low Repair** — single-print at session extreme acts as magnet for next session
23. **VPOC Magnet** — previous session's volume point of control acts as magnet when price is remote

---

## 8. Manual Signal Trading System Plan

The manual signal system is the first real-world validation layer. It requires no broker connection, no automation, and no demo account. It can be built and run immediately.

### Purpose

The manual system answers one question: does the backtest signal logic produce actionable, tradeable alerts in live market conditions? A signal that fires on stale bars, during illiquid hours, or consistently at prices that have already moved is not useful regardless of its backtest Sharpe.

### Components to Build

**1. Signal Logger (Already Partially Exists)**
- `tick_live_executor.py` in dry-run mode already logs signals to `06_live_trading/logs/signals_YYYYMMDD.jsonl`
- Each entry records: timestamp, strategy ID, symbol, direction, entry price, stop price, target price, reason accepted/rejected
- Existing: YES — needs bar builder running to produce live signals

**2. Console Alert Output**
- Extend executor to print a formatted human-readable alert to console on each signal fire
- Format: `[14:32:15 CT] LONG GC | Strategy: CVD_VWAP | Entry: 2342.5 | Stop: 2338.0 | Target: 2351.5 | R: $47 | Reason: CVD above threshold at VWAP band`
- Implementation: add a `--alert-console` flag to executor, minimal code change

**3. Telegram Alert (Optional, No Credentials Yet)**
- When Telegram bot credentials exist, extend the alert output to push to a Telegram channel
- Architecture: alert bot reads signal JSONL every poll cycle, pushes new entries to Telegram
- This is the `Alert Bot` from `ai_brain_design.md` Agent 7
- Not a blocker — console and JSONL logging are sufficient for Stage 3

**4. Execution Tracking Spreadsheet**
- Human manually records: which alerts were acted on, actual fill price, actual exit price, slippage vs expected
- Compare actual fills vs signal entry prices to compute real-world fill quality
- Gate: average actual slippage must be within 2 ticks of backtest assumption

### Eligible Strategies for Manual Signals

Only strategies with credible multi-year evidence should generate manual alerts. Do not act on dry-run signals from ES/NQ strategies until 2+ years of historical data exist.

Eligible for manual trading signals:
- All 9 OHLCV survivors (GC, SI, CL, MES event-driven)
- L2 GC: CVD_VWAP (best variant), Repeated_Replenishment (best variant)
- L2 SI: CVD_Microprice (cvd=60, mp=1.0), Sweep_Continuation (hold=5)

Not eligible for manual trading signals until evidence improved:
- Any ES strategy
- Any NQ strategy
- Depth_Imbalance_Momentum GC (position bug)
- Sweep_Absorption_Reversal GC (recency artifact)

### Operation Protocol

1. Start bar builder: `venv_new\Scripts\python.exe -X utf8 04_codebase\tick_bar_builder.py --rest` (requires Tradovate credentials — not yet available)
2. Run executor in dry-run: `venv_new\Scripts\python.exe -X utf8 04_codebase\tick_live_executor.py --poll 60`
3. Monitor console and JSONL log for signals from eligible strategies
4. Manually evaluate each alert: is the market in session, is liquidity adequate, is there a pending news event?
5. If trading the signal manually: record fill in tracking spreadsheet
6. End of day: compare fills with `tick_signal_log_reader.py` output

---

## 9. Personal Broker Automation Plan

This is the long-term target. Prop firm automation is explicitly not the target route.

### Why Personal Broker, Not Prop Firm Automation

Prop firm automation requires written compliance approval before any autonomous order submission. Topstep API access is not the same as automation permission. The trader agreement governs, not API availability. Getting that approval, maintaining compliance, and managing account resets adds operational risk that is unnecessary once a personal broker route is available.

Personal broker trading with real personal capital:
- No prop firm rules constraining strategy parameters
- No account reset risk
- No daily loss limit enforced by a third party
- Natural scaling as edge is proven
- Can be connected to NinjaTrader Brokerage, Interactive Brokers, or Tradovate personal account

The cost of this route is that losses are real from day one. That is why the personal broker route does not start until at minimum 30 days of profitable demonstration on a simulator account.

### Target Broker Candidates

| Broker | API | Bracket Orders | Micro Contracts | Decision |
|--------|:---:|:--------------:|:---------------:|---------|
| Tradovate personal account | REST/WebSocket | OSO supported | MGC, MES, MNQ, SIL, MCL | First target — code already written for Tradovate API |
| NinjaTrader Brokerage | NinjaTrader ATM | Supported natively | All micros | Second option — requires NinjaTrader bridge |
| Interactive Brokers | TWS API / IBKR API | TWS orders with bracket | All micros | Third option — different API, requires new client |

### Required Before Any Personal Broker Live Connection

All of the following must be complete before connecting to a real personal broker account with capital at risk:

1. Tradovate demo/sim account created and tested (or equivalent sim at target broker)
2. OSO bracket order format exchange-verified on demo (entry + stop + target all confirmed at exchange level)
3. State persistence fully integrated into executor (not just code existing — wired in and tested)
4. Startup reconciliation against real broker positions verified working
5. Stale-bar detection implemented and tested (executor halts if newest bar is > N minutes old during market hours)
6. Kill switch tested end-to-end (KILL_SWITCH.txt = STOP → positions flattened within one poll cycle)
7. At minimum 30 trading days of sim/demo with no system crashes, no unexplained positions, no CRITICAL reconciliation events
8. Slippage report produced: actual fills vs expected within 2 ticks average
9. Explicit manual sign-off (write date and decision to a document)

### Personal Broker Architecture

The existing code stack is already designed for this route:
- `tick_tradovate_client.py` wraps the Tradovate REST API
- `tick_live_executor.py` handles signal generation, risk checks, and order routing
- `tick_state_manager.py` handles position persistence across restarts
- `tick_broker_reconciliation.py` handles startup state comparison
- `tick_portfolio_coordinator.py` prevents same-symbol conflicts and enforces max-contracts rules

What needs to be built or verified:
- Exchange-verified OSO (one module flag `_OSO_EXCHANGE_VERIFIED = True` after manual confirmation)
- Stale-bar detection in executor
- News filter applied to backtest results for eligible strategies
- Integration tests against demo account (read-only position queries, then bracket orders)

---

## 10. AI Brain Plan

The AI brain is defined as an intelligent observer and advisor. It does not trade. It cannot enable strategies. It cannot override risk controls. These constraints are permanent design decisions, not temporary restrictions.

### Seven Agents — Implementation Priority

**Phase 1 — Build First (Before Demo Trading)**

**Agent 1: Monitor Agent**
Watches: heartbeat file age, log file errors, data freshness per symbol, process health, state file consistency.
Output: structured health status dict; alerts to Alert Bot on anomalies.
Cannot: take corrective action.
Implementation: a cron-style Python process that reads state files and JSONL logs, writes a status.json, triggers Telegram alerts on anomalies.

**Agent 7: Alert Bot**
Sends alerts for: signal fired, trade skipped, strategy halted, drawdown threshold hit (>80% consumed), broker mismatch, data stale, kill switch triggered, daily summary, broker disconnected, unexpected position.
Channels: Telegram (primary), Discord webhook (secondary).
Implementation: reads `06_live_trading/logs/signals_YYYYMMDD.jsonl` and `06_live_trading/state/status.json`, sends Telegram messages via bot API.

**Phase 2 — Build Before Scaling**

**Agent 2: Risk Sentinel**
Watches: daily PnL vs limit (threshold: 80% consumed), trailing drawdown vs limit, per-strategy halt conditions, broker position mismatch.
Can: issue PAUSE recommendation for a strategy, trigger alert, log risk events.
Cannot: increase risk limits, re-enable a halted strategy, override RiskManager.
Implementation: reads StateManager files every N seconds, computes percentages, writes to `06_live_trading/logs/risk_events.jsonl`.

**Agent 6: Daily Report Agent**
Produces at EOD: daily PnL summary, signal log, risk events, execution quality, error log summary, system status.
Output: Markdown report + JSON data file at `06_live_trading/reports/daily/YYYYMMDD.md`.

**Phase 3 — Build After Live Operation Is Stable**

**Agent 3: Research Scout** — hypothesis cards from literature, generates `02_research/candidates/`
**Agent 4: Validation Agent** — automated WFO + DSR + PBO runs on candidate strategies
**Agent 5: Deployment Gatekeeper** — gate-check report for demo eligibility (cannot grant eligibility directly)

### Permanent Forbidden Actions for All AI Agents

The AI brain can never: increase position size, enable a disabled strategy, override RiskManager, override kill switch, override the allowlist, trade live without explicit approval, deploy a new strategy without validation, modify compliance rules, chase news autonomously, remove loss limits, edit historical backtest results, hide rejected strategies, or transition demo mode to live mode.

If any code path in the AI brain leads to one of these actions, it is a bug.

---

## 11. Risk Management Plan

### Per-Trade Risk

| Instrument | ATR stop distance | Micro contract PV | Typical risk | Cap |
|-----------|:----------------:|:-----------------:|:------------:|:---:|
| MGC (Gold micro) | $10–15/point | $10/point | $150–$220/trade | $200 hard cap |
| SIL (Silver micro) | varies | $50/oz | $100–$200/trade | $200 hard cap |
| MCL (Crude micro) | varies | $100/contract | variable | $200 hard cap |
| MES (ES micro) | 12–20 points | $5/point | $90–$150/trade | $200 hard cap |
| MNQ (NQ micro) | 50–90 points | $2/point | $150–$270/trade | $200 (blocks some entries) |

The $200 cap is enforced in `tick_tradovate_client.py:place_bracket_order()` via dollar risk calculation before submission. High-ATR entries are blocked — this is protective behaviour.

### Daily Loss Limits (Micro Account)

| Limit | Amount | Effect |
|-------|:------:|--------|
| Per-strategy daily halt | $250 | No new entries for that strategy for rest of session |
| Portfolio daily halt | $600 | No new entries from any strategy for rest of session |
| Account trailing DD halt | $800 | Halt account; resume next session with review |

### Personal Account Risk Constraints

From `user_risk_constraints.md`: 10 funded Topstep accounts, approximately $1,000 remaining runway each, personal maximum DD $2,000 per account. This means the $200 per-trade cap is correct — a single trade should never risk more than 10% of the remaining runway on any account.

### Trailing Drawdown (Ratchet Stop)

The RiskManager implements a ratchet trailing stop at the account level. As account equity rises, the trailing drawdown floor rises with it. The floor never decreases. This prevents giving back all gains after a profitable run.

### News Window Management

`tick_news_monitor.py` blocks new entries during high-impact news windows (FOMC, NFP, CPI, ECB, BOE, EIA). The window is configurable. Default is 30 minutes before and 30 minutes after each event.

Current gap: if the ForexFactory feed fails, the news gate silently does nothing (no conservative fallback). This must be fixed before demo auto-trade — when feed fails, block all trading for the duration rather than open the gate.

### Weekend and Session Flattening

The executor has `_is_friday_close_time()` to flatten all positions before the weekend. This is a hard rule. All positions must be flat before end of CME Friday session.

### Partial Take-Profit Restriction

With MAX_CONTRACTS_PER_TRADE = 1, partial TP (closing 50% of 1 contract = 0.5 contracts) is physically impossible at any broker. The current code tracks partial TP in memory but cannot execute it. This must be disabled. Use full exit at +3R only (STOP_MULT=1.5, TP_MULT=3.0 giving 2:1 R:R).

---

## 12. Portfolio Construction Plan

### Current Portfolio Evidence (OHLCV Survivors, Unit Sizing)

The 9-survivor OHLCV portfolio has:
- DSR: +14.64
- Profit Factor: 2.034
- Maximum Drawdown: $5,827
- 12/12 positive years (2014–2025)
- All inter-strategy correlations below 0.49 (GC strategies within family); cross-instrument correlations near zero
- Monte Carlo P(drawdown breach) = 0.15%

### L2 Portfolio Integration Plan

The L2 GC and SI survivors are not yet portfolio-backtested against each other or against the OHLCV survivors. The next portfolio construction step is:

1. Select top 3–5 L2 strategies (CVD_VWAP GC, CVD_Microprice SI, Sweep_Continuation SI, Repeated_Replenishment GC) for portfolio inclusion
2. Run correlation analysis: L2 strategies likely have higher intra-group correlation than OHLCV strategies (all use CVD/order flow signals)
3. Compute portfolio Sharpe with combined OHLCV + L2 portfolio
4. Check that adding L2 strategies does not increase max drawdown beyond $10,000 (estimated personal tolerable threshold)
5. Position the portfolio for the personal broker sizing goal: 1 micro contract per strategy, no exceptions initially

### Correlation Rules

- Max 2 strategies per instrument per session in a personal broker account
- Never run two strategies with the same core signal source (e.g., two CVD divergence variants on the same symbol in the same account)
- If running on a Topstep account: 1 strategy per account only (daily loss stacking risk)
- For personal broker with more capital: can run 3–5 strategies on different symbols simultaneously

### Regime-Dependent Deployment

From `evidence_upgrade_plan.md` — strategies should be sliced by regime before deployment:

| Slice | Expected Action |
|-------|----------------|
| VIX > 25 (high vol) | Keep trend-following strategies; pause mean-reversion |
| VIX < 18 (low vol) | Mean-reversion viable; trend strategies may underperform |
| ADX > 30 (strong trend) | Favour trend-following; caution on VWAP mean-reversion |
| ADX < 20 (chop) | VWAP strategies work; Donchian may whipsaw |
| FOMC/CPI/NFP days | Apply news window block; fomc_drift activates |

### Drawdown Overlap Analysis

The portfolio backtest showed that even in the worst historical year (2015: -$3,654 on 5-survivor run, improved in 9-survivor run), the drawdown was never catastrophic. The max DD of $5,827 across 12 years at unit sizing is well within the personal $2,000 per-account runway constraint because unit sizing is 1 micro contract and the personal account would eventually hold more than 1 account per strategy.

---

## 13. Deployment Gates

These are the exact pass/fail criteria for each phase transition. No gate can be waived.

### Gate 0: Code Audit Complete

**Status: PASS** (live_readiness_audit.md, 2026-05-17)

---

### Gate 1: No Hard-Coded Secrets

**Status: PASS** — All credentials read from environment variables. `.env` is Git-ignored.

---

### Gate 2: Dry-Run Executor Works

**Status: PASS** — 10/10 dry-run validation, 29 PASS / 0 FAIL startup checklist.

---

### Gate 3: Live Bar Builder Connected to Real Feed

**Status: BLOCKED — No Tradovate credentials exist.**

Pass criteria: Bar builder (REST or WebSocket) runs without errors for one full RTH session. Parquet files updated. Data freshness check reports bars within 5 minutes of current time during market hours.

---

### Gate 4: OSO Bracket Order Exchange-Verified on Demo

**Status: BLOCKED — No demo account exists.**

Pass criteria: A far-below-market bracket order (entry + stop + target) is placed on a Tradovate demo account. All three legs appear in the broker's order management system. OCO cancellation of the sibling leg on fill is confirmed. `_OSO_EXCHANGE_VERIFIED` flag set to True by human after confirmation.

---

### Gate 5: State Persistence Integrated and Tested

**Status: PARTIALLY DONE** — `tick_state_manager.py` exists, 44/44 tests pass, but not yet wired into the executor restart path.

Pass criteria: Executor restarts while a dry-run position is open. After restart, executor reads `06_live_trading/state/positions.json`, recognises the existing position, and does not re-enter. Reconciliation log shows clean state.

---

### Gate 6: Stale-Bar Detection Active

**Status: NOT BUILT.**

Pass criteria: If bar builder stops during market hours and newest bar is more than 5 minutes old, executor logs a STALE_DATA warning and blocks all new entries until data refreshes.

---

### Gate 7: News Filter Applied and Conservative Fallback

**Status: PARTIAL** — News monitor works but has no conservative fallback on feed failure.

Pass criteria: If ForexFactory JSON fails to load, the news gate blocks all entries for the rest of the poll cycle rather than silently allowing trading.

---

### Gate 8: Kill Switch End-to-End Tested

**Status: IMPLEMENTED, NOT LIVE-TESTED.**

Pass criteria: With executor running in demo mode with at least one open position, write "STOP" to KILL_SWITCH.txt. Executor must flatten all positions and halt within one poll cycle.

---

### Gate 9: 30 Days Manual Signal Tracking

**Status: NOT STARTED.**

Pass criteria: 30 trading days of logged signals from eligible GC/SI strategies (not ES/NQ). Human tracks actual fills vs signal entry prices. Average slippage within 2 ticks of backtest assumption. No signals firing on stale data.

---

### Gate 10: 30 Days Personal Broker Simulator

**Status: NOT STARTED.**

Pass criteria: One strategy on one micro contract in broker paper/sim account. 30 trading days with: no system crashes, no unexplained positions, no CRITICAL reconciliation events, kill switch tested, slippage within 2 ticks.

---

### Gate 11: Personal Broker Tiny Live

**Status: NOT STARTED.**

Pass criteria: 60 days of live trading: one strategy, one micro contract, $200 max risk per trade, daily loss limit active. Actual cumulative PnL positive. Actual slippage within 2 ticks of backtest. No scaling before Day 60.

---

### Gate 12: Manual Sign-Off Before Any Scaling

**Status: NOT STARTED.**

Pass criteria: Explicit dated written confirmation by account owner (Conor) after reviewing Gates 10–11 evidence. No AI or automated process can open this gate.

---

## 14. 90-Day Roadmap

**Starting date:** 2026-06-03  
**Period:** June 3 through August 31, 2026

### Weeks 1–2 (June 3–14): Infrastructure Completion

**Objectives:**
- Implement stale-bar detection in `tick_live_executor.py` (alert and block entries if newest bar > 5 minutes old during RTH)
- Fix news monitor conservative fallback: if ForexFactory fails, block entries for that cycle
- Fix partial-TP impossibility: disable partial-TP in executor when MAX_CONTRACTS_PER_TRADE=1, use full exit at +3R only
- Integrate `tick_state_manager.py` into executor restart path (read positions.json on startup before entering bar loop)
- Wire `tick_broker_reconciliation.py` into executor startup sequence in demo/live modes
- Run `tick_mock_broker.py` smoke tests to confirm 7/7 still pass after any changes
- Create Tradovate personal/sim account (administrative task — can be done in parallel)
- Run contract rollover check (MESM5/MGCM5/MNQM5 expire June 20; rollover to U5 by June 13)

**Exit gate:** Stale-bar detection test passes. Partial-TP disabled. State manager wired into executor startup. Contract rollover complete.

---

### Weeks 3–4 (June 15–28): Live Data Dry-Run

**Prerequisite:** Tradovate credentials available (or alternative bar feed configured).

**Objectives:**
- Run bar builder (REST mode) for at least 3 full RTH sessions
- Confirm data freshness checks report current bars
- Run executor in dry-run for eligible GC/SI strategies (IDs 4, 9, 16–23 from allowlist)
- Accumulate signal log: verify signal timing distribution matches backtest expectations
- Run `tick_signal_log_reader.py` after each session; confirm no duplicate signals
- Begin manual signal tracking spreadsheet
- Test kill switch: while executor running, write STOP to KILL_SWITCH.txt, confirm halt within one poll cycle

**Exit gate:** 3+ sessions of clean dry-run signals. Signal log content valid. Kill switch confirmed working. No crashes in any component.

---

### Weeks 5–8 (June 29 – July 26): Manual Signal Trading Period

**Objectives:**
- Generate live signals from eligible GC/SI strategies during RTH
- Human evaluates each alert and decides whether to place manual trade
- Track every alert and outcome in execution spreadsheet: alert time, entry price, fill price, exit price, slippage
- Run for minimum 30 trading days before advancing
- Compute actual slippage distribution: target is mean within 2 ticks

**Exit gate (Gate 9):** 30 trading days logged. Mean slippage within 2 ticks. Signals fire at expected frequency. No system crashes.

---

### Weeks 9–12 (July 27 – August 23): Personal Broker Simulator

**Prerequisite:** Gate 9 passed. Demo account (sim/paper) created at target personal broker.

**Objectives:**
- Select one strategy for simulator (recommended: SI CVD_Microprice mp=1.0 or GC CVD_VWAP — both have 5-year evidence and pass 2-tick slippage)
- Configure simulator account with 1 micro contract limit
- Run executor in demo mode (not dry-run) against simulator account
- Verify bracket order legs appear in broker's order management system
- Run reconciliation check on every startup
- Log all fills to JSONL
- Monitor for 30 trading days

**Exit gate (Gate 10):** 30 days, no crashes, no unexplained positions, no CRITICAL reconciliation events, slippage within 2 ticks.

---

### Weeks 13+ (August 24+): Decision Point

By August 24, two outcomes are possible:

**Outcome A — Gates 9 and 10 both passed:**
Proceed to personal broker tiny live (Gate 11). One strategy, one micro contract, $200 max risk per trade, 60-day no-scaling period.

**Outcome B — Gate blocked:**
Identify the specific blocker. Most likely causes: slippage exceeds 2 ticks (backtest assumption too optimistic), signal frequency lower than expected (strategy not firing in current regime), or execution bug requiring code fix. Document and resolve before advancing.

---

### Parallel Research (Ongoing Throughout All Weeks)

These research tasks run in parallel with infrastructure and execution work. They do not block any gate.

- Weeks 1–4: Fix Depth_Imbalance_Momentum position-limit bug in `tick_l2_backtest.py`; re-run evidence upgrade with single-contract enforcement
- Weeks 3–6: Implement and test `cvd_divergence_vwap` from l2_strategy_backlog.json (top priority L2 strategy)
- Weeks 5–8: Implement and test `ofi_multi_level_confirmation` and `sweep_no_replenishment_continuation`
- Weeks 7–10: Obtain ES/NQ Databento historical data (requires explicit budget approval ~$50-100); re-run ES/NQ backtests over 5-year window
- Weeks 9–12: Portfolio combination backtest of top 3 L2 strategies + 9 OHLCV survivors
- Ongoing: Maintain l2_strategy_backlog.json as new hypotheses arise

---

## 15. Hard Stop Rules

These rules cannot be overridden by any system component, script, AI agent, or human decision made under time pressure. They exist because the decisions that lead to financial ruin are almost always made quickly and under emotion.

**Rule 1: Do not place real orders.**
No automated or manual order submission to any broker using real capital until Gates 9, 10, and 11 are all in PASS state and Gate 12 (manual sign-off) is complete. "I feel confident" is not a gate.

**Rule 2: Do not connect funded accounts.**
Funded Topstep accounts and any personal capital accounts with real money must not be connected to the automation system until Gate 12 is passed. Connecting for "just a test" is not permitted.

**Rule 3: Do not connect real personal broker accounts yet.**
Even a personal broker account with $1,000 is real money. Gate 10 (30-day simulator) must pass before any connection to a live personal account.

**Rule 4: Do not hard-code secrets.**
No API keys, passwords, tokens, or credentials in source code. All secrets read from environment variables at runtime. If you find a hard-coded secret, rotate it immediately before doing anything else.

**Rule 5: Do not commit `.env`.**
The `.env` file is Git-ignored. Never remove it from `.gitignore`. Never stage it with `git add`. If you accidentally commit `.env`, rotate all credentials in it immediately.

**Rule 6: Do not download paid Databento data without cost estimate and explicit approval.**
Current Databento budget is approximately $5 remaining from the $125 allocation. Any new data download must be estimated, reviewed, and explicitly approved before the command is run.

**Rule 7: Do not treat ES/NQ as valid evidence.**
ES and NQ data covers only 5.5 months (December 2025–May 2026), one macro regime. Any Sharpe ratio, DSR, or profit factor computed on this data is not a reliable basis for deployment decisions. ES/NQ strategies may run in dry-run monitoring mode only.

**Rule 8: Do not treat low-trade-count strategies as deployable.**
Strategies with fewer than 100 OOS trades have statistically meaningless Sharpe ratios. NQ/trade_absorption_signal (n=21) is explicitly excluded. fomc_drift (n=57) is accepted only because FOMC events are inherently infrequent and the strategy is event-based.

**Rule 9: Do not deploy without all four execution pre-conditions.**
No strategy may run in any auto-trade mode (demo or live) without: (1) broker-native bracket orders exchange-verified, (2) state persistence integrated and tested, (3) stale-bar detection active, (4) startup reconciliation working. All four must be present simultaneously.

**Rule 10: Manual alert system is allowed before automation.**
Generating dry-run signals and acting on them manually is explicitly permitted at any stage. This is how Stage 3 works. Manual trading is a validation tool, not a workaround.

**Rule 11: Full automation targets personal broker route, not prop firms.**
The automation architecture is designed for a personal broker account (NinjaTrader Brokerage, IBKR, Tradovate personal). Prop firm automation requires explicit written compliance approval from each firm before any autonomous order submission. Never send automated orders to a prop firm account without that approval.

**Rule 12: Do not deploy Sweep_Absorption_Reversal GC.**
This strategy passes quick-mode backtesting but is absent from the full 5-year hardened set. It is a confirmed recency artifact. Deploying it would be a category error that the research process specifically exists to prevent.

**Rule 13: Do not deploy Depth_Imbalance_Momentum without position-limit analysis.**
The backtest metrics for this strategy are inflated by overlapping positions (39% of GC bars trigger entries = physically impossible with 1 contract). The metrics are unreliable until the bug is fixed and the strategy is re-run with single-contract enforcement.

**Rule 14: No scaling before Day 60 of live trading.**
When live personal broker trading begins, the contract count is 1 micro per strategy. No increase in size is permitted for the first 60 live trading days, regardless of P&L.

**Rule 15: Human required for all deployment decisions.**
No AI agent, automated script, or `--auto-deploy` flag may add a strategy to the allowlist, change a strategy's eligibility tier, or increase position sizing. All deployment decisions require explicit human action with a dated record.
