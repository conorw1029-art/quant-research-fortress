"""
Risk Configuration
==================
All account-level risk rules in one immutable dataclass.
Designed around Topstep Trading Combine / Funded Trader parameters
but parameterisable for any prop-firm structure.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class RiskConfig:
    """
    Immutable risk rule specification.

    Topstep $25K defaults are the baseline; override for other accounts.

    Args:
        account_size:               Starting balance in dollars.
        max_daily_loss_usd:         Max intraday loss before session is locked.
        max_trailing_drawdown_usd:  Max drawdown from equity peak; breach = account killed.
        profit_target_usd:          (Evaluation phase only) target to pass the combine.
        max_position_contracts:     Hard ceiling on open contracts per instrument.
        max_open_positions:         Total simultaneous open positions.
        sizing_method:              "fixed" | "fractional" | "kelly"
        fixed_contracts:            Contracts per trade when sizing_method="fixed".
        risk_fraction:              Fraction of equity to risk per trade (fractional/kelly).
        kelly_half:                 If True, use 50% Kelly (conservative).
        max_consecutive_losses:     Pause trading after N consecutive losses.
        circuit_breaker_on_loss_run: Enforce the consecutive-loss pause.
        allow_pyramiding:           Whether to add to a winning position.
        min_trade_spacing_minutes:  Minimum minutes between consecutive signals.
        topstep_mode:               Enforce Topstep intraday position-reset rule
                                    (must flatten before daily loss limit).
    """
    # Account
    account_size:                  float = 25_000.0
    max_daily_loss_usd:            float = 1_500.0
    max_trailing_drawdown_usd:     float = 2_000.0
    profit_target_usd:             float = 1_500.0   # for evaluation phase

    # Position limits
    max_position_contracts:        int   = 1
    max_open_positions:            int   = 1
    allow_pyramiding:              bool  = False

    # Sizing
    sizing_method:                 str   = "fixed"    # "fixed" | "fractional" | "kelly"
    fixed_contracts:               int   = 1
    risk_fraction:                 float = 0.01       # 1% of equity per trade
    kelly_half:                    bool  = True        # half-Kelly reduces overbetting

    # Circuit breakers
    max_consecutive_losses:        int   = 5
    circuit_breaker_on_loss_run:   bool  = True
    min_trade_spacing_minutes:     int   = 0           # 0 = no minimum

    # Prop-firm mode
    topstep_mode:                  bool  = True

    def daily_loss_floor(self, peak_equity: float) -> float:
        """Daily equity floor = today's open equity - max_daily_loss_usd."""
        return peak_equity - self.max_daily_loss_usd

    def trailing_dd_floor(self, peak_equity: float) -> float:
        """Trailing DD floor = all-time peak equity - max_trailing_drawdown_usd."""
        return peak_equity - self.max_trailing_drawdown_usd

    def __repr__(self) -> str:
        return (
            f"RiskConfig(account=${self.account_size:,.0f}, "
            f"daily_loss=${self.max_daily_loss_usd:,.0f}, "
            f"trail_dd=${self.max_trailing_drawdown_usd:,.0f}, "
            f"sizing={self.sizing_method}[{self.fixed_contracts}])"
        )
