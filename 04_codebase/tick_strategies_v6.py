"""
L2 Tick Strategy Library — V6: Session, VWAP, Prior-Day Level
=============================================================
10 new strategies using pure OHLCV + volume (no CVD required):

  1. vwap_reclaim_reject       — Daily VWAP reclaim long / reject short
  2. vwap_mean_reversion        — Price stretched from VWAP → fade back
  3. vwap_band_fade             — VWAP ± N×std band → mean revert
  4. prior_day_hl_breakout      — Prior day H/L breakout continuation
  5. prior_day_hl_sweep         — Prior day H/L swept (wick), then reversal
  6. opening_range_breakout     — First-N-bar session range → breakout trade
  7. opening_range_fakeout      — ORB initial move fails → fade
  8. overnight_gap_fill         — Session-open gap vs prior close → fade
  9. wick_reversal              — Large wick at session high/low extreme → reverse
 10. session_close_momentum     — Prior session closed near high/low → next bias
"""

import numpy as np
import pandas as pd


# ── Shared helpers ────────────────────────────────────────────────────────────

def _signal(long_cond, short_cond):
    s = pd.Series(0, index=long_cond.index, dtype=int)
    s[long_cond]  =  1
    s[short_cond] = -1
    return s


def _session_vwap(df):
    """Daily VWAP (typical price × volume, reset each UTC date)."""
    dates = df.index.normalize()
    tp    = (df["high"] + df["low"] + df["close"]) / 3
    tpv   = tp * df["volume"].clip(lower=0)
    cum_tpv = tpv.groupby(dates).cumsum()
    cum_vol = df["volume"].clip(lower=0).groupby(dates).cumsum()
    return (cum_tpv / cum_vol.replace(0, np.nan)).reindex(df.index)


def _prior_day_hl(df):
    """Previous TRADING day's high and low for each bar (no lookahead)."""
    dates      = df.index.normalize()
    daily_h    = df["high"].groupby(dates).max()
    daily_l    = df["low"].groupby(dates).min()
    unique_d   = sorted(daily_h.index.unique())
    ph, pl     = {}, {}
    for i, d in enumerate(unique_d):
        if i > 0:
            ph[d] = daily_h[unique_d[i - 1]]
            pl[d] = daily_l[unique_d[i - 1]]
        else:
            ph[d] = np.nan
            pl[d] = np.nan
    prev_h = pd.Series([ph.get(d, np.nan) for d in dates], index=df.index)
    prev_l = pd.Series([pl.get(d, np.nan) for d in dates], index=df.index)
    return prev_h, prev_l


def _prior_day_close(df):
    """Previous TRADING day's closing price for each bar (no lookahead)."""
    dates    = df.index.normalize()
    daily_c  = df["close"].groupby(dates).last()
    unique_d = sorted(daily_c.index.unique())
    pc       = {}
    for i, d in enumerate(unique_d):
        pc[d] = daily_c[unique_d[i - 1]] if i > 0 else np.nan
    return pd.Series([pc.get(d, np.nan) for d in dates], index=df.index)


def _opening_range_hl(df, n_bars):
    """First n_bars high/low of each UTC trading day (no lookahead)."""
    dates     = df.index.normalize()
    bar_rank  = df.groupby(dates).cumcount()   # 0-based within each day
    in_range  = bar_rank < n_bars
    sub       = df[in_range].copy()
    sub_dates = sub.index.normalize()
    day_h     = sub["high"].groupby(sub_dates).max()
    day_l     = sub["low"].groupby(sub_dates).min()
    # Signal only fires AFTER the opening range is complete
    orb_complete = bar_rank >= n_bars
    orh = pd.Series([day_h.get(d, np.nan) for d in dates], index=df.index)
    orl = pd.Series([day_l.get(d, np.nan) for d in dates], index=df.index)
    orh[~orb_complete] = np.nan
    orl[~orb_complete] = np.nan
    return orh, orl


def _atr(df, window=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


# ── 1. VWAP Reclaim / Reject ─────────────────────────────────────────────────

def vwap_reclaim_reject(df, min_dist_atr_pct=0.3, atr_window=14):
    """
    Long when close crosses above daily VWAP (reclaim).
    Short when close crosses below daily VWAP (reject).
    Requires at least min_dist_atr_pct × ATR distance in prior bar.
    """
    vwap   = _session_vwap(df)
    atr    = _atr(df, atr_window)
    above  = df["close"] > vwap
    below  = df["close"] < vwap
    prev_below = below.shift(1).fillna(False)
    prev_above = above.shift(1).fillna(False)
    # Reclaim: was below, now above, and prior bar was meaningfully below
    reclaim = (above & prev_below & ((vwap - df["close"].shift(1)) > min_dist_atr_pct * atr))
    reject  = (below & prev_above & ((df["close"].shift(1) - vwap) > min_dist_atr_pct * atr))
    return _signal(reclaim, reject)

VWAP_RECLAIM_REJECT = {
    "name": "vwap_reclaim_reject",
    "compute": vwap_reclaim_reject,
    "param_grid": {
        "min_dist_atr_pct": [0.1, 0.3, 0.5],
        "atr_window":       [10, 14],
    },
}


# ── 2. VWAP Mean Reversion ────────────────────────────────────────────────────

def vwap_mean_reversion(df, z_thresh=1.5, vwap_window=20):
    """
    When price z-score from daily VWAP exceeds thresh → fade back toward VWAP.
    """
    vwap  = _session_vwap(df)
    dev   = df["close"] - vwap
    z     = (dev - dev.rolling(vwap_window).mean()) / dev.rolling(vwap_window).std().replace(0, np.nan)
    long  = z < -z_thresh   # price too far below VWAP → long (revert up)
    short = z >  z_thresh   # price too far above VWAP → short (revert down)
    return _signal(long, short)

VWAP_MEAN_REVERSION = {
    "name": "vwap_mean_reversion",
    "compute": vwap_mean_reversion,
    "param_grid": {
        "z_thresh":    [1.0, 1.5, 2.0, 2.5],
        "vwap_window": [10, 20, 40],
    },
}


# ── 3. VWAP Band Fade ─────────────────────────────────────────────────────────

def vwap_band_fade(df, band_mult=1.5, std_window=20):
    """
    Compute rolling std of (close - VWAP). When price is at VWAP ± mult×std,
    fade back toward VWAP.
    """
    vwap = _session_vwap(df)
    dev  = df["close"] - vwap
    std  = dev.rolling(std_window).std().replace(0, np.nan)
    upper = vwap + band_mult * std
    lower = vwap - band_mult * std
    long  = df["close"] < lower    # at lower band → long
    short = df["close"] > upper    # at upper band → short
    return _signal(long, short)

VWAP_BAND_FADE = {
    "name": "vwap_band_fade",
    "compute": vwap_band_fade,
    "param_grid": {
        "band_mult":  [1.0, 1.5, 2.0, 2.5],
        "std_window": [10, 20, 40],
    },
}


# ── 4. Prior Day H/L Breakout (continuation) ─────────────────────────────────

def prior_day_hl_breakout(df, buffer_atr_pct=0.1, atr_window=14):
    """
    Close above prior day high → long (breakout continuation).
    Close below prior day low  → short.
    Optional ATR buffer to avoid marginal breaks.
    """
    prev_h, prev_l = _prior_day_hl(df)
    atr    = _atr(df, atr_window)
    buf    = buffer_atr_pct * atr
    long   = df["close"] > prev_h + buf
    short  = df["close"] < prev_l - buf
    return _signal(long.fillna(False), short.fillna(False))

PRIOR_DAY_HL_BREAKOUT = {
    "name": "prior_day_hl_breakout",
    "compute": prior_day_hl_breakout,
    "param_grid": {
        "buffer_atr_pct": [0.0, 0.1, 0.2],
        "atr_window":     [10, 14],
    },
}


# ── 5. Prior Day H/L Sweep Reversal ──────────────────────────────────────────

def prior_day_hl_sweep(df, wick_atr_pct=0.2, atr_window=14):
    """
    Bar wicks above prior day high but closes back below it → short (sweep fail).
    Bar wicks below prior day low but closes back above it → long (sweep fail).
    """
    prev_h, prev_l = _prior_day_hl(df)
    atr = _atr(df, atr_window)
    buf = wick_atr_pct * atr
    # Upside sweep: high pierced prev_h, close came back below it
    up_sweep   = (df["high"] > prev_h + buf) & (df["close"] < prev_h)
    # Downside sweep: low pierced prev_l, close came back above it
    down_sweep = (df["low"]  < prev_l - buf) & (df["close"] > prev_l)
    return _signal(down_sweep.fillna(False), up_sweep.fillna(False))

PRIOR_DAY_HL_SWEEP = {
    "name": "prior_day_hl_sweep",
    "compute": prior_day_hl_sweep,
    "param_grid": {
        "wick_atr_pct": [0.1, 0.2, 0.3],
        "atr_window":   [10, 14],
    },
}


# ── 6. Opening Range Breakout ─────────────────────────────────────────────────

def opening_range_breakout(df, orb_bars=6, buffer_atr_pct=0.0, atr_window=14):
    """
    First orb_bars of each day define the opening range.
    First close above range high → long; below range low → short.
    Signal resets each new day.
    """
    orh, orl = _opening_range_hl(df, orb_bars)
    atr       = _atr(df, atr_window)
    buf       = buffer_atr_pct * atr
    long  = (df["close"] > orh + buf) & orh.notna()
    short = (df["close"] < orl - buf) & orl.notna()
    # Only first signal per day
    dates    = df.index.normalize()
    day_rank = df.groupby(dates).cumcount()
    # Avoid holding into next day — signal cleared at day boundary
    long_first  = long  & ~long.shift(1).fillna(False)
    short_first = short & ~short.shift(1).fillna(False)
    return _signal(long_first, short_first)

OPENING_RANGE_BREAKOUT = {
    "name": "opening_range_breakout",
    "compute": opening_range_breakout,
    "param_grid": {
        "orb_bars":       [3, 6, 12],
        "buffer_atr_pct": [0.0, 0.1],
        "atr_window":     [14],
    },
}


# ── 7. Opening Range Fakeout (Reversal) ───────────────────────────────────────

def opening_range_fakeout(df, orb_bars=6, reentry_atr_pct=0.1, atr_window=14):
    """
    Price breaks above/below opening range but then closes BACK inside → fade.
    This captures the false breakout that occurs ~30-40% of sessions.
    """
    orh, orl = _opening_range_hl(df, orb_bars)
    atr       = _atr(df, atr_window)
    buf       = reentry_atr_pct * atr
    # Fakeout long: high exceeded orh, but close came back inside range
    fake_long  = (df["high"] > orh) & (df["close"] < orh - buf) & orh.notna()
    # Fakeout short: low breached orl, but close came back inside range
    fake_short = (df["low"]  < orl) & (df["close"] > orl + buf) & orl.notna()
    return _signal(fake_short.fillna(False), fake_long.fillna(False))

OPENING_RANGE_FAKEOUT = {
    "name": "opening_range_fakeout",
    "compute": opening_range_fakeout,
    "param_grid": {
        "orb_bars":        [3, 6, 12],
        "reentry_atr_pct": [0.05, 0.1, 0.2],
        "atr_window":      [14],
    },
}


# ── 8. Overnight Gap Fill ─────────────────────────────────────────────────────

def overnight_gap_fill(df, gap_atr_mult=0.5, atr_window=14):
    """
    Gap at session open (first bar open vs prior bar close) > gap_atr_mult × ATR.
    Fade the gap: if opened UP, go short (gap fill down); opened DOWN, go long.
    Signal fires only on first bar of each day.
    """
    atr       = _atr(df, atr_window)
    prev_close = _prior_day_close(df)
    dates     = df.index.normalize()
    bar_rank  = df.groupby(dates).cumcount()
    is_first  = bar_rank == 0
    gap       = df["open"] - prev_close
    gap_up    = (gap >  gap_atr_mult * atr) & is_first
    gap_down  = (gap < -gap_atr_mult * atr) & is_first
    return _signal(gap_down.fillna(False), gap_up.fillna(False))

OVERNIGHT_GAP_FILL = {
    "name": "overnight_gap_fill",
    "compute": overnight_gap_fill,
    "param_grid": {
        "gap_atr_mult": [0.3, 0.5, 0.75, 1.0],
        "atr_window":   [14],
    },
}


# ── 9. Wick Reversal at Session Extreme ───────────────────────────────────────

def wick_reversal(df, wick_ratio=0.6, lookback=20, atr_window=14):
    """
    When a bar has a dominant wick (> wick_ratio of total range) AND the bar
    body is near a recent extreme (rolling N-bar high/low), expect reversal.
    Upper wick dominant at rolling high → short.
    Lower wick dominant at rolling low  → long.
    """
    total_range = (df["high"] - df["low"]).replace(0, np.nan)
    upper_wick  = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick  = df[["open", "close"]].min(axis=1) - df["low"]
    upper_dom   = (upper_wick / total_range) > wick_ratio
    lower_dom   = (lower_wick / total_range) > wick_ratio
    roll_h      = df["high"].rolling(lookback).max()
    roll_l      = df["low"].rolling(lookback).min()
    at_high     = df["high"] >= roll_h
    at_low      = df["low"]  <= roll_l
    short = upper_dom & at_high
    long  = lower_dom & at_low
    return _signal(long, short)

WICK_REVERSAL = {
    "name": "wick_reversal",
    "compute": wick_reversal,
    "param_grid": {
        "wick_ratio": [0.5, 0.6, 0.7],
        "lookback":   [10, 20, 40],
        "atr_window": [14],
    },
}


# ── 10. Session Close Momentum ────────────────────────────────────────────────

def session_close_momentum(df, close_pct_thresh=0.7):
    """
    Prior trading day closed in top X% of its range → bullish bias next session.
    Prior trading day closed in bottom X% of its range → bearish bias.
    """
    dates    = df.index.normalize()
    daily_h  = df["high"].groupby(dates).max()
    daily_l  = df["low"].groupby(dates).min()
    daily_c  = df["close"].groupby(dates).last()
    unique_d = sorted(daily_h.index.unique())

    ph, pl, pc = {}, {}, {}
    for i, d in enumerate(unique_d):
        if i > 0:
            ph[d] = daily_h[unique_d[i - 1]]
            pl[d] = daily_l[unique_d[i - 1]]
            pc[d] = daily_c[unique_d[i - 1]]
        else:
            ph[d] = pl[d] = pc[d] = np.nan

    prev_h  = pd.Series([ph.get(d, np.nan) for d in dates], index=df.index)
    prev_l  = pd.Series([pl.get(d, np.nan) for d in dates], index=df.index)
    prev_c  = pd.Series([pc.get(d, np.nan) for d in dates], index=df.index)
    rng     = (prev_h - prev_l).replace(0, np.nan)
    close_p = (prev_c - prev_l) / rng   # 0=low end, 1=high end

    bull = close_p >= close_pct_thresh    # closed near top → continue up
    bear = close_p <= (1 - close_pct_thresh)  # closed near bottom → continue down
    return _signal(bull.fillna(False), bear.fillna(False))

SESSION_CLOSE_MOMENTUM = {
    "name": "session_close_momentum",
    "compute": session_close_momentum,
    "param_grid": {
        "close_pct_thresh": [0.6, 0.7, 0.75, 0.8],
    },
}


# ── Strategy map ──────────────────────────────────────────────────────────────

STRAT_MAP_V6 = {
    "vwap_reclaim_reject":    VWAP_RECLAIM_REJECT,
    "vwap_mean_reversion":    VWAP_MEAN_REVERSION,
    "vwap_band_fade":         VWAP_BAND_FADE,
    "prior_day_hl_breakout":  PRIOR_DAY_HL_BREAKOUT,
    "prior_day_hl_sweep":     PRIOR_DAY_HL_SWEEP,
    "opening_range_breakout": OPENING_RANGE_BREAKOUT,
    "opening_range_fakeout":  OPENING_RANGE_FAKEOUT,
    "overnight_gap_fill":     OVERNIGHT_GAP_FILL,
    "wick_reversal":          WICK_REVERSAL,
    "session_close_momentum": SESSION_CLOSE_MOMENTUM,
}

STRATEGIES_V6 = list(STRAT_MAP_V6.values())
