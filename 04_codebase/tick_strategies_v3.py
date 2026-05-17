"""
L2 Tick Strategy Library — V3 (10 new strategies)
===================================================
New strategy concepts:
  1. break_retest_cvd          — Level break → pullback → CVD absorbs → entry
  2. opening_range_bias        — ORB with session CVD bias filter
  3. delta_exhaustion_level    — Buying/selling climax at rolling extremes
  4. wick_delta_trap           — Wick candle + delta divergence (footprint trap)
  5. vwap_stretch_reversal     — Price stretched from VWAP + delta turning
  6. session_momentum_follow   — First-bar session delta → momentum follow-through
  7. large_print_at_level      — Large prints ONLY when price at key level
  8. consecutive_delta_flip    — N bars same-direction delta → exhaustion flip
  9. range_contraction_break   — Squeeze + CVD breakout confirmation
 10. cvd_roc_divergence        — Price momentum diverges from CVD ROC
"""

import numpy as np
import pandas as pd


# ── Helpers ──────────────────────────────────────────────────────────────────

def _zscore(series, window):
    mu  = series.rolling(window).mean()
    sig = series.rolling(window).std()
    return (series - mu) / sig.replace(0, np.nan)


def _roc(series, n):
    return (series - series.shift(n)) / series.shift(n).replace(0, np.nan)


def _rolling_high(s, n):
    return s.rolling(n).max()


def _rolling_low(s, n):
    return s.rolling(n).min()


def _signal(cond_long, cond_short):
    sig = pd.Series(0, index=cond_long.index)
    sig[cond_long]  =  1
    sig[cond_short] = -1
    return sig.fillna(0).astype(int)


def _atr(df, window=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


# ── 1. Break & Retest with CVD ───────────────────────────────────────────────

def break_retest_cvd(df, level_window=20, retest_bars=5, atr_mult=0.5, cvd_z=0.3):
    """
    1) Detect structural break: close crosses rolling high/low
    2) Within retest_bars after break, price pulls back near the level
    3) CVD confirms accumulation (z-score positive after upward break)
    """
    atr   = _atr(df, 14)
    rh    = _rolling_high(df["close"], level_window).shift(1)
    rl    = _rolling_low(df["close"], level_window).shift(1)
    cvd_z = _zscore(df["cvd_delta"], level_window)

    # Breakout detection
    bull_break = df["close"] > rh
    bear_break = df["close"] < rl

    # Propagate break flag for retest_bars
    bull_flag = bull_break.rolling(retest_bars).max().fillna(0).astype(bool)
    bear_flag = bear_break.rolling(retest_bars).max().fillna(0).astype(bool)

    # Retest: price returns near level but hasn't re-broken
    near_rh = (df["low"] <= rh + atr * atr_mult) & (df["close"] > rh - atr * atr_mult)
    near_rl = (df["high"] >= rl - atr * atr_mult) & (df["close"] < rl + atr * atr_mult)

    long_sig  = bull_flag & near_rh & (cvd_z > cvd_z) & ~bull_break
    short_sig = bear_flag & near_rl & (cvd_z < -cvd_z) & ~bear_break

    # Fix: use threshold directly
    cvdz = _zscore(df["cvd_delta"], level_window)
    long_sig  = bull_flag & near_rh & (cvdz >  0.2) & ~bull_break
    short_sig = bear_flag & near_rl & (cvdz < -0.2) & ~bear_break

    return _signal(long_sig, short_sig)


# ── 2. Opening Range Breakout + CVD Bias ────────────────────────────────────

def opening_range_bias(df, or_bars=2, cvd_z_thresh=0.5, breakout_pct=0.0003):
    """
    First or_bars establish opening range.
    Break + CVD confirmation = entry in breakout direction.
    Daily bias: only trade in direction of first-bar CVD.
    """
    # Use time index to identify session opens (UTC 14:00 for ES/NQ, 13:30 ≈ 14 UTC)
    if hasattr(df.index, 'hour'):
        session_open = (df.index.hour == 14) & (df.index.minute == 0)
    else:
        session_open = pd.Series(False, index=df.index)

    # Rolling OR: high/low of previous or_bars in each day
    # Simple proxy: if we can't identify session, use first bars of each day
    if hasattr(df.index, 'date'):
        df2 = df.copy()
        df2["_date"] = df.index.date
        df2["_bar_n"] = df2.groupby("_date").cumcount()
        or_high = df2.groupby("_date")["high"].transform(lambda x: x.iloc[:or_bars].max())
        or_low  = df2.groupby("_date")["low"].transform(lambda x: x.iloc[:or_bars].min())
        first_cvd = df2.groupby("_date")["cvd_delta"].transform("first")
        bar_n = df2["_bar_n"]
    else:
        or_high   = df["high"].rolling(or_bars).max()
        or_low    = df["low"].rolling(or_bars).min()
        first_cvd = df["cvd_delta"]
        bar_n     = pd.Series(range(len(df)), index=df.index)

    cvdz = _zscore(df["cvd_delta"], 20)

    # Only trade after OR is established
    after_or = bar_n >= or_bars

    bull_break = after_or & (df["close"] > or_high * (1 + breakout_pct)) & (cvdz > cvd_z_thresh)
    bear_break = after_or & (df["close"] < or_low  * (1 - breakout_pct)) & (cvdz < -cvd_z_thresh)

    # Bias filter: only long if first bar bullish CVD
    bull_bias = first_cvd > 0
    bear_bias = first_cvd < 0

    return _signal(bull_break & bull_bias, bear_break & bear_bias)


# ── 3. Delta Exhaustion at Key Levels ────────────────────────────────────────

def delta_exhaustion_level(df, level_window=20, delta_z=1.5, proximity_pct=0.002):
    """
    At rolling highs/lows, detect abnormally large delta (exhaustion/climax).
    Buying climax at high → short. Selling climax at low → long.
    """
    rh   = _rolling_high(df["high"], level_window).shift(1)
    rl   = _rolling_low(df["low"],   level_window).shift(1)
    dz   = _zscore(df["cvd_delta"], level_window)
    atr  = _atr(df, 14)

    # Near key level
    at_high = (df["high"] >= rh * (1 - proximity_pct)) & (df["high"] <= rh * (1 + proximity_pct))
    at_low  = (df["low"]  <= rl * (1 + proximity_pct)) & (df["low"]  >= rl * (1 - proximity_pct))

    # Exhaustion: extreme delta at level, then next bar reversal
    buy_climax   = at_high & (dz >  delta_z) & (df["close"] < df["open"])  # bearish bar after buy climax
    sell_climax  = at_low  & (dz < -delta_z) & (df["close"] > df["open"])  # bullish bar after sell climax

    return _signal(sell_climax, buy_climax)


# ── 4. Wick Delta Trap (Footprint-Inspired) ──────────────────────────────────

def wick_delta_trap(df, wick_ratio=1.5, delta_z=0.8, vol_z=0.5):
    """
    Candle with significant wick + delta divergence = trapped participants.
    Upper wick + negative delta → buyers trapped → short.
    Lower wick + positive delta → sellers trapped → long.
    """
    body       = (df["close"] - df["open"]).abs()
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    body_safe  = body.replace(0, np.nan)

    upper_dominant = upper_wick > lower_wick * wick_ratio
    lower_dominant = lower_wick > upper_wick * wick_ratio

    dz  = _zscore(df["cvd_delta"], 20)
    vz  = _zscore(df["volume"], 20)

    # Upper wick (potential short): wick big + delta was positive but closed weak
    trapped_buyers  = upper_dominant & (dz < -delta_z) & (vz > vol_z) & (df["close"] < df["open"])
    # Lower wick (potential long): wick big + delta was negative but closed strong
    trapped_sellers = lower_dominant & (dz >  delta_z) & (vz > vol_z) & (df["close"] > df["open"])

    return _signal(trapped_sellers, trapped_buyers)


# ── 5. VWAP Stretch Reversal ─────────────────────────────────────────────────

def vwap_stretch_reversal(df, vwap_atr_mult=2.0, delta_z=0.5, confirm_bars=2):
    """
    Price stretched far from VWAP + delta turning opposite direction.
    Mean reversion entry.
    """
    # Intraday VWAP approximation: daily cumulative VWAP
    if hasattr(df.index, 'date'):
        df2 = df.copy()
        df2["_date"] = df.index.date
        df2["_tp"]   = (df["high"] + df["low"] + df["close"]) / 3 * df["volume"]
        df2["_cvol"] = df2.groupby("_date")["volume"].cumsum()
        df2["_ctp"]  = df2.groupby("_date")["_tp"].cumsum()
        vwap = df2["_ctp"] / df2["_cvol"]
    else:
        tp   = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (tp * df["volume"]).cumsum() / df["volume"].cumsum()

    atr  = _atr(df, 14)
    dz   = _zscore(df["cvd_delta"], 20)

    above_vwap = df["close"] > vwap + atr * vwap_atr_mult
    below_vwap = df["close"] < vwap - atr * vwap_atr_mult

    # Delta must be turning: recent delta z-score negative despite being above VWAP
    delta_neg = dz < -delta_z
    delta_pos = dz >  delta_z

    # Confirm signal holds for confirm_bars (rolling min/max)
    long_raw  = (below_vwap & delta_pos).rolling(confirm_bars).max().fillna(0).astype(bool)
    short_raw = (above_vwap & delta_neg).rolling(confirm_bars).max().fillna(0).astype(bool)

    # Only signal on bar where condition first becomes true
    long_sig  = long_raw  & ~long_raw.shift(1).fillna(False)
    short_sig = short_raw & ~short_raw.shift(1).fillna(False)

    return _signal(long_sig, short_sig)


# ── 6. Session Momentum Follow-Through ───────────────────────────────────────

def session_momentum_follow(df, session_hour=14, bias_z=1.0, follow_bars=8, break_pct=0.0002):
    """
    First bar after session open has strong CVD → bias set.
    Within follow_bars, enter on price breakout in bias direction.
    """
    if not hasattr(df.index, 'hour'):
        return pd.Series(0, index=df.index)

    is_first_bar = (df.index.hour == session_hour) & (df.index.minute < 30)
    if hasattr(df.index, 'date'):
        df2 = df.copy()
        df2["_date"] = df.index.date
        df2["_bar_n"] = df2.groupby("_date").cumcount()
        first_bar_mask = df2["_bar_n"] == 0
    else:
        first_bar_mask = is_first_bar

    cvdz = _zscore(df["cvd_delta"], 20)

    # First bar delta direction becomes daily bias
    first_bar_long  = first_bar_mask & (cvdz >  bias_z)
    first_bar_short = first_bar_mask & (cvdz < -bias_z)

    # Propagate bias for follow_bars
    long_bias  = first_bar_long.rolling(follow_bars).max().fillna(0).astype(bool)
    short_bias = first_bar_short.rolling(follow_bars).max().fillna(0).astype(bool)

    # Price must also break first-bar high/low
    first_high = df["high"].where(first_bar_mask).ffill()
    first_low  = df["low"].where(first_bar_mask).ffill()

    long_entry  = long_bias  & (df["close"] > first_high * (1 + break_pct)) & ~first_bar_mask
    short_entry = short_bias & (df["close"] < first_low  * (1 - break_pct)) & ~first_bar_mask

    return _signal(long_entry, short_entry)


# ── 7. Large Print at Key Level ──────────────────────────────────────────────

def large_print_at_level(df, level_window=20, proximity_pct=0.003, min_large=2, cvd_z=0.3):
    """
    Large prints (institutional orders) only meaningful at key structural levels.
    Large buys at support → long. Large sells at resistance → short.
    More selective than raw large_print strategies.
    """
    rh  = _rolling_high(df["high"], level_window).shift(1)
    rl  = _rolling_low(df["low"],   level_window).shift(1)
    atr = _atr(df, 14)

    at_resist = (df["high"] >= rh * (1 - proximity_pct))
    at_support = (df["low"]  <= rl * (1 + proximity_pct))

    # Large prints: cumulative over recent bars
    lb_z = _zscore(df["large_buys"],  20)
    ls_z = _zscore(df["large_sells"], 20)
    cvdz = _zscore(df["cvd_delta"], 20)

    # Large buys at support with positive CVD → long
    long_sig  = at_support & (df["large_buys"] >= min_large) & (lb_z > 0.5) & (cvdz >  cvd_z)
    # Large sells at resistance with negative CVD → short
    short_sig = at_resist  & (df["large_sells"] >= min_large) & (ls_z > 0.5) & (cvdz < -cvd_z)

    return _signal(long_sig, short_sig)


# ── 8. Consecutive Delta Flip (Exhaustion) ───────────────────────────────────

def consecutive_delta_flip(df, streak=3, flip_z=0.5, price_thresh=0.001):
    """
    After N consecutive bars of same-sign delta (accumulation/distribution),
    detect the first bar of opposite delta → exhaustion reversal.
    Price must not have moved much during streak (accumulation without follow).
    """
    delta = df["cvd_delta"]
    pos = (delta > 0).astype(int)
    neg = (delta < 0).astype(int)

    # Count consecutive positive/negative streaks
    pos_streak = pos.rolling(streak).sum()
    neg_streak = neg.rolling(streak).sum()

    # Streak broken: was all-same last N bars, now opposite
    bull_streak_end = (pos_streak.shift(1) == streak) & (delta < 0)
    bear_streak_end = (neg_streak.shift(1) == streak) & (delta > 0)

    # Price movement during streak was small (absorption without follow-through)
    price_roc = _roc(df["close"], streak).abs()
    low_move  = price_roc < price_thresh

    dz = _zscore(delta, 20)

    # Flip magnitude
    long_flip  = bear_streak_end & low_move & (dz >  flip_z)
    short_flip = bull_streak_end & low_move & (dz < -flip_z)

    return _signal(long_flip, short_flip)


# ── 9. Range Contraction Breakout ────────────────────────────────────────────

def range_contraction_break(df, atr_window=14, squeeze_window=20, squeeze_pct=30,
                             breakout_z=1.0, cvd_z=0.5):
    """
    Identify periods of volatility compression (ATR percentile < squeeze_pct%).
    When range expands AND CVD confirms direction → breakout entry.
    Classic 'squeeze + order flow' setup.
    """
    atr  = _atr(df, atr_window)
    atr_pct = atr.rolling(squeeze_window).rank(pct=True) * 100

    in_squeeze    = atr_pct < squeeze_pct
    leaving_squeeze = in_squeeze.shift(1) & ~in_squeeze

    # Range expansion: current bar range vs ATR
    bar_range = df["high"] - df["low"]
    range_z   = _zscore(bar_range, squeeze_window)
    cvdz      = _zscore(df["cvd_delta"], squeeze_window)

    long_break  = leaving_squeeze & (range_z > breakout_z) & (cvdz >  cvd_z) & (df["close"] > df["open"])
    short_break = leaving_squeeze & (range_z > breakout_z) & (cvdz < -cvd_z) & (df["close"] < df["open"])

    return _signal(long_break, short_break)


# ── 10. CVD Rate-of-Change Divergence ────────────────────────────────────────

def cvd_roc_divergence(df, price_window=10, cvd_window=10, roc_thresh=0.3):
    """
    Price momentum (ROC) diverges from CVD ROC.
    Price making new highs but CVD momentum decelerating → short.
    Price making new lows but CVD momentum recovering → long.
    """
    price_roc = _roc(df["close"], price_window)
    cvd_roc   = _roc(df["cvd"],   cvd_window)

    price_roc_z = _zscore(price_roc, 40)
    cvd_roc_z   = _zscore(cvd_roc,   40)

    # Bearish divergence: price ROC positive, CVD ROC falling
    bear_div = (price_roc_z >  roc_thresh) & (cvd_roc_z < -roc_thresh)
    # Bullish divergence: price ROC negative, CVD ROC rising
    bull_div = (price_roc_z < -roc_thresh) & (cvd_roc_z >  roc_thresh)

    return _signal(bull_div, bear_div)


# ── Strategy Registry ─────────────────────────────────────────────────────────

STRATEGIES_V3 = [
    {
        "name": "break_retest_cvd",
        "compute": break_retest_cvd,
        "param_grid": {
            "level_window":  [15, 20, 30],
            "retest_bars":   [3, 5, 8],
            "atr_mult":      [0.3, 0.5, 0.8],
        },
        "requires_mbp": False,
        "description": "Level break → pullback retest → CVD absorbs → entry",
    },
    {
        "name": "opening_range_bias",
        "compute": opening_range_bias,
        "param_grid": {
            "or_bars":       [1, 2, 3],
            "cvd_z_thresh":  [0.3, 0.5, 0.8],
            "breakout_pct":  [0.0002, 0.0003, 0.0005],
        },
        "requires_mbp": False,
        "description": "ORB + session CVD bias filter — trade breakout in bias direction",
    },
    {
        "name": "delta_exhaustion_level",
        "compute": delta_exhaustion_level,
        "param_grid": {
            "level_window":   [15, 20, 30],
            "delta_z":        [1.2, 1.5, 2.0],
            "proximity_pct":  [0.001, 0.002, 0.003],
        },
        "requires_mbp": False,
        "description": "Buying/selling climax (extreme delta) at rolling extremes",
    },
    {
        "name": "wick_delta_trap",
        "compute": wick_delta_trap,
        "param_grid": {
            "wick_ratio": [1.2, 1.5, 2.0],
            "delta_z":    [0.5, 0.8, 1.2],
            "vol_z":      [0.3, 0.5, 0.8],
        },
        "requires_mbp": False,
        "description": "Wick candle + delta divergence = footprint trap reversal",
    },
    {
        "name": "vwap_stretch_reversal",
        "compute": vwap_stretch_reversal,
        "param_grid": {
            "vwap_atr_mult": [1.5, 2.0, 2.5],
            "delta_z":       [0.3, 0.5, 0.8],
            "confirm_bars":  [1, 2, 3],
        },
        "requires_mbp": False,
        "description": "Price far from VWAP + delta turning → mean reversion",
    },
    {
        "name": "session_momentum_follow",
        "compute": session_momentum_follow,
        "param_grid": {
            "bias_z":      [0.5, 1.0, 1.5],
            "follow_bars": [4, 8, 12],
            "break_pct":   [0.0001, 0.0002, 0.0003],
        },
        "requires_mbp": False,
        "description": "Session open delta bias → follow momentum breakout",
    },
    {
        "name": "large_print_at_level",
        "compute": large_print_at_level,
        "param_grid": {
            "level_window":   [15, 20, 30],
            "proximity_pct":  [0.002, 0.003, 0.005],
            "min_large":      [1, 2, 3],
        },
        "requires_mbp": False,
        "description": "Institutional large prints at key structural levels only",
    },
    {
        "name": "consecutive_delta_flip",
        "compute": consecutive_delta_flip,
        "param_grid": {
            "streak":        [2, 3, 4],
            "flip_z":        [0.3, 0.5, 0.8],
            "price_thresh":  [0.0005, 0.001, 0.002],
        },
        "requires_mbp": False,
        "description": "N consecutive same-sign delta → exhaustion flip signal",
    },
    {
        "name": "range_contraction_break",
        "compute": range_contraction_break,
        "param_grid": {
            "squeeze_pct":  [20, 30, 40],
            "breakout_z":   [0.5, 1.0, 1.5],
            "cvd_z":        [0.3, 0.5, 0.8],
        },
        "requires_mbp": False,
        "description": "ATR squeeze + expansion with CVD breakout confirmation",
    },
    {
        "name": "cvd_roc_divergence",
        "compute": cvd_roc_divergence,
        "param_grid": {
            "price_window": [5, 10, 15],
            "cvd_window":   [5, 10, 15],
            "roc_thresh":   [0.2, 0.3, 0.5],
        },
        "requires_mbp": False,
        "description": "Price ROC diverges from CVD ROC → momentum exhaustion",
    },
]

STRAT_MAP_V3: dict = {s["name"]: s for s in STRATEGIES_V3}
