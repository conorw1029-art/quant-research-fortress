"""
Risk Management Module
=======================
Live-trading guard layer between strategy signals and order execution.

Components:
  - RiskConfig     — account rules (daily loss, trailing DD, sizing)
  - AccountState   — mutable equity/drawdown tracking
  - PositionSizer  — contracts per trade (fixed, Kelly, fractional)
  - RiskEvent      — typed events emitted when rules fire
  - RiskManager    — orchestrator: checks, sizes, updates state

Usage:
    from src.risk import RiskManager, RiskConfig

    cfg = RiskConfig(
        account_size=25_000,
        max_daily_loss_usd=1_500,
        max_trailing_drawdown_usd=2_000,
    )
    rm = RiskManager(cfg)
    rm.on_session_start("2026-01-15")

    if rm.can_trade():
        n = rm.size_position("MES", atr_pts=12.5)
        # ... place order ...
        rm.on_trade_open(entry_price=5200.0, direction=1, instrument="MES")
        rm.on_trade_close(exit_price=5205.0, instrument="MES")
"""

from .risk_config import RiskConfig
from .account_state import AccountState
from .position_sizer import PositionSizer
from .risk_events import RiskEvent, RiskEventType
from .risk_manager import RiskManager

__all__ = [
    "RiskConfig",
    "AccountState",
    "PositionSizer",
    "RiskEvent",
    "RiskEventType",
    "RiskManager",
]
