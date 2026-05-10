"""
HYPOTHESIS 6: FOMC Pre-Announcement Drift
=============================================
Calendar anomaly. Fundamentally different mechanism from H1-H5.

Academic basis: Lucca & Moench (2015), FRBNY Staff Report No. 512
  "Pre-FOMC Announcement Drift"
  S&P 500 futures drift upward in the ~24hrs before scheduled
  FOMC rate decisions. Mechanism: institutional hedge unwinding.

Signal     : Go long ES/MES before FOMC announcement
Entry      : Prior day close OR morning of announcement day
Exit       : Shortly after announcement (14:00 ET) or EOD
Instrument : ES continuous futures (1-minute bars)
Cost       : MES 0.52pts/RT
Trades/year: ~8 (scheduled FOMC meetings)
Date       : 2026-04-24
Status     : PRE-REGISTERED. 4 variants. No post-hoc additions.

NOTE: FOMC dates are publicly known years in advance.
      This is NOT data snooping -- the calendar is the signal.
"""

import argparse
import warnings
from datetime import time, timedelta

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# -----------------------------------------------------------------
# FROZEN PARAMETERS
# -----------------------------------------------------------------
IS_END       = "2018-12-31"
OOS_START    = "2019-01-01"
OOS_END      = "2024-12-31"

MES_COST_PTS = 0.52
ES_COST_PTS  = 1.2

# 4 variants: entry_time x exit_time
# Entry options: (a) prior day 15:50 ET, (b) announcement day 09:35 ET
# Exit options:  (a) announcement day 14:15 ET (15 min after announcement),
#                (b) announcement day 15:55 ET (EOD)
VARIANTS = [
    {"name": "prior_close->post_fomc",  "entry_time": "prior_1550", "exit_time": "1415"},
    {"name": "prior_close->eod",        "entry_time": "prior_1550", "exit_time": "1555"},
    {"name": "morning->post_fomc",      "entry_time": "day_0935",   "exit_time": "1415"},
    {"name": "morning->eod",            "entry_time": "day_0935",   "exit_time": "1555"},
]
N_VARIANTS   = len(VARIANTS)
BONFERRONI_P = 0.10 / N_VARIANTS  # 0.025

MIN_PF       = 1.25
MIN_WR       = 0.40
MAX_DD_STOPS = 20.0

# -----------------------------------------------------------------
# FOMC DATES (all scheduled rate decisions 2010-2024)
# Source: Federal Reserve official calendar
# These are announcement dates (statement released at 14:00 ET)
# -----------------------------------------------------------------
FOMC_DATES = [
    # 2010
    "2010-01-27", "2010-03-16", "2010-04-28", "2010-06-23",
    "2010-08-10", "2010-09-21", "2010-11-03", "2010-12-14",
    # 2011
    "2011-01-26", "2011-03-15", "2011-04-27", "2011-06-22",
    "2011-08-09", "2011-09-21", "2011-11-02", "2011-12-13",
    # 2012
    "2012-01-25", "2012-03-13", "2012-04-25", "2012-06-20",
    "2012-08-01", "2012-09-13", "2012-10-24", "2012-12-12",
    # 2013
    "2013-01-30", "2013-03-20", "2013-05-01", "2013-06-19",
    "2013-07-31", "2013-09-18", "2013-10-30", "2013-12-18",
    # 2014
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18",
    "2014-07-30", "2014-09-17", "2014-10-29", "2014-12-17",
    # 2015
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
]

FOMC_DATES_SET = set(pd.to_datetime(FOMC_DATES).date)


# -----------------------------------------------------------------
# DATA LOADING
# -----------------------------------------------------------------

def load_data(path, col_ts="ts_event", col_open="open", col_high="high",
              col_low="low", col_close="close", col_volume="volume"):
    print(f"Loading {path} ...")
    df = pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df[col_ts], utc=True)
    df = df.set_index("ts").sort_index()
    df.index = df.index.tz_convert("America/New_York")
    df = df.rename(columns={col_open: "open", col_high: "high", col_low: "low",
                             col_close: "close", col_volume: "volume"}
                   )[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    t = df.index.time
    df = df[(t >= time(9, 30)) & (t <= time(15, 59))]
    print(f"  {len(df):,} 1-min RTH bars  {df.index[0].date()} -> {df.index[-1].date()}")
    return df


# -----------------------------------------------------------------
# FIND PRIOR TRADING DAY
# -----------------------------------------------------------------

def get_prior_trading_day(df_1m, target_date):
    """Return the trading day immediately before target_date."""
    all_dates = sorted(set(df_1m.index.date))
    for i, d in enumerate(all_dates):
        if d == target_date and i > 0:
            return all_dates[i - 1]
    return None


# -----------------------------------------------------------------
# BACKTEST
# -----------------------------------------------------------------

def run_variant(df_1m, fomc_dates, entry_type, exit_time_str, cost_pts):
    """
    Long-only on FOMC days.
    entry_type: 'prior_1550' or 'day_0935'
    exit_time_str: '1415' or '1555'
    """
    exit_time = time(int(exit_time_str[:2]), int(exit_time_str[2:]))

    trades = []

    for fomc_date in fomc_dates:
        fomc_dt = pd.to_datetime(fomc_date).date()

        # Get entry price
        if entry_type == "prior_1550":
            prior_day = get_prior_trading_day(df_1m, fomc_dt)
            if prior_day is None:
                continue
            prior_bars = df_1m[df_1m.index.date == prior_day]
            entry_bars = prior_bars[prior_bars.index.time >= time(15, 50)]
            if len(entry_bars) == 0:
                continue
            entry_price = entry_bars.iloc[-1]["close"]
            entry_ts = entry_bars.index[-1]
        elif entry_type == "day_0935":
            day_bars = df_1m[df_1m.index.date == fomc_dt]
            entry_bars = day_bars[day_bars.index.time >= time(9, 35)]
            if len(entry_bars) == 0:
                continue
            entry_price = entry_bars.iloc[0]["open"]
            entry_ts = entry_bars.index[0]
        else:
            continue

        # Get exit price
        day_bars = df_1m[df_1m.index.date == fomc_dt]
        exit_bars = day_bars[day_bars.index.time >= exit_time]
        if len(exit_bars) == 0:
            # Use last bar of day
            if len(day_bars) == 0:
                continue
            exit_price = day_bars.iloc[-1]["close"]
            exit_ts = day_bars.index[-1]
        else:
            exit_price = exit_bars.iloc[0]["close"]
            exit_ts = exit_bars.index[0]

        pnl = (exit_price - entry_price) - cost_pts  # long only
        trades.append({
            "fomc_date": fomc_dt,
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pts": pnl,
            "direction": 1,
        })

    return pd.DataFrame(trades) if trades else pd.DataFrame()


# -----------------------------------------------------------------
# METRICS
# -----------------------------------------------------------------

def compute_metrics(trades, label, alpha):
    n = len(trades)
    if n < 10:
        return {"label": label, "n_trades": n, "PASSED": False,
                "status": "INSUFFICIENT DATA", "mean_pnl": -999}

    pnl = trades["pnl_pts"].values
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]

    mean_pnl = pnl.mean()
    win_rate = len(wins) / n
    gross_win = wins.sum() if len(wins) else 0.0
    gross_los = abs(losses.sum()) if len(losses) else 1e-9
    pf = gross_win / gross_los

    t_stat, p2 = stats.ttest_1samp(pnl, 0)
    p_one = p2 / 2 if t_stat > 0 else 1.0

    equity = np.cumsum(pnl)
    max_dd = abs((equity - np.maximum.accumulate(equity)).min())

    avg_loss = abs(losses).mean() if len(losses) > 0 else max(abs(pnl).mean(), 0.01)
    avg_loss = max(avg_loss, 0.01)
    dd_ratio = max_dd / avg_loss

    mid = n // 2
    def sharpe(x):
        if len(x) < 2: return 0.0
        return x.mean() / (x.std() + 1e-9) * np.sqrt(8)  # ~8 trades/year
    sh1 = sharpe(pnl[:mid]); sh2 = sharpe(pnl[mid:])

    # Annual
    trades_c = trades.copy()
    trades_c["year"] = pd.to_datetime(trades_c["fomc_date"]).dt.year
    annual = trades_c.groupby("year")["pnl_pts"].agg(["mean", "count", "sum"])

    criteria = {
        "mean_pnl_positive": mean_pnl > 0,
        "p_value":           p_one < alpha,
        "profit_factor":     pf >= MIN_PF,
        "win_rate":          win_rate >= MIN_WR,
        "dd_ratio":          dd_ratio <= MAX_DD_STOPS,
        "both_halves_pos":   sh1 > 0 and sh2 > 0,
    }
    passed = all(criteria.values())

    return {
        "label": label, "n_trades": n,
        "mean_pnl": round(mean_pnl, 4),
        "median_pnl": round(float(np.median(pnl)), 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 3),
        "p_one_sided": round(p_one, 5),
        "max_dd_pts": round(max_dd, 2),
        "avg_loss_pts": round(avg_loss, 2),
        "dd_ratio": round(dd_ratio, 1),
        "sharpe_h1": round(sh1, 3),
        "sharpe_h2": round(sh2, 3),
        "annual": annual,
        "criteria": criteria,
        "PASSED": passed,
        "status": "PASS" if passed else "FAIL",
    }


def print_metrics(m):
    if m.get("status") == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA ({m['n_trades']})")
        return
    icon = "PASS" if m["PASSED"] else "FAIL"
    print(f"\n  [{icon}] {m['label']}")
    print(f"    n={m['n_trades']:3d}  mean={m['mean_pnl']:+.4f}pts  "
          f"median={m['median_pnl']:+.4f}pts  "
          f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.3f}  "
          f"p={m['p_one_sided']:.5f}")
    print(f"    MaxDD={m['max_dd_pts']:.1f}pts  AvgLoss={m['avg_loss_pts']:.2f}pts  "
          f"DD/AvgLoss={m['dd_ratio']:.1f}  "
          f"Sharpe[H1/H2]={m['sharpe_h1']:.3f}/{m['sharpe_h2']:.3f}")
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED: {', '.join(fails)}")
    annual = m.get("annual")
    if annual is not None and len(annual) > 0:
        print(f"    Annual:")
        for yr, row in annual.iterrows():
            print(f"      {yr}: n={int(row['count']):2d}  "
                  f"mean={row['mean']:+.2f}pts  total={row['sum']:+.1f}pts")


def sep(title=""):
    print(f"\n{'='*70}\n  {title}\n{'='*70}" if title else f"\n{'='*70}")


# -----------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-tz",     default="UTC")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--col-open",      default="open")
    parser.add_argument("--col-high",      default="high")
    parser.add_argument("--col-low",       default="low")
    parser.add_argument("--col-close",     default="close")
    parser.add_argument("--col-volume",    default="volume")
    args = parser.parse_args()

    df_1m = load_data(args.input, args.col_timestamp,
                      args.col_open, args.col_high, args.col_low,
                      args.col_close, args.col_volume)

    # Split FOMC dates
    fomc_is  = [d for d in FOMC_DATES if d <= IS_END]
    fomc_oos = [d for d in FOMC_DATES if OOS_START <= d <= OOS_END]

    sep("FOMC PRE-ANNOUNCEMENT DRIFT -- HYPOTHESIS 6")
    print(f"  Calendar anomaly. Long-only before FOMC announcements.")
    print(f"  Academic basis: Lucca & Moench (2015), FRBNY Staff Report")
    print(f"  FOMC dates in dataset: IS={len(fomc_is)}, OOS={len(fomc_oos)}")
    print(f"  Variants: {N_VARIANTS}  |  Bonferroni alpha: {BONFERRONI_P:.4f}")
    print(f"  Cost: MES {MES_COST_PTS}pts/RT")

    # Diagnostic: average FOMC day return vs non-FOMC day
    sep("DIAGNOSTIC -- FOMC vs non-FOMC day returns (full sample)")
    daily = df_1m.groupby(df_1m.index.date).agg(
        day_open=("open", "first"), day_close=("close", "last"))
    daily["ret_pts"] = daily["day_close"] - daily["day_open"]
    daily["is_fomc"] = daily.index.isin(FOMC_DATES_SET)

    fomc_ret = daily[daily["is_fomc"]]["ret_pts"]
    non_fomc_ret = daily[~daily["is_fomc"]]["ret_pts"]
    print(f"  FOMC days:     n={len(fomc_ret):3d}  mean={fomc_ret.mean():+.2f}pts  "
          f"std={fomc_ret.std():.2f}")
    print(f"  Non-FOMC days: n={len(non_fomc_ret):3d}  mean={non_fomc_ret.mean():+.2f}pts  "
          f"std={non_fomc_ret.std():.2f}")
    t_diag, p_diag = stats.ttest_ind(fomc_ret, non_fomc_ret)
    print(f"  Difference t-test: t={t_diag:.3f}  p={p_diag:.4f}")

    # Run variants
    is_res, oos_res = [], []

    for v in VARIANTS:
        lbl = v["name"]

        t_is  = run_variant(df_1m, fomc_is,  v["entry_time"], v["exit_time"], MES_COST_PTS)
        t_oos = run_variant(df_1m, fomc_oos, v["entry_time"], v["exit_time"], MES_COST_PTS)

        is_res.append( compute_metrics(t_is,  f"IS  | {lbl}", BONFERRONI_P))
        oos_res.append(compute_metrics(t_oos, f"OOS | {lbl}", BONFERRONI_P))

    sep("IN-SAMPLE RESULTS (2010 -- 2018)")
    for m in is_res:
        print_metrics(m)

    sep("OUT-OF-SAMPLE RESULTS (2019 -- 2024)")
    for m in oos_res:
        print_metrics(m)

    # Verdict
    sep("VERDICT")
    oos_pass = [m for m in oos_res if m.get("PASSED")]

    if oos_pass:
        print(f"\n  SIGNAL DETECTED -- {len(oos_pass)} variant(s) passed:")
        for m in oos_pass:
            print(f"    -> {m['label']}")
            print(f"       mean={m['mean_pnl']:+.4f}pts  WR={m['win_rate']:.1%}  "
                  f"PF={m['profit_factor']:.3f}  p={m['p_one_sided']:.5f}")
        print(f"\n  NOTE: ~8 trades/year limits statistical confidence.")
        print(f"  This is a COMPONENT for an ensemble, not standalone.")
        print(f"  NEXT: Consider combining with H5b RSI mean-reversion.")
    else:
        best = max(oos_res, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  HYPOTHESIS REJECTED.")
        print(f"\n  Best OOS: {best['label']}")
        print(f"    mean={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}")
        print(f"\n  Low n (~48 OOS trades) makes rejection less definitive.")
        print(f"  The effect may exist but be too weak or variable for")
        print(f"  our sample size to detect at alpha={BONFERRONI_P:.4f}.")

    # Also run ES cost for comparison
    sep("REFERENCE: ES cost comparison (best variant)")
    best_v = VARIANTS[0]  # prior_close->post_fomc (classic Lucca-Moench)
    t_oos_es = run_variant(df_1m, fomc_oos, best_v["entry_time"],
                           best_v["exit_time"], ES_COST_PTS)
    m_es = compute_metrics(t_oos_es, f"OOS | {best_v['name']} (ES cost)", BONFERRONI_P)
    print_metrics(m_es)

    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()