# Master Next Step Analysis — Fortress Trading System
**Date:** 2026-05-18  
**Status:** DECISION DOCUMENT — Read before any next action

---

## 1. Current System Classification

| Dimension | Status |
|---|---|
| Dry-run signal generation | **DRY-RUN READY** |
| Demo auto-trade | **NOT READY** |
| Live / funded auto-trade | **NOT READY** |
| Bracket order support | **MOCK-VERIFIED ONLY** — not exchange-verified |
| State persistence | **NOT IMPLEMENTED** |
| Broker reconciliation | **NOT IMPLEMENTED** |

The system can generate signals, apply risk rules, log to JSONL, and execute dry-run bracket order payloads. It cannot safely run unattended demo or live trading.

---

## 2. Current Strengths

### Research Pipeline
- Full OHLCV backtesting with DSR, WFO, Monte Carlo, and 1-tick Sharpe
- 150-day tick/L2 bar backtest covering Dec 2025 – May 2026
- 4,144 trades across 15 strategies on 3 instruments (ES, NQ, GC)
- 7-year GC data coverage; validated over multiple regimes (2020–2026)
- Strategy allowlist with 5 eligibility tiers enforced at runtime

### Execution Infrastructure
- `tick_live_executor.py` — signal generation for 15 strategies, news bias gate, allowlist enforcement
- `tick_risk_manager.py` — ratchet trailing stop, consecutive loss circuit breaker, per-strategy halts, account halt
- `tick_tradovate_client.py` — demo-safe Tradovate API wrapper, placeOSO implemented, dry_run=True default
- `tick_bar_builder.py` — WebSocket + REST fallback bar builder, CVD continuity on restart
- `live_strategy_allowlist.yaml` — runtime gating; DEMO_CANDIDATE / ENABLED_DRY_RUN / REVIEW_REQUIRED / DISABLED / RESEARCH_ONLY
- `KILL_SWITCH.txt` — file-based hard stop read at every bar loop iteration
- Signal JSONL logging — all entries and exits with R-multiple, ratchet state, reason
- `tick_signal_log_reader.py` — closed trade analysis, win rate, R-distribution from live logs

### Validation
- `tick_dry_run_validation.py` — 10/10 PASS (all 15 strategies loaded, bracket mock payloads verified)
- `tick_startup_checklist.py` — pre-flight: data freshness, allowlist, bracket test (dynamic symbol lookup)
- `tick_credentials_test.py` — 5-gate credential pre-flight (authentication → account → contracts → quotes → positions)

### Monitoring / Control
- `tick_recent_performance.py` — trailing P&L report with regime flag
- `tick_session_supervisor.py` — process supervisor for bar builder + executor
- `tick_contract_rollover.py` — quarterly rollover helper (reads all 3 files, UTF-8 safe, no hardcoded suffixes)
- Remote access via Tailscale / RustDesk / SSH
- `tick_news_monitor.py` — ForexFactory calendar + RSS headlines, counter-bias blocking

### Statistical Quality
- Survivors-only profit factor: **1.87x** (trustworthy subset)
- Expectancy: **+$23.45/trade** (survivors), **+$19.92/trade** (full portfolio)
- GC 7-year stress: 7/7 years positive on multiple strategies
- Monte Carlo: P(drawdown breach) = 0.15%

---

## 3. Current Weaknesses

### Evidence Quality
| Issue | Detail |
|---|---|
| ES/NQ data depth | Only 5 months (Dec 2025 – May 2026). One regime, one macro environment. |
| GC concentration | ~46% of total portfolio profit from ~14% of trades. If GC regime shifts, portfolio suffers. |
| Full portfolio PF 1.44x | Decent but not large. Driven partly by high-trade-count low-edge strategies. |
| Low-count strategies | Strategies 5 (n=81), 12 (n=21), 3/4/6/7/9 (n=unknown) — unreliable Sharpe |
| Regime sensitivity | 30-day regime analysis shows ES mean-reversion degrading; regime-specific outperformance may not persist |

### Execution Safety
| Gap | Impact |
|---|---|
| Bracket orders mock-verified only | The OSO payload format and response parsing are untested against real exchange. Unknown whether `[entry, stop, target]` list structure matches actual Tradovate response. |
| No broker-native stop verification | If a position is opened and the bracket fails silently, there is no broker-native stop protecting capital. |
| State persistence missing | On restart, executor has no memory of open positions, active brackets, entry prices, or daily P&L. Reconciliation is impossible without persistent state. |
| Broker reconciliation missing | No logic to compare local state against broker state after restart or disconnect. |
| No kill switch check inside placeOSO | `place_bracket_order()` does not check `KILL_SWITCH.txt` before submitting. |
| No session-open gate | No check that market session is open before submitting orders. |
| No duplicate order protection | No persistent signal ID tracking to prevent double-entry on restart. |

### Structural / Architecture
| Gap | Detail |
|---|---|
| Partial TP impossible | 1 micro contract cannot split. Ratchet stop is the workaround, but it is inferior to partial-profit booking. |
| L2 bars ≠ event-level L2 | Current strategies use aggregated L2 bars, not full order book event streams. True OFI, sweep detection, and absorption require event-level data and a separate feature engine. |
| No live monitoring dashboard | No status.json, no heartbeat.json, no dashboard. Monitoring requires manual log inspection. |
| Slippage not modelled in live | Backtest uses 1-tick cost assumption. Real demo execution will have latency, wider spreads on news, and potential order rejection. |

### Risk to Capital
| Issue | Detail |
|---|---|
| Worst-day risk on disabled strategies | Strategies 1, 5, 6, 10, 11, 12 each have worst-day micro losses of $304–$965. These are correctly disabled. |
| Strategy 15 (GC/key_level_cvd/5m) | Worst day $1,623 on micro — far above account runway. Disabled correctly, but if RiskManager is bypassed, catastrophic. |
| 1t-Sharpe below 1.0 on GC 5m | Strategy 15 borderline; may not be profitable after real execution costs. |

---

## 4. Strategic Decision

**Do not add more strategies.**  
**Do not begin demo auto-trading.**  
**Do not touch funded accounts.**

The next development phase is:

1. **Harden execution first** — bracket orders must be mock-tested, then exchange-verified on demo. State persistence must be built. Broker reconciliation must be built.

2. **Upgrade evidence** — ES/NQ need 2+ years of data before regime-robust conclusions are possible. GC concentration must be reduced by finding non-correlated instruments or waiting for ES/NQ validation.

3. **Expand the L2 feature engine** — current strategies use aggregated bars. The next generation should use a proper feature pipeline with OFI, depth imbalance, sweeps, and absorption derived from event-level L2 data.

4. **Demo one strategy only** — after execution safety is proven, demo a single low-risk strategy (Strategy 2: ES/cvd_divergence_large_print/15m). Monitor slippage, fill quality, and bracket behaviour before expanding.

5. **Do not deploy the 15-strategy portfolio** — most strategies are not demo-eligible. Deploying all 15 would create uncontrolled risk and obscure per-strategy diagnostics.

---

## 5. Required Work Before Any Demo Auto-Trade Can Begin

All items below must be complete before Strategy 2 can run in demo:

| # | Item | File | Status |
|---|---|---|---|
| 1 | Bracket order mock test suite | `tick_tradovate_client.py` | Partially done (placeOSO exists, response parsing unverified) |
| 2 | Exchange-verify OSO payload with demo | Manual test via credentials | **BLOCKED** — needs credentials |
| 3 | State persistence layer | `06_live_trading/state/*.json` | **NOT BUILT** |
| 4 | Broker reconciliation logic | `tick_live_executor.py` | **NOT BUILT** |
| 5 | Kill switch check inside place_bracket_order | `tick_tradovate_client.py` | **NOT BUILT** |
| 6 | Session-open gate before order submission | `tick_tradovate_client.py` | **NOT BUILT** |
| 7 | Duplicate order protection | `tick_live_executor.py` | **NOT BUILT** |
| 8 | Tradovate demo credentials | Environment variables | **NOT YET RECEIVED** |
| 9 | Live-vs-backtest degradation report (post-demo) | After demo runs | **NOT YET** |

---

## 6. Final Decision: 30-Day Sprint

| Week | Focus | Gate |
|---|---|---|
| Week 1 | Execution safety design and mocked tests | Bracket mock tests pass; state persistence designed |
| Week 2 | Live data dry-run | Bar builder verified on live feed; dry-run signals logged |
| Week 3 | Demo one strategy | Strategy 2 only; bracket orders + reconciliation required |
| Week 4 | Evaluate demo results | Degradation report; decide expand/pause/acquire data |

**Current safe next command:**
```
python -X utf8 tick_startup_checklist.py --quick
```
Then review signal log from previous dry-run sessions:
```
python -X utf8 tick_signal_log_reader.py --days 7 --trades
```

---

*This document supersedes any informal "next step" notes. Update when a gate changes state.*
