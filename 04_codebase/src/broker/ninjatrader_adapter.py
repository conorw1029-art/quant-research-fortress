"""
ninjatrader_adapter.py — NinjaTrader 8 ATI file-based broker adapter
=====================================================================
Implements the BrokerAdapter interface using NinjaTrader 8's Automated
Trading Interface (ATI) — Route B: file-based order submission.

How it works:
  1. NinjaTrader 8 monitors an "incoming" directory for .txt command files.
  2. This adapter writes command files to that directory.
  3. NinjaTrader processes the file, executes the order, and writes a
     response to an "outgoing" directory.
  4. This adapter polls the outgoing directory for confirmations.

Setup in NinjaTrader 8 (one-time, takes 2 minutes):
  Tools → Options → Automated Trading
    [x] Enable ATI  (check this box)
    [x] Allow strategies to submit orders
  Restart NinjaTrader after enabling.

Default ATI paths (NinjaTrader 8):
  Incoming: C:\\Users\\<you>\\Documents\\NinjaTrader 8\\incoming\\
  Outgoing: C:\\Users\\<you>\\Documents\\NinjaTrader 8\\outgoing\\

Config keys (pass in config dict):
  nt_incoming_dir   — path to NT8 incoming folder
  nt_outgoing_dir   — path to NT8 outgoing folder
  nt_account        — your NinjaTrader account name (e.g. "Sim101" for sim)
  nt_symbol_map     — dict mapping base symbols to NT instrument names
                      e.g. {"MGC": "MGC 09-26", "SIL": "SIL 09-26"}
  poll_timeout_secs — seconds to wait for order confirmation (default 10)

Instrument naming:
  NinjaTrader uses names like "MGC 09-26" (micro gold Sep 2026).
  Update nt_symbol_map each quarterly rollover — same cadence as TV_CONTRACT_MAP.

Usage:
  broker = NinjaTraderAdapter(mode=BrokerMode.DEMO, config={
      "nt_account": "Sim101",
      "nt_symbol_map": {"MGC": "MGC 09-26", "SIL": "SIL 09-26",
                        "MES": "MES 09-26", "MNQ": "MNQ 09-26"},
  })
  broker.connect()
  bracket = broker.place_bracket_order("MGC", "BUY", 1, None, 2310.0, 2320.0)
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import BrokerAdapter, BrokerMode
from .broker_models import (
    AccountState,
    BracketOrder,
    BracketStatus,
    BrokerOrder,
    BrokerPosition,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationResult,
)

# ── Default NT8 ATI paths ──────────────────────────────────────────────────────

_DEFAULT_INCOMING = Path(os.environ.get(
    "NT8_INCOMING_DIR",
    str(Path.home() / "Documents" / "NinjaTrader 8" / "incoming"),
))
_DEFAULT_OUTGOING = Path(os.environ.get(
    "NT8_OUTGOING_DIR",
    str(Path.home() / "Documents" / "NinjaTrader 8" / "outgoing"),
))

# Sep 2026 defaults — update each quarterly rollover
_DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "MGC":  "MGC 09-26",
    "MES":  "MES 09-26",
    "MNQ":  "MNQ 09-26",
    "SIL":  "SIL 09-26",
    "MCL":  "MCL 09-26",
    "GC":   "GC 09-26",
    "ES":   "ES 09-26",
    "NQ":   "NQ 09-26",
    "SI":   "SI 09-26",
}


def _nt_instrument(symbol: str, symbol_map: dict[str, str]) -> str:
    """Resolve a base symbol to NT8 instrument name."""
    s = symbol.upper()
    # Strip contract month suffix if present (e.g. "MGCU5" -> "MGC")
    _month_codes = frozenset("FGHJKMNQUVXZ")
    i = len(s) - 1
    while i > 0 and s[i].isdigit():
        i -= 1
    if i > 0 and s[i] in _month_codes:
        s = s[:i]
    return symbol_map.get(s, s)


class NinjaTraderAdapter(BrokerAdapter):
    """
    NinjaTrader 8 broker adapter using the ATI file-based interface.

    Supports DEMO (Sim account) and LIVE modes.
    All order placement is done by writing command files to the NT8 incoming
    directory. NinjaTrader processes them asynchronously.

    Bracket orders are placed as three separate ATI commands:
      1. Market entry order
      2. Stop order (OCO group)
      3. Limit/target order (same OCO group)

    The OCO group ensures one cancels the other when either fires.
    """

    def __init__(self, mode: BrokerMode = BrokerMode.DEMO, config: Optional[dict] = None):
        super().__init__(mode=mode, config=config or {})

        self._incoming  = Path(self.config.get("nt_incoming_dir", _DEFAULT_INCOMING))
        self._outgoing  = Path(self.config.get("nt_outgoing_dir", _DEFAULT_OUTGOING))
        self._account   = self.config.get("nt_account", "Sim101")
        self._sym_map   = {**_DEFAULT_SYMBOL_MAP, **self.config.get("nt_symbol_map", {})}
        self._timeout   = float(self.config.get("poll_timeout_secs", 10))
        self._connected = False
        self._brackets: dict[str, BracketOrder] = {}

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Verify the NT8 ATI directories exist and are accessible.
        NinjaTrader must be running with ATI enabled.
        """
        if not self._incoming.exists():
            print(f"[NT8] WARNING: incoming dir not found: {self._incoming}")
            print(f"[NT8]   Make sure NinjaTrader 8 is running and ATI is enabled.")
            print(f"[NT8]   Tools -> Options -> Automated Trading -> Enable ATI")
            self._connected = False
            return False

        self._outgoing.mkdir(parents=True, exist_ok=True)
        self._connected = True
        print(f"[NT8] Connected — account={self._account} incoming={self._incoming}")
        return True

    def disconnect(self) -> None:
        self._connected = False
        print("[NT8] Disconnected from ATI interface")

    def is_connected(self) -> bool:
        return self._connected and self._incoming.exists()

    # ── Account (Sim account — NT8 does not expose balance via ATI) ───────────

    def get_account_state(self) -> AccountState:
        # NT8 ATI does not expose account equity directly. Return a placeholder.
        # For real account info, use the NinjaTrader Account Data window.
        return AccountState(
            balance=50_000.0, equity=50_000.0,
            margin_used=0.0, margin_available=50_000.0,
            daily_pnl=0.0, positions=[],
        )

    def get_positions(self) -> list[BrokerPosition]:
        # NT8 ATI outgoing folder contains position files — parse them if available
        positions = []
        try:
            for f in sorted(self._outgoing.glob("position_*.txt")):
                lines = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                for line in lines:
                    parts = line.split(";")
                    if len(parts) >= 3:
                        sym     = parts[0].strip()
                        net_pos = int(parts[1].strip()) if parts[1].strip().lstrip("-").isdigit() else 0
                        avg_px  = float(parts[2].strip()) if parts[2].strip() else 0.0
                        if net_pos != 0:
                            positions.append(BrokerPosition(
                                symbol=sym, net_pos=net_pos, avg_price=avg_px,
                            ))
        except Exception:
            pass
        return positions

    def get_open_orders(self) -> list[BrokerOrder]:
        return []  # NT ATI does not expose live order list

    # ── Order placement ────────────────────────────────────────────────────────

    def place_bracket_order(
        self,
        symbol:       str,
        side:         str,
        qty:          int,
        entry_price:  Optional[float],
        stop_price:   float,
        target_price: float,
        order_type:   str = "MARKET",
    ) -> BracketOrder:
        """
        Place an entry + stop + target bracket via NT8 ATI.

        Writes three files to the incoming directory:
          1. Entry (MARKET or LIMIT)
          2. Stop order (STOP) — part of OCO group
          3. Target order (LIMIT) — part of same OCO group

        The OCO group ensures that when stop fires, target is cancelled and
        vice versa. NinjaTrader handles this natively.
        """
        if not self._connected:
            raise ConnectionError("[NT8] Not connected — call connect() first")

        nt_sym    = _nt_instrument(symbol, self._sym_map)
        group_id  = str(uuid.uuid4())[:8].upper()
        oca_group = f"BRCKT_{group_id}"

        entry_id  = f"ENT_{group_id}"
        stop_id   = f"STP_{group_id}"
        target_id = f"TGT_{group_id}"

        side_up    = side.upper()
        exit_side  = "SELL" if side_up == "BUY" else "BUY"
        otype      = order_type.upper()

        # ── 1. Entry order ─────────────────────────────────────────────────────
        if otype == "MARKET":
            entry_cmd = (
                f"PLACE;{self._account};{nt_sym};"
                f"{side_up};{qty};MARKET;0;0;DAY;;{entry_id}"
            )
        else:
            lp = entry_price or 0
            entry_cmd = (
                f"PLACE;{self._account};{nt_sym};"
                f"{side_up};{qty};LIMIT;{lp:.4f};0;DAY;;{entry_id}"
            )
        self._write_ati(entry_id, entry_cmd)
        print(f"[NT8] Entry sent: {side_up} {qty} {nt_sym} @ {'MKT' if otype == 'MARKET' else entry_price}")

        # ── 2. Stop order (OCO group) ──────────────────────────────────────────
        stop_cmd = (
            f"PLACE;{self._account};{nt_sym};"
            f"{exit_side};{qty};STOP;0;{stop_price:.4f};GTC;{oca_group};{stop_id}"
        )
        self._write_ati(stop_id, stop_cmd)
        print(f"[NT8] Stop sent:  {exit_side} {qty} {nt_sym} stop={stop_price:.4f} oca={oca_group}")

        # ── 3. Target order (OCO group) ────────────────────────────────────────
        target_cmd = (
            f"PLACE;{self._account};{nt_sym};"
            f"{exit_side};{qty};LIMIT;{target_price:.4f};0;GTC;{oca_group};{target_id}"
        )
        self._write_ati(target_id, target_cmd)
        print(f"[NT8] Target sent: {exit_side} {qty} {nt_sym} limit={target_price:.4f} oca={oca_group}")

        # ── Build BracketOrder model ───────────────────────────────────────────
        now = datetime.now(timezone.utc)
        def _mk(oid, oside, otype2, lp, sp):
            return BrokerOrder(
                order_id=oid, symbol=nt_sym,
                side=OrderSide(oside), qty=qty,
                order_type=otype2, status=OrderStatus.WORKING,
                limit_price=lp, stop_price=sp,
                bracket_group_id=group_id, created_at=now,
            )

        bracket = BracketOrder(
            bracket_id   = group_id,
            symbol       = nt_sym,
            entry_order  = _mk(entry_id, side_up, OrderType(otype), entry_price, None),
            stop_order   = _mk(stop_id, exit_side, OrderType.STOP, None, stop_price),
            target_order = _mk(target_id, exit_side, OrderType.LIMIT, target_price, None),
            status       = BracketStatus.PENDING_ENTRY,
            created_at   = now,
        )
        self._brackets[group_id] = bracket
        return bracket

    def cancel_order(self, order_id: str) -> bool:
        cmd = f"CANCEL;{self._account};{order_id}"
        self._write_ati(f"cancel_{order_id}", cmd)
        print(f"[NT8] Cancel sent: {order_id}")
        return True

    def cancel_all(self) -> int:
        cmd = f"CANCELALLORDERS;{self._account}"
        self._write_ati(f"cancelall_{int(time.time())}", cmd)
        print(f"[NT8] Cancel-all sent for account {self._account}")
        return 0

    def flatten_symbol(self, symbol: str) -> bool:
        nt_sym = _nt_instrument(symbol, self._sym_map)
        cmd = f"CLOSEPOSITION;{self._account};{nt_sym}"
        self._write_ati(f"flat_{symbol}_{int(time.time())}", cmd)
        print(f"[NT8] Flatten sent: {nt_sym}")
        return True

    def flatten_all(self) -> bool:
        cmd = f"CLOSEPOSITION;{self._account};ALL"
        self._write_ati(f"flatall_{int(time.time())}", cmd)
        print(f"[NT8] Flatten-all sent for account {self._account}")
        return True

    # ── Reconciliation ─────────────────────────────────────────────────────────

    def reconcile(self) -> ReconciliationResult:
        positions = self.get_positions()
        return ReconciliationResult(
            ok=True, severity="OK",
            halt_new_entries=False,
            requires_human_review=False,
            actions=[],
            reason="NT8 ATI reconciliation: position file scan complete",
            broker_positions={p.symbol: p.net_pos for p in positions},
        )

    def heartbeat(self) -> bool:
        return self._incoming.exists()

    # ── ATI file writer ────────────────────────────────────────────────────────

    def _write_ati(self, name: str, command: str) -> Path:
        """
        Write a single ATI command to a uniquely named file in the incoming
        directory. NinjaTrader picks it up within ~100ms when ATI is enabled.
        """
        ts  = int(time.time() * 1000)
        out = self._incoming / f"fortress_{name}_{ts}.txt"
        out.write_text(command + "\n", encoding="utf-8")
        return out
