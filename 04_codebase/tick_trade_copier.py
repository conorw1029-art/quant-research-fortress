"""
tick_trade_copier.py — Fortress Trade Copier
============================================
Monitors your leader Tradovate account for fills.
Copies each fill to all follower accounts in real-time.
Runs as fortress-copier systemd service on the VPS 24/7.

Config via /opt/fortress/.env:
  COPIER_LEADER_NAME=TakeProfit
  COPIER_LEADER_USER=email@example.com
  COPIER_LEADER_PASS=password

  COPIER_FOLLOWER_1_NAME=Lucid
  COPIER_FOLLOWER_1_USER=email@example.com
  COPIER_FOLLOWER_1_PASS=password

  COPIER_FOLLOWER_2_NAME=Tradeify
  COPIER_FOLLOWER_2_USER=email@example.com
  COPIER_FOLLOWER_2_PASS=password

  COPIER_FOLLOWER_3_NAME=Apex
  COPIER_FOLLOWER_3_USER=email@example.com
  COPIER_FOLLOWER_3_PASS=password

  COPIER_POLL_SECS=2        (default: 2 seconds)
  COPIER_QTY_MULT=1         (multiply leader qty for followers, default 1:1)
  COPIER_DRY_RUN=false      (set true to log copies without executing)

Dashboard: adds status to 06_live_trading/state/copier_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Add parent dir so we can import tick_tradovate_client
sys.path.insert(0, str(Path(__file__).parent))
from tick_tradovate_client import TradovateClient, TradovateOrder

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
STATE_DIR = ROOT / "06_live_trading" / "state"
LOG_DIR   = ROOT / "06_live_trading" / "logs"
STATE_FILE = STATE_DIR / "copier_state.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=8,
        )
    except Exception:
        pass


# ── Account wrapper ────────────────────────────────────────────────────────────

@dataclass
class CopierAccount:
    name:     str
    username: str
    password: str
    client:   TradovateClient = field(init=False)
    ok:       bool            = field(default=False, init=False)

    def __post_init__(self):
        self.client = TradovateClient(
            username=self.username,
            password=self.password,
            app_id="FortressCopier",
            app_version="1.0",
            # cid/secret auto-read from TRADOVATE_CID / TRADOVATE_SECRET env vars
            demo=False,
        )

    def authenticate(self) -> bool:
        try:
            self.ok = self.client.authenticate()
            if self.ok:
                print(f"[Copier] {self.name}: authenticated (account_id={self.client.account_id})")
            else:
                print(f"[Copier] {self.name}: auth FAILED")
        except Exception as e:
            print(f"[Copier] {self.name}: auth error — {e}")
            self.ok = False
        return self.ok


# ── Main copier ────────────────────────────────────────────────────────────────

class TradeCopier:

    def __init__(self, leader: CopierAccount, followers: list[CopierAccount],
                 qty_mult: float = 1.0, dry_run: bool = False,
                 poll_secs: float = 2.0):
        self.leader       = leader
        self.followers    = followers
        self.qty_mult     = qty_mult
        self.dry_run      = dry_run
        self.poll_secs    = poll_secs
        self.seen_fill_ids: set[int] = set()
        self._contract_cache: dict[int, str] = {}
        self._copy_count  = 0
        self._error_count = 0
        self._start_time  = datetime.now(timezone.utc).isoformat()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate_all(self) -> bool:
        self.leader.authenticate()
        for f in self.followers:
            f.authenticate()
        if not self.leader.ok:
            print("[Copier] Leader auth failed — will retry (check credentials / rate limit)")
            return False
        ready  = [f.name for f in self.followers if f.ok]
        failed = [f.name for f in self.followers if not f.ok]
        print(f"[Copier] Followers ready: {ready}")
        if failed:
            print(f"[Copier] Followers FAILED (skipped): {failed}")
        return True  # leader OK is enough to proceed; failed followers just get skipped

    # ── Fill detection ─────────────────────────────────────────────────────────

    def _get_fills(self) -> list[dict]:
        try:
            fills = self.leader.client._get("/fill/list")
            if not isinstance(fills, list):
                return []
            return fills
        except Exception as e:
            print(f"[Copier] Error fetching fills: {e}")
            return []

    def _resolve_symbol(self, contract_id: int) -> str:
        if contract_id in self._contract_cache:
            return self._contract_cache[contract_id]
        try:
            result = self.leader.client._get("/contract/item", id=contract_id)
            sym = result.get("name", str(contract_id))
            self._contract_cache[contract_id] = sym
            return sym
        except Exception:
            return str(contract_id)

    def _seed_seen_fills(self) -> None:
        """On startup, mark all existing fills as already seen so we don't re-copy history."""
        fills = self._get_fills()
        for f in fills:
            self.seen_fill_ids.add(f.get("id", 0))
        print(f"[Copier] Seeded {len(self.seen_fill_ids)} existing fills — will only copy NEW fills from now on")

    # ── Copy logic ─────────────────────────────────────────────────────────────

    def _copy_fill(self, fill: dict) -> None:
        fill_id     = fill.get("id", "?")
        contract_id = fill.get("contractId", 0)
        action      = fill.get("action", "Buy")   # "Buy" or "Sell"
        qty         = fill.get("qty", 1)
        price       = fill.get("price", 0)
        symbol      = self._resolve_symbol(contract_id)

        follower_qty = max(1, round(qty * self.qty_mult))

        ts  = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        msg = (
            f"[Copier {ts}] LEADER fill #{fill_id}: {action} {qty}x {symbol} @ {price:.2f}\n"
            f"Copying {follower_qty}x to {len(self.followers)} followers..."
        )
        print(msg)

        results = []
        for follower in self.followers:
            if not follower.ok:
                results.append(f"  {follower.name}: SKIPPED (not authenticated)")
                continue
            if self.dry_run:
                results.append(f"  {follower.name}: DRY_RUN — would place {action} {follower_qty}x {symbol}")
                continue
            try:
                order = TradovateOrder(
                    symbol=symbol,
                    action=action,
                    qty=follower_qty,
                    order_type="Market",
                )
                resp = follower.client.place_order(order)
                order_id = resp.get("orderId") or resp.get("id") or "?"
                results.append(f"  {follower.name}: OK (order_id={order_id})")
                self._copy_count += 1
            except Exception as e:
                results.append(f"  {follower.name}: ERROR — {e}")
                self._error_count += 1

        summary = "\n".join(results)
        print(summary)

        # Telegram alert
        tg_msg = (
            f"FORTRESS COPIER\n"
            f"{action} {qty}x {symbol} @ {price:.2f}\n"
            f"{summary}"
        )
        _telegram(tg_msg)

        # Log to file
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = LOG_DIR / f"copier_{today}.jsonl"
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fill_id":   fill_id,
            "symbol":    symbol,
            "action":    action,
            "qty":       qty,
            "price":     price,
            "results":   results,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    # ── State file (for dashboard) ─────────────────────────────────────────────

    def _write_state(self) -> None:
        state = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "running":     True,
            "dry_run":     self.dry_run,
            "start_time":  self._start_time,
            "copy_count":  self._copy_count,
            "error_count": self._error_count,
            "leader":      {"name": self.leader.name, "ok": self.leader.ok,
                            "account_id": self.leader.client.account_id},
            "followers":   [{"name": f.name, "ok": f.ok,
                             "account_id": f.client.account_id}
                            for f in self.followers],
        }
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"[Copier] Starting — {mode} mode, polling every {self.poll_secs}s")
        print(f"[Copier] Leader: {self.leader.name}")
        print(f"[Copier] Followers: {[f.name for f in self.followers]}")

        while not self.authenticate_all():
            wait = 600  # 10 minutes between auth retries (avoids rate limit spiral)
            print(f"[Copier] Auth failed — retrying in {wait}s (Tradovate rate limit is 5/hr)")
            _telegram(f"FORTRESS COPIER: auth failed, retrying in {wait//60} min")
            time.sleep(wait)

        # Seed seen fills so we don't replay old history on startup
        self._seed_seen_fills()
        self._write_state()

        _telegram(
            f"FORTRESS COPIER STARTED ({mode})\n"
            f"Leader: {self.leader.name}\n"
            f"Followers: {', '.join(f.name for f in self.followers)}\n"
            f"Polling every {self.poll_secs}s"
        )

        print(f"[Copier] Watching for new fills on {self.leader.name}...")
        _reauth_counter = 0

        while True:
            try:
                fills = self._get_fills()
                new_fills = [f for f in fills if f.get("id", 0) not in self.seen_fill_ids]

                for fill in new_fills:
                    self._copy_fill(fill)
                    self.seen_fill_ids.add(fill.get("id", 0))

                self._write_state()

                # Re-auth every ~15 minutes to keep tokens fresh
                _reauth_counter += 1
                if _reauth_counter >= (900 / self.poll_secs):
                    _reauth_counter = 0
                    print("[Copier] Token refresh...")
                    self.authenticate_all()

            except KeyboardInterrupt:
                print("[Copier] Stopped by user")
                break
            except Exception as e:
                print(f"[Copier] Loop error: {e}")
                self._error_count += 1

            time.sleep(self.poll_secs)


# ── Config loader ──────────────────────────────────────────────────────────────

def _load_account(prefix: str) -> Optional[CopierAccount]:
    name = os.environ.get(f"{prefix}_NAME", "").strip()
    user = os.environ.get(f"{prefix}_USER", "").strip()
    pwd  = os.environ.get(f"{prefix}_PASS", "").strip()
    if not user or not pwd:
        return None
    return CopierAccount(name=name or prefix, username=user, password=pwd)


def _load_config() -> tuple[CopierAccount, list[CopierAccount]]:
    leader = _load_account("COPIER_LEADER")
    if not leader:
        print("[Copier] FATAL: COPIER_LEADER_USER and COPIER_LEADER_PASS must be set in .env")
        sys.exit(1)

    followers = []
    for i in range(1, 10):
        acc = _load_account(f"COPIER_FOLLOWER_{i}")
        if acc:
            followers.append(acc)

    if not followers:
        print("[Copier] FATAL: at least one COPIER_FOLLOWER_N_USER/PASS pair must be set in .env")
        sys.exit(1)

    return leader, followers


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Fortress trade copier")
    p.add_argument("--dry-run", action="store_true",
                   help="Log what would be copied without placing real orders")
    p.add_argument("--poll",    type=float, default=None,
                   help="Poll interval in seconds (default: COPIER_POLL_SECS env or 2)")
    p.add_argument("--mult",    type=float, default=None,
                   help="Qty multiplier for followers (default: COPIER_QTY_MULT env or 1.0)")
    args = p.parse_args()

    leader, followers = _load_config()

    dry_run = args.dry_run or os.environ.get("COPIER_DRY_RUN", "false").lower() == "true"
    poll    = args.poll  or float(os.environ.get("COPIER_POLL_SECS", "2"))
    mult    = args.mult  or float(os.environ.get("COPIER_QTY_MULT",  "1.0"))

    copier = TradeCopier(
        leader=leader,
        followers=followers,
        qty_mult=mult,
        dry_run=dry_run,
        poll_secs=poll,
    )
    copier.run()


if __name__ == "__main__":
    main()
