"""
L2 Tick Strategy Library — V4 (8 Deep Microstructure Strategies)
=================================================================
Focus: Features we haven't exploited yet:
  - n_trades (transaction count → institutional vs retail detection)
  - buy_vol / sell_vol ratio (normalized volume imbalance)
  - avg order size = cvd_delta / n_trades (institutional fingerprint)
  - Delta acceleration (second derivative of order flow)
  - Large print velocity (burst detection)
  - OBI persistence (GC/SI book pressure over time)
  - Volume absorption (high txns + small move = digestion)
  - Microstructure squeeze (compression then release)

These go deeper into the footprint than any previous strategy set.
"""

import numpy as np
import pandas as pd


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── 1. Average Order Size Divergence ─────────────────────────────────────────

def avg_order_size_divergence(df, window=20, z_thresh=1.0, price_thresh=0.001):
    """
    Institutional fingerprint: large average order size = smart money.
    avg_buy  = buy_vol  / n_trades  → mean buy transaction size
    avg_sell = sell_vol / n_trades  → mean sell transaction size

    Institutional buying (large avg buy) at price support → long.
    Institutional selling (large avg sell) at price resistance → short.
    Divergence: large avg buys while price makes new LOW = accumulation → long.
    """
    n_safe = df["n_trades"].replace(0, np.nan)
    avg_buy  = df["buy_vol"]  / n_safe
    avg_sell = df["sell_vol"] / n_safe
    size_ratio = (avg_buy - avg_sell) / (avg_buy + avg_sell + 1e-9)

    ratio_z = _zscore(size_ratio, window)
    price_roc = _roc(df["close"], window)

    # Bull: large avg buy size + price was weak (accumulation divergence)
    bull = (ratio_z >  z_thresh) & (price_roc < price_thresh)
    # Bear: large avg sell size + price was strong (distribution divergence)
    bear = (ratio_z < -z_thresh) & (price_roc > -price_thresh)

    return _signal(bull, bear)


# ── 2. Volume Ratio Persistence ───────────────────────────────────────────────

def volume_ratio_persistence(df, window=5, ratio_thresh=0.15, min_streak=3):
    """
    Normalized volume imbalance sustained over multiple bars.
    ratio = (buy_vol - sell_vol) / (buy_vol + sell_vol)

    If ratio > thresh for min_streak consecutive bars → persistent buying → long.
    If ratio < -thresh for min_streak consecutive bars → persistent selling → short.
    This detects sustained institutional activity, not one-bar spikes.
    """
    total = (df["buy_vol"] + df["sell_vol"]).replace(0, np.nan)
    ratio = (df["buy_vol"] - df["sell_vol"]) / total

    bull_bar = (ratio >  ratio_thresh).astype(int)
    bear_bar = (ratio < -ratio_thresh).astype(int)

    # Count consecutive runs
    bull_streak = bull_bar.rolling(min_streak).sum()
    bear_streak = bear_bar.rolling(min_streak).sum()

    bull_sig = (bull_streak == min_streak) & (bull_streak.shift(1) < min_streak)
    bear_sig = (bear_streak == min_streak) & (bear_streak.shift(1) < min_streak)

    return _signal(bull_sig, bear_sig)


# ── 3. Trade Absorption Signal ────────────────────────────────────────────────

def trade_absorption_signal(df, ntrades_z=1.2, range_z=-0.3, cvd_z=0.4, window=20):
    """
    Absorption: many transactions (high n_trades) but price barely moves.
    = Market is digesting supply/demand at a level → likely reversal.

    High n_trades zscore + small bar range + CVD turning = entry.
    Bullish absorption: many sells absorbed without price dropping → long.
    Bearish absorption: many buys absorbed without price rising → short.
    """
    n_z   = _zscore(df["n_trades"], window)
    rng   = df["high"] - df["low"]
    rng_z = _zscore(rng, window)
    dz    = _zscore(df["cvd_delta"], window)

    # High activity (lots of trades), small bar range = absorption
    absorbing = (n_z > ntrades_z) & (rng_z < range_z)

    # Bullish: absorbing + net positive CVD (buyers winning the absorption)
    bull = absorbing & (dz >  cvd_z)
    # Bearish: absorbing + net negative CVD (sellers winning the absorption)
    bear = absorbing & (dz < -cvd_z)

    return _signal(bull, bear)


# ── 4. Delta Acceleration Exhaustion ─────────────────────────────────────────

def delta_acceleration_exhaustion(df, accel_window=5, z_thresh=1.0, price_window=10):
    """
    Second derivative of cumulative delta (CVD acceleration).
    d²CVD/dt² = cvd_delta.diff()

    If price is rising AND delta acceleration turns negative → buyers exhausting → short.
    If price is falling AND delta acceleration turns positive → sellers exhausting → long.
    More sensitive than first-derivative CVD divergence.
    """
    # First derivative already in data as cvd_delta
    # Second derivative: acceleration of delta
    delta_accel = df["cvd_delta"].diff()
    accel_z     = _zscore(delta_accel, accel_window * 4)
    price_roc   = _roc(df["close"], price_window)
    price_roc_z = _zscore(price_roc, 40)

    # Bear: price momentum positive but delta decelerating sharply
    bear = (price_roc_z >  z_thresh) & (accel_z < -z_thresh)
    # Bull: price momentum negative but delta accelerating (sellers exhausting)
    bull = (price_roc_z < -z_thresh) & (accel_z >  z_thresh)

    return _signal(bull, bear)


# ── 5. Large Print Velocity Burst ─────────────────────────────────────────────

def large_print_velocity(df, burst_window=3, lookback=10, min_total=3, cvd_z=0.3):
    """
    Detects BURSTS of institutional activity (multiple large prints in quick succession).
    Not just a single large print, but a cluster = aggressive institutional sweep.

    large_buy_burst  = sum of large_buys over burst_window bars
    If burst is abnormally high vs recent history AND CVD confirms → momentum entry.
    """
    buy_burst  = df["large_buys"].rolling(burst_window).sum()
    sell_burst = df["large_sells"].rolling(burst_window).sum()

    buy_burst_z  = _zscore(buy_burst,  lookback)
    sell_burst_z = _zscore(sell_burst, lookback)
    cvdz         = _zscore(df["cvd_delta"], lookback)

    # Burst: abnormally high cluster of large prints in direction
    bull = (buy_burst >= min_total) & (buy_burst_z > 1.0) & (cvdz >  cvd_z)
    bear = (sell_burst >= min_total) & (sell_burst_z > 1.0) & (cvdz < -cvd_z)

    # Only on first bar of burst
    bull_new = bull & ~bull.shift(1).fillna(False)
    bear_new = bear & ~bear.shift(1).fillna(False)

    return _signal(bull_new, bear_new)


# ── 6. OBI Trend Momentum (GC/SI only) ────────────────────────────────────────

def obi_trend_momentum(df, obi_window=10, obi_z=0.8, price_z=0.3):
    """
    Order Book Imbalance (OBI) from top 5 levels — only available on GC/SI.
    obi_5 > 0 = more bids than offers = market is bid up
    obi_5 < 0 = more offers than bids = market is offered down

    Strategy: OBI trend in one direction + price breakout = momentum trade.
    OBI reversal at extreme = fade trade.
    """
    if "obi_5" not in df.columns:
        return pd.Series(0, index=df.index)

    obi      = df["obi_5"]
    obi_ma   = obi.rolling(obi_window).mean()
    obi_z    = _zscore(obi, obi_window * 2)
    price_roc = _roc(df["close"], obi_window)
    price_roc_z = _zscore(price_roc, 40)

    # Trend momentum: OBI consistently positive + price confirming
    bull = (obi_ma > 0) & (obi_z >  obi_z) & (price_roc_z >  price_z)
    bear = (obi_ma < 0) & (obi_z < -obi_z) & (price_roc_z < -price_z)

    return _signal(bull, bear)


# ── 7. Microstructure Compression Breakout ────────────────────────────────────

def microstructure_compression(df, vol_window=20, compression_pct=25, cvd_z=0.5, breakout_z=0.8):
    """
    Detects microstructure 'squeeze': period of unusually balanced volume
    (buy_vol ≈ sell_vol, low |ratio|), then sudden imbalance break.

    Like a coiled spring: the longer the compression, the larger the move.
    Uses volume ratio variance as compression measure.
    """
    total = (df["buy_vol"] + df["sell_vol"]).replace(0, np.nan)
    ratio = (df["buy_vol"] - df["sell_vol"]).abs() / total

    # Compression: rolling variance of ratio is very low
    ratio_var    = ratio.rolling(vol_window).std()
    ratio_var_pct = ratio_var.rolling(vol_window * 2).rank(pct=True) * 100

    in_compression   = ratio_var_pct < compression_pct
    leaving_compress = in_compression.shift(1) & ~in_compression

    # Current bar: big imbalance (compression released)
    ratio_signed = (df["buy_vol"] - df["sell_vol"]) / total
    ratio_z_now  = _zscore(ratio_signed, vol_window)
    cvdz         = _zscore(df["cvd_delta"], vol_window)

    bull = leaving_compress & (ratio_z_now >  breakout_z) & (cvdz >  cvd_z)
    bear = leaving_compress & (ratio_z_now < -breakout_z) & (cvdz < -cvd_z)

    return _signal(bull, bear)


# ── 8. Book Pressure Reversal (GC/SI only, or proxy) ─────────────────────────

def book_pressure_reversal(df, bp_window=15, extreme_z=1.5, reversal_z=0.5):
    """
    Book pressure (bid_size / ask_size) extremes followed by reversal.
    When the book shows overwhelmingly one-sided interest but price fails to
    follow through → trapped participants → reversal opportunity.

    Falls back to buy_vol/sell_vol ratio if book_pressure not available.
    """
    if "book_pressure" in df.columns:
        pressure = df["book_pressure"]
    else:
        ask = df["sell_vol"].replace(0, np.nan)
        pressure = df["buy_vol"] / ask

    pressure_z = _zscore(pressure, bp_window)
    cvdz       = _zscore(df["cvd_delta"], bp_window)

    # Was book heavily bid (high pressure) but delta is now negative → trapped longs → short
    was_bid  = pressure_z.shift(1) >  extreme_z
    was_sold = pressure_z.shift(1) < -extreme_z

    reversal_short = was_bid  & (cvdz < -reversal_z) & (df["close"] < df["open"])
    reversal_long  = was_sold & (cvdz >  reversal_z) & (df["close"] > df["open"])

    return _signal(reversal_long, reversal_short)


# ── Strategy Registry ──────────────────────────────────────────────────────────

STRATEGIES_V4 = [
    {
        "name": "avg_order_size_divergence",
        "compute": avg_order_size_divergence,
        "param_grid": {
            "window":       [15, 20, 30],
            "z_thresh":     [0.8, 1.0, 1.5],
            "price_thresh": [0.0005, 0.001, 0.002],
        },
        "requires_mbp": False,
        "description": "Avg order size (buy_vol/n_trades) divergence — institutional fingerprint",
    },
    {
        "name": "volume_ratio_persistence",
        "compute": volume_ratio_persistence,
        "param_grid": {
            "ratio_thresh": [0.10, 0.15, 0.20],
            "min_streak":   [2, 3, 4],
        },
        "requires_mbp": False,
        "description": "Sustained buy/sell volume imbalance over multiple bars",
    },
    {
        "name": "trade_absorption_signal",
        "compute": trade_absorption_signal,
        "param_grid": {
            "ntrades_z": [0.8, 1.2, 1.5],
            "range_z":   [-0.5, -0.3, 0.0],
            "cvd_z":     [0.3, 0.4, 0.6],
        },
        "requires_mbp": False,
        "description": "High n_trades + small range + CVD turning = absorption reversal",
    },
    {
        "name": "delta_acceleration_exhaustion",
        "compute": delta_acceleration_exhaustion,
        "param_grid": {
            "accel_window": [3, 5, 8],
            "z_thresh":     [0.8, 1.0, 1.5],
            "price_window": [5, 10, 15],
        },
        "requires_mbp": False,
        "description": "d²(CVD)/dt² turns against price trend = momentum exhaustion",
    },
    {
        "name": "large_print_velocity",
        "compute": large_print_velocity,
        "param_grid": {
            "burst_window": [2, 3, 5],
            "min_total":    [2, 3, 4],
            "cvd_z":        [0.2, 0.3, 0.5],
        },
        "requires_mbp": False,
        "description": "Cluster/burst of large institutional prints = aggressive sweep",
    },
    {
        "name": "obi_trend_momentum",
        "compute": obi_trend_momentum,
        "param_grid": {
            "obi_window": [5, 10, 15],
            "obi_z":      [0.5, 0.8, 1.2],
            "price_z":    [0.2, 0.3, 0.5],
        },
        "requires_mbp": True,
        "description": "OBI (order book imbalance top 5) trend momentum — GC/SI only",
    },
    {
        "name": "microstructure_compression",
        "compute": microstructure_compression,
        "param_grid": {
            "compression_pct": [20, 25, 35],
            "cvd_z":           [0.3, 0.5, 0.8],
            "breakout_z":      [0.5, 0.8, 1.2],
        },
        "requires_mbp": False,
        "description": "Volume ratio compression (coil) → sudden release = breakout",
    },
    {
        "name": "book_pressure_reversal",
        "compute": book_pressure_reversal,
        "param_grid": {
            "bp_window":   [10, 15, 20],
            "extreme_z":   [1.2, 1.5, 2.0],
            "reversal_z":  [0.3, 0.5, 0.8],
        },
        "requires_mbp": False,
        "description": "Extreme book pressure followed by reversal delta = trapped side",
    },
]

STRAT_MAP_V4: dict = {s["name"]: s for s in STRATEGIES_V4}
