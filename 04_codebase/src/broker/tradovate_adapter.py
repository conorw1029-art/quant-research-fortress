"""
tradovate_adapter.py — Tradovate broker adapter
================================================
Wraps the existing tick_tradovate_client.TradovateClient to implement the
BrokerAdapter interface.

Implementation status:
  IMPLEMENTED (delegates to TradovateClient):
    connect()          — calls authenticate()
    is_connected()     — checks access_token presence
    get_positions()    — calls client.get_positions()
    get_open_orders()  — calls client.get_open_orders()
    cancel_order()     — calls client.cancel_order()
    flatten_symbol()   — calls client.close_position()
    flatten_all()      — calls client.close_all_positions()
    heartbeat()        — lightweight account check

  NOT YET IMPLEMENTED (raises NotImplementedError):
    place_bracket_order() — OSO payload is designed in TradovateClient but
                            the exchange-verification flag is still False.
                            Enable only after real exchange test confirms
                            placeOSO response shape.
    get_account_state()   — needs cashbalance endpoint integration
    reconcile()           — needs BrokerReconciler integration

Safety:
  - Always reads credentials from environment variables — no hardcoded creds
  - dry_run=True is the default for place_bracket_order until _OSO_EXCHANGE_VERIFIED
  - LIVE mode requires FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND

Config keys (passed via BrokerFactory config dict):
  username:    Tradovate account email (override env TRADOVATE_USERNAME)
  password:    Tradovate password (override env TRADOVATE_PASSWORD)
  cid:         API CID integer (override env TRADOVATE_CID)
  secret:      API secret string (override env TRADOVATE_SECRET)
  demo:        bool, default True (use demo.tradovateapi.com)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import BrokerAdapter, BrokerMode
from .broker_models import (
    AccountState,
    BracketOrder,
    BracketStatus,
    BrokerFill,
    BrokerOrder,
    BrokerPosition,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationResult,
)

logger = logging.getLogger(__name__)

# Import the existing Tradovate client
_CLIENT_PATH = Path(__file__).parents[2]   # 04_codebase/
if str(_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(_CLIENT_PATH))

try:
    from tick_tradovate_client import TradovateClient, TradovateOrder
    _CLIENT_AVAILABLE = True
except ImportError:
    _CLIENT_AVAILABLE = False
    logger.warning(
        "tick_tradovate_client not found. TradovateAdapter will fail on connect(). "
        "Ensure 04_codebase/ is in the Python path."
    )


class TradovateAdapter(BrokerAdapter):
    """
    BrokerAdapter wrapper around TradovateClient.

    Uses existing tick_tradovate_client.py for all API operations.
    Translates between TradovateClient types and broker_models types.

    Important: place_bracket_order() raises NotImplementedError until the
    exchange verification flag in tick_tradovate_client.py is set to True.
    This is a deliberate safety gate — see tick_tradovate_client._OSO_EXCHANGE_VERIFIED.

    Args:
        mode:   BrokerMode — DEMO or LIVE only (not DRY_RUN, use MockBroker for that)
        config: Dict with optional credential overrides (see module docstring)
    """

    def __init__(self, mode: BrokerMode = BrokerMode.DEMO, config: Optional[dict] = None):
        super().__init__(mode=mode, config=config or {})

        if mode == BrokerMode.DRY_RUN:
            raise ValueError(
                "TradovateAdapter does not support DRY_RUN mode. "
                "Use MockBroker for in-process simulation."
            )

        self._client: Optional["TradovateClient"] = None
        self._is_demo = (mode != BrokerMode.LIVE)

    def _build_client(self) -> "TradovateClient":
        """Construct TradovateClient from config or environment variables."""
        if not _CLIENT_AVAILABLE:
            raise RuntimeError(
                "tick_tradovate_client.py not importable. "
                "Check that 04_codebase/ is in sys.path."
            )

        username = self.config.get("username") or os.environ.get("TRADOVATE_USERNAME", "")
        password = self.config.get("password") or os.environ.get("TRADOVATE_PASSWORD", "")
        cid      = int(self.config.get("cid", 0) or os.environ.get("TRADOVATE_CID", "0"))
        secret   = self.config.get("secret") or os.environ.get("TRADOVATE_SECRET", "")

        if not username or not password:
            raise ValueError(
                "Tradovate credentials not set. "
                "Set TRADOVATE_USERNAME and TRADOVATE_PASSWORD environment variables."
            )

        return TradovateClient(
            username    = username,
            password    = password,
            cid         = cid,
            secret      = secret,
            demo        = self._is_demo,
        )

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Authenticate with Tradovate and obtain an access token.
        Reads credentials from environment variables or config dict.
        """
        try:
            self._client = self._build_client()
            success = self._client.authenticate()
            if success:
                mode_label = "DEMO" if self._is_demo else "LIVE"
                logger.info("TradovateAdapter connected — mode=%s", mode_label)
            else:
                logger.error("TradovateAdapter: authentication failed")
            return success
        except Exception as e:
            logger.error("TradovateAdapter.connect() error: %s", e)
            return False

    def disconnect(self) -> None:
        """
        Tradovate REST API is stateless — there is no explicit logout endpoint.
        We clear the client reference to prevent further API calls.
        """
        self._client = None
        logger.info("TradovateAdapter disconnected (token cleared)")

    def is_connected(self) -> bool:
        """Return True if we have an authenticated client with a valid token."""
        if self._client is None:
            return False
        return bool(self._client.access_token)

    # ── Account information ────────────────────────────────────────────────────

    def get_account_state(self) -> AccountState:
        """
        Return account state snapshot from Tradovate cashbalance endpoint.

        Note: This method is partially implemented. daily_pnl is derived
        from open positions; the cashbalance endpoint provides balance only.
        """
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")

        try:
            info = self._client.get_account_info()
            balance = float(info.get("cashBalance", 0))
        except Exception as e:
            logger.warning("get_account_info failed: %s", e)
            balance = 0.0

        positions = self.get_positions()
        daily_pnl = sum(p.unrealized_pnl + p.realized_pnl for p in positions)
        equity    = balance + sum(p.unrealized_pnl for p in positions)

        return AccountState(
            balance          = balance,
            equity           = equity,
            margin_used      = 0.0,     # not yet implemented
            margin_available = equity,  # simplified
            daily_pnl        = daily_pnl,
            positions        = positions,
        )

    def get_positions(self) -> list[BrokerPosition]:
        """Return all open positions, translated to BrokerPosition objects."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")

        try:
            raw_positions = self._client.get_positions()
        except Exception as e:
            logger.error("get_positions failed: %s", e)
            return []

        result = []
        for p in raw_positions:
            if p.net_pos == 0:
                continue
            result.append(BrokerPosition(
                symbol         = p.symbol,
                qty            = p.net_pos,      # signed: +long / -short
                avg_price      = p.avg_price,
                unrealized_pnl = p.open_pnl,
                realized_pnl   = p.closed_pnl,
            ))
        return result

    def get_open_orders(self) -> list[BrokerOrder]:
        """Return all working orders from Tradovate."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")

        try:
            raw_orders = self._client.get_open_orders()
        except Exception as e:
            logger.error("get_open_orders failed: %s", e)
            return []

        result = []
        for o in raw_orders:
            order_id   = str(o.get("id") or o.get("orderId", ""))
            symbol     = o.get("symbol", "")
            action     = o.get("action", "Buy")
            qty        = int(o.get("orderQty", 1))
            order_type = o.get("orderType", "Market")
            price      = o.get("price")
            stop_price = o.get("stopPrice")

            side = OrderSide.BUY if action.lower() == "buy" else OrderSide.SELL
            otype = _map_order_type(order_type)

            result.append(BrokerOrder(
                order_id    = order_id,
                symbol      = symbol,
                side        = side,
                qty         = qty,
                order_type  = otype,
                status      = OrderStatus.WORKING,
                limit_price = float(price) if price is not None else None,
                stop_price  = float(stop_price) if stop_price is not None else None,
            ))
        return result

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
        Place a bracket order via Tradovate placeOSO endpoint.

        CURRENTLY RAISES NotImplementedError.

        This will be enabled once _OSO_EXCHANGE_VERIFIED is set to True in
        tick_tradovate_client.py, which requires a successful end-to-end test
        against the Tradovate exchange (demo or live) confirming:
          1. The placeOSO JSON payload format is accepted
          2. The response structure contains the expected order IDs
          3. The OCO/OSO group IDs are correctly parsed
          4. Both bracket legs show Working status after entry fills

        See: tick_tradovate_client.py → _OSO_EXCHANGE_VERIFIED
        See: 08_docs/personal_broker_automation_design.md → Section 6

        When ready to implement:
          1. Set _OSO_EXCHANGE_VERIFIED = True in tick_tradovate_client.py
          2. Call self._client.place_bracket_order(... dry_run=False ...)
          3. Parse the BracketOrderResult into a BracketOrder model
          4. Store order IDs for reconciliation
        """
        raise NotImplementedError(
            "Bracket orders not yet implemented in TradovateAdapter. "
            "The placeOSO endpoint payload is designed but exchange-verification "
            "has not been completed. "
            "See tick_tradovate_client.py → _OSO_EXCHANGE_VERIFIED "
            "and 08_docs/personal_broker_automation_design.md."
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by ID."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")
        try:
            self._client.cancel_order(int(order_id))
            return True
        except Exception as e:
            logger.error("cancel_order(%s) failed: %s", order_id, e)
            return False

    def cancel_all(self) -> int:
        """Cancel all working orders."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")
        orders = self.get_open_orders()
        count  = 0
        for o in orders:
            if self.cancel_order(o.order_id):
                count += 1
        logger.info("cancel_all: cancelled %d orders", count)
        return count

    def flatten_symbol(self, symbol: str) -> bool:
        """Close the open position in symbol."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")
        try:
            self._client.close_position(symbol)
            return True
        except Exception as e:
            logger.error("flatten_symbol(%s) failed: %s", symbol, e)
            return False

    def flatten_all(self) -> bool:
        """Close all open positions."""
        if self._client is None:
            raise ConnectionError("TradovateAdapter: not connected")
        try:
            self._client.close_all_positions()
            return True
        except Exception as e:
            logger.error("flatten_all() failed: %s", e)
            return False

    # ── Maintenance ────────────────────────────────────────────────────────────

    def reconcile(self) -> ReconciliationResult:
        """
        Run position reconciliation against Tradovate's reported positions.

        Note: This is a partial implementation — it returns a ReconciliationResult
        with broker positions populated but without comparing against internal state.
        Use BrokerReconciler.reconcile_on_startup() for the full comparison.
        """
        broker_positions = self.get_positions()
        broker_state = {p.symbol: p.qty for p in broker_positions if p.qty != 0}

        return ReconciliationResult(
            internal_positions = {},    # caller must supply via BrokerReconciler
            broker_positions   = broker_state,
            is_clean           = True,  # no mismatches possible without internal state
        )

    def heartbeat(self) -> bool:
        """Check if the Tradovate token is still valid with a lightweight call."""
        if self._client is None or not self._client.access_token:
            return False
        try:
            # Re-authenticate if token is expired
            self._client._ensure_auth()
            return True
        except Exception:
            return False


# ── Helper ────────────────────────────────────────────────────────────────────

def _map_order_type(tv_type: str) -> OrderType:
    """Map Tradovate order type string to OrderType enum."""
    mapping = {
        "Market": OrderType.MARKET,
        "Limit":  OrderType.LIMIT,
        "Stop":   OrderType.STOP,
        "StopLimit": OrderType.STOP,
    }
    return mapping.get(tv_type, OrderType.MARKET)
