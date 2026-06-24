# Fortress Trading System — Master Context File

**Read this entire file before doing anything.** This is a live quantitative trading system running 24/7 on a Hetzner VPS. This file is the single source of truth — it was written to allow any Claude instance (Windows, iPad, server) to pick up exactly where the last session left off without asking questions.

---

## 1. WHO IS THE USER

**Conor** — quantitative trader running funded prop firm accounts, currently traveling/abroad. His personal PC cannot be relied on to stay running.

- **Email:** conorw1029@gmail.com
- **Telegram:** bot token `8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA`, chat_id `8483433910`
- **10 funded Topstep accounts**, ~$1,000 drawdown limit each, personal max DD $2,000/account
- **4 broker accounts** — all accessed via Tradovate login:
  - TakeProfit Trader → username `ConorWalsh1` (LEADER account — copier sources trades from here)
  - Lucid → username `LTT024LOBH5`
  - Tradeify → username `TDFYU439260492`
  - Apex → username `APEX_496623`
- **Platforms:** Tradovate (all 4 above) + NinjaTrader/Rithmic (separate, not connected to this system yet)
- **PC:** Windows 10, `C:\Users\conor\Desktop\quant-research`, SSH key at `C:\Users\Conor\.ssh\fortress_deploy`

---

## 2. THE SYSTEM IN ONE PARAGRAPH

Fortress is a server-side automated trading system. 43 strategies monitor GC (Gold), SI (Silver), ES (S&P 500), and NQ (Nasdaq) futures. When a strategy fires a signal, the executor places a bracket order on the leader Tradovate account, and the trade copier mirrors that fill to all 3 follower accounts. A web dashboard shows live P&L. Everything runs as systemd services on a Hetzner VPS — it runs 24/7 even when Conor's PC is off.

**Current state as of 2026-06-20:** The system is running in DRY_RUN mode (signals + Telegram only, no real orders placed). The single blocker to going fully live is Tradovate API keys (cid + sec). API Access could not be found on the Tradovate platform — it is likely disabled by the prop firms. Emails requesting API access have been sent to all 4 prop firms (TakeProfit, Lucid, Tradeify, Apex). See Section 5 for full context and the email template used.

---

## 3. LIVE SERVER

**Hetzner VPS: `46.225.110.190` (Ubuntu 24.04)**

| Access method | Details |
|---|---|
| SSH (terminal) | `ssh root@46.225.110.190` password `Fortress2026!` |
| Web terminal (iPad) | http://46.225.110.190:3000 login: root / Fortress2026! |
| Dashboard | http://46.225.110.190:5050 |
| Code path | `/opt/fortress/` |
| Python venv | `/opt/fortress/venv/bin/python` |
| Services run as | user `fortress` |
| Credentials file | `/opt/fortress/.env` (never in git) |

**Windows paramiko key:** `C:\Users\Conor\.ssh\fortress_deploy` — this is an **Ed25519 key**. ALWAYS load it with `paramiko.Ed25519Key.from_private_key_file()` — never `RSAKey` (RSAKey throws `unpack requires a buffer of 4 bytes`).

**Never write `.env` via SSH heredoc** — `$` characters get corrupted. Always write it via SFTP direct write: `sftp.open('/opt/fortress/.env', 'w').write(content)`.

---

## 4. THE 8 SYSTEMD SERVICES

```
fortress-yfinance   ACTIVE   — downloads GC/SI/ES/NQ OHLCV bars every ~5 min (15-min delayed, free tier)
fortress-executor   ACTIVE   — runs 43 strategies, sends Telegram signals. Currently DRY_RUN (no orders)
fortress-barreader  ACTIVE   — watches live/ dir for JSONL bars, appends to parquet files
fortress-dashboard  ACTIVE   — Flask web UI at :5050 (signals, P&L, kill switch)
fortress-terminal   ACTIVE   — wetty web terminal at :3000
fortress-monitor    ACTIVE   — hourly Telegram AI health report (tick_ai_monitor.py)
fortress-tradovate  STOPPED+DISABLED — real-time WebSocket feed. BLOCKED on missing cid/sec (see Section 5)
fortress-copier     STOPPED+DISABLED — leader→follower trade copier. Same blocker.
```

**Quick service commands:**
```bash
# Check all 8 at once
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor fortress-tradovate fortress-copier

# Live signal stream
journalctl -u fortress-executor -f

# Restart a service
systemctl restart fortress-executor

# Dashboard API (read-only check)
curl -s http://localhost:5050/api/snapshot | python3 -m json.tool | head -60
```

---

## 5. THE ONE BLOCKER: TRADOVATE API KEYS

**Root cause confirmed 2026-06-16.** The Tradovate REST API (`POST /auth/accesstokenrequest`) requires a `cid` (Client ID, a number) and `sec` (Secret, a string) generated per-account on the Tradovate web platform. `/opt/fortress/.env` currently has `TV_CID=0` and `TV_SECRET=` (empty) — placeholders never replaced. This is why all API auth fails even though browser login works.

**This is not a lockout. It is not a credential typo. The API was simply never provisioned.**

### Current status of this blocker (updated 2026-06-20)

Conor tried to find API Access on the Tradovate platform and could not locate it. This is almost certainly because the **prop firms (TakeProfit, Lucid, Tradeify, Apex) have disabled API access** on their funded accounts — this is common practice to prevent certain automated strategies.

**Emails have been sent to all 4 prop firms requesting API access.** We are waiting for replies.

### Email template that was sent (for reference):

> **Subject:** API Access Request — Funded Account [USERNAME]
>
> Hi,
>
> I'm a funded account holder with [Firm Name] and I'd like to enable API access on my Tradovate account. I'm running a personal automated trading system and need the Tradovate API credentials (Client ID and Secret) to connect it to my account. I understand this is accessed via Settings → API Access on the Tradovate platform, but I'm unable to locate this option — I believe it may need to be enabled on your end.
>
> Could you please either enable API access on my account so I can generate my own CID and Secret, or let me know the process for obtaining API credentials for funded accounts.
>
> My account details: Username [USERNAME], Account type: Funded.
>
> Thanks, Conor

**Sent to:**
- TakeProfit Trader — support@takeprofittrader.com — username `ConorWalsh1`
- Lucid — their support/Discord — username `LTT024LOBH5`
- Tradeify — support@tradeify.co — username `TDFYU439260492`
- Apex — support@apextraderfunding.com — username `APEX_496623`

### When a prop firm replies

If they provide a CID + SEC: send values to Claude with "update the env file" and Claude will SFTP them in immediately, then proceed with the activation sequence in Section 6.

Format to send Claude:
```
TakeProfit: cid=XXXX sec=XXXXXXXXXX [api_password=XXXXXXXX if they require a separate API password]
```

If they say API access is not available: we switch to Polygon.io for real-time data (free sign-up, Claude handles the integration) and investigate alternatives for order execution.

---

## 6. ACTIVATION SEQUENCE (after API keys are received)

Do these steps in order. Do NOT skip ahead.

### Step 1 — Update .env
Claude writes all 4 accounts' cid/sec into `/opt/fortress/.env` via SFTP.

The relevant env var names:
```
TRADOVATE_CID=<leader cid>
TRADOVATE_SECRET=<leader sec>
COPIER_LEADER_PASS=<leader api_password if different from login password>
# (each follower account uses its own cid/sec when authenticating as the copier)
```

Note: the copier authenticates each account independently, so each account's cid/sec needs to be in the env file with separate variable names.

### Step 2 — Test auth (one call, NOT a service start)
```bash
cd /opt/fortress && python3 -c "
from dotenv import load_dotenv
load_dotenv('.env')
from tick_tradovate_client import TradovateClient
import os
c = TradovateClient(os.environ['COPIER_LEADER_USER'], os.environ['COPIER_LEADER_PASS'], demo=False)
ok = c.authenticate()
print('Auth OK:', ok, 'account_id:', c.account_id)
"
```
Expected: `Auth OK: True account_id: <number>`

### Step 3 — Start real-time data feed
```bash
systemctl enable fortress-tradovate
systemctl start fortress-tradovate
journalctl -u fortress-tradovate -f
# Wait for: [Feed] Authenticated (user=..., LIVE)
```
This starts streaming true real-time 1m bars into `/opt/fortress/01_data/tick_bars/live/`. The barreader service picks these up automatically and appends to the parquet files. Strategies immediately begin running on real-time data instead of 15-min delayed yfinance.

### Step 4 — Switch executor to live trading
```bash
# Edit the service file to add --live-auto-trade
nano /opt/fortress/server/fortress-executor.service
# Change: ExecStart=...tick_live_executor.py --poll 60
# To:     ExecStart=...tick_live_executor.py --live-auto-trade --poll 60

systemctl daemon-reload
systemctl restart fortress-executor
journalctl -u fortress-executor -f
# Watch for: [Executor] Mode: LIVE_AUTO_TRADE
```
Note: `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` is already set in `.env` — Conor explicitly authorized live trading on 2026-06-17. No additional confirmation needed for this step.

### Step 5 — Start trade copier (DRY_RUN first)
```bash
systemctl enable fortress-copier
systemctl start fortress-copier
journalctl -u fortress-copier -f
# Watch for: [Copier] Starting — DRY-RUN mode
# [Copier] Leader: TakeProfit: authenticated (account_id=...)
# [Copier] Seeded NNN existing fills
```
Run in dry-run for at least one trading session. Verify via logs and Telegram that it correctly detects fills and would copy them to all 3 followers. Only proceed to Step 6 after Conor explicitly says "looks good, enable real copying."

### Step 6 — Enable real copying (REQUIRES EXPLICIT USER APPROVAL)
```bash
# Only after Conor says "enable real copying" or equivalent
# Edit .env via SFTP:  COPIER_DRY_RUN=false
systemctl restart fortress-copier
```
**Do NOT flip `COPIER_DRY_RUN=false` without fresh explicit go-ahead from Conor in this conversation.**

---

## 7. EXECUTOR MODES EXPLAINED

The executor (`tick_live_executor.py`) supports 4 modes:

| Mode | How to start | Effect |
|---|---|---|
| **DRY_RUN** | `--poll 60` (no broker flags) | Signals + Telegram only. No broker connection. **Current mode.** |
| **MOCK** | `--mock --poll 60` | Uses in-process MockBroker. Simulates fills locally. |
| **LIVE** | `--live-auto-trade --poll 60` | Connects to real Tradovate, places real bracket orders. Requires `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` in env. |
| **DEMO** | `--demo-auto-trade --poll 60` | Paper trades on Tradovate's demo environment. |

**Kill switch:** Create `/opt/fortress/KILL_SWITCH.txt` containing the word `STOP`. The executor checks this on every pass and will flatten all positions and exit. Reset by writing `RUN` to the file. The dashboard at :5050 has a red STOP button that does this via the API (`POST /api/halt` and `POST /api/resume`).

---

## 8. STRATEGY UNIVERSE

**43 strategies across 4 markets:**

- **V1–V9** (IDs 1–39): OHLCV + CVD strategies. 39 total. Running on yfinance data (15-min delayed until Tradovate feed is live).
- **V10** (IDs 40–44): L2 microstructure strategies. Need real-time DOM/tick data from Tradovate feed. Currently show "no data" — will activate automatically once `fortress-tradovate` is running.

**Key files:**
- `04_codebase/live_strategy_allowlist.yaml` — which strategies are active and their parameters
- `04_codebase/tick_strategies_v1_v5.py`, `tick_strategies_v6_v9.py`, `tick_strategies_v10_l2.py` — strategy logic
- `04_codebase/tick_live_executor.py` — runs all strategies, manages positions, risk controls

**Risk controls in executor:**
- Per-strategy circuit breaker (3 consecutive losses → strategy paused)
- Daily P&L limit (default -$600/day → halt all new signals)
- Trailing drawdown limit ($800 → halt)
- Portfolio coordinator prevents simultaneous same-direction positions in correlated strategies

---

## 9. DATA PIPELINE

```
[Source 1] yfinance (15-min delayed, free)
    → downloads GC/SI/ES/NQ every ~5 min
    → writes to /opt/fortress/01_data/tick_bars/{SYM}_bars_{TF}m.parquet
    → service: fortress-yfinance (ACTIVE)

[Source 2] Tradovate WebSocket (real-time tick data + L2/DOM)
    → streams 1m bars + order book snapshots
    → writes to /opt/fortress/01_data/tick_bars/live/{SYM}_1m_live.jsonl
    → service: fortress-tradovate (STOPPED — needs cid/sec)

[Source 3] NinjaTrader FortressBarWriter (real-time, Windows only)
    → NT8 indicator writes JSONL on every bar close
    → syncs to server via tick_nt8_syncer.py
    → NOT ACTIVE (requires PC to stay on — not practical while traveling)

[Bar Reader] fortress-barreader (ACTIVE)
    → watches live/*.jsonl for new lines
    → appends to *.parquet AND *_l2_*.parquet files

[Executor] fortress-executor (ACTIVE, DRY_RUN)
    → reads parquets every 60s
    → runs all 43 strategy classes
    → signals → Telegram alerts
    → logs to 06_live_trading/logs/signals_YYYYMMDD.jsonl

[Trade Copier] fortress-copier (STOPPED)
    → polls leader /fill/list every 2s
    → copies new fills as market orders to 3 follower accounts
    → logs to 06_live_trading/logs/copier_YYYYMMDD.jsonl

[Dashboard] fortress-dashboard (ACTIVE)
    → Flask at :5050
    → reads parquets + state files + signal logs
    → /api/snapshot → equity curve, positions, signals, risk, market data
    → kill switch: GET /api/kill-switch, POST /api/halt, POST /api/resume

[Monitor] fortress-monitor (ACTIVE)
    → hourly Telegram health report
    → checks service states, data freshness, P&L, open positions
```

**Live data directory is currently empty** — `/opt/fortress/01_data/tick_bars/live/` has no files. This is expected. It fills up once `fortress-tradovate` is running.

---

## 10. PERFORMANCE DATA (as of 2026-06-18)

**⚠️ CRITICAL CAVEAT: All numbers below are on 15-minute delayed yfinance data. Short-term strategies (1m, 3m bars) are firing 15 candles late. These numbers are NOT indicative of real performance. Do not make strategy decisions based on this data.**

| Date | Trades | Wins | Win% | P&L |
|------|--------|------|------|-----|
| Jun 10 | 1 | 0 | 0% | -$4.00 |
| Jun 11 | 1 | 0 | 0% | -$4.00 |
| Jun 12 | 1 | 0 | 0% | -$4.00 |
| Jun 15 | 6 | 1 | 17% | -$131.33 |
| Jun 16 | 11 | 2 | 18% | -$545.70 |
| Jun 17 | 7 | 0 | 0% | -$741.92 |
| Jun 18 | 4 | 1 | 25% | -$19.43 |
| **TOTAL** | **31** | **4** | **13%** | **-$1,450** |

**Per-strategy (paper P&L, delayed data):**
- ID 27 MGC/consecutive_close_momentum: +$191 (67% WR, 3 trades) ← only profitable
- ID 21 SIL/ema_crossover: -$579 (10% WR, 10 trades) ← largest drag
- ID 23 SIL/opening_range_fakeout: -$364 (0% WR, 3 trades)
- ID 31 SIL/consecutive_close_momentum: -$201 (0% WR, 5 trades)

**Today (Jun 18):** Equity $48,651.90, trailing DD $348 of $800 limit, P&L today -$19.43 (3% of daily limit). 1 open position: SHORT 1x SIL (strategy 19).

**Re-evaluate performance only after running 3–5 days on real-time Tradovate data.**

---

## 11. .ENV FILE STRUCTURE

Located at `/opt/fortress/.env` — never committed to git. Current state:

```
# Telegram
TELEGRAM_BOT_TOKEN=<set>
TELEGRAM_CHAT_ID=8483433910

# Tradovate API (CRITICAL: CID AND SECRET ARE PLACEHOLDER ZEROS — need real values)
TV_USERNAME=LTT024LOBH5
TV_PASSWORD=<set>
TV_APP_ID=FortressFeed
TV_APP_VER=1.0
TV_CID=0                   ← NEEDS REAL VALUE
TV_SECRET=                 ← NEEDS REAL VALUE

TRADOVATE_USERNAME=LTT024LOBH5
TRADOVATE_PASSWORD=<set>
TRADOVATE_CID=0            ← NEEDS REAL VALUE
TRADOVATE_SECRET=          ← NEEDS REAL VALUE

# Live trading gate
FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND   ← already set, Conor authorized live trading 2026-06-17

# Trade Copier accounts
COPIER_LEADER_NAME=TakeProfit
COPIER_LEADER_USER=ConorWalsh1
COPIER_LEADER_PASS=<set>

COPIER_FOLLOWER_1_NAME=Lucid
COPIER_FOLLOWER_1_USER=LTT024LOBH5
COPIER_FOLLOWER_1_PASS=<set>

COPIER_FOLLOWER_2_NAME=Tradeify
COPIER_FOLLOWER_2_USER=TDFYU439260492
COPIER_FOLLOWER_2_PASS=<set>

COPIER_FOLLOWER_3_NAME=Apex
COPIER_FOLLOWER_3_USER=APEX_496623
COPIER_FOLLOWER_3_PASS=<set>

COPIER_DRY_RUN=true        ← must stay true until Conor explicitly approves real copying
COPIER_POLL_SECS=2
COPIER_QTY_MULT=1
```

---

## 12. KEY FILE LOCATIONS

### On the server (`/opt/fortress/`)
```
04_codebase/tick_live_executor.py        — main strategy runner (43 strategies)
04_codebase/tick_live_bar_reader.py      — JSONL → parquet updater
04_codebase/tick_yfinance_updater.py     — yfinance OHLCV downloader
04_codebase/tick_tradovate_live_feed.py  — real-time Tradovate WebSocket feed
04_codebase/tick_tradovate_client.py     — Tradovate REST API client (shared by feed+copier)
04_codebase/tick_trade_copier.py         — leader→follower trade copier
04_codebase/tick_dashboard_server.py     — Flask dashboard backend
04_codebase/tick_dashboard/index.html   — dashboard frontend
04_codebase/tick_ai_monitor.py           — hourly health monitor
04_codebase/tick_nt8_syncer.py           — Windows NT8 → server JSONL syncer (not active)
04_codebase/live_strategy_allowlist.yaml — which strategies run + parameters
server/fortress-executor.service         — systemd unit for executor
server/fortress-copier.service           — systemd unit for copier
01_data/tick_bars/                       — parquet OHLCV files (updated by yfinance)
01_data/tick_bars/live/                  — JSONL real-time bars (empty until Tradovate feed runs)
06_live_trading/state/                   — positions, heartbeat, daily_pnl, copier_state.json
06_live_trading/logs/                    — signals_YYYYMMDD.jsonl, copier_YYYYMMDD.jsonl
.env                                     — all credentials (never in git)
KILL_SWITCH.txt                          — write "STOP" to halt executor immediately
```

### In the git repo (`C:\Users\conor\Desktop\quant-research\`)
```
CLAUDE.md                                — this file
04_codebase/                             — all Python source (same as server)
server/                                  — systemd service files
requirements.txt                         — pip dependencies
```

**Deployment process:** The server is NOT a git clone. After editing locally:
1. `git commit && git push` to GitHub
2. SFTP the changed file(s) to server via paramiko (never SSH heredoc for files with `$`)
3. `systemctl daemon-reload && systemctl restart <service>` if service files changed

---

## 13. CONTRACTS (Sep 2026, roll ~Sep 17–19)

```
MGCU6  → GC  Micro Gold
SILU6  → SI  Micro Silver
MESU6  → ES  Micro E-mini S&P 500
MNQU6  → NQ  Micro E-mini Nasdaq
GCU6   → GC  Full Gold
SIU6   → SI  Full Silver
ESU6   → ES  Full S&P 500
NQU6   → NQ  Full Nasdaq
```

Already updated in `tick_tradovate_client.py` MICRO_SYMBOLS dict (commit `ab576a3`).

---

## 14. GITHUB REPO

`https://github.com/conorw1029-art/quant-research-fortress.git`

All code changes as of 2026-06-18 are committed and pushed to `main`. Recent commits:
- `ab576a3` — live-ready infra: cid/sec env auto-read, kill switch, OSO gate unlocked
- `47e300b` — docs: Tradovate API root cause confirmed
- `3de0479` — fix: dashboard parses executor signal format correctly
- `8b3c411` — feat: trade copier (leader→follower)

---

## 15. HOW TO CONTINUE FROM ANY DEVICE

### BEST: Reconnect after WiFi drop (tmux — zero context loss)
Claude Code runs inside a persistent **tmux** session on the server. WiFi drops don't kill it.

1. Open browser → `http://46.225.110.190:3000`
2. Login: `root` / `Fortress2026!`
3. Type: **`fortress`**  ← this reattaches to the running Claude Code session

That's it. Everything is exactly where you left off — same context, same conversation.

To detach without killing: press `Ctrl+B` then `D` inside tmux.
To check if a session is running: `tmux ls`

### Starting a fresh session (first time, or after server reboot):
1. Open browser → `http://46.225.110.190:3000`
2. Login: `root` / `Fortress2026!`
3. Type: `fortress` — it creates a new tmux session and starts Claude Code automatically

### From Termius (SSH app):
- Host: `46.225.110.190`, user: `root`, password: `Fortress2026!`
- Then type `fortress`

### From Windows PC:
- Open Claude Code in `C:\Users\conor\Desktop\quant-research`
- This CLAUDE.md loads automatically

### Reconnect prompt (if a truly new session starts):
Paste this verbatim:
```
I'm Conor Walsh — resuming the Quant Research Fortress project. Load all memory from /root/.claude/projects/-root/memory/ and give me a quick status check: services running? Any API replies from TakeProfit or Tradeify? (Lucid confirmed NO — skip them.) Current state and next step?
```

---

## 16. WHAT CLAUDE MUST DO ON STARTUP (any device)

Run these 3 commands immediately on every session start:

```bash
# 1. Service health
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor fortress-tradovate fortress-copier

# 2. Recent signals (last 20 lines)
journalctl -u fortress-executor --no-pager -n 20 2>&1 | cat

# 3. Dashboard snapshot
curl -s http://localhost:5050/api/snapshot | python3 -c "
import json,sys
d=json.load(sys.stdin)
p=d['portfolio']
print('P&L today:', p['pnl_today'], '| 7d:', p['pnl_7d'])
print('Open positions:', p['open_positions'])
print('Daily limit used:', p['daily_pct_used'], '%')
print('Kill switch:', d['heartbeat'].get('kill_switch','RUN'))
print('Mode:', d['heartbeat'].get('mode'))
print('Strategies active:', d['risk']['strategies_active'])
"
```

Then tell Conor:
- Which services are up/down
- Today's P&L and open positions
- What the current blocker is (almost certainly: still need Tradovate API keys)
- What step we are on in the activation sequence (Section 6)

**Do NOT start `fortress-tradovate` or `fortress-copier` or run any Tradovate auth test until cid/sec are confirmed set in `.env` (TV_CID != 0 and TV_SECRET != empty).**

---

## 17. QUICK DIAGNOSTICS

```bash
# All services running?
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor

# Live signal stream
journalctl -u fortress-executor -f

# Data freshness (should be updated every few minutes)
ls -la /opt/fortress/01_data/tick_bars/*.parquet | tail -5

# Is real-time data flowing? (empty = Tradovate feed not running yet)
ls -la /opt/fortress/01_data/tick_bars/live/

# Full dashboard snapshot
curl -s http://localhost:5050/api/snapshot | python3 -m json.tool | head -80

# Kill switch status
curl -s http://localhost:5050/api/kill-switch

# Check if API keys are still placeholders
python3 -c "
import os; from dotenv import load_dotenv
load_dotenv('/opt/fortress/.env')
cid = os.environ.get('TRADOVATE_CID','0')
sec = os.environ.get('TRADOVATE_SECRET','')
print('CID set:', cid != '0', '| SEC set:', len(sec) > 0)
"
```

---

## 18. FOOTGUNS (lessons learned — read before touching server config)

- **Paramiko key type:** The deploy key is Ed25519. Use `paramiko.Ed25519Key.from_private_key_file()`, never `RSAKey` — RSAKey throws `unpack requires a buffer of 4 bytes`.
- **Never write .env via SSH heredoc:** Python string escaping + bash heredoc corrupts `$` characters. Always use SFTP direct write: `sftp.open(path, 'w').write(content)`.
- **Tradovate rate limit is 5 auth requests/hour per account.** Never let a service crash-loop against `/auth/accesstokenrequest`. The copier uses 600s backoff between retries. Never retry cid=0/sec="" — it just wastes the quota.
- **Tradovate cid=0/sec="" gives same error as wrong password.** The error `Incorrect username or password` from the Tradovate API means the `cid`/`sec` are wrong, not necessarily the username/password. Don't change the usernames or passwords when you see this error — get the API keys instead.
- **KILL_SWITCH.txt:** Content must be exactly `STOP` (nothing else). `ARMED` does NOT trigger a halt. Dashboard writes `RUN` or `STOP`. If the file contains something else (e.g. from a previous manual edit), the executor treats it as `RUN`.
- **OSO bracket orders:** `_OSO_EXCHANGE_VERIFIED = True` is set in `tick_tradovate_client.py` (commit `ab576a3`). Do not change it back to False.
- **systemd EnvironmentFile does NOT expand `$`** in variable values — this was confirmed to not be an issue; the actual corruption risk is only via SSH heredoc (see above).
- **Windows Unicode:** Printing `→` or `⚠` via `print()` in PowerShell raises `UnicodeEncodeError` on cp1252. Use `errors='replace'` encoding or avoid the characters. Affects diagnostic scripts only, not server services.

---

## 19. CURRENT STATUS (as of 2026-06-24)

| Item | Status |
|---|---|
| Services running | 6 of 8 (yfinance, executor, barreader, dashboard, terminal, monitor) |
| fortress-tradovate | STOPPED — waiting on cid/sec API keys |
| fortress-copier | STOPPED — same blocker |
| Executor mode | DRY_RUN (`--poll 60`, no broker) |
| Live trading authorized | YES — `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` in .env |
| Real copying authorized | NO — `COPIER_DRY_RUN=true`, needs explicit per-session approval |
| Data quality | 15-min delayed (yfinance). Real-time: zero — live/ dir empty |
| API keys | `TV_CID=0`, `TV_SECRET=` empty — emails sent to all 4 prop firms 2026-06-20, awaiting reply |
| Kill switch | RUN (clear) |
| Strategies | 46 in allowlist (IDs 45-46 added 2026-06-24) |
| Paper P&L (2026-06-24) | +$368 net (strategies 19, 21, 31 profitable; 23 and 35 on circuit breaker) |
| GitHub | commit 39c7ce9 — V678 results + 2 new allowlist IDs. Push blocked (no PAT on VPS) |
| ANTHROPIC_API_KEY | Not set — AI health summaries use plain text fallback |

**2026-06-24 session fixes:**
- Bug fix: `KeyError: 'obi_5'` / `KeyError: 'large_buys'` — L2 columns now stubbed with 0.0
- Bug fix: `_check_stale` flat 20min → per-timeframe thresholds {1:3, 3:6, 5:10, 15:25, 30:45}
- Bug fix: `UnboundLocalError: 'traded_sym'` in stale block log handler
- New: `tick_history_bootstrap.py` — 60d@5m + 730d@1h downloaded for all 4 symbols
- New: `tick_runner_v9.py` — FOMC drift backtest (4 survivors at 30m/60m)
- New: `tick_runner_v678.py` — WFO on new bootstrap data (53 survivors, 30m trusted)
- New: `tick_worst_day_v678.py` — worst-day analysis for top 29 new candidates
- Allowlist: IDs 45 (NQ/donchian_breakout/30m, DSR=1.85) and 46 (NQ/overnight_gap_fill/30m, DSR=1.71)

---

## 20. ORDERED NEXT STEPS

**Step 1 — WAITING (Conor):** Awaiting replies from all 4 prop firms (TakeProfit, Lucid, Tradeify, Apex) to API access request emails sent 2026-06-20. When a firm replies with CID + SEC, bring those values to Claude immediately.

**Step 2 — Push to GitHub (Conor):** Commit 39c7ce9 exists locally on VPS but push is blocked (no GitHub PAT on VPS). To fix:
```bash
# Option A: from Windows PC
cd C:\Users\conor\Desktop\quant-research
git pull origin main  # pull VPS commits down first (they're only local)
# ... actually the VPS commits aren't on GitHub yet, need to push FROM VPS
# Set PAT on VPS:
git remote set-url origin https://YOUR_PAT@github.com/conorw1029-art/quant-research-fortress
git push origin main
```

**Step 3 (Claude):** Receive cid/sec → SFTP write to `.env` → run one-shot auth test.

**Step 4 (Claude):** Start `fortress-tradovate` → verify real-time bars → strategies run on live data.

**Step 5 (Claude):** Edit executor service to `--live-auto-trade` → restart.

**Step 6 (Claude):** Enable `fortress-copier` in dry-run → verify fills for 1 session.

**Step 7 (Conor explicitly approves):** "Enable real copying" → Claude sets `COPIER_DRY_RUN=false`.

**Step 8 (when live):** Run Phase 2 stress test (tick_worst_day_v678.py style analysis) on 1m/3m/5m/15m strategies using real Tradovate historical data. The V678 WFO run on bootstrap data found many high-DSR 1m/15m strategies but they only have 10-70 days of data — meaningless. With Tradovate real history, these can be properly validated.

**If prop firms block API entirely:** Integrate Polygon.io real-time data feed instead. Order execution via NinjaTrader/Rithmic alternative.

**V678 data note:** The bootstrap WFO run produced 53 survivors, but ONLY 30m results are trusted (2.5yr data). Do NOT add any 1m/3m/5m/15m strategies to the allowlist until running on real Tradovate multi-year historical data.
