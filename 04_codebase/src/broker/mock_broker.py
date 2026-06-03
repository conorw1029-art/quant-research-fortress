"""
mock_broker.py — MockBroker: in-process simulation broker
==========================================================
Implements the full BrokerAdapter interface without any real API calls.
Refactored and extended from the original tick_mock_broker.py.

Safety guarantees:
  - ZERO network calls under any circumstances
  - ZERO real credentials required
  - Always operates in DRY_RUN or DEMO mode — never LIVE
  - Can be constructed from BrokerFactory.create(BrokerMode.DRY_RUN, "mock", {})

New capabilities over tick_mock_broker.py:
  - Implements full BrokerAdapter abstract interface
  - simulate_tick() for paper-trading mode (bid/ask/last feed)
  - Proper BracketOrder / BrokerOrder model objects
  - get_account_state() returning full AccountState
  - reconcile() returning ReconciliationResult
  - heartbeat() for health checks

Smoke test:
  venv_new/Scripts/python.exe -X utf8 04_codebase/src/broker/mock_broker.py
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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

# Default starting balance for mock accounts
_DEFAULT_BALANCE = 50_000.0

# Contract specification table used for P&L calculation
_CONTRACT_SPECS: Dict[str, dict] = {
    "GC":  {"tick_size": 0.10,  "tick_value": 10.0,    "name": "Gold"},
    "MGC": {"tick_size": 0.10,  "tick_value": 1.0,     "name": "Micro Gold"},
    "SI":  {"tick_size": 0.005, "tick_value": 25.0,    "name": "Silver"},
    "SIL": {"tick_size": 0.005, "tick_value": 5.0,     "name": "Micro Silver"},
    "ES":  {"tick_size": 0.25,  "tick_value": 12.5,    "name": "E-mini S&P 500"},
    "MES": {"tick_size": 0.25,  "tick_value": 1.25,    "name": "Micro E-mini S&P 500"},
    "NQ":  {"tick_size": 0.25,  "tick_value": 5.0,     "name": "E-mini NASDAQ-100"},
    "MNQ": {"tick_size": 0.25,  "tick_value": 0.50,    "name": "Micro E-mini NASDAQ-100"},
}


def _base_symbol(symbol: str) -> str:
    """Strip contract month/year suffix to get base symbol (e.g. 'MESM5' -> 'MES')."""
    s = symbol.upper().strip()
    _month_codes = frozenset("FGHJKMNQUVXZ")
    i = len(s) - 1
    while i > 0 and s[i].isdigit():
        i -= 1
    if i > 0 and s[i] in _month_codes:
        return s[:i]
    return s


def _spec(symbol: str) -> dict:
    base = _base_symbol(symbol)
    return _CONTRACT_SPECS.get(base, {"tick_size": 0.01, "tick_value": 1.0, "name": base})


def _pnl_per_point(symbol: str) -> float:
    s = _spec(symbol)
    return s["tick_value"] / s["tick_size"]


class MockBroker(BrokerAdapter):
    """
    Full in-process broker simulation.

    All orders are stored in memory. Fills are triggered when:
      - Market orders: immediately on placement
      - Stop/Limit orders: when simulate_price() or simulate_tick() crosses
        the trigger level

    The internal state is intentionally simple and single-threaded.
    This is designed for testing, dry-run validation, and paper trading
    scaffolding — not for production concurrency.

    Args:
        mode:                  Must be DRY_RUN or DEMO (never LIVE)
        config:                Dict with optional keys:
            initial_balance      (float)  starting account balance, default 50_000
            default_slippage_ticks (int)  default slippage on fills, default 1
            reject_probability   (float)  0.0-1.0 chaos-mode rejection rate
        connected:             Start in connected state (default True)
    """

    def __init__(
        self,
        mode:      BrokerMode = BrokerMode.DRY_RUN,
        config:    Optional[dict] = None,
        connected: bool = True,
    ):
        if mode == BrokerMode.LIVE:
            raise ValueError("MockBroker cannot operate in LIVE mode. Use a real adapter.")

        super().__init__(mode=mode, config=config or {})

        self._balance              = float(self.config.get("initial_balance", _DEFAULT_BALANCE))
        self._equity               = self._balance
        self._daily_pnl            = 0.0
        self._default_slippage     = int(self.config.get("default_slippage_ticks", 1))
        self._reject_probability   = float(self.config.get("reject_probability", 0.0))
        self._connected            = connected

        self._orders:    Dict[str, BrokerOrder]   = {}
        self._brackets:  Dict[str, BracketOrder]  = {}
        self._positions: Dict[str, BrokerPosition] = {}
        self._fills:     List[BrokerFill]          = []
        self._event_log: List[str]                 = []

        self._log_event("MockBroker initialised — NO REAL API CALLS — mode=%s" % mode.value)

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        self._connected = True
        self._log_event("MockBroker.connect() — always succeeds in simulation")
        return True

    def disconnect(self) -> None:
        self._connected = False
        self._log_event("MockBroker.disconnect() — simulation disconnect")

    def is_connected(self) -> bool:
        return self._connected

    def reconnect(self) -> bool:
        """Simulate a reconnect (for chaos/recovery testing)."""
        self._connected = True
        self._log_event("MockBroker.reconnect() — simulation reconnect")
        return True

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("MockBroker: broker is disconnected (simulation)")

    # ── Account information ────────────────────────────────────────────────────

    def get_account_state(self) -> AccountState:
        self._require_connected()
        margin_used = 0.0   # mock does not track margin requirements
        return AccountState(
            balance          = round(self._balance, 2),
            equity           = round(self._equity, 2),
            margin_used      = margin_used,
            margin_available = round(self._equity - margin_used, 2),
            daily_pnl        = round(self._daily_pnl, 2),
            positions        = [p for p in self._positions.values() if not p.is_flat],
        )

    def get_positions(self) -> list[BrokerPosition]:
        self._require_connected()
        return [p for p in self._positions.values() if not p.is_flat]

    def get_open_orders(self) -> list[BrokerOrder]:
        self._require_connected()
        return [o for o in self._orders.values() if o.status == OrderStatus.WORKING]

    def get_fills(self) -> List[BrokerFill]:
        return list(self._fills)

    def get_brackets(self) -> Dict[str, BracketOrder]:
        return dict(self._brackets)

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
        Place an OSO bracket (entry + stop + target).

        The entry order fills immediately if order_type is MARKET.
        Stop and target legs go to WORKING status and wait for simulate_price()
        or simulate_tick() to cross their levels.

        Args:
            symbol:       Base or full symbol (e.g. "GC" or "GCM5")
            side:         "BUY" or "SELL"
            qty:          Contracts (>= 1)
            entry_price:  Limit price (None for market entry)
            stop_price:   Stop-loss level
            target_price: Profit target level
            order_type:   "MARKET" or "LIMIT"

        Returns:
            BracketOrder with all three order IDs.
        """
        self._require_connected()

        side_enum   = OrderSide(side.upper())
        otype_enum  = OrderType(order_type.upper())
        exit_side   = OrderSide.SELL if side_enum == OrderSide.BUY else OrderSide.BUY
        group_id    = self._new_oid()

        # ── Entry order ────────────────────────────────────────────────────────
        entry_id = self._new_oid()
        entry_order = BrokerOrder(
            order_id         = entry_id,
            symbol           = symbol,
            side             = side_enum,
            qty              = qty,
            order_type       = otype_enum,
            status           = OrderStatus.WORKING,
            limit_price      = entry_price if otype_enum == OrderType.LIMIT else None,
            bracket_group_id = group_id,
        )

        # Chaos-mode rejection
        if self._reject_probability > 0 and random.random() < self._reject_probability:
            entry_order.status = OrderStatus.REJECTED
            self._orders[entry_id] = entry_order
            self._log_event(f"BRACKET ENTRY REJECTED (chaos): {side} {qty} {symbol}")
            raise ValueError(
                f"MockBroker: order rejected by chaos simulation (reject_probability="
                f"{self._reject_probability})"
            )

        # ── Stop order ─────────────────────────────────────────────────────────
        stop_id = self._new_oid()
        stop_order = BrokerOrder(
            order_id         = stop_id,
            symbol           = symbol,
            side             = exit_side,
            qty              = qty,
            order_type       = OrderType.STOP,
            status           = OrderStatus.WORKING,
            stop_price       = stop_price,
            bracket_group_id = group_id,
        )

        # ── Target order ───────────────────────────────────────────────────────
        target_id = self._new_oid()
        target_order = BrokerOrder(
            order_id         = target_id,
            symbol           = symbol,
            side             = exit_side,
            qty              = qty,
            order_type       = OrderType.LIMIT,
            status           = OrderStatus.WORKING,
            limit_price      = target_price,
            bracket_group_id = group_id,
        )

        # Cross-link the OCO pair
        stop_order.oco_partner_id   = target_id
        target_order.oco_partner_id = stop_id

        # Register all three orders
        self._orders[entry_id]  = entry_order
        self._orders[stop_id]   = stop_order
        self._orders[target_id] = target_order

        bracket = BracketOrder(
            entry_order_id  = entry_id,
            stop_order_id   = stop_id,
            target_order_id = target_id,
            symbol          = symbol,
            side            = side_enum,
            qty             = qty,
            entry_price     = entry_price,
            stop_price      = stop_price,
            target_price    = target_price,
            status          = BracketStatus.OPEN,
        )
        self._brackets[group_id] = bracket

        self._log_event(
            f"BRACKET PLACED [{group_id[:6]}]: {side} {qty} {symbol} | "
            f"entry={'MKT' if not entry_price else entry_price} "
            f"stop={stop_price} target={target_price}"
        )

        # ── Fill entry order ───────────────────────────────────────────────────
        if otype_enum == OrderType.MARKET:
            fill_px = entry_price or 0.0
            slippage = self._calc_slippage(symbol, side_enum)
            self._fill_order(entry_id, fill_px + slippage)
            bracket.status = BracketStatus.ACTIVE

        return bracket

    def _place_single_order(
        self,
        symbol:     str,
        side:       str,
        qty:        int,
        order_type: str = "MARKET",
        price:      Optional[float] = None,
    ) -> BrokerOrder:
        """Place a single non-bracket order. Returns the BrokerOrder object."""
        self._require_connected()
        side_enum  = OrderSide(side.upper())
        otype_enum = OrderType(order_type.upper())

        oid = self._new_oid()
        order = BrokerOrder(
            order_id   = oid,
            symbol     = symbol,
            side       = side_enum,
            qty        = qty,
            order_type = otype_enum,
            status     = OrderStatus.WORKING,
            limit_price = price if otype_enum == OrderType.LIMIT else None,
            stop_price  = price if otype_enum == OrderType.STOP  else None,
        )
        self._orders[oid] = order

        if otype_enum == OrderType.MARKET:
            self._fill_order(oid, price or 0.0)

        return order

    # ── Order management ───────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        o = self._orders.get(order_id)
        if o is None or o.status != OrderStatus.WORKING:
            return False
        o.status = OrderStatus.CANCELLED
        self._log_event(f"ORDER CANCELLED: {order_id}")
        return True

    def cancel_all(self) -> int:
        count = 0
        for o in list(self._orders.values()):
            if o.status == OrderStatus.WORKING:
                if self.cancel_order(o.order_id):
                    count += 1
        self._log_event(f"CANCEL_ALL: {count} orders cancelled")
        return count

    def flatten_symbol(self, symbol: str) -> bool:
        """Close all positions in symbol at market."""
        self._require_connected()
        # Cancel all working orders for this symbol first
        for o in list(self._orders.values()):
            if o.symbol == symbol and o.status == OrderStatus.WORKING:
                self.cancel_order(o.order_id)

        pos = self._positions.get(symbol)
        if pos is None or pos.is_flat:
            return True

        close_side = "SELL" if pos.is_long else "BUY"
        self._place_single_order(
            symbol=symbol, side=close_side, qty=abs(pos.qty),
            order_type="MARKET", price=pos.avg_price,
        )
        self._log_event(f"FLATTEN_SYMBOL: {symbol} flattened")
        return True

    def flatten_all(self) -> bool:
        """Flatten all open positions."""
        self._require_connected()
        symbols = list(self._positions.keys())
        ok = True
        for symbol in symbols:
            if not self.flatten_symbol(symbol):
                ok = False
        return ok

    # ── Price simulation ───────────────────────────────────────────────────────

    def simulate_price(self, symbol: str, price: float) -> None:
        """
        Feed a single bar's closing price for a symbol.
        Triggers stop/limit fills for any working orders on that symbol.

        Call this after each bar closes to drive the simulation.
        """
        self._require_connected()

        for order_id, o in list(self._orders.items()):
            if o.status != OrderStatus.WORKING or o.symbol != symbol:
                continue

            triggered = False

            if o.order_type == OrderType.STOP:
                # Stop BUY fires when price rises to/above stop level
                # Stop SELL fires when price falls to/below stop level
                if o.side == OrderSide.BUY  and price >= (o.stop_price or 0):
                    triggered = True
                if o.side == OrderSide.SELL and price <= (o.stop_price or float("inf")):
                    triggered = True

            elif o.order_type == OrderType.LIMIT:
                # Limit BUY fires when price falls to/below limit level
                # Limit SELL fires when price rises to/above limit level
                if o.side == OrderSide.BUY  and price <= (o.limit_price or 0):
                    triggered = True
                if o.side == OrderSide.SELL and price >= (o.limit_price or float("inf")):
                    triggered = True

            if triggered:
                slippage   = self._calc_slippage(symbol, o.side)
                fill_price = price + slippage
                self._fill_order(order_id, fill_price)

        # Update open P&L for this symbol at the new price
        self._update_open_pnl(symbol, price)

    def simulate_tick(self, symbol: str, bid: float, ask: float, last: float) -> None:
        """
        Feed a real-time tick (bid/ask/last) for paper trading simulation.
        Uses last price for stop/limit triggering, consistent with exchange rules.
        """
        self._require_connected()
        self.simulate_price(symbol, last)

    # ── Internal fill and position logic ──────────────────────────────────────

    def _fill_order(self, order_id: str, fill_price: float) -> None:
        o = self._orders.get(order_id)
        if o is None or o.status != OrderStatus.WORKING:
            return

        now            = datetime.now(timezone.utc)
        o.status       = OrderStatus.FILLED
        o.fill_price   = fill_price
        o.fill_qty     = o.qty
        o.filled_at    = now

        fill = BrokerFill(
            order_id          = order_id,
            symbol            = o.symbol,
            side              = o.side,
            qty               = o.qty,
            price             = fill_price,
            timestamp         = now,
            slippage_estimate = abs(fill_price - (o.stop_price or o.limit_price or fill_price)),
        )
        self._fills.append(fill)

        self._log_event(
            f"FILL: {o.side.value} {o.qty} {o.symbol} @ {fill_price:.4f} "
            f"order={order_id[:6]}"
        )

        # Update position
        self._update_position(o.symbol, o.side, o.qty, fill_price)

        # Cancel OCO partner
        if o.oco_partner_id:
            self.cancel_order(o.oco_partner_id)

        # Update bracket status
        if o.bracket_group_id and o.bracket_group_id in self._brackets:
            bracket = self._brackets[o.bracket_group_id]
            if order_id == bracket.entry_order_id:
                bracket.status = BracketStatus.ACTIVE
            elif order_id in (bracket.stop_order_id, bracket.target_order_id):
                bracket.status   = BracketStatus.CLOSED
                bracket.closed_at = now
                bracket.exit_via  = (
                    "STOP"   if order_id == bracket.stop_order_id else "TARGET"
                )

    def _update_position(
        self, symbol: str, side: OrderSide, qty: int, fill_price: float
    ) -> None:
        signed = qty if side == OrderSide.BUY else -qty

        if symbol not in self._positions:
            self._positions[symbol] = BrokerPosition(
                symbol=symbol, qty=0, avg_price=0.0
            )

        pos = self._positions[symbol]

        if pos.qty == 0:
            pos.qty       = signed
            pos.avg_price = fill_price

        elif (pos.qty > 0) == (signed > 0):
            # Adding to existing position — update weighted average price
            total = pos.qty + signed
            pos.avg_price = (
                (pos.avg_price * abs(pos.qty) + fill_price * abs(signed))
                / abs(total)
            )
            pos.qty = total

        else:
            # Reducing or reversing
            realised = (
                (fill_price - pos.avg_price) * min(abs(pos.qty), abs(signed))
                * _pnl_per_point(symbol)
                * (1 if pos.qty > 0 else -1)
            )
            pos.realized_pnl   += realised
            self._daily_pnl    += realised
            self._balance      += realised

            pos.qty += signed
            if pos.qty == 0:
                pos.avg_price = 0.0

        self._update_equity()

    def _update_open_pnl(self, symbol: str, current_price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None or pos.qty == 0:
            return
        ppl = _pnl_per_point(symbol)
        pos.unrealized_pnl = (current_price - pos.avg_price) * pos.qty * ppl
        self._update_equity()

    def _update_equity(self) -> None:
        open_pnl = sum(
            p.unrealized_pnl
            for p in self._positions.values()
            if not p.is_flat
        )
        self._equity = self._balance + open_pnl

    # ── Reconciliation ─────────────────────────────────────────────────────────

    def reconcile(self) -> ReconciliationResult:
        """
        Compare internal positions against themselves.
        MockBroker has no external state, so internal == broker by definition.
        This method exists to satisfy the interface and for use in test harnesses
        that inject expected_positions.
        """
        internal = {
            sym: pos.qty
            for sym, pos in self._positions.items()
            if pos.qty != 0
        }
        result = ReconciliationResult(
            internal_positions = internal,
            broker_positions   = dict(internal),  # mock: broker state IS internal state
            is_clean           = True,
        )
        self._log_event("RECONCILE: clean (mock — internal == broker)")
        return result

    def reconcile_against(self, expected: Dict[str, int]) -> ReconciliationResult:
        """
        Compare internal positions against a provided expected dict.
        Useful for test scenarios where you want to check specific state.
        """
        internal = {
            sym: pos.qty
            for sym, pos in self._positions.items()
            if pos.qty != 0
        }
        result = ReconciliationResult(
            internal_positions = internal,
            broker_positions   = expected,
        )
        all_symbols = set(internal) | set(expected)
        for sym in all_symbols:
            actual   = internal.get(sym, 0)
            expected_qty = expected.get(sym, 0)
            if actual != expected_qty:
                result.add_mismatch({
                    "symbol":   sym,
                    "internal": actual,
                    "expected": expected_qty,
                    "delta":    actual - expected_qty,
                })
        return result

    # ── Heartbeat ──────────────────────────────────────────────────────────────

    def heartbeat(self) -> bool:
        return self._connected

    # ── Convenience query methods ──────────────────────────────────────────────

    def position_qty(self, symbol: str) -> int:
        """Return signed position qty for symbol (0 if flat)."""
        return self._positions.get(symbol, BrokerPosition(symbol, 0, 0.0)).qty

    def account_summary(self) -> dict:
        """Human-readable account summary dict."""
        return self.get_account_state().as_dict()

    def event_log(self) -> List[str]:
        return list(self._event_log)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _new_oid() -> str:
        return str(uuid.uuid4())[:12]

    def _calc_slippage(self, symbol: str, side: OrderSide) -> float:
        """Return signed slippage in price points for a fill."""
        tick = _spec(symbol).get("tick_size", 0.01)
        magnitude = self._default_slippage * tick
        # Buys fill higher, sells fill lower
        return magnitude if side == OrderSide.BUY else -magnitude

    def _log_event(self, msg: str) -> None:
        ts    = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        entry = f"[{ts}] [MockBroker] {msg}"
        self._event_log.append(entry)
        self._log.debug(msg)


# ── Smoke test ─────────────────────────────────────────────────────────────────

def _smoke_test() -> None:
    import sys
    print("\n" + "=" * 65)
    print("  MOCK BROKER SMOKE TEST — src/broker/mock_broker.py")
    print("  No real API calls.  No capital at risk.")
    print("=" * 65)

    broker = MockBroker(mode=BrokerMode.DRY_RUN, config={"initial_balance": 50_000})
    assert broker.connect()
    assert broker.is_connected()
    assert broker.is_demo()
    assert not broker.is_live()

    # Test 1: bracket order — target fill
    print("\n[TEST 1] Bracket order — target fill")
    bracket = broker.place_bracket_order("GC", "BUY", 1, 2000.0, 1995.0, 2010.0, "MARKET")
    assert broker.position_qty("GC") == 1, f"Expected +1, got {broker.position_qty('GC')}"
    broker.simulate_price("GC", 2011.0)
    assert broker._orders[bracket.stop_order_id].status   == OrderStatus.CANCELLED
    assert broker._orders[bracket.target_order_id].status == OrderStatus.FILLED
    assert broker.position_qty("GC") == 0
    print("  PASS: target filled, stop cancelled, position flat")

    # Test 2: bracket order — stop fill
    print("\n[TEST 2] Bracket order — stop fill")
    b2 = broker.place_bracket_order("SI", "BUY", 1, 30.0, 29.5, 31.0, "MARKET")
    assert broker.position_qty("SI") == 1
    broker.simulate_price("SI", 29.4)
    assert broker._orders[b2.stop_order_id].status   == OrderStatus.FILLED
    assert broker._orders[b2.target_order_id].status == OrderStatus.CANCELLED
    assert broker.position_qty("SI") == 0
    print("  PASS: stop filled, target cancelled, position flat")

    # Test 3: disconnect / reconnect
    print("\n[TEST 3] Disconnect / reconnect cycle")
    broker.disconnect()
    assert not broker.is_connected()
    try:
        broker.place_bracket_order("GC", "BUY", 1, 2000.0, 1990.0, 2020.0)
        assert False, "Should raise ConnectionError"
    except ConnectionError:
        pass
    broker.reconnect()
    assert broker.is_connected()
    print("  PASS: ConnectionError raised when disconnected; reconnect works")

    # Test 4: cancel_all
    print("\n[TEST 4] cancel_all")
    broker2 = MockBroker(mode=BrokerMode.DRY_RUN)
    broker2.connect()
    broker2._place_single_order("GC", "BUY", 1, "STOP", price=2001.0)
    broker2._place_single_order("GC", "SELL", 1, "LIMIT", price=2010.0)
    assert len(broker2.get_open_orders()) == 2
    n = broker2.cancel_all()
    assert n == 2
    assert len(broker2.get_open_orders()) == 0
    print("  PASS: cancel_all cancelled 2 working orders")

    # Test 5: reconcile
    print("\n[TEST 5] reconcile")
    broker3 = MockBroker(mode=BrokerMode.DRY_RUN)
    broker3.connect()
    broker3.place_bracket_order("GC", "BUY", 1, 2000.0, 1990.0, 2020.0)
    result = broker3.reconcile()
    assert result.is_clean
    mismatch_result = broker3.reconcile_against({"GC": 2})  # expect 2, actual 1
    assert not mismatch_result.is_clean
    assert len(mismatch_result.mismatches) == 1
    print("  PASS: reconcile clean; mismatch detected correctly")

    # Test 6: account state
    print("\n[TEST 6] account state")
    state = broker.get_account_state()
    assert state.balance > 0
    assert isinstance(state.positions, list)
    print(f"  PASS: account state balance=${state.balance:,.0f}")

    # Test 7: chaos rejection
    print("\n[TEST 7] chaos rejection (reject_probability=1.0)")
    chaos = MockBroker(mode=BrokerMode.DRY_RUN, config={"reject_probability": 1.0})
    chaos.connect()
    try:
        chaos.place_bracket_order("GC", "BUY", 1, 2000.0, 1990.0, 2020.0)
        assert False, "Should raise ValueError"
    except ValueError:
        pass
    print("  PASS: ValueError raised on chaos-rejected order")

    print("\n" + "=" * 65)
    print("  ALL TESTS PASSED")
    print("  Broker: MOCK_ONLY | No capital at risk | No API calls")
    print("=" * 65 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    _smoke_test()
