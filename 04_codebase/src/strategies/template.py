"""
Strategy Template
==================
Copy this file to src/strategies/YOUR_STRATEGY.py and modify.
Then add an entry to src/zoo/registry.py with matching module_path.

Checklist for a new strategy:
  [ ] Rename class from TemplateStrategy to YourStrategyName
  [ ] Set name, category, timeframe, description
  [ ] Define param_grid (keep small: 2-3 params, 3-5 values each)
  [ ] Implement generate_signals()
  [ ] Optionally override signals_to_trades() for custom exits
  [ ] Add to _STRATEGIES list in registry.py
  [ ] Run: python run_strategy.py --key your_key

Common gotchas:
  - All features must be CAUSAL (use .shift(), never .rolling(center=True))
  - Return signals at the bar where decision is made; trades enter next bar
  - Keep param grids small to avoid DSR penalty
  - Document your thesis in the docstring — future you will thank you
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class TemplateStrategy(BaseStrategy):
    """
    THESIS: [Write your 1-2 sentence thesis here. Why should this work?]

    SIGNAL: [Describe the entry rule]

    EXIT:   [Describe stop, target, timeout]

    ACADEMIC BASIS: [Citation or reference, if any]

    NOTE: [Known limitations, regime dependencies, etc.]
    """

    # ── Class metadata (override these) ─────────────────────────
    name = "Template"
    description = "Replace with your strategy description"
    category = "uncategorized"   # mean_reversion, trend, calendar, volume, etc.
    timeframe = "5min"           # 5min, 15min, 1h, 1D
    version = "1.0"
    min_holding_bars = 1
    max_trades_per_day = 1

    # ── Parameter grid ──────────────────────────────────────────
    # Keep small! DSR penalizes for total combos. 2-3 params × 3-4 values each.
    param_grid = {
        "example_param_1": [10, 20, 30],
        "example_param_2": [1.0, 1.5, 2.0],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        # Set defaults if params not provided
        self.params = params or {
            "example_param_1": 20,
            "example_param_2": 1.5,
        }

    # ══════════════════════════════════════════════════════════
    # REQUIRED: generate_signals()
    # ══════════════════════════════════════════════════════════
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        Return a Series of signals aligned to data.index:
           1 = long entry at next bar's open
          -1 = short entry at next bar's open
           0 = no action

        Available columns (when pipeline features are applied):
          open, high, low, close, volume, session_date,
          prior_close, atr, daily_range, session_high, session_low,
          session_vwap, session_vwap_std, rsi, volume_avg

        IMPORTANT: Use only CAUSAL data — everything is automatically
        causal if you use .shift(), iloc, etc. Do NOT use .rolling(center=True)
        or any forward-looking transformations.
        """
        # Example: signal when some_indicator crosses above threshold
        p1 = self.params["example_param_1"]
        p2 = self.params["example_param_2"]

        # Placeholder logic — REPLACE with real signal
        signals = pd.Series(0, index=data.index)

        # Example template:
        # condition_long = (data["rsi"].shift(1) >= p1) & (data["rsi"] < p1)
        # condition_short = (data["rsi"].shift(1) <= p2) & (data["rsi"] > p2)
        # signals[condition_long] = 1
        # signals[condition_short] = -1

        return signals

    # ══════════════════════════════════════════════════════════
    # OPTIONAL: signals_to_trades()
    # ══════════════════════════════════════════════════════════
    # If the default exit logic (target, stop, timeout) in BaseStrategy
    # suits you, you don't need to override this. Otherwise, here's
    # a template that gives you full control.
    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        """
        Return list of trade dicts. Each dict MUST have:
            entry_time, entry_price, exit_time, exit_price,
            direction, exit_type, gross_pnl

        Costs are applied later by the cost_model — return GROSS pnl only.
        """
        target_mult = self.params["example_param_2"]  # example: target multiplier
        stop_mult = 1.5  # example: stop multiplier (fixed)
        timeout_bars = min(12, max_bars_per_trade)  # example: 60-min timeout on 5-min bars
        trades = []

        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                # Enter at next bar's open
                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time = entry_bar.name
                direction = int(signals[idx])

                # Use ATR for target/stop sizing
                atr_val = data["atr"].loc[idx] if "atr" in data.columns else 0.0
                if np.isnan(atr_val) or atr_val == 0:
                    continue

                target_pts = target_mult * atr_val
                stop_pts = stop_mult * atr_val

                if direction == 1:
                    target_price = entry_price + target_pts
                    stop_loss = entry_price - stop_pts
                else:
                    target_price = entry_price - target_pts
                    stop_loss = entry_price + stop_pts

                # Scan forward for exit
                exit_price = None
                exit_time = None
                exit_type = "timeout"

                for i in range(1, timeout_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar = data.iloc[entry_loc + 1 + i]
                    if direction == 1:
                        if bar["low"] <= stop_loss:
                            exit_price = stop_loss
                            exit_time = bar.name
                            exit_type = "stop"
                            break
                        if bar["high"] >= target_price:
                            exit_price = target_price
                            exit_time = bar.name
                            exit_type = "target"
                            break
                    else:
                        if bar["high"] >= stop_loss:
                            exit_price = stop_loss
                            exit_time = bar.name
                            exit_type = "stop"
                            break
                        if bar["low"] <= target_price:
                            exit_price = target_price
                            exit_time = bar.name
                            exit_type = "target"
                            break

                if exit_price is None:
                    exit_index = entry_loc + 1 + timeout_bars
                    if exit_index < len(data):
                        exit_bar = data.iloc[exit_index]
                        exit_price = exit_bar["close"]
                        exit_time = exit_bar.name
                        exit_type = "timeout"
                    else:
                        continue

                gross_pnl = (exit_price - entry_price) * direction
                trades.append({
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": exit_time,
                    "exit_price": exit_price,
                    "direction": direction,
                    "exit_type": exit_type,
                    "gross_pnl": gross_pnl,
                })
            except Exception:
                continue

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        """Convert trade list to DataFrame — default implementation."""
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df