"""
tick_watchdog.py — Fortress Autonomous System Watchdog
=======================================================
Monitors all services, data freshness, disk, and memory every 5 minutes.
Sends Telegram alerts on failures and attempts service restarts.
Sends a daily health summary at 08:00 UTC.

Run: /opt/fortress/venv/bin/python3 tick_watchdog.py
Or via systemd: systemctl start fortress-watchdog
"""

import os
import sys
import time
import subprocess
import json
import requests
import psutil
from datetime import datetime, timezone, date
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

BAR_DIR   = Path("/opt/fortress/01_data/tick_bars")
LOG_DIR   = Path("/opt/fortress/06_live_trading/logs")
STATE_DIR = Path("/opt/fortress/06_live_trading/state")

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "8034600379:AAGLzv9sFl61fya5DBkeTcidxvrd9o1aLmA")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT_ID",  "8483433910")

POLL_INTERVAL   = 300   # seconds between checks (5 min)
DAILY_REPORT_HR = 8     # UTC hour for daily summary

# Services that MUST be active for the system to work
CRITICAL_SERVICES = [
    "fortress-executor",
    "fortress-tv-webhook",
    "fortress-dashboard",
    "fortress-yfinance",
]
# Services that are NICE-TO-HAVE (alert but don't restart)
OPTIONAL_SERVICES = [
    "fortress-barreader",
    "fortress-monitor",
    "fortress-ibkr",         # will exist once IBKR is set up
]

# Data freshness thresholds — if parquet not updated in N minutes, alert
STALE_THRESHOLDS = {
    "ES_bars_15m": 30, "NQ_bars_15m": 30,
    "GC_bars_15m": 30, "SI_bars_15m": 30,
    "ES_bars_30m": 60, "NQ_bars_30m": 60,
    "GC_bars_30m": 60, "SI_bars_30m": 60,
}

DISK_WARN_PCT  = 80   # warn at 80% full
DISK_CRIT_PCT  = 90   # critical at 90% full
MEM_WARN_PCT   = 85   # warn at 85% memory used

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg: str, silent: bool = False):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT,
            "text": msg,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }, timeout=10)
    except Exception as e:
        print(f"  [Watchdog] Telegram failed: {e}")

# ── Service checks ────────────────────────────────────────────────────────────

def check_service(name: str) -> tuple[bool, str]:
    """Returns (is_active, status_string)."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=10
        )
        active = r.stdout.strip() == "active"
        return active, r.stdout.strip()
    except Exception as e:
        return False, str(e)

def restart_service(name: str) -> bool:
    try:
        subprocess.run(["systemctl", "restart", name], timeout=30, check=True)
        time.sleep(5)
        active, _ = check_service(name)
        return active
    except Exception:
        return False

# ── Data freshness ────────────────────────────────────────────────────────────

def check_data_freshness() -> list[str]:
    issues = []
    now = time.time()
    for fname, max_age_min in STALE_THRESHOLDS.items():
        path = BAR_DIR / f"{fname}.parquet"
        if not path.exists():
            issues.append(f"Missing: {fname}.parquet")
            continue
        age_min = (now - path.stat().st_mtime) / 60
        if age_min > max_age_min:
            issues.append(f"Stale {fname}: {age_min:.0f}min old (max {max_age_min}min)")
    return issues

# ── Disk space ────────────────────────────────────────────────────────────────

def check_disk() -> tuple[float, bool, bool]:
    usage = psutil.disk_usage("/")
    pct   = usage.percent
    return pct, pct >= DISK_WARN_PCT, pct >= DISK_CRIT_PCT

# ── Memory ───────────────────────────────────────────────────────────────────

def check_memory() -> tuple[float, bool]:
    vm  = psutil.virtual_memory()
    pct = vm.percent
    return pct, pct >= MEM_WARN_PCT

# ── Executor heartbeat ────────────────────────────────────────────────────────

def check_heartbeat() -> tuple[bool, str]:
    hb_path = STATE_DIR / "heartbeat.json"
    try:
        data = json.loads(hb_path.read_text())
        last = data.get("last_check", "")
        running = data.get("running", False)
        return running, last
    except Exception as e:
        return False, str(e)

# ── Log rotation helper ───────────────────────────────────────────────────────

def rotate_logs():
    """Delete signal logs older than 30 days, trim reconciliation log."""
    cutoff = time.time() - 30 * 86400
    deleted = 0
    for f in LOG_DIR.glob("signals_*.jsonl"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    # Trim reconciliation log to last 10,000 lines
    rec_log = LOG_DIR / "broker_reconciliation_log.jsonl"
    if rec_log.exists():
        lines = rec_log.read_text().splitlines()
        if len(lines) > 10000:
            rec_log.write_text("\n".join(lines[-10000:]) + "\n")
    if deleted:
        print(f"  [Watchdog] Rotated {deleted} old signal log(s)")

# ── Daily report ──────────────────────────────────────────────────────────────

def build_daily_report() -> str:
    now = datetime.now(timezone.utc)
    lines = [f"<b>🏰 Fortress Daily Report — {now.strftime('%Y-%m-%d %H:%M UTC')}</b>"]

    # Services
    svc_lines = []
    for svc in CRITICAL_SERVICES + OPTIONAL_SERVICES:
        active, status = check_service(svc)
        icon = "✅" if active else "❌"
        svc_lines.append(f"  {icon} {svc}: {status}")
    lines.append("\n<b>Services:</b>\n" + "\n".join(svc_lines))

    # Data freshness
    issues = check_data_freshness()
    if issues:
        lines.append("\n<b>Data Issues:</b>\n" + "\n".join(f"  ⚠️ {i}" for i in issues))
    else:
        lines.append("\n<b>Data:</b> ✅ All feeds fresh")

    # Disk
    disk_pct, _, crit = check_disk()
    disk_icon = "❌" if crit else ("⚠️" if disk_pct >= DISK_WARN_PCT else "✅")
    lines.append(f"\n<b>Disk:</b> {disk_icon} {disk_pct:.1f}% used")

    # Memory
    mem_pct, mem_warn = check_memory()
    mem_icon = "⚠️" if mem_warn else "✅"
    lines.append(f"<b>Memory:</b> {mem_icon} {mem_pct:.1f}% used")

    # Today's signal log
    today_log = LOG_DIR / f"signals_{now.strftime('%Y%m%d')}.jsonl"
    if today_log.exists():
        lines_count = len(today_log.read_text().splitlines())
        size_kb = today_log.stat().st_size // 1024
        lines.append(f"<b>Signals today:</b> {lines_count} events ({size_kb}KB)")

    # Next FOMC (hardcoded)
    lines.append(f"\n<b>Next FOMC:</b> 2026-07-29 (33 days)")
    lines.append(f"<b>Contract expiry:</b> MESU6/MNQU6 Sep 19 | MGCU6/SILU6 Sep 26")

    return "\n".join(lines)

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    # Load .env
    env_file = Path("/opt/fortress/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    print("=" * 60)
    print("  FORTRESS WATCHDOG — autonomous monitoring active")
    print(f"  Poll interval: {POLL_INTERVAL}s | Daily report: {DAILY_REPORT_HR}:00 UTC")
    print("=" * 60)

    send_telegram("🏰 <b>Fortress Watchdog started</b>\nMonitoring every 5 min. Daily report at 08:00 UTC.")

    last_daily_date = None
    restart_counts: dict[str, int] = {}

    while True:
        now = datetime.now(timezone.utc)
        print(f"\n[Watchdog] Check at {now.strftime('%Y-%m-%d %H:%M UTC')}")
        alerts = []

        # ── Service checks ──────────────────────────────────────────────
        for svc in CRITICAL_SERVICES:
            active, status = check_service(svc)
            if not active:
                count = restart_counts.get(svc, 0) + 1
                restart_counts[svc] = count
                print(f"  ❌ {svc} DOWN (attempt #{count}) — restarting...")
                ok = restart_service(svc)
                if ok:
                    alerts.append(f"⚠️ <b>{svc}</b> was DOWN, restarted successfully (attempt #{count})")
                else:
                    alerts.append(f"🚨 <b>{svc}</b> DOWN and failed to restart! Manual intervention needed.")
            else:
                restart_counts.pop(svc, None)
                print(f"  ✅ {svc}")

        for svc in OPTIONAL_SERVICES:
            active, _ = check_service(svc)
            if not active:
                print(f"  ⚠️  {svc} inactive (optional — not restarting)")

        # ── Executor heartbeat liveness check ───────────────────────────
        # Checks that the executor's heartbeat.json was updated recently.
        # A hung-but-active executor (e.g. blocked on IBKR gate, OOM, etc.)
        # will not update its heartbeat and should alert after 5 min silence.
        hb_path = STATE_DIR / "heartbeat.json"
        try:
            hb_data = json.loads(hb_path.read_text())
            hb_ts_str = hb_data.get("timestamp", "")
            if hb_ts_str:
                from datetime import datetime as _dt
                hb_ts = _dt.fromisoformat(hb_ts_str.replace("Z", "+00:00"))
                hb_age = (now - hb_ts).total_seconds()
                if hb_age > POLL_INTERVAL * 2:  # stale if older than 2 poll cycles
                    alerts.append(f"⚠️ Executor heartbeat stale: last seen {int(hb_age/60)}m ago")
                    print(f"  ⚠️  Executor heartbeat stale ({int(hb_age/60)}m ago)")
                else:
                    print(f"  ✅ Executor heartbeat {int(hb_age)}s ago")
            else:
                print(f"  ⚠️  Executor heartbeat timestamp missing")
        except Exception as hb_err:
            print(f"  ⚠️  Cannot read heartbeat: {hb_err}")

        # ── Data freshness ──────────────────────────────────────────────
        data_issues = check_data_freshness()
        for issue in data_issues:
            print(f"  ⚠️  {issue}")
            alerts.append(f"⚠️ Data: {issue}")

        # ── Disk ────────────────────────────────────────────────────────
        disk_pct, disk_warn, disk_crit = check_disk()
        if disk_crit:
            alerts.append(f"🚨 Disk CRITICAL: {disk_pct:.1f}% used — system may crash!")
            print(f"  🚨 Disk {disk_pct:.1f}%")
        elif disk_warn:
            alerts.append(f"⚠️ Disk high: {disk_pct:.1f}% used")
            print(f"  ⚠️  Disk {disk_pct:.1f}%")
        else:
            print(f"  ✅ Disk {disk_pct:.1f}%")

        # ── Memory ─────────────────────────────────────────────────────
        mem_pct, mem_warn = check_memory()
        if mem_warn:
            alerts.append(f"⚠️ Memory high: {mem_pct:.1f}% used")
            print(f"  ⚠️  Memory {mem_pct:.1f}%")
        else:
            print(f"  ✅ Memory {mem_pct:.1f}%")

        # ── Log rotation (daily) ────────────────────────────────────────
        rotate_logs()

        # ── Send alerts ─────────────────────────────────────────────────
        if alerts:
            msg = "🏰 <b>Fortress Alert</b>\n" + "\n".join(alerts)
            send_telegram(msg)

        # ── Daily report ────────────────────────────────────────────────
        today = now.date()
        if today != last_daily_date and now.hour >= DAILY_REPORT_HR:
            try:
                report = build_daily_report()
                send_telegram(report, silent=True)
                last_daily_date = today
                print("  📊 Daily report sent to Telegram")
            except Exception as e:
                print(f"  Daily report error: {e}")

        print(f"  Next check in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
