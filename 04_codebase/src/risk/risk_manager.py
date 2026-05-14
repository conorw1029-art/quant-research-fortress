"""
Risk Manager
============
Central risk-enforcement layer for live trading.
All trading decisions go through can_trade() before an order is sent.
All trade events (open, close, tick) flow through the update methods.

Design:
  - Immutable config (RiskConfig), mutable state (AccountState)
  - Emits RiskEvent objects for downstream alerting / logging
  - Never touches order routing — it only approves/blocks and sizes
  - Compatible with both live trading and Topstep-replay simulation

Usage:
    from src.risk import RiskManager, RiskConfig

    cfg = RiskConfig(account_size=25_000, max_daily_loss_usd=1_500, ...)
    rm  = RiskManager(cfg)

    rm.on_session_start("2026-01-15")

    # Before placing an order:
    ok, reason = rm.can_trade("MES")
    if ok:
        n = rm.size_position("MES", stop_distance_pts=4.0)
        # ... send order ...
        rm.on_trade_open(entry_price=5200.0, direction=1, instrument="MES",
                         n_contracts=n, point_value=5.0)

    # On fill:
    rm.on_trade_close(exit_price=5210.0, instrument="MES")
"""

import datetime as dt
import logging
from typing import Callable, List, Optional, Tuple

from .account_state import AccountState
from .position_sizer import PositionSizer
from .risk_config import RiskConfig
from .risk_events import RiskEvent, RiskEventType, _make_event

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Stateful risk manager for a single trading account.

    Args:
        config:             Immutable rule specification.
        instrument_specs:   Optional dict of InstrumentSpec for Kelly/fractional sizing.
        event_handlers:     Optional list of callables(RiskEvent) for real-time alerts.
    """

    def __init__(
        self,
        config: RiskConfig,
        instrument_specs: Optional[dict] = None,
        event_handlers: Optional[List[Callable[[RiskEvent], None]]] = None,
    ):
        self.config = config
        self.state = AccountState(account_size=config.account_size)
        self.sizer = PositionSizer(config, instrument_specs)
        self._handlers: List[Callable[[RiskEvent], None]] = event_handlers or []
        self._last_trade_time: Optional[dt.datetime] = None

    # ── Event system ───────────────────────────────────────────────

    def _emit(self, event: RiskEvent):
        logger.info(str(event))
        for handler in self._handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    def add_handler(self, handler: Callable[[RiskEvent], None]):
        self._handlers.append(handler)

    # ── Session lifecycle ──────────────────────────────────────────

    def on_session_start(self, date=None):
        """
        Call at the start of each trading session.
        Resets intraday tracking while preserving cumulative equity.
        """
        today = date or dt.date.today()
        st = self.state
        st.session_date = today
        st.session_open_equity = st.current_equity
        st.session_realized_pnl = 0.0
        st.session_n_trades = 0
        st.session_consecutive_losses = 0
        st.session_max_loss_hit = False
        st.session_locked = False
        st.log(f"Session opened: {today} equity=${st.current_equity:,.0f}")
        self._emit(_make_event(
            RiskEventType.SESSION_START,
            f"Session {today} opened. Equity=${st.current_equity:,.0f}  "
            f"Trail_DD=${st.trailing_drawdown:,.0f}",
            st.current_equity,
            {"date": str(today)},
        ))

    def on_session_end(self):
        """Call at end-of-day. Flattens any open position and resets session."""
        st = self.state
        if st.in_trade:
            logger.warning("Session end with open position — forcing close at last price")
            self._close_trade_internal(pnl_pts=0.0, pnl_usd=0.0, note="EOD force-close")
        self._emit(_make_event(
            RiskEventType.SESSION_END,
            f"Session {st.session_date} ended. "
            f"P&L=${st.session_realized_pnl:+,.0f}  n={st.session_n_trades}",
            st.current_equity,
        ))

    # ── Gate check ─────────────────────────────────────────────────

    def can_trade(
        self,
        instrument: str = "",
        signal_direction: int = 0,
    ) -> Tuple[bool, str]:
        """
        Check whether a new trade is allowed right now.

        Returns (True, "") if approved, or (False, reason_str) if blocked.
        Does NOT consume the approval — caller must also call on_trade_open.
        """
        st = self.state
        cfg = self.config

        # Hard blocks
        if st.account_killed:
            return False, f"account_killed: {st.kill_reason}"
        if st.session_locked:
            return False, "session_locked: daily loss limit reached"
        if st.in_trade and not cfg.allow_pyramiding:
            return False, "in_trade: no pyramiding allowed"

        # Trailing drawdown check (real-time)
        if st.trailing_drawdown >= cfg.max_trailing_drawdown_usd:
            self._kill_account(f"trailing_drawdown=${st.trailing_drawdown:,.0f}")
            return False, f"trailing_dd_breached: ${st.trailing_drawdown:,.0f}"

        # Daily loss check
        net_session = st.session_realized_pnl + st.open_unrealised_pnl
        if net_session <= -cfg.max_daily_loss_usd:
            self._lock_session(f"daily_loss=${abs(net_session):,.0f}")
            return False, f"daily_loss_breached: ${abs(net_session):,.0f}"

        # Warn at 80% of limits
        daily_pct = abs(net_session) / cfg.max_daily_loss_usd if cfg.max_daily_loss_usd > 0 else 0
        if daily_pct >= 0.80 and not st.session_max_loss_hit:
            self._emit(_make_event(
                RiskEventType.DAILY_LOSS_WARNING,
                f"Daily loss at {daily_pct*100:.0f}% of limit (${abs(net_session):,.0f})",
                st.current_equity, severity="WARN",
            ))

        trail_pct = st.trailing_drawdown / cfg.max_trailing_drawdown_usd if cfg.max_trailing_drawdown_usd > 0 else 0
        if trail_pct >= 0.80:
            self._emit(_make_event(
                RiskEventType.TRAILING_DD_WARNING,
                f"Trailing DD at {trail_pct*100:.0f}% of limit (${st.trailing_drawdown:,.0f})",
                st.current_equity, severity="WARN",
            ))

        # Circuit breaker: consecutive losses
        if (cfg.circuit_breaker_on_loss_run
                and st.consecutive_losses >= cfg.max_consecutive_losses):
            self._emit(_make_event(
                RiskEventType.LOSS_RUN_CIRCUIT,
                f"Circuit breaker: {st.consecutive_losses} consecutive losses",
                st.current_equity, severity="WARN",
            ))
            return False, f"loss_run_circuit: {st.consecutive_losses} consecutive losses"

        # Spacing check
        if cfg.min_trade_spacing_minutes > 0 and self._last_trade_time:
            elapsed = (dt.datetime.now() - self._last_trade_time).total_seconds() / 60
            if elapsed < cfg.min_trade_spacing_minutes:
                return False, f"too_soon: {elapsed:.1f}min < {cfg.min_trade_spacing_minutes}min minimum"

        self._emit(_make_event(
            RiskEventType.TRADE_APPROVED,
            f"Trade approved: {instrument} dir={signal_direction:+d}",
            st.current_equity,
        ))
        return True, ""

    # ── Trade events ───────────────────────────────────────────────

    def on_trade_open(
        self,
        entry_price: float,
        direction: int,
        instrument: str,
        n_contracts: int = 1,
        point_value: float = 5.0,
    ):
        """Record that a position was opened."""
        st = self.state
        st.in_trade = True
        st.open_entry_price = entry_price
        st.open_direction = direction
        st.open_instrument = instrument
        st.open_n_contracts = n_contracts
        st.open_point_value = point_value
        st.open_unrealised_pnl = 0.0
        self._last_trade_time = dt.datetime.now()
        st.log(f"OPEN {instrument} {'+' if direction>0 else '-'}{n_contracts} @{entry_price}")
        self._emit(_make_event(
            RiskEventType.TRADE_OPENED,
            f"Opened {instrument} dir={direction:+d} n={n_contracts} @{entry_price:.4f}",
            st.current_equity,
            {"instrument": instrument, "direction": direction, "n": n_contracts, "price": entry_price},
        ))

    def on_trade_close(
        self,
        exit_price: float,
        instrument: str,
        cost_pts: float = 0.0,
    ):
        """
        Record that the open position was closed.

        Args:
            exit_price: Fill price.
            instrument: Must match the open position's instrument.
            cost_pts:   Round-trip transaction cost in points.
        """
        st = self.state
        if not st.in_trade:
            logger.warning("on_trade_close called with no open position")
            return

        gross_pts = (exit_price - st.open_entry_price) * st.open_direction
        net_pts   = gross_pts - cost_pts
        net_usd   = net_pts * st.open_point_value * st.open_n_contracts

        self._close_trade_internal(net_pts, net_usd, note=f"close @{exit_price:.4f}")

        self._emit(_make_event(
            RiskEventType.TRADE_CLOSED,
            f"Closed {instrument} @{exit_price:.4f}  net=${net_usd:+,.0f}",
            st.current_equity,
            {"instrument": instrument, "gross_pts": gross_pts,
             "net_pts": net_pts, "net_usd": net_usd},
        ))

    def on_price_update(self, current_price: float):
        """Update unrealised P&L for open position. Call on each bar."""
        self.state.update_unrealised(current_price)

    # ── Sizing ─────────────────────────────────────────────────────

    def size_position(
        self,
        instrument: str,
        stop_distance_pts: Optional[float] = None,
        atr_pts: Optional[float] = None,
        win_rate: Optional[float] = None,
        avg_win_pts: Optional[float] = None,
        avg_loss_pts: Optional[float] = None,
    ) -> int:
        """Return the recommended number of contracts. Already capped at config max."""
        return self.sizer.size(
            instrument=instrument,
            equity=self.state.current_equity,
            stop_distance_pts=stop_distance_pts,
            atr_pts=atr_pts,
            win_rate=win_rate,
            avg_win_pts=avg_win_pts,
            avg_loss_pts=avg_loss_pts,
        )

    # ── State accessors ────────────────────────────────────────────

    def get_state(self) -> AccountState:
        return self.state

    def get_snapshot(self) -> dict:
        return self.state.snapshot()

    def get_events(self) -> List[RiskEvent]:
        return list(self._events_log)

    # ── Internal helpers ───────────────────────────────────────────

    def _close_trade_internal(self, pnl_pts: float, pnl_usd: float, note: str = ""):
        st = self.state
        cfg = self.config

        # Update equity
        st.current_equity += pnl_usd
        st.open_unrealised_pnl = 0.0

        # Update peak
        prev_peak = st.peak_equity
        if st.current_equity > st.peak_equity:
            st.peak_equity = st.current_equity
            self._emit(_make_event(
                RiskEventType.NEW_EQUITY_PEAK,
                f"New equity peak: ${st.peak_equity:,.0f}",
                st.current_equity,
            ))

        # Update session stats
        st.session_realized_pnl += pnl_usd
        st.session_n_trades += 1
        st.total_realized_pnl += pnl_usd
        st.total_n_trades += 1

        # Streak tracking
        if pnl_usd < 0:
            st.consecutive_losses += 1
            st.session_consecutive_losses += 1
        else:
            if st.consecutive_losses > 0:
                self._emit(_make_event(
                    RiskEventType.LOSS_RUN_CLEARED,
                    f"Loss streak of {st.consecutive_losses} broken by win",
                    st.current_equity,
                ))
            st.consecutive_losses = 0
            st.session_consecutive_losses = 0

        # Flat position
        st.in_trade = False
        st.open_entry_price = 0.0
        st.open_direction = 0
        st.open_instrument = ""
        st.open_n_contracts = 0
        st.open_point_value = 0.0

        st.log(f"CLOSE {note}  pnl=${pnl_usd:+,.0f}  equity=${st.current_equity:,.0f}")

        # Post-close rule checks
        self._check_rules_post_close()

    def _check_rules_post_close(self):
        st = self.state
        cfg = self.config

        # Trailing drawdown
        if st.trailing_drawdown >= cfg.max_trailing_drawdown_usd:
            self._kill_account(
                f"trailing_drawdown=${st.trailing_drawdown:,.0f} "
                f">= limit=${cfg.max_trailing_drawdown_usd:,.0f}"
            )

        # Daily loss
        elif st.session_realized_pnl <= -cfg.max_daily_loss_usd:
            self._lock_session(
                f"session_pnl=${st.session_realized_pnl:,.0f} "
                f"<= -limit=${cfg.max_daily_loss_usd:,.0f}"
            )

        # Profit target (evaluation mode)
        elif (cfg.profit_target_usd > 0
              and st.total_realized_pnl >= cfg.profit_target_usd):
            self._emit(_make_event(
                RiskEventType.PROFIT_TARGET_HIT,
                f"Profit target ${cfg.profit_target_usd:,.0f} reached! "
                f"Total P&L=${st.total_realized_pnl:,.0f}",
                st.current_equity, severity="INFO",
            ))

    def _lock_session(self, reason: str):
        st = self.state
        if st.session_locked:
            return
        st.session_locked = True
        st.session_max_loss_hit = True
        st.log(f"SESSION LOCKED: {reason}")
        self._emit(_make_event(
            RiskEventType.DAILY_LOSS_LIMIT_HIT,
            f"Session locked: {reason}",
            st.current_equity,
            {"reason": reason},
            severity="CRITICAL",
        ))

    def _kill_account(self, reason: str):
        st = self.state
        if st.account_killed:
            return
        st.account_killed = True
        st.session_locked = True
        st.kill_reason = reason
        st.kill_timestamp = dt.datetime.now()
        st.log(f"ACCOUNT KILLED: {reason}")
        self._emit(_make_event(
            RiskEventType.TRAILING_DD_LIMIT_HIT,
            f"Account killed: {reason}",
            st.current_equity,
            {"reason": reason},
            severity="CRITICAL",
        ))

    def __repr__(self) -> str:
        return f"RiskManager(config={self.config}, state={self.state})"
