"""
Donchian Channel Breakout (B2)
================================
THESIS: Trend following on daily ES bars. Price breaking above/below
        a rolling N-day high/low, confirmed by ADX (trend strength),
        captures multi-day directional moves.

SIGNAL:
  - Long:  Close breaks above Donchian upper (N-day high)
           AND ADX(14) > adx_threshold (strong trend)
  - Short: Close breaks below Donchian lower (N-day low)
           AND ADX(14) > adx_threshold

EXIT:
  - Trailing stop: exit if price closes back below opposite Donchian
    boundary (e.g., if long, exit if close < N-day low)
  - Timeout: max_hold_days trading days

TIMEFRAME: Daily bars (resampled from 1-min RTH data).

PARAM GRID: 2 x 2 = 4 combos.
  donchian_period: [20, 40] days
  adx_threshold:  [20, 25]

ACADEMIC BASIS:
  Donchian, R. (1960s). Original turtle trading rules.
  Covel, M. (2004). "Trend Following." FT Press.
  Hurst, B., Ooi, Y., Pedersen, L. (2017). "A Century of Evidence
  on Trend-Following Investing." AQR Capital.

NOTE: Daily trend following has very different characteristics than
      intraday mean-reversion. Expect: fewer trades (~20-40/yr),
      larger per-trade swings, regime-dependent (works in trending
      markets, loses in choppy ones). This is the right complement
      to FOMC drift which is regime-independent.
"""

from typing import Any, Dict, List, Optional
from datetime import timedelta

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> pd.Series:
    """Compute Average Directional Index (Wilder's method)."""
    # True Range
    prev_close = close.shift(1)
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close),
                               np.abs(low - prev_close)))

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    pos_dm = pd.Series(pos_dm, index=close.index)
    neg_dm = pd.Series(neg_dm, index=close.index)

    # Wilder smoothing
    atr_s = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    pos_di = 100 * pos_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s
    neg_di = 100 * neg_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s

    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    return adx


class DonchianBreakoutStrategy(BaseStrategy):

    name = "Donchian_Breakout"
    description = "Daily Donchian channel breakout with ADX trend filter"
    category = "trend"
    timeframe = "1D"
    version = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "donchian_period": [20, 40],
        "adx_threshold": [20, 25],
    }

    ADX_PERIOD = 14
    MAX_HOLD_DAYS = 30  # trailing stop usually exits before this

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"donchian_period": 20, "adx_threshold": 20}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        period = self.params["donchian_period"]
        adx_thresh = self.params["adx_threshold"]

        close = data["close"]
        high = data["high"]
        low = data["low"]

        # Donchian channels (causal: use shift to avoid current bar)
        # Break above yesterday's N-day high = signal
        upper = close.rolling(period, min_periods=period).max().shift(1)
        lower = close.rolling(period, min_periods=period).min().shift(1)

        # ADX
        adx = _compute_adx(high, low, close, self.ADX_PERIOD)

        signals = pd.Series(0, index=data.index)

        long_cond = (close > upper) & (adx > adx_thresh) & upper.notna()
        short_cond = (close < lower) & (adx > adx_thresh) & lower.notna()

        signals[long_cond] = 1
        signals[short_cond] = -1

        # Store channels for exit logic
        data["_donchian_upper"] = close.rolling(period, min_periods=period).max()
        data["_donchian_lower"] = close.rolling(period, min_periods=period).min()

        return signals

    def signals_to_trades(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        max_bars_per_trade: int = 78,
    ) -> List[Dict]:
        """
        Trailing stop exit: long exits when close < N-day low (opposite channel).
        Short exits when close > N-day high.
        Also: max hold days timeout.
        """
        period = self.params["donchian_period"]
        timeout = min(self.MAX_HOLD_DAYS, max_bars_per_trade)
        trades = []

        # Recompute channels if not already in data
        if "_donchian_upper" not in data.columns:
            data["_donchian_upper"] = data["close"].rolling(period, min_periods=period).max()
            data["_donchian_lower"] = data["close"].rolling(period, min_periods=period).min()

        upper = data["_donchian_upper"]
        lower = data["_donchian_lower"]
        close = data["close"]

        for idx in signals[signals != 0].index:
            try:
                entry_loc = data.index.get_loc(idx)
                if entry_loc + 1 >= len(data):
                    continue

                entry_bar = data.iloc[entry_loc + 1]
                # Enter at open of next day
                entry_price = float(entry_bar.get("open", entry_bar["close"]))
                entry_time = entry_bar.name
                direction = int(signals[idx])

                exit_price = None
                exit_time = None
                exit_type = "timeout"

                for i in range(1, timeout + 1):
                    if entry_loc + 1 + i >= len(data):
                        break

                    bar = data.iloc[entry_loc + 1 + i]
                    bar_close = float(bar["close"])
                    bar_upper = float(upper.iloc[entry_loc + 1 + i])
                    bar_lower = float(lower.iloc[entry_loc + 1 + i])

                    if np.isnan(bar_close):
                        continue

                    # Trailing stop: exit when price crosses opposite channel
                    if direction == 1 and not np.isnan(bar_lower):
                        if bar_close < bar_lower:
                            exit_price = bar_close
                            exit_time = bar.name
                            exit_type = "trailing_stop"
                            break
                    elif direction == -1 and not np.isnan(bar_upper):
                        if bar_close > bar_upper:
                            exit_price = bar_close
                            exit_time = bar.name
                            exit_type = "trailing_stop"
                            break

                if exit_price is None:
                    exit_index = entry_loc + 1 + timeout
                    if exit_index < len(data):
                        exit_bar = data.iloc[exit_index]
                        exit_price = float(exit_bar["close"])
                        exit_time = exit_bar.name
                        exit_type = "timeout"
                    else:
                        # Exit at last available bar
                        exit_bar = data.iloc[-1]
                        exit_price = float(exit_bar["close"])
                        exit_time = exit_bar.name
                        exit_type = "eod"

                gross_pnl = direction * (exit_price - entry_price)
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
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"] = pd.to_datetime(df["exit_time"])
        return df