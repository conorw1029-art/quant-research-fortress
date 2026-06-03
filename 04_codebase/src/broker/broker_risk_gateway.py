"""
broker_risk_gateway.py — Pre-trade risk enforcement gateway
=============================================================
The BrokerRiskGateway wraps a BrokerAdapter and enforces risk rules BEFORE
any order reaches the broker. It is the final automated safety gate.

Rules enforced (in order):
  1. Kill switch (reads KILL_SWITCH.txt — blocks all orders if STOP)
  2. Connection check (adapter must be connected)
  3. Max contracts per trade
  4. Daily loss limit
  5. Trailing drawdown limit (Topstep-style trailing)
  6. News window block (configurable time blackout windows)
  7. Stale data block (bars must be fresh within threshold)
  8. No partial TP when qty == 1 (fractional contracts not supported)
  9. Strategy-level halt check

Design principles:
  - Gateway is BLOCKING: it raises exceptions rather than silently swallowing
    rejected orders. The caller must handle the rejection explicitly.
  - All blocked orders are logged to blocked_orders_YYYYMMDD.jsonl.
  - The gateway never places orders itself — it delegates to the wrapped adapter.
  - Adding new rules is done by implementing _check_<rule_name>() and adding
    it to the _RULE_CHAIN list in check_order().

Usage:
    from src.broker.broker_risk_gateway import BrokerRiskGateway, OrderCandidate

    gateway = BrokerRiskGateway(
        adapter=mock_broker,
        max_contracts_per_trade=1,
        max_daily_loss_usd=500.0,
        max_trailing_dd_usd=1000.0,
        kill_switch_path=Path("KILL_SWITCH.txt"),
    )

    ok, reason = gateway.check_order(candidate)
    if ok:
        bracket = gateway.place_bracket_order(...)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .base import BrokerAdapter
from .broker_models import AccountState, BracketOrder, BrokerOrder, BrokerPosition

logger = logging.getLogger(__name__)

# Path defaults
_REPO_ROOT       = Path(__file__).parents[3]
_KILL_SWITCH_DEFAULT = _REPO_ROOT / "KILL_SWITCH.txt"
_BLOCKED_LOG_DIR = _REPO_ROOT / "06_live_trading" / "logs"


# ── Order candidate ────────────────────────────────────────────────────────────

@dataclass
class OrderCandidate:
    """
    Represents a proposed order before it reaches the broker.

    All risk checks are evaluated against this object.
    The gateway either approves it (returns to caller) or blocks it with a reason.
    """
    symbol:       str
    side:         str       # "BUY" or "SELL"
    qty:          int
    entry_price:  Optional[float]   # None for market
    stop_price:   float
    target_price: float
    order_type:   str = "MARKET"    # "MARKET" or "LIMIT"

    # Context provided by caller — used for stale data and halt checks
    strategy_name: Optional[str] = None
    bar_timestamp: Optional[datetime] = None   # timestamp of last bar for this symbol

    # Metadata (not used for risk checks, carried through for logging)
    signal_id: Optional[str] = None

    def stop_distance_pts(self) -> Optional[float]:
        if self.entry_price is None:
            return None
        return abs(self.entry_price - self.stop_price)


# ── News window ────────────────────────────────────────────────────────────────

@dataclass
class NewsWindow:
    """A time window during which trading is blocked (e.g. around NFP, FOMC)."""
    name:       str
    start_utc:  str    # "HH:MM" 24h UTC
    end_utc:    str    # "HH:MM" 24h UTC

    def is_active(self, now_utc: Optional[datetime] = None) -> bool:
        now = now_utc or datetime.now(timezone.utc)
        current_hm = now.strftime("%H:%M")
        return self.start_utc <= current_hm <= self.end_utc


# ── BrokerRiskGateway ──────────────────────────────────────────────────────────

class BrokerRiskGateway:
    """
    Risk enforcement wrapper around a BrokerAdapter.

    All order-placement calls flow through check_order() first.
    If any rule blocks the order, it is logged and a rejection is returned.
    The underlying adapter's place_bracket_order() is only called when all
    rules pass.

    Args:
        adapter:                  The real or mock broker adapter to wrap.
        max_contracts_per_trade:  Hard maximum contracts per order (default 1).
        max_daily_loss_usd:       Block new orders if daily loss exceeds this.
        max_trailing_dd_usd:      Topstep-style trailing DD limit.
        news_windows:             List of NewsWindow objects (blocked time ranges).
        bar_freshness_seconds:    Max age of last bar before stale-data block.
        halted_strategies:        Set of strategy names that should not trade.
        kill_switch_path:         Path to KILL_SWITCH.txt file.
        log_dir:                  Override for blocked-order log directory.
    """

    def __init__(
        self,
        adapter:                 BrokerAdapter,
        max_contracts_per_trade: int = 1,
        max_daily_loss_usd:      float = 500.0,
        max_trailing_dd_usd:     float = 1_000.0,
        news_windows:            Optional[List[NewsWindow]] = None,
        bar_freshness_seconds:   int = 300,        # 5 minutes
        halted_strategies:       Optional[set] = None,
        kill_switch_path:        Optional[Path] = None,
        log_dir:                 Optional[Path] = None,
    ):
        self.adapter                 = adapter
        self.max_contracts_per_trade = max_contracts_per_trade
        self.max_daily_loss_usd      = max_daily_loss_usd
        self.max_trailing_dd_usd     = max_trailing_dd_usd
        self.news_windows            = news_windows or []
        self.bar_freshness_seconds   = bar_freshness_seconds
        self.halted_strategies       = halted_strategies or set()
        self.kill_switch_path        = kill_switch_path or _KILL_SWITCH_DEFAULT
        self.log_dir                 = log_dir or _BLOCKED_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Peak equity tracker (for trailing DD)
        self._peak_equity: Optional[float] = None

        # Statistics
        self._orders_approved = 0
        self._orders_blocked  = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_order(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """
        Run all pre-trade risk checks against the order candidate.

        Returns:
            (True, "")              if the order is approved
            (False, reason_str)     if any rule blocks the order

        This method NEVER places an order — it only evaluates rules.
        Use place_bracket_order() to both check and execute.
        """
        checks = [
            self._check_kill_switch,
            self._check_connection,
            self._check_max_contracts,
            self._check_daily_loss,
            self._check_trailing_dd,
            self._check_news_window,
            self._check_bar_freshness,
            self._check_strategy_halt,
            self._check_partial_tp_qty,
        ]

        for check_fn in checks:
            allowed, reason = check_fn(candidate)
            if not allowed:
                self._block_order(candidate, reason, check_fn.__name__)
                self._orders_blocked += 1
                return False, reason

        self._orders_approved += 1
        return True, ""

    def place_bracket_order(
        self, candidate: OrderCandidate
    ) -> BracketOrder:
        """
        Run all risk checks then place a bracket order if approved.

        Raises:
            PermissionError: if any risk rule blocks the order
            ConnectionError: if adapter is not connected
            ValueError:      if order parameters are invalid
        """
        allowed, reason = self.check_order(candidate)
        if not allowed:
            raise PermissionError(
                f"BrokerRiskGateway: order blocked — {reason}"
            )

        return self.adapter.place_bracket_order(
            symbol       = candidate.symbol,
            side         = candidate.side,
            qty          = candidate.qty,
            entry_price  = candidate.entry_price,
            stop_price   = candidate.stop_price,
            target_price = candidate.target_price,
            order_type   = candidate.order_type,
        )

    def update_peak_equity(self, current_equity: float) -> None:
        """
        Update the peak equity for trailing drawdown tracking.
        Call this on each bar or after each fill.
        """
        if self._peak_equity is None or current_equity > self._peak_equity:
            self._peak_equity = current_equity

    def halt_strategy(self, strategy_name: str, reason: str) -> None:
        """Add a strategy to the halt list. Halted strategies cannot place orders."""
        self.halted_strategies.add(strategy_name)
        logger.warning("Strategy halted: %s — reason: %s", strategy_name, reason)

    def resume_strategy(self, strategy_name: str) -> None:
        """Remove a strategy from the halt list."""
        self.halted_strategies.discard(strategy_name)
        logger.info("Strategy resumed: %s", strategy_name)

    def get_stats(self) -> dict:
        return {
            "orders_approved": self._orders_approved,
            "orders_blocked":  self._orders_blocked,
            "halted_strategies": list(self.halted_strategies),
            "peak_equity":     self._peak_equity,
        }

    # ── Individual rule checks ─────────────────────────────────────────────────

    def _check_kill_switch(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """Block all orders when KILL_SWITCH.txt contains 'STOP'."""
        try:
            content = self.kill_switch_path.read_text(encoding="utf-8").strip().upper()
            if content == "STOP":
                return False, "KILL_SWITCH=STOP: all trading is halted"
            # Any other value (including 'RUN') allows trading
            return True, ""
        except FileNotFoundError:
            return True, ""   # Missing file = safe default (RUN)
        except Exception as e:
            # Any read error → conservatively block
            return False, f"KILL_SWITCH_READ_ERROR: {e}"

    def _check_connection(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        if not self.adapter.is_connected():
            return False, "BROKER_DISCONNECTED: adapter is not connected"
        return True, ""

    def _check_max_contracts(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        if candidate.qty <= 0:
            return False, f"INVALID_QTY: qty={candidate.qty} must be >= 1"
        if candidate.qty > self.max_contracts_per_trade:
            return False, (
                f"MAX_CONTRACTS_EXCEEDED: qty={candidate.qty} > "
                f"max={self.max_contracts_per_trade}"
            )
        return True, ""

    def _check_daily_loss(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """Block if today's realised P&L is below the daily loss limit."""
        try:
            state = self.adapter.get_account_state()
        except Exception:
            return True, ""   # Can't check — allow through (non-fatal)

        if state.daily_pnl < -abs(self.max_daily_loss_usd):
            return False, (
                f"DAILY_LOSS_LIMIT: daily_pnl=${state.daily_pnl:+,.2f} "
                f"exceeds limit=${-self.max_daily_loss_usd:,.2f}"
            )
        return True, ""

    def _check_trailing_dd(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """
        Topstep-style trailing drawdown check.
        The trailing DD limit moves up with peak equity but never down.
        Once equity falls more than max_trailing_dd_usd below the peak, trading stops.
        """
        if self._peak_equity is None:
            return True, ""  # No peak recorded yet — allow through

        try:
            state = self.adapter.get_account_state()
        except Exception:
            return True, ""

        # Update peak if equity has risen
        self.update_peak_equity(state.equity)

        drawdown = self._peak_equity - state.equity
        if drawdown > self.max_trailing_dd_usd:
            return False, (
                f"TRAILING_DD_LIMIT: drawdown=${drawdown:,.2f} exceeds "
                f"limit=${self.max_trailing_dd_usd:,.2f} "
                f"(peak=${self._peak_equity:,.2f} current=${state.equity:,.2f})"
            )
        return True, ""

    def _check_news_window(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """Block orders during configured high-impact news windows."""
        now = datetime.now(timezone.utc)
        for window in self.news_windows:
            if window.is_active(now):
                return False, (
                    f"NEWS_WINDOW_BLOCK: '{window.name}' "
                    f"({window.start_utc}–{window.end_utc} UTC)"
                )
        return True, ""

    def _check_bar_freshness(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """Block if the most recent bar for this symbol is older than the threshold."""
        if candidate.bar_timestamp is None:
            return True, ""   # No timestamp provided — skip check

        now = datetime.now(timezone.utc)
        # Ensure bar_timestamp is tz-aware
        bar_ts = candidate.bar_timestamp
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)

        age_seconds = (now - bar_ts).total_seconds()
        if age_seconds > self.bar_freshness_seconds:
            return False, (
                f"STALE_DATA: last bar for {candidate.symbol} is "
                f"{age_seconds:.0f}s old (limit={self.bar_freshness_seconds}s)"
            )
        return True, ""

    def _check_strategy_halt(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """Block orders from strategies that have been halted."""
        if candidate.strategy_name and candidate.strategy_name in self.halted_strategies:
            return False, (
                f"STRATEGY_HALTED: '{candidate.strategy_name}' is on the halt list"
            )
        return True, ""

    def _check_partial_tp_qty(self, candidate: OrderCandidate) -> Tuple[bool, str]:
        """
        Guard against inadvertent partial TP attempts when qty == 1.
        With a single contract, there is no partial — the exit is always full.
        This check exists to surface any upstream code that might incorrectly
        try to send a partial-close order when only 1 contract is held.

        Note: this does NOT block the order — it raises ValueError to alert
        the programmer that something is wrong in the order-generation logic.
        """
        # Currently qty==1 is always valid; this is a placeholder for
        # fractional-contract guard logic if multi-contract support is added.
        # A partial-TP at qty==1 would be qty=0.5 which is invalid.
        if candidate.qty < 1:
            raise ValueError(
                f"OrderCandidate.qty={candidate.qty} is invalid. "
                f"Minimum is 1 contract. If you intended a partial TP, "
                f"note that partial TP requires qty >= 2."
            )
        return True, ""

    # ── Blocked-order logging ──────────────────────────────────────────────────

    def _block_order(
        self,
        candidate: OrderCandidate,
        reason:    str,
        rule_name: str,
    ) -> None:
        """Log a blocked order to the daily JSONL file."""
        today    = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = self.log_dir / f"blocked_orders_{today}.jsonl"

        entry = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "rule":          rule_name,
            "reason":        reason,
            "symbol":        candidate.symbol,
            "side":          candidate.side,
            "qty":           candidate.qty,
            "entry_price":   candidate.entry_price,
            "stop_price":    candidate.stop_price,
            "target_price":  candidate.target_price,
            "order_type":    candidate.order_type,
            "strategy_name": candidate.strategy_name,
            "signal_id":     candidate.signal_id,
        }

        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("Failed to write blocked-order log: %s", e)

        logger.warning(
            "ORDER BLOCKED [%s]: %s %s %s — %s",
            rule_name, candidate.side, candidate.qty, candidate.symbol, reason
        )
