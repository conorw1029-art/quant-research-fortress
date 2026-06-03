"""
alert_bot.py — Multi-channel alert dispatcher
=============================================
AlertBot routes alert messages to Telegram, Discord webhook, and/or console.
It never reads market data or makes trading decisions — it only sends messages.

Level routing defaults:
  - INFO    → console only
  - WARNING → console + Telegram
  - CRITICAL → console + Telegram + Discord

All network failures return False and are logged as warnings —
they never raise exceptions (alerts must not crash the calling process).

Environment variables (read in __init__ if not passed):
  TELEGRAM_BOT_TOKEN    — Telegram bot API token
  TELEGRAM_CHAT_ID      — Target chat/channel ID (can be negative for groups)
  DISCORD_WEBHOOK_URL   — Full Discord webhook URL

Usage:
    from ai_brain.alert_bot import AlertBot

    bot = AlertBot()
    bot.send_alert("GC bars are stale", level="WARNING")
    bot.send_critical("KILL SWITCH ACTIVATED")
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

# Telegram API base URL
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Default routing by level
_DEFAULT_DESTINATIONS = {
    "INFO":     ["console"],
    "WARNING":  ["console", "telegram"],
    "CRITICAL": ["console", "telegram", "discord"],
}


class AlertBot:
    """
    Sends alert messages to one or more destinations.

    Args:
        telegram_token:   Telegram bot token. Reads TELEGRAM_BOT_TOKEN if None.
        telegram_chat_id: Telegram chat ID. Reads TELEGRAM_CHAT_ID if None.
        discord_webhook:  Discord webhook URL. Reads DISCORD_WEBHOOK_URL if None.
    """

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        discord_webhook: Optional[str] = None,
    ):
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.discord_webhook = discord_webhook or os.environ.get("DISCORD_WEBHOOK_URL")

    # ── Public send methods ────────────────────────────────────────────────────

    def send_telegram(self, message: str) -> bool:
        """
        Send a message via the Telegram Bot API.

        Args:
            message: Text to send. Long messages are truncated at 4096 chars
                     (Telegram API limit).

        Returns:
            True if the message was accepted by the API, False on any failure.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning(
                "[AlertBot] Telegram not configured. Set TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_CHAT_ID."
            )
            return False

        # Truncate to Telegram's limit
        text = message[:4096]

        try:
            import requests  # type: ignore

            url = _TELEGRAM_API.format(token=self.telegram_token)
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            else:
                logger.warning(
                    "[AlertBot] Telegram API error: status=%d body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

        except ImportError:
            logger.warning(
                "[AlertBot] 'requests' library not installed. "
                "Cannot send Telegram message."
            )
            return False
        except Exception as e:
            logger.warning("[AlertBot] Telegram send failed: %s", e)
            return False

    def send_discord(self, message: str) -> bool:
        """
        Send a message via a Discord webhook.

        Args:
            message: Text to send. Discord limit is 2000 chars; longer messages
                     are truncated.

        Returns:
            True if the webhook accepted the message, False on failure.
        """
        if not self.discord_webhook:
            logger.warning(
                "[AlertBot] Discord not configured. Set DISCORD_WEBHOOK_URL."
            )
            return False

        # Truncate to Discord's limit
        content = message[:2000]

        try:
            import requests  # type: ignore

            payload = {"content": content}
            resp = requests.post(self.discord_webhook, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                return True
            else:
                logger.warning(
                    "[AlertBot] Discord webhook error: status=%d body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

        except ImportError:
            logger.warning(
                "[AlertBot] 'requests' library not installed. "
                "Cannot send Discord message."
            )
            return False
        except Exception as e:
            logger.warning("[AlertBot] Discord send failed: %s", e)
            return False

    def send_console(self, message: str, level: str = "INFO") -> None:
        """
        Print a message to stdout with a timestamp and level prefix.

        Args:
            message: Text to print.
            level:   Log level label ("INFO", "WARNING", "CRITICAL", etc.).
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prefix = f"[{ts}] [{level}] [AlertBot]"
        print(f"{prefix} {message}")

    def send_alert(
        self,
        message: str,
        level: str = "INFO",
        destinations: Optional[List[str]] = None,
    ) -> None:
        """
        Route an alert to the appropriate destinations based on level.

        If destinations is explicitly provided, it overrides the default
        level-based routing.

        Default routing:
          - INFO    → console
          - WARNING → console + telegram
          - CRITICAL → console + telegram + discord

        Args:
            message:      Alert text.
            level:        "INFO", "WARNING", or "CRITICAL".
            destinations: Optional override list of destination names.
                          Valid values: "console", "telegram", "discord".
        """
        level = level.upper()

        if destinations is None:
            destinations = _DEFAULT_DESTINATIONS.get(level, ["console"])

        for dest in destinations:
            dest_lower = dest.lower().strip()
            if dest_lower == "console":
                self.send_console(message, level=level)
            elif dest_lower == "telegram":
                result = self.send_telegram(f"[{level}] {message}")
                if not result:
                    # Fallback: at least log to console
                    self.send_console(
                        f"(Telegram delivery failed) {message}",
                        level=level,
                    )
            elif dest_lower == "discord":
                result = self.send_discord(f"**[{level}]** {message}")
                if not result:
                    self.send_console(
                        f"(Discord delivery failed) {message}",
                        level=level,
                    )
            else:
                logger.warning("[AlertBot] Unknown destination: '%s'", dest)

    def send_critical(self, message: str) -> None:
        """
        Send a CRITICAL alert to all configured destinations immediately.

        Args:
            message: Critical alert text. Will be prefixed with CRITICAL marker.
        """
        critical_msg = f"CRITICAL ALERT: {message}"
        self.send_console(critical_msg, level="CRITICAL")
        self.send_telegram(f"[CRITICAL] {message}")
        self.send_discord(f"**[CRITICAL]** :red_circle: {message}")
