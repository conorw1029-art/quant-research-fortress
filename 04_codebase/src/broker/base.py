"""
base.py — Abstract broker adapter interface
============================================
Every concrete broker adapter (MockBroker, TradovateAdapter, IBKRAdapter,
NinjaTraderAdapter) must inherit from BrokerAdapter and implement all
abstract methods.

Design principles:
  - BrokerAdapter is the ONLY interface the rest of the system touches.
    No code outside src/broker/ should import a concrete adapter directly.
  - BrokerMode enforces a hard distinction between simulation and live.
    LIVE mode requires an explicit env var to prevent accidental activation.
  - BrokerFactory.create() is the single creation point — this makes it
    straightforward to swap adapters in tests or config.

Safety contract:
  - Adapters in DRY_RUN or PAPER mode MUST NOT make network calls.
  - LIVE mode MUST check FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND before
    any order-placement call.
  - heartbeat() is safe to call at any time (no side effects, no orders).
"""

from __future__ import annotations

import abc
import logging
import os
from enum import Enum
from typing import Any, Dict, Optional, Type

from .broker_models import (
    AccountState,
    BracketOrder,
    BrokerOrder,
    BrokerPosition,
    ReconciliationResult,
)

logger = logging.getLogger(__name__)

# ── Live-mode safety env var ───────────────────────────────────────────────────
_LIVE_ENABLE_ENV   = "FORTRESS_LIVE_ENABLE"
_LIVE_ENABLE_VALUE = "YES_I_UNDERSTAND"


# ── BrokerMode ─────────────────────────────────────────────────────────────────

class BrokerMode(str, Enum):
    """
    Ordered from safest to most dangerous.

    DRY_RUN  — In-process simulation only.  Zero network calls.
    PAPER    — Tracks hypothetical fills against real market data, but
               does not route to an exchange.  May use broker's paper API.
    DEMO     — Uses the broker's own demo/sim environment.  Real API calls
               but a practice account with no real money.
    LIVE     — Real money.  Requires FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND.
    """
    DRY_RUN = "DRY_RUN"
    PAPER   = "PAPER"
    DEMO    = "DEMO"
    LIVE    = "LIVE"

    @property
    def is_real_money(self) -> bool:
        return self == BrokerMode.LIVE

    @property
    def allows_network(self) -> bool:
        return self in (BrokerMode.DEMO, BrokerMode.LIVE)


# ── Abstract base ──────────────────────────────────────────────────────────────

class BrokerAdapter(abc.ABC):
    """
    Abstract interface that all broker adapters must implement.

    Concrete subclasses:
      - MockBroker         — in-process simulation (DRY_RUN)
      - TradovateAdapter   — wraps tick_tradovate_client.py (DEMO/LIVE)
      - NinjaTraderAdapter — future NinjaTrader bridge (stub)
      - IBKRAdapter        — future IBKR bridge (stub)

    Usage pattern:
        adapter = BrokerFactory.create(BrokerMode.DRY_RUN, "mock", {})
        adapter.connect()
        bracket = adapter.place_bracket_order("GC", "BUY", 1, 2000, 1990, 2020)
        ...
        adapter.disconnect()
    """

    def __init__(self, mode: BrokerMode, config: Optional[Dict[str, Any]] = None):
        self.mode   = mode
        self.config = config or {}
        self._log   = logging.getLogger(self.__class__.__name__)

        if mode.is_real_money:
            self._enforce_live_gate()

    def _enforce_live_gate(self) -> None:
        """Raise PermissionError if the live-enable env var is not set correctly."""
        env_val = os.environ.get(_LIVE_ENABLE_ENV, "")
        if env_val != _LIVE_ENABLE_VALUE:
            raise PermissionError(
                f"LIVE mode requires env var {_LIVE_ENABLE_ENV}={_LIVE_ENABLE_VALUE}. "
                f"Set this variable only when you have verified the full safety stack. "
                f"Current value: '{env_val}'"
            )
        self._log.warning(
            "LIVE MODE ENABLED — real money broker. "
            "All risk rules are active. Kill switch is at KILL_SWITCH.txt."
        )

    # ── Connection ─────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to broker. Returns True on success.
        For DRY_RUN adapters, this always returns True with no network call.
        """

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Cleanly close the broker connection."""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return True if the adapter has an active connection."""

    # ── Mode introspection ─────────────────────────────────────────────────────

    def is_demo(self) -> bool:
        """Return True if adapter is in a non-real-money mode (DRY_RUN, PAPER, DEMO)."""
        return not self.mode.is_real_money

    def is_live(self) -> bool:
        """Return True only if mode is LIVE (real money)."""
        return self.mode.is_real_money

    def get_mode(self) -> BrokerMode:
        return self.mode

    # ── Account information ────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_account_state(self) -> AccountState:
        """
        Return a full account snapshot: balance, equity, margin, P&L, positions.
        This is a point-in-time read — it may trigger an API call.
        """

    @abc.abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        """Return all open (non-flat) positions."""

    @abc.abstractmethod
    def get_open_orders(self) -> list[BrokerOrder]:
        """Return all orders that are not in a terminal state."""

    # ── Order placement ────────────────────────────────────────────────────────

    @abc.abstractmethod
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
        Place an OSO bracket order (entry + stop + target).

        The stop and target are sent atomically with the entry so that the
        position is protected even if the Python process dies.

        Args:
            symbol:       Instrument symbol (e.g. "MGC", "MES")
            side:         "BUY" or "SELL"
            qty:          Number of contracts (must be >= 1)
            entry_price:  Limit entry price; None for market orders
            stop_price:   Stop-loss price
            target_price: Profit target price
            order_type:   "MARKET" or "LIMIT"

        Returns:
            BracketOrder with all three order IDs populated.

        Raises:
            ValueError:        if parameters fail pre-trade validation
            ConnectionError:   if not connected
            PermissionError:   if risk gateway blocks the order
        """

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a single working order by ID.
        Returns True if successfully cancelled, False if already terminal.
        """

    @abc.abstractmethod
    def cancel_all(self) -> int:
        """
        Cancel all working orders across all symbols.
        Returns the count of orders that were cancelled.
        """

    @abc.abstractmethod
    def flatten_symbol(self, symbol: str) -> bool:
        """
        Close all open positions in `symbol` at market.
        Returns True if the symbol was flat after the call (or was already flat).
        """

    @abc.abstractmethod
    def flatten_all(self) -> bool:
        """
        Flatten all open positions across all symbols.
        Returns True if fully flat after the call.
        """

    # ── Maintenance ────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def reconcile(self) -> ReconciliationResult:
        """
        Compare internal state against broker's reported positions.
        Returns a ReconciliationResult with any discrepancies.
        Safe to call frequently — it is read-only with no side effects.
        """

    @abc.abstractmethod
    def heartbeat(self) -> bool:
        """
        Lightweight liveness check.
        Returns True if the adapter can communicate with the broker.
        For mock adapters, returns True as long as connected.
        Should be fast (<200 ms) and free of side effects.
        """


# ── BrokerFactory ──────────────────────────────────────────────────────────────

class BrokerFactory:
    """
    Single creation point for all broker adapters.

    Usage:
        adapter = BrokerFactory.create(BrokerMode.DRY_RUN, "mock", {})
        adapter = BrokerFactory.create(BrokerMode.DEMO, "tradovate", config)

    Supported adapter_name values:
        "mock"        — MockBroker (always DRY_RUN / DEMO)
        "tradovate"   — TradovateAdapter (DEMO or LIVE)
        "ninjatrader" — NinjaTraderAdapter (stub — NotImplementedError)
        "ibkr"        — IBKRAdapter (stub — NotImplementedError)

    The factory lazily imports adapters so that missing optional dependencies
    (e.g. ib_insync) do not break the import of this module.
    """

    _REGISTRY: Dict[str, str] = {
        "mock":        "src.broker.mock_broker.MockBroker",
        "tradovate":   "src.broker.tradovate_adapter.TradovateAdapter",
        "ninjatrader": "src.broker.ninjatrader_adapter.NinjaTraderAdapter",
        "ibkr":        "src.broker.ibkr_adapter.IBKRAdapter",
    }

    @classmethod
    def create(
        cls,
        mode:         BrokerMode,
        adapter_name: str,
        config:       Optional[Dict[str, Any]] = None,
    ) -> BrokerAdapter:
        """
        Instantiate and return the requested adapter.

        Raises:
            ValueError:       if adapter_name is unknown
            PermissionError:  if LIVE mode env var is not set
        """
        config = config or {}
        adapter_name = adapter_name.lower().strip()

        if adapter_name not in cls._REGISTRY:
            raise ValueError(
                f"Unknown broker adapter '{adapter_name}'. "
                f"Available: {list(cls._REGISTRY.keys())}"
            )

        # Guard: mock adapter must not be used in LIVE mode
        if adapter_name == "mock" and mode == BrokerMode.LIVE:
            raise ValueError(
                "MockBroker cannot be used in LIVE mode. "
                "Use a real broker adapter for live trading."
            )

        module_path, class_name = cls._REGISTRY[adapter_name].rsplit(".", 1)

        import importlib
        module = importlib.import_module(module_path)
        adapter_cls: Type[BrokerAdapter] = getattr(module, class_name)

        logger.info(
            "BrokerFactory: creating %s in %s mode",
            adapter_name, mode.value
        )
        return adapter_cls(mode=mode, config=config)

    @classmethod
    def register(cls, name: str, full_class_path: str) -> None:
        """
        Register a custom adapter class for use with BrokerFactory.create().

        full_class_path must be importable, e.g.:
            "mypackage.mymodule.MyAdapter"
        """
        cls._REGISTRY[name.lower()] = full_class_path
        logger.info("BrokerFactory: registered custom adapter '%s'", name)
