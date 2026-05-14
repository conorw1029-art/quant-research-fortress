"""
Position Sizer
==============
Computes the number of contracts to trade given account state,
instrument specs, and signal characteristics.

Three sizing methods:
  fixed       — always trade N contracts (good for evaluation accounts)
  fractional  — risk risk_fraction of equity per trade, sized by stop distance
  kelly       — full/half Kelly based on historical win rate and P&L ratio
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Stateless position sizer.

    Args:
        config:    RiskConfig with sizing parameters.
        instrument_specs:  Optional dict mapping instrument -> InstrumentSpec.
    """

    def __init__(self, config, instrument_specs: Optional[dict] = None):
        self.config = config
        self.instrument_specs = instrument_specs or {}

    def size(
        self,
        instrument: str,
        equity: float,
        stop_distance_pts: Optional[float] = None,
        win_rate: Optional[float] = None,
        avg_win_pts: Optional[float] = None,
        avg_loss_pts: Optional[float] = None,
        atr_pts: Optional[float] = None,
    ) -> int:
        """
        Return the number of contracts to trade.

        Args:
            instrument:          Instrument symbol.
            equity:              Current account equity (dollars).
            stop_distance_pts:   Distance to stop in points (for fractional sizing).
            win_rate:            Historical win rate [0,1] (for Kelly).
            avg_win_pts:         Historical average win in points (for Kelly).
            avg_loss_pts:        Historical average loss in points (for Kelly).
            atr_pts:             Current ATR in points (fallback stop for fractional).

        Returns:
            Number of contracts (minimum 1, capped at config.max_position_contracts).
        """
        cfg = self.config
        method = cfg.sizing_method

        if method == "fixed":
            n = cfg.fixed_contracts

        elif method == "fractional":
            n = self._fractional_size(instrument, equity, stop_distance_pts, atr_pts)

        elif method == "kelly":
            n = self._kelly_size(instrument, equity, win_rate, avg_win_pts, avg_loss_pts)

        else:
            logger.warning(f"Unknown sizing method '{method}', defaulting to fixed={cfg.fixed_contracts}")
            n = cfg.fixed_contracts

        n = max(1, min(n, cfg.max_position_contracts))
        return int(n)

    def _fractional_size(
        self,
        instrument: str,
        equity: float,
        stop_pts: Optional[float],
        atr_pts: Optional[float],
    ) -> int:
        """
        Risk risk_fraction of equity. Stop = stop_pts (or 1.5×ATR as fallback).
        n_contracts = floor(risk_usd / (stop_pts × point_value))
        """
        spec = self.instrument_specs.get(instrument)
        if spec is None:
            return self.config.fixed_contracts

        point_value = spec.point_value
        risk_usd = equity * self.config.risk_fraction

        # Determine stop distance
        if stop_pts and stop_pts > 0:
            stop = stop_pts
        elif atr_pts and atr_pts > 0:
            stop = 1.5 * atr_pts
        else:
            return self.config.fixed_contracts

        risk_per_contract = stop * point_value
        if risk_per_contract <= 0:
            return self.config.fixed_contracts

        n = math.floor(risk_usd / risk_per_contract)
        return max(1, n)

    def _kelly_size(
        self,
        instrument: str,
        equity: float,
        win_rate: Optional[float],
        avg_win_pts: Optional[float],
        avg_loss_pts: Optional[float],
    ) -> int:
        """
        Kelly criterion: f* = (b*p - q) / b
        where b = avg_win/avg_loss, p = win_rate, q = 1-p.

        Uses half-Kelly if config.kelly_half is True.
        Sizes by: n = floor(kelly_fraction * equity / (avg_loss * point_value))
        """
        spec = self.instrument_specs.get(instrument)
        if spec is None:
            return self.config.fixed_contracts

        # Need win_rate, avg_win, avg_loss to compute Kelly
        if not all([win_rate, avg_win_pts, avg_loss_pts]):
            return self.config.fixed_contracts
        if avg_loss_pts <= 0 or avg_win_pts <= 0:
            return self.config.fixed_contracts

        p = win_rate
        q = 1.0 - p
        b = avg_win_pts / avg_loss_pts
        kelly_f = (b * p - q) / b

        if kelly_f <= 0:
            return 1

        if self.config.kelly_half:
            kelly_f *= 0.5

        kelly_f = min(kelly_f, 0.25)  # never bet more than 25% of equity

        risk_usd = kelly_f * equity
        risk_per_contract = avg_loss_pts * spec.point_value
        if risk_per_contract <= 0:
            return self.config.fixed_contracts

        n = math.floor(risk_usd / risk_per_contract)
        return max(1, n)
