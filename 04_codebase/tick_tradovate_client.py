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


def _point_value(symbol: str) -> float:
    """Look up point value for a symbol, using 3-char then 2-char prefix match."""
    base3 = symbol[:3].upper()
    base2 = symbol[:2].upper()
    return _POINT_VALUE_MAP.get(base3) or _POINT_VALUE_MAP.get(base2) or 5.0


@dataclass
class BracketOrderResult:
    """Structured result from place_bracket_order()."""
    ok:              bool
    mode:            str            # "DRY_RUN" | "DEMO" | "LIVE"
    entry_order_id:  Optional[int]
    stop_order_id:   Optional[int]
    target_order_id: Optional[int]
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
                client_order_id=client_order_id, reason=reason,
            ).as_dict()

        # ── Validation ────────────────────────────────────────────────────────
        if not symbol or not symbol.strip():
            return _fail("symbol is empty")

        side = side.upper()
        if side not in ("BUY", "SELL"):
            return _fail(f"invalid side '{side}' — must be BUY or SELL")

        if quantity <= 0:
            return _fail(f"invalid quantity {quantity} — must be > 0")

        if quantity > MAX_BRACKET_CONTRACTS:
            return _fail(f"quantity {quantity} exceeds max {MAX_BRACKET_CONTRACTS} contracts")

        if entry_type.upper() not in ("MARKET", "LIMIT"):
            return _fail(f"invalid entry_type '{entry_type}' — must be Market or Limit")

        if entry_type.upper() == "LIMIT" and entry_price is None:
            return _fail("entry_price required for Limit orders")

        ref_price = entry_price
        if ref_price is None:
            # Market order — can't validate direction without price, skip directional checks
            pass
        else:
            if side == "BUY":
                if stop_price >= ref_price:
                    return _fail(
                        f"stop_price {stop_price} must be BELOW entry {ref_price} for BUY"
                    )
                if target_price <= ref_price:
                    return _fail(
                        f"target_price {target_price} must be ABOVE entry {ref_price} for BUY"
                    )
            else:  # SELL
                if stop_price <= ref_price:
                    return _fail(
                        f"stop_price {stop_price} must be ABOVE entry {ref_price} for SELL"
                    )
                if target_price >= ref_price:
                    return _fail(
                        f"target_price {target_price} must be BELOW entry {ref_price} for SELL"
                    )

        stop_dist = abs((ref_price or stop_price) - stop_price)
        if stop_dist == 0:
            return _fail("stop distance is zero — cannot place bracket order")

        tgt_dist = abs((ref_price or target_price) - target_price)
        if tgt_dist == 0:
            return _fail("target distance is zero — cannot place bracket order")

        if ref_price is not None:
            pv          = _point_value(symbol)
            est_risk    = abs(ref_price - stop_price) * pv * quantity
            if est_risk > MAX_BRACKET_RISK_USD:
                return _fail(
                    f"estimated risk ${est_risk:,.2f} exceeds maximum "
                    f"${MAX_BRACKET_RISK_USD:,.0f} per trade"
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

        # ── Dry-run — return payload without API call ─────────────────────────
        if dry_run:
            print(f"[Tradovate] DRY_RUN — payload validated, no API call made")
            return BracketOrderResult(
                ok=True, mode="DRY_RUN",
                entry_order_id=None, stop_order_id=None, target_order_id=None,
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

                print(
                    f"[Tradovate] Bracket placed: entry={entry_id} "
                    f"stop={stop_id} target={target_id}"
                )
                return BracketOrderResult(
                    ok=True, mode=mode_str,
                    entry_order_id=entry_id,
                    stop_order_id=stop_id,
                    target_order_id=target_id,
                    client_order_id=client_order_id, reason="",
                    payload=payload,
                ).as_dict()

            elif isinstance(resp, dict) and resp.get("errorText"):
                return _fail(f"API error: {resp['errorText']}")
            else:
                # Unknown response shape — log and flag for manual review
                print(f"[Tradovate] placeOSO unexpected response shape: {resp}")
                return BracketOrderResult(
                    ok=False, mode=mode_str,
                    entry_order_id=None, stop_order_id=None, target_order_id=None,
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
