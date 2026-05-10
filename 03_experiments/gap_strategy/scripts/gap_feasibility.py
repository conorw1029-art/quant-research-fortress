"""
gap_feasibility.py

Overnight-gap feasibility study on SPY daily bars.

Purpose: determine whether overnight gap size and direction predict the
subsequent RTH session return on the S&P 500, using 24 years of free data,
before committing money to higher-resolution ES futures data.

This is a FEASIBILITY test, not a backtest. We are not simulating a strategy.
We are answering one statistical question: does the gap signal contain
predictive information about intraday returns, net of noise?

Methodology
-----------
- Data: SPY daily bars from yfinance, 2000-01-01 to present.
- Feature: overnight gap = Open[t] - Close[t-1], normalized by trailing
  20-day ATR (computed causally: shift(1) then rolling mean).
- Outcome: session return = Close[t] - Open[t], as percentage of Open[t].
- Split: In-sample 2000-2017, Out-of-sample 2018-2024.
- Analysis:
    1. In-sample: bucket by gap size/direction, measure conditional session
       returns, test each bucket vs zero with t-test.
    2. If in-sample shows signal: check same buckets out-of-sample.
    3. Report effect sizes, t-stats, p-values, sample counts.
- Decision: if no bucket shows p < 0.05 in-sample, hypothesis is dead.
  If in-sample passes but OOS fails, hypothesis is dead. Only if both
  pass do we invest in ES intraday data for the real research.

No parameters are tuned. No optimization is performed. This is pure
measurement.
"""

from __future__ import annotations

import sys
from datetime import date
from io import StringIO

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats


# ------------------------------------------------------------------ Config

TICKER = "SPY"
START_DATE = "2000-01-01"

# Pre-registered split boundaries.
IN_SAMPLE_END = "2017-12-31"
OUT_OF_SAMPLE_START = "2018-01-01"
OUT_OF_SAMPLE_END = "2024-12-31"

# ATR lookback for gap normalization (trading days).
ATR_PERIOD = 20

# Gap buckets defined by |gap_normalized| thresholds.
# These are NOT optimized — they are round numbers chosen before seeing data.
GAP_BUCKETS = {
    "tiny":   (0.0, 0.25),
    "small":  (0.25, 0.5),
    "medium": (0.5, 1.0),
    "large":  (1.0, 1.5),
    "huge":   (1.5, float("inf")),
}

# Significance threshold. We use 0.05 for feasibility (not the stricter
# 0.01 we pre-registered for the final ES test, because this is a
# preliminary check, not the decision point).
ALPHA = 0.05


# ------------------------------------------------------------ Data loading

def download_spy() -> pd.DataFrame:
    """Download SPY daily bars from yfinance."""
    print(f"Downloading {TICKER} daily bars from {START_DATE}...")
    df = yf.download(TICKER, start=START_DATE, auto_adjust=True, progress=False)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty:
        print("ERROR: yfinance returned no data. Check your internet connection.")
        sys.exit(1)

    print(f"  Downloaded {len(df)} daily bars from {df.index.min().date()} to {df.index.max().date()}")
    return df


# --------------------------------------------------------- Feature compute

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add causally-valid gap features to daily OHLCV bars.

    All features for day t are computable at 9:30 ET on day t (using only
    the opening price and prior history). The session return is the OUTCOME
    we are trying to predict — it is never used as a feature.
    """
    out = df.copy()

    # Prior close: yesterday's RTH close. Shift(1) = causal.
    out["prior_close"] = out["Close"].shift(1)

    # Raw gap in dollars and percentage.
    out["gap_raw"] = out["Open"] - out["prior_close"]
    out["gap_pct"] = out["gap_raw"] / out["prior_close"]

    # Daily range for ATR computation.
    out["daily_range"] = out["High"] - out["Low"]

    # 20-day trailing ATR. shift(1) FIRST so today's ATR uses only
    # yesterday's and prior data. This is the critical causal step.
    out["atr_20"] = (
        out["daily_range"]
        .shift(1)
        .rolling(ATR_PERIOD, min_periods=ATR_PERIOD)
        .mean()
    )

    # Normalized gap: how large is today's gap relative to recent volatility?
    out["gap_normalized"] = out["gap_raw"] / out["atr_20"]

    # Absolute normalized gap for bucketing.
    out["abs_gap_norm"] = out["gap_normalized"].abs()

    # Gap direction: +1 for gap up, -1 for gap down, 0 for flat.
    out["gap_dir"] = np.sign(out["gap_raw"])

    # --- OUTCOME (not a feature — this is what we're predicting) ---
    # Session return: how much does price move from open to close?
    out["session_return"] = out["Close"] - out["Open"]
    out["session_return_pct"] = out["session_return"] / out["Open"]

    # Did the gap "fill"? A gap-up fills if price returns to prior_close
    # during the session. We can only approximate with daily bars: the gap
    # filled if the session low was <= prior_close (for gap-up) or session
    # high was >= prior_close (for gap-down).
    out["gap_filled"] = False
    gap_up = out["gap_dir"] > 0
    gap_down = out["gap_dir"] < 0
    out.loc[gap_up, "gap_filled"] = out.loc[gap_up, "Low"] <= out.loc[gap_up, "prior_close"]
    out.loc[gap_down, "gap_filled"] = out.loc[gap_down, "High"] >= out.loc[gap_down, "prior_close"]

    # Drop warmup rows (first ATR_PERIOD + 1 days have NaN features).
    out = out.dropna(subset=["gap_normalized", "atr_20"])

    return out


# ------------------------------------------------------------- Analysis

def assign_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """Assign each row to a gap-size bucket and a direction."""
    out = df.copy()
    out["gap_bucket"] = "unassigned"
    for name, (lo, hi) in GAP_BUCKETS.items():
        mask = (out["abs_gap_norm"] >= lo) & (out["abs_gap_norm"] < hi)
        out.loc[mask, "gap_bucket"] = name
    return out


def analyze_period(
    df: pd.DataFrame, period_label: str
) -> pd.DataFrame:
    """
    For each (gap_bucket, gap_direction) combination, compute:
    - count
    - mean session return (%)
    - std of session return
    - t-statistic vs zero
    - p-value (two-sided)
    - gap fill rate
    - mean session return when fading the gap (opposing direction return)

    Returns a summary DataFrame.
    """
    print(f"\n{'='*70}")
    print(f"  {period_label}")
    print(f"  Date range: {df.index.min().date()} to {df.index.max().date()}")
    print(f"  Total trading days: {len(df)}")
    print(f"{'='*70}")

    # Overall statistics first.
    print(f"\n  Overall session return: mean={df['session_return_pct'].mean()*100:.4f}%, "
          f"std={df['session_return_pct'].std()*100:.4f}%")
    print(f"  Overall gap fill rate: {df['gap_filled'].mean()*100:.1f}%")

    records = []

    for direction_label, dir_val in [("gap_up", 1.0), ("gap_down", -1.0)]:
        for bucket_name in GAP_BUCKETS:
            mask = (df["gap_bucket"] == bucket_name) & (df["gap_dir"] == dir_val)
            subset = df.loc[mask]
            n = len(subset)

            if n < 10:
                records.append({
                    "direction": direction_label,
                    "bucket": bucket_name,
                    "count": n,
                    "mean_session_ret_pct": np.nan,
                    "std_session_ret_pct": np.nan,
                    "t_stat": np.nan,
                    "p_value": np.nan,
                    "fill_rate_pct": np.nan,
                    "significant": False,
                })
                continue

            ret = subset["session_return_pct"]
            mean_ret = ret.mean()
            std_ret = ret.std()
            t_stat, p_val = stats.ttest_1samp(ret, 0.0)
            fill_rate = subset["gap_filled"].mean()

            # "Fade return": if we bet AGAINST the gap direction, our return
            # is -session_return for gap_up, +session_return for gap_down.
            # Actually simpler: the fade return is -(gap_dir * session_return_pct).
            # A positive fade return means fading the gap was profitable.
            fade_ret = -(dir_val * ret)
            mean_fade = fade_ret.mean()

            records.append({
                "direction": direction_label,
                "bucket": bucket_name,
                "count": n,
                "mean_session_ret_pct": mean_ret * 100,
                "std_session_ret_pct": std_ret * 100,
                "t_stat": t_stat,
                "p_value": p_val,
                "fill_rate_pct": fill_rate * 100,
                "mean_fade_ret_pct": mean_fade * 100,
                "significant": p_val < ALPHA,
            })

    summary = pd.DataFrame(records)

    # Print formatted table.
    print(f"\n  Conditional session returns by gap bucket and direction:")
    print(f"  (fade_ret = return from betting AGAINST the gap direction)")
    print(f"  {'dir':<10} {'bucket':<8} {'n':>5} {'mean_ret%':>10} {'std%':>8} "
          f"{'t_stat':>8} {'p_val':>8} {'fill%':>7} {'fade%':>8} {'sig':>4}")
    print(f"  {'-'*85}")

    for _, row in summary.iterrows():
        if pd.isna(row["t_stat"]):
            print(f"  {row['direction']:<10} {row['bucket']:<8} {row['count']:>5} "
                  f"{'-- too few samples --'}")
            continue

        sig_marker = " ***" if row["significant"] else ""
        print(
            f"  {row['direction']:<10} {row['bucket']:<8} {row['count']:>5} "
            f"{row['mean_session_ret_pct']:>+10.4f} {row['std_session_ret_pct']:>8.4f} "
            f"{row['t_stat']:>8.3f} {row['p_value']:>8.4f} "
            f"{row['fill_rate_pct']:>6.1f}% {row.get('mean_fade_ret_pct', 0):>+7.4f}"
            f"{sig_marker}"
        )

    return summary


def analyze_combined_directions(df: pd.DataFrame, period_label: str) -> pd.DataFrame:
    """
    Analyze gap buckets ignoring direction — just looking at absolute gap
    size and whether the session return opposes the gap (mean reversion)
    or continues it (momentum).
    """
    print(f"\n  --- Direction-agnostic analysis (does gap SIZE alone matter?) ---")
    print(f"  fade_ret here = return from always fading (opposing) the gap\n")

    records = []
    for bucket_name in GAP_BUCKETS:
        mask = (df["gap_bucket"] == bucket_name) & (df["gap_dir"] != 0)
        subset = df.loc[mask]
        n = len(subset)

        if n < 20:
            records.append({
                "bucket": bucket_name, "count": n,
                "mean_fade_ret_pct": np.nan, "t_stat": np.nan,
                "p_value": np.nan, "fill_rate_pct": np.nan,
            })
            continue

        # Fade return: bet against the gap.
        fade_ret = -(subset["gap_dir"] * subset["session_return_pct"])
        t_stat, p_val = stats.ttest_1samp(fade_ret, 0.0)
        fill_rate = subset["gap_filled"].mean()

        records.append({
            "bucket": bucket_name,
            "count": n,
            "mean_fade_ret_pct": fade_ret.mean() * 100,
            "std_fade_ret_pct": fade_ret.std() * 100,
            "t_stat": t_stat,
            "p_value": p_val,
            "fill_rate_pct": fill_rate * 100,
            "significant": p_val < ALPHA,
        })

    summary = pd.DataFrame(records)

    print(f"  {'bucket':<8} {'n':>6} {'mean_fade%':>11} {'std%':>8} "
          f"{'t_stat':>8} {'p_val':>8} {'fill%':>7} {'sig':>4}")
    print(f"  {'-'*65}")

    for _, row in summary.iterrows():
        if pd.isna(row.get("t_stat")):
            print(f"  {row['bucket']:<8} {row['count']:>6}   -- too few samples --")
            continue
        sig = " ***" if row.get("significant", False) else ""
        print(
            f"  {row['bucket']:<8} {row['count']:>6} "
            f"{row['mean_fade_ret_pct']:>+11.4f} {row['std_fade_ret_pct']:>8.4f} "
            f"{row['t_stat']:>8.3f} {row['p_value']:>8.4f} "
            f"{row['fill_rate_pct']:>6.1f}%{sig}"
        )

    return summary


def check_temporal_stability(df: pd.DataFrame) -> None:
    """
    Split in-sample into two halves and check that the effect direction
    is consistent. If it flips sign between halves, the signal is unstable.
    """
    print(f"\n  --- Temporal stability check (in-sample split in half) ---")

    mid = df.index[len(df) // 2]
    first_half = df.loc[:mid]
    second_half = df.loc[mid:]

    for label, subset in [("First half", first_half), ("Second half", second_half)]:
        # Overall fade return across all non-zero gaps.
        mask = subset["gap_dir"] != 0
        fade = -(subset.loc[mask, "gap_dir"] * subset.loc[mask, "session_return_pct"])
        mean_fade = fade.mean() * 100
        t, p = stats.ttest_1samp(fade, 0.0) if len(fade) > 10 else (np.nan, np.nan)
        print(f"  {label} ({subset.index.min().date()} to {subset.index.max().date()}): "
              f"n={len(fade)}, mean_fade={mean_fade:+.4f}%, t={t:.3f}, p={p:.4f}")


def rolling_annual_fade_return(df: pd.DataFrame) -> None:
    """Print per-year fade returns to check for decay over time."""
    print(f"\n  --- Annual fade returns (checking for decay) ---")
    print(f"  {'year':>6} {'n':>5} {'mean_fade%':>11} {'t_stat':>8} {'p_val':>8}")
    print(f"  {'-'*45}")

    df_with_year = df.copy()
    df_with_year["year"] = df_with_year.index.year
    mask = df_with_year["gap_dir"] != 0

    for year, group in df_with_year.loc[mask].groupby("year"):
        fade = -(group["gap_dir"] * group["session_return_pct"])
        n = len(fade)
        mean_f = fade.mean() * 100
        if n > 10:
            t, p = stats.ttest_1samp(fade, 0.0)
        else:
            t, p = np.nan, np.nan
        print(f"  {year:>6} {n:>5} {mean_f:>+11.4f} {t:>8.3f} {p:>8.4f}")


# ------------------------------------------------------------------ Main

def main() -> None:
    print("=" * 70)
    print("  OVERNIGHT GAP FEASIBILITY STUDY — SPY DAILY BARS")
    print("  Pre-registered analysis. No parameters tuned.")
    print("=" * 70)

    # 1. Download
    spy = download_spy()

    # 2. Compute features
    featured = compute_features(spy)
    print(f"  Feature computation complete: {len(featured)} rows after warmup")

    # 3. Bucket assignment
    featured = assign_buckets(featured)

    # 4. Split
    in_sample = featured.loc[:IN_SAMPLE_END]
    oos = featured.loc[OUT_OF_SAMPLE_START:OUT_OF_SAMPLE_END]

    print(f"\n  In-sample:        {len(in_sample)} days ({in_sample.index.min().date()} to {in_sample.index.max().date()})")
    print(f"  Out-of-sample:    {len(oos)} days ({oos.index.min().date()} to {oos.index.max().date()})")

    # 5. In-sample analysis
    is_detail = analyze_period(in_sample, "IN-SAMPLE ANALYSIS (2000–2017)")
    is_combined = analyze_combined_directions(in_sample, "IN-SAMPLE")

    check_temporal_stability(in_sample)
    rolling_annual_fade_return(in_sample)

    # 6. In-sample verdict
    any_significant_is = is_combined["significant"].any() if "significant" in is_combined.columns else False

    print(f"\n{'='*70}")
    print(f"  IN-SAMPLE VERDICT")
    print(f"{'='*70}")

    if any_significant_is:
        sig_buckets = is_combined.loc[
            is_combined.get("significant", pd.Series(dtype=bool)),
            ["bucket", "mean_fade_ret_pct", "p_value"]
        ]
        print(f"  SIGNAL DETECTED in {len(sig_buckets)} bucket(s):")
        for _, row in sig_buckets.iterrows():
            print(f"    - {row['bucket']}: mean_fade={row['mean_fade_ret_pct']:+.4f}%, p={row['p_value']:.4f}")
        print(f"\n  Proceeding to out-of-sample check...")
    else:
        print(f"  NO SIGNIFICANT SIGNAL in any gap bucket.")
        print(f"  The overnight gap does not predict session returns on SPY")
        print(f"  at any size threshold we tested.")
        print(f"\n  RECOMMENDATION: REJECT this hypothesis. Move to Hypothesis 2.")
        print(f"  Do not buy ES intraday data for gap research.")
        return

    # 7. Out-of-sample analysis (only reached if in-sample passed)
    oos_detail = analyze_period(oos, "OUT-OF-SAMPLE ANALYSIS (2018–2024)")
    oos_combined = analyze_combined_directions(oos, "OUT-OF-SAMPLE")
    rolling_annual_fade_return(oos)

    # 8. OOS verdict
    any_significant_oos = oos_combined["significant"].any() if "significant" in oos_combined.columns else False

    print(f"\n{'='*70}")
    print(f"  OUT-OF-SAMPLE VERDICT")
    print(f"{'='*70}")

    if any_significant_oos:
        sig_oos = oos_combined.loc[
            oos_combined.get("significant", pd.Series(dtype=bool)),
            ["bucket", "mean_fade_ret_pct", "p_value"]
        ]
        print(f"  SIGNAL PERSISTS in OOS in {len(sig_oos)} bucket(s):")
        for _, row in sig_oos.iterrows():
            print(f"    - {row['bucket']}: mean_fade={row['mean_fade_ret_pct']:+.4f}%, p={row['p_value']:.4f}")
        print(f"\n  RECOMMENDATION: Hypothesis is ALIVE. Invest in ES intraday")
        print(f"  data and proceed to full research with proper train/val/test.")
    else:
        print(f"  SIGNAL DOES NOT PERSIST out-of-sample.")
        print(f"  In-sample result was likely noise or the effect has decayed.")
        print(f"\n  RECOMMENDATION: REJECT this hypothesis. Move to Hypothesis 2.")

    # 9. Final summary
    print(f"\n{'='*70}")
    print(f"  COMPLETE — Copy everything above and send to your research partner.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
