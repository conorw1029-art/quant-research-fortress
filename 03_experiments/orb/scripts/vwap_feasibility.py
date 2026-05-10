"""
HYPOTHESIS 3: VWAP Mean Reversion
===================================
Pre-registered feasibility study. DO NOT modify parameters after running.

Theory: During RTH, ES prices anchored to session VWAP by institutional
        passive order flow. Extreme deviations with declining volume signal
        exhaustion → reversion trade.

Instrument : ES continuous futures (1-minute bars)
Data source: Databento CSV (same file as ORB study)
Costs      : $30/round-turn ($12.50/tick × 2 ticks slippage + $5 commission)
             = 1.2 points per round-turn on ES
Author     : Quant Research Factory – Hypothesis 3
Date       : 2026-04-23
Status     : PRE-REGISTERED – parameters frozen
"""

import argparse
import sys
import warnings
from datetime import time
from itertools import product

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# FROZEN PRE-REGISTERED PARAMETERS
# ─────────────────────────────────────────────
IS_END      = "2018-12-31"   # in-sample end (inclusive)
OOS_START   = "2019-01-01"
OOS_END     = "2024-12-31"

RTH_START   = time(9, 31)    # first tradeable bar after open
RTH_END     = time(15, 55)   # last entry; force-exit at 15:55
ENTRY_CUTOFF = time(15, 0)   # no new entries after this

COST_PTS    = 1.2            # round-turn in ES points ($30 / $12.50 × 0.25)
TICK        = 0.25           # ES tick size
MAX_TRADES_PER_DAY = 4       # 2 long + 2 short ceiling

STOP_MULT   = 1.5            # stop = entry_band_width × STOP_MULT beyond entry

# 6 pre-registered variants (band_mult × vol_ratio)
BAND_MULTS  = [1.5, 2.0, 2.5]
VOL_RATIOS  = [0.7, 1.0]     # 1.0 = no volume filter

# Pass/fail thresholds (Bonferroni α = 0.10 / 6)
BONFERRONI_P    = 0.10 / 6   # ≈ 0.0167
MIN_PROFIT_FACTOR = 1.25
MIN_WIN_RATE    = 0.40
MAX_DD_STOPS    = 20.0       # max_dd / avg_stop must be ≤ this


# ─────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────

def load_data(
    path: str,
    source_tz: str = "America/New_York",
    col_ts: str = "ts_event",
    col_open: str = "open",
    col_high: str = "high",
    col_low: str = "low",
    col_close: str = "close",
    col_volume: str = "volume",
) -> pd.DataFrame:
    """Load and normalise the 1-minute CSV to a standard DataFrame."""
    print(f"Loading data from {path} ...")
    df = pd.read_csv(
    path,
    low_memory=False,
    usecols=["ts_event", "open", "high", "low", "close", "volume"],
)

    # Timestamp
    df["ts"] = pd.to_datetime(df[col_ts], utc=True)
    df = df.set_index("ts").sort_index()
    df.index = df.index.tz_convert("America/New_York")

    # Rename OHLCV
    rename = {col_open: "open", col_high: "high", col_low: "low",
              col_close: "close", col_volume: "volume"}
    df = df.rename(columns=rename)[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna()

    # RTH filter
    t = df.index.time
    df = df[(t >= RTH_START) & (t <= time(15, 59))]

    print(f"  Loaded {len(df):,} RTH bars from {df.index[0].date()} to {df.index[-1].date()}")
    return df


# ─────────────────────────────────────────────
# FEATURE ENGINEERING (causal – no lookahead)
# ─────────────────────────────────────────────

def add_session_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each bar compute session VWAP, rolling σ, and volume ratio.
    All calculations use only data UP TO AND INCLUDING the current bar.
    """
    df = df.copy()
    df["date"] = df.index.date

    # Cumulative session VWAP: sum(typical_price × volume) / sum(volume)
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]

    # Per-session cumulative sums (groupby date)
    df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"]    = df.groupby("date")["volume"].cumsum()
    df["vwap"]       = df["cum_tp_vol"] / df["cum_vol"]

    # Rolling σ of close around VWAP within session
    # Use expanding window per session for stability in early bars
    def session_std(group):
        return group["close"].expanding().std()

    df["session_std"] = df.groupby("date", group_keys=False).apply(session_std)
    df["session_std"] = df["session_std"].bfill()

    # Volume ratio: current bar / prior bar (simple exhaustion proxy)
    df["vol_ratio"] = df["volume"] / df["volume"].shift(1)
    df["vol_ratio"] = df["vol_ratio"].fillna(1.0).clip(0, 10)

    # Distance from VWAP in σ units (signed)
    df["vwap_z"] = (df["close"] - df["vwap"]) / df["session_std"].clip(lower=1e-6)

    return df


# ─────────────────────────────────────────────
# BACKTEST ENGINE (single variant)
# ─────────────────────────────────────────────

def run_variant(
    df: pd.DataFrame,
    band_mult: float,
    vol_ratio_thresh: float,
) -> pd.DataFrame:
    """
    Simulate VWAP mean-reversion trades for one parameter combination.

    Entry rules (per bar, in order):
      1. Not already in a position.
      2. Bar time between RTH_START and ENTRY_CUTOFF.
      3. Trades this day < MAX_TRADES_PER_DAY.
      4. |vwap_z| > band_mult  (price outside band)
      5. vol_ratio < vol_ratio_thresh  (declining volume = exhaustion)
         – skipped if vol_ratio_thresh == 1.0 (no filter variant)
      6. Short if z > +band_mult, Long if z < -band_mult.

    Exit rules (first hit):
      A. Price crosses back through VWAP (reversion target).
      B. Bar time >= 15:55 ET (force exit).
      C. Stop hit: price moves STOP_MULT × band_width against entry.

    Returns a DataFrame of individual trades.
    """
    use_vol_filter = vol_ratio_thresh < 1.0

    trades = []
    in_trade = False
    entry_price = direction = stop_price = target_price = None
    entry_time = entry_date = None
    daily_trade_count = 0
    prev_date = None

    for ts, row in df.iterrows():
        bar_time = ts.time()
        bar_date = ts.date()

        # Reset daily counter
        if bar_date != prev_date:
            daily_trade_count = 0
            prev_date = bar_date

        # ── Force exit at 15:55 ──
        if in_trade and bar_time >= RTH_END:
            pnl = (row["close"] - entry_price) * direction - COST_PTS
            trades.append({
                "entry_time": entry_time,
                "exit_time": ts,
                "entry_price": entry_price,
                "exit_price": row["close"],
                "direction": direction,
                "pnl_pts": pnl,
                "exit_reason": "EOD",
                "band_mult": band_mult,
                "vol_ratio_thresh": vol_ratio_thresh,
            })
            in_trade = False
            continue

        # ── Check stop / target mid-trade ──
        if in_trade:
            # Check stop (use bar high/low for conservative fill)
            if direction == 1 and row["low"] <= stop_price:
                pnl = (stop_price - entry_price) * direction - COST_PTS
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "entry_price": entry_price,
                    "exit_price": stop_price,
                    "direction": direction,
                    "pnl_pts": pnl,
                    "exit_reason": "STOP",
                    "band_mult": band_mult,
                    "vol_ratio_thresh": vol_ratio_thresh,
                })
                in_trade = False
                continue
            if direction == -1 and row["high"] >= stop_price:
                pnl = (stop_price - entry_price) * direction - COST_PTS
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "entry_price": entry_price,
                    "exit_price": stop_price,
                    "direction": direction,
                    "pnl_pts": pnl,
                    "exit_reason": "STOP",
                    "band_mult": band_mult,
                    "vol_ratio_thresh": vol_ratio_thresh,
                })
                in_trade = False
                continue

            # Check VWAP reversion target
            vwap = row["vwap"]
            crossed = (direction == 1 and row["high"] >= vwap) or \
                      (direction == -1 and row["low"] <= vwap)
            if crossed:
                exit_px = vwap  # assume fill at VWAP
                pnl = (exit_px - entry_price) * direction - COST_PTS
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "entry_price": entry_price,
                    "exit_price": exit_px,
                    "direction": direction,
                    "pnl_pts": pnl,
                    "exit_reason": "TARGET",
                    "band_mult": band_mult,
                    "vol_ratio_thresh": vol_ratio_thresh,
                })
                in_trade = False
                continue

        # ── Entry logic ──
        if (not in_trade
                and bar_time <= ENTRY_CUTOFF
                and daily_trade_count < MAX_TRADES_PER_DAY
                and not np.isnan(row["vwap_z"])
                and not np.isnan(row["session_std"])):

            z = row["vwap_z"]
            abs_z = abs(z)

            # Volume filter
            vol_ok = (not use_vol_filter) or (row["vol_ratio"] < vol_ratio_thresh)

            if abs_z > band_mult and vol_ok:
                direction = -1 if z > 0 else 1  # fade the extreme
                entry_price = row["close"]
                entry_time  = ts
                entry_date  = bar_date

                # Stop: STOP_MULT × band_width beyond entry
                band_width  = band_mult * row["session_std"]
                stop_dist   = STOP_MULT * band_width
                stop_price  = entry_price - direction * stop_dist
                # (not used for exit logic above but stored for DD calc)
                stop_pts    = stop_dist

                in_trade = True
                daily_trade_count += 1

    return pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["entry_time", "exit_time", "entry_price", "exit_price",
                 "direction", "pnl_pts", "exit_reason", "band_mult", "vol_ratio_thresh"]
    )


# ─────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, label: str) -> dict:
    """Compute all pre-registered pass/fail metrics for a trade set."""
    if len(trades) < 10:
        return {"label": label, "n_trades": len(trades), "status": "INSUFFICIENT DATA"}

    pnl = trades["pnl_pts"].values
    n   = len(pnl)

    wins  = pnl[pnl > 0]
    losses = pnl[pnl <= 0]

    mean_pnl   = pnl.mean()
    win_rate   = len(wins) / n
    gross_win  = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 1e-9
    pf         = gross_win / gross_loss

    # One-sided t-test: H0: mean_pnl <= 0
    t_stat, p_two = stats.ttest_1samp(pnl, 0)
    p_one = p_two / 2 if t_stat > 0 else 1.0

    # Equity curve & drawdown
    equity = np.cumsum(pnl)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = abs(dd.min()) if len(dd) else 0.0

    # Avg stop size (approximate from stop-exit trades or use band_mult proxy)
    stop_trades = trades[trades["exit_reason"] == "STOP"]
    if len(stop_trades) > 0:
        avg_stop = abs(stop_trades["pnl_pts"] + COST_PTS).mean()
    else:
        avg_stop = abs(pnl).mean()  # fallback
    avg_stop = max(avg_stop, 0.01)

    dd_to_stop = max_dd / avg_stop

    # Half-period Sharpe (for stability check)
    mid = n // 2
    sharpe_h1 = pnl[:mid].mean() / (pnl[:mid].std() + 1e-9) * np.sqrt(252)
    sharpe_h2 = pnl[mid:].mean() / (pnl[mid:].std() + 1e-9) * np.sqrt(252)

    # Pass/fail evaluation
    criteria = {
        "mean_pnl_positive": mean_pnl > 0,
        "p_value":           p_one < BONFERRONI_P,
        "profit_factor":     pf >= MIN_PROFIT_FACTOR,
        "win_rate":          win_rate >= MIN_WIN_RATE,
        "dd_ratio":          dd_to_stop <= MAX_DD_STOPS,
        "both_halves_pos":   (sharpe_h1 > 0) and (sharpe_h2 > 0),
    }
    passed = all(criteria.values())

    return {
        "label":        label,
        "n_trades":     n,
        "mean_pnl":     round(mean_pnl, 4),
        "win_rate":     round(win_rate, 4),
        "profit_factor": round(pf, 3),
        "p_one_sided":  round(p_one, 5),
        "max_dd_pts":   round(max_dd, 2),
        "avg_stop_pts": round(avg_stop, 2),
        "dd_to_stop":   round(dd_to_stop, 1),
        "sharpe_h1":    round(sharpe_h1, 3),
        "sharpe_h2":    round(sharpe_h2, 3),
        "criteria":     criteria,
        "PASSED":       passed,
        "status":       "PASS" if passed else "FAIL",
    }


def print_section(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print("=" * 70)


def print_metrics(m: dict) -> None:
    if "status" in m and m["status"] == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA ({m['n_trades']} trades)")
        return

    status_str = "✓ PASS" if m["PASSED"] else "✗ FAIL"
    print(f"\n  [{status_str}] {m['label']}")
    print(f"    n={m['n_trades']:4d}  mean={m['mean_pnl']:+.4f}pts  "
          f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.3f}  "
          f"p={m['p_one_sided']:.5f}")
    print(f"    MaxDD={m['max_dd_pts']:.2f}pts  AvgStop={m['avg_stop_pts']:.2f}pts  "
          f"DD/Stop={m['dd_to_stop']:.1f}  "
          f"Sharpe[H1/H2]={m['sharpe_h1']:.3f}/{m['sharpe_h2']:.3f}")

    # Show which criteria failed
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED criteria: {', '.join(fails)}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VWAP Mean-Reversion Feasibility Study")
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="UTC",
                        help="Timezone of source timestamps (default: UTC)")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--col-open",   default="open")
    parser.add_argument("--col-high",   default="high")
    parser.add_argument("--col-low",    default="low")
    parser.add_argument("--col-close",  default="close")
    parser.add_argument("--col-volume", default="volume")
    args = parser.parse_args()

    # ── Load ──
    df_raw = load_data(
        args.input,
        source_tz=args.source_tz,
        col_ts=args.col_timestamp,
        col_open=args.col_open,
        col_high=args.col_high,
        col_low=args.col_low,
        col_close=args.col_close,
        col_volume=args.col_volume,
    )

    df_feat = add_session_features(df_raw)

    # ── Split ──
    df_is  = df_feat[df_feat.index <= IS_END]
    df_oos = df_feat[(df_feat.index >= OOS_START) & (df_feat.index <= OOS_END)]

    print_section(f"VWAP MEAN REVERSION – HYPOTHESIS 3")
    print(f"  Pre-registered. Parameters frozen before execution.")
    print(f"  IS : {df_is.index[0].date()} → {df_is.index[-1].date()}  "
          f"({len(df_is):,} bars)")
    print(f"  OOS: {df_oos.index[0].date()} → {df_oos.index[-1].date()}  "
          f"({len(df_oos):,} bars)")
    print(f"  Variants: {len(BAND_MULTS)} band_mult × {len(VOL_RATIOS)} vol_ratio = "
          f"{len(BAND_MULTS)*len(VOL_RATIOS)} total")
    print(f"  Bonferroni α: {BONFERRONI_P:.5f}  "
          f"Cost: {COST_PTS} pts/RT (${COST_PTS * 50:.2f})")

    # ── Run all variants ──
    is_results  = []
    oos_results = []

    for bm, vr in product(BAND_MULTS, VOL_RATIOS):
        label_vr = f"vol<{vr}" if vr < 1.0 else "no_vol_filter"
        label = f"band={bm:.1f}σ  {label_vr}"

        t_is  = run_variant(df_is, bm, vr)
        t_oos = run_variant(df_oos, bm, vr)

        is_results.append(compute_metrics(t_is, f"IS  | {label}"))
        oos_results.append(compute_metrics(t_oos, f"OOS | {label}"))

    # ── Print IS ──
    print_section("IN-SAMPLE ANALYSIS (2010 – 2018)")
    for m in is_results:
        print_metrics(m)

    # ── Print OOS ──
    print_section("OUT-OF-SAMPLE ANALYSIS (2019 – 2024)")
    for m in oos_results:
        print_metrics(m)

    # ── Verdict ──
    print_section("VERDICT")
    oos_passes = [m for m in oos_results if m.get("PASSED")]

    if oos_passes:
        print(f"\n  ✓ SIGNAL DETECTED IN {len(oos_passes)} VARIANT(S):")
        for m in oos_passes:
            print(f"    → {m['label']}")
        print("\n  NEXT STEP: Do NOT proceed to infrastructure yet.")
        print("  Run robustness checks: walk-forward bootstrap, regime decomp.")
        print("  Paste full output back to research partner.")
    else:
        # Show best OOS result for diagnostics
        best = max(oos_results, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  ✗ NO VARIANT PASSED. Hypothesis REJECTED.")
        print(f"\n  Best OOS variant: {best['label']}")
        print(f"    mean PnL={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}")
        print(f"\n  RECOMMENDATION: Move to Hypothesis 4 (Late-Session Drift).")
        print(f"  Do NOT purchase additional data. Do NOT add features.")
        print(f"  Copy this full output and paste to research partner.")

    print(f"\n{'=' * 70}")
    print(f"  COMPLETE")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()