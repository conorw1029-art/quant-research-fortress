"""
telegram_notifier.py — Telegram signal alert bot
=================================================
Sends a Telegram message every time the executor fires a live signal.
Uses only Python stdlib (urllib) — no extra packages needed.

Setup (one-time, ~5 minutes):
  1. Open Telegram -> search @BotFather -> /newbot -> follow prompts
  2. Copy the bot token (looks like: 123456789:ABCdef...)
  3. Start a chat with your new bot (click the link BotFather gives you)
  4. Run:  python 04_codebase/get_telegram_chat_id.py
  5. Copy your Chat ID from that script's output

Then set env vars:
  $env:TELEGRAM_BOT_TOKEN = "123456789:ABCdef..."
  $env:TELEGRAM_CHAT_ID   = "987654321"

Or pass token/chat_id directly to TelegramNotifier().
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


class TelegramNotifier:
    """
    Sends Fortress trading signals to a Telegram chat.
    Gracefully no-ops if credentials are missing.
    """

    def __init__(
        self,
        token:   Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.token   = token   or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.token and self.chat_id)
        if not self._enabled:
            print("[Telegram] No credentials — alerts disabled. "
                  "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable.")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Public API ──────────────────────────────────────────────────────────────

    def send_signal(self, alert: dict, mode: str, contracts: int) -> bool:
        """
        Format and send a trade signal alert.
        alert dict must have: symbol, direction (1/-1), stop_px, target_px,
        entry_px, strategy, risk_usd (all from check_all_strategies fired alerts).
        """
        if not self._enabled:
            return False

        direction = "BUY" if alert.get("direction", alert.get("signal", 0)) == 1 else "SELL"
        symbol    = alert.get("symbol", "?")
        stop      = alert.get("stop_px", 0)
        target    = alert.get("target_px", 0)
        entry     = alert.get("entry_px", 0)
        strategy  = alert.get("strategy", "?")
        risk      = alert.get("risk_usd", 0)
        bar_min   = alert.get("bar_minutes", 1)

        rr_str = "?"
        if entry and stop and target and abs(entry - stop) > 0:
            rr = abs(target - entry) / abs(entry - stop)
            rr_str = f"1:{rr:.1f}"

        time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

        lines = [
            f"<b>FORTRESS SIGNAL</b>  [{mode}]",
            "",
            f"<b>{direction} {symbol}</b>  x{contracts}  ({bar_min}m bar)",
            f"Strategy:  {strategy}",
            "",
            f"Entry:     MARKET",
            f"Stop:      {stop:.2f}",
            f"Target:    {target:.2f}",
            f"Risk:      ${risk:.0f}   RR: {rr_str}",
            "",
            f"Time: {time_str}",
            "",
            "<b>--- Place on all accounts now ---</b>",
        ]
        return self._send("\n".join(lines))

    def send_text(self, text: str) -> bool:
        """Send a plain text message."""
        return self._send(text)

    def send_startup(self, mode: str, n_strategies: int) -> bool:
        """Send a startup notification."""
        if not self._enabled:
            return False
        msg = (
            f"<b>FORTRESS STARTED</b>\n"
            f"Mode: {mode}\n"
            f"Strategies: {n_strategies}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        return self._send(msg)

    def send_kill_switch(self) -> bool:
        """Send a kill switch alert."""
        if not self._enabled:
            return False
        return self._send(
            "<b>KILL SWITCH ACTIVATED</b>\n"
            "Fortress executor has stopped. No new signals will fire."
        )

    # ── Internal ────────────────────────────────────────────────────────────────

    def _send(self, text: str) -> bool:
        """HTTP POST to Telegram Bot API. Returns True on success."""
        url     = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status == 200
                if not ok:
                    print(f"[Telegram] HTTP {resp.status}")
                return ok
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"[Telegram] HTTPError {e.code}: {body}")
            return False
        except Exception as e:
            print(f"[Telegram] Send failed: {e}")
            return False


def from_env() -> TelegramNotifier:
    """Convenience factory — reads token/chat_id from env vars."""
    return TelegramNotifier()
