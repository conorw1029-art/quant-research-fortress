"""
Batch 2 Strategies — Remaining OHLCV-Based
=============================================
NR7 breakout, gap fill, Fibonacci retracement, initial balance fade,
volume-weighted MACD, Connors 2-period RSI.
"""

from typing import Any, Dict, List, Optional
from datetime import time

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class NR7Strategy(BaseStrategy):
    """
    THESIS: NR7 (narrowest range of last 7 bars on daily) precedes
    range expansion. Breakout of the NR7 bar's range is directional.
    Academic: Toby Crabel's "Day Trading with Short-Term Price Patterns".
    """

    name = "NR7_Breakout"
    description = "Narrowest range of last 7 days -> breakout"
    category = "level_breakout"
    timeframe = "1D"
    max_trades_per_day = 1

    param_grid = {
        "lookback": [4, 7],       # NR4 or NR7
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.0, 1.5],
        "timeout_bars": [3, 5],   # days
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "lookback": 7,
            "stop_atr_mult": 1.5,
            "rr_ratio": 1.5,
            "timeout_bars": 5,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)
        lookback = self.params["lookback"]

        h = data["high"].values
        l = data["low"].values
        c = data["close"].values
        ranges = h - l

        for i in range(lookback, len(data)):
            window = ranges[i - lookback + 1 : i + 1]
            # Current bar is the narrowest in the window
            if ranges[i] == window.min() and ranges[i] > 0:
                # Signal: breakout direction from next bar
                # Use close vs midpoint as directional hint
                midpoint = (h[i] + l[i]) / 2
                if c[i] > midpoint:
                    signals.iloc[i] = 1
                else:
                    signals.iloc[i] = -1

        return signals


class GapFillStrategy(BaseStrategy):
    """
    THESIS: Overnight gaps (open vs prior close) tend to fill during
    the session. Fade the gap direction.
    H1 on SPY daily REJECTED. Re-test on intraday data across all markets.
    """

    name = "Gap_Fill_Intraday"
    description = "Fade overnight gap toward prior close"
    category = "mean_reversion"
    timeframe = "5min"
    max_trades_per_day = 1

    param_grid = {
        "min_gap_atr": [0.25, 0.5],    # minimum gap size in ATR
        "max_gap_atr": [1.5, 3.0],     # maximum (huge gaps don't fill)
        "stop_atr_mult": [1.0, 1.5],
        "timeout_bars": [12, 24, 48],  # 1-4 hours at 5min
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "min_gap_atr": 0.25,
            "max_gap_atr": 1.5,
            "stop_atr_mult": 1.5,
            "timeout_bars": 24,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        if "session_date" not in data.columns or "atr" not in data.columns:
            return signals
        if "prior_close" not in data.columns:
            return signals

        min_gap = self.params["min_gap_atr"]
        max_gap = self.params["max_gap_atr"]

        for session_date, group in data.groupby("session_date"):
            if len(group) < 3:
                continue

            first_bar = group.iloc[0]
            session_open = first_bar["open"]
            prior_close = first_bar.get("prior_close", np.nan)
            atr = first_bar.get("atr", np.nan)

            if np.isnan(prior_close) or np.isnan(atr) or atr <= 0:
                continue

            gap = session_open - prior_close
            gap_atr = abs(gap) / atr

            if gap_atr < min_gap or gap_atr > max_gap:
                continue

            # Signal on the first bar of the session
            # Fade = trade opposite to gap direction
            if gap > 0:
                signals.loc[group.index[0]] = -1  # Gap up → short (fade)
            else:
                signals.loc[group.index[0]] = 1   # Gap down → long (fade)

        return signals


class FibRetracementStrategy(BaseStrategy):
    """
    THESIS: Price retracing to 38.2% or 61.8% of prior day's range
    finds support/resistance and reverses.

    SIGNAL: Compute prior day high/low. Calculate fib levels.
    If price touches 61.8% retracement and reverses → enter.
    """

    name = "Fib_Retracement"
    description = "Enter at Fibonacci retracement levels of prior day range"
    category = "level_reaction"
    timeframe = "5min"
    max_trades_per_day = 1

    param_grid = {
        "fib_level": [0.382, 0.500, 0.618],
        "confirmation_bars": [1, 2],
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.0, 1.5, 2.0],
        "timeout_bars": [12, 24],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "fib_level": 0.618,
            "confirmation_bars": 1,
            "stop_atr_mult": 1.5,
            "rr_ratio": 1.5,
            "timeout_bars": 24,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        if "session_date" not in data.columns:
            return signals

        fib = self.params["fib_level"]
        sessions = sorted(data["session_date"].unique())

        for i in range(1, len(sessions)):
            prev_session = sessions[i - 1]
            curr_session = sessions[i]

            prev_data = data[data["session_date"] == prev_session]
            curr_data = data[data["session_date"] == curr_session]

            if len(prev_data) < 5 or len(curr_data) < 5:
                continue

            prev_high = prev_data["high"].max()
            prev_low = prev_data["low"].min()
            prev_range = prev_high - prev_low

            if prev_range <= 0:
                continue

            # Fib levels for bullish retracement (from high)
            fib_support = prev_high - fib * prev_range
            # Fib levels for bearish retracement (from low)
            fib_resistance = prev_low + fib * prev_range

            signaled = False
            for idx in curr_data.index:
                if signaled:
                    break
                bar = curr_data.loc[idx]
                if bar.name.time() >= time(15, 30):
                    break

                # Bullish: price touches fib support from above and bounces
                if bar["low"] <= fib_support and bar["close"] > fib_support:
                    signals.loc[idx] = 1
                    signaled = True

                # Bearish: price touches fib resistance from below and rejects
                elif bar["high"] >= fib_resistance and bar["close"] < fib_resistance:
                    signals.loc[idx] = -1
                    signaled = True

        return signals


class IBFadeStrategy(BaseStrategy):
    """
    THESIS: Initial Balance (first hour of RTH) defines the session range.
    When IB is narrow (low volatility), price tends to break out then
    revert. Fade the false breakout.

    SIGNAL: If IB range < 0.5 * ATR and price breaks IB extreme,
    fade the breakout (mean-reversion).
    """

    name = "IB_Fade"
    description = "Fade initial balance extremes when range is narrow"
    category = "mean_reversion"
    timeframe = "5min"
    max_trades_per_day = 1

    param_grid = {
        "ib_narrow_threshold": [0.5, 0.75],  # fraction of ATR
        "stop_atr_mult": [1.0, 1.5],
        "rr_ratio": [1.0, 1.5],
        "timeout_bars": [12, 24],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "ib_narrow_threshold": 0.5,
            "stop_atr_mult": 1.5,
            "rr_ratio": 1.5,
            "timeout_bars": 24,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        if "session_date" not in data.columns or "atr" not in data.columns:
            return signals

        ib_end = time(10, 30)  # First hour: 09:30-10:30
        threshold = self.params["ib_narrow_threshold"]

        for session_date, group in data.groupby("session_date"):
            if len(group) < 10:
                continue

            ib_bars = group[group.index.time <= ib_end]
            post_ib = group[group.index.time > ib_end]

            if len(ib_bars) < 2 or len(post_ib) < 2:
                continue

            ib_high = ib_bars["high"].max()
            ib_low = ib_bars["low"].min()
            ib_range = ib_high - ib_low
            atr = ib_bars["atr"].iloc[-1] if "atr" in ib_bars.columns else 0

            if atr <= 0:
                continue

            # Only trade narrow IB days
            if ib_range > threshold * atr:
                continue

            signaled = False
            for idx in post_ib.index:
                if signaled:
                    break
                bar = post_ib.loc[idx]
                if bar.name.time() >= time(15, 30):
                    break

                # Fade breakout above IB high
                if bar["high"] > ib_high and bar["close"] < ib_high:
                    signals.loc[idx] = -1
                    signaled = True

                # Fade breakout below IB low
                elif bar["low"] < ib_low and bar["close"] > ib_low:
                    signals.loc[idx] = 1
                    signaled = True

        return signals


class VolMACDStrategy(BaseStrategy):
    """
    THESIS: MACD weighted by relative volume gives momentum signals
    that reflect institutional participation. High-volume MACD crosses
    are more reliable.

    SIGNAL: MACD line crosses signal line AND current volume > 1.5x avg.
    """

    name = "Vol_MACD"
    description = "Volume-weighted MACD momentum"
    category = "momentum"
    timeframe = "15min"
    max_trades_per_day = 1

    param_grid = {
        "fast_period": [8, 12],
        "slow_period": [21, 26],
        "signal_period": [7, 9],
        "vol_multiplier": [1.0, 1.5],
        "stop_atr_mult": [1.5, 2.0],
        "timeout_bars": [8, 16],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "vol_multiplier": 1.5,
            "stop_atr_mult": 1.5,
            "timeout_bars": 16,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)

        fast = self.params["fast_period"]
        slow = self.params["slow_period"]
        sig_period = self.params["signal_period"]
        vol_mult = self.params["vol_multiplier"]

        c = data["close"]
        v = data["volume"] if "volume" in data.columns else pd.Series(1, index=data.index)

        # MACD
        ema_fast = c.ewm(span=fast, adjust=False).mean()
        ema_slow = c.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=sig_period, adjust=False).mean()

        # Volume filter
        vol_avg = v.rolling(20, min_periods=5).mean()

        # Crossover detection
        prev_macd = macd_line.shift(1)
        prev_signal = signal_line.shift(1)

        bullish_cross = (prev_macd <= prev_signal) & (macd_line > signal_line)
        bearish_cross = (prev_macd >= prev_signal) & (macd_line < signal_line)

        vol_ok = v > vol_avg * vol_mult

        signals[bullish_cross & vol_ok] = 1
        signals[bearish_cross & vol_ok] = -1

        # Max one trade per session
        if "session_date" in data.columns:
            for sd, group in data.groupby("session_date"):
                group_signals = signals.loc[group.index]
                non_zero = group_signals[group_signals != 0]
                if len(non_zero) > 1:
                    signals.loc[non_zero.index[1:]] = 0

        return signals


class ConnorsRSIStrategy(BaseStrategy):
    """
    THESIS: Connors RSI uses RSI(2) on daily bars for extreme short-term
    mean-reversion. Well-documented in Larry Connors' research.

    RSI(2) < 10 → long (deeply oversold, expect bounce)
    RSI(2) > 90 → short (deeply overbought, expect pullback)

    EXIT: Close when RSI crosses 50, or after N bars.
    """

    name = "Connors_RSI"
    description = "Connors 2-period RSI mean-reversion"
    category = "mean_reversion"
    timeframe = "1D"
    max_trades_per_day = 1

    param_grid = {
        "rsi_period": [2, 3],
        "oversold": [5, 10],
        "overbought": [90, 95],
        "exit_rsi": [50, 60],
        "timeout_bars": [5, 10],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "rsi_period": 2,
            "oversold": 10,
            "overbought": 90,
            "exit_rsi": 50,
            "timeout_bars": 5,
        }

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=data.index)
        period = self.params["rsi_period"]
        oversold = self.params["oversold"]
        overbought = self.params["overbought"]

        c = data["close"]

        # Compute RSI
        delta = c.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        signals[rsi < oversold] = 1
        signals[rsi > overbought] = -1

        return signals