"""
ninjatrader_adapter.py — NinjaTrader broker adapter (stub)
===========================================================
Planned integration approach:
  NinjaTrader 8 offers two viable automation routes:

  Route A — NinjaScript C# strategy (preferred for low latency):
    A NinjaScript strategy runs inside NinjaTrader 8 and receives signals
    via a TCP socket or shared memory file written by the Python system.
    The NinjaScript handles order routing, bracket management, and OCO
    natively through NT8's Order Management System.

    Pros: sub-10ms order routing, native bracket support, no API rate limits
    Cons: requires C# NinjaScript development, socket protocol design

  Route B — NinjaTrader 8 Add-On with ATI (Automated Trading Interface):
    NT8 exposes an ATI via a file-based interface (AtmStrategy) that accepts
    orders by writing a CSV file to a monitored directory.

    Pros: simple file-based protocol, no custom NinjaScript required
    Cons: higher latency (file polling), limited order types

  Current status: NOT YET IMPLEMENTED
  See: 08_docs/personal_broker_automation_design.md for the full design.

  This stub raises NotImplementedError on all methods to prevent accidental
  usage. Replace each method body with a real implementation when the
  NinjaScript bridge is ready.
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
    "NinjaTrader adapter not yet implemented. "
    "See 08_docs/personal_broker_automation_design.md for the planned "
    "integration approach via TCP socket bridge or NinjaScript ATI."
)


class NinjaTraderAdapter(BrokerAdapter):
    """
    Stub adapter for NinjaTrader 8 integration.

    All methods raise NotImplementedError. This class exists to:
      1. Define the interface that the real implementation must satisfy
      2. Allow BrokerFactory to reference this adapter by name
      3. Document the intended integration design in its docstring

    To implement:
      1. Design the TCP socket protocol or ATI file format
      2. Write the NinjaScript receiver (C#) or ATI configurator
      3. Replace each NotImplementedError with a real implementation
      4. Add authentication configuration to __init__
      5. Write integration tests against NT8 simulator
    """

    def __init__(self, mode: BrokerMode = BrokerMode.DEMO, config: Optional[dict] = None):
        super().__init__(mode=mode, config=config or {})

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
