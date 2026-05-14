"""
Account State
=============
Mutable record of the live account's current equity, drawdown,
and session statistics. Updated by RiskManager on every trade event.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AccountState:
    """
    Current state of a live trading account.

    All dollar values are in USD. Updated in-place by RiskManager.
    Clone with copy() before simulating hypothetical trades.
    """
    # Starting capital (never changes after init)
    account_size:          float

    # Equity tracking
    current_equity:        float = 0.0   # initialised from account_size in __post_init__
    peak_equity:           float = 0.0   # all-time high-water mark

    # Session (intraday) tracking
    session_date:          Optional[dt.date] = None
    session_open_equity:   float = 0.0   # equity at start of today's session
    session_realized_pnl:  float = 0.0   # cumulative net P&L this session (dollars)
    session_n_trades:      int   = 0
    session_consecutive_losses: int = 0
    session_max_loss_hit:  bool  = False  # True once daily loss limit breached
    session_locked:        bool  = False  # True → no new trades allowed this session

    # All-time statistics
    total_realized_pnl:    float = 0.0
    total_n_trades:        int   = 0
    consecutive_losses:    int   = 0      # running streak (resets on any win)

    # Open position
    in_trade:              bool  = False
    open_entry_price:      float = 0.0
    open_direction:        int   = 0      # +1 or -1
    open_instrument:       str   = ""
    open_n_contracts:      int   = 0
    open_point_value:      float = 0.0
    open_unrealised_pnl:   float = 0.0

    # Account kill
    account_killed:        bool  = False
    kill_reason:           str   = ""
    kill_timestamp:        Optional[dt.datetime] = None

    # Event log (for replay / auditing)
    events:                List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.current_equity == 0.0:
            self.current_equity = self.account_size
        if self.peak_equity == 0.0:
            self.peak_equity = self.account_size
        if self.session_open_equity == 0.0:
            self.session_open_equity = self.account_size

    # ── Computed properties ────────────────────────────────────────

    @property
    def trailing_drawdown(self) -> float:
        """Current trailing drawdown from all-time peak (dollars, positive = loss)."""
        return self.peak_equity - self.current_equity

    @property
    def session_drawdown(self) -> float:
        """Current drawdown from session open (dollars, positive = loss)."""
        return self.session_open_equity - (self.current_equity + self.open_unrealised_pnl)

    @property
    def unrealised_pnl_usd(self) -> float:
        """Open P&L in dollars (0 if flat)."""
        return self.open_unrealised_pnl

    @property
    def net_equity(self) -> float:
        """Equity including open unrealised P&L."""
        return self.current_equity + self.open_unrealised_pnl

    # ── Mutation helpers ───────────────────────────────────────────

    def update_unrealised(self, current_price: float):
        """Update open trade unrealised P&L from latest price."""
        if self.in_trade and self.open_n_contracts > 0:
            move = (current_price - self.open_entry_price) * self.open_direction
            self.open_unrealised_pnl = (
                move * self.open_point_value * self.open_n_contracts
            )

    def log(self, msg: str):
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:12]
        self.events.append(f"[{ts}] {msg}")

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot for logging."""
        return {
            "equity":          self.current_equity,
            "peak_equity":     self.peak_equity,
            "trailing_dd":     self.trailing_drawdown,
            "session_pnl":     self.session_realized_pnl,
            "total_trades":    self.total_n_trades,
            "consec_losses":   self.consecutive_losses,
            "in_trade":        self.in_trade,
            "locked":          self.session_locked,
            "killed":          self.account_killed,
            "kill_reason":     self.kill_reason,
        }

    def copy(self) -> "AccountState":
        """Shallow copy for simulation."""
        import copy
        return copy.copy(self)

    def __repr__(self) -> str:
        status = "KILLED" if self.account_killed else ("LOCKED" if self.session_locked else "ACTIVE")
        return (
            f"AccountState({status} equity=${self.current_equity:,.0f} "
            f"trail_dd=${self.trailing_drawdown:,.0f} "
            f"session_pnl=${self.session_realized_pnl:+,.0f})"
        )
