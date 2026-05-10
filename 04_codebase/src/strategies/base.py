"""
Strategy Interface
===================
Abstract base class for all trading strategies in the research factory.

Every strategy must:
  1. Accept a params dict in __init__
  2. Implement generate_signals() returning entry/exit signals
  3. Define param_grid for walk-forward optimization
  4. Define metadata (name, description, holding period, etc.)

The walk-forward engine calls:
  strategy = MyStrategy(params)
  signals = strategy.generate_signals(data)
  trades = strategy.signals_to_trades(data, signals)

Strategies return GROSS P&L. Costs are applied by the cost model,
not by the strategy. This separation ensures consistent cost treatment.

Usage:
    class RSIMeanRev(BaseStrategy):
        name = "RSI Mean Reversion"
        
        def generate_signals(self, data):
            ...
            return signals_df
        
        @property
        def param_grid(self):
            return {
                "rsi_oversold": [25, 30],
                "rsi_overbought": [70, 75],
                "rr_ratio": [1.0, 1.5],
            }
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
import data_schema as S

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Types of trading signals a strategy can produce."""
    LONG = 1
    SHORT = -1
    FLAT = 0


class ExitReason(Enum):
    """Why a trade was exited."""
    TARGET = "target"
    STOP = "stop"
    TIME = "time"
    SIGNAL = "signal"       # opposite signal triggered
    EOD = "eod"             # end of day (intraday strategies)
    MANUAL = "manual"


@dataclass
class Trade:
    """
    Immutable record of a single completed trade.
    All P&L in points (gross — before costs).
    """
    entry_time: Any         # timestamp or date
    exit_time: Any
    entry_price: float
    exit_price: float
    direction: int          # +1 long, -1 short
    gross_pnl: float        # points, before costs
    exit_reason: str
    bars_held: int = 0
    atr_at_entry: float = 0.0
    # Metadata for analysis
    session_date: Any = None
    signal_strength: float = 0.0  # optional confidence score


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement:
      - generate_signals(): produce entry/exit signals from data
      - param_grid (property): define parameter space for optimization

    Subclasses may override:
      - signals_to_trades(): convert signals to Trade objects
      - name, description, min_holding_bars, max_trades_per_day
    """

    # ── Class-level metadata (override in subclass) ────────────
    name: str = "BaseStrategy"
    description: str = ""
    version: str = "1.0"
    min_holding_bars: int = 1       # minimum bars to hold (prop firm compliance)
    max_trades_per_day: int = 1     # limit overtrading
    timeframe: str = "5min"         # primary operating timeframe
    category: str = "uncategorized" # mean_reversion, trend, calendar, etc.

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        Args:
            params: Strategy parameters. Keys must match param_grid keys.
        """
        self.params = params or {}
        self._validate_params()

    def _validate_params(self):
        """Ensure all required params are present."""
        grid = self.param_grid
        for key in grid:
            if key not in self.params:
                # Use first value from grid as default
                self.params[key] = grid[key][0]
                logger.debug(f"  {self.name}: defaulting {key}={self.params[key]}")

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals from OHLCV + features data.

        Args:
            data: DataFrame with OHLCV columns + any features from the pipeline.
                  Index is timestamp (for intraday) or date (for daily).

        Returns:
            DataFrame with columns:
              - signal: int in {-1, 0, 1} (short, flat, long)
              - entry_price: float (NaN if no signal)
              - stop_price: float (NaN if no signal)
              - target_price: float (NaN if no signal)
              - signal_strength: float [0, 1] optional confidence
            Index must match input data index.
        """
        pass

    @property
    @abstractmethod
    def param_grid(self) -> Dict[str, List[Any]]:
        """
        Parameter grid for walk-forward optimization.
        Each key maps to a list of values to search.
        Keep grids SMALL (3-5 values per param, max 2-3 params)
        to avoid DSR penalty from excessive trials.

        Returns:
            Dict like {"param_name": [val1, val2, val3]}
        """
        pass

    # ── Trade simulation ───────────────────────────────────────
    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.DataFrame,
        max_bars: int = 78,  # default: full RTH session in 5-min bars
    ) -> List[Trade]:
        """
        Convert signal DataFrame to list of Trade objects.
        Simulates entries and exits bar-by-bar.

        Default implementation handles:
          - Entry at signal bar's close
          - Exit at target, stop, or time limit
          - One trade per day (configurable)
          - End-of-day forced exit for intraday strategies

        Subclasses can override for custom exit logic.

        Args:
            data: Original OHLCV data.
            signals: Output from generate_signals().
            max_bars: Maximum bars to hold before time stop.

        Returns:
            List of Trade objects with gross P&L.
        """
        trades = []
        dates_traded_today = {}  # session_date -> count

        # Ensure signals has a name before joining
        if hasattr(signals, 'name') and signals.name is None:
            signals = signals.rename('signal')
        # Merge data and signals
        merged = data.join(signals, rsuffix="_sig")
        if "signal" not in merged.columns:
            return trades

        i = 0
        while i < len(merged) - 1:
            row = merged.iloc[i]
            signal = row.get("signal", 0)

            if signal == 0 or np.isnan(signal):
                i += 1
                continue

            direction = int(signal)
            session = row.get(S.SESSION_DATE, None)

            # Enforce max trades per day
            if session is not None:
                day_count = dates_traded_today.get(session, 0)
                if day_count >= self.max_trades_per_day:
                    i += 1
                    continue

            entry_price = row.get("entry_price", row[S.CLOSE])
            stop_price = row.get("stop_price", np.nan)
            target_price = row.get("target_price", np.nan)
            atr = row.get(S.ATR, 0.0)
            strength = row.get("signal_strength", 0.0)

            if np.isnan(entry_price):
                i += 1
                continue

            # Simulate forward
            exit_price = None
            exit_reason = None
            bars_held = 0

            for j in range(i + 1, min(i + 1 + max_bars, len(merged))):
                future = merged.iloc[j]
                bars_held += 1

                # Check if we've crossed into a new session (EOD exit)
                future_session = future.get(S.SESSION_DATE, None)
                if session is not None and future_session != session:
                    # Exit at previous bar's close (EOD)
                    prev = merged.iloc[j - 1]
                    exit_price = prev[S.CLOSE]
                    exit_reason = ExitReason.EOD.value
                    bars_held -= 1
                    break

                # Check stop
                if not np.isnan(stop_price):
                    if direction == 1 and future[S.LOW] <= stop_price:
                        exit_price = stop_price
                        exit_reason = ExitReason.STOP.value
                        break
                    elif direction == -1 and future[S.HIGH] >= stop_price:
                        exit_price = stop_price
                        exit_reason = ExitReason.STOP.value
                        break

                # Check target
                if not np.isnan(target_price):
                    if direction == 1 and future[S.HIGH] >= target_price:
                        exit_price = target_price
                        exit_reason = ExitReason.TARGET.value
                        break
                    elif direction == -1 and future[S.LOW] <= target_price:
                        exit_price = target_price
                        exit_reason = ExitReason.TARGET.value
                        break

            # Time stop if no exit yet
            if exit_price is None:
                if bars_held > 0:
                    exit_bar = merged.iloc[min(i + bars_held, len(merged) - 1)]
                    exit_price = exit_bar[S.CLOSE]
                    exit_reason = ExitReason.TIME.value
                else:
                    i += 1
                    continue

            gross_pnl = direction * (exit_price - entry_price)
            entry_time = merged.index[i]
            exit_idx = min(i + bars_held, len(merged) - 1)
            exit_time = merged.index[exit_idx]

            trade = Trade(
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                direction=direction,
                gross_pnl=gross_pnl,
                exit_reason=exit_reason,
                bars_held=bars_held,
                atr_at_entry=atr if not np.isnan(atr) else 0.0,
                session_date=session,
                signal_strength=strength if not np.isnan(strength) else 0.0,
            )
            trades.append(trade)

            # Track trades per day
            if session is not None:
                dates_traded_today[session] = dates_traded_today.get(session, 0) + 1

            # Skip past the exit bar
            i = exit_idx + 1
            continue

        return trades

    # ── Utility ────────────────────────────────────────────────
    def trades_to_dataframe(self, trades: List[Trade]) -> pd.DataFrame:
        """Convert list of Trade objects to DataFrame for analysis."""
        if not trades:
            return pd.DataFrame()

        records = []
        for t in trades:
            records.append({
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "direction": t.direction,
                "gross_pnl": t.gross_pnl,
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
                "atr": t.atr_at_entry,
                "session_date": t.session_date,
                "signal_strength": t.signal_strength,
            })

        return pd.DataFrame(records)

    @property
    def n_param_combinations(self) -> int:
        """Total number of parameter combinations in grid."""
        n = 1
        for values in self.param_grid.values():
            n *= len(values)
        return n

    def __repr__(self) -> str:
        return f"{self.name}(params={self.params})"