"""
ES/NQ Pairs Trading Strategy
==============================
THESIS: ES and NQ are cointegrated. When their spread deviates beyond
        a Z-score threshold, it mean-reverts. This is a statistical
        arbitrage edge that is structurally different from directional
        signals — it profits from relative mispricing, not market direction.

SIGNAL:
  - Compute rolling OLS hedge ratio: ES = alpha + beta * NQ
  - Spread = ES_return - beta * NQ_return (normalized, not price)
  - Z-score = (spread - rolling_mean) / rolling_std
  - Long ES / Short NQ when Z < -entry_z  (ES cheap relative to NQ)
  - Short ES / Long NQ when Z > +entry_z  (ES expensive relative to NQ)
  - Exit when Z crosses exit_z toward zero

NOTE: This strategy trades the SPREAD between ES and NQ.
      In practice you'd trade MES and MNQ simultaneously.
      For backtesting purposes we test the ES leg only (the
      signal is valid; execution requires two instruments).

      The P&L here represents the ES leg gross return.
      A full pairs backtest would require simultaneous NQ data
      which our current framework handles as two separate instruments.
      We test the signal quality here — if it passes, implementation
      details (leg sizing, simultaneous fills) become the next step.

ACADEMIC BASIS:
  Gatev, Goetzmann, Rouwenhorst (2006). "Pairs Trading: Performance
  of a Relative Value Arbitrage Rule." Review of Financial Studies.

  Alexander, C. (2001). "Market Models." Wiley.

DATA: Requires both ES_1min.csv and NQ_1min.csv to be loaded
      and aligned. The strategy class receives pre-aligned data
      with both ES and NQ columns.

VARIANTS: 3 param combos.
  lookback: [30, 60] days — rolling window for hedge ratio + Z-score
  entry_z: [1.5, 2.0] — Z-score threshold to enter
  exit_z: fixed at 0.5 — exit when spread reverts toward zero

METHOD: Walk-forward (rolling, 1-year train / 1-year test).
        Param grid: 2 x 2 = 4 combos.

TIMEFRAME: 1-hour bars (not 5-min — pairs signals need more data
           per observation to be meaningful).
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from src.strategies.base import BaseStrategy


class ESNQPairsStrategy(BaseStrategy):

    name = "ESNQ_Pairs"
    description = "ES/NQ cointegration spread mean-reversion"
    category = "stat_arb"
    timeframe = "1h"
    version = "1.0"
    max_trades_per_day = 2

    param_grid = {
        "lookback_days": [30, 60],
        "entry_z": [1.5, 2.0],
    }

    EXIT_Z = 0.5          # exit when spread reverts to this Z-score
    STOP_Z = 3.5          # stop loss if spread widens to this Z-score
    TIMEOUT_BARS = 48     # max hold: 48 hours on 1-hr bars

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"lookback_days": 30, "entry_z": 1.5}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        Expects data to have columns: close_es, close_nq (aligned).
        Returns signal series: 1 = long ES leg, -1 = short ES leg, 0 = flat.
        """
        if "close_es" not in data.columns or "close_nq" not in data.columns:
            return pd.Series(0, index=data.index)

        lookback = self.params["lookback_days"] * 7  # ~7 1-hr bars per trading day
        entry_z = self.params["entry_z"]

        # Log returns (more stationary than prices)
        es_ret = np.log(data["close_es"]).diff()
        nq_ret = np.log(data["close_nq"]).diff()

        # Rolling OLS hedge ratio (causal — using only past data)
        beta = es_ret.rolling(lookback, min_periods=lookback // 2).apply(
            lambda x: _rolling_beta(x, nq_ret.loc[x.index]),
            raw=False
        )

        # Spread = ES return - beta * NQ return
        spread = es_ret - beta * nq_ret

        # Rolling Z-score of spread
        spread_mean = spread.rolling(lookback, min_periods=lookback // 2).mean()
        spread_std = spread.rolling(lookback, min_periods=lookback // 2).std()
        z_score = (spread - spread_mean) / spread_std.replace(0, np.nan)

        # Store z_score for use in signals_to_trades
        data["_z_score"] = z_score
        data["_beta"] = beta

        # Signals: enter when Z crosses threshold
        signals = pd.Series(0, index=data.index)

        # Long ES (ES cheap vs NQ): Z crosses below -entry_z
        long_cond = (z_score.shift(1) >= -entry_z) & (z_score < -entry_z)
        # Short ES (ES expensive vs NQ): Z crosses above +entry_z
        short_cond = (z_score.shift(1) <= entry_z) & (z_score > entry_z)

        signals[long_cond] = 1
        signals[short_cond] = -1

        return signals

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        """
        Exit logic based on Z-score reversion, not price targets.
        - Exit when Z-score crosses EXIT_Z toward zero
        - Stop when Z-score reaches STOP_Z (spread widened further)
        - Timeout at TIMEOUT_BARS
        """
        entry_z = self.params["entry_z"]
        timeout_bars = min(self.TIMEOUT_BARS, max_bars_per_trade)
        trades = []

        if "_z_score" not in data.columns:
            # Re-run generate_signals to populate z_score
            signals = self.generate_signals(data)
            if "_z_score" not in data.columns:
                return trades

        z_score = data["_z_score"]

        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["close_es"]
                entry_time = entry_bar.name
                direction = int(signals[idx])
                entry_z_val = float(z_score.iloc[entry_loc])

                if np.isnan(entry_price) or np.isnan(entry_z_val):
                    continue

                exit_price = None
                exit_time = None
                exit_type = "timeout"

                for i in range(1, timeout_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break

                    future_bar = data.iloc[entry_loc + 1 + i]
                    future_z = float(z_score.iloc[entry_loc + 1 + i])

                    if np.isnan(future_z):
                        continue

                    # Exit: Z-score reverts past EXIT_Z toward zero
                    if direction == 1:  # long ES, expecting Z to rise (ES to outperform)
                        if future_z >= -self.EXIT_Z:
                            exit_price = float(future_bar["close_es"])
                            exit_time = future_bar.name
                            exit_type = "target"
                            break
                        # Stop: Z fell further (ES got even cheaper vs NQ — wrong)
                        if future_z <= -self.STOP_Z:
                            exit_price = float(future_bar["close_es"])
                            exit_time = future_bar.name
                            exit_type = "stop"
                            break
                    else:  # short ES, expecting Z to fall
                        if future_z <= self.EXIT_Z:
                            exit_price = float(future_bar["close_es"])
                            exit_time = future_bar.name
                            exit_type = "target"
                            break
                        if future_z >= self.STOP_Z:
                            exit_price = float(future_bar["close_es"])
                            exit_time = future_bar.name
                            exit_type = "stop"
                            break

                if exit_price is None:
                    exit_index = entry_loc + 1 + timeout_bars
                    if exit_index < len(data):
                        exit_bar = data.iloc[exit_index]
                        exit_price = float(exit_bar["close_es"])
                        exit_time = exit_bar.name
                        exit_type = "timeout"
                    else:
                        continue

                gross_pnl = direction * (exit_price - entry_price)
                trades.append({
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": exit_time,
                    "exit_price": exit_price,
                    "direction": direction,
                    "exit_type": exit_type,
                    "gross_pnl": gross_pnl,
                    "entry_z": entry_z_val,
                })
            except Exception:
                continue

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df


def _rolling_beta(es_window: pd.Series, nq_window: pd.Series) -> float:
    """OLS beta of ES returns on NQ returns over a rolling window."""
    try:
        aligned = pd.concat([es_window, nq_window], axis=1).dropna()
        if len(aligned) < 10:
            return 1.0
        slope, _, _, _, _ = stats.linregress(aligned.iloc[:, 1], aligned.iloc[:, 0])
        return float(slope)
    except Exception:
        return 1.0