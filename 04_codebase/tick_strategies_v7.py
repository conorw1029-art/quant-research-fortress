"""
L2 Tick Strategy Library — V7: Trend Following and Mean Reversion
=================================================================
10 strategies using pure OHLCV price action:

  1. donchian_breakout         — N-bar Donchian channel breakout continuation
  2. ema_crossover             — Fast/slow EMA crossover trend entry
  3. keltner_breakout          — Keltner channel (EMA ± N×ATR) breakout
  4. bollinger_breakout        — Bollinger band breakout continuation
  5. bollinger_rsi_reversal    — BB squeeze + RSI extreme → mean revert
  6. rsi2_reversal             — Larry Connors RSI-2 extreme reversal
  7. zscore_reversion          — Rolling z-score of close → fade extremes
  8. momentum_continuation     — N-bar return strong → continue
  9. atr_channel_breakout      — Price breaks out of ATR-based range
 10. failed_breakout_reversal  — Price breaks N-bar high/low then reverses
"""

import numpy as np
import pandas as pd


# ── Shared helpers ────────────────────────────────────────────────────────────

def _signal(long_cond, short_cond):
    s = pd.Series(0, index=long_cond.index, dtype=int)
    s[long_cond]  =  1
    s[short_cond] = -1
    return s


def _atr(df, window=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c   = c.shift(1)
    tr       = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ── 1. Donchian Breakout ──────────────────────────────────────────────────────

def _donchian_breakout(df, n=20, confirm=1):
    """
    Long when close breaks above N-bar high (shifted 1 to avoid lookahead).
    Short when close breaks below N-bar low.
    confirm: require this many bars above/below channel before entry.
    """
    roll_high = df["high"].shift(1).rolling(n, min_periods=n).max()
    roll_low  = df["low"].shift(1).rolling(n, min_periods=n).min()
    above = (df["close"] > roll_high).rolling(confirm, min_periods=confirm).min().astype(bool)
    below = (df["close"] < roll_low).rolling(confirm, min_periods=confirm).min().astype(bool)
    return _signal(above, below)


DONCHIAN_BREAKOUT = {
    "name": "donchian_breakout",
    "compute": _donchian_breakout,
    "param_grid": {
        "n":       [10, 20, 40],
        "confirm": [1, 2],
    },
}


# ── 2. EMA Crossover ─────────────────────────────────────────────────────────

def _ema_crossover(df, fast=8, slow=21, slope_bars=3):
    """
    Long when fast EMA crosses above slow EMA and slow EMA has positive slope.
    slope_bars: look back N bars to determine EMA slope direction.
    """
    f = _ema(df["close"], fast)
    s = _ema(df["close"], slow)
    cross_up   = (f > s) & (f.shift(1) <= s.shift(1))
    cross_down = (f < s) & (f.shift(1) >= s.shift(1))
    slope_up   = s > s.shift(slope_bars)
    slope_down = s < s.shift(slope_bars)
    return _signal(cross_up & slope_up, cross_down & slope_down)


EMA_CROSSOVER = {
    "name": "ema_crossover",
    "compute": _ema_crossover,
    "param_grid": {
        "fast":       [5, 8, 13],
        "slow":       [21, 34],
        "slope_bars": [3, 5],
    },
}


# ── 3. Keltner Channel Breakout ───────────────────────────────────────────────

def _keltner_breakout(df, ema_span=20, atr_win=14, mult=1.5):
    """
    Long when close breaks above EMA + mult×ATR.
    Short when close breaks below EMA - mult×ATR.
    """
    mid   = _ema(df["close"], ema_span)
    atr   = _atr(df, atr_win)
    upper = mid + mult * atr
    lower = mid - mult * atr
    return _signal(df["close"] > upper, df["close"] < lower)


KELTNER_BREAKOUT = {
    "name": "keltner_breakout",
    "compute": _keltner_breakout,
    "param_grid": {
        "ema_span": [15, 20, 30],
        "atr_win":  [10, 14],
        "mult":     [1.5, 2.0, 2.5],
    },
}


# ── 4. Bollinger Breakout ─────────────────────────────────────────────────────

def _bollinger_breakout(df, window=20, std_mult=2.0, squeeze_bars=5):
    """
    Long on close above upper BB after a squeeze (narrow bands).
    squeeze_bars: min bars where BB width < rolling median width to qualify as squeeze.
    """
    mid   = df["close"].rolling(window, min_periods=window).mean()
    std   = df["close"].rolling(window, min_periods=window).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    med_w = width.rolling(window * 2, min_periods=window).median()
    squeezed = (width < med_w).rolling(squeeze_bars, min_periods=squeeze_bars).min().astype(bool)
    long_sig  = (df["close"] > upper) & squeezed.shift(1).fillna(False)
    short_sig = (df["close"] < lower) & squeezed.shift(1).fillna(False)
    return _signal(long_sig, short_sig)


BOLLINGER_BREAKOUT = {
    "name": "bollinger_breakout",
    "compute": _bollinger_breakout,
    "param_grid": {
        "window":       [15, 20],
        "std_mult":     [1.8, 2.0, 2.2],
        "squeeze_bars": [3, 5],
    },
}


# ── 5. Bollinger RSI Reversal ─────────────────────────────────────────────────

def _bollinger_rsi_reversal(df, window=20, std_mult=2.0, rsi_win=14, rsi_ob=70, rsi_os=30):
    """
    Long when close touches lower BB and RSI is oversold.
    Short when close touches upper BB and RSI is overbought.
    """
    mid   = df["close"].rolling(window, min_periods=window).mean()
    std   = df["close"].rolling(window, min_periods=window).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    rsi   = _rsi(df["close"], rsi_win)
    long_sig  = (df["close"] <= lower) & (rsi < rsi_os)
    short_sig = (df["close"] >= upper) & (rsi > rsi_ob)
    return _signal(long_sig, short_sig)


BOLLINGER_RSI_REVERSAL = {
    "name": "bollinger_rsi_reversal",
    "compute": _bollinger_rsi_reversal,
    "param_grid": {
        "window":   [15, 20],
        "std_mult": [1.8, 2.0],
        "rsi_win":  [10, 14],
        "rsi_ob":   [70],
        "rsi_os":   [30],
    },
}


# ── 6. RSI-2 Reversal ────────────────────────────────────────────────────────

def _rsi2_reversal(df, rsi_win=2, ob=90, os=10, trend_ema=200):
    """
    Larry Connors RSI-2: extreme RSI in direction of longer-term trend.
    Long when 200-EMA trend is up and RSI-2 < os.
    Short when 200-EMA trend is down and RSI-2 > ob.
    """
    rsi_fast = _rsi(df["close"], rsi_win)
    trend    = _ema(df["close"], trend_ema)
    long_sig  = (df["close"] > trend) & (rsi_fast < os)
    short_sig = (df["close"] < trend) & (rsi_fast > ob)
    return _signal(long_sig, short_sig)


RSI2_REVERSAL = {
    "name": "rsi2_reversal",
    "compute": _rsi2_reversal,
    "param_grid": {
        "rsi_win":   [2, 3],
        "ob":        [85, 90],
        "os":        [10, 15],
        "trend_ema": [100, 200],
    },
}


# ── 7. Z-Score Reversion ─────────────────────────────────────────────────────

def _zscore_reversion(df, window=30, z_entry=2.0, z_exit=0.5):
    """
    Fade price when rolling z-score of close exceeds ±z_entry.
    Exit signal when z-score returns within ±z_exit (not modeled here — entry only).
    """
    mu    = df["close"].rolling(window, min_periods=window).mean()
    sigma = df["close"].rolling(window, min_periods=window).std(ddof=0)
    z     = (df["close"] - mu) / sigma.replace(0, np.nan)
    return _signal(z < -z_entry, z > z_entry)


ZSCORE_REVERSION = {
    "name": "zscore_reversion",
    "compute": _zscore_reversion,
    "param_grid": {
        "window":  [20, 30, 50],
        "z_entry": [1.5, 2.0, 2.5],
        "z_exit":  [0.5],
    },
}


# ── 8. Momentum Continuation ─────────────────────────────────────────────────

def _momentum_continuation(df, lookback=10, thresh_pct=0.003):
    """
    Long when N-bar return exceeds +thresh_pct (strong up momentum).
    Short when N-bar return is below -thresh_pct.
    """
    ret = df["close"].pct_change(lookback)
    return _signal(ret > thresh_pct, ret < -thresh_pct)


MOMENTUM_CONTINUATION = {
    "name": "momentum_continuation",
    "compute": _momentum_continuation,
    "param_grid": {
        "lookback":   [5, 10, 20],
        "thresh_pct": [0.002, 0.003, 0.005],
    },
}


# ── 9. ATR Channel Breakout ───────────────────────────────────────────────────

def _atr_channel_breakout(df, atr_win=14, channel_bars=20, mult=1.0):
    """
    Build a channel: mid = rolling close mean; channel = ±mult×ATR.
    Signal when close breaks outside channel (continuation of push).
    """
    mid   = df["close"].rolling(channel_bars, min_periods=channel_bars).mean()
    atr   = _atr(df, atr_win)
    upper = mid + mult * atr
    lower = mid - mult * atr
    return _signal(df["close"] > upper, df["close"] < lower)


ATR_CHANNEL_BREAKOUT = {
    "name": "atr_channel_breakout",
    "compute": _atr_channel_breakout,
    "param_grid": {
        "atr_win":      [10, 14],
        "channel_bars": [15, 20],
        "mult":         [0.75, 1.0, 1.5],
    },
}


# ── 10. Failed Breakout Reversal ──────────────────────────────────────────────

def _failed_breakout_reversal(df, n=20, reversal_bars=2):
    """
    Price breaks N-bar high/low (the bait) but closes back inside within reversal_bars.
    This traps breakout traders → trade the reversal.
    """
    prev_high = df["high"].shift(1).rolling(n, min_periods=n).max()
    prev_low  = df["low"].shift(1).rolling(n, min_periods=n).min()
    # Breakout bar: close breaks out
    broke_high = df["close"] > prev_high
    broke_low  = df["close"] < prev_low
    # Within reversal_bars: close re-enters prior range (close back inside)
    back_inside_low  = df["close"] < prev_high.shift(1)
    back_inside_high = df["close"] > prev_low.shift(1)
    # Detect: recent breakout that has now reversed
    was_high_break = broke_high.shift(1).rolling(reversal_bars, min_periods=1).max().astype(bool)
    was_low_break  = broke_low.shift(1).rolling(reversal_bars, min_periods=1).max().astype(bool)
    long_sig  = was_low_break  & back_inside_high
    short_sig = was_high_break & back_inside_low
    return _signal(long_sig, short_sig)


FAILED_BREAKOUT_REVERSAL = {
    "name": "failed_breakout_reversal",
    "compute": _failed_breakout_reversal,
    "param_grid": {
        "n":             [10, 20],
        "reversal_bars": [1, 2, 3],
    },
}


# ── Registry ──────────────────────────────────────────────────────────────────

STRAT_MAP_V7 = {
    "donchian_breakout":         DONCHIAN_BREAKOUT,
    "ema_crossover":             EMA_CROSSOVER,
    "keltner_breakout":          KELTNER_BREAKOUT,
    "bollinger_breakout":        BOLLINGER_BREAKOUT,
    "bollinger_rsi_reversal":    BOLLINGER_RSI_REVERSAL,
    "rsi2_reversal":             RSI2_REVERSAL,
    "zscore_reversion":          ZSCORE_REVERSION,
    "momentum_continuation":     MOMENTUM_CONTINUATION,
    "atr_channel_breakout":      ATR_CHANNEL_BREAKOUT,
    "failed_breakout_reversal":  FAILED_BREAKOUT_REVERSAL,
}
STRATEGIES_V7 = list(STRAT_MAP_V7.values())
