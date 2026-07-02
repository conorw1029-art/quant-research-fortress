# Fortress Trading System — Master Context File

**Rewritten 2026-07-02.** Read this before doing anything. This file contains NO secrets —
credentials live in `/opt/fortress/.env` and `/root/CREDENTIALS_2026_07_02.txt` (both
outside git). NEVER add passwords, tokens, or API keys to this file or any committed file:
this repo was public with the old root password in it, forcing a full rotation.

For the complete engineering assessment see `FORTRESS_DIAGNOSTIC_2026_07_02.md` (repo root).
For session-by-session history see Claude's memory at `/root/.claude/projects/-root/memory/`.

---

## 1. WHO / WHAT

**Conor Walsh** — quantitative futures trader, traveling; conorw1029@gmail.com.
⚠️ **He no longer holds any Topstep/funded prop accounts (as of 2026-06-30).** Anything in
older docs/code assuming "10 Topstep accounts", a leader/follower copier, or $49k combined
equity is a dead premise. Current phase: **research + DRY_RUN validation, no execution account.**

Fortress is a 24/7 automated futures research/trading system on a Hetzner VPS
(46.225.110.190, Ubuntu 24.04). 54 registered strategies across GC/SI/ES/NQ; executor
runs in **DRY_RUN** (no orders, no broker). Live code: `/opt/fortress/` (NOT a git clone).
Git copy: `/root/quant-research-fortress/` — **edit live → test → restart service → cp to
git → commit → push** (SSH key `~/.ssh/github_fortress`).

## 2. CURRENT STATE (2026-07-02)

- Services active: executor, dashboard, watchdog, monitor, yfinance, barreader, terminal.
  **fortress-tv-webhook: STOPPED+DISABLED on purpose** (see §4). tradovate/copier/databento: stopped.
- Executor gates: entries suspended until `/opt/fortress/DATA_READY` exists. Kill switch: RUN.
- Data: yfinance (delayed 10-15 min) keeps 1m/3m/5m/15m/30m/60m parquets fresh.
  30m history = 2.5yr (restored 2026-07-02 after corruption incident). Only 30m backtests are
  statistically trusted.
- Dashboard: port 5050, view public, halt/resume/chat token-protected (`DASHBOARD_TOKEN` in .env;
  bookmark URL in `/root/CREDENTIALS_2026_07_02.txt`).

## 3. HARD INVARIANTS

- No real orders without `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` + explicit fresh approval from Conor.
- `COPIER_DRY_RUN=true` until Conor explicitly says otherwise (copier currently pointless — no accounts).
- `USE_MICROS=True`; max $200 risk/trade; 1 contract.
- Kill switch (`/opt/fortress/KILL_SWITCH.txt` = STOP) is the first gate everywhere.
- The `*_bars_l2_*.parquet` files are RESERVED for real footprint/L2 data (Databento/IBKR/NT8).
  Nothing may write synthetic values there.
- Never commit secrets. Never trust `filt_Sharpe` numbers (in-sample session filters = overfit).

## 4. THE 2026-07-01 DATA CORRUPTION INCIDENT (read before touching data or watchdog)

The 20 TradingView alerts are **misconfigured**: they fire ~every minute with volume=1 and
open==close, labeled as 3m/5m/15m/30m bars. The webhook wrote them all; its old flat
1000-row trim then destroyed the 2.5-year 30m history. The watchdog kept auto-restarting
the webhook even though it had been deliberately disabled. All three failures are fixed
(commit 0787eb3): watchdog respects disabled units; webhook rejects off-grid bars, uses
per-tf row caps, never writes `_l2_` files; history rebuilt via `tick_history_bootstrap.py`.
**Before re-enabling the webhook, Conor must recreate the TV alerts as true bar-close alerts
per timeframe chart** (and use the new `TV_WEBHOOK_TOKEN` from the credentials file).

## 5. DATA STRATEGY (decided direction, 2026-07-02)

- **Now ($0):** yfinance delayed bars → forward-validate the trusted 30m strategies in DRY_RUN.
- **Real-time L2 when ready: Ironbeam** (FCM, free real-time CME/COMEX Level-2 for non-pros,
  REST+WebSocket API, demo mirrors live; docs.ironbeamapi.com). Verify at signup: demo data
  entitlements + whether trade feed exposes aggressor side (else tick-rule classify like
  `tick_processor.py`).
- Databento Historical (`trades`, ~$49/mo) = ~8h delayed — dataset building only. Databento
  real-time = $179/mo+ (not worth it). TopstepX/ProjectX = dead (needs Topstep account).
  IBKR paper = delayed L1 only. Tradovate direct API = dead (CME ILA cost).
- CVD aggressor convention (Databento): side 'B'=BUY aggressor, 'A'=SELL. Do NOT re-flip
  (commit 495824a).

## 6. KEY PATHS / COMMANDS

- Code: `/opt/fortress/04_codebase/` · venv: `/opt/fortress/venv/bin/python` (system python has no pandas)
- Bars: `/opt/fortress/01_data/tick_bars/` · state: `/opt/fortress/06_live_trading/state/`
- Health: `systemctl is-active fortress-executor fortress-dashboard fortress-watchdog fortress-yfinance`
- Logs: `journalctl -u fortress-executor -f` · pre-flight: `venv/bin/python tick_startup_checklist.py`
- Reattach session: web terminal :3000 → `fortress` (tmux). Login = root + password from
  `/root/CREDENTIALS_2026_07_02.txt`.
- Second opinion from ChatGPT: `venv/bin/python 04_codebase/tick_gpt_bridge.py --prompt "..."`
  (needs `OPENAI_API_KEY` in .env).

## 7. WHAT CLAUDE SHOULD DO ON SESSION START

1. Load memory (MEMORY.md index → latest session file).
2. Check services + executor journal (last 20 lines) + heartbeat freshness.
3. Confirm git in sync (`cd ~/quant-research-fortress && git status`).
4. Report state + single most important next action. Don't start stopped services without
   checking WHY they're stopped (`systemctl is-enabled` — disabled = operator decision).

## 8. OPEN ITEMS (full list in FORTRESS_DIAGNOSTIC_2026_07_02.md)

- Conor: make GitHub repo PRIVATE; rotate Telegram bot token via @BotFather; fix 20 TV alerts.
- Consolidate duplicated contract-spec/tick-size dicts into one module (see diagnostic §6).
- Forward-validate 30m survivors 4-6 weeks on clean data before any spend/live decision.
- V9 FOMC/CPI hardcoded dates expire Dec 2026. Contract rollover: Sep 10 (U6→Z6, cron installed).
