"""
OFI-based L2 Strategies
========================
Strategies that use Order Flow Imbalance from the L2 order book.

These strategies require L2 bars (produced by build_l2_bars) with columns:
    ofi_1, ofi_5, imbal_L5_last, imbal_L5_mean, microprice_last,
    spread_mean, cvd_delta

All strategies follow the BaseStrategy interface.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class OFIContinuationStrategy(BaseStrategy):
    """
    OFI Continuation.
    THESIS: When OFI is strongly positive and price is above VWAP, institutions
    are aggressively buying — momentum will continue.
    Uses rolling quantile thresholds (adaptive to market regime).
    """
    name     = "OFI_Continuation"
    category = "l2_ofi"
    timeframe = "1min"
    version  = "2.0"

    param_grid = {
        "ofi_pct":   [90, 93, 95],    # very high percentile = rare strong OFI
        "roll_win":  [60, 120],       # rolling window for percentile
        "rr_ratio":  [1.5, 2.0],
        "hold_bars": [5, 10],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {"ofi_pct": 92, "roll_win": 100, "rr_ratio": 1.5, "hold_bars": 10}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "ofi_5" not in data.columns:
            return pd.Series(0, index=data.index)

        pct     = float(self.params["ofi_pct"])
        roll_w  = int(self.params["roll_win"])
        ofi     = data["ofi_5"].fillna(0.0)
        close   = data["close"]

        # Rolling percentile thresholds (adaptive) — very high pct = rare events
        ofi_high = ofi.rolling(roll_w, min_periods=20).quantile(pct / 100)
        ofi_low  = ofi.rolling(roll_w, min_periods=20).quantile(1 - pct / 100)

        # Only fire on the FIRST bar that exceeds the threshold (no re-entry within 5 bars)
        above_thr = (ofi >= ofi_high)
        below_thr = (ofi <= ofi_low)
        # De-cluster: skip if signal already fired in last 5 bars
        prev_above = above_thr.shift(1).fillna(False) | above_thr.shift(2).fillna(False) | \
                     above_thr.shift(3).fillna(False) | above_thr.shift(4).fillna(False) | \
                     above_thr.shift(5).fillna(False)
        prev_below = below_thr.shift(1).fillna(False) | below_thr.shift(2).fillna(False) | \
                     below_thr.shift(3).fillna(False) | below_thr.shift(4).fillna(False) | \
                     below_thr.shift(5).fillna(False)

        ofi_surge_long  = above_thr & ~prev_above
        ofi_surge_short = below_thr & ~prev_below

        # VWAP filter
        if "session_vwap" in data.columns:
            above_vwap = close > data["session_vwap"]
            below_vwap = close < data["session_vwap"]
        else:
            above_vwap = pd.Series(True, index=data.index)
            below_vwap = pd.Series(True, index=data.index)

        signals = pd.Series(0, index=data.index)
        signals[ofi_surge_long  & above_vwap] =  1
        signals[ofi_surge_short & below_vwap] = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        rr = float(self.params["rr_ratio"])
        hold = int(self.params["hold_bars"])
        spread_col = "spread_mean" if "spread_mean" in data.columns else None
        return _l2_trades(data, signals, rr, hold, max_bars_per_trade, spread_col)


class OFIReversalStrategy(BaseStrategy):
    """
    OFI Reversal (Exhaustion).
    THESIS: Extreme OFI with no price follow-through = exhausted aggression.
    Uses rolling quantile to define "extreme" adaptively.
    """
    name     = "OFI_Reversal"
    category = "l2_ofi"
    timeframe = "1min"
    version  = "2.0"

    param_grid = {
        "ofi_pct":       [85, 90, 95],   # top percentile = "extreme"
        "body_fraction": [0.2, 0.35],
        "rr_ratio":      [1.5, 2.0],
        "hold_bars":     [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"ofi_pct": 90, "body_fraction": 0.3,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "ofi_5" not in data.columns:
            return pd.Series(0, index=data.index)

        ofi    = data["ofi_5"].fillna(0.0)
        close  = data["close"]
        open_  = data["open"]
        high   = data["high"]
        low    = data["low"]

        pct    = float(self.params["ofi_pct"])
        bf_thr = float(self.params["body_fraction"])

        # Adaptive extreme thresholds
        ofi_top = ofi.rolling(60, min_periods=10).quantile(pct / 100)
        ofi_bot = ofi.rolling(60, min_periods=10).quantile(1 - pct / 100)

        body   = abs(close - open_)
        range_ = (high - low).replace(0, np.nan)
        body_frac = (body / range_).fillna(0.0)

        extreme_buy_no_follow  = (ofi >= ofi_top) & (body_frac < bf_thr)
        extreme_sell_no_follow = (ofi <= ofi_bot) & (body_frac < bf_thr)

        signals = pd.Series(0, index=data.index)
        signals[extreme_sell_no_follow] =  1
        signals[extreme_buy_no_follow]  = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        rr = float(self.params["rr_ratio"])
        hold = int(self.params["hold_bars"])
        return _l2_trades(data, signals, rr, hold, max_bars_per_trade)


class OFIMicropriceStrategy(BaseStrategy):
    """
    OFI + Microprice Divergence.
    THESIS: Microprice above last close (book tilted bullish) + positive OFI
    momentum = convergence trade. Both signals use rolling percentiles.
    """
    name     = "OFI_Microprice"
    category = "l2_ofi"
    timeframe = "1min"
    version  = "2.0"

    param_grid = {
        "mp_diff_pct": [60, 70, 80],    # mp-close in top N% = "above"
        "ofi_pct":     [60, 70],
        "rr_ratio":    [1.5, 2.0],
        "hold_bars":   [5, 10],
    }

    def __init__(self, params=None):
        super().__init__()
        self.params = params or {"mp_diff_pct": 70, "ofi_pct": 65,
                                 "rr_ratio": 1.5, "hold_bars": 8}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "microprice_last" not in data.columns or "ofi_5" not in data.columns:
            return pd.Series(0, index=data.index)

        mp    = data["microprice_last"]
        close = data["close"]
        ofi   = data["ofi_5"].fillna(0.0)
        mp_diff = (mp - close).fillna(0.0)

        mp_pct  = float(self.params["mp_diff_pct"])
        ofi_pct = float(self.params["ofi_pct"])

        mp_high = mp_diff.rolling(60, min_periods=10).quantile(mp_pct / 100)
        mp_low  = mp_diff.rolling(60, min_periods=10).quantile(1 - mp_pct / 100)
        ofi_high = ofi.rolling(60, min_periods=10).quantile(ofi_pct / 100)
        ofi_low  = ofi.rolling(60, min_periods=10).quantile(1 - ofi_pct / 100)

        signals = pd.Series(0, index=data.index)
        signals[(mp_diff >= mp_high) & (ofi >= ofi_high)] =  1
        signals[(mp_diff <= mp_low)  & (ofi <= ofi_low)]  = -1
        return signals

    def signals_to_trades(self, data, signals, max_bars_per_trade=78):
        rr = float(self.params["rr_ratio"])
        hold = int(self.params["hold_bars"])
        return _l2_trades(data, signals, rr, hold, max_bars_per_trade)


def _l2_trades(
    data: pd.DataFrame,
    signals: pd.Series,
    rr_ratio: float,
    hold_bars: int,
    max_bars_per_trade: int,
    spread_col: Optional[str] = None,
) -> List[Dict]:
    """Shared trade builder for L2 strategies using ATR-based stops."""
    timeout = min(hold_bars, max_bars_per_trade)

    atr = _compute_atr(data, period=10)

    trades = []
    for idx in signals[signals != 0].index:
        try:
            direction = int(signals[idx])
            sig_loc   = data.index.get_loc(idx)
            if sig_loc + 1 >= len(data):
                continue

            entry_bar   = data.iloc[sig_loc + 1]
            entry_price = entry_bar["open"]
            entry_time  = entry_bar.name

            atr_val = atr.iloc[sig_loc] if sig_loc < len(atr) else atr.mean()
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            # Stop = 1.0 × ATR; Target = rr × ATR
            stop_dist    = max(atr_val, 1e-6)
            stop_price   = entry_price - direction * stop_dist
            target_price = entry_price + direction * rr_ratio * stop_dist

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


def _compute_atr(data: pd.DataFrame, period: int = 10) -> pd.Series:
    high  = data["high"]
    low   = data["low"]
    close = data["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()
