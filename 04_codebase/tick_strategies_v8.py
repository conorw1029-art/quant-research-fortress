"""
L2 Tick Strategy Library — V8: Volatility, Volume, and Misc Price Action
========================================================================
10 strategies using pure OHLCV:

  1. atr_compression_breakout   — ATR shrinks below threshold → breakout on expansion
  2. consecutive_close_momentum — N consecutive closes in same direction → continue
  3. high_volume_continuation   — Volume spike bar in trend direction → continue
  4. inside_bar_breakout        — Inside bar (narrow range within prior bar) → breakout
  5. pivot_reversal             — Classic pivot point (H+L+C/3) support/resistance
  6. rolling_return_zscore      — Z-score of rolling N-bar returns → revert extremes
  7. range_expansion_follow     — Today's range already larger than N-day avg → follow
  8. ma_slope_regime            — Positive/negative MA slope as trade filter + entry
  9. rsi_momentum               — RSI crosses 50 level → trade momentum direction
 10. close_position_momentum    — Close near high of bar (strong bar) → continue
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


def _rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


# ── 1. ATR Compression Breakout ───────────────────────────────────────────────

def _atr_compression_breakout(df, atr_win=14, comp_window=20, comp_pct=0.7):
    """
    ATR compresses to comp_pct of its N-bar rolling max → next direction is the breakout.
    Long when current close > prior close after compression. Short otherwise.
    """
    atr     = _atr(df, atr_win)
    atr_max = atr.rolling(comp_window, min_periods=comp_window).max()
    compressed = atr < (comp_pct * atr_max)
    # Entry: first bar after compression that closes up/down
    just_compressed = compressed.shift(1).fillna(False)
    long_sig  = just_compressed & (df["close"] > df["close"].shift(1))
    short_sig = just_compressed & (df["close"] < df["close"].shift(1))
    return _signal(long_sig, short_sig)


ATR_COMPRESSION_BREAKOUT = {
    "name": "atr_compression_breakout",
    "compute": _atr_compression_breakout,
    "param_grid": {
        "atr_win":    [10, 14],
        "comp_window":[15, 20],
        "comp_pct":   [0.6, 0.7, 0.8],
    },
}


# ── 2. Consecutive Close Momentum ─────────────────────────────────────────────

def _consecutive_close_momentum(df, n=3):
    """
    N consecutive up closes → long. N consecutive down closes → short.
    """
    up   = (df["close"] > df["close"].shift(1)).astype(int)
    down = (df["close"] < df["close"].shift(1)).astype(int)
    long_sig  = up.rolling(n, min_periods=n).min().astype(bool)
    short_sig = down.rolling(n, min_periods=n).min().astype(bool)
    return _signal(long_sig, short_sig)


CONSECUTIVE_CLOSE_MOMENTUM = {
    "name": "consecutive_close_momentum",
    "compute": _consecutive_close_momentum,
    "param_grid": {
        "n": [2, 3, 4, 5],
    },
}


# ── 3. High Volume Continuation ───────────────────────────────────────────────

def _high_volume_continuation(df, vol_window=20, vol_mult=2.0, trend_bars=5):
    """
    Volume spike (> vol_mult × N-bar avg) on a directional bar.
    Requires a prior trend (close above/below N-bar SMA) for continuation.
    """
    avg_vol  = df["volume"].rolling(vol_window, min_periods=vol_window).mean()
    vol_spike = df["volume"] > (vol_mult * avg_vol)
    trend_sma = df["close"].rolling(trend_bars, min_periods=trend_bars).mean()
    up_bar   = df["close"] > df["open"]
    down_bar = df["close"] < df["open"]
    long_sig  = vol_spike & up_bar   & (df["close"] > trend_sma)
    short_sig = vol_spike & down_bar & (df["close"] < trend_sma)
    return _signal(long_sig, short_sig)


HIGH_VOLUME_CONTINUATION = {
    "name": "high_volume_continuation",
    "compute": _high_volume_continuation,
    "param_grid": {
        "vol_window": [15, 20],
        "vol_mult":   [1.5, 2.0, 2.5],
        "trend_bars": [5, 10],
    },
}


# ── 4. Inside Bar Breakout ────────────────────────────────────────────────────

def _inside_bar_breakout(df, n_inside=1, breakout_confirm=0):
    """
    Inside bar: current H/L is fully within prior bar's H/L.
    Next bar breaks above/below → trade breakout direction.
    n_inside: consecutive inside bars required (tighter compression = better).
    breakout_confirm: bars close above/below inside bar range to confirm.
    """
    prev_h = df["high"].shift(1)
    prev_l = df["low"].shift(1)
    is_inside = (df["high"] <= prev_h) & (df["low"] >= prev_l)
    # Require n_inside consecutive inside bars
    was_inside = is_inside.rolling(n_inside, min_periods=n_inside).min().astype(bool)
    # Breakout: next bar close vs the inside bar's parent H/L
    parent_h = prev_h.shift(1)
    parent_l = prev_l.shift(1)
    long_sig  = was_inside.shift(1).fillna(False) & (df["close"] > parent_h)
    short_sig = was_inside.shift(1).fillna(False) & (df["close"] < parent_l)
    return _signal(long_sig, short_sig)


INSIDE_BAR_BREAKOUT = {
    "name": "inside_bar_breakout",
    "compute": _inside_bar_breakout,
    "param_grid": {
        "n_inside":          [1, 2],
        "breakout_confirm":  [0],
    },
}


# ── 5. Pivot Reversal ─────────────────────────────────────────────────────────

def _pivot_reversal(df, pivot_bars=5, bounce_atr_mult=0.3, atr_win=14):
    """
    Rolling pivot = (high + low + close) / 3 over pivot_bars.
    R1 = 2×pivot − rolling_low; S1 = 2×pivot − rolling_high.
    Long when price touches S1 ± bounce_atr. Short near R1.
    """
    atr    = _atr(df, atr_win)
    roll_h = df["high"].rolling(pivot_bars, min_periods=pivot_bars).max()
    roll_l = df["low"].rolling(pivot_bars, min_periods=pivot_bars).min()
    pivot  = (roll_h + roll_l + df["close"]) / 3
    r1     = 2 * pivot - roll_l
    s1     = 2 * pivot - roll_h
    tol    = bounce_atr_mult * atr
    long_sig  = (df["low"] <= s1 + tol) & (df["close"] > s1 - tol)
    short_sig = (df["high"] >= r1 - tol) & (df["close"] < r1 + tol)
    return _signal(long_sig, short_sig)


PIVOT_REVERSAL = {
    "name": "pivot_reversal",
    "compute": _pivot_reversal,
    "param_grid": {
        "pivot_bars":       [5, 10, 20],
        "bounce_atr_mult":  [0.2, 0.3, 0.5],
        "atr_win":          [10, 14],
    },
}


# ── 6. Rolling Return Z-Score ─────────────────────────────────────────────────

def _rolling_return_zscore(df, ret_bars=5, zscore_win=50, z_thresh=1.8):
    """
    Compute N-bar returns, then z-score those returns over zscore_win bars.
    Fade when z-score of return is extreme (unusual burst).
    """
    ret = df["close"].pct_change(ret_bars)
    mu  = ret.rolling(zscore_win, min_periods=zscore_win).mean()
    sig = ret.rolling(zscore_win, min_periods=zscore_win).std(ddof=0)
    z   = (ret - mu) / sig.replace(0, np.nan)
    return _signal(z < -z_thresh, z > z_thresh)


ROLLING_RETURN_ZSCORE = {
    "name": "rolling_return_zscore",
    "compute": _rolling_return_zscore,
    "param_grid": {
        "ret_bars":    [3, 5, 10],
        "zscore_win":  [30, 50],
        "z_thresh":    [1.5, 1.8, 2.0],
    },
}


# ── 7. Range Expansion Follow ─────────────────────────────────────────────────

def _range_expansion_follow(df, range_win=10, expansion_mult=1.5):
    """
    Current bar range (H-L) > expansion_mult × avg N-bar range → follow direction.
    Strong directional expansion bars tend to continue.
    """
    bar_range = df["high"] - df["low"]
    avg_range = bar_range.shift(1).rolling(range_win, min_periods=range_win).mean()
    expanded  = bar_range > (expansion_mult * avg_range)
    up_bar    = df["close"] > df["open"]
    down_bar  = df["close"] < df["open"]
    return _signal(expanded & up_bar, expanded & down_bar)


RANGE_EXPANSION_FOLLOW = {
    "name": "range_expansion_follow",
    "compute": _range_expansion_follow,
    "param_grid": {
        "range_win":       [5, 10, 20],
        "expansion_mult":  [1.3, 1.5, 2.0],
    },
}


# ── 8. MA Slope Regime ────────────────────────────────────────────────────────

def _ma_slope_regime(df, ma_win=20, slope_bars=5, entry_rsi_win=10, rsi_ob=60, rsi_os=40):
    """
    MA slope determines regime (positive = bull, negative = bear).
    In bull regime: enter long when RSI dips below rsi_os (pullback buy).
    In bear regime: enter short when RSI rises above rsi_ob (rally sell).
    """
    ma       = df["close"].rolling(ma_win, min_periods=ma_win).mean()
    slope_up = ma > ma.shift(slope_bars)
    rsi      = _rsi(df["close"], entry_rsi_win)
    long_sig  = slope_up  & (rsi < rsi_os)
    short_sig = ~slope_up & (rsi > rsi_ob)
    return _signal(long_sig, short_sig)


MA_SLOPE_REGIME = {
    "name": "ma_slope_regime",
    "compute": _ma_slope_regime,
    "param_grid": {
        "ma_win":        [15, 20, 30],
        "slope_bars":    [3, 5],
        "entry_rsi_win": [10, 14],
        "rsi_ob":        [60],
        "rsi_os":        [40],
    },
}


# ── 9. RSI Momentum (50-line cross) ───────────────────────────────────────────

def _rsi_momentum(df, rsi_win=14, smooth=3):
    """
    RSI crosses above 50 → long momentum. Crosses below 50 → short.
    Optional smoothing of RSI before cross detection.
    """
    rsi      = _rsi(df["close"], rsi_win)
    rsi_s    = rsi.rolling(smooth, min_periods=smooth).mean()
    cross_up  = (rsi_s > 50) & (rsi_s.shift(1) <= 50)
    cross_dn  = (rsi_s < 50) & (rsi_s.shift(1) >= 50)
    return _signal(cross_up, cross_dn)


RSI_MOMENTUM = {
    "name": "rsi_momentum",
    "compute": _rsi_momentum,
    "param_grid": {
        "rsi_win": [10, 14, 20],
        "smooth":  [1, 2, 3],
    },
}


# ── 10. Close-Position Momentum ───────────────────────────────────────────────

def _close_position_momentum(df, cp_window=3, cp_thresh=0.75):
    """
    Close position = (close - low) / (high - low). Values near 1 = strong up bar.
    N-bar average close position above cp_thresh → long. Below (1-cp_thresh) → short.
    """
    h_l  = (df["high"] - df["low"]).replace(0, np.nan)
    cp   = (df["close"] - df["low"]) / h_l
    avg_cp = cp.rolling(cp_window, min_periods=cp_window).mean()
    return _signal(avg_cp > cp_thresh, avg_cp < (1 - cp_thresh))


CLOSE_POSITION_MOMENTUM = {
    "name": "close_position_momentum",
    "compute": _close_position_momentum,
    "param_grid": {
        "cp_window": [2, 3, 5],
        "cp_thresh": [0.70, 0.75, 0.80],
    },
}


# ── Registry ──────────────────────────────────────────────────────────────────

STRAT_MAP_V8 = {
    "atr_compression_breakout":   ATR_COMPRESSION_BREAKOUT,
    "consecutive_close_momentum": CONSECUTIVE_CLOSE_MOMENTUM,
    "high_volume_continuation":   HIGH_VOLUME_CONTINUATION,
    "inside_bar_breakout":        INSIDE_BAR_BREAKOUT,
    "pivot_reversal":             PIVOT_REVERSAL,
    "rolling_return_zscore":      ROLLING_RETURN_ZSCORE,
    "range_expansion_follow":     RANGE_EXPANSION_FOLLOW,
    "ma_slope_regime":            MA_SLOPE_REGIME,
    "rsi_momentum":               RSI_MOMENTUM,
    "close_position_momentum":    CLOSE_POSITION_MOMENTUM,
}
STRATEGIES_V8 = list(STRAT_MAP_V8.values())
