"""
Bollinger RSI + ADX Regime Filter — M6E rescue
================================================
THESIS: bollinger_rsi_fxe posted DSR=+11.25 at realistic costs but was
killed at conservative costs (PF collapsed from 1.465 → 1.153). Root
cause: 9,453 trades × extra $0.0002/trade = ~$1.9 pts total cost drag.
Fix: add ADX(14) < threshold regime filter to suppress signals on
trending days where mean-reversion fails. This should cut trade count
by 30-40% while preserving the profitable range-bound alpha.

SIGNAL (same as BollingerRSI, with ADX gate):
  - Low-ADX (range-bound) only: ADX(14) < adx_threshold
  - Long:  bar touches or breaches BB lower AND RSI < rsi_extreme
  - Short: bar touches or breaches BB upper AND RSI > (100-rsi_extreme)

EXIT:
  - Target: BB midline
  - Stop: 1.5 × ATR from entry
  - Timeout: 12 bars

PARAM GRID: 2 × 2 × 2 = 8 combos
  bb_period:     [20, 50]
  rsi_extreme:   [25, 30]
  adx_threshold: [20, 25]
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close),
                               np.abs(low - prev_close)))
    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=close.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=close.index)
    alpha  = 1.0 / period
    atr_s  = pd.Series(tr).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    pos_di = 100 * pos_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
    neg_di = 100 * neg_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
    dx     = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()


class BollingerRSIADXStrategy(BaseStrategy):

    name        = "Bollinger_RSI_ADX"
    description = "BB+RSI mean-reversion with ADX range-bound filter"
    category    = "mean_reversion"
    timeframe   = "5min"
    version     = "1.0"
    max_trades_per_day = 2

    BB_STD       = 2.0
    STOP_ATR     = 1.5
    TIMEOUT_BARS = 12
    ADX_PERIOD   = 14

    param_grid = {
        "bb_period":     [20, 50],
        "rsi_extreme":   [25, 30],
        "adx_threshold": [20, 25],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"bb_period": 20, "rsi_extreme": 30, "adx_threshold": 25}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        bb_period     = self.params["bb_period"]
        rsi_extreme   = self.params["rsi_extreme"]
        adx_threshold = self.params["adx_threshold"]
        rsi_high      = 100 - rsi_extreme

        close = data["close"]
        rsi   = data["rsi"] if "rsi" in data.columns else pd.Series(50.0, index=data.index)

        bb_mid   = close.rolling(bb_period, min_periods=bb_period).mean()
        bb_std   = close.rolling(bb_period, min_periods=bb_period).std()
        bb_upper = bb_mid + self.BB_STD * bb_std
        bb_lower = bb_mid - self.BB_STD * bb_std

        adx = _compute_adx(data["high"], data["low"], close, self.ADX_PERIOD)
        range_bound = adx < adx_threshold

        signals = pd.Series(0, index=data.index)
        long_cond  = (data["low"] <= bb_lower) & (rsi < rsi_extreme)  & bb_lower.notna() & range_bound
        short_cond = (data["high"] >= bb_upper) & (rsi > rsi_high)    & bb_upper.notna() & range_bound
        signals[long_cond]  =  1
        signals[short_cond] = -1
        return signals

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        timeout_bars = min(self.TIMEOUT_BARS, max_bars_per_trade)
        bb_period    = self.params["bb_period"]
        bb_mid       = data["close"].rolling(bb_period, min_periods=bb_period).mean()

        trades = []
        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue
                entry_bar   = data.iloc[entry_loc + 1]
                entry_price = entry_bar["open"]
                entry_time  = entry_bar.name
                direction   = int(signals[idx])

                atr_val = data["atr"].loc[idx]
                if np.isnan(atr_val) or atr_val == 0:
                    continue
                stop_pts  = self.STOP_ATR * atr_val
                stop_loss = entry_price - direction * stop_pts

                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, timeout_bars + 1):
                    if entry_loc + 1 + i >= len(data):
                        break
                    bar         = data.iloc[entry_loc + 1 + i]
                    current_mid = bb_mid.iloc[entry_loc + 1 + i]

                    if direction == 1 and bar["low"] <= stop_loss:
                        exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break
                    elif direction == -1 and bar["high"] >= stop_loss:
                        exit_price = stop_loss; exit_time = bar.name; exit_type = "stop"; break

                    if not np.isnan(current_mid):
                        if direction == 1 and bar["high"] >= current_mid:
                            exit_price = current_mid; exit_time = bar.name; exit_type = "target"; break
                        elif direction == -1 and bar["low"] <= current_mid:
                            exit_price = current_mid; exit_time = bar.name; exit_type = "target"; break

                if exit_price is None:
                    exit_index = entry_loc + 1 + timeout_bars
                    if exit_index < len(data):
                        exit_bar   = data.iloc[exit_index]
                        exit_price = exit_bar["close"]
                        exit_time  = exit_bar.name
                        exit_type  = "timeout"
                    else:
                        continue

                trades.append({
                    "entry_time":  entry_time,  "entry_price": entry_price,
                    "exit_time":   exit_time,   "exit_price":  exit_price,
                    "direction":   direction,    "exit_type":   exit_type,
                    "gross_pnl":   (exit_price - entry_price) * direction,
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
