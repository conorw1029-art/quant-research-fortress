"""
tick_ai_monitor.py — Fortress AI Health Monitor
================================================
Runs every hour. Checks all system components, reads recent signals,
then sends a concise Telegram status update using Claude.

If ANTHROPIC_API_KEY is not set, sends a plain-text health check instead.

Run:
    python tick_ai_monitor.py           # one-shot check
    python tick_ai_monitor.py --loop    # run every hour forever
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT      = Path(__file__).parent.parent
LOG_DIR   = ROOT / "06_live_trading" / "logs"
STATE_DIR = ROOT / "06_live_trading" / "state"
BAR_DIR   = ROOT / "01_data" / "tick_bars"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")

SERVICES = [
    "fortress-yfinance",
    "fortress-executor",
    "fortress-barreader",
    "fortress-dashboard",
    "fortress-terminal",
]


def _service_status() -> dict[str, str]:
    out = {}
    for svc in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            out[svc] = r.stdout.strip()
        except Exception:
            out[svc] = "unknown"
    return out


def _data_freshness() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    result = {}
    for sym in ("GC", "SI", "ES", "NQ"):
        p = BAR_DIR / f"{sym}_bars_1m.parquet"
        if p.exists():
            age = now - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            mins = int(age.total_seconds() / 60)
            result[sym] = f"{mins}m ago"
        else:
            result[sym] = "missing"
    return result


def _recent_signals(n: int = 20) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    path  = LOG_DIR / f"signals_{today}.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def _account_state() -> dict:
    p = STATE_DIR / "account_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _build_plain_report(services: dict, freshness: dict,
                         signals: list, acct: dict) -> str:
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok   = sum(1 for s in services.values() if s == "active")
    fail = [k for k, v in services.items() if v != "active"]

    lines = [
        f"*FORTRESS MONITOR* — {now}",
        "",
        f"Services: {ok}/{len(services)} active" +
        (f" | FAILED: {', '.join(fail)}" if fail else " ✓"),
        "",
        "Data freshness:",
    ]
    for sym, age in freshness.items():
        lines.append(f"  {sym}: {age}")

    equity = acct.get("equity", 49000)
    dd     = acct.get("trailing_dd", 0)
    pnl    = acct.get("pnl_today", 0)
    lines += [
        "",
        f"Account: ${equity:,.0f} equity | DD: ${dd:,.0f} | P&L today: ${pnl:+,.2f}",
        "",
    ]

    accepted = [s for s in signals if s.get("accepted")]
    long_acc  = [s for s in accepted if s.get("signal", 0) == 1]
    short_acc = [s for s in accepted if s.get("signal", 0) == -1]
    lines.append(f"Recent signals ({len(signals)} total | accepted: {len(long_acc)}L {len(short_acc)}S):")
    for sig in signals[-5:]:
        ts  = (sig.get("timestamp", ""))[11:16] or "?"
        key = sig.get("strategy", sig.get("strategy_key", "?"))
        sym = sig.get("symbol", "")
        sig_val = sig.get("signal", 0)
        act = "LONG" if sig_val == 1 else ("SHORT" if sig_val == -1 else "flat")
        accepted = sig.get("accepted", False)
        flag = "✓" if accepted else "✗"
        lines.append(f"  {ts} [{flag}] {sym}/{key} → {act}")

    return "\n".join(lines)


def _build_ai_report(plain: str) -> str:
    """Ask Claude to summarise and flag anything unusual."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = (
            "You are an AI monitor for a live quantitative trading system called Fortress. "
            "Analyse the following system health report and write a short Telegram message "
            "(max 200 words) in plain text (no markdown headers, use emojis sparingly). "
            "Flag anything unusual. End with an emoji status: ✅ if all clear, ⚠️ if minor issues, 🚨 if critical.\n\n"
            f"HEALTH DATA:\n{plain}"
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        return plain + f"\n\n[AI summary unavailable: {e}]"


def _send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Monitor] No Telegram credentials — printing to stdout only")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
        return r.ok
    except Exception as e:
        print(f"[Monitor] Telegram error: {e}")
        return False


def run_once() -> None:
    print(f"[Monitor] Running health check at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    services  = _service_status()
    freshness = _data_freshness()
    signals   = _recent_signals(20)
    acct      = _account_state()

    plain = _build_plain_report(services, freshness, signals, acct)

    if ANTHROPIC_KEY:
        report = _build_ai_report(plain)
    else:
        report = plain

    _send_telegram(report)

    failed = [k for k, v in services.items() if v != "active"]
    if failed:
        print(f"[Monitor] WARNING — services down: {failed}")
        for svc in failed:
            print(f"[Monitor] Attempting restart: {svc}")
            subprocess.run(["systemctl", "restart", svc], timeout=15)


def main() -> None:
    p = argparse.ArgumentParser(description="Fortress AI health monitor")
    p.add_argument("--loop", action="store_true", help="Run every hour forever")
    p.add_argument("--interval", type=int, default=3600, help="Loop interval in seconds")
    args = p.parse_args()

    run_once()

    if args.loop:
        print(f"[Monitor] Looping every {args.interval}s — press Ctrl+C to stop")
        while True:
            time.sleep(args.interval)
            run_once()


if __name__ == "__main__":
    main()
