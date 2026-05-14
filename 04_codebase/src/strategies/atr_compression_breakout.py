"""
ATR Compression Breakout
=========================
THESIS: Extended low-volatility consolidation (ATR compression) reliably
precedes explosive directional moves in commodity and FX markets. When
ATR(14) has been suppressed below a fraction of ATR_slow(50) for N
consecutive bars, the next expansion bar signals that volatility is
releasing — enter in the direction of the expansion bar.

Energy and metal markets have better-defined volatility cycles than
equity indices, making them the primary targets.

SIGNAL:
  1. Compression: ATR(14) < compression_threshold × ATR_slow(50)
     for ≥ compression_bars consecutive completed bars
  2. Expansion trigger: current ATR(14) > ATR_slow(50)
     (volatility breaks free)
  3. Direction: long if bar is bullish (close > open) AND close is above
     the N-bar high; short if bearish AND close is below the N-bar low
  - One trade per session.

ENTRY / EXIT:
  - Entry at next bar's open after expansion trigger
  - Stop: N-bar low (for long) / N-bar high (for short) at signal time
  - Target: entry ± rr_ratio × stop_distance
  - Timeout: 24 bars

PARAM GRID: 2 × 2 × 2 = 8 combos
  compression_bars:      [5, 10]
  compression_threshold: [0.7, 0.8]
  rr_ratio:              [1.5, 2.0]

LOOKAHEAD RISK:
  ATR values use completed bars only — fully causal.
  ATR_slow uses a longer window but still causal.
  N-bar high/low look back only (shift=1 on the range window).
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


class ATRCompressionBreakoutStrategy(BaseStrategy):

    name        = "ATR_Compression_Breakout"
    description = "Volatility compression then ATR expansion directional breakout"
    category    = "volatility_breakout"
    timeframe   = "5min"
    version     = "1.0"
    max_trades_per_day = 1

    ATR_FAST_PERIOD  = 14
    ATR_SLOW_PERIOD  = 50

    param_grid = {
        "compression_bars":      [5, 10],
        "compression_threshold": [0.7, 0.8],
        "rr_ratio":              [1.5, 2.0],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "compression_bars": 5,
            "compression_threshold": 0.7,
            "rr_ratio": 1.5,
        }

    def _compute_atr(self, data: pd.DataFrame, period: int) -> pd.Series:
        if "atr" in data.columns and period == self.ATR_FAST_PERIOD:
            return data["atr"]
        close = data["close"]
        prev  = close.shift(1)
        tr    = np.maximum(data["high"] - data["low"],
                           np.maximum(np.abs(data["high"] - prev),
                                      np.abs(data["low"]  - prev)))
        return pd.Series(tr, index=data.index).rolling(period, min_periods=period).mean()

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        comp_bars  = int(self.params["compression_bars"])
        comp_thresh = float(self.params["compression_threshold"])

        atr_fast = self._compute_atr(data, self.ATR_FAST_PERIOD)
        atr_slow = self._compute_atr(data, self.ATR_SLOW_PERIOD)

        # Rolling count of consecutive compressed bars (vectorized)
        compressed = (atr_fast < comp_thresh * atr_slow).astype(int)
        # Streak of consecutive compressed bars ending at each index
        streak = compressed.groupby(
            (compressed != compressed.shift()).cumsum()
        ).cumsum()

        # Expansion bar: ATR(fast) now > ATR(slow) AND streak before this bar
        # We need: the bar PRIOR to current had streak >= comp_bars,
        # and current bar's ATR is expanding.
        prior_streak  = streak.shift(1)
        prior_atr_f   = atr_fast.shift(1)
        prior_atr_s   = atr_slow.shift(1)
        was_compressed = prior_streak >= comp_bars
        now_expanding  = atr_fast > atr_slow  # current bar breaks out of slow ATR

        # Direction: from bar's body and N-bar range
        close   = data["close"]
        open_p  = data["open"]
        n_bar_high = data["high"].rolling(comp_bars, min_periods=comp_bars).max().shift(1)
        n_bar_low  = data["low"].rolling(comp_bars, min_periods=comp_bars).min().shift(1)

        bullish_bar = close > open_p
        bearish_bar = close < open_p
        long_filter  = bullish_bar & (close > n_bar_high)
        short_filter = bearish_bar & (close < n_bar_low)

        base = was_compressed & now_expanding

        signals = pd.Series(0, index=data.index)
        signals[base & long_filter]  =  1
        signals[base & short_filter] = -1

        # One trade per session
        if "session_date" in data.columns:
            signals = self._one_per_session(signals, data["session_date"])

        return signals

    @staticmethod
    def _one_per_session(signals: pd.Series, session_date: pd.Series) -> pd.Series:
        result = signals.copy()
        seen = set()
        for idx, val in signals.items():
            if val == 0:
                continue
            sd = session_date.loc[idx]
            if sd in seen:
                result.loc[idx] = 0
            else:
                seen.add(sd)
        return result

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        rr_ratio  = float(self.params["rr_ratio"])
        comp_bars = int(self.params["compression_bars"])
        timeout   = min(24, max_bars_per_trade)

        n_bar_high = data["high"].rolling(comp_bars, min_periods=comp_bars).max().shift(1)
        n_bar_low  = data["low"].rolling(comp_bars, min_periods=comp_bars).min().shift(1)

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

                # Stop = N-bar low (long) or N-bar high (short) at signal bar
                stop_price = (n_bar_low.loc[idx] if direction == 1
                              else n_bar_high.loc[idx])
                if np.isnan(stop_price):
                    continue
                stop_dist = abs(entry_price - stop_price)
                if stop_dist <= 0:
                    continue

                target_price = entry_price + direction * rr_ratio * stop_dist

                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, timeout + 1):
                    loc = sig_loc + 1 + i
                    if loc >= len(data):
                        break
                    bar = data.iloc[loc]

                    if direction == 1:
                        if bar["low"] <= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["high"] >= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
                    else:
                        if bar["high"] >= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["low"] <= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break

                if exit_price is None:
                    last_loc   = min(sig_loc + 1 + timeout, len(data) - 1)
                    exit_price = data.iloc[last_loc]["close"]
                    exit_time  = data.iloc[last_loc].name
                    exit_type  = "timeout"

                trades.append({
                    "entry_time":  entry_time,  "entry_price": entry_price,
                    "exit_time":   exit_time,   "exit_price":  exit_price,
                    "direction":   direction,    "exit_type":   exit_type,
                    "gross_pnl":   (exit_price - entry_price) * direction,
                    "stop_price":  stop_price,   "target_price": target_price,
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
