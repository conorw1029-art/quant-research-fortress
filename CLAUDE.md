# Fortress Trading System — Claude Context File

Read this first. This is a live quantitative trading system running 24/7 on a Hetzner VPS.
When Claude Code starts (on any device), this file gives full context.

## Who is the user

Conor — quantitative trader running funded prop firm accounts.
- 10 funded Topstep accounts, ~$1,000 drawdown limit each
- Personal max DD: $2,000 per account
- Platforms: Tradovate/Lucid + NinjaTrader + Apex/Tradeify (Rithmic)
- Email: conorw1029@gmail.com
- Telegram: bot token 8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA, chat_id 8483433910

## The System

43 strategies across 4 markets (GC=Gold, SI=Silver, ES=S&P, NQ=Nasdaq).
- V1-V9: OHLCV + CVD strategies (39 total)
- V10 IDs 40-44: L2 microstructure strategies (need DOM data)
- All 43 active in executor (status: REVIEW_REQUIRED or better)
- Running in --mock mode (signals only, no real orders) until Tradovate auth confirmed

Strategy allowlist: `04_codebase/live_strategy_allowlist.yaml`
Strategy code: `04_codebase/tick_strategies*.py`
Executor: `04_codebase/tick_live_executor.py`

## Live Server

**Hetzner VPS: 46.225.110.190 (Ubuntu 24.04)**
- SSH: `ssh root@46.225.110.190` password `Fortress2026!`
- Code lives at: `/opt/fortress/`
- Python venv: `/opt/fortress/venv/bin/python`
- All services run as user `fortress`

**URLs (open in any browser, including iPad):**
- Dashboard: http://46.225.110.190:5050
- Web Terminal (shell in browser): http://46.225.110.190:3000 — login: root / Fortress2026!

## 7 Systemd Services (auto-restart, survive reboots)

```
fortress-yfinance    — downloads GC/SI/ES/NQ OHLCV every 5 min (15-min delayed)
fortress-executor    — runs 43 strategies, sends Telegram signals (--mock mode)
fortress-barreader   — watches live/ dir for NT8/Tradovate JSONL, appends to parquet
fortress-dashboard   — Flask web UI at port 5050
fortress-terminal    — wetty web terminal at port 3000
fortress-monitor     — hourly Telegram AI health reports (tick_ai_monitor.py)
fortress-tradovate   — real-time L2 WebSocket feed [STOPPED/DISABLED - rate limited]
```

**Service commands:**
```bash
systemctl status fortress-executor          # check status
journalctl -u fortress-executor -f          # live signal stream
journalctl -u fortress-yfinance -f          # data feed log
systemctl restart fortress-executor         # restart
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor
```

## Go-Live Sequence (Tradovate rate limit clears ~17:00 UTC 2026-06-15)

Credentials are CONFIRMED CORRECT (hit 5/hr rate limit, not auth failure).
They are stored in `/opt/fortress/.env` already.

```bash
# Step 1: Start real-time L2 feed
systemctl enable fortress-tradovate
systemctl start fortress-tradovate
journalctl -u fortress-tradovate -f
# Wait for: [Feed] Authenticated (user=..., LIVE)

# Step 2: Switch executor to live orders (executor service file already has --live-auto-trade)
systemctl restart fortress-executor
journalctl -u fortress-executor -f
# Look for: ** LIVE MODE **
```

If captcha prompt appears: user logs into trader.tradovate.com in browser first, then retry Step 1.

## Data Pipeline

```
[Data Sources]
  yfinance (delayed)  →  01_data/tick_bars/{SYMBOL}_bars_{TF}m.parquet
  NT8 FortressBarWriter (Windows)  →  01_data/tick_bars/live/{SYMBOL}_1m_live.jsonl
  Tradovate WebSocket (server, real-time)  →  01_data/tick_bars/live/{SYMBOL}_1m_live.jsonl

[Bar Reader]  tick_live_bar_reader.py
  reads live/*.jsonl  →  appends to *.parquet AND *_l2_*.parquet

[Executor]  tick_live_executor.py --mock --poll 60
  reads parquets  →  runs strategies  →  Telegram alerts

[Dashboard]  tick_dashboard_server.py --host 0.0.0.0 --port 5050
  reads parquets + state files  →  live web UI

[Monitor]  tick_ai_monitor.py --loop --interval 3600
  checks services + data freshness + signals  →  Telegram every hour

[State files]
  06_live_trading/state/  — positions, account_state, daily_pnl, heartbeat
  06_live_trading/logs/   — signals_YYYYMMDD.jsonl
```

## Key File Locations on Server

```
/opt/fortress/04_codebase/tick_live_executor.py       — main strategy runner
/opt/fortress/04_codebase/tick_live_bar_reader.py     — JSONL → parquet
/opt/fortress/04_codebase/tick_yfinance_updater.py    — free data feed
/opt/fortress/04_codebase/tick_tradovate_live_feed.py — real-time L2 feed
/opt/fortress/04_codebase/tick_dashboard_server.py    — web dashboard
/opt/fortress/04_codebase/tick_ai_monitor.py          — hourly health reports
/opt/fortress/04_codebase/tick_nt8_syncer.py          — Windows→server JSONL syncer
/opt/fortress/01_data/tick_bars/                      — parquet data files
/opt/fortress/01_data/tick_bars/live/                 — live JSONL files
/opt/fortress/06_live_trading/state/                  — runtime state
/opt/fortress/.env                                    — credentials (never committed to git)
/opt/fortress/requirements.txt                        — pip deps
```

## Contracts (Sep 2026, roll ~Sep 17-19)

```
MGCU6 → GC (Micro Gold)
SILU6 → SI (Micro Silver)
MESU6 → ES (Micro E-mini S&P 500)
MNQU6 → NQ (Micro E-mini Nasdaq)
GCU6  → GC (Full Gold)
SIU6  → SI (Full Silver)
ESU6  → ES (Full S&P)
NQU6  → NQ (Full Nasdaq)
```

## GitHub Repo

https://github.com/conorw1029-art/quant-research-fortress.git

The server is NOT a git clone (deployed via tarball/SFTP). To update server after a local code change:
1. Edit files locally, commit, push
2. SFTP the changed files to server via paramiko or scp

## How to Continue from Any Device

**From iPad browser (no SSH app needed):**
1. Go to http://46.225.110.190:3000 (web terminal)
2. Login: root / Fortress2026!
3. Type: `cd /opt/fortress && claude`
4. Claude Code starts with full context from this CLAUDE.md file

**From Termius SSH app:**
- Host: 46.225.110.190, user: root, password: Fortress2026!

**From Windows (development machine):**
- Open Claude Code in the quant-research directory
- SSH key: C:\Users\Conor\.ssh\fortress_deploy

## What Claude Should Do When Starting on the Server

1. Read this file (done automatically)
2. Run: `systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor fortress-tradovate`
3. Run: `journalctl -u fortress-executor --no-pager -n 20` to see recent signals
4. Run: `journalctl -u fortress-tradovate --no-pager -n 10` to check feed status
5. Report status and current blockers to user

## Quick Diagnostics

```bash
# All services OK?
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor

# Recent signals
journalctl -u fortress-executor --no-pager -n 30

# Data freshness
ls -la /opt/fortress/01_data/tick_bars/*.parquet

# Live L2 data flowing?
ls -la /opt/fortress/01_data/tick_bars/live/

# Credentials set?
grep -E "TV_USERNAME|TRADOVATE_USERNAME" /opt/fortress/.env

# Tradovate feed logs
journalctl -u fortress-tradovate --no-pager -n 30
```

## Current Status (as of 2026-06-15)

- 5 of 7 services ACTIVE (yfinance, executor, barreader, dashboard, terminal, monitor)
- fortress-tradovate STOPPED (rate limited, re-enable after 17:00 UTC 2026-06-15)
- Executor in --mock mode (no real orders)
- All 43 strategies monitoring
- Tradovate credentials confirmed correct
- Data: 15-min delayed OHLCV (will become real-time once tradovate feed restarts)
- ANTHROPIC_API_KEY not yet set in .env (AI health summaries use plain text fallback)
