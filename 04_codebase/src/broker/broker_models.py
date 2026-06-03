"""
broker_models.py — Core data models for the broker abstraction layer
=====================================================================
All broker adapters (mock, real, stub) use these shared data structures.
No business logic lives here — these are pure data containers.

Design notes:
  - Uses Python dataclasses with default factories for mutable fields
  - All datetimes are UTC-aware
  - Enums use str mixin for clean JSON serialisation
  - BrokerPosition uses signed qty: positive=long, negative=short
  - ReconciliationResult is the output of position-vs-broker comparison
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Enums ──────────────────────────────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    STOP   = "STOP"


class OrderStatus(str, Enum):
    PENDING   = "PENDING"    # accepted by gateway, not yet at exchange
    WORKING   = "WORKING"    # live at exchange, awaiting fill
    FILLED    = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


class BracketStatus(str, Enum):
    OPEN      = "OPEN"       # entry not yet filled
    ACTIVE    = "ACTIVE"     # entry filled; stop+target working
    CLOSED    = "CLOSED"     # one of stop/target filled (position flat)
    CANCELLED = "CANCELLED"  # cancelled before entry filled


# ── Core order models ──────────────────────────────────────────────────────────

@dataclass
class BrokerOrder:
    """
    Represents a single resting or completed order at the broker.

    For bracket orders, see BracketOrder which aggregates three BrokerOrders.
    """
    order_id:    str
    symbol:      str
    side:        OrderSide
    qty:         int
    order_type:  OrderType
    status:      OrderStatus = OrderStatus.PENDING

    # Price levels — None means not applicable for this order type
    limit_price: Optional[float] = None
    stop_price:  Optional[float] = None

    # Fill details — populated when status transitions to FILLED
    fill_price:  Optional[float] = None
    fill_qty:    int = 0
    filled_at:   Optional[datetime] = None

    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Internal linkage (used by bracket / OSO orders)
    oco_partner_id:   Optional[str] = None   # OCO sibling order
    bracket_group_id: Optional[str] = None   # group this order belongs to

    def is_terminal(self) -> bool:
        """Return True if this order cannot change state further."""
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "order_id":    self.order_id,
            "symbol":      self.symbol,
            "side":        self.side.value,
            "qty":         self.qty,
            "order_type":  self.order_type.value,
            "status":      self.status.value,
            "limit_price": self.limit_price,
            "stop_price":  self.stop_price,
            "fill_price":  self.fill_price,
            "fill_qty":    self.fill_qty,
            "filled_at":   self.filled_at.isoformat() if self.filled_at else None,
            "created_at":  self.created_at.isoformat(),
        }


@dataclass
class BrokerPosition:
    """
    Current position in a single symbol.

    qty is signed: positive=long, negative=short, zero=flat.
    avg_price is cost basis (average entry price, not mark-to-market).
    unrealized_pnl and realized_pnl are in USD.
    """
    symbol:         str
    qty:            int        # signed; +long / -short / 0=flat
    avg_price:      float      # average entry cost

    unrealized_pnl: float = 0.0
    realized_pnl:   float = 0.0

    # Populated if available from broker
    contract_id:    Optional[int] = None

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol":         self.symbol,
            "qty":            self.qty,
            "avg_price":      self.avg_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl":   self.realized_pnl,
        }


@dataclass
class BrokerFill:
    """
    Record of a single execution (fill event).

    Fills are immutable records — they record what actually happened.
    slippage_estimate measures deviation from the expected/limit price.
    commission is in USD.
    """
    order_id:          str
    symbol:            str
    side:              OrderSide
    qty:               int
    price:             float
    timestamp:         datetime

    commission:        float = 0.0    # USD
    slippage_estimate: float = 0.0    # price points, not USD

    def as_dict(self) -> Dict[str, Any]:
        return {
            "order_id":          self.order_id,
            "symbol":            self.symbol,
            "side":              self.side.value,
            "qty":               self.qty,
            "price":             self.price,
            "timestamp":         self.timestamp.isoformat(),
            "commission":        self.commission,
            "slippage_estimate": self.slippage_estimate,
        }


@dataclass
class BracketOrder:
    """
    An OSO/OCO bracket: entry order with linked stop-loss and profit target.

    When entry fills, stop and target become active (OSO — One-Sends-Other).
    When stop or target fills, the other is cancelled (OCO — One-Cancels-Other).

    All three order IDs refer to BrokerOrder objects stored in the adapter's
    order registry.
    """
    entry_order_id:  str
    stop_order_id:   str
    target_order_id: str

    symbol:      str
    side:        OrderSide
    qty:         int

    entry_price:  Optional[float]  # None for market-entry brackets
    stop_price:   float
    target_price: float

    status: BracketStatus = BracketStatus.OPEN

    # Set on creation
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at:  Optional[datetime] = None

    # Which leg closed the position
    exit_via: Optional[str] = None   # "STOP" | "TARGET" | "MANUAL"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "entry_order_id":  self.entry_order_id,
            "stop_order_id":   self.stop_order_id,
            "target_order_id": self.target_order_id,
            "symbol":          self.symbol,
            "side":            self.side.value,
            "qty":             self.qty,
            "entry_price":     self.entry_price,
            "stop_price":      self.stop_price,
            "target_price":    self.target_price,
            "status":          self.status.value,
            "created_at":      self.created_at.isoformat(),
            "closed_at":       self.closed_at.isoformat() if self.closed_at else None,
            "exit_via":        self.exit_via,
        }


@dataclass
class AccountState:
    """
    Full snapshot of the trading account at a point in time.

    balance:           Cash balance (unrealised P&L excluded)
    equity:            Net liquidation value (balance + open P&L)
    margin_used:       Initial margin consumed by open positions
    margin_available:  equity - margin_used
    daily_pnl:         Total realised + unrealised P&L since midnight UTC
    positions:         All open (non-flat) positions
    """
    balance:          float
    equity:           float
    margin_used:      float
    margin_available: float
    daily_pnl:        float

    positions: List[BrokerPosition] = field(default_factory=list)
    snapshot_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "balance":          self.balance,
            "equity":           self.equity,
            "margin_used":      self.margin_used,
            "margin_available": self.margin_available,
            "daily_pnl":        self.daily_pnl,
            "positions":        [p.as_dict() for p in self.positions],
            "snapshot_at":      self.snapshot_at.isoformat(),
        }


@dataclass
class ReconciliationResult:
    """
    Output of comparing internal (system) position state against broker positions.

    is_clean is True only when there are ZERO mismatches.
    mismatches is a list of dicts — each describes one discrepancy.
    """
    internal_positions: Dict[str, int]     # symbol -> signed qty (from our state)
    broker_positions:   Dict[str, int]     # symbol -> signed qty (from broker API)

    mismatches: List[Dict[str, Any]] = field(default_factory=list)

    is_clean:   bool = True
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_mismatch(self, mismatch: Dict[str, Any]) -> None:
        self.mismatches.append(mismatch)
        self.is_clean = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "internal_positions": self.internal_positions,
            "broker_positions":   self.broker_positions,
            "mismatches":         self.mismatches,
            "is_clean":           self.is_clean,
            "checked_at":         self.checked_at.isoformat(),
        }
