# Fortress Trading System — Claude Context File

Read this first. This is a live quantitative trading system running 24/7 on a Hetzner VPS.
When Claude Code starts (on any device), this file gives full context.

## Who is the user

Conor — quantitative trader running funded prop firm accounts, currently traveling/abroad (cannot rely on his personal PC being on).
- 10 funded Topstep accounts, ~$1,000 drawdown limit each
- Personal max DD: $2,000 per account
- 4 broker accounts, ALL accessed via Tradovate login: TakeProfit Trader (leader), Lucid, Tradeify, Apex — all in heavy drawdown
- Platforms: Tradovate (Lucid/TakeProfit/Tradeify/Apex all route through Tradovate) + NinjaTrader (Rithmic, separate)
- Email: conorw1029@gmail.com
- Telegram: bot token 8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA, chat_id 8483433910

## The System

43 strategies across 4 markets (GC=Gold, SI=Silver, ES=S&P, NQ=Nasdaq).
- V1-V9: OHLCV + CVD strategies (39 total)
- V10 IDs 40-44: L2 microstructure strategies (need DOM data — blocked until Tradovate feed is back)
- All 43 active in executor (status: REVIEW_REQUIRED or better)
- Running in **--mock mode** (signals only, no real orders). User has explicitly said: "i dont want to place the trades yet but i want to see how they perform" — do NOT switch to live trading or flip `COPIER_DRY_RUN` to false without fresh, explicit authorization.

Strategy allowlist: `04_codebase/live_strategy_allowlist.yaml`
Strategy code: `04_codebase/tick_strategies*.py`
Executor: `04_codebase/tick_live_executor.py`
Trade copier: `04_codebase/tick_trade_copier.py`

## Live Server

**Hetzner VPS: 46.225.110.190 (Ubuntu 24.04)**
- SSH: `ssh root@46.225.110.190` password `Fortress2026!`
- Windows SSH key (paramiko): `C:\Users\Conor\.ssh\fortress_deploy` — **Ed25519 key, must load with `paramiko.Ed25519Key.from_private_key_file()`, NOT `RSAKey`** (RSAKey throws `unpack requires a buffer of 4 bytes`)
- Code lives at: `/opt/fortress/`
- Python venv: `/opt/fortress/venv/bin/python`
- All services run as user `fortress`

**URLs (open in any browser, including iPad):**
- Dashboard: http://46.225.110.190:5050 — **confirmed live and working**, shows real-time signals, per-strategy win/loss + P&L, equity curve, risk gauges
- Web Terminal (shell in browser): http://46.225.110.190:3000 — login: root / Fortress2026!

## 8 Systemd Services (auto-restart, survive reboots)

```
fortress-yfinance    — ACTIVE — downloads GC/SI/ES/NQ OHLCV every 5 min (15-min delayed)
fortress-executor    — ACTIVE — runs 43 strategies, sends Telegram signals (--mock mode)
fortress-barreader   — ACTIVE — watches live/ dir for NT8/Tradovate JSONL, appends to parquet
fortress-dashboard   — ACTIVE — Flask web UI at port 5050 (signal/P&L display fixed and verified this session)
fortress-terminal    — ACTIVE — wetty web terminal at port 3000
fortress-monitor     — ACTIVE — hourly Telegram AI health reports (tick_ai_monitor.py)
fortress-tradovate   — STOPPED + DISABLED — real-time L2 WebSocket feed. BLOCKED on missing Tradovate API keys (see below). Do not start until real cid/sec are in .env.
fortress-copier      — STOPPED + DISABLED — trade copier (leader→follower). Same missing-API-key blocker.
```

**Service commands:**
```bash
systemctl status fortress-executor          # check status
journalctl -u fortress-executor -f          # live signal stream
journalctl -u fortress-yfinance -f          # data feed log
systemctl restart fortress-executor         # restart
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor fortress-tradovate fortress-copier
```

## ⚠️ CRITICAL OPEN ISSUE: Tradovate API was never provisioned (root cause found 2026-06-16)

All 4 Tradovate-linked accounts (TakeProfit/leader, Lucid, Tradeify, Apex) fail authentication with:
```
{'errorText': 'Incorrect username or password. Please try again, noting that passwords are case-sensitive.'}
```
**This is not a lockout, CAPTCHA, or rate-limit issue, and it is not a credential typo.** Root cause confirmed 2026-06-16:

1. Username/password for all 4 accounts were re-verified byte-for-byte against what the user originally provided (read `.env` via SFTP, compared `repr()` of every value, checked for hidden chars / CRLF corruption) — **all 8 values match exactly**. Credentials are not the problem.
2. The user independently confirmed clean, no-CAPTCHA, no-rejection manual browser logins for **all 4 accounts** on the same day the API kept failing. This ruled out an account-level lock or IP-based soft-lock theory (the earlier hypothesis in this doc, now superseded).
3. Per Tradovate's own docs/forum (see sources below), `POST /auth/accesstokenrequest` requires a `cid` (client ID) + `sec` (secret) pair that must be generated per-account via **Settings → API Access / Generate API Key** on the Tradovate web platform — a registered-application credential, separate from the login password. Some accounts also require a distinct **API password**, different from the normal login password.
4. `/opt/fortress/.env` has been sending `cid=0` and `sec=""` for all 4 accounts — placeholders, never replaced with real Tradovate-issued values. **This is the root cause.** It explains every symptom: browser login is clean (doesn't use cid/sec), API auth fails identically across all 4 accounts regardless of password correctness (none of the 4 have ever generated a real API key), and it never "cleared" over time because there was never anything time-based to clear.

Sources: [Tradovate API Access Requirements — Subscription, CID, SEC](https://danetrades.com/help-center/accounts-connections/tradovate-api-requirements-and-subscription/), [Accesstokenrequest Access denied — Tradovate Forum](https://community.tradovate.com/t/accesstokenrequest-access-denied-does-api-key-generation-take-time-to-kick-in/8874), [API password? — Tradovate Forum](https://community.tradovate.com/t/api-password/5161)

**What the user needs to do, per account (TakeProfit, Lucid, Tradeify, Apex):**
1. Log into that account on the Tradovate web platform → gear/Settings icon → "API Access" / "Generate API Key."
2. Generate (or retrieve, if one already exists) the `cid` + `sec` pair for that account.
3. Check whether the platform also requires/offers a separate **API password** distinct from the login password — if so, set one.
4. Hand over `cid` / `sec` (and API password if applicable) for each account so `.env` can be updated via SFTP.

This can be done one account at a time — start with the leader (TakeProfit) since the copier needs that one first. **Do NOT run further automated Tradovate auth attempts from the server until at least one account has a real cid/sec** — retrying with cid=0/sec="" will just keep failing the same way and adds no new information.

Once real cid/sec are in `.env`, re-enable with:
```bash
systemctl enable fortress-tradovate && systemctl start fortress-tradovate
journalctl -u fortress-tradovate -f
# Wait for: [Feed] Authenticated (user=..., LIVE)
# Only then:
systemctl enable fortress-copier && systemctl start fortress-copier   # COPIER_DRY_RUN=true — logs only, no real orders
```

Separately, TakeProfit Trader's credentials (`ConorWalsh1` / `G7841O4782K2454tv=`) were rejected even before this was diagnosed — almost certainly the same cid/sec root cause, not a separate issue. Re-test once TakeProfit has a real API key.

## Trade Copier (custom-built, replaces TradeCopia)

User has a TradeCopia Pro subscription ($49.99/mo) but it requires the desktop app to stay running on his PC, which he cannot keep on (traveling). Upgrading to Pro+/Pro+ Lite ($79.99–149.99/mo) was explicitly declined. Built a free custom copier instead, since all 4 accounts already go through Tradovate and we already have a working `TradovateClient` (`04_codebase/tick_tradovate_client.py`).

**`04_codebase/tick_trade_copier.py`** — polls the leader account's `/fill/list` every `COPIER_POLL_SECS` (default 2s), copies each new fill as a market order to every follower account, sends a Telegram alert, and logs to `06_live_trading/logs/copier_YYYYMMDD.jsonl`. Seeds existing fills on startup so it never replays history.

Config lives in `/opt/fortress/.env` (never committed to git):
```
COPIER_LEADER_NAME=TakeProfit / COPIER_LEADER_USER / COPIER_LEADER_PASS
COPIER_FOLLOWER_1_NAME=Lucid / _USER / _PASS
COPIER_FOLLOWER_2_NAME=Tradeify / _USER / _PASS
COPIER_FOLLOWER_3_NAME=Apex / _USER / _PASS
COPIER_DRY_RUN=true        # MUST stay true until user explicitly approves real copying
COPIER_POLL_SECS=2
COPIER_QTY_MULT=1
```

Service file: `server/fortress-copier.service`. Currently **stopped + disabled** pending the auth lockout above. Once leader auth works again, start it with `COPIER_DRY_RUN=true` first and watch `journalctl -u fortress-copier -f` + Telegram to confirm it would correctly mirror fills, before ever setting `COPIER_DRY_RUN=false`.

Crash-loop safety (already fixed, commit `1e9d7ae`): only the leader failing auth is treated as fatal; follower auth failures are logged and skipped. On auth failure, it retries every 600s (not immediately) and alerts via Telegram, so it can't spiral into a rate-limit storm again. `RestartSec=300` in the systemd unit as a second layer of protection.

## Dashboard — signal/P&L visibility (fixed and verified this session)

User wanted to observe signal performance without placing real trades: "i want to be able to see all the signals which are sent and if they were correct or not and how much could be made."

**Root cause found:** the executor writes signal logs to `06_live_trading/logs/signals_YYYYMMDD.jsonl` using its own field names (`event_type`, `signal`, `accepted`, `reason`, `timestamp`, `entry`/`entry_px`/`exit_px`, `pnl`) and sometimes writes literal `NaN` tokens (invalid JSON). The dashboard's `_aggregate_signals()` expected different field names (`action`, `alert_time`, `strategy_id`) and `json.loads()` was silently failing on the `NaN` tokens, so every signal got dropped before reaching the UI.

**Fix (commit `3de0479`, `04_codebase/tick_dashboard_server.py`):**
- `_parse_signal_line()` — regex-replaces `NaN`/`Infinity`/`-Infinity` tokens with `null` before `json.loads()`
- `_normalize_record()` — maps the executor's native field names onto what `_aggregate_signals()` expects (event_type+reason → action of BUY/SELL/TARGET/STOP/TIMEOUT/EXIT; timestamp → alert_time; entry → entry_px)

**Verified live** by pulling `/api/snapshot` directly from the running server: 52 signals present today, with real outcomes, e.g. `MGC #27 TIMEOUT pnl=+74.00`, `SIL #21 STOP pnl=-71.46`. The frontend (`04_codebase/tick_dashboard/index.html`) already had full support for this — per-signal action/price/pnl/time in the signal feed, and per-strategy `wins_today`/`losses_today`/`pnl_today`/`pnl_7d` columns in the strategy table — it just wasn't receiving usable data before. No frontend changes were needed, only the backend parsing fix.

Today's snapshot (2026-06-16, mock mode, paper P&L only): `pnl_today: -$475.07` against a `-$600` daily limit (79% used), `pnl_7d: -$618.40`, 1 open position, 0 strategies halted/disabled.

## Data Pipeline

```
[Data Sources]
  yfinance (delayed)  →  01_data/tick_bars/{SYMBOL}_bars_{TF}m.parquet
  NT8 FortressBarWriter (Windows)  →  01_data/tick_bars/live/{SYMBOL}_1m_live.jsonl
  Tradovate WebSocket (server, real-time)  →  01_data/tick_bars/live/{SYMBOL}_1m_live.jsonl  [BLOCKED — see auth lockout]

[Bar Reader]  tick_live_bar_reader.py
  reads live/*.jsonl  →  appends to *.parquet AND *_l2_*.parquet

[Executor]  tick_live_executor.py --mock --poll 60
  reads parquets  →  runs strategies  →  Telegram alerts  →  06_live_trading/logs/signals_YYYYMMDD.jsonl

[Trade Copier]  tick_trade_copier.py  [STOPPED — see auth lockout]
  polls leader /fill/list  →  copies to followers  →  06_live_trading/logs/copier_YYYYMMDD.jsonl

[Dashboard]  tick_dashboard_server.py --host 0.0.0.0 --port 5050
  reads parquets + state files + signal logs  →  live web UI (signal feed + win/loss + P&L — fixed & verified)

[Monitor]  tick_ai_monitor.py --loop --interval 3600
  checks services + data freshness + signals  →  Telegram every hour

[State files]
  06_live_trading/state/  — positions, account_state, daily_pnl, heartbeat, copier_state.json
  06_live_trading/logs/   — signals_YYYYMMDD.jsonl, copier_YYYYMMDD.jsonl
```

## Key File Locations on Server

```
/opt/fortress/04_codebase/tick_live_executor.py       — main strategy runner
/opt/fortress/04_codebase/tick_live_bar_reader.py     — JSONL → parquet
/opt/fortress/04_codebase/tick_yfinance_updater.py    — free data feed
/opt/fortress/04_codebase/tick_tradovate_live_feed.py — real-time L2 feed
/opt/fortress/04_codebase/tick_tradovate_client.py    — Tradovate REST API client (shared by feed + copier)
/opt/fortress/04_codebase/tick_trade_copier.py        — leader→follower trade copier
/opt/fortress/04_codebase/tick_dashboard_server.py    — web dashboard
/opt/fortress/04_codebase/tick_ai_monitor.py          — hourly health reports
/opt/fortress/04_codebase/tick_nt8_syncer.py          — Windows→server JSONL syncer
/opt/fortress/01_data/tick_bars/                      — parquet data files
/opt/fortress/01_data/tick_bars/live/                 — live JSONL files (empty until Tradovate feed/NT8 syncer is running)
/opt/fortress/06_live_trading/state/                  — runtime state (positions, heartbeat, copier_state.json)
/opt/fortress/06_live_trading/logs/                   — signals_YYYYMMDD.jsonl, copier_YYYYMMDD.jsonl
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
2. SFTP the changed files to server via paramiko or scp (NOT via SSH heredoc — see "Footguns" below)

All code changes through this session are committed and pushed to `main`. Working tree is clean.

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
- SSH key: C:\Users\Conor\.ssh\fortress_deploy (Ed25519 — see Footguns)

## What Claude Should Do When Starting (any device)

1. Read this file (done automatically)
2. Run: `systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor fortress-tradovate fortress-copier`
3. Run: `journalctl -u fortress-executor --no-pager -n 20` to see recent signals
4. Check the dashboard at http://46.225.110.190:5050 (or `curl localhost:5050/api/snapshot` from the server) for current signals/P&L
5. **Do NOT start `fortress-tradovate` or `fortress-copier`, or run any Tradovate auth attempt, until the user has confirmed the auth lockout (see above) is cleared.** Every failed attempt risks extending it.
6. Report status and current blockers to user

## Quick Diagnostics (safe — none of these touch Tradovate auth)

```bash
# All core services OK?
systemctl is-active fortress-yfinance fortress-executor fortress-barreader fortress-dashboard fortress-terminal fortress-monitor

# Recent signals
journalctl -u fortress-executor --no-pager -n 30

# Data freshness
ls -la /opt/fortress/01_data/tick_bars/*.parquet

# Live signal/P&L data (dashboard API, read-only)
curl -s http://localhost:5050/api/snapshot | python3 -m json.tool | head -50

# Live L2 data flowing? (will be empty until Tradovate feed is restored)
ls -la /opt/fortress/01_data/tick_bars/live/

# Credentials present? (do NOT use this to trigger new auth attempts)
grep -E "TV_USERNAME|TRADOVATE_USERNAME|COPIER_LEADER_NAME" /opt/fortress/.env
```

## Footguns / lessons learned this session (read before touching server config)

- **paramiko SSH key**: the deploy key is Ed25519. Use `paramiko.Ed25519Key.from_private_key_file(...)`, not `RSAKey` — RSAKey throws a cryptic `unpack requires a buffer of 4 bytes`.
- **Never write `.env` (or anything with `$` in it) via an SSH heredoc** (`ssh.exec_command('cat > file << EOF ...')`). Python string escaping + bash heredoc quoting compounds and corrupts special characters like `$`. Always write config files via **SFTP** (`sftp.open(path, 'w').write(content)`) — no shell involved, no double-escaping risk.
- **systemd `EnvironmentFile=` does NOT expand `$`** — that was a red herring; the real corruption source was the heredoc issue above.
- **Tradovate rate limit is 5 requests/hour per account** — never let a service crash-loop against the auth endpoint; always use a long backoff (600s+) and make follower-only failures non-fatal.
- **Tradovate REST API requires a real `cid`/`sec` per account**, generated via Settings → API Access on the web platform — `cid=0`/`sec=""` placeholders will fail with the same generic "incorrect username or password" error as a real credential mistake, even though browser login works fine. Don't mistake this for a lockout (see critical issue above) — confirmed root cause 2026-06-16 after ruling out IP-block/CAPTCHA-lock theories.
- **Windows console + arrows**: printing `→` or similar unicode via plain `print()` in PowerShell/cmd raises `UnicodeEncodeError` on cp1252. Cosmetic only — encode with `errors='replace'` or avoid the character in diagnostic scripts.

## Pending / Next Steps

1. **Get real Tradovate API keys** (all 4 accounts) — user needs to generate `cid`/`sec` per account via Settings → API Access on the Tradovate web platform (see critical issue above) and hand them over to replace the `cid=0`/`sec=""` placeholders in `.env`. This blocks both the real-time L2 feed and the trade copier. Start with TakeProfit (leader) since the copier needs that one first.
2. **TakeProfit Trader** auth failure is almost certainly the same cid/sec root cause, not a separate issue — re-test once it has a real API key.
3. Once leader auth is restored: start `fortress-copier` in dry-run, verify via logs/Telegram that it correctly mirrors fills, before ever setting `COPIER_DRY_RUN=false` (requires explicit user go-ahead — no real copying yet).
4. L2 strategies (IDs 40-44) remain blocked on no DOM/L2 data until `fortress-tradovate` is back.

## Current Status (as of 2026-06-16)

- 6 of 8 services ACTIVE: yfinance, executor, barreader, dashboard, terminal, monitor
- `fortress-tradovate` and `fortress-copier` STOPPED + DISABLED — waiting on real Tradovate API keys (cid/sec), see critical issue above. Root cause confirmed today: not a lockout, the API was simply never provisioned for any of the 4 accounts.
- Executor in --mock mode (no real orders) — by explicit user instruction, do not change without fresh approval
- All 43 strategies monitoring, 0 halted, 0 disabled
- Dashboard signal/P&L visibility bug fixed and verified live (see Dashboard section)
- Today's mock P&L: -$475.07 (79% of daily limit), 7-day: -$618.40
- Data: yfinance OHLCV updating every ~5 min, fresh as of last check; no L2/DOM data (blocked on Tradovate)
- ANTHROPIC_API_KEY not yet set in .env (AI health summaries use plain text fallback)
