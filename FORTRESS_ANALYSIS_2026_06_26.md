# Fortress System — Complete Analysis & Action Plan
**Date:** 2026-06-26  
**Author:** Claude Sonnet 4.6 + Opus research agents  
**Status:** Living document — update on each major session

---

## EXECUTIVE SUMMARY

Fortress is a 54-strategy automated futures trading system for GC/SI/ES/NQ on 10 Topstep prop accounts. It runs on a Hetzner VPS (46.225.110.190) in DRY_RUN mode. The primary blocker for going live is: **no Tradovate API keys** (pending TakeProfit/Tradeify) and **no real-time data source** (IBKR account in progress). All code is now on GitHub at github.com/conorw1029-art/quant-research-fortress.

Today's session: 5 bugs fixed, comprehensive system analysis completed, action plan written.

---

## BUGS FIXED — CUMULATIVE HISTORY

### Session 2026-06-23
- ✅ News monitor conservative fallback: ForexFactory failure now BLOCKS (was silently allowing)
- ✅ Stale bar detection now BLOCKS new entries per timeframe
- ✅ State persistence: executor saves positions to correct StateManager level
- ✅ Startup position restore: executor restores open positions from positions.json on restart

### Session 2026-06-24
- ✅ TV webhook curly/smart quote normalization (TradingView editor autocorrects quotes → invalid JSON)
- ✅ TV webhook JSON key newline parse bug fixed
- ✅ All 20 TradingView alerts created (4 symbols × 5 timeframes)
- ✅ Nginx reverse proxy: port 80 → TV webhook (TradingView requires port 80)

### Session 2026-06-26 Part 1
- ✅ Strategies 52 (v6→v8), 53 (v7→v6), 54 (v7→v6) — wrong version assignments causing "not found" errors every pass
- ✅ News monitor always-block bug: `refresh()` returned None instead of bool, always triggered conservative block

### Session 2026-06-26 Part 2 (this session)
- ✅ **Executor crash on 0-byte parquet** — `load_bars()` / `load_bars_l2()` now catch `ArrowInvalid` and return None (race condition: tv-webhook truncates parquet during write, executor reads at that instant → crash)
- ✅ **TV webhook memory leak** — `_upsert()` was reading full parquet history on every bar write; trimmed to last 1000 rows (was peaking at 601MB; now bounded)
- ✅ **IBKR gate** — executor suspends new entries until `/opt/fortress/IBKR_READY` file is created (but still manages open position exits)
- ✅ **TV webhook Telegram silent failure** — reads `TELEGRAM_TOKEN` but .env has `TELEGRAM_BOT_TOKEN`; fixed with env var fallback
- ✅ **Signal log bloat** — stale_data_block events were logged to signal file every 60s (15,000+/day, 15MB yesterday); now suppressed to stdout only
- ✅ **IBKR gate blocked exits** — original gate returned before `check_all_strategies`, so open DRY_RUN positions could never exit; now passes `block_new_entries=True` instead, allowing exits while blocking new entries

---

## ACTIVE BUGS & ISSUES

### CRITICAL (could cause real money loss when live)

**C1. Trailing DD resets on executor restart**  
File: `tick_live_executor.py:1969` — `RiskManager` always initialises with `starting_equity=49000.0` hardcoded. On crash+restart, trailing drawdown resets to $0 regardless of real equity. In DRY_RUN this is benign; in LIVE mode this is a safety gap.  
Fix: On startup in DEMO/LIVE mode, query Tradovate for real account equity and set `rm.account.equity` accordingly. Add `sm.save_account_state()` after every equity update so state persists across restarts.

**C2. No HTTPS on any endpoint**  
Dashboard (5050), TV webhook (8765), web terminal (3000) are all plain HTTP. Credentials, strategy details, kill switch accessible over open internet.  
Fix: Nginx SSL termination with Let's Encrypt, or at minimum basic-auth + firewall whitelist by IP.

**C3. Flask dev server for TV webhook**  
`tick_tv_webhook.py` runs Flask's built-in dev server ("Do not use in production deployment"). Under sustained load (all 20 alerts firing simultaneously on bar close) this can drop requests.  
Fix: Replace with `gunicorn -w 2 tick_tv_webhook:app`. Add to service unit.

**C4. Tradovate credentials in .env plain text**  
COPIER_LEADER/FOLLOWER passwords are in `/opt/fortress/.env` as plain text. If VPS is compromised, all prop accounts are exposed.  
Fix: At minimum, restrict .env permissions (`chmod 600 /opt/fortress/.env`). Longer term, use a secrets manager or encrypted vault.

### HIGH (incorrect behaviour, not money-loss risk yet)

**H1. account_state.json stale since 2026-06-24**  
The state file shows `equity: 49000.0` from June 24 and never gets updated. The executor never calls `sm.save_account_state()` in the main loop.  
Fix: Add `sm.save_account_state()` call after each equity update in the risk manager.

**H2. ANTHROPIC_API_KEY missing from .env**  
The AI terminal in the dashboard (`/api/chat`) and the AI health monitor (`tick_ai_monitor.py`) both need this. Without it: dashboard AI terminal is disabled; monitor sends plain-text health checks instead of Claude-powered analysis.  
Fix: Add `ANTHROPIC_API_KEY=sk-ant-...` to `/opt/fortress/.env`. Use `claude-opus-4-8` model for best brain.

**H3. 60m parquet files are stale and unused**  
`{GC,SI,ES,NQ}_bars_60m.parquet` (2.4MB total) were written by the initial history bootstrap on Jun 15 and haven't been updated since. No strategy uses 60m bars. They waste disk and mislead any audit.  
Fix: Delete them or add 60m bars to TradingView alerts if needed for future strategies.

**H4. No rate-limit handling in yfinance updater**  
Yahoo Finance silently rate-limits. If `fortress-yfinance` hits limits, bar data goes stale without any error or Telegram alert.  
Fix: Add retry logic with exponential backoff and a Telegram alert if >3 consecutive failures.

**H5. V9 calendar dates expire December 2026**  
FOMC dates hardcoded to 2026-12-09, CPI dates same. In January 2027 all V9 strategies silently stop firing.  
Fix: Add 2027 dates each December or scrape from BLS/Fed calendar automatically (cron job).

**H6. Lucid follower (LTT024LOBH5) permanently unautomatable**  
Lucid confirmed "we do not provide API keys". This follower account can never be part of the automated copier.  
Fix decision needed: (a) manual-only, (b) switch to a different prop firm that allows API access, (c) use NinjaTrader/Rithmic path.

**H7. Apex account APEX_496623 deactivated**  
Hit trailing DD limit or had trades past 4:59 PM ET on 2026-06-03. Needs manual re-signup before any automation.  
Fix: Re-signup at apextraderfunding.com (Rithmic-based → can use `ninjatrader_adapter.py`).

**H8. GC/30m TradingView alert reported dead**  
Noted in prior session as not sending bars. Needs manual verification in TradingView alert manager.  
Fix: Open TradingView, check COMEX:GC1!/30m alert, re-create if expired.

### MEDIUM (suboptimal, missing feature)

**M1. Watchdog doesn't monitor itself (no external health check)**  
If `fortress-watchdog` dies, nothing restarts it and no alert fires. Systemd `Restart=always` handles single crashes but not persistent failures.  
Fix: Add a simple external cron ping (`curl` to dashboard `/api/snapshot`) from a different process that alerts if the response fails.

**M2. Dashboard memory — running 9+ days without restart**  
`fortress-dashboard` uses 306MB and has been running since Jun 17. Flask in production can have memory leaks; at 9 days it's already higher than expected.  
Fix: Add weekly restart cron for the dashboard or run behind gunicorn with worker recycling.

**M3. No data sanity checks (price spike detection)**  
If yfinance or TradingView delivers a corrupt bar (e.g., GC at $0 or $99,999), strategies will compute signals from bad data. No price sanity checks exist.  
Fix: In `_upsert()` and `load_bars()`, validate OHLCV: O/H/L/C must be positive, H≥L, H≥O/C, L≤O/C. Reject bars outside ±10% of previous close.

**M4. Processed signals dedup file only has 3 entries**  
After restarts, `processed_signals.json` resets. This could cause duplicate order submission if the executor restarts mid-trade in LIVE mode.  
Fix: Don't truncate processed_signals.json on restart; load and merge existing entries.

**M5. No intraday equity tracking in DRY_RUN**  
DRY_RUN P&L is split across daily_pnl.json (realized, persisted) and executor in-memory (unrealized). After restart, the in-memory state shows only positions restored from file, not cumulative P&L for the day. The display "Portfolio today: -$64.80" and daily_pnl.json "$651.33" are tracking different things.  
Fix: Load today's realized P&L from daily_pnl.json on startup and add it to the risk manager's equity tracker.

**M6. No correlation-aware portfolio sizing**  
GC/SI are tightly correlated (~0.8); ES/NQ are tightly correlated (~0.95). Running simultaneous positions in both pairs effectively doubles risk. No cross-strategy correlation cap exists.  
Fix: In `PortfolioCoordinator`, add a per-correlation-cluster net position limit (e.g., max 1 GC + 1 SI combined, max 1 ES + 1 NQ combined).

**M7. No Topstep consistency rule enforcement**  
On the Consistency Payout path, no single day can exceed 40-50% of total profit. If one strategy has a huge day, it could disqualify the payout. No code enforces this.  
Fix: Add daily profit ceiling (configurable %) to RiskManager; once reached, block new entries for the day.

**M8. `daily_loss_remaining` field confusingly named**  
In `daily_pnl.json`, `daily_loss_remaining: -1251.33` looks alarming but means "you can sustain $1251 more losses before halt". The negative sign is confusing.  
Fix: Rename to `halt_pnl_threshold` and add a positive `daily_loss_capacity` field.

**M9. No TV webhook authentication**  
Anyone who discovers the webhook URL can POST fake bars and manipulate the executor's data. The optional `TV_WEBHOOK_TOKEN` env var is not set.  
Fix: Set `TV_WEBHOOK_TOKEN` in `.env` and add `?token=...` to all 20 TradingView alert URLs.

---

## STOPPING POINTS — WHAT'S BLOCKING LIVE TRADING

| Blocker | Status | Action |
|---|---|---|
| Tradovate API keys — TakeProfit | Requested 2026-06-24, no reply | Email sent to support@takeprofittrader.com; chase after 48hr |
| Tradovate API keys — Tradeify | Requested 2026-06-24; use chat widget | https://help.tradeify.co chat widget |
| IBKR paper account | Email sent 2026-06-26 | Wait for response, then run tick_ibkr_setup.sh |
| IBKR_READY gate | File does not exist | `touch /opt/fortress/IBKR_READY` once IBKR data flowing |
| Apex re-signup | Account deactivated Jun 3 | Manual re-signup at apextraderfunding.com |
| Lucid API keys | Dead end — they don't provide them | Accept as manual-only or switch firm |
| TradingView CME real-time data | 10-min lag; costs ~$4-6/month | Subscribe in TradingView account settings |

---

## DATA ARCHITECTURE — CURRENT STATE & GAPS

### What's flowing now
| Source | Symbols | TFs | Lag | Strategies enabled |
|---|---|---|---|---|
| TradingView webhook (20 alerts) | GC/SI/ES/NQ | 1m/3m/5m/15m/30m | 10min (CME delayed) | 15m/30m only (stale detector blocks 1m-5m) |
| yfinance updater (every 5 min) | GC/SI/ES/NQ | 1m/3m/5m/15m/30m | 15min (free tier) | All OHLCV strategies |

### Pending data sources
| Source | When | Unlocks |
|---|---|---|
| IBKR L1 (real-time) | IBKR account + `touch IBKR_READY` | All 54 strategies at correct latency |
| IBKR L2 DOM (real-time) | Same as above | V10 L2 microstructure strategies (IDs 40-44) |
| TradingView CME real-time (~$4-6/mo) | Pay the add-on | 1m/3m/5m strategies via TV webhook |
| Databento historical | Research budget | Proper walk-forward on L2 data; backtesting V10 on real MBO data |

### Data quality issues
- `*_bars_60m.parquet` files unused and stale (delete recommended)
- All working parquets are 40-100KB (1000-row trim working correctly)
- No price sanity validation (see M3)

---

## STRATEGY INVENTORY SNAPSHOT

### Currently running in DRY_RUN (post-IBKR gate: entries BLOCKED, exits active)
- 54 strategies loaded, all REVIEW_REQUIRED or better
- 3 ENABLED_DRY_RUN, 1 DEMO_CANDIDATE
- Open positions: strategy 35 (NQ SHORT @ 29319.25), strategy 51 (SI SHORT @ 59.11)
- Today's realized P&L: **+$651.33** (DRY_RUN)

### V9 Calendar event dates (needs annual update)
- FOMC: hardcoded to 2026-12-09 (expires Dec 2026)
- CPI: hardcoded to 2026-12-09 (expires Dec 2026)
- NFP: auto-computed via first-Friday formula (never expires)
- Action: Update FOMC/CPI dates each December

---

## STRATEGIC IMPROVEMENTS — RESEARCH FINDINGS

*From Opus research agent + internet research, 2026-06-26*

### Top 10 Strategy Additions (ranked by priority)

**1. HMM Regime Classifier (master switch)**  
A 3-4 state Hidden Markov Model on 30m returns + realized vol labels Trend/Range/HighVol/Crash. Route capital: trend-following in trending states, mean-reversion in ranging states, cut gross in turbulent states. Single biggest portfolio improvement — directly addresses trend vs. mean-reversion timing. Uses data you already have.  
Tool: `hmmlearn` Python package. Train on 30m bars.

**2. COT Positioning Filter for GC/SI**  
Fade extreme managed-money positioning when diverging from commercials. Free CFTC weekly data (Tue data, Fri release). 60-70% win rate on 1-3 month signals. Use as directional bias gate on existing GC/SI strategies, not standalone.  
Data: https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm

**3. GEX/Dealer Gamma Levels for ES/NQ**  
Positive-GEX regime → price pins to high-gamma strikes (fade extremes); negative-GEX → moves accelerate (breakout/momentum). Acts as a *regime switch* for existing V6-8 ES/NQ strategies.  
Data: SpotGamma, MenthorQ (paid); or self-compute from CME options open interest.

**4. Cross-Asset Confirmation Layer**  
VIX filter for ES/NQ (trade only when VIX aligned with direction); DXY filter for GC/SI. Reportedly lifts win rate ~48%→55% on ES alone. Zero new strategy code — a filter/gate layer on existing signals.

**5. Overnight Range / Gap Statistics**  
Overnight-high breakout ~72%, gap-fill ~81% (same-session ES), RTH-open vs overnight-mid predicts direction ~76%. Implement as a daily directional bias gate and standalone gap-fill strategy.  
Works on 30m data available now.

**6. Order Flow Imbalance (OFI) Predictor (needs IBKR L2)**  
Near-linear relationship between OFI and short-horizon price changes. More rigorous than current depth-imbalance V10. Design for 30-second to 2-minute signal persistence (retail IBKR latency can't compete on microseconds).

**7. VWAP + High Volume Node Confluence**  
Anchored VWAP + Volume Profile High Volume Node at same price = institutional defense zone. Fade into zone, target VWAP. Works on 30m bars now.

**8. Gold Seasonality + DXY/Real-Rate Macro Bias**  
Gold/real-yields ≈ -0.82 correlation, gold/DXY ≈ -0.45. Use as slow directional bias gate. Note 2025 regime break (gold +65% despite high real yields) driven by central-bank buying — weight DXY/CB-flow currently.

**9. Fractional Kelly Sizing**  
Current system uses fixed risk per trade. With $1k runway, use ¼-Kelly: survives bad sequences while still compounding. Compute per-strategy Kelly from live win-rate/payoff in daily_pnl.json, scale by regime confidence.

**10. Volatility-Scaled Stops / Position Sizing**  
Key stops from VIX bucket (VIX 12-15 → smaller stop; VIX 30+ → wider stop). Prevents getting stopped out in high-vol regimes and keeps risk constant in dollar terms.

### AI Brain Architecture (layered)

```
Layer 1: Regime Engine
  → HMM state (Trend/Range/HighVol/Crash)
  → GEX state (positive/negative/flip)
  → VIX bucket (low/mid/high/extreme)
  → COT bias (GC/SI only)
  Output: discrete market state every 30m bar

Layer 2: Signal Ensemble
  → All 54 existing strategies, each gated by allowed regime states
  → New strategies (HMM, gap, GEX, OFI)
  Output: candidate signals with regime-filtered confidence

Layer 3: Narrow ML (not price prediction)
  → LLM (Claude) news sentiment scorer on economic headline feed
    → Sentiment state that gates risk around events
    → Claude API, claude-opus-4-8, classify/summarize only
  → Quantile vol forecaster for adaptive stop placement
  Output: event gate + vol band for sizing

Layer 4: Position Sizer
  → ¼-Kelly × regime confidence
  → Topstep consistency cap (daily profit ceiling)
  → Correlation cluster cap (GC+SI = 1 unit, ES+NQ = 1 unit)
  Output: contract count (max 1 micro per account)

Layer 5: Execution
  → Passive-limit-first for non-urgent entries
  → Suppress resting orders during V9 event windows
  → TWAP slicing for SI/GC overnight
```

### Risk Management Improvements

1. **¼-Kelly sizing** — full Kelly draws down 30-50% even with positive expectancy
2. **Topstep EoD trailing MLL mechanics** — bank intraday gains to ratchet MLL up; once MLL locks at $0 (at +$2k balance on $50k accounts) the hard stop disappears
3. **Daily profit cap** — enforce Topstep consistency rule (max 40-50% of total profit in single day)
4. **Correlation cluster cap** — GC+SI = 1 risk unit, ES+NQ = 1 risk unit
5. **Regime-gated gross exposure** — cut gross exposure when HMM turbulent-state probability rises

### Execution Quality Improvements

1. **Passive-limit-first** — resting bids/offers when spread is wide; cross only on urgency
2. **TWAP slicing** for less-liquid contexts (SI, GC overnight, around 30m bar closes)
3. **Suppress orders** into FOMC/NFP/CPI microstructure prints (V9 already flags these)
4. **OFI-conditional urgency** — when L2 shows thin book / wide spread, wait or use limits

---

## RECOMMENDED ACTION SEQUENCE

### Phase 0 — Right now (no new hardware/accounts needed)

- [x] Fix all 6 bugs from this session (committed `3f83887`, pending final commit)
- [ ] Add `ANTHROPIC_API_KEY` to `/opt/fortress/.env` — enables AI terminal + real monitor analysis
- [ ] Add `TV_WEBHOOK_TOKEN` to `.env` — secures webhook from fake bar injection
- [ ] Delete unused 60m parquet files (`rm /opt/fortress/01_data/tick_bars/*60m.parquet`)
- [ ] `chmod 600 /opt/fortress/.env` — protect plain-text credentials
- [ ] Subscribe to CME real-time data on TradingView (~$4-6/month) — unlocks 1m/3m/5m
- [ ] Verify GC/30m TradingView alert is alive (reported dead, needs check)
- [ ] Begin HMM regime classifier implementation (`tick_regime_classifier.py`)
- [ ] Begin COT data downloader (`tick_cot_downloader.py` — free CFTC weekly)
- [ ] Update V9 FOMC/CPI dates for 2027

### Phase 1 — When IBKR responds

- [ ] Open IBKR paper account
- [ ] Run `bash /opt/fortress/tick_ibkr_setup.sh`
- [ ] Edit IBKR credentials in IBC config
- [ ] Add IBKR env vars to `.env`
- [ ] `systemctl enable --now fortress-ibkr-gateway fortress-ibkr`
- [ ] Verify L1 data flowing in all 4 symbols
- [ ] `touch /opt/fortress/IBKR_READY` — executor starts generating signals
- [ ] Monitor first 5 trading days in DRY_RUN with IBKR data

### Phase 2 — When Tradovate API keys arrive

- [ ] Write CID/SECRET to `/opt/fortress/.env`
- [ ] Run `tick_credentials_test.py` — verify auth
- [ ] `systemctl start fortress-tradovate`
- [ ] Set executor to `--demo-auto-trade` for single strategy (ES/cvd_divergence_large_print)
- [ ] Start `fortress-copier` with `COPIER_DRY_RUN=true`
- [ ] Monitor 5 trading days in demo mode
- [ ] Conor explicitly approves flipping `COPIER_DRY_RUN=false`

### Phase 3 — Scale (after 60 days live)

- [ ] Enable ENABLED_DRY_RUN strategies to DEMO_CANDIDATE as track record builds
- [ ] Implement HMM regime classifier as strategy gating layer
- [ ] Add COT bias gate for GC/SI strategies
- [ ] Implement fractional Kelly (¼) position sizer
- [ ] Add cross-asset confirmation (VIX gate for ES/NQ, DXY gate for GC/SI)
- [ ] Subscribe to Databento for L2 history — properly backtest V10 strategies
- [ ] Consider Tradeify/additional accounts as API keys arrive

---

## PROP FIRM STATUS

| Account | Firm | Type | API Status | Notes |
|---|---|---|---|---|
| ConorWalsh1 | TakeProfit | LEADER | Requested 2026-06-24 | Use for all live trades |
| LTT024LOBH5 | Lucid | Follower | DEAD END | Manual-only; no API access |
| TDFYU439260492 | Tradeify | Follower | Requested; try chat widget | https://help.tradeify.co |
| APEX_496623 | Apex | Follower | DEACTIVATED | Re-signup needed (Rithmic-based) |
| + 6 Topstep | Topstep | Followers | Via copier (pending leader key) | |

---

## KEY COMMANDS (reference)

```bash
# VPS access
ssh root@46.225.110.190  # password: Fortress2026!
# or: http://46.225.110.190:3000 → type 'fortress' to attach tmux

# Enable signals (do after IBKR data confirmed flowing)
touch /opt/fortress/IBKR_READY

# Emergency stop
echo "STOP" > /opt/fortress/KILL_SWITCH.txt

# Service management
systemctl status fortress-executor
systemctl restart fortress-executor
journalctl -u fortress-executor -n 50 --no-pager

# Push to GitHub
cd ~/quant-research-fortress
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_fortress -o StrictHostKeyChecking=no" git push origin main

# Contract rollover (crons installed)
# Sep 10 08:00 UTC: U6 → Z6
# Dec 10 08:00 UTC: Z6 → H7
```

---

## DATA SOURCES — COMPLETE REFERENCE

| Source | URL | Cost | Data | Status |
|---|---|---|---|---|
| TradingView alerts | 20 alerts configured | Included in plan | GC/SI/ES/NQ OHLCV 1m-30m | Live (10-min lag) |
| yfinance | Auto via fortress-yfinance | Free | GC/SI/ES/NQ 1m-30m | Live (15-min lag) |
| IBKR API | tick_ibkr_bar_builder.py ready | ~$30/mo or free w/ commissions | Real-time L1+L2 all 4 symbols | Pending account |
| Databento | Historical API | $199/mo historical | Historical L2/MBO for backtesting | Not configured |
| CFTC COT | cftc.gov/MarketReports | Free | Weekly GC/SI/ES/NQ positioning | Not integrated |
| SpotGamma/MenthorQ | spotgamma.com / menthorq.com | ~$39-99/mo | GEX / dealer gamma levels | Not integrated |

---

*Generated: 2026-06-26 | Commit: pending | Next review: when IBKR account confirmed*
