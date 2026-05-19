"""
tick_tradovate_client.py — Tradovate REST API Client
=====================================================
Used for Lucid Trading (and any Tradovate-connected account).

Capabilities:
  - Authentication (OAuth2 access token)
  - Account info + current positions
  - Place market / stop orders
  - Close positions
  - Live price quotes
  - Account P&L and balance

Tradovate API docs: https://api.tradovate.com/v1/docs

Setup:
  1. Log into Tradovate / Lucid Trading
  2. Go to Settings → API Credentials
  3. Create an API key (name, CID, secret)
  4. Set environment variables or pass to TradovateClient()

Usage:
  from tick_tradovate_client import TradovateClient, TradovateOrder
  client = TradovateClient(username="your@email.com", password="yourpass",
                           app_id="YourApp", app_version="1.0",
                           cid=12345, secret="yoursecret")
  client.authenticate()

  # Place a market long order (1 MES contract)
  order = TradovateOrder(symbol="MESM5", action="Buy", qty=1, order_type="Market")
  result = client.place_order(order)

  # Check positions
  positions = client.get_positions()

  # Close all
  client.close_all_positions()
"""

import json
import os
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error
import urllib.parse

# ── Configuration ─────────────────────────────────────────────────────────────

TRADOVATE_LIVE_URL = "https://live.tradovateapi.com/v1"
TRADOVATE_DEMO_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_MD_URL   = "https://md.tradovateapi.com/v1"   # market data

# Micro contract symbol mapping (month codes: F=Jan G=Feb H=Mar J=Apr K=May M=Jun N=Jul Q=Aug U=Sep V=Oct X=Nov Z=Dec)
# For live trading, use current front month
MICRO_SYMBOLS = {
    "MGC": "MGCM5",   # MGC June 2026 — UPDATE TO CURRENT MONTH
    "MES": "MESM5",   # MES June 2026
    "MNQ": "MNQM5",   # MNQ June 2026
}

# Point values for risk calculation — keyed by base symbol (first 2-3 chars)
_POINT_VALUE_MAP = {
    "MGC": 10.0,   # $10/pt
    "MES": 5.0,    # $5/pt
    "MNQ": 2.0,    # $2/pt
    "GC":  100.0,  # $100/pt
    "ES":  50.0,   # $50/pt
    "NQ":  20.0,   # $20/pt
    "SI":  5000.0, # $5000/pt
    "SIL": 1000.0, # $1000/pt
}

# Hard limits for bracket orders
MAX_BRACKET_CONTRACTS = 5
MAX_BRACKET_RISK_USD  = 200.0

# Live trading env var (must match executor)
_LIVE_ENABLE_ENV   = "FORTRESS_LIVE_ENABLE"
_LIVE_ENABLE_VALUE = "YES_I_UNDERSTAND"

# OSO endpoint (all three legs sent atomically)
_ENDPOINT_PLACE_OSO = "/order/placeOSO"

# Minimum tick sizes (in points) per instrument base symbol
_TICK_SIZE_MAP = {
    "MGC": 0.10,
    "MES": 0.25,
    "MNQ": 0.25,
    "GC":  0.10,
    "ES":  0.25,
    "NQ":  0.25,
    "SI":  0.005,
    "SIL": 0.005,
}

# Kill switch file — read at every bracket order attempt
_KILL_SWITCH_PATH = Path(__file__).parent / "KILL_SWITCH.txt"

# Client order IDs issued this process lifetime (duplicate protection)
_issued_client_order_ids: set = set()

# OSO exchange-verification flag — set to True only after real exchange test confirms
# the payload structure, response shape, and OCO/OSO ID fields are correct.
_OSO_EXCHANGE_VERIFIED = False


def _point_value(symbol: str) -> float:
    """Look up point value for a symbol, using 3-char then 2-char prefix match."""
    base3 = symbol[:3].upper()
    base2 = symbol[:2].upper()
    return _POINT_VALUE_MAP.get(base3) or _POINT_VALUE_MAP.get(base2) or 5.0


def _tick_size(symbol: str) -> float:
    """Return minimum tick size for a symbol."""
    base3 = symbol[:3].upper()
    base2 = symbol[:2].upper()
    return _TICK_SIZE_MAP.get(base3) or _TICK_SIZE_MAP.get(base2) or 0.25


def _is_tick_rounded(price: float, symbol: str) -> bool:
    """Return True if price is a valid multiple of the instrument's tick size."""
    tick = _tick_size(symbol)
    remainder = round(price % tick, 8)
    return remainder < 1e-7 or abs(remainder - tick) < 1e-7


def _read_kill_switch(path: Path = None) -> str:
    """
    Return the current kill switch value, uppercased and stripped.
    Returns "RUN" if file is missing (safe default — don't block everything on missing file).
    Returns "STOP" if file content is STOP (case-insensitive).
    """
    p = path or _KILL_SWITCH_PATH
    try:
        content = Path(p).read_text(encoding="utf-8").strip().upper()
        return content if content in ("RUN", "STOP") else "RUN"
    except FileNotFoundError:
        return "RUN"
    except Exception:
        return "STOP"   # any read error → conservatively treat as STOP


@dataclass
class BracketOrderResult:
    """Structured result from place_bracket_order()."""
    ok:              bool
    mode:            str            # "DRY_RUN" | "DEMO" | "LIVE"
    entry_order_id:  Optional[int]
    stop_order_id:   Optional[int]
    target_order_id: Optional[int]
    oco_id:          Optional[str]  # OCO group ID linking stop + target (exchange-assigned)
    oso_id:          Optional[str]  # OSO group ID linking entry + OCO bracket (exchange-assigned)
    client_order_id: str
    reason:          str
    payload:         dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok":              self.ok,
            "mode":            self.mode,
            "entry_order_id":  self.entry_order_id,
            "stop_order_id":   self.stop_order_id,
            "target_order_id": self.target_order_id,
            "oco_id":          self.oco_id,
            "oso_id":          self.oso_id,
            "client_order_id": self.client_order_id,
            "reason":          self.reason,
            "payload":         self.payload,
        }


@dataclass
class TradovateOrder:
    symbol:     str                    # e.g., "MESM5"
    action:     str                    # "Buy" or "Sell"
    qty:        int                    # number of contracts
    order_type: str = "Market"         # "Market", "Limit", "Stop"
    price:      Optional[float] = None # required for Limit
    stop_price: Optional[float] = None # required for Stop
    time_in_force: str = "Day"         # "Day", "GTC", "IOC"


@dataclass
class Position:
    account_id:  int
    contract_id: int
    symbol:      str
    net_pos:     int       # +ve = long, -ve = short
    avg_price:   float
    open_pnl:    float
    closed_pnl:  float


class TradovateClient:
    """
    Tradovate REST API client for Lucid Trading / any Tradovate account.
    Use demo=True for paper trading (strongly recommended for first 2 weeks).
    """

    def __init__(self, username: str, password: str,
                 app_id: str = "QuantBot", app_version: str = "1.0",
                 cid: int = 0, secret: str = "",
                 demo: bool = True):
        self.username    = username
        self.password    = password
        self.app_id      = app_id
        self.app_version = app_version
        self.cid         = cid
        self.secret      = secret
        self.demo        = demo

        self.base_url    = TRADOVATE_DEMO_URL if demo else TRADOVATE_LIVE_URL
        self.access_token: Optional[str]  = None
        self.token_expiry: Optional[float] = None
        self.account_id:  Optional[int]   = None
        self._lock = threading.Lock()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """Get access token. Call once; auto-refresh handled internally."""
        payload = {
            "name":       self.username,
            "password":   self.password,
            "appId":      self.app_id,
            "appVersion": self.app_version,
            "cid":        self.cid,
            "sec":        self.secret,
            "deviceId":   "quantbot-001",
        }
        try:
            resp = self._post("/auth/accesstokenrequest", payload, auth=False)
            if "accessToken" not in resp:
                print(f"[Tradovate] Auth failed: {resp}")
                return False
            self.access_token = resp["accessToken"]
            expires_in = resp.get("expirationTime", 86400000)  # ms
            self.token_expiry = time.time() + expires_in / 1000 - 60
            print(f"[Tradovate] Authenticated. Token expires in {expires_in//1000//60:.0f} minutes")

            # Get account
            accounts = self._get("/account/list")
            if accounts:
                self.account_id = accounts[0]["id"]
                print(f"[Tradovate] Account ID: {self.account_id}")
            return True
        except Exception as e:
            print(f"[Tradovate] Auth error: {e}")
            return False

    def _ensure_auth(self):
        if self.token_expiry and time.time() > self.token_expiry:
            print("[Tradovate] Token expired, re-authenticating...")
            self.authenticate()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _request(self, method: str, endpoint: str, payload: dict = None,
                 auth: bool = True) -> dict:
        self._ensure_auth()
        url  = self.base_url + endpoint
        data = json.dumps(payload).encode() if payload else None
        headers = {"Content-Type": "application/json"}
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        req  = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code} on {endpoint}: {body}")

    def _get(self, endpoint: str, **params) -> dict:
        if params:
            endpoint += "?" + urllib.parse.urlencode(params)
        return self._request("GET", endpoint)

    def _post(self, endpoint: str, payload: dict = None, auth: bool = True) -> dict:
        return self._request("POST", endpoint, payload, auth=auth)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Return account balance and risk info."""
        if not self.account_id:
            return {}
        return self._get(f"/cashbalance/getcashbalancesnapshot", accountId=self.account_id)

    def get_positions(self) -> list[Position]:
        """Return all open positions."""
        if not self.account_id:
            return []
        raw = self._get("/position/list")
        positions = []
        for p in raw:
            if p.get("netPos", 0) == 0:
                continue
            sym = self._contract_id_to_symbol(p.get("contractId", 0))
            positions.append(Position(
                account_id  = p.get("accountId", 0),
                contract_id = p.get("contractId", 0),
                symbol      = sym,
                net_pos     = p.get("netPos", 0),
                avg_price   = p.get("avgPrice", 0),
                open_pnl    = p.get("openPnL", 0),
                closed_pnl  = p.get("closedPnL", 0),
            ))
        return positions

    def _contract_id_to_symbol(self, contract_id: int) -> str:
        try:
            result = self._get(f"/contract/item", id=contract_id)
            return result.get("name", str(contract_id))
        except Exception:
            return str(contract_id)

    def get_daily_pnl(self) -> float:
        """Return today's realized + unrealized P&L."""
        positions = self.get_positions()
        return sum(p.open_pnl + p.closed_pnl for p in positions)

    # ── Order Placement ───────────────────────────────────────────────────────

    def get_contract_id(self, symbol: str) -> Optional[int]:
        """Look up contract ID for a symbol like 'MESM5'."""
        try:
            results = self._get("/contract/suggest", text=symbol, limit=5)
            for r in (results if isinstance(results, list) else [results]):
                if r.get("name", "").upper() == symbol.upper():
                    return r["id"]
            return None
        except Exception:
            return None

    def place_order(self, order: TradovateOrder) -> dict:
        """
        Place an order. Returns order confirmation dict.
        For market orders: immediate fill, no price needed.
        """
        if not self.account_id:
            raise RuntimeError("Not authenticated or no account found")

        contract_id = self.get_contract_id(order.symbol)
        if not contract_id:
            raise ValueError(f"Contract not found: {order.symbol}")

        payload: dict = {
            "accountId":   self.account_id,
            "action":      order.action,       # "Buy" / "Sell"
            "symbol":      order.symbol,
            "orderQty":    order.qty,
            "orderType":   order.order_type,   # "Market" / "Limit" / "Stop"
            "timeInForce": order.time_in_force,
        }
        if order.price is not None:
            payload["price"] = order.price
        if order.stop_price is not None:
            payload["stopPrice"] = order.stop_price

        result = self._post("/order/placeorder", payload)
        print(f"[Tradovate] Order placed: {order.action} {order.qty} {order.symbol} → {result}")
        return result

    def close_position(self, symbol: str) -> dict:
        """Close existing position in symbol (flatten)."""
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol and pos.net_pos != 0:
                action = "Sell" if pos.net_pos > 0 else "Buy"
                qty    = abs(pos.net_pos)
                order  = TradovateOrder(symbol=symbol, action=action, qty=qty)
                return self.place_order(order)
        print(f"[Tradovate] No open position in {symbol}")
        return {}

    def close_all_positions(self) -> list[dict]:
        """Close all open positions. Call before weekend/end of session."""
        results = []
        for pos in self.get_positions():
            if pos.net_pos != 0:
                r = self.close_position(pos.symbol)
                results.append(r)
        return results

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> dict:
        """Get latest quote (bid/ask/last) for a symbol."""
        try:
            contract_id = self.get_contract_id(symbol)
            if not contract_id:
                return {}
            result = self._get("/md/getquotesnapshot", contractIds=contract_id)
            return result[0] if isinstance(result, list) and result else result
        except Exception as e:
            return {"error": str(e)}

    def status_line(self) -> str:
        """One-line account status for display."""
        try:
            info = self.get_account_info()
            pnl  = self.get_daily_pnl()
            bal  = info.get("cashBalance", 0)
            mode = "DEMO" if self.demo else "LIVE"
            return f"[Tradovate {mode}] Balance=${bal:,.0f}  DailyPnL=${pnl:+,.0f}"
        except Exception:
            return "[Tradovate] status unavailable"

    # ── Order management ──────────────────────────────────────────────────────

    def cancel_order(self, order_id: int) -> dict:
        """Cancel an open order by ID."""
        if not self.account_id:
            raise RuntimeError("Not authenticated")
        return self._post("/order/cancelorder", {"orderId": order_id})

    def get_order_status(self, order_id: int) -> dict:
        """Get current status of an order."""
        return self._get("/order/item", id=order_id)

    def get_open_orders(self) -> list[dict]:
        """
        Return all orders in a working / contingent state for this account.
        Useful for finding active bracket stop/target legs after a restart.
        """
        if not self.account_id:
            return []
        raw = self._get("/order/list")
        if not isinstance(raw, list):
            return []
        _open_statuses = {"Working", "Accepted", "PendingNew",
                          "ContingencyOrder", "PendingReplace"}
        return [o for o in raw if o.get("ordStatus") in _open_statuses]

    def get_bracket_order_ids_by_symbol(self) -> dict:
        """
        Return {base_symbol: [order_id, ...]} for all active bracket legs.

        Calls get_open_orders() (which filters to working/contingent statuses),
        then strips month codes to group by base symbol (e.g. "MES", "MGC").

        Returns {} if not authenticated or on any error.
        """
        _bracket_statuses = {
            "Working", "ContingencyOrder", "PendingNew", "Accepted", "PendingReplace"
        }
        _month_codes = frozenset("FGHJKMNQUVXZ")

        def _strip(sym: str) -> str:
            s = sym.upper().strip()
            i = len(s) - 1
            while i > 0 and s[i].isdigit():
                i -= 1
            return s[:i] if i > 0 and s[i] in _month_codes else s

        try:
            raw = self.get_open_orders()
            result: dict = {}
            for o in raw:
                if o.get("ordStatus") not in _bracket_statuses:
                    continue
                order_id = o.get("id") or o.get("orderId")
                if not order_id:
                    continue
                sym = o.get("symbol", "")
                if not sym:
                    cid = o.get("contractId")
                    if cid:
                        sym = self._contract_id_to_symbol(int(cid))
                if not sym:
                    continue
                base = _strip(str(sym))
                result.setdefault(base, []).append(int(order_id))
            return result
        except Exception:
            return {}

    def confirm_bracket_alive(self, stop_order_id: int, target_order_id: int,
                               max_wait_seconds: float = 30.0,
                               poll_interval: float = 2.0) -> dict:
        """
        Poll until both bracket legs show a working/contingent status.

        Returns:
            {"alive": bool, "stop_status": str, "target_status": str,
             "elapsed": float, "reason": str}
        alive=True only when both legs are in the alive set.
        """
        _alive = {"Working", "ContingencyOrder", "Accepted"}
        _terminal = {"Filled", "Cancelled", "Rejected", "Expired", "Error"}
        start = time.time()
        stop_status = "Unknown"
        target_status = "Unknown"
        while time.time() - start < max_wait_seconds:
            try:
                sr = self.get_order_status(stop_order_id)
                stop_status = sr.get("ordStatus", "Unknown")
            except Exception:
                stop_status = "Unknown"
            try:
                tr = self.get_order_status(target_order_id)
                target_status = tr.get("ordStatus", "Unknown")
            except Exception:
                target_status = "Unknown"
            elapsed = round(time.time() - start, 2)
            if stop_status in _alive and target_status in _alive:
                return {
                    "alive": True,
                    "stop_status": stop_status, "target_status": target_status,
                    "elapsed": elapsed, "reason": "ok",
                }
            if stop_status in _terminal or target_status in _terminal:
                return {
                    "alive": False,
                    "stop_status": stop_status, "target_status": target_status,
                    "elapsed": elapsed, "reason": "bracket_leg_terminal_state",
                }
            time.sleep(poll_interval)
        elapsed = round(time.time() - start, 2)
        return {
            "alive": False,
            "stop_status": stop_status, "target_status": target_status,
            "elapsed": elapsed, "reason": "timeout",
        }

    def get_positions_dict(self) -> dict[str, dict]:
        """
        Return current broker positions as {symbol: {net_pos, avg_price, open_pnl}}.
        Symbols are resolved to their names (e.g. "MESM5").
        Excludes flat (netPos=0) positions.
        """
        positions = self.get_positions()
        result = {}
        for p in positions:
            if p.net_pos != 0:
                result[p.symbol] = {
                    "net_pos":   p.net_pos,
                    "avg_price": p.avg_price,
                    "open_pnl":  p.open_pnl,
                }
        return result

    # ── Bracket / OSO orders ──────────────────────────────────────────────────

    @classmethod
    def create_dry_run(cls) -> "TradovateClient":
        """Minimal client for dry-run bracket order validation only. No API calls made."""
        return cls(username="", password="", cid=0, secret="", demo=True)

    def _build_oso_payload(self, symbol: str, side: str, quantity: int,
                           entry_type: str, entry_price: Optional[float],
                           stop_price: float, target_price: float,
                           account_id: Optional[int] = None) -> dict:
        """
        Build a Tradovate placeOSO JSON payload.

        Structure: entry order + OCO bracket (stop + target) that activates on fill.
        Per Tradovate API: the entry 'first' order, when filled, sends the 'second'
        OCO order (limit target with stop-loss 'other' leg).
        """
        act      = side.capitalize()          # "Buy" or "Sell"
        opp_act  = "Sell" if side == "BUY" else "Buy"
        acct_id  = account_id or self.account_id or 0

        entry_order: dict = {
            "accountId":   acct_id,
            "action":      act,
            "symbol":      symbol,
            "orderQty":    quantity,
            "orderType":   entry_type.capitalize(),   # "Market" or "Limit"
            "timeInForce": "Day",
        }
        if entry_type.upper() == "LIMIT" and entry_price is not None:
            entry_order["price"] = entry_price

        bracket: dict = {
            "orderQty":    quantity,
            "action":      opp_act,
            "orderType":   "Limit",
            "price":       target_price,
            "timeInForce": "GTC",
            "other": {
                "orderQty":    quantity,
                "action":      opp_act,
                "orderType":   "Stop",
                "stopPrice":   stop_price,
                "timeInForce": "GTC",
            },
        }

        return {"first": entry_order, "second": bracket}

    def place_bracket_order(
        self,
        symbol:          str,
        side:            str,
        quantity:        int,
        entry_type:      str,
        entry_price:     Optional[float],
        stop_price:      float,
        target_price:    float,
        account_id:      Optional[str]  = None,
        demo:            bool           = True,
        client_order_id: Optional[str]  = None,
        dry_run:         bool           = True,
        strategy_key:    Optional[str]  = None,
        session_open:    Optional[bool] = None,
    ) -> dict:
        """
        Place a bracket order (entry + broker-native stop + broker-native target).

        Uses Tradovate's /order/placeOSO endpoint so all three legs are sent
        atomically. The stop and target are held at the exchange — if the Python
        process crashes, the position is still protected.

        Args:
            symbol:          Tradovate contract symbol, e.g. "MESM5"
            side:            "BUY" or "SELL" (case-insensitive)
            quantity:        Number of contracts (>0, <= MAX_BRACKET_CONTRACTS)
            entry_type:      "Market" or "Limit"
            entry_price:     Required for Limit; used for risk validation on Market
            stop_price:      Stop-loss level (broker-native)
            target_price:    Take-profit level (broker-native, OCO with stop)
            account_id:      Override account ID (default: uses authenticated account)
            demo:            True = paper account; False = live (requires env var)
            client_order_id: Optional idempotency key (auto-generated if None)
            dry_run:         True = validate and log payload, no API call made

        Returns:
            BracketOrderResult.as_dict() — always contains:
              ok, mode, entry_order_id, stop_order_id, target_order_id,
              client_order_id, reason, payload
        """
        # ── Client order ID ───────────────────────────────────────────────────
        if not client_order_id:
            client_order_id = f"fortress-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        mode_str = "DRY_RUN" if dry_run else ("DEMO" if demo else "LIVE")

        def _fail(reason: str) -> dict:
            print(f"[Tradovate] place_bracket_order REJECTED ({mode_str}): {reason}")
            return BracketOrderResult(
                ok=False, mode=mode_str,
                entry_order_id=None, stop_order_id=None, target_order_id=None,
                oco_id=None, oso_id=None,
                client_order_id=client_order_id, reason=reason,
            ).as_dict()

        # ── Kill switch check (first — before anything else) ──────────────────
        ks = _read_kill_switch()
        if ks == "STOP":
            return _fail("KILL_SWITCH_STOP: trading is halted by kill switch")

        # ── Session-open check ────────────────────────────────────────────────
        # session_open=None means "caller didn't specify" — trust account_state.json
        # session_open=True/False means caller explicitly set it (used in tests)
        if session_open is False:
            return _fail("SESSION_CLOSED: market session is not open")

        # ── Duplicate client_order_id protection ──────────────────────────────
        if client_order_id in _issued_client_order_ids:
            return _fail(f"DUPLICATE_CLIENT_ORDER_ID: {client_order_id} already issued this session")

        # ── OSO exchange-verification gate (non-dry-run) ──────────────────────
        if not dry_run and not _OSO_EXCHANGE_VERIFIED:
            return _fail(
                "BRACKET_OSO_UNVERIFIED: OSO payload format and response parsing have not been "
                "verified against the real Tradovate exchange. Cannot place real bracket orders "
                "until exchange verification is complete. Use dry_run=True."
            )

        # ── Validation ────────────────────────────────────────────────────────
        if not symbol or not symbol.strip():
            return _fail("INVALID_SYMBOL: symbol is empty")

        side = side.upper()
        if side not in ("BUY", "SELL"):
            return _fail(f"INVALID_SIDE: '{side}' — must be BUY or SELL")

        if quantity <= 0:
            return _fail(f"INVALID_QUANTITY: {quantity} — must be > 0")

        if quantity > MAX_BRACKET_CONTRACTS:
            return _fail(f"QUANTITY_EXCEEDS_MAX: {quantity} > {MAX_BRACKET_CONTRACTS} contracts")

        if entry_type.upper() not in ("MARKET", "LIMIT"):
            return _fail(f"INVALID_ENTRY_TYPE: '{entry_type}' — must be Market or Limit")

        if entry_type.upper() == "LIMIT" and entry_price is None:
            return _fail("ENTRY_PRICE_REQUIRED: entry_price required for Limit orders")

        # ── Tick rounding validation ──────────────────────────────────────────
        if entry_price is not None and not _is_tick_rounded(entry_price, symbol):
            return _fail(f"OFF_TICK_PRICE: entry_price {entry_price} is not a valid tick for {symbol} (tick={_tick_size(symbol)})")
        if not _is_tick_rounded(stop_price, symbol):
            return _fail(f"OFF_TICK_PRICE: stop_price {stop_price} is not a valid tick for {symbol} (tick={_tick_size(symbol)})")
        if not _is_tick_rounded(target_price, symbol):
            return _fail(f"OFF_TICK_PRICE: target_price {target_price} is not a valid tick for {symbol} (tick={_tick_size(symbol)})")

        ref_price = entry_price
        if ref_price is None:
            # Market order — can't validate direction without price, skip directional checks
            pass
        else:
            if side == "BUY":
                if stop_price >= ref_price:
                    return _fail(
                        f"STOP_ABOVE_ENTRY_FOR_BUY: stop {stop_price} must be below entry {ref_price}"
                    )
                if target_price <= ref_price:
                    return _fail(
                        f"TARGET_BELOW_ENTRY_FOR_BUY: target {target_price} must be above entry {ref_price}"
                    )
            else:  # SELL
                if stop_price <= ref_price:
                    return _fail(
                        f"STOP_BELOW_ENTRY_FOR_SELL: stop {stop_price} must be above entry {ref_price}"
                    )
                if target_price >= ref_price:
                    return _fail(
                        f"TARGET_ABOVE_ENTRY_FOR_SELL: target {target_price} must be below entry {ref_price}"
                    )

        stop_dist = abs((ref_price or stop_price) - stop_price)
        if stop_dist == 0:
            return _fail("ZERO_STOP_DISTANCE: stop distance is zero")

        tgt_dist = abs((ref_price or target_price) - target_price)
        if tgt_dist == 0:
            return _fail("ZERO_TARGET_DISTANCE: target distance is zero")

        if ref_price is not None:
            pv          = _point_value(symbol)
            est_risk    = abs(ref_price - stop_price) * pv * quantity
            if est_risk > MAX_BRACKET_RISK_USD:
                return _fail(
                    f"ESTIMATED_RISK_EXCEEDS_LIMIT: ${est_risk:,.2f} > ${MAX_BRACKET_RISK_USD:,.0f}"
                )

        # ── Live mode guard ───────────────────────────────────────────────────
        if not demo and not dry_run:
            live_env = os.environ.get(_LIVE_ENABLE_ENV, "")
            if live_env != _LIVE_ENABLE_VALUE:
                return _fail(
                    f"live mode requires env var {_LIVE_ENABLE_ENV}={_LIVE_ENABLE_VALUE}"
                )

        # ── Build payload ─────────────────────────────────────────────────────
        acct_id = int(account_id) if account_id else (self.account_id or 0)
        payload = self._build_oso_payload(
            symbol=symbol, side=side, quantity=quantity,
            entry_type=entry_type, entry_price=entry_price,
            stop_price=stop_price, target_price=target_price,
            account_id=acct_id,
        )
        payload["clientOrderId"] = client_order_id

        print(
            f"[Tradovate] place_bracket_order({mode_str}) | "
            f"{side} {quantity}x {symbol} | "
            f"entry={'MKT' if not entry_price else entry_price} | "
            f"stop={stop_price} | target={target_price} | "
            f"id={client_order_id}"
        )

        # ── Register client_order_id (after all validation passes) ───────────
        _issued_client_order_ids.add(client_order_id)

        # ── Dry-run — return payload without API call ─────────────────────────
        if dry_run:
            print(f"[Tradovate] DRY_RUN — payload validated, no API call made")
            return BracketOrderResult(
                ok=True, mode="DRY_RUN",
                entry_order_id=None, stop_order_id=None, target_order_id=None,
                oco_id=None, oso_id=None,
                client_order_id=client_order_id, reason="",
                payload=payload,
            ).as_dict()

        # ── Demo / live API call ──────────────────────────────────────────────
        if not self.account_id:
            return _fail("not authenticated — call authenticate() before placing orders")

        try:
            self._ensure_auth()
            resp = self._post(_ENDPOINT_PLACE_OSO, payload)

            # Tradovate placeOSO returns an array of order confirmation objects
            # [entry_confirmation, stop_confirmation, target_confirmation]
            if isinstance(resp, list) and len(resp) >= 1:
                entry_conf  = resp[0] if len(resp) > 0 else {}
                stop_conf   = resp[1] if len(resp) > 1 else {}
                target_conf = resp[2] if len(resp) > 2 else {}

                entry_id  = entry_conf.get("orderId") or entry_conf.get("id")
                stop_id   = stop_conf.get("orderId") or stop_conf.get("id")
                target_id = target_conf.get("orderId") or target_conf.get("id")

                # Extract OCO/OSO group IDs if present in response
                # NOTE: field names unverified — update after exchange test confirms shape
                oco_id = (entry_conf.get("ocoId") or stop_conf.get("ocoId")
                          or target_conf.get("ocoId"))
                oso_id = (entry_conf.get("osoId") or entry_conf.get("contingencyOrderId"))

                if not entry_id or not stop_id or not target_id:
                    print(f"[Tradovate] WARNING: incomplete order IDs in placeOSO response — "
                          f"entry={entry_id} stop={stop_id} target={target_id}")
                    print(f"[Tradovate] Raw response: {resp}")

                print(
                    f"[Tradovate] Bracket placed: entry={entry_id} "
                    f"stop={stop_id} target={target_id} oco={oco_id} oso={oso_id}"
                )
                return BracketOrderResult(
                    ok=True, mode=mode_str,
                    entry_order_id=entry_id,
                    stop_order_id=stop_id,
                    target_order_id=target_id,
                    oco_id=str(oco_id) if oco_id else None,
                    oso_id=str(oso_id) if oso_id else None,
                    client_order_id=client_order_id, reason="",
                    payload=payload,
                ).as_dict()

            elif isinstance(resp, dict) and resp.get("errorText"):
                return _fail(f"API_ERROR: {resp['errorText']}")
            else:
                # Unknown response shape — log raw and flag for manual review
                print(f"[Tradovate] placeOSO unexpected response shape: {resp}")
                return BracketOrderResult(
                    ok=False, mode=mode_str,
                    entry_order_id=None, stop_order_id=None, target_order_id=None,
                    oco_id=None, oso_id=None,
                    client_order_id=client_order_id,
                    reason=f"UNEXPECTED_RESPONSE: {str(resp)[:200]}",
                    payload=payload,
                ).as_dict()

        except Exception as e:
            return _fail(f"API exception: {e}")


# ── Signal → Order bridge ─────────────────────────────────────────────────────

def signal_to_order(signal: int, base_symbol: str, micro: bool = True,
                    qty: int = 1) -> Optional[TradovateOrder]:
    """
    Convert a strategy signal (+1/-1) to a TradovateOrder.
    signal: +1 = long, -1 = short, 0 = flat (no order)
    Returns None if signal is 0.
    """
    if signal == 0:
        return None

    # Resolve micro symbol — UPDATE CONTRACT MONTH EACH QUARTERLY ROLLOVER
    symbol_map = MICRO_SYMBOLS if micro else {
        "GC": "GCM5", "ES": "ESM5", "NQ": "NQM5"
    }

    tv_symbol = symbol_map.get(base_symbol)
    if not tv_symbol:
        return None

    action = "Buy" if signal > 0 else "Sell"
    return TradovateOrder(symbol=tv_symbol, action=action, qty=qty)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    print("Tradovate Client — Connection Test")
    print("="*50)
    print("\nTo use this, set these environment variables:")
    print("  TRADOVATE_USERNAME=your@email.com")
    print("  TRADOVATE_PASSWORD=yourpassword")
    print("  TRADOVATE_CID=12345")
    print("  TRADOVATE_SECRET=yoursecret")
    print("\nOr edit the credentials directly below for testing.")
    print("\nThis runs in DEMO mode by default (no real money).")
    print("Change demo=False only when you are confident it works.")

    # Uncomment and fill in to test:
    # client = TradovateClient(
    #     username   = os.environ.get("TRADOVATE_USERNAME", ""),
    #     password   = os.environ.get("TRADOVATE_PASSWORD", ""),
    #     cid        = int(os.environ.get("TRADOVATE_CID", "0")),
    #     secret     = os.environ.get("TRADOVATE_SECRET", ""),
    #     demo       = True,   # ALWAYS start with demo=True
    # )
    # if client.authenticate():
    #     print(client.status_line())
    #     print("Positions:", client.get_positions())
    #     quote = client.get_quote("MESM5")
    #     print("MES quote:", quote)
