# FORTRESS — Full Engineering & Viability Diagnostic
**Date:** 2026-07-02 · **Author:** Claude (senior-engineer review, full codebase + live VPS audit)
**Scope:** architecture, data integrity, code quality, security, operations, and an unsentimental
assessment of long-term profitability.

---

## 1. Executive summary

Fortress is a structurally sound but premise-broken system. The engineering skeleton —
layered risk gates, atomic state persistence, kill switch, allowlist, reconciliation —
is better than the vast majority of retail algo stacks. But it was built to copy-trade
10 funded prop accounts that **no longer exist**, it has never run a single trade on
real-time data, its only confirmed edge (pre-FOMC drift) is academically documented as
dead post-2015, and this week its own watchdog resurrected a disabled service that
destroyed the only statistically trustworthy dataset it had. The system's greatest
asset is its validation discipline (DSR, walk-forward, worst-day analysis); its greatest
liability is the gap between backtest fiction (session-filtered Sharpe 7–12) and any
evidence of live edge (10 days live-paper: **-$1,450, 13% win rate**, on delayed data).

**Verdict:** keep it as a research/validation platform on a $0 data budget; spend nothing
and connect no broker until the trusted 30m strategies survive 4–6 weeks of honest forward
validation on the now-clean data. The path to real-time Level-2 exists at $0/mo (Ironbeam)
when — and only when — the forward validation earns it.

---

## 2. System inventory

**Host:** Hetzner VPS 46.225.110.190, Ubuntu 24.04, 3.7GB RAM + 2GB swap.
**Live code:** `/opt/fortress/` (deployed by cp/SFTP — NOT a git checkout).
**Git:** `github.com/conorw1029-art/quant-research-fortress` (⚠ still PUBLIC as of this report).

| Service | Role | State 2026-07-02 |
|---|---|---|
| fortress-executor | 54-strategy signal engine + risk + (dormant) order routing | ACTIVE, DRY_RUN, entries gated on DATA_READY |
| fortress-yfinance | delayed OHLCV bars (1m…60m), 5-min loop | ACTIVE |
| fortress-dashboard | Flask :5050, SSE, kill switch, AI chat | ACTIVE (view public, actions token-gated) |
| fortress-watchdog | service/data/disk monitor + auto-restart + Telegram | ACTIVE (fixed 07-02: respects disabled units) |
| fortress-monitor, barreader, terminal | health report / NT8 JSONL reader / wetty :3000 | ACTIVE |
| fortress-tv-webhook | TradingView bar receiver :8765 | **STOPPED+DISABLED (deliberate — see §4)** |
| fortress-tradovate, copier, databento, ibkr | broker feed / copier / footprint poller / IBKR | STOPPED (no accounts / no funding) |

**Codebase:** `04_codebase/` ≈ 150+ Python files. Core execution stack ≈ 7,800 LOC
(executor 2,380; tradovate client 909; risk manager 789; coordinator 538; dashboard 620).
Strategy logic duplicated across `tick_strategies_v1–v9 + l2` (3,800 LOC) and
`src/strategies/` (44 modules). Research/backtest layer: zoo registry (300+ entries),
walk-forward pipeline, cost models, 13 stress-test batches.

## 3. Architecture & data flow (as reverse-engineered)

```
yfinance (delayed) ──┐
TV webhook (OFF)   ──┼→ 01_data/tick_bars/{SYM}_bars_{TF}m.parquet
Databento (OFF)    ──┘        │  re-read fully every 60s
                              ▼
        tick_live_executor.py  (single poll loop)
        gates: kill-switch → allowlist → SUSPENDED_IDS → DATA_READY
             → news window/bias → per-TF staleness → RiskManager.can_enter
             → PortfolioCoordinator (7 layers) → signal dedup
                              │
        RiskManager (ledger/ratchet/streaks)  ← restored from state files
        StateManager (atomic JSON: positions, brackets, halts, heartbeat,
                      daily_pnl, account_state, processed_signals)
                              │
        alerts → Telegram · signals_YYYYMMDD.jsonl · testing_pnl.json
        (DEMO/LIVE only: TradovateClient 13-gate bracket orders — dormant)
```

Order lifecycle (when a broker exists): signal → risk gate → coordinator →
`place_bracket_order()` (kill switch, tick rounding, direction, $200 risk cap, live-enable
env) → bracket confirm → state persist → copier mirrors leader fills (dormant).

## 4. Incident log — 2026-07-01/02 data corruption (found + fixed this review)

**Impact:** every 15m/30m parquet reduced to ≤1000 rows of per-minute, volume=1,
open==close garbage; the 2.5-year 30m history (the ONLY trusted backtest base) destroyed;
30m strategies were "forward validating" on noise.

**Causal chain (3 independent failures):**
1. The 20 TradingView alerts fire ~every minute with `volume=1` (misconfigured on the TV
   side — not bar-close alerts; `{{volume}}` broken), labeled as 3m/5m/15m/30m bars.
2. `tick_tv_webhook.py` accepted any bar for any timeframe with no grid validation, wrote
   synthetic L2 into the real footprint files, and trimmed everything to a flat 1000 rows.
3. `tick_watchdog.py` auto-restarted the webhook — which had been **deliberately
   stopped+disabled on 2026-06-30** — because it only checked is-active, never is-enabled.
   The safety system fought the operator and won.

**Remediation (deployed, commit `0787eb3`):** watchdog skips disabled/masked units;
webhook rejects off-grid bars, per-TF row caps (30k/60k), never writes `_l2_` files;
off-grid rows purged; history rebuilt via `tick_history_bootstrap.py` (30m ≈ 14.8k bars
Feb 2024→Jul 2026, ×4 symbols); synthetic L2 archived to `_synthetic_l2_archive_20260702/`.
**Residual action (Conor):** recreate the 20 TV alerts as true bar-close alerts before any
webhook re-enable; use the new token.

**Lesson encoded:** automation that "heals" must distinguish *crashed* from *decommissioned*;
data writers must validate cadence; retention policies must be timeframe-aware.

## 5. Fixes shipped in this review cycle (commits `0787eb3`, `0ba791e`, + 07-02 part 2)

1. **Crash-safe position restore.** positions.json now keyed by strategy id (symbol-keyed
   records collided when two strategies held the same symbol) and persists full trade state
   (stop/initial-stop/target/point-value/ratchet flags, updated on ratchet moves). Restart
   rebuilds RiskManager TradeRecords → stop/target/ratchet/timeout enforced across restarts
   (previously: tracked-but-unmanaged zombie positions). Unit-tested round trip.
2. **Risk-state persistence.** Daily P&L ledger, equity/peak, account halt, and
   consecutive-loss streaks now restore on startup from daily_pnl.json/account_state.json —
   the daily-loss halt can no longer be erased by a service restart (verified: -$650 day
   still blocks entries after restart). `breakeven` events no longer misread as full closes.
3. **Security rotation (2026-07-02).** VPS root password + DASHBOARD_TOKEN + TV_WEBHOOK_TOKEN
   rotated (old values were in public git history). New values only in
   `/root/CREDENTIALS_2026_07_02.txt` (mode 600) and `/opt/fortress/.env`.
   CLAUDE.md rewritten secret-free. Remaining for Conor: flip repo to PRIVATE;
   revoke/regenerate Telegram bot token via @BotFather.
4. `ACCOUNT_EQUITY` configurable via `FORTRESS_ACCOUNT_EQUITY` (dead 10-account premise).
5. `tick_gpt_bridge.py` — cross-model (ChatGPT) review loop for risk/strategy code.

## 6. Open risk register (ranked)

| # | Severity | Issue | Fix |
|---|---|---|---|
| 1 | HIGH | GitHub repo PUBLIC; old secrets in history | Conor: Settings→Danger Zone→Private. Rotation already done, so historical values are dead once Telegram token is regenerated |
| 2 | HIGH | Telegram bot token unrotated (server can't do it) | Conor: @BotFather → /mybots → Revoke; give new token to update .env |
| 3 | HIGH | wetty :3000 + dashboard :5050 exposed over HTTP to the internet | ufw allow SSH only; access dashboard/terminal via SSH tunnel or Tailscale; longer term keys-only SSH |
| 4 | MED | Contract specs/tick sizes defined 5× (`SPECS`, `MICRO_SPECS`, `_TICK_SIZE`, `_TICK_SIZE_MAP`, `MICRO_SYMBOLS`); stale thresholds 3×; expiry maps 2× | Create `fortress_specs.py` as single source; import everywhere; add startup-checklist gate asserting cross-module equality until migration completes |
| 5 | MED | 54-strategy PORTFOLIO as positional 8-tuples inside the executor; hand-maintained correlation ID sets | Move portfolio definition to YAML (allowlist already is); derive correlation clusters from the symbol field |
| 6 | MED | Executor monolith (2.4k LOC) mixes data loading, gating, execution, persistence, CLI | Extract: data_loader / gate_chain / execution_router modules. Do AFTER a forward-validation cycle, not during |
| 7 | MED | Live `/opt/fortress` vs git drift in ~60 research files; deploy = manual cp | One-time reconciliation pass, then make /opt/fortress a git checkout with a deploy branch |
| 8 | LOW | Full parquet re-read + full-series signal recompute for 54 strategies every 60s | Fine at current scale; incremental cache when symbol count grows |
| 9 | LOW | V9 FOMC/CPI hardcoded dates expire Dec 2026; TV_CONTRACT_MAP manual quarterly edit | Calendar refresh + rollover automation (cron exists for Sep/Dec) |
| 10 | LOW | Debris: patch_*.py, .bak/.BACKUP files, venv_new/, stress batches in repo root | Sweep into _archive/; add .gitignore rules |

## 7. Data strategy — the real-time Level-2 question, settled

The cost floor for legit real-time CME data is the exchange non-pro fee; vendors either
pass it through cheaply (brokers) or bundle margin (data companies). Options priced 2026:

| Route | Real-time? | L2 depth? | Cost | Verdict |
|---|---|---|---|---|
| **Ironbeam FCM API** | ✅ | ✅ full depth CME/COMEX/NYMEX/CBOT | **$0 data for non-pros** + tiny account | **THE PATH.** REST+WS, demo mirrors live, built for algo. Verify: demo data entitlement; aggressor side on trades (else tick-rule) |
| Rithmic (via broker) | ✅ | ✅ (MBO available) | ~$101/mo CME bundle | Solid but strictly worse than free |
| Databento Live | ✅ | ✅ | $179/mo + license | Discontinued pay-as-you-go for live CME; overkill |
| Databento Historical `trades` | ~8h delayed | footprint only | ~$49/mo | Good for dataset building / re-validation, NOT live signals |
| TopstepX/ProjectX | ✅ | ✅ | $14.50/mo | DEAD — requires a Topstep account |
| IBKR paper (unfunded) | ❌ delayed L1 | ❌ | — | Dead end (confirmed 06-28) |
| TradingView webhook + CME sub | ~real-time bars | ❌ OHLCV only | ~$5/mo | Only worth it after TV alerts are rebuilt correctly; unlocks 1m-15m OHLCV |
| Tradovate direct API | — | — | CME ILA $290-500/mo | Dead |

**Sequence:** (1) $0 now — yfinance forward validation of 30m strategies (delay irrelevant
at 30m holding horizons for validation purposes). (2) When validation passes: open Ironbeam
account → build `tick_ironbeam_feed.py` writing the existing parquet schema (real CVD via
aggressor side or tick-rule) → footprint/L2 strategies get their first honest live test.
(3) Databento historical only if multi-year footprint re-validation is needed.

## 8. Profitability assessment — the ruthless version

**Evidence against, from Fortress's own data:**
- 300+ zoo strategies → 1 confirmed survivor (fomc_drift, DSR 1.67), whose live WR decayed
  62%→32%→24% (2024→2026). The pre-FOMC drift is documented as gone post-2015
  (Kurov/Wolfe/Gilbert, Finance Research Letters 2021; confirmed through 2024 in Applied
  Economics 2024) — attributed to collapsed announcement-day VIX uncertainty.
- The 15m "survivors" carry session-hour filters tuned on the same ~70 days they're scored
  on. Filtered Sharpe 7–12 is not an edge, it is curve-fitting. Treat every `filt_Sharpe`
  as fiction; only the 30m WFO results (2.5yr, ~25 folds, DSR 1.5–3.4) merit forward testing.
- Live paper: -$1,450 over 10 days, 13% WR (delayed data — mitigating, but the only OOS
  evidence in existence is negative).
- L2 survivors: DSR 0.36–0.51 at 38–42% WR — one extra tick of slippage erases them.
- Cost drag: ~$6 RT commission + 1-2 ticks slippage ≈ 30–60% of a typical 15m ATR edge on micros.

**Evidence for:**
- The validation machinery itself (DSR, WFO, worst-day, cost scenarios, slippage modeling)
  is genuinely institutional-grade discipline — it keeps *catching* the overfitting.
- 30m OHLCV strategies (overnight_gap_fill ES/NQ, donchian NQ, vwap_mean_reversion) rest on
  2.5 years of data with plausible mechanisms; several confirmed across independent runs.
- Infrastructure risk (crashes, zombie positions, halt amnesia) is now largely engineered out.

**Industry base rates:** Quantopian's 888-strategy study: backtest Sharpe ≈ zero predictive
power for live performance; over-optimized strategies lose ~80% of backtest profit live.
Prop-firm funnel (if ever revisited): ~14/100 pass evaluations, ~7/100 ever get paid;
Topstep's own 2025 numbers: 16.8% of Combines completed, 33% of funded traders ever paid.

**Bottom line:** Long-term profitability is *possible* but currently *unproven and
improbable without discipline*. The single decision that determines everything: whether the
30m portfolio's forward P&L (testing_pnl.json, now on clean data) tracks its backtest over
the next 4–6 weeks. Everything else — data spend, broker accounts, sizing, more strategies —
is downstream of that one measurement. Expected realistic outcome even on success: modest
absolute returns at micro sizing (single-digit % monthly on small capital, with drawdowns);
this is an edge-compounding research platform, not near-term income.

## 9. Roadmap (gated — do not skip gates)

- **Gate 0 (done 07-02):** clean data, crash-safe state, rotated secrets.
- **Gate 1 (Conor, this week):** repo → private; Telegram token rotate; firewall :3000/:5050;
  fix 20 TV alerts (bar-close, correct volume) — optional TV CME sub (~$5/mo) later.
- **Gate 2 (4–6 weeks, $0):** forward-validate ONLY the 30m survivors (IDs 33, 37, 38, 45,
  46 + fomc_drift as a control that should keep failing). `touch /opt/fortress/DATA_READY`
  to open DRY_RUN entries on delayed data. Weekly: compare testing P&L vs backtest
  expectancy; kill anything >1.5σ under.
- **Gate 3 (only if Gate 2 passes):** Ironbeam account + feed adapter → real-time validation;
  1m–15m and L2 strategies get their first honest test. Consolidate specs module (risk #4)
  before any live order code path is re-enabled.
- **Gate 4 (only if Gate 3 passes, months out):** smallest personal live account, 1 micro,
  quarter-Kelly, 3-month tuition budget defined in advance. Prop firms only reconsidered
  with their VPS/automation ToS in writing.
- **Continuous:** HMM regime gate + VIX/DXY filters on the 30m portfolio (research already
  scoped 06-26); COT weekly bias for GC/SI (free CFTC data); no new strategy enters the
  allowlist without WFO on ≥2yr data + conservative cost scenario.

## 10. Two-model review loop

`tick_gpt_bridge.py` lets Claude pipe any analysis/diff to ChatGPT for adversarial review
(`OPENAI_API_KEY` required in .env). Use it on: risk-manager changes, new strategy
promotions, and every Gate decision above. ruflo MCP is registered for orchestration
experiments (`claude mcp remove ruflo` to drop it; it runs third-party npm code — keep it
away from anything touching credentials).
