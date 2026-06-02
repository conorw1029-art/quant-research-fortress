"""
tick_mock_broker.py — Mock Broker Adapter
==========================================
Simulates a broker API (Tradovate-compatible interface) without any real API calls.

Purpose:
  End-to-end dry-run testing of order logic, bracket orders, reconciliation,
  fill handling, and error scenarios — with zero capital risk.

Safety:
  This module makes NO network calls and holds NO real credentials.
  It is safe to run in BROKER_MODE=MOCK_ONLY / EXECUTION_MODE=DRY_RUN.

Run smoke test:
  venv_new/Scripts/python.exe -X utf8 04_codebase/tick_mock_broker.py

Usage:
  from tick_mock_broker import MockBroker
  broker = MockBroker(initial_balance=50_000)
  oid = broker.place_bracket("GC", "BUY", qty=1, entry=2000.0, stop=1995.0, target=2010.0)
  broker.simulate_price(2010.5)   # target hit
  print(broker.account_summary())
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ── Enums ─────────────────────────────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    STOP   = "STOP"


class OrderStatus(str, Enum):
    PENDING   = "PENDING"
    WORKING   = "WORKING"
    FILLED    = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Order:
    order_id:    str
    symbol:      str
    side:        OrderSide
    order_type:  OrderType
    qty:         int
    price:       Optional[float]    # limit/stop trigger price; None for market
    status:      OrderStatus = OrderStatus.PENDING
    fill_price:  Optional[float] = None
    fill_time:   Optional[datetime] = None
    oco_partner: Optional[str] = None   # sibling order id in an OCO pair
    bracket_parent: Optional[str] = None  # entry order that spawned this bracket
    notes:       str = ""


@dataclass
class Position:
    symbol:    str
    qty:       int           # positive = long, negative = short
    avg_price: float
    open_pnl:  float = 0.0   # updated by simulate_price()


@dataclass
class Fill:
    order_id:   str
    symbol:     str
    side:       OrderSide
    qty:        int
    price:      float
    timestamp:  datetime
    slippage:   float = 0.0


# ── MockBroker ────────────────────────────────────────────────────────────────

class MockBroker:
    """
    Simulates account, positions, orders, fills, bracket orders, and error scenarios.
    Thread-safe for single-threaded use only (no locks).
    """

    def __init__(
        self,
        initial_balance: float = 50_000.0,
        default_slippage_ticks: int = 1,
        reject_probability: float = 0.0,   # 0.0–1.0 for chaos testing
        connected: bool = True,
    ):
        self.balance = initial_balance
        self.equity  = initial_balance
        self.default_slippage_ticks = default_slippage_ticks
        self.reject_probability     = reject_probability
        self._connected             = connected

        self._orders:    Dict[str, Order]    = {}
        self._positions: Dict[str, Position] = {}
        self._fills:     List[Fill]          = []
        self._log:       List[str]           = []

        self._contract_specs: Dict[str, dict] = {
            "GC": {"tick_size": 0.10, "tick_value": 10.0,   "name": "Gold"},
            "SI": {"tick_size": 0.005, "tick_value": 25.0,  "name": "Silver"},
            "ES": {"tick_size": 0.25,  "tick_value": 12.5,  "name": "E-mini S&P"},
            "NQ": {"tick_size": 0.25,  "tick_value": 5.0,   "name": "E-mini NQ"},
        }

        self._log_event("MockBroker initialised — NO REAL API CALLS")

    # ── Connection simulation ─────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def disconnect(self):
        """Simulate broker disconnect (for chaos/recovery testing)."""
        self._connected = False
        self._log_event("DISCONNECT simulated")

    def reconnect(self):
        """Simulate successful reconnect."""
        self._connected = True
        self._log_event("RECONNECT simulated")

    def _require_connected(self):
        if not self._connected:
            raise ConnectionError("MockBroker: broker is disconnected")

    # ── Order placement ───────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        notes: str = "",
    ) -> str:
        """
        Place a single order. Returns order_id.
        Raises: ConnectionError if disconnected, ValueError if rejected.
        """
        self._require_connected()

        import random
        if self.reject_probability > 0 and random.random() < self.reject_probability:
            oid = self._new_oid()
            o = Order(
                order_id=oid, symbol=symbol,
                side=OrderSide(side), order_type=OrderType(order_type),
                qty=qty, price=price, status=OrderStatus.REJECTED, notes=notes,
            )
            self._orders[oid] = o
            self._log_event(f"ORDER REJECTED: {side} {qty} {symbol} @ {price} (chaos)")
            raise ValueError(f"Order rejected (chaos test) — id={oid}")

        oid = self._new_oid()
        o = Order(
            order_id=oid, symbol=symbol,
            side=OrderSide(side), order_type=OrderType(order_type),
            qty=qty, price=price, status=OrderStatus.WORKING, notes=notes,
        )
        self._orders[oid] = o
        self._log_event(f"ORDER PLACED: {side} {qty} {symbol} type={order_type} price={price} id={oid}")

        # Market orders fill immediately at price (or a placeholder 0.0)
        if order_type == "MARKET":
            fill_price = price or 0.0
            self._fill_order(oid, fill_price)

        return oid

    def place_bracket(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry: float,
        stop: float,
        target: float,
        entry_type: str = "MARKET",
    ) -> Tuple[str, str, str]:
        """
        Place an OSO bracket order: entry → (stop + target in OCO).
        Returns (entry_id, stop_id, target_id).

        Stop and target are placed as WORKING orders immediately but only
        fill when simulate_price() crosses their trigger.
        """
        self._require_connected()

        if stop is None:
            self._log_event(f"WARNING: missing stop on bracket for {symbol}")
        if target is None:
            self._log_event(f"WARNING: missing target on bracket for {symbol}")

        # Entry order
        entry_id = self.place_order(symbol, side, qty, order_type=entry_type,
                                    price=entry, notes="bracket-entry")

        # Determine bracket sides
        exit_side = "SELL" if side == "BUY" else "BUY"

        # Stop (protective)
        stop_id = self._new_oid()
        stop_order = Order(
            order_id=stop_id, symbol=symbol,
            side=OrderSide(exit_side), order_type=OrderType.STOP,
            qty=qty, price=stop, status=OrderStatus.WORKING,
            bracket_parent=entry_id, notes="bracket-stop",
        )

        # Target (profit)
        target_id = self._new_oid()
        target_order = Order(
            order_id=target_id, symbol=symbol,
            side=OrderSide(exit_side), order_type=OrderType.LIMIT,
            qty=qty, price=target, status=OrderStatus.WORKING,
            bracket_parent=entry_id, notes="bracket-target",
            oco_partner=stop_id,
        )
        stop_order.oco_partner = target_id

        self._orders[stop_id]   = stop_order
        self._orders[target_id] = target_order

        self._log_event(
            f"BRACKET PLACED: entry={entry_id} stop={stop_id} target={target_id} "
            f"entry={entry} stop={stop} target={target}"
        )
        return entry_id, stop_id, target_id

    # ── Order management ──────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        o = self._orders.get(order_id)
        if o is None or o.status != OrderStatus.WORKING:
            return False
        o.status = OrderStatus.CANCELLED
        self._log_event(f"ORDER CANCELLED: {order_id}")
        return True

    def cancel_all(self, symbol: Optional[str] = None):
        """Cancel all working orders, optionally filtered by symbol."""
        for o in self._orders.values():
            if o.status == OrderStatus.WORKING:
                if symbol is None or o.symbol == symbol:
                    self.cancel_order(o.order_id)

    # ── Price simulation ──────────────────────────────────────────────────────

    def simulate_price(self, symbol: str, price: float):
        """
        Feed a new market price. Evaluates all working stop/limit orders
        and triggers fills where appropriate.

        Call this on each new bar's close (or high/low for stop triggers).
        """
        self._require_connected()

        for order_id, o in list(self._orders.items()):
            if o.status != OrderStatus.WORKING or o.symbol != symbol:
                continue
            if o.order_type == OrderType.MARKET:
                continue

            triggered = False
            if o.order_type == OrderType.STOP:
                # Stop buy triggers when price >= stop; stop sell triggers when price <= stop
                if o.side == OrderSide.BUY  and price >= o.price:
                    triggered = True
                if o.side == OrderSide.SELL and price <= o.price:
                    triggered = True

            elif o.order_type == OrderType.LIMIT:
                # Limit buy triggers when price <= limit; limit sell when price >= limit
                if o.side == OrderSide.BUY  and price <= o.price:
                    triggered = True
                if o.side == OrderSide.SELL and price >= o.price:
                    triggered = True

            if triggered:
                slippage = self._calc_slippage(symbol)
                direction = 1 if o.side == OrderSide.BUY else -1
                fill_price = price + direction * slippage
                self._fill_order(order_id, fill_price)

    # ── Internal fill logic ───────────────────────────────────────────────────

    def _fill_order(self, order_id: str, fill_price: float):
        o = self._orders.get(order_id)
        if o is None or o.status != OrderStatus.WORKING:
            return

        o.status     = OrderStatus.FILLED
        o.fill_price = fill_price
        o.fill_time  = datetime.now(timezone.utc)

        fill = Fill(
            order_id=order_id, symbol=o.symbol, side=o.side,
            qty=o.qty, price=fill_price, timestamp=o.fill_time,
            slippage=abs(fill_price - (o.price or fill_price)),
        )
        self._fills.append(fill)

        # Update position
        self._update_position(o.symbol, o.side, o.qty, fill_price)

        # Update P&L
        self._update_equity()

        self._log_event(f"FILL: {o.side.value} {o.qty} {o.symbol} @ {fill_price:.4f} id={order_id}")

        # Cancel OCO sibling
        if o.oco_partner:
            self.cancel_order(o.oco_partner)

    def _update_position(self, symbol: str, side: OrderSide, qty: int, fill_price: float):
        signed_qty = qty if side == OrderSide.BUY else -qty
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol, qty=0, avg_price=0.0)

        pos = self._positions[symbol]
        if pos.qty == 0:
            pos.qty       = signed_qty
            pos.avg_price = fill_price
        elif (pos.qty > 0) == (signed_qty > 0):
            # Adding to position
            total_qty  = pos.qty + signed_qty
            pos.avg_price = (pos.avg_price * abs(pos.qty) + fill_price * qty) / abs(total_qty)
            pos.qty       = total_qty
        else:
            # Reducing/reversing position
            pos.qty += signed_qty
            if pos.qty == 0:
                pos.avg_price = 0.0

    def _update_equity(self):
        self.equity = self.balance
        for sym, pos in self._positions.items():
            if pos.qty == 0 or pos.open_pnl == 0.0:
                continue
            self.equity += pos.open_pnl

    def _update_open_pnl(self, symbol: str, current_price: float):
        spec = self._contract_specs.get(symbol, {})
        tick_size  = spec.get("tick_size",  0.01)
        tick_value = spec.get("tick_value", 1.0)
        pts_per_dollar = tick_value / tick_size

        pos = self._positions.get(symbol)
        if pos and pos.qty != 0:
            pos.open_pnl = (current_price - pos.avg_price) * pos.qty * pts_per_dollar
        self._update_equity()

    # ── Reconciliation ────────────────────────────────────────────────────────

    def reconcile(self, expected_positions: Dict[str, int]) -> List[str]:
        """
        Compare expected positions to actual. Returns list of mismatch descriptions.
        Empty list = reconciled.
        """
        issues = []
        all_symbols = set(expected_positions) | set(self._positions)
        for sym in all_symbols:
            expected = expected_positions.get(sym, 0)
            actual   = self._positions.get(sym, Position(sym, 0, 0.0)).qty
            if expected != actual:
                issues.append(f"RECONCILIATION MISMATCH {sym}: expected={expected} actual={actual}")
        for issue in issues:
            self._log_event(issue)
        return issues

    # ── Queries ───────────────────────────────────────────────────────────────

    def account_summary(self) -> dict:
        return {
            "connected": self._connected,
            "balance":   round(self.balance, 2),
            "equity":    round(self.equity, 2),
            "n_orders":  len(self._orders),
            "n_fills":   len(self._fills),
            "positions": {
                sym: {"qty": p.qty, "avg_price": p.avg_price, "open_pnl": p.open_pnl}
                for sym, p in self._positions.items() if p.qty != 0
            },
        }

    def working_orders(self, symbol: Optional[str] = None) -> List[Order]:
        return [
            o for o in self._orders.values()
            if o.status == OrderStatus.WORKING
            and (symbol is None or o.symbol == symbol)
        ]

    def position(self, symbol: str) -> int:
        return self._positions.get(symbol, Position(symbol, 0, 0.0)).qty

    def fills(self) -> List[Fill]:
        return list(self._fills)

    def log(self) -> List[str]:
        return list(self._log)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _new_oid() -> str:
        return str(uuid.uuid4())[:8]

    def _calc_slippage(self, symbol: str) -> float:
        spec = self._contract_specs.get(symbol, {})
        tick_size = spec.get("tick_size", 0.01)
        return self.default_slippage_ticks * tick_size

    def _log_event(self, msg: str):
        ts  = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        print(entry)


# ── Smoke test ────────────────────────────────────────────────────────────────

def _smoke_test():
    print("\n" + "=" * 60)
    print("  MOCK BROKER SMOKE TEST")
    print("  No real API calls. No capital at risk.")
    print("=" * 60)

    broker = MockBroker(initial_balance=50_000, default_slippage_ticks=1)

    # --- Test 1: Market order (immediate fill)
    print("\n[TEST 1] Market order")
    oid = broker.place_order("GC", "BUY", qty=1, order_type="MARKET", price=2000.0)
    assert broker.position("GC") == 1, "Position should be +1"
    print("  PASS: position is +1 after market buy")

    # --- Test 2: Flat position
    broker.place_order("GC", "SELL", qty=1, order_type="MARKET", price=2005.0)
    assert broker.position("GC") == 0, "Position should be 0 after close"
    print("  PASS: position is 0 after close")

    # --- Test 3: Bracket order — target hit
    print("\n[TEST 2] Bracket order — target fill")
    entry_id, stop_id, target_id = broker.place_bracket(
        "GC", "BUY", qty=1, entry=2000.0, stop=1995.0, target=2010.0
    )
    assert broker.position("GC") == 1, "Position should be +1 after bracket entry"

    broker.simulate_price("GC", 2011.0)   # target crossed
    assert broker._orders[target_id].status == OrderStatus.FILLED, "Target should be filled"
    assert broker._orders[stop_id].status   == OrderStatus.CANCELLED, "Stop should be cancelled (OCO)"
    assert broker.position("GC") == 0, "Position should be 0 after target fill"
    print("  PASS: target filled, stop cancelled, flat")

    # --- Test 4: Bracket order — stop hit
    print("\n[TEST 3] Bracket order — stop fill")
    broker2 = MockBroker(initial_balance=50_000)
    e, s, t = broker2.place_bracket("SI", "BUY", qty=1, entry=30.0, stop=29.5, target=31.0)
    broker2.simulate_price("SI", 29.4)   # stop crossed
    assert broker2._orders[s].status == OrderStatus.FILLED,   "Stop should be filled"
    assert broker2._orders[t].status == OrderStatus.CANCELLED, "Target should be cancelled"
    assert broker2.position("SI") == 0
    print("  PASS: stop filled, target cancelled, flat")

    # --- Test 5: Disconnect
    print("\n[TEST 4] Disconnect / reconnect")
    broker3 = MockBroker(initial_balance=50_000)
    broker3.disconnect()
    try:
        broker3.place_order("GC", "BUY", qty=1)
        assert False, "Should have raised ConnectionError"
    except ConnectionError:
        print("  PASS: ConnectionError raised when disconnected")
    broker3.reconnect()
    broker3.place_order("GC", "BUY", qty=1, price=2000.0)
    assert broker3.position("GC") == 1
    print("  PASS: order accepted after reconnect")

    # --- Test 6: Order rejection (chaos mode)
    print("\n[TEST 5] Order rejection simulation")
    chaos = MockBroker(initial_balance=50_000, reject_probability=1.0)
    try:
        chaos.place_order("GC", "BUY", qty=1)
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  PASS: ValueError raised on rejected order")

    # --- Test 7: Reconciliation mismatch
    print("\n[TEST 6] Reconciliation mismatch detection")
    broker4 = MockBroker(initial_balance=50_000)
    broker4.place_order("GC", "BUY", qty=1, price=2000.0)
    issues = broker4.reconcile({"GC": 2})   # expect 2 but actual is 1
    assert len(issues) == 1 and "GC" in issues[0], "Should detect mismatch"
    print(f"  PASS: mismatch detected → {issues[0]}")

    # --- Test 8: Cancel all
    print("\n[TEST 7] Cancel all working orders")
    broker5 = MockBroker(initial_balance=50_000)
    broker5.place_order("GC", "BUY", qty=1, order_type="STOP", price=2001.0)
    broker5.place_order("GC", "SELL", qty=1, order_type="LIMIT", price=2010.0)
    assert len(broker5.working_orders()) == 2
    broker5.cancel_all()
    assert len(broker5.working_orders()) == 0
    print("  PASS: all working orders cancelled")

    print("\n" + "=" * 60)
    print("  ALL SMOKE TESTS PASSED")
    print("  Broker: MOCK_ONLY | No capital at risk | No API calls")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    _smoke_test()
