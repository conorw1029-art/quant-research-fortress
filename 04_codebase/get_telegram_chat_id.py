#!/usr/bin/env python3
"""
get_telegram_chat_id.py
=======================
Run this once after creating your bot to find your Telegram Chat ID.

Steps:
  1. Create a bot via @BotFather in Telegram (/newbot)
  2. Copy the token it gives you
  3. Open the bot in Telegram and send it any message (e.g. "hi")
  4. Run this script:  python 04_codebase/get_telegram_chat_id.py
  5. Copy the Chat ID it prints
"""

import json
import os
import urllib.request


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        token = input("Paste your bot token: ").strip()
    if not token:
        print("ERROR: No token provided.")
        return

    print("\nMake sure you sent a message to your bot in Telegram first.")
    input("Press Enter when ready...")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR: Could not reach Telegram API: {e}")
        return

    results = data.get("result", [])
    if not results:
        print("\nNo messages found.")
        print("Make sure you sent a message to your bot BEFORE running this script.")
        print("Then try again.")
        return

    seen = set()
    for r in results:
        msg  = r.get("message", {})
        chat = msg.get("chat", {})
        cid  = chat.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            name = f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
            print(f"\n  Chat ID : {cid}")
            print(f"  Name    : {name or '(unknown)'}")
            print(f"  Type    : {chat.get('type', '?')}")

    if seen:
        print("\n--- Copy one of the Chat IDs above, then set these env vars ---")
        print(f"  $env:TELEGRAM_BOT_TOKEN = \"{token}\"")
        print(f"  $env:TELEGRAM_CHAT_ID   = \"{next(iter(seen))}\"")
        print("\nThen run the executor and you'll get alerts on your phone.")


if __name__ == "__main__":
    main()
