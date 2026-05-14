"""
Risk Events
============
Typed events emitted by RiskManager when rule thresholds are crossed.
Consumers (alerting, order management, logging) subscribe to these events
rather than polling account state.
"""

import datetime as dt
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional


class RiskEventType(Enum):
    # Session lifecycle
    SESSION_START          = auto()   # new trading day opened
    SESSION_LOCKED         = auto()   # daily loss limit hit; no new trades today
    SESSION_END            = auto()   # end-of-day, all positions should be flat

    # Trade lifecycle
    TRADE_APPROVED         = auto()   # can_trade() returned True
    TRADE_BLOCKED          = auto()   # can_trade() returned False; reason attached
    TRADE_OPENED           = auto()   # position opened
    TRADE_CLOSED           = auto()   # position closed; realised P&L attached

    # P&L alerts
    DAILY_LOSS_WARNING     = auto()   # approaching daily loss limit (80%)
    DAILY_LOSS_LIMIT_HIT   = auto()   # daily loss limit breached; session locked
    TRAILING_DD_WARNING    = auto()   # approaching trailing drawdown limit (80%)
    TRAILING_DD_LIMIT_HIT  = auto()   # trailing drawdown limit breached; account killed

    # Circuit breakers
    LOSS_RUN_CIRCUIT       = auto()   # consecutive-loss circuit breaker fired
    LOSS_RUN_CLEARED       = auto()   # consecutive-loss streak broken by a win

    # Account milestones
    PROFIT_TARGET_HIT      = auto()   # (evaluation mode) profit target reached
    NEW_EQUITY_PEAK        = auto()   # account reached a new all-time high

    # Errors
    RISK_ERROR             = auto()   # unexpected state; should never happen in production


@dataclass
class RiskEvent:
    """
    A single risk event emitted by RiskManager.

    Attributes:
        event_type:   What happened.
        timestamp:    When it happened.
        message:      Human-readable description.
        data:         Arbitrary payload for downstream consumers.
        equity:       Account equity at time of event.
        severity:     "INFO" | "WARN" | "CRITICAL"
    """
    event_type:  RiskEventType
    timestamp:   dt.datetime
    message:     str
    data:        Dict[str, Any]
    equity:      float
    severity:    str = "INFO"

    def __str__(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return f"[{self.severity}] {ts} {self.event_type.name}: {self.message}"

    def is_critical(self) -> bool:
        return self.severity == "CRITICAL"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.name,
            "timestamp":  self.timestamp.isoformat(),
            "message":    self.message,
            "data":       self.data,
            "equity":     self.equity,
            "severity":   self.severity,
        }


def _make_event(
    event_type: RiskEventType,
    message: str,
    equity: float,
    data: Optional[dict] = None,
    severity: str = "INFO",
) -> RiskEvent:
    return RiskEvent(
        event_type=event_type,
        timestamp=dt.datetime.now(),
        message=message,
        data=data or {},
        equity=equity,
        severity=severity,
    )
