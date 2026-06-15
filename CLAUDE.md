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

44 strategies across 4 markets (GC=Gold, SI=Silver, ES=S&P, NQ=Nasdaq).
- V1-V9: OHLCV + CVD strategies (39 total)
- V10 IDs 40-44: L2 microstructure strategies (need DOM data)
- 27 of 44 currently active; 17 are DISABLED_FOR_LIVE (worst-day risk > $1k)
- All running in --mock mode (signals only, no real orders) until manual approval

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

## 5 Systemd Services (all auto-restart, survive reboots)

```
fortress-yfinance   — downloads GC/SI/ES/NQ OHLCV every 5 min (15-min delayed)
fortress-executor   — runs 44 strategies, sends Telegram signals
fortress-barreader  — watches live/ dir for NT8/Tradovate JSONL, appends to parquet
fortress-dashboard  — Flask web UI at port 5050
fortress-terminal   — wetty web terminal at port 3000
```

**Service commands:**
```bash
systemctl status fortress-executor          # check status
journalctl -u fortress-executor -f          # live signal stream
journalctl -u fortress-yfinance -f          # data feed log
systemctl restart fortress-executor         # restart
systemctl restart fortress-yfinance fortress-executor fortress-dashboard  # restart all
```

**Tradovate live feed (NOT YET RUNNING — waiting for credentials):**
```
fortress-tradovate  — real-time L2 WebSocket feed (service installed, disabled)
```
To activate: edit `/opt/fortress/.env` → add TV_USERNAME and TV_PASSWORD → then:
```bash
systemctl start fortress-tradovate
systemctl enable fortress-tradovate
```

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

[State files]
  06_live_trading/state/  — positions, account_state, daily_pnl, heartbeat
  06_live_trading/logs/   — signals_YYYYMMDD.jsonl
```

## Key File Locations on Server

```
/opt/fortress/04_codebase/tick_live_executor.py      — main strategy runner
/opt/fortress/04_codebase/tick_live_bar_reader.py    — JSONL → parquet
/opt/fortress/04_codebase/tick_yfinance_updater.py   — free data feed
/opt/fortress/04_codebase/tick_tradovate_live_feed.py — real-time feed (needs creds)
/opt/fortress/04_codebase/tick_dashboard_server.py   — web dashboard
/opt/fortress/04_codebase/tick_nt8_syncer.py         — Windows→server JSONL syncer
/opt/fortress/01_data/tick_bars/                     — parquet data files
/opt/fortress/01_data/tick_bars/live/                — live JSONL files
/opt/fortress/06_live_trading/state/                 — runtime state
/opt/fortress/.env                                   — credentials (TV_USERNAME etc)
/opt/fortress/requirements.txt                       — pip deps
```

## Contracts (Sep 2026, roll ~Sep 17-19)

```
MGCU6 → GC (Micro Gold)
SIU6  → SI (Silver — verify exact symbol in Tradovate UI)
MESU6 → ES (Micro E-mini S&P 500)
MNQU6 → NQ (Micro E-mini Nasdaq)
```

## Current Blockers (as of 2026-06-15)

1. **Real-time data**: yfinance gives 15-min delayed OHLCV. To go real-time:
   - Edit `/opt/fortress/.env` → set `TV_USERNAME` and `TV_PASSWORD` (Tradovate login)
   - `systemctl start fortress-tradovate && systemctl enable fortress-tradovate`
   - L2 strategies (40-44) become active once JSONL data flows

2. **L2 strategies 40-44**: Show "no data" because they need tick_tradovate_live_feed.py running

3. **Mock mode**: Executor runs `--mock` (signals only). To go live:
   - Change `ExecStart` in `/etc/systemd/system/fortress-executor.service` (remove `--mock`)
   - Only do this when ready and funded accounts are confirmed

## NinjaTrader / Windows Setup

When NT8 is running on Windows:
- Add FortressBarWriter.cs indicator to GC and SI charts (1-minute timeframe)
- Run `start_fortress.bat` — Window 4 (NT8 Syncer) SFTPs JSONL files to server
- NT8 data supplements Tradovate data for GC/SI L2

## GitHub

Repo: https://github.com/conorw1029-art/quant-research-fortress.git
The server is NOT a git clone (deployed via tarball). To update server code:
1. Edit files locally, commit, push
2. Then SFTP the changed files to server, or rebuild the tarball

## How to Continue from Any Device

**From iPad browser:**
1. Go to http://46.225.110.190:3000 (web terminal)
2. Login: root / Fortress2026!
3. Type: `cd /opt/fortress && claude`
4. Claude Code starts with full context from this file

**From Termius / SSH app:**
- Host: 46.225.110.190, user: root, password: Fortress2026!

**From Windows (this machine):**
- Open Claude Code in the quant-research directory
- SSH key is at C:\Users\Conor\.ssh\fortress_deploy

## What Claude Should Do When Starting on the Server

1. Read this file (done automatically)
2. Run: `systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal`
3. Run: `journalctl -u fortress-executor --no-pager -n 20` to see recent signals
4. Check: `cat /opt/fortress/.env` to see if TV_USERNAME is set
5. Report status to user

## Quick Diagnostics

```bash
# All services OK?
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal

# Recent signals
journalctl -u fortress-executor --no-pager -n 30

# Data freshness
ls -la /opt/fortress/01_data/tick_bars/*.parquet

# Live L2 data flowing?
ls -la /opt/fortress/01_data/tick_bars/live/

# Credentials set?
grep TV_USERNAME /opt/fortress/.env
```
