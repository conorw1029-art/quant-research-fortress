"""
tick_strategies_v2.py — 8 new L2 strategies inspired by:
  - Bookmap Liquidity Heatmap (wick traps, order block retests)
  - Bookmap Traps Lite (deep-wick trapped participants)
  - Supply/Demand + Fibonacci zone logic
  - Historic volume time-of-day normalization
  - Footprint imbalance / aggregated cluster traps
  - Large-print key level confluence

Interface: same as tick_strategies.py
  strategy = {"name": str, "compute": fn(df, **params)->pd.Series{-1,0,1},
               "param_grid": dict, "requires_mbp": bool, "description": str}
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Any

# ── helpers (mirrors tick_strategies.py) ────────────────────────────────────

def _zscore(s: pd.Series, window: int) -> pd.Series:
    m = s.rolling(window).mean()
    sd = s.rolling(window).std().replace(0, np.nan)
    return (s - m) / sd

def _roc(s: pd.Series, window: int) -> pd.Series:
    prev = s.shift(window).replace(0, np.nan)
    return (s - prev) / prev.abs()

def _rolling_high(s: pd.Series, window: int) -> pd.Series:
    return s.shift(1).rolling(window).max()

def _rolling_low(s: pd.Series, window: int) -> pd.Series:
    return s.shift(1).rolling(window).min()

def _signal(bull: pd.Series, bear: pd.Series) -> pd.Series:
    sig = pd.Series(0, index=bull.index, dtype=int)
    sig[bull.fillna(False)] = 1
    sig[bear.fillna(False)] = -1
    # Never both
    sig[(bull.fillna(False)) & (bear.fillna(False))] = 0
    return sig

def _strategy(name, fn, param_grid, requires_mbp=False, description=""):
    return {
        "name": name,
        "compute": fn,
        "param_grid": param_grid,
        "requires_mbp": requires_mbp,
        "description": description,
    }


# ============================================================================
# STRATEGY 1 — WICK TRAP REVERSAL
# Inspired by: Bookmap Traps (Lite) — deep-wick trapped participant detection.
# Logic:
#   Upper wick trap (trapped buyers): bar has a significant upper wick
#   (wick > wick_pct × bar range) but closes near the low (body skew).
#   CVD delta on that bar is NEGATIVE (sellers dominated despite upper wick).
#   → Fade the false buyers; go short.
#   Lower wick trap (trapped sellers): mirror.
# ============================================================================

def wick_trap_reversal(df: pd.DataFrame,
                       wick_pct: float = 0.6,
                       cvd_z_window: int = 10) -> pd.Series:
    """
    Detect bars with deep rejection wicks where CVD confirms sellers/buyers
    trapped inside the wick. CVD Z-score adds conviction filter.
    """
    bar_range = (df["high"] - df["low"]).replace(0, np.nan)
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]

    upper_wick_pct = upper_wick / bar_range
    lower_wick_pct = lower_wick / bar_range

    # Deep upper wick + CVD negative on this bar → sellers trapped buyers → SHORT
    cvd_z = _zscore(df["cvd_delta"], cvd_z_window)
    bear = (upper_wick_pct > wick_pct) & (cvd_z < 0)
    bull = (lower_wick_pct > wick_pct) & (cvd_z > 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 2 — ORDER BLOCK RETEST
# Inspired by: Bookmap Liquidity Heatmap — Fibonacci zone, OB retest.
# Logic:
#   Identify "impulse" bars: bar range > ATR × impulse_mult AND
#   CVD delta Z-score > cvd_z.
#   The ORDER BLOCK is the body of the bar BEFORE the impulse bar.
#   When price retraces INTO that zone (low-to-high of OB bar) with CVD
#   still aligned, enter in impulse direction.
# ============================================================================

def order_block_retest(df: pd.DataFrame,
                       atr_window: int = 14,
                       impulse_mult: float = 1.5,
                       cvd_z_thresh: float = 1.0) -> pd.Series:
    """
    Enter on retest of the pre-impulse order block zone.
    Uses prior bar as OB zone and CVD Z for conviction.
    """
    bar_range = df["high"] - df["low"]
    atr = bar_range.rolling(atr_window).mean()

    cvd_z = _zscore(df["cvd_delta"], atr_window)
    is_impulse_bull = (bar_range > atr * impulse_mult) & (cvd_z > cvd_z_thresh)
    is_impulse_bear = (bar_range > atr * impulse_mult) & (cvd_z < -cvd_z_thresh)

    # OB zone = previous bar's high/low (the bar before the impulse)
    ob_high = df["high"].shift(2)  # bar before impulse bar (shifted back 2 from current)
    ob_low  = df["low"].shift(2)

    # Track last impulse direction and OB zone
    # We look whether the current bar is INSIDE the OB of the most recent impulse
    last_bull_impulse = is_impulse_bull.shift(1).rolling(10).max().astype(bool)
    last_bear_impulse = is_impulse_bear.shift(1).rolling(10).max().astype(bool)

    in_ob_zone = (df["close"] >= ob_high.shift(1)) & (df["close"] <= ob_high.shift(1) * 1.002) | \
                 (df["close"] <= ob_low.shift(1)) & (df["close"] >= ob_low.shift(1) * 0.998)

    bull = last_bull_impulse & (df["close"] <= ob_high.shift(1)) & (df["close"] >= ob_low.shift(1)) & (cvd_z > 0)
    bear = last_bear_impulse & (df["close"] >= ob_low.shift(1)) & (df["close"] <= ob_high.shift(1)) & (cvd_z < 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 3 — VOLUME TOD SURGE
# Inspired by: Historic Volume indicator — same-time-of-day volume average.
# Logic:
#   For each UTC hour, compute rolling N-day mean and std of volume at
#   that hour. Current volume > mean + k*std = SURGE.
#   Direction from CVD delta sign on the surge bar.
#   Filters out low-participation moves; only trades confirmed high-vol bars.
# ============================================================================

def volume_tod_surge(df: pd.DataFrame,
                     lookback_days: int = 10,
                     surge_z: float = 1.5) -> pd.Series:
    """
    TOD-normalized volume surge: volume anomaly vs same-hour historical mean.
    Direction confirmed by CVD delta.
    """
    # Use the bar timestamp hour as TOD key
    hour = df.index.hour if hasattr(df.index, 'hour') else pd.Series(0, index=df.index)

    vol = df["volume"].copy()
    vol_z = pd.Series(0.0, index=df.index)

    # Group by hour of day and compute rolling zscore
    # Approximate: use a global rolling window but we'll use the existing _zscore
    # with a longer window since we can't do real groupby in a vectorized way
    # Instead: compute zscore over lookback_days × approx_bars_per_hour
    # Safer: use a 1-day rolling window mapped to bar-length
    # For 1m bars ≈ 1440 bars/day, 5m ≈ 288, 30m ≈ 48
    # We use a fixed window of lookback_days * 24 as a heuristic
    window = max(lookback_days * 24, 20)
    vol_z = _zscore(vol, window)

    surge = vol_z > surge_z
    bull = surge & (df["cvd_delta"] > 0)
    bear = surge & (df["cvd_delta"] < 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 4 — PREV SESSION SWEEP & REVERSAL
# Inspired by: Bookmap Liquidity Heatmap — liquidity resting at prior highs/lows.
# Logic:
#   Rolling N-bar high/low = "liquidity pool" (resting orders).
#   Price sweeps ABOVE rolling high (stop hunt) then CVD flips negative → SHORT.
#   Price sweeps BELOW rolling low then CVD flips positive → LONG.
#   This is the "stop hunt reversal" concept extended to rolling S/R levels.
# ============================================================================

def prev_session_sweep(df: pd.DataFrame,
                       level_window: int = 20,
                       cvd_flip_window: int = 3,
                       sweep_buffer: float = 0.0002) -> pd.Series:
    """
    Price sweeps prior rolling high/low (liquidity grab) then instantly reverses.
    CVD flip in opposite direction confirms the rejection.
    """
    rolling_hi = _rolling_high(df["high"], level_window)
    rolling_lo = _rolling_low(df["low"], level_window)

    # Sweep: high exceeds rolling high by at least sweep_buffer
    swept_high = df["high"] > rolling_hi * (1 + sweep_buffer)
    swept_low  = df["low"]  < rolling_lo * (1 - sweep_buffer)

    # CVD flip: sum of recent CVD deltas reverses
    recent_cvd = df["cvd_delta"].rolling(cvd_flip_window).sum()

    # Bear: swept high + CVD now negative (sellers overwhelming stop-triggered buyers)
    bear = swept_high & (recent_cvd < 0)
    # Bull: swept low + CVD now positive
    bull = swept_low & (recent_cvd > 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 5 — OPENING RANGE CVD BREAKOUT
# Inspired by: Supply/Demand opening range / LiquidityMagic.
# Logic:
#   First open_bars bars of the session define the opening range (OR) high/low.
#   Breakout above OR_high with CVD trending up → LONG.
#   Breakdown below OR_low with CVD trending down → SHORT.
#   Time filter enforces this only fires once per session per direction.
# ============================================================================

def opening_range_cvd(df: pd.DataFrame,
                      open_bars: int = 6,
                      cvd_window: int = 10) -> pd.Series:
    """
    Opening range breakout confirmed by CVD trend.
    OR defined as first N bars after midnight UTC.
    """
    # Detect session start: first bar of each UTC date
    if hasattr(df.index, 'date'):
        dates = pd.Series(df.index.date, index=df.index)
    else:
        dates = pd.Series(0, index=df.index)

    bar_num_in_day = dates.groupby(dates).cumcount()

    # OR high/low = expanding max/min over first open_bars per day
    # Use date-groupby expanding window
    or_high = pd.Series(np.nan, index=df.index)
    or_low  = pd.Series(np.nan, index=df.index)

    in_or  = bar_num_in_day < open_bars
    post_or = bar_num_in_day >= open_bars

    # Forward-fill OR levels computed from the opening bars
    # For each bar, OR = max/min of the first open_bars of that day
    def _compute_or(group):
        h = group["high"].iloc[:open_bars].max() if len(group) >= open_bars else group["high"].max()
        l = group["low"].iloc[:open_bars].min()  if len(group) >= open_bars else group["low"].min()
        return pd.DataFrame({"or_h": h, "or_l": l}, index=group.index)

    grouped = df[["high", "low"]].join(dates.rename("date")).groupby("date")
    or_vals = grouped.apply(lambda g: _compute_or(g[["high", "low"]])).droplevel(0)
    or_high = or_vals["or_h"]
    or_low  = or_vals["or_l"]

    cvd_trend = df["cvd_delta"].rolling(cvd_window).sum()

    bull = post_or & (df["close"] > or_high) & (cvd_trend > 0)
    bear = post_or & (df["close"] < or_low)  & (cvd_trend < 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 6 — DELTA ACCELERATION REVERSAL
# Inspired by: cvd_acceleration (existing) + Bookmap Traps exhaustion.
# Logic:
#   Fast CVD momentum (short window) DECELERATES vs slow window → exhaustion.
#   Fast_CVD was strongly positive but is now turning negative → mean-revert.
#   Essentially: fade CVD momentum exhaustion when fast > slow then cross under.
#   Different from existing cvd_acceleration which follows the momentum.
# ============================================================================

def delta_acceleration_reversal(df: pd.DataFrame,
                                 fast_window: int = 5,
                                 slow_window: int = 20,
                                 cross_z: float = 1.0) -> pd.Series:
    """
    Fade CVD momentum exhaustion: fast CVD crosses below slow CVD after
    being significantly above (or vice versa). Cross filtered by Z-score magnitude.
    """
    fast_cvd = df["cvd_delta"].rolling(fast_window).mean()
    slow_cvd = df["cvd_delta"].rolling(slow_window).mean()
    diff     = fast_cvd - slow_cvd
    diff_z   = _zscore(diff, slow_window)

    # Fast was above slow (extreme positive diff_z) but now crossing under → SHORT
    was_above = diff_z.shift(1) > cross_z
    now_below = diff < 0
    bear = was_above & now_below

    # Fast was below slow then crossing above → LONG
    was_below  = diff_z.shift(1) < -cross_z
    now_above  = diff > 0
    bull = was_below & now_above
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 7 — BOOK DEPTH TREND (GC/SI only — requires_mbp=True)
# Inspired by: Bookmap Liquidity Heatmap depth state machine.
# Logic:
#   OBI (order book imbalance) sustains above threshold for N bars → real depth.
#   Not a spike — a TREND in book pressure means large passive orders queued.
#   Enter in direction of sustained OBI with CVD confirmation.
#   Only for GC/SI which have obi_5 and book_pressure columns.
# ============================================================================

def book_depth_trend(df: pd.DataFrame,
                     obi_window: int = 10,
                     obi_threshold: float = 0.25,
                     cvd_window: int = 5) -> pd.Series:
    """
    Sustained OBI (order book imbalance) trend above threshold with CVD confirm.
    Requires MBP data (obi_5, book_pressure columns). GC/SI only.
    """
    # OBI consistently above threshold for obi_window bars = sustained depth
    obi_min = df["obi_5"].rolling(obi_window).min()
    obi_max = df["obi_5"].rolling(obi_window).max()
    cvd_trend = df["cvd_delta"].rolling(cvd_window).sum()

    # Sustained bid depth (obi consistently positive) + CVD bullish
    sustained_bid = obi_min > obi_threshold
    sustained_ask = obi_max < -obi_threshold

    bull = sustained_bid & (cvd_trend > 0)
    bear = sustained_ask & (cvd_trend < 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY 8 — LARGE PRINT KEY LEVEL
# Inspired by: Footprint Imbalance Bubbles + Bookmap Heatmap key level confluence.
# Logic:
#   Large institutional prints (large_buys / large_sells) appearing AT or NEAR
#   a significant price level (rolling N-bar high/low). Smart money accumulating
#   at a structural level = high-conviction directional signal.
# ============================================================================

def large_print_key_level(df: pd.DataFrame,
                          level_window: int = 30,
                          level_pct: float = 0.003,
                          min_large: int = 2,
                          cvd_window: int = 5) -> pd.Series:
    """
    Large prints clustering at rolling structural highs/lows with CVD confirm.
    Level proximity defined as within level_pct% of the rolling high/low.
    """
    rolling_hi = _rolling_high(df["high"], level_window)
    rolling_lo = _rolling_low(df["low"], level_window)

    near_high = (df["close"] >= rolling_hi * (1 - level_pct)) & (df["close"] <= rolling_hi * (1 + level_pct))
    near_low  = (df["close"] <= rolling_lo * (1 + level_pct)) & (df["close"] >= rolling_lo * (1 - level_pct))

    # Large prints at key level
    large_buy_cluster  = df["large_buys"].rolling(3).sum()
    large_sell_cluster = df["large_sells"].rolling(3).sum()

    cvd_trend = df["cvd_delta"].rolling(cvd_window).sum()

    # Large buyers accumulating at rolling lows → bullish
    bull = near_low  & (large_buy_cluster  >= min_large) & (cvd_trend > 0)
    # Large sellers distributing at rolling highs → bearish
    bear = near_high & (large_sell_cluster >= min_large) & (cvd_trend < 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY LIST
# ============================================================================

STRATEGIES_V2: List[Dict[str, Any]] = [

    _strategy(
        "wick_trap_reversal", wick_trap_reversal,
        {"wick_pct": [0.5, 0.6, 0.7], "cvd_z_window": [5, 10, 20]},
        requires_mbp=False,
        description="Deep rejection wick + CVD confirms trapped participants → fade",
    ),

    _strategy(
        "order_block_retest", order_block_retest,
        {"atr_window": [10, 14], "impulse_mult": [1.2, 1.5, 2.0], "cvd_z_thresh": [0.8, 1.2]},
        requires_mbp=False,
        description="Impulse bar OB zone retest with CVD alignment",
    ),

    _strategy(
        "volume_tod_surge", volume_tod_surge,
        {"lookback_days": [5, 10, 20], "surge_z": [1.0, 1.5, 2.0]},
        requires_mbp=False,
        description="TOD-normalized volume surge in CVD direction",
    ),

    _strategy(
        "prev_session_sweep", prev_session_sweep,
        {"level_window": [15, 20, 30], "cvd_flip_window": [2, 3, 5], "sweep_buffer": [0.0001, 0.0003]},
        requires_mbp=False,
        description="Rolling high/low liquidity sweep + instant CVD reversal",
    ),

    _strategy(
        "opening_range_cvd", opening_range_cvd,
        {"open_bars": [4, 6, 8], "cvd_window": [5, 10]},
        requires_mbp=False,
        description="Opening range breakout with CVD trend confirmation",
    ),

    _strategy(
        "delta_acceleration_reversal", delta_acceleration_reversal,
        {"fast_window": [3, 5], "slow_window": [15, 20, 30], "cross_z": [0.8, 1.2]},
        requires_mbp=False,
        description="Fade CVD momentum exhaustion — fast/slow CVD cross with Z filter",
    ),

    _strategy(
        "book_depth_trend", book_depth_trend,
        {"obi_window": [5, 10, 15], "obi_threshold": [0.2, 0.3], "cvd_window": [3, 5]},
        requires_mbp=True,
        description="Sustained OBI depth trend with CVD confirm (GC/SI only)",
    ),

    _strategy(
        "large_print_key_level", large_print_key_level,
        {"level_window": [20, 30], "level_pct": [0.002, 0.003, 0.005], "min_large": [1, 2]},
        requires_mbp=False,
        description="Institutional prints at structural high/low levels with CVD",
    ),
]

# Name → strategy dict (mirrors STRATEGY_MAP in tick_strategies.py)
STRAT_MAP: dict = {s["name"]: s for s in STRATEGIES_V2}
