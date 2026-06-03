"""
tick_alert_router.py
=====================
Handles routing of signal alerts to different output destinations.
Clean separation from the signal engine — the engine calls this;
this module knows nothing about bar loading or strategies.

Destinations:
  - console:  Box-drawn formatted print to stdout
  - jsonl:    Append to 06_live_trading/logs/signals_YYYYMMDD.jsonl
  - telegram: POST to Telegram Bot API
  - discord:  POST to Discord webhook (optional, disabled by default)

Usage:
    from tick_alert_router import AlertRouter
    router = AlertRouter(config={"console": True, "jsonl": True, "telegram": False})
    router.send_signal(signal_dict)
    router.send_blocked(signal_dict)
    router.send_daily_summary(report_dict)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("AlertRouter")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR    = _REPO_ROOT / "06_live_trading" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Formatting helpers
# ===========================================================================

_BOX_WIDTH = 62  # inner width (excluding border chars)


def _pad(text: str, width: int = _BOX_WIDTH) -> str:
    """Pad text to box width, truncating if needed."""
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def _format_signal_console(signal: Dict[str, Any]) -> str:
    """
    Render a signal as a box-drawn console string.

    Example:
        ╔══════════════════════════════════════════════════════════╗
        ║ SIGNAL: CVD_Microprice | SI | LONG                       ║
        ║ Entry: 32.150–32.200  Stop: 31.950  Target: 32.550       ║
        ║ Risk: $125  R/R: 2.0  Confidence: HIGH                   ║
        ║ Context: CVD_delta=140 | mp_tick=1 | session_vwap=32.100  ║
        ║ Regime: RANGING | Bar: 2026-06-03 14:30 UTC              ║
        ║ Invalidation: Cancel if price < 32.050 before entry      ║
        ╚══════════════════════════════════════════════════════════╝
    """
    w       = _BOX_WIDTH
    top     = "╔" + "═" * (w + 2) + "╗"
    bottom  = "╚" + "═" * (w + 2) + "╝"
    div     = "╟" + "─" * (w + 2) + "╢"

    def row(text: str) -> str:
        return "║ " + _pad(text, w) + " ║"

    strat    = signal.get("strategy_name", "?")
    sym      = signal.get("symbol", "?")
    side     = signal.get("side", "?")
    entry_z  = signal.get("entry_zone", "?")
    stop_p   = signal.get("stop_price", "?")
    target_p = signal.get("target_price", "?")
    risk_d   = signal.get("risk_dollars", "?")
    rr       = signal.get("rr_ratio", "?")
    conf     = signal.get("confidence", "?")
    regime   = signal.get("market_regime", "?")
    inv_cond = signal.get("invalidation_condition", "?")
    bar_ts   = signal.get("bar_timestamp", "?")

    # Shorten bar_ts to "2026-06-03 14:30 UTC"
    try:
        bar_ts_str = str(bar_ts)[:16].replace("T", " ") + " UTC"
    except Exception:
        bar_ts_str = str(bar_ts)

    # Build context string from top L2 keys
    ctx = signal.get("context", {}) or {}
    ctx_parts = []
    for key in ["cvd_delta", "cvd", "microprice_last", "session_vwap",
                "ofi_5", "buy_sweeps", "absorption_score"]:
        if key in ctx and ctx[key] is not None:
            label = key.replace("_last", "").replace("microprice", "mp")
            ctx_parts.append(f"{label}={ctx[key]}")
    ctx_str = " | ".join(ctx_parts[:4]) or "N/A"

    lines = [
        top,
        row(f"SIGNAL: {strat} | {sym} | {side}"),
        row(f"Entry: {entry_z}  Stop: {stop_p}  Target: {target_p}"),
        row(f"Risk: ${risk_d}  R/R: {rr}  Confidence: {conf}"),
        row(f"Context: {ctx_str}"),
        row(f"Regime: {regime} | Bar: {bar_ts_str}"),
        row(f"Invalidation: {inv_cond}"),
        bottom,
    ]
    return "\n".join(lines)


def _format_blocked_console(signal: Dict[str, Any]) -> str:
    """Compact single-line format for blocked signals."""
    reason = signal.get("block_reason", "unknown")
    strat  = signal.get("strategy_name", "?")
    sym    = signal.get("symbol", "?")
    side   = signal.get("side", "N/A")
    return (
        f"[BLOCKED] {strat} | {sym} | {side} | Reason: {reason}"
    )


def _format_signal_telegram(signal: Dict[str, Any]) -> str:
    """Condensed plain-text Telegram message for a fired signal."""
    strat    = signal.get("strategy_name", "?")
    sym      = signal.get("symbol", "?")
    side     = signal.get("side", "?")
    entry_z  = signal.get("entry_zone", "?")
    stop_p   = signal.get("stop_price", "?")
    target_p = signal.get("target_price", "?")
    risk_d   = signal.get("risk_dollars", "?")
    rr       = signal.get("rr_ratio", "?")
    conf     = signal.get("confidence", "?")
    regime   = signal.get("market_regime", "?")
    inv      = signal.get("invalidation_condition", "?")
    bar_ts   = signal.get("bar_timestamp", "?")
    try:
        bar_ts_str = str(bar_ts)[:16].replace("T", " ") + " UTC"
    except Exception:
        bar_ts_str = str(bar_ts)

    return (
        f"SIGNAL: {strat} | {sym} | {side}\n"
        f"Entry: {entry_z}\n"
        f"Stop: {stop_p}  Target: {target_p}\n"
        f"Risk: ${risk_d}  R/R: {rr}  Confidence: {conf}\n"
        f"Regime: {regime} | Bar: {bar_ts_str}\n"
        f"Invalidation: {inv}"
    )


def _format_daily_summary_console(report: Dict[str, Any]) -> str:
    """Render daily summary report to console string."""
    date       = report.get("date", "?")
    total      = report.get("total_signals_fired", 0)
    blocked    = report.get("total_signals_blocked", 0)
    hypo_pnl   = report.get("hypo_pnl_dollars", 0.0)
    win_rate   = report.get("hypo_win_rate_pct", 0.0)
    avg_r      = report.get("avg_r_achieved", 0.0)
    by_sym     = report.get("signals_by_symbol", {})
    by_strat   = report.get("signals_by_strategy", {})
    block_brkd = report.get("block_breakdown", {})
    worst_miss = report.get("worst_miss", None)

    lines = [
        "=" * 60,
        f"  DAILY SIGNAL REPORT — {date}",
        "=" * 60,
        f"  Signals Fired:   {total}",
        f"  Signals Blocked: {blocked}",
    ]
    if block_brkd:
        for reason, count in block_brkd.items():
            lines.append(f"    - {reason}: {count}")
    lines += [
        f"",
        f"  Hypothetical Performance",
        f"    PnL:      ${hypo_pnl:,.2f}",
        f"    Win Rate: {win_rate:.1f}%",
        f"    Avg R:    {avg_r:.2f}R",
    ]
    if by_sym:
        lines.append(f"")
        lines.append(f"  By Symbol:")
        for sym, cnt in by_sym.items():
            lines.append(f"    {sym}: {cnt} signal(s)")
    if by_strat:
        lines.append(f"")
        lines.append(f"  By Strategy:")
        for strat, cnt in by_strat.items():
            lines.append(f"    {strat}: {cnt} signal(s)")
    if worst_miss:
        lines.append(f"")
        lines.append(f"  Worst Miss (blocked signal that would have won):")
        lines.append(f"    {worst_miss}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ===========================================================================
# Telegram Sender
# ===========================================================================

def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    """
    Send a text message via Telegram Bot API.
    Returns True on success, False on failure.
    """
    try:
        import requests  # type: ignore
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except ImportError:
        logger.warning("requests library not available — Telegram disabled")
        return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ===========================================================================
# Discord Sender
# ===========================================================================

def _send_discord(webhook_url: str, text: str) -> bool:
    """Send to Discord webhook. Returns True on success."""
    try:
        import requests  # type: ignore
        payload = {"content": text[:2000]}  # Discord 2000 char limit
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning(f"Discord error {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Discord send failed: {e}")
        return False


# ===========================================================================
# AlertRouter
# ===========================================================================

class AlertRouter:
    """
    Routes signal dicts to enabled destinations.

    Config keys (all bool):
        console  — print to stdout (default True)
        jsonl    — append to log file (default True)
        telegram — send via Telegram Bot API (default False)
        discord  — send via Discord webhook (default False)
        dry_run  — if True, suppress all disk/network writes (default False)

    Environment variables (Telegram):
        TELEGRAM_BOT_TOKEN
        TELEGRAM_CHAT_ID

    Environment variables (Discord):
        DISCORD_WEBHOOK_URL
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg              = config or {}
        self.console     = bool(cfg.get("console",  True))
        self.jsonl       = bool(cfg.get("jsonl",    True))
        self.telegram    = bool(cfg.get("telegram", False))
        self.discord     = bool(cfg.get("discord",  False))
        self.dry_run     = bool(cfg.get("dry_run",  False))

        self._tg_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._tg_chat    = os.environ.get("TELEGRAM_CHAT_ID",   "")
        self._dc_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")

        if self.telegram and (not self._tg_token or not self._tg_chat):
            logger.warning(
                "Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in env. "
                "Telegram alerts disabled."
            )
            self.telegram = False

        if self.discord and not self._dc_webhook:
            logger.warning("Discord enabled but DISCORD_WEBHOOK_URL not set. Discord disabled.")
            self.discord = False

        logger.info(
            f"AlertRouter init | console={self.console} jsonl={self.jsonl} "
            f"telegram={self.telegram} discord={self.discord} dry_run={self.dry_run}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_signal(self, signal: Dict[str, Any]) -> None:
        """Route a fired (non-blocked) signal to all enabled destinations."""
        if self.console:
            print(_format_signal_console(signal))

        if self.jsonl and not self.dry_run:
            self._write_jsonl(signal)

        if self.telegram:
            text = _format_signal_telegram(signal)
            _send_telegram(self._tg_token, self._tg_chat, text)

        if self.discord:
            text = _format_signal_telegram(signal)  # same format is fine for Discord
            _send_discord(self._dc_webhook, text)

    def send_blocked(self, signal: Dict[str, Any]) -> None:
        """Log a blocked signal — console and JSONL only, never Telegram/Discord."""
        if self.console:
            print(_format_blocked_console(signal))

        if self.jsonl and not self.dry_run:
            self._write_jsonl(signal)

    def send_error(self, message: str) -> None:
        """Send an error notification. Console always; Telegram if enabled."""
        error_text = f"[ERROR] Signal Engine: {message}"
        if self.console:
            print(error_text)

        if self.telegram:
            _send_telegram(self._tg_token, self._tg_chat, error_text)

    def send_daily_summary(self, report: Dict[str, Any]) -> None:
        """Send daily summary report. Console + optional Telegram."""
        if self.console:
            print(_format_daily_summary_console(report))

        if self.jsonl and not self.dry_run:
            self._write_report_json(report)

        if self.telegram:
            date     = report.get("date", "?")
            total    = report.get("total_signals_fired", 0)
            pnl      = report.get("hypo_pnl_dollars", 0.0)
            win_rate = report.get("hypo_win_rate_pct", 0.0)
            avg_r    = report.get("avg_r_achieved", 0.0)
            text = (
                f"Daily Signal Report — {date}\n"
                f"Fired: {total} | PnL: ${pnl:,.0f}\n"
                f"Win Rate: {win_rate:.1f}% | Avg R: {avg_r:.2f}R"
            )
            _send_telegram(self._tg_token, self._tg_chat, text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _signal_log_path(self) -> Path:
        today = datetime.utcnow().strftime("%Y%m%d")
        return LOG_DIR / f"signals_{today}.jsonl"

    def _report_path(self, date_str: Optional[str] = None) -> Path:
        report_dir = _REPO_ROOT / "06_live_trading" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        d = date_str or datetime.utcnow().strftime("%Y%m%d")
        return report_dir / f"daily_{d}.json"

    def _write_jsonl(self, signal: Dict[str, Any]) -> None:
        try:
            path = self._signal_log_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal, default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to write signal to JSONL: {e}")

    def _write_report_json(self, report: Dict[str, Any]) -> None:
        try:
            date_str = report.get("date", datetime.utcnow().strftime("%Y-%m-%d")).replace("-", "")
            path = self._report_path(date_str)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
            logger.info(f"Daily report written to {path}")
        except Exception as e:
            logger.error(f"Failed to write daily report: {e}")
