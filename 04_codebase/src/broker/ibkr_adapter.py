"""
ibkr_adapter.py — Interactive Brokers broker adapter (stub)
=============================================================
Planned integration approach:
  Interactive Brokers provides two Python APIs:

  Route A — ib_insync (recommended):
    A high-level asyncio wrapper around ibapi. Provides clean bracket order
    support via ib.placeOrder() with the BracketOrder helper.

    Install: pip install ib_insync
    Requires: TWS or IB Gateway running locally, and API connections enabled
    in TWS settings (Edit → Global Configuration → API → Enable ActiveX and
    Socket Clients).

    Key features:
      - Native bracket (parent + stop + target) order in one call
      - Realtime account/position updates via callbacks
      - asyncio-compatible for non-blocking operation
      - Paper trading account supported on separate port (7497 vs 7496)

    Connection params:
      host:      "127.0.0.1"  (TWS/IB Gateway)
      port:      7497          (paper) or 7496 (live)
      clientId:  1             (must be unique per TWS connection)

  Route B — ibapi (official, lower level):
    The official IBKR Python API. Requires more boilerplate but is the
    authoritative implementation.

    Install: pip install ibapi
    Same connection requirements as ib_insync.

  Current status: NOT YET IMPLEMENTED
  Capital requirement: ~$25k for pattern day trader rules (futures: lower)
  See: 08_docs/personal_broker_automation_design.md

  This stub raises NotImplementedError on all methods to prevent accidental
  usage.
"""

from __future__ import annotations

from typing import Optional

from .base import BrokerAdapter, BrokerMode
from .broker_models import (
    AccountState,
    BracketOrder,
    BrokerOrder,
    BrokerPosition,
    ReconciliationResult,
)

_NOT_IMPL_MSG = (
    "IBKR adapter not yet implemented. "
    "Planned via ib_insync library. "
    "See 08_docs/personal_broker_automation_design.md for integration design."
)


class IBKRAdapter(BrokerAdapter):
    """
    Stub adapter for Interactive Brokers (IBKR) integration.

    All methods raise NotImplementedError. This class exists to:
      1. Define the interface the real implementation must satisfy
      2. Allow BrokerFactory to reference this adapter by name
      3. Document the intended integration design in its docstring

    To implement:
      1. pip install ib_insync
      2. Start TWS or IB Gateway with API connections enabled
      3. Replace each NotImplementedError with ib_insync calls
      4. Handle reconnection logic (TWS can be disconnected at ~11:45pm ET)
      5. Write integration tests against IB paper account (port 7497)

    Example (pseudocode) for connect():
        from ib_insync import IB
        self._ib = IB()
        self._ib.connect("127.0.0.1", 7497, clientId=1)

    Example (pseudocode) for place_bracket_order():
        from ib_insync import Stock, Future, MarketOrder, LimitOrder, StopOrder
        contract = Future("GC", "202506", "COMEX")
        parent, take_profit, stop_loss = ib_insync.util.bracketOrder(
            action="BUY", quantity=qty,
            limitPrice=target_price, stopLossPrice=stop_price
        )
        for o in [parent, take_profit, stop_loss]:
            self._ib.placeOrder(contract, o)
    """

    def __init__(self, mode: BrokerMode = BrokerMode.DEMO, config: Optional[dict] = None):
        super().__init__(mode=mode, config=config or {})
        # Placeholder for ib_insync IB() instance
        self._ib = None

    def connect(self) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def disconnect(self) -> None:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def is_connected(self) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def get_account_state(self) -> AccountState:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def get_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def get_open_orders(self) -> list[BrokerOrder]:
        raise NotImplementedError(_NOT_IMPL_MSG)

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
        raise NotImplementedError(_NOT_IMPL_MSG)

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def cancel_all(self) -> int:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def flatten_symbol(self, symbol: str) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def flatten_all(self) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def reconcile(self) -> ReconciliationResult:
        raise NotImplementedError(_NOT_IMPL_MSG)

    def heartbeat(self) -> bool:
        raise NotImplementedError(_NOT_IMPL_MSG)
