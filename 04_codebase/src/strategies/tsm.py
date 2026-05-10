"""
Time-Series Momentum (TSM)
==========================
Replicates Baltas-Kosowski (2017) "Demystifying Time-Series Momentum Strategies"
and Moskowitz-Ooi-Pedersen (2012) "Time series momentum".

Signal rule: sign of past N-month return
Position sizing: inverse volatility scaling to target_vol annualized
Holding: 1 month (re-evaluate monthly)

Lookback variants: 1m (21d), 3m (63d), 6m (126d), 12m (252d)

Why this should work on our data:
  - Donchian breakout on CL is already a survivor — that strategy IS time-series
    momentum in disguise. Generalizing the rule across all 19 markets with
    proper vol scaling should yield more survivors.
  - Academically: TSM explains ~75% of CTA returns (Baltas-Kosowski 2013).
  - Diversification: 19 markets, 4 lookbacks = 76 independent signals.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.strategies.base import BaseStrategy


class TimeSeriesMomentumStrategy(BaseStrategy):
    """
    Daily-frequency time-series momentum.

    Parameters
    ----------
    lookback_days : int
        Sign rule lookback window. 21 (1m), 63 (3m), 126 (6m), 252 (12m).
    vol_lookback_days : int
        Window for ex-ante volatility estimate (default 60 = ~3 months).
    target_vol_annual : float
        Annualized target volatility for each position (default 0.40 per Baltas-Kosowski).
    rebalance_freq : str
        'M' = monthly, 'W' = weekly. Default 'M'.
    """
    name = "Time_Series_Momentum"
    category = "trend"
    timeframe = "1D"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "lookback_days": [21, 63, 126, 252],
    }

    # Fixed params (do NOT tune these — overfitting risk)
    VOL_LOOKBACK = 60
    TARGET_VOL_ANNUAL = 0.40
    REBALANCE_FREQ = "ME"
    TRADING_DAYS_PER_YEAR = 252

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"lookback_days": 252}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        lookback = self.params["lookback_days"]
        close = data["close"]

        daily_close = close.resample("1D").last().ffill().dropna()
        if len(daily_close) < lookback + self.VOL_LOOKBACK:
            return pd.Series(0, index=data.index)

        past_return = daily_close / daily_close.shift(lookback) - 1.0
        sign = np.sign(past_return)

        rebalance_dates = (
            daily_close.resample(self.REBALANCE_FREQ).last().index
        )

        daily_signals = pd.Series(0, index=daily_close.index, dtype=int)
        for rd in rebalance_dates:
            if rd in sign.index and not pd.isna(sign.loc[rd]):
                daily_signals.loc[rd] = int(sign.loc[rd])

        signals = pd.Series(0, index=data.index, dtype=int)
        session_dates = pd.Series(data.index.date, index=data.index)

        for rd in daily_signals[daily_signals != 0].index:
            mask = session_dates == rd.date()
            if mask.any():
                first_bar_idx = data.index[mask][0]
                signals.loc[first_bar_idx] = int(daily_signals.loc[rd])

        return signals

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 23 * 60 * 22,
    ) -> List[Dict[str, Any]]:
        daily_close = data["close"].resample("1D").last().ffill().dropna()
        daily_returns = daily_close.pct_change()
        daily_vol = daily_returns.rolling(self.VOL_LOOKBACK, min_periods=20).std()
        annual_vol = daily_vol * np.sqrt(self.TRADING_DAYS_PER_YEAR)
        size_factor = (self.TARGET_VOL_ANNUAL / annual_vol).clip(upper=5.0)

        trades: List[Dict[str, Any]] = []
        active_signals = signals[signals != 0]

        for i, entry_idx in enumerate(active_signals.index):
            try:
                entry_loc = data.index.get_loc(entry_idx)
                if entry_loc + 1 >= len(data):
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time = entry_bar.name
                direction = int(active_signals.loc[entry_idx])

                if i + 1 < len(active_signals):
                    exit_signal_idx = active_signals.index[i + 1]
                    exit_loc = data.index.get_loc(exit_signal_idx)
                else:
                    exit_loc = len(data) - 1

                exit_loc = min(exit_loc, entry_loc + max_bars_per_trade)
                if exit_loc <= entry_loc:
                    continue

                exit_bar = data.iloc[exit_loc]
                exit_price = exit_bar["open"] if "open" in exit_bar else exit_bar["close"]
                exit_time = exit_bar.name

                entry_date = entry_time.date() if hasattr(entry_time, "date") else None
                size = 1.0
                if entry_date is not None:
                    daily_idx = pd.Timestamp(entry_date)
                    if daily_idx in size_factor.index:
                        sf = size_factor.loc[daily_idx]
                        if not pd.isna(sf) and sf > 0:
                            size = float(sf)

                gross_pnl = (exit_price - entry_price) * direction * size

                trades.append({
                    "entry_time":  entry_time,
                    "entry_price": entry_price,
                    "exit_time":   exit_time,
                    "exit_price":  exit_price,
                    "direction":   direction,
                    "exit_type":   "rebalance",
                    "gross_pnl":   gross_pnl,
                    "size_factor": size,
                })
            except Exception:
                continue

        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
