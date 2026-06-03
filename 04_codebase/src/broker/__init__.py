"""
src/broker/__init__.py — Broker abstraction layer
==================================================
The broker layer provides a uniform interface for all order-routing backends.
External code should only import from this module — never from concrete adapters.

Public API:
    BrokerAdapter         — abstract base for all adapters
    BrokerMode            — enum: DRY_RUN, PAPER, DEMO, LIVE
    BrokerFactory         — create adapters by name
    MockBroker            — in-process simulation (DRY_RUN / DEMO)
    TradovateAdapter      — wraps tick_tradovate_client.py
    NinjaTraderAdapter    — stub (NotImplementedError)
    IBKRAdapter           — stub (NotImplementedError)
    BrokerOrder           — single order record
    BrokerPosition        — single position record
    BrokerFill            — execution fill record
    BracketOrder          — OSO/OCO bracket (entry + stop + target)
    AccountState          — full account snapshot
    ReconciliationResult  — output of position comparison
    BrokerReconciler      — reconcile internal state vs broker
    BrokerRiskGateway     — pre-trade risk enforcement
    OrderCandidate        — proposed order (input to risk gateway)
    NewsWindow            — time window for news blackout

Usage:
    from src.broker import BrokerFactory, BrokerMode

    # Create a simulation broker for backtesting / dry-run
    adapter = BrokerFactory.create(BrokerMode.DRY_RUN, "mock", {})
    adapter.connect()

    # Place a bracket order
    bracket = adapter.place_bracket_order(
        symbol="GC", side="BUY", qty=1,
        entry_price=2000.0, stop_price=1990.0, target_price=2020.0,
    )
"""

from .base import BrokerAdapter, BrokerFactory, BrokerMode
from .broker_models import (
    AccountState,
    BracketOrder,
    BrokerFill,
    BrokerOrder,
    BrokerPosition,
    ReconciliationResult,
)
from .broker_reconciliation import BrokerReconciler
from .broker_risk_gateway import BrokerRiskGateway, NewsWindow, OrderCandidate
from .ibkr_adapter import IBKRAdapter
from .mock_broker import MockBroker
from .ninjatrader_adapter import NinjaTraderAdapter
from .tradovate_adapter import TradovateAdapter

__all__ = [
    # Base / factory
    "BrokerAdapter",
    "BrokerFactory",
    "BrokerMode",
    # Concrete adapters
    "MockBroker",
    "TradovateAdapter",
    "NinjaTraderAdapter",
    "IBKRAdapter",
    # Data models
    "BrokerOrder",
    "BrokerPosition",
    "BrokerFill",
    "BracketOrder",
    "AccountState",
    "ReconciliationResult",
    # Infrastructure
    "BrokerReconciler",
    "BrokerRiskGateway",
    "OrderCandidate",
    "NewsWindow",
]
