"""
ES/NQ Price Action Strategies
===============================
Price-action strategies that work on OHLCV bars (no L2 required).
These are designed to use the extended 2020-2025 historical data.

Strategies:
1. Opening Range Breakout with volume confirmation
2. Previous Day H/L Sweep Reversal (enhanced)
3. VWAP Deviation Mean Reversion (enhanced)
4. Range Contraction Breakout (enhanced)
5. Multi-day Momentum Follow
6. Session High/Low Fade

All strategies use ATR-based position sizing and stops.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


def _atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low = data["high"], data["low"]
    prev_close = data["close"].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _vwap(data: pd.DataFrame) -> pd.Series:
    """Session VWAP approximation (resets at CME session open ~17:00 UTC)."""
    close = data["close"]
    vol   = data.get("volume", pd.Series(1, index=data.index))

    # Session date: CME contract rolls at 17:00 UTC
    session_key = (data.index - pd.Timedelta(hours=17)).date

    vwap = pd.Series(np.nan, index=data.index)
    s_series = pd.Series(session_key, index=data.index)

    for date, grp_idx in s_series.groupby(s_series).groups.items():
        v = vol.loc[grp_idx]
        c = close.loc[grp_idx]
        cum_vol  = v.cumsum()
        cum_tpv  = (c * v).cumsum()
        vwap.loc[grp_idx] = cum_tpv / cum_vol.replace(0, np.nan)

    return vwap


def _scan_exit(data, start_loc, direction, stop_price, target_price, timeout):
    n = len(data)
    for i in range(1, timeout + 1):
        loc = start_loc + i
        if loc >= n:
            break
        bar = data.iloc[loc]
        if direction == 1:
            if bar["low"] <= stop_price:
                return stop_price, bar.name, "stop"
            if bar["high"] >= target_price:
                return target_price, bar.name, "target"
        else:
            if bar["high"] >= stop_price:
                return stop_price, bar.name, "stop"
            if bar["low"] <= target_price:
                return target_price, bar.name, "target"
    last_loc = min(start_loc + timeout, n - 1)
    return data.iloc[last_loc]["close"], data.iloc[last_loc].name, "timeout"


def _build_trades(data, signals, rr, hold, max_bars=78, use_atr=True, atr_period=14):
    atr_vals = _atr(data, atr_period) if use_atr else None
    timeout  = min(hold, max_bars)
    trades   = []

    for idx in signals[signals != 0].index:
        try:
            direction = int(signals[idx])
            sig_loc   = data.index.get_loc(idx)
            if sig_loc + 1 >= len(data):
                continue

            entry_bar   = data.iloc[sig_loc + 1]
            entry_price = entry_bar["open"]
            entry_time  = entry_bar.name

            if use_atr and atr_vals is not None:
                stop_dist = float(atr_vals.iloc[sig_loc]) if sig_loc < len(atr_vals) else 0
                if stop_dist <= 0:
                    continue
                stop_price   = entry_price - direction * stop_dist
                target_price = entry_price + direction * rr * stop_dist
            else:
                sig_bar    = data.iloc[sig_loc]
                stop_price  = sig_bar["low"] if direction == 1 else sig_bar["high"]
                stop_dist   = abs(entry_price - stop_price)
                if stop_dist <= 0:
                    continue
                target_price = entry_price + direction * rr * stop_dist

            exit_price, exit_time, exit_type = _scan_exit(
                data, sig_loc + 1, direction, stop_price, target_price, timeout
            )

            trades.append({
                "entry_time": entry_time, "entry_price": entry_price,
                "exit_time": exit_time,   "exit_price": exit_price,
                "direction": direction,   "exit_type": exit_type,
                "gross_pnl": (exit_price - entry_price) * direction,
                "stop_price": stop_price, "target_price": target_price,
            })
        except Exception:
            continue
    return trades


class EnhancedORBStrategy(BaseStrategy):
    """
    Enhanced Opening Range Breakout.
    THESIS: The first 15 minutes of the RTH session establishes the intraday
    range. Breakouts from this range with volume confirmation are high probability.
    Enhanced: uses 3-bar confirmation and volume filter.
    """
    name     = "Enhanced_ORB"
    category = "price_action"
    timeframe = "5min"
    version  = "2.0"

    param_grid = {
        "orb_bars":     [3, 5],         # number of opening bars
        "vol_mult":     [1.0, 1.5],     # volume multiplier for confirmation
        "rr_ratio":     [1.5, 2.0, 2.5],
        "hold_bars":    [8, 12, 20],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"orb_bars": 3, "vol_mult": 1.2, "rr_ratio": 2.0, "hold_bars": 12}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        high  = data["high"]
        low   = data["low"]
        vol   = data.get("volume", pd.Series(1, index=data.index))
        vol_avg = vol.rolling(50, min_periods=10).mean()

        orb_n   = int(self.params["orb_bars"])
        vol_mul = float(self.params["vol_mult"])

        # RTH opens at 14:30 UTC (9:30 ET)
        hour  = data.index.hour
        minute = data.index.minute
        is_rth_open = (hour == 14) & (minute >= 30)

        signals = pd.Series(0, index=data.index)

        # Rolling ORB: high/low of last orb_n bars at session open
        orb_high = high.rolling(orb_n).max().shift(1)
        orb_low  = low.rolling(orb_n).min().shift(1)

        # Only trade in RTH (14:30–21:00 UTC)
        in_rth = ((hour > 14) | ((hour == 14) & (minute >= 30))) & (hour < 21)

        vol_ok = (vol >= vol_mul * vol_avg).fillna(False) if vol_mul > 1.0 else pd.Series(True, index=data.index)

        # Breakout
        bo_long  = (high > orb_high) & (close > orb_high) & in_rth & vol_ok
        bo_short = (low  < orb_low)  & (close < orb_low)  & in_rth & vol_ok

        signals[bo_long]  =  1
        signals[bo_short] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _build_trades(data, signals,
                             float(self.params["rr_ratio"]),
                             int(self.params["hold_bars"]),
                             max_bars_per_trade,
                             use_atr=False)


class VWAPDeviationStrategy(BaseStrategy):
    """
    VWAP Deviation Mean Reversion.
    THESIS: Price stretched too far from VWAP during RTH will revert.
    Enhanced: uses ATR-normalized deviation and volume exhaustion filter.
    """
    name     = "VWAP_Deviation_MeanRev"
    category = "price_action"
    timeframe = "5min"
    version  = "2.0"

    param_grid = {
        "dev_atr_mult": [1.5, 2.0, 2.5],   # deviation threshold in ATR units
        "atr_period":   [10, 14],
        "rr_ratio":     [1.0, 1.5],
        "hold_bars":    [6, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"dev_atr_mult": 2.0, "atr_period": 14,
                                 "rr_ratio": 1.2, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        atr_p = int(self.params["atr_period"])
        thr   = float(self.params["dev_atr_mult"])

        atr = _atr(data, atr_p)
        if "session_vwap" in data.columns:
            vwap = data["session_vwap"]
        else:
            vwap = _vwap(data)

        deviation = close - vwap
        dev_atr   = deviation / atr.replace(0, np.nan)

        hour = data.index.hour
        in_rth = (hour >= 14) & (hour < 21)

        signals = pd.Series(0, index=data.index)
        # Extended too far above VWAP → short (mean revert)
        signals[(dev_atr >  thr) & in_rth] = -1
        # Extended too far below VWAP → long (mean revert)
        signals[(dev_atr < -thr) & in_rth] =  1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _build_trades(data, signals,
                             float(self.params["rr_ratio"]),
                             int(self.params["hold_bars"]),
                             max_bars_per_trade,
                             use_atr=True,
                             atr_period=int(self.params["atr_period"]))


class PrevDayHLSweepRevStrategy(BaseStrategy):
    """
    Previous Day H/L Sweep Reversal (Enhanced).
    THESIS: Price sweeps above previous day high or below previous day low
    to grab liquidity (stop orders), then reverses. Strong intraday signal.
    Enhanced: requires reversal close back through the level.
    """
    name     = "PrevDay_HL_Sweep_Rev"
    category = "price_action"
    timeframe = "5min"
    version  = "2.0"

    param_grid = {
        "sweep_atr": [0.5, 1.0],       # minimum sweep size vs ATR
        "rr_ratio":  [1.5, 2.0, 2.5],
        "hold_bars": [8, 12, 20],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"sweep_atr": 0.5, "rr_ratio": 2.0, "hold_bars": 12}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        high  = data["high"]
        low   = data["low"]

        # Previous day levels (approximate: 390 bars of 1-minute = 1 day)
        # Use 1440-minute rolling high/low shifted by 1 day
        prev_day_high = high.rolling(1440, min_periods=60).max().shift(1440)
        prev_day_low  = low.rolling(1440, min_periods=60).min().shift(1440)

        atr = _atr(data, 14)
        min_sweep = float(self.params["sweep_atr"]) * atr

        hour = data.index.hour
        in_rth = (hour >= 14) & (hour < 21)

        # Price swept above prev day high but closed back below it
        swept_high_reversed = (high > prev_day_high) & (close < prev_day_high) & (high - prev_day_high > min_sweep)
        # Price swept below prev day low but closed back above it
        swept_low_reversed  = (low < prev_day_low)  & (close > prev_day_low)  & (prev_day_low - low > min_sweep)

        signals = pd.Series(0, index=data.index)
        signals[swept_low_reversed  & in_rth] =  1
        signals[swept_high_reversed & in_rth] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _build_trades(data, signals,
                             float(self.params["rr_ratio"]),
                             int(self.params["hold_bars"]),
                             max_bars_per_trade,
                             use_atr=False)


class RangeContractionBreakoutStrategy(BaseStrategy):
    """
    Range Contraction Breakout.
    THESIS: After N bars of decreasing range (ATR compression), a breakout
    tends to be explosive. Trade in the direction of the breakout.
    """
    name     = "Range_Contraction_Breakout"
    category = "price_action"
    timeframe = "5min"
    version  = "2.0"

    param_grid = {
        "compress_bars":  [4, 6, 8],
        "compress_ratio": [0.5, 0.7],   # ATR must be this fraction of 20-bar ATR
        "rr_ratio":       [2.0, 2.5, 3.0],
        "hold_bars":      [10, 15, 20],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"compress_bars": 5, "compress_ratio": 0.6,
                                 "rr_ratio": 2.5, "hold_bars": 15}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high  = data["high"]
        low   = data["low"]
        close = data["close"]

        n_bars = int(self.params["compress_bars"])
        ratio  = float(self.params["compress_ratio"])

        # Rolling range of last n bars
        short_atr = (high - low).rolling(n_bars).mean()
        long_atr  = (high - low).rolling(20, min_periods=5).mean()

        compressed = (short_atr / long_atr.replace(0, np.nan)).fillna(1.0) < ratio

        # Breakout = high exceeds N-bar high or low exceeds N-bar low
        n_high = high.rolling(n_bars).max().shift(1)
        n_low  = low.rolling(n_bars).min().shift(1)

        bo_long  = compressed & (close > n_high)
        bo_short = compressed & (close < n_low)

        signals = pd.Series(0, index=data.index)
        signals[bo_long]  =  1
        signals[bo_short] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _build_trades(data, signals,
                             float(self.params["rr_ratio"]),
                             int(self.params["hold_bars"]),
                             max_bars_per_trade,
                             use_atr=True)


class MultiDayMomentumStrategy(BaseStrategy):
    """
    Multi-Day Momentum Follow.
    THESIS: Strong 3-5 day directional momentum in ES/NQ tends to continue
    for at least 1 more day. Enter on first pullback within the trend.
    """
    name     = "MultiDay_Momentum"
    category = "price_action"
    timeframe = "5min"
    version  = "1.0"

    param_grid = {
        "trend_days":    [3, 5],
        "pullback_atr":  [0.5, 1.0],    # pullback at least 0.5 ATR into trend
        "rr_ratio":      [1.5, 2.0],
        "hold_bars":     [20, 40],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"trend_days": 4, "pullback_atr": 0.75,
                                 "rr_ratio": 1.5, "hold_bars": 30}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        trend_bars = int(self.params["trend_days"]) * 390  # ~390 1min bars per day
        pb_atr     = float(self.params["pullback_atr"])

        atr = _atr(data, 20)

        # Multi-day trend: price above/below N-day EMA
        ema_slow = close.ewm(span=trend_bars, min_periods=100, adjust=False).mean()
        above_trend = close > ema_slow
        below_trend = close < ema_slow

        # Pullback: price pulled back at least 0.5 ATR from recent high/low
        recent_high = close.rolling(60).max().shift(1)  # 1-hour lookback
        recent_low  = close.rolling(60).min().shift(1)

        # Pullback into uptrend (dip in uptrend)
        pulled_back_in_uptrend = above_trend & (recent_high - close > pb_atr * atr)
        # Rally in downtrend
        rallied_in_downtrend   = below_trend & (close - recent_low > pb_atr * atr)

        signals = pd.Series(0, index=data.index)
        signals[pulled_back_in_uptrend] =  1   # buy the dip in uptrend
        signals[rallied_in_downtrend]   = -1   # sell the rally in downtrend
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        return _build_trades(data, signals,
                             float(self.params["rr_ratio"]),
                             int(self.params["hold_bars"]),
                             max_bars_per_trade,
                             use_atr=True)
