"""
L2 Tick Strategy Library — 30 Strategies
=========================================
Every strategy is a dict with:
  name         : unique key
  compute      : fn(df, **params) -> pd.Series of {-1, 0, +1}
  param_grid   : dict of parameter lists for grid search
  requires_mbp : True if needs obi_5 / book_pressure / spread columns
  description  : one-line explanation

Available feature columns (from tick_processor.py):
  ALL symbols : open, high, low, close, volume,
                buy_vol, sell_vol, cvd_delta, cvd,
                n_trades, trade_rate, large_buys, large_sells
  GC/SI only  : spread_mean, bid_sz_mean, ask_sz_mean, book_pressure, obi_5
"""

import numpy as np
import pandas as pd


# ── Rolling helpers ──────────────────────────────────────────────────────────

def _zscore(series: pd.Series, window: int) -> pd.Series:
    mu  = series.rolling(window).mean()
    sig = series.rolling(window).std()
    return (series - mu) / sig.replace(0, np.nan)


def _roc(series: pd.Series, n: int) -> pd.Series:
    return (series - series.shift(n)) / series.shift(n).replace(0, np.nan)


def _crossover(a: pd.Series, b) -> pd.Series:
    """Returns +1 on the bar a crosses above b, -1 below, else 0."""
    if isinstance(b, (int, float)):
        b = pd.Series(b, index=a.index)
    above_now  = (a >  b).astype(int)
    above_prev = (a.shift(1) > b.shift(1) if isinstance(b, pd.Series)
                  else a.shift(1) > b).astype(int)
    return (above_now - above_prev).clip(-1, 1)


def _rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).max()


def _rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).min()


def _signal(condition_long, condition_short) -> pd.Series:
    """Combine two boolean series into a {-1,0,+1} signal."""
    sig = pd.Series(0, index=condition_long.index)
    sig[condition_long]  =  1
    sig[condition_short] = -1
    return sig.fillna(0).astype(int)


# ============================================================================
# GROUP 1 — CVD-BASED  (6 strategies)
# ============================================================================

def cvd_divergence(df: pd.DataFrame,
                   price_window: int = 20,
                   cvd_window: int   = 20,
                   threshold: float  = 0.5) -> pd.Series:
    """
    Bearish: price makes rolling high but CVD makes rolling low.
    Bullish: price makes rolling low  but CVD makes rolling high.
    """
    price_hi = df["close"].rolling(price_window).max()
    price_lo = df["close"].rolling(price_window).min()
    cvd_hi   = df["cvd"].rolling(cvd_window).max()
    cvd_lo   = df["cvd"].rolling(cvd_window).min()

    at_price_hi = (df["close"] >= price_hi * (1 - threshold / 100))
    at_price_lo = (df["close"] <= price_lo * (1 + threshold / 100))
    cvd_lag      = df["cvd"] - df["cvd"].shift(cvd_window)

    bear_div = at_price_hi & (cvd_lag < 0)
    bull_div = at_price_lo & (cvd_lag > 0)
    return _signal(bull_div, bear_div)


def cvd_momentum(df: pd.DataFrame,
                 cvd_window: int   = 20,
                 z_entry: float    = 1.5,
                 z_exit: float     = 0.5) -> pd.Series:
    """Trend-follow when CVD z-score exceeds threshold."""
    z = _zscore(df["cvd_delta"], cvd_window)
    long_entry  = z >  z_entry
    short_entry = z < -z_entry
    sig = pd.Series(0, index=df.index)
    position = 0
    for i in range(len(sig)):
        if position == 0:
            if long_entry.iloc[i]:  position =  1
            elif short_entry.iloc[i]: position = -1
        elif position == 1:
            if z.iloc[i] < z_exit: position = 0
        else:
            if z.iloc[i] > -z_exit: position = 0
        sig.iloc[i] = position
    # Convert state to entry signals
    return sig.diff().clip(-1, 1).fillna(0).astype(int)


def cvd_breakout(df: pd.DataFrame,
                 breakout_window: int = 20,
                 confirm_bars: int    = 1) -> pd.Series:
    """Long when CVD breaks above N-bar high; short below N-bar low."""
    cvd_hi = df["cvd"].shift(1).rolling(breakout_window).max()
    cvd_lo = df["cvd"].shift(1).rolling(breakout_window).min()
    long_  = (df["cvd"] > cvd_hi)
    short_ = (df["cvd"] < cvd_lo)
    if confirm_bars > 1:
        long_  = long_.rolling(confirm_bars).min().astype(bool)
        short_ = short_.rolling(confirm_bars).min().astype(bool)
    return _signal(long_, short_)


def cvd_exhaustion(df: pd.DataFrame,
                   spike_window: int   = 5,
                   z_spike: float      = 2.5,
                   reversal_bars: int  = 3) -> pd.Series:
    """
    Extreme CVD spike followed by price NOT following.
    Buy after massive sell spike if close holds; sell after buy spike.
    """
    z = _zscore(df["cvd_delta"], 50)
    big_sell = (z < -z_spike)
    big_buy  = (z >  z_spike)
    # Reversal: after extreme spike, close is above / below spike bar close
    close_shift = df["close"].shift(reversal_bars)
    bull = big_sell.shift(reversal_bars) & (df["close"] > close_shift)
    bear = big_buy.shift(reversal_bars)  & (df["close"] < close_shift)
    return _signal(bull, bear)


def cvd_mean_reversion(df: pd.DataFrame,
                       window: int    = 30,
                       z_entry: float = 2.0) -> pd.Series:
    """Fade extreme CVD moves; bet on reversion to mean."""
    z = _zscore(df["cvd"], window)
    long_  = (z < -z_entry)  # oversold delta → expect bounce
    short_ = (z >  z_entry)  # overbought delta → expect fade
    return _signal(long_, short_)


def cvd_acceleration(df: pd.DataFrame,
                     roc_window: int  = 5,
                     z_threshold: float = 1.5) -> pd.Series:
    """Trade CVD rate-of-change (momentum in momentum)."""
    cvd_roc = _roc(df["cvd_delta"], roc_window)
    z = _zscore(cvd_roc, 30)
    return _signal(z > z_threshold, z < -z_threshold)


# ============================================================================
# GROUP 2 — ORDER BOOK IMBALANCE  (5 strategies, GC/SI only)
# ============================================================================

def obi_threshold(df: pd.DataFrame,
                  threshold: float = 0.5,
                  smooth_window: int = 3) -> pd.Series:
    """Simple OBI: strong bid imbalance → long; ask imbalance → short."""
    obi = df["obi_5"].rolling(smooth_window).mean()
    return _signal(obi > threshold, obi < -threshold)


def obi_momentum(df: pd.DataFrame,
                 obi_window: int    = 10,
                 z_threshold: float = 1.5) -> pd.Series:
    """Trade when OBI z-score is trending strongly."""
    z = _zscore(df["obi_5"], obi_window)
    return _signal(z > z_threshold, z < -z_threshold)


def obi_divergence(df: pd.DataFrame,
                   window: int      = 20,
                   threshold: float = 0.3) -> pd.Series:
    """
    Bullish: price falling but OBI strongly positive (buyers absorbing).
    Bearish: price rising but OBI strongly negative (sellers absorbing).
    """
    price_roc  = _roc(df["close"], window)
    obi_smooth = df["obi_5"].rolling(5).mean()
    bull = (price_roc < -threshold) & (obi_smooth >  0.3)
    bear = (price_roc >  threshold) & (obi_smooth < -0.3)
    return _signal(bull, bear)


def obi_mean_reversion(df: pd.DataFrame,
                       window: int    = 20,
                       z_entry: float = 2.0) -> pd.Series:
    """Fade extreme OBI readings — mean reversion in order book."""
    z = _zscore(df["obi_5"], window)
    # Extreme bid imbalance → expect asks to push back → short
    return _signal(z < -z_entry, z > z_entry)


def obi_breakout(df: pd.DataFrame,
                 breakout_window: int = 30) -> pd.Series:
    """OBI breaks to new extremes (persistence strategy)."""
    obi_hi = df["obi_5"].shift(1).rolling(breakout_window).max()
    obi_lo = df["obi_5"].shift(1).rolling(breakout_window).min()
    return _signal(df["obi_5"] > obi_hi, df["obi_5"] < obi_lo)


# ============================================================================
# GROUP 3 — BOOK PRESSURE / SPREAD  (3 strategies, GC/SI only)
# ============================================================================

def book_pressure_momentum(df: pd.DataFrame,
                           window: int    = 10,
                           z_threshold: float = 1.5) -> pd.Series:
    """Trade when top-of-book bid/ask pressure is trending."""
    z = _zscore(df["book_pressure"], window)
    return _signal(z > z_threshold, z < -z_threshold)


def book_pressure_reversion(df: pd.DataFrame,
                            window: int    = 20,
                            z_entry: float = 2.0) -> pd.Series:
    """Fade extreme top-of-book pressure."""
    z = _zscore(df["book_pressure"], window)
    return _signal(z < -z_entry, z > z_entry)


def spread_compression_breakout(df: pd.DataFrame,
                                spread_window: int = 20,
                                compression_pct: float = 0.3,
                                breakout_window: int   = 10) -> pd.Series:
    """
    When spread compresses below its rolling low × (1+compression_pct),
    trade in the direction of the price breakout.
    Tight spread = liquidity = imminent move.
    """
    spread_ma    = df["spread_mean"].rolling(spread_window).mean()
    spread_lo    = spread_ma.rolling(spread_window).min()
    compressed   = df["spread_mean"] < spread_lo * (1 + compression_pct)
    price_hi     = df["close"].rolling(breakout_window).max().shift(1)
    price_lo     = df["close"].rolling(breakout_window).min().shift(1)
    bull = compressed & (df["close"] > price_hi)
    bear = compressed & (df["close"] < price_lo)
    return _signal(bull, bear)


# ============================================================================
# GROUP 4 — LARGE PRINTS / INSTITUTIONAL FLOW  (5 strategies)
# ============================================================================

def large_print_momentum(df: pd.DataFrame,
                         window: int        = 5,
                         min_prints: int    = 2) -> pd.Series:
    """Follow direction when institutional prints cluster."""
    buy_cluster  = df["large_buys"].rolling(window).sum()
    sell_cluster = df["large_sells"].rolling(window).sum()
    bull = (buy_cluster  >= min_prints) & (buy_cluster  > sell_cluster)
    bear = (sell_cluster >= min_prints) & (sell_cluster > buy_cluster)
    return _signal(bull, bear)


def large_print_absorption(df: pd.DataFrame,
                           window: int         = 10,
                           z_price: float      = -0.5,
                           min_large_buys: int = 2) -> pd.Series:
    """
    Price dropping but large buy prints appearing → sellers being absorbed.
    Strong contrarian buy signal.
    """
    price_z       = _zscore(df["close"], window)
    large_buys_w  = df["large_buys"].rolling(window).sum()
    large_sells_w = df["large_sells"].rolling(window).sum()
    # Bullish absorption: price weak + large buying
    bull = (price_z < z_price) & (large_buys_w >= min_large_buys) & (large_buys_w > large_sells_w)
    # Bearish absorption: price strong + large selling
    bear = (price_z > -z_price) & (large_sells_w >= min_large_buys) & (large_sells_w > large_buys_w)
    return _signal(bull, bear)


def large_print_divergence(df: pd.DataFrame,
                           window: int         = 15,
                           price_pct: float    = 0.2) -> pd.Series:
    """
    Price making highs but large sellers dominating → bearish divergence.
    Price making lows but large buyers dominating → bullish divergence.
    """
    price_roc   = _roc(df["close"], window) * 100
    large_net   = df["large_buys"] - df["large_sells"]
    large_net_z = _zscore(large_net, window)
    bull = (price_roc < -price_pct) & (large_net_z >  1.0)
    bear = (price_roc >  price_pct) & (large_net_z < -1.0)
    return _signal(bull, bear)


def large_print_cluster(df: pd.DataFrame,
                        cluster_window: int = 3,
                        min_cluster: int    = 3,
                        confirm_window: int = 1) -> pd.Series:
    """3+ large prints same direction within N bars → high-conviction."""
    buy_cl  = df["large_buys"].rolling(cluster_window).sum()
    sell_cl = df["large_sells"].rolling(cluster_window).sum()
    bull = buy_cl  >= min_cluster
    bear = sell_cl >= min_cluster
    if confirm_window > 1:
        bull = bull.rolling(confirm_window).max().astype(bool)
        bear = bear.rolling(confirm_window).max().astype(bool)
    return _signal(bull, bear)


def large_print_imbalance(df: pd.DataFrame,
                          window: int     = 10,
                          ratio: float    = 2.0) -> pd.Series:
    """Long when large_buys >> large_sells by a factor of ratio."""
    buy_w  = df["large_buys"].rolling(window).sum() + 0.1
    sell_w = df["large_sells"].rolling(window).sum() + 0.1
    bull = buy_w  / sell_w > ratio
    bear = sell_w / buy_w  > ratio
    return _signal(bull, bear)


# ============================================================================
# GROUP 5 — TRADE FLOW / TAPE SPEED  (4 strategies)
# ============================================================================

def trade_acceleration(df: pd.DataFrame,
                       window: int    = 10,
                       z_threshold: float = 1.5) -> pd.Series:
    """Tape speed spike in direction of CVD → momentum."""
    rate_z = _zscore(df["n_trades"], window)
    cvd_positive = df["cvd_delta"] > 0
    cvd_negative = df["cvd_delta"] < 0
    bull = (rate_z > z_threshold) & cvd_positive
    bear = (rate_z > z_threshold) & cvd_negative
    return _signal(bull, bear)


def trade_deceleration(df: pd.DataFrame,
                       window: int    = 15,
                       z_threshold: float = -1.5) -> pd.Series:
    """
    Tape suddenly slows while price is extended → reversal likely.
    Classic exhaustion pattern.
    """
    rate_z    = _zscore(df["n_trades"], window)
    price_roc = _roc(df["close"], window)
    # Price up but tape slows → short
    bear = (rate_z < z_threshold) & (price_roc > 0.001)
    bull = (rate_z < z_threshold) & (price_roc < -0.001)
    return _signal(bull, bear)


def flow_momentum(df: pd.DataFrame,
                  window: int    = 20,
                  z_threshold: float = 1.0) -> pd.Series:
    """Composite: trade rate × CVD_delta as a flow strength measure."""
    flow  = df["n_trades"] * df["cvd_delta"]
    z     = _zscore(flow, window)
    return _signal(z > z_threshold, z < -z_threshold)


def tape_absorption(df: pd.DataFrame,
                    price_window: int = 10,
                    vol_z_threshold: float = 1.5,
                    price_threshold: float = 0.001) -> pd.Series:
    """
    High volume but price barely moves → absorption.
    Bias determined by CVD direction during absorption.
    """
    price_roc = abs(_roc(df["close"], price_window))
    vol_z     = _zscore(df["volume"], price_window)
    absorbing = (vol_z > vol_z_threshold) & (price_roc < price_threshold)
    cvd_pos   = df["cvd_delta"] > 0
    cvd_neg   = df["cvd_delta"] < 0
    bull = absorbing & cvd_pos
    bear = absorbing & cvd_neg
    return _signal(bull, bear)


# ============================================================================
# GROUP 6 — COMPOSITE MULTI-SIGNAL  (7 strategies)
# ============================================================================

def cvd_obi_confirmation(df: pd.DataFrame,
                         cvd_window: int    = 20,
                         cvd_z: float       = 1.0,
                         obi_threshold: float = 0.3) -> pd.Series:
    """Both CVD momentum and OBI must agree. Highest conviction entries."""
    cvd_z_series = _zscore(df["cvd_delta"], cvd_window)
    obi_smooth   = df["obi_5"].rolling(3).mean()
    bull = (cvd_z_series >  cvd_z) & (obi_smooth >  obi_threshold)
    bear = (cvd_z_series < -cvd_z) & (obi_smooth < -obi_threshold)
    return _signal(bull, bear)


def cvd_divergence_large_print(df: pd.DataFrame,
                                price_window: int   = 20,
                                cvd_window: int     = 20,
                                min_large: int      = 1) -> pd.Series:
    """
    CVD diverges from price AND confirmed by large institutional print.
    Strongest reversal signal in the set.
    """
    price_hi  = df["close"].rolling(price_window).max()
    price_lo  = df["close"].rolling(price_window).min()
    cvd_lag   = df["cvd"] - df["cvd"].shift(cvd_window)
    at_hi     = df["close"] >= price_hi * 0.999
    at_lo     = df["close"] <= price_lo * 1.001
    large_buy_recent  = df["large_buys"].rolling(3).sum()  >= min_large
    large_sell_recent = df["large_sells"].rolling(3).sum() >= min_large
    bull = at_lo & (cvd_lag > 0) & large_buy_recent
    bear = at_hi & (cvd_lag < 0) & large_sell_recent
    return _signal(bull, bear)


def obi_spread_breakout(df: pd.DataFrame,
                         obi_threshold: float = 0.4,
                         spread_window: int   = 20,
                         compression: float   = 0.2,
                         breakout_window: int = 5) -> pd.Series:
    """
    OBI strongly imbalanced + spread compressed + price breaking out.
    Three-factor filter for GC/SI only.
    """
    obi_strong   = df["obi_5"].rolling(3).mean()
    spread_lo    = df["spread_mean"].rolling(spread_window).min()
    compressed   = df["spread_mean"] < spread_lo * (1 + compression)
    price_hi     = df["close"].rolling(breakout_window).max().shift(1)
    price_lo     = df["close"].rolling(breakout_window).min().shift(1)
    bull = (obi_strong >  obi_threshold) & compressed & (df["close"] > price_hi)
    bear = (obi_strong < -obi_threshold) & compressed & (df["close"] < price_lo)
    return _signal(bull, bear)


def delta_exhaustion_reversal(df: pd.DataFrame,
                               cvd_z_window: int    = 30,
                               cvd_z_spike: float   = 2.5,
                               obi_threshold: float = 0.3) -> pd.Series:
    """
    Extreme CVD spike (exhaustion) + OBI flipping opposite direction.
    Requires mbp for OBI confirmation.
    """
    z       = _zscore(df["cvd_delta"], cvd_z_window)
    big_sell = z < -cvd_z_spike
    big_buy  = z >  cvd_z_spike
    obi_flip_pos = df["obi_5"] >  obi_threshold
    obi_flip_neg = df["obi_5"] < -obi_threshold
    # After extreme sell spike, if book is now net bid → buy
    bull = big_sell.shift(1) & obi_flip_pos
    bear = big_buy.shift(1)  & obi_flip_neg
    return _signal(bull, bear)


def absorption_pattern(df: pd.DataFrame,
                        price_window: int   = 5,
                        vol_z: float        = 1.5,
                        cvd_flip_bars: int  = 3,
                        obi_min: float      = 0.2) -> pd.Series:
    """
    Full absorption pattern:
      - High volume bar(s) with little price movement (absorption)
      - CVD was negative but starts turning positive (sellers exhausted)
      - OBI remains positive (passive buyers present)
    """
    price_range_z = _zscore(df["high"] - df["low"], 20)
    vol_z_series  = _zscore(df["volume"], 20)
    absorbing = (vol_z_series > vol_z) & (price_range_z < 0)

    cvd_turn_up = (df["cvd_delta"] > 0) & (df["cvd_delta"].shift(cvd_flip_bars) < 0)
    obi_pos     = df["obi_5"] > obi_min
    obi_neg     = df["obi_5"] < -obi_min
    cvd_turn_dn = (df["cvd_delta"] < 0) & (df["cvd_delta"].shift(cvd_flip_bars) > 0)

    bull = absorbing.shift(1) & cvd_turn_up & obi_pos
    bear = absorbing.shift(1) & cvd_turn_dn & obi_neg
    return _signal(bull, bear)


def stop_hunt_reversal(df: pd.DataFrame,
                        spike_bars: int       = 2,
                        spike_pct: float      = 0.002,
                        cvd_flip_window: int  = 3) -> pd.Series:
    """
    Price spikes aggressively through a level then snaps back with CVD reversal.
    Classic stop hunt / liquidity grab.
    """
    price_roc    = _roc(df["close"], spike_bars)
    big_up_spike = price_roc >  spike_pct
    big_dn_spike = price_roc < -spike_pct
    cvd_now      = df["cvd_delta"]
    cvd_prev     = df["cvd_delta"].shift(cvd_flip_window)
    cvd_flipped_neg = (cvd_now < 0) & (cvd_prev > 0)
    cvd_flipped_pos = (cvd_now > 0) & (cvd_prev < 0)
    # Price spiked up + CVD flipped to negative → sell the spike
    bear = big_up_spike.shift(1) & cvd_flipped_neg
    bull = big_dn_spike.shift(1) & cvd_flipped_pos
    return _signal(bull, bear)


def multi_factor_momentum(df: pd.DataFrame,
                           cvd_window: int      = 15,
                           cvd_z_min: float     = 0.8,
                           flow_window: int     = 10,
                           flow_z_min: float    = 0.8,
                           large_window: int    = 5) -> pd.Series:
    """
    Composite momentum: CVD momentum + flow momentum + large print confirmation.
    Works on all symbols (no mbp required).
    """
    cvd_z  = _zscore(df["cvd_delta"], cvd_window)
    flow   = df["n_trades"] * df["cvd_delta"]
    flow_z = _zscore(flow, flow_window)
    large_net = (df["large_buys"] - df["large_sells"]).rolling(large_window).sum()

    bull = (cvd_z >  cvd_z_min) & (flow_z >  flow_z_min) & (large_net >= 0)
    bear = (cvd_z < -cvd_z_min) & (flow_z < -flow_z_min) & (large_net <= 0)
    return _signal(bull, bear)


# ============================================================================
# STRATEGY REGISTRY
# ============================================================================

def _strategy(name, fn, param_grid, requires_mbp=False, description=""):
    return {
        "name": name,
        "compute": fn,
        "param_grid": param_grid,
        "requires_mbp": requires_mbp,
        "description": description,
    }


STRATEGIES = [
    # ── CVD ──────────────────────────────────────────────────────────────
    _strategy(
        "cvd_divergence", cvd_divergence,
        {"price_window": [10, 20, 40], "cvd_window": [10, 20, 40], "threshold": [0.3, 0.5, 1.0]},
        description="Price/CVD diverge at rolling extremes",
    ),
    _strategy(
        "cvd_momentum", cvd_momentum,
        {"cvd_window": [10, 20, 50], "z_entry": [1.0, 1.5, 2.0], "z_exit": [0.3, 0.5]},
        description="Follow CVD z-score momentum",
    ),
    _strategy(
        "cvd_breakout", cvd_breakout,
        {"breakout_window": [10, 20, 40], "confirm_bars": [1, 2]},
        description="CVD breaks N-bar high/low",
    ),
    _strategy(
        "cvd_exhaustion", cvd_exhaustion,
        {"spike_window": [3, 5], "z_spike": [2.0, 2.5, 3.0], "reversal_bars": [2, 3, 5]},
        description="Extreme CVD spike then price holds → reversal",
    ),
    _strategy(
        "cvd_mean_reversion", cvd_mean_reversion,
        {"window": [20, 40, 60], "z_entry": [1.5, 2.0, 2.5]},
        description="Fade extreme CVD z-score",
    ),
    _strategy(
        "cvd_acceleration", cvd_acceleration,
        {"roc_window": [3, 5, 10], "z_threshold": [1.0, 1.5, 2.0]},
        description="CVD rate-of-change momentum",
    ),

    # ── OBI (GC/SI only) ─────────────────────────────────────────────────
    _strategy(
        "obi_threshold", obi_threshold,
        {"threshold": [0.3, 0.5, 0.7], "smooth_window": [1, 3, 5]},
        requires_mbp=True,
        description="Order book imbalance threshold",
    ),
    _strategy(
        "obi_momentum", obi_momentum,
        {"obi_window": [5, 10, 20], "z_threshold": [1.0, 1.5, 2.0]},
        requires_mbp=True,
        description="OBI z-score momentum",
    ),
    _strategy(
        "obi_divergence", obi_divergence,
        {"window": [10, 20, 30], "threshold": [0.1, 0.2, 0.3]},
        requires_mbp=True,
        description="Price/OBI diverge (absorption signal)",
    ),
    _strategy(
        "obi_mean_reversion", obi_mean_reversion,
        {"window": [15, 30, 50], "z_entry": [1.5, 2.0, 2.5]},
        requires_mbp=True,
        description="Fade extreme OBI readings",
    ),
    _strategy(
        "obi_breakout", obi_breakout,
        {"breakout_window": [20, 30, 50]},
        requires_mbp=True,
        description="OBI breaks to new extremes (persistence)",
    ),

    # ── Book pressure / spread (GC/SI only) ──────────────────────────────
    _strategy(
        "book_pressure_momentum", book_pressure_momentum,
        {"window": [5, 10, 20], "z_threshold": [1.0, 1.5, 2.0]},
        requires_mbp=True,
        description="Top-of-book pressure z-score momentum",
    ),
    _strategy(
        "book_pressure_reversion", book_pressure_reversion,
        {"window": [15, 30], "z_entry": [1.5, 2.0, 2.5]},
        requires_mbp=True,
        description="Fade extreme top-of-book pressure",
    ),
    _strategy(
        "spread_compression_breakout", spread_compression_breakout,
        {"spread_window": [10, 20], "compression_pct": [0.1, 0.2, 0.3], "breakout_window": [5, 10]},
        requires_mbp=True,
        description="Spread compression precedes breakout",
    ),

    # ── Large prints ─────────────────────────────────────────────────────
    _strategy(
        "large_print_momentum", large_print_momentum,
        {"window": [3, 5, 10], "min_prints": [1, 2, 3]},
        description="Follow clustered institutional prints",
    ),
    _strategy(
        "large_print_absorption", large_print_absorption,
        {"window": [5, 10, 20], "z_price": [-0.3, -0.5, -0.8], "min_large_buys": [1, 2]},
        description="Large buys at weak price → absorption buy",
    ),
    _strategy(
        "large_print_divergence", large_print_divergence,
        {"window": [10, 15, 20], "price_pct": [0.1, 0.2, 0.3]},
        description="Price/institutional flow diverge",
    ),
    _strategy(
        "large_print_cluster", large_print_cluster,
        {"cluster_window": [2, 3, 5], "min_cluster": [2, 3], "confirm_window": [1, 2]},
        description="3+ large prints same direction in N bars",
    ),
    _strategy(
        "large_print_imbalance", large_print_imbalance,
        {"window": [5, 10, 20], "ratio": [1.5, 2.0, 3.0]},
        description="Large buy/sell ratio extreme",
    ),

    # ── Trade flow ───────────────────────────────────────────────────────
    _strategy(
        "trade_acceleration", trade_acceleration,
        {"window": [5, 10, 20], "z_threshold": [1.0, 1.5, 2.0]},
        description="Tape speed spike in CVD direction",
    ),
    _strategy(
        "trade_deceleration", trade_deceleration,
        {"window": [10, 15, 20], "z_threshold": [-1.0, -1.5, -2.0]},
        description="Tape slows while price extended → reversal",
    ),
    _strategy(
        "flow_momentum", flow_momentum,
        {"window": [10, 20, 30], "z_threshold": [0.8, 1.0, 1.5]},
        description="n_trades × cvd_delta composite momentum",
    ),
    _strategy(
        "tape_absorption", tape_absorption,
        {"price_window": [5, 10], "vol_z_threshold": [1.0, 1.5, 2.0], "price_threshold": [0.0005, 0.001, 0.002]},
        description="High volume + little price movement + CVD bias",
    ),

    # ── Composite ────────────────────────────────────────────────────────
    _strategy(
        "cvd_obi_confirmation", cvd_obi_confirmation,
        {"cvd_window": [10, 20], "cvd_z": [0.8, 1.0, 1.5], "obi_threshold": [0.2, 0.3, 0.5]},
        requires_mbp=True,
        description="CVD + OBI both agree (highest conviction)",
    ),
    _strategy(
        "cvd_divergence_large_print", cvd_divergence_large_print,
        {"price_window": [10, 20], "cvd_window": [10, 20], "min_large": [1, 2]},
        description="CVD diverges from price + institutional confirm",
    ),
    _strategy(
        "obi_spread_breakout", obi_spread_breakout,
        {"obi_threshold": [0.3, 0.4, 0.5], "spread_window": [10, 20], "compression": [0.1, 0.2], "breakout_window": [3, 5]},
        requires_mbp=True,
        description="OBI extreme + spread compressed + price breaks",
    ),
    _strategy(
        "delta_exhaustion_reversal", delta_exhaustion_reversal,
        {"cvd_z_window": [20, 30], "cvd_z_spike": [2.0, 2.5, 3.0], "obi_threshold": [0.2, 0.3, 0.4]},
        requires_mbp=True,
        description="Extreme CVD spike + OBI flips opposite",
    ),
    _strategy(
        "absorption_pattern", absorption_pattern,
        {"price_window": [3, 5], "vol_z": [1.0, 1.5, 2.0], "cvd_flip_bars": [2, 3], "obi_min": [0.1, 0.2]},
        requires_mbp=True,
        description="Full 3-factor absorption pattern",
    ),
    _strategy(
        "stop_hunt_reversal", stop_hunt_reversal,
        {"spike_bars": [1, 2, 3], "spike_pct": [0.001, 0.002, 0.003], "cvd_flip_window": [2, 3, 5]},
        description="Price spike + instant CVD reversal (stop hunt)",
    ),
    _strategy(
        "multi_factor_momentum", multi_factor_momentum,
        {"cvd_window": [10, 15], "cvd_z_min": [0.5, 0.8, 1.0],
         "flow_window": [5, 10], "flow_z_min": [0.5, 0.8], "large_window": [3, 5]},
        description="CVD + flow + large print composite momentum",
    ),
]


# ============================================================================
# GROUP 7 — ADVANCED ORDER FLOW  (6 strategies)
# VPIN, Stacked Imbalance, VWAP+CVD, Buying Climax, Level Delta Flip,
# Composite Score
# ============================================================================

def vpin_approximation(df: pd.DataFrame,
                       bucket_window: int  = 20,
                       vpin_threshold: float = 0.7,
                       trend_window: int   = 5) -> pd.Series:
    """
    VPIN (Volume-Synchronized Probability of Informed Trading) approximation.
    Easley, Lopez de Prado & O'Hara (2012).
    VPIN ≈ |buy_vol - sell_vol| / volume per bucket.
    High VPIN with directional bias → informed flow → follow direction.
    """
    total = df["volume"].replace(0, np.nan)
    vpin  = (df["buy_vol"] - df["sell_vol"]).abs() / total
    vpin_smooth = vpin.rolling(bucket_window).mean()
    cvd_dir     = df["cvd_delta"].rolling(trend_window).mean()
    high_vpin   = vpin_smooth > vpin_threshold
    bull = high_vpin & (cvd_dir > 0)
    bear = high_vpin & (cvd_dir < 0)
    return _signal(bull, bear)


def stacked_imbalance(df: pd.DataFrame,
                      stack_n: int       = 4,
                      min_delta: float   = 0.0,
                      confirm_vol: bool  = False) -> pd.Series:
    """
    N consecutive bars all with same-direction delta → structural imbalance.
    Footprint chart concept: consecutive bid/ask imbalances stack up.
    """
    delta = df["cvd_delta"]
    # All N bars in window are positive (buy imbalance)
    all_pos = (delta > min_delta).rolling(stack_n).min().astype(bool)
    all_neg = (delta < -min_delta).rolling(stack_n).min().astype(bool)
    if confirm_vol:
        vol_z = _zscore(df["volume"], 20)
        above_avg = vol_z > 0
        bull = all_pos & above_avg
        bear = all_neg & above_avg
    else:
        bull, bear = all_pos, all_neg
    return _signal(bull, bear)


def vwap_cvd_divergence(df: pd.DataFrame,
                         vwap_window: int     = 50,
                         dev_threshold: float = 0.001,
                         cvd_window: int      = 20,
                         cvd_z_thresh: float  = 1.0) -> pd.Series:
    """
    Price deviates from VWAP while CVD contradicts price direction.
    VWAP computed as volume-weighted rolling average.
    Price above VWAP but CVD falling → short.
    Price below VWAP but CVD rising → long.
    """
    # Rolling VWAP
    vwap = (df["close"] * df["volume"]).rolling(vwap_window).sum() / \
            df["volume"].rolling(vwap_window).sum()
    dev = (df["close"] - vwap) / vwap.replace(0, np.nan)
    cvd_z = _zscore(df["cvd_delta"], cvd_window)
    above_vwap = dev >  dev_threshold
    below_vwap = dev < -dev_threshold
    # Price above VWAP but delta turning negative → mean reversion short
    bear = above_vwap & (cvd_z < -cvd_z_thresh)
    bull = below_vwap & (cvd_z >  cvd_z_thresh)
    return _signal(bull, bear)


def buying_climax(df: pd.DataFrame,
                  vol_z_spike: float    = 2.5,
                  price_up_pct: float   = 0.001,
                  reversal_window: int  = 3) -> pd.Series:
    """
    Buying/selling climax: massive volume spike on price extension,
    immediately followed by price reversal. Classic Wyckoff pattern.
    """
    vol_z     = _zscore(df["volume"], 30)
    price_roc = _roc(df["close"], 1)
    big_vol   = vol_z > vol_z_spike
    # Climax up: huge volume on up bar, then reversal
    climax_up = big_vol & (price_roc >  price_up_pct)
    climax_dn = big_vol & (price_roc < -price_up_pct)
    # Signal fires N bars later when reversal is confirmed
    close_after = df["close"]
    close_at    = df["close"].shift(reversal_window)
    reversed_dn = climax_up.shift(reversal_window) & (close_after < close_at)
    reversed_up = climax_dn.shift(reversal_window) & (close_after > close_at)
    return _signal(reversed_up, reversed_dn)


def level_delta_flip(df: pd.DataFrame,
                      price_tolerance: float = 0.0005,
                      lookback: int          = 20,
                      flip_confirm: int      = 2) -> pd.Series:
    """
    Price revisits a recent key level (high/low) while CVD flips direction.
    When sellers fail to push price lower at a prior low + delta turns positive
    → absorption complete → long.
    """
    recent_hi = df["close"].rolling(lookback).max().shift(1)
    recent_lo = df["close"].rolling(lookback).min().shift(1)
    at_hi = (df["close"] >= recent_hi * (1 - price_tolerance))
    at_lo = (df["close"] <= recent_lo * (1 + price_tolerance))
    # Delta flip: was negative (sell pressure) now positive (buyers taking over)
    delta_now  = df["cvd_delta"].rolling(flip_confirm).mean()
    delta_prev = df["cvd_delta"].shift(flip_confirm).rolling(flip_confirm).mean()
    flip_pos = (delta_now > 0) & (delta_prev < 0)
    flip_neg = (delta_now < 0) & (delta_prev > 0)
    # At key low with delta flipping positive → buy
    bull = at_lo & flip_pos
    bear = at_hi & flip_neg
    return _signal(bull, bear)


def composite_order_flow_score(df: pd.DataFrame,
                                cvd_weight: float   = 1.0,
                                flow_weight: float  = 1.0,
                                large_weight: float = 1.0,
                                threshold: float    = 1.5) -> pd.Series:
    """
    Normalised composite of CVD momentum, flow momentum, and large print bias.
    Combines all three signals into a single directional score.
    Works on all symbols — no MBP needed.
    z-scores each component then weighted sum.
    """
    cvd_z   = _zscore(df["cvd_delta"], 20).fillna(0)
    flow    = (df["n_trades"] * df["cvd_delta"])
    flow_z  = _zscore(flow, 15).fillna(0)
    large_net = (df["large_buys"] - df["large_sells"]).rolling(5).sum()
    large_z = _zscore(large_net, 30).fillna(0)

    score = (cvd_z * cvd_weight + flow_z * flow_weight + large_z * large_weight) / \
            (cvd_weight + flow_weight + large_weight)

    return _signal(score > threshold, score < -threshold)


# Register the 6 new strategies
STRATEGIES += [
    _strategy(
        "vpin_approximation", vpin_approximation,
        {"bucket_window": [10, 20, 30], "vpin_threshold": [0.5, 0.7, 0.85], "trend_window": [3, 5]},
        description="VPIN informed trading proxy — directional bias when toxicity high",
    ),
    _strategy(
        "stacked_imbalance", stacked_imbalance,
        {"stack_n": [3, 4, 5], "min_delta": [0.0, 0.5]},
        description="N consecutive same-direction delta bars (footprint concept)",
    ),
    _strategy(
        "vwap_cvd_divergence", vwap_cvd_divergence,
        {"vwap_window": [30, 50, 100], "dev_threshold": [0.0005, 0.001, 0.002],
         "cvd_window": [15, 20], "cvd_z_thresh": [0.8, 1.0, 1.5]},
        description="Price deviates from VWAP while CVD contradicts it",
    ),
    _strategy(
        "buying_climax", buying_climax,
        {"vol_z_spike": [2.0, 2.5, 3.0], "price_up_pct": [0.0005, 0.001, 0.002],
         "reversal_window": [2, 3, 5]},
        description="Wyckoff buying/selling climax — huge volume + price reversal",
    ),
    _strategy(
        "level_delta_flip", level_delta_flip,
        {"price_tolerance": [0.0003, 0.0005, 0.001], "lookback": [10, 20, 30],
         "flip_confirm": [1, 2, 3]},
        description="Price at key level + CVD flips direction (absorption confirmed)",
    ),
    _strategy(
        "composite_order_flow_score", composite_order_flow_score,
        {"cvd_weight": [1.0, 2.0], "flow_weight": [1.0, 2.0],
         "large_weight": [0.5, 1.0], "threshold": [1.0, 1.5, 2.0]},
        description="Normalised composite: CVD + flow + large prints combined score",
    ),
]

# Rebuild lookup including new strategies
STRATEGY_MAP = {s["name"]: s for s in STRATEGIES}
