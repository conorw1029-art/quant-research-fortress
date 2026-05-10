"""
Transaction Cost Model
=======================
Institutional-grade cost modeling for futures backtesting.

Supports:
  - Per-instrument commission + slippage (ES, MES, NQ, MNQ)
  - Optimistic vs conservative slippage scenarios
  - Volatility-adjusted slippage (optional: scales with ATR percentile)
  - Detailed cost breakdown per trade

Design:
  - Injected into the walk-forward engine — strategies never calculate their own costs.
  - All costs expressed in POINTS (not dollars) for instrument-agnostic P&L tracking.
  - Dollar conversion happens at reporting layer via instrument.point_value.

Usage:
    from src.backtesting.cost_model import TransactionCost
    from src.data.data_schema import MES

    cost = TransactionCost(instrument=MES, slippage_scenario="conservative")
    cost_pts = cost.cost_per_rt()          # fixed cost per round-trip
    net_pnl = cost.apply(gross_pnl=2.5)   # 2.5 - cost
    trades = cost.apply_to_trades(trades)  # batch: adds cost_pts, net_pnl columns
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
import data_schema as S

logger = logging.getLogger(__name__)


class SlippageScenario(Enum):
    """Slippage assumptions for backtesting."""
    ZERO = "zero"                # Commission only. Unrealistic but useful as upper bound.
    OPTIMISTIC = "optimistic"    # 0.5 tick per side (0.25 pts RT). Limit orders, liquid hours.
    REALISTIC = "realistic"      # 1 tick per side (0.50 pts RT). Market orders, normal conditions.
    CONSERVATIVE = "conservative"  # 2 ticks per side (1.0 pts RT). Fast markets, wide spreads.


# Slippage in ticks per side for each scenario
_SLIPPAGE_TICKS: Dict[SlippageScenario, float] = {
    SlippageScenario.ZERO: 0.0,
    SlippageScenario.OPTIMISTIC: 0.5,
    SlippageScenario.REALISTIC: 1.0,
    SlippageScenario.CONSERVATIVE: 2.0,
}


@dataclass
class TransactionCost:
    """
    Transaction cost calculator for futures instruments.

    All costs are in POINTS for the given instrument.
    Multiply by instrument.point_value to get dollars.

    Attributes:
        instrument: InstrumentSpec from data_schema.
        slippage_scenario: How much slippage to assume.
        volatility_adjusted: If True, scale slippage by ATR percentile.
        vol_base_atr: Baseline ATR for volatility scaling (set from training data).
        vol_multiplier_cap: Max multiplier for volatility-adjusted slippage.
    """
    instrument: S.InstrumentSpec = field(default_factory=lambda: S.MES)
    slippage_scenario: str = "realistic"
    volatility_adjusted: bool = False
    vol_base_atr: float = 0.0
    vol_multiplier_cap: float = 3.0

    def __post_init__(self):
        if isinstance(self.slippage_scenario, str):
            self.slippage_scenario = SlippageScenario(self.slippage_scenario)

    # ── Core cost calculation ──────────────────────────────────
    def commission_per_rt_pts(self) -> float:
        """Round-trip commission in points."""
        return (2 * self.instrument.commission_per_side) / self.instrument.point_value

    def slippage_per_rt_pts(self, current_atr: Optional[float] = None) -> float:
        """
        Round-trip slippage in points.
        If volatility_adjusted=True and current_atr provided, scales slippage.
        """
        base_ticks = _SLIPPAGE_TICKS[self.slippage_scenario]
        base_pts = 2 * base_ticks * self.instrument.tick_size

        if self.volatility_adjusted and current_atr and self.vol_base_atr > 0:
            vol_ratio = current_atr / self.vol_base_atr
            vol_mult = min(vol_ratio, self.vol_multiplier_cap)
            return base_pts * vol_mult

        return base_pts

    def cost_per_rt(self, current_atr: Optional[float] = None) -> float:
        """Total round-trip cost in points."""
        return self.commission_per_rt_pts() + self.slippage_per_rt_pts(current_atr)

    def cost_per_rt_dollars(self, current_atr: Optional[float] = None) -> float:
        """Total round-trip cost in dollars."""
        return self.cost_per_rt(current_atr) * self.instrument.point_value

    # ── Apply to trades ────────────────────────────────────────
    def apply(
        self,
        gross_pnl: float,
        current_atr: Optional[float] = None,
    ) -> float:
        """Apply cost to a single trade's gross P&L. Returns net P&L in points."""
        return gross_pnl - self.cost_per_rt(current_atr)

    def apply_to_trades(
        self,
        trades: pd.DataFrame,
        gross_pnl_col: str = "gross_pnl",
        atr_col: Optional[str] = "atr",
    ) -> pd.DataFrame:
        """
        Apply costs to a DataFrame of trades.
        Adds columns: cost_pts, net_pnl, cost_dollars.

        Args:
            trades: Must have gross_pnl_col. Optionally atr_col for vol-adjusted.
            gross_pnl_col: Column name for gross P&L in points.
            atr_col: Column name for ATR (used if volatility_adjusted=True).

        Returns:
            DataFrame with cost columns added (copy).
        """
        out = trades.copy()

        if self.volatility_adjusted and atr_col in out.columns:
            out["cost_pts"] = out[atr_col].apply(
                lambda a: self.cost_per_rt(current_atr=a)
            )
        else:
            out["cost_pts"] = self.cost_per_rt()

        out["net_pnl"] = out[gross_pnl_col] - out["cost_pts"]
        out["cost_dollars"] = out["cost_pts"] * self.instrument.point_value

        return out

    # ── Reporting ──────────────────────────────────────────────
    def summary(self) -> str:
        """Human-readable cost summary."""
        lines = [
            f"TransactionCost({self.instrument.symbol})",
            f"  Commission/RT:  {self.commission_per_rt_pts():.4f} pts"
            f" (${2 * self.instrument.commission_per_side:.2f})",
            f"  Slippage/RT:    {self.slippage_per_rt_pts():.4f} pts"
            f" ({self.slippage_scenario.value})",
            f"  Total/RT:       {self.cost_per_rt():.4f} pts"
            f" (${self.cost_per_rt_dollars():.2f})",
            f"  Vol-adjusted:   {self.volatility_adjusted}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"TransactionCost({self.instrument.symbol}, "
                f"{self.slippage_scenario.value}, "
                f"{self.cost_per_rt():.3f}pts/RT)")


# ── Factory functions for common configurations ────────────────
def mes_optimistic() -> TransactionCost:
    """MES with commission only (0.25 pts/RT). Our original 0.52 figure."""
    return TransactionCost(instrument=S.MES, slippage_scenario="zero")

def mes_realistic() -> TransactionCost:
    """MES with 1-tick slippage per side (0.75 pts/RT)."""
    return TransactionCost(instrument=S.MES, slippage_scenario="realistic")

def mes_conservative() -> TransactionCost:
    """MES with 2-tick slippage per side (1.25 pts/RT)."""
    return TransactionCost(instrument=S.MES, slippage_scenario="conservative")

def es_realistic() -> TransactionCost:
    """ES with 1-tick slippage per side (1.10 pts/RT)."""
    return TransactionCost(instrument=S.ES, slippage_scenario="realistic")

def es_conservative() -> TransactionCost:
    """ES with 2-tick slippage per side (1.60 pts/RT)."""
    return TransactionCost(instrument=S.ES, slippage_scenario="conservative")


# ── Cost comparison table ──────────────────────────────────────
def print_cost_comparison():
    """Print cost comparison across all instruments and scenarios."""
    print(f"\n{'='*70}")
    print(f"  TRANSACTION COST COMPARISON (points per round-trip)")
    print(f"{'='*70}")
    print(f"  {'Instrument':<12s} {'Scenario':<16s} {'Commission':>12s}"
          f" {'Slippage':>10s} {'Total':>10s} {'Dollars':>10s}")
    print(f"  {'-'*70}")

    for inst in [S.MES, S.ES, S.NQ, S.MNQ]:
        for scenario in SlippageScenario:
            tc = TransactionCost(instrument=inst, slippage_scenario=scenario.value)
            print(f"  {inst.symbol:<12s} {scenario.value:<16s}"
                  f" {tc.commission_per_rt_pts():>12.4f}"
                  f" {tc.slippage_per_rt_pts():>10.4f}"
                  f" {tc.cost_per_rt():>10.4f}"
                  f" {tc.cost_per_rt_dollars():>10.2f}")
        print()