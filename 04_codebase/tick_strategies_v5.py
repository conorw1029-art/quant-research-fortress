"""
L2 Tick Strategy Library — V5 (1 Strategy: key_level_cvd_rejection)
====================================================================

AUDIT SUMMARY — All existing strategies by volume-usage category
-----------------------------------------------------------------
CATEGORY A — Pure CVD signals (use cvd / cvd_delta only):
  v1: cvd_divergence, cvd_momentum, cvd_breakout, cvd_exhaustion,
      cvd_mean_reversion, cvd_acceleration, cvd_roc_divergence (v3)
  v3: consecutive_delta_flip (cvd_delta only)

CATEGORY B — Volume + CVD (use buy_vol, sell_vol, volume, cvd_delta):
  v1: tape_absorption, flow_momentum, trade_acceleration, trade_deceleration,
      vpin_approximation, stacked_imbalance, vwap_cvd_divergence, buying_climax,
      stop_hunt_reversal, multi_factor_momentum, composite_order_flow_score
  v2: wick_trap_reversal, order_block_retest, volume_tod_surge,
      prev_session_sweep, opening_range_cvd, delta_acceleration_reversal,
      large_print_key_level
  v3: break_retest_cvd, opening_range_bias, delta_exhaustion_level,
      wick_delta_trap, vwap_stretch_reversal, session_momentum_follow,
      large_print_at_level, range_contraction_break
  v4: avg_order_size_divergence, volume_ratio_persistence,
      trade_absorption_signal, delta_acceleration_exhaustion,
      large_print_velocity, microstructure_compression, book_pressure_reversal

CATEGORY C — OBI / book-pressure only (GC/SI, requires_mbp=True):
  v1: obi_threshold, obi_momentum, obi_divergence, obi_mean_reversion,
      obi_breakout, book_pressure_momentum, book_pressure_reversion,
      spread_compression_breakout, cvd_obi_confirmation, obi_spread_breakout,
      delta_exhaustion_reversal, absorption_pattern
  v2: book_depth_trend
  v4: obi_trend_momentum

CATEGORY D — Large-prints + key level (use large_buys / large_sells):
  v1: large_print_momentum, large_print_absorption, large_print_divergence,
      large_print_cluster, large_print_imbalance, cvd_divergence_large_print,
      level_delta_flip
  v2: large_print_key_level
  v3: large_print_at_level

CATEGORY E — Price-structure only (open/high/low/close, no volume used):
  (None identified — every strategy in v1-v4 uses at least one volume column.)

GAP IDENTIFIED: no strategy combines key-level proximity (rolling high/low as
proxy for PDH/PDL/VWAP/POC) with a rolling CVD *delta* (net change over a
window) to confirm rejection direction. The closest are:
  - level_delta_flip (v1): uses price at level + instant cvd_delta flip —
    no ATR proximity band, uses raw delta sign rather than windowed CVD delta.
  - large_print_key_level (v2 / v3): level + large prints + rolling cvd_delta
    sum, but gated behind large_buys / large_sells (unavailable on all symbols).
  - break_retest_cvd (v3): break first, then retest — a different entry trigger.
None fires purely on (near-level AND CVD-delta-window confirms rejection) without
requiring large prints or an explicit prior break. That is the gap filled here.
"""

import numpy as np
import pandas as pd


# ── Helpers (mirrors v3 / v4 conventions) ─────────────────────────────────────

def _zscore(series, window):
    mu  = series.rolling(window).mean()
    sig = series.rolling(window).std()
    return (series - mu) / sig.replace(0, np.nan)


def _atr(df, window=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def _signal(cond_long, cond_short):
    sig = pd.Series(0, index=cond_long.index)
    sig[cond_long.fillna(False)]  =  1
    sig[cond_short.fillna(False)] = -1
    # When both conditions are true simultaneously, resolve to 0
    sig[(cond_long.fillna(False)) & (cond_short.fillna(False))] = 0
    return sig.fillna(0).astype(int)


# ── Strategy ──────────────────────────────────────────────────────────────────

def key_level_cvd_rejection(
    df: pd.DataFrame,
    key_level_window: int   = 20,
    cvd_window: int         = 10,
    rejection_atr_pct: float = 0.5,
) -> pd.Series:
    """
    Key-Level CVD Rejection
    =======================
    Fires when price tests a key structural level (rolling N-bar high as
    resistance, rolling N-bar low as support — a standalone proxy for
    PDH/PDL/VWAP/POC; to use real session levels, swap in tick_key_levels.py)
    AND cumulative volume delta (CVD) confirms rejection in the opposite
    direction over the preceding cvd_window bars.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain: open, high, low, close, volume.
        CVD sourced in order of preference:
          1. 'cvd' column (pre-computed cumulative delta).
          2. 'buy_vol' + 'sell_vol' → cvd computed as (buy_vol-sell_vol).cumsum().
          3. Neither available → cvd_available=False, signal uses price structure
             only (near-level condition without CVD gate).
    key_level_window : int
        Look-back bars for rolling resistance high and support low.  Default 20.
    cvd_window : int
        Window over which the change in CVD (cvd_delta_window) is measured.
        Negative change at resistance → sellers absorbing; positive at support
        → buyers absorbing.  Default 10.
    rejection_atr_pct : float
        A bar is "near" a level when the close is within
        rejection_atr_pct × ATR(14) of that level.  Default 0.5.

    Returns
    -------
    pd.Series of int {-1, 0, +1}
        +1 = long signal (support rejection, buyers absorbing sellers)
        -1 = short signal (resistance rejection, sellers absorbing buyers)
         0 = no signal
    """
    # ── 1. ATR band ────────────────────────────────────────────────────────────
    atr  = _atr(df, window=14)
    band = rejection_atr_pct * atr

    # ── 2. Key levels (shifted 1 bar to avoid look-ahead) ─────────────────────
    # Resistance: rolling max of HIGH over key_level_window bars (prior bars only)
    resistance = df["high"].shift(1).rolling(key_level_window).max()
    # Support:    rolling min of LOW  over key_level_window bars (prior bars only)
    support    = df["low"].shift(1).rolling(key_level_window).min()

    # ── 3. Proximity condition ─────────────────────────────────────────────────
    # Near resistance: close is within band below resistance (approaching from below)
    near_resistance = (df["close"] >= resistance - band) & (df["close"] <= resistance + band)
    # Near support:    close is within band above support (approaching from above)
    near_support    = (df["close"] <= support + band) & (df["close"] >= support - band)

    # ── 4. CVD construction ───────────────────────────────────────────────────
    cvd_available = True

    if "cvd" in df.columns:
        cvd = df["cvd"]
    elif "buy_vol" in df.columns and "sell_vol" in df.columns:
        cvd = (df["buy_vol"] - df["sell_vol"]).cumsum()
    else:
        cvd_available = False

    # ── 5. CVD windowed delta (net change over cvd_window bars) ───────────────
    if cvd_available:
        cvd_delta_window = cvd - cvd.shift(cvd_window)
        # Resistance rejection: CVD has been falling over the window (net sellers)
        cvd_rejects_resistance = cvd_delta_window < 0
        # Support rejection:    CVD has been rising  over the window (net buyers)
        cvd_rejects_support    = cvd_delta_window > 0
    else:
        # No volume data: accept any proximity hit (price-structure only)
        cvd_rejects_resistance = pd.Series(True, index=df.index)
        cvd_rejects_support    = pd.Series(True, index=df.index)

    # ── 6. Raw signals ────────────────────────────────────────────────────────
    # Short: near resistance AND CVD net negative over window → sellers winning
    short_raw = near_resistance & cvd_rejects_resistance
    # Long:  near support    AND CVD net positive over window → buyers winning
    long_raw  = near_support    & cvd_rejects_support

    raw_sig = _signal(long_raw, short_raw)

    # ── 7. Debounce: suppress repeated same-direction signals ─────────────────
    # diff() == 0 where the signal direction did not change; those become 0.
    debounced = raw_sig.copy()
    debounced[raw_sig.diff() == 0] = 0

    return debounced.fillna(0).astype(int)


# ── Strategy Registry ─────────────────────────────────────────────────────────

STRAT_MAP_V5: dict = {
    "key_level_cvd_rejection": {
        "name": "key_level_cvd_rejection",
        "compute": key_level_cvd_rejection,
        "param_grid": {
            "key_level_window":  [10, 20, 30],
            "cvd_window":        [5, 10, 20],
            "rejection_atr_pct": [0.25, 0.5, 1.0],
        },
        "requires_mbp": False,
        "description": (
            "Price tests rolling high/low key level within ATR band "
            "and CVD confirms rejection in opposite direction — "
            "standalone PDH/PDL/VWAP/POC rejection without large-print gate"
        ),
    },
}
