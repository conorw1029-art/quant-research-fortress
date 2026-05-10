"""
HYPOTHESIS 4: Intraday Momentum
==================================
Pre-registered feasibility study. Replication of:
  Gao, Han, Li & Zhou (2018) "Market intraday momentum"
  Journal of Financial Economics, Vol 129, Issue 2

Thesis: The return in the first k minutes of RTH predicts the sign
        and magnitude of the return in the LAST 30 minutes of RTH
        on the same trading day.

Instrument : ES continuous futures (1-minute bars)
Costs      : $30/round-turn = 1.2 points per round-turn on ES
Author     : Quant Research Factory – Hypothesis 4
Date       : 2026-04-23
Status     : PRE-REGISTERED – 4 variants only. No post-hoc additions.
"""

import argparse
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
IS_END      = "2018-12-31"
OOS_START   = "2019-01-01"
OOS_END     = "2024-12-31"

RTH_OPEN    = time(9, 30)        # first RTH minute
ENTRY_TIME  = time(15, 0)        # go long/short here (start of last 30 min + 25 min)
FORCE_EXIT  = time(15, 55)       # force close

COST_PTS    = 1.2                # round-turn ES points

# Daily ATR for magnitude filter (causal, shifted)
DAILY_ATR_PERIOD = 14

# 4 pre-registered variants
SIGNAL_WINDOWS  = [30, 60]                # minutes of RTH used for signal
MAGNITUDE_FILTS = [None, 0.25]            # None = no filter, 0.25 = signal >= 0.25 × daily ATR

# Pass/fail thresholds
N_VARIANTS        = len(SIGNAL_WINDOWS) * len(MAGNITUDE_FILTS)
BONFERRONI_P      = 0.10 / N_VARIANTS     # 0.025
MIN_PROFIT_FACTOR = 1.25
MIN_WIN_RATE      = 0.40
MAX_DD_STOPS      = 20.0


# ─────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────

def load_data(path, source_tz="UTC", col_ts="ts_event",
              col_open="open", col_high="high", col_low="low",
              col_close="close", col_volume="volume") -> pd.DataFrame:
    print(f"Loading {path} ...")
    df = pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df[col_ts], utc=True)
    df = df.set_index("ts").sort_index()
    df.index = df.index.tz_convert("America/New_York")
    df = df.rename(columns={col_open:"open", col_high:"high", col_low:"low",
                             col_close:"close", col_volume:"volume"}
                   )[["open","high","low","close","volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna()

    # Keep full RTH: 9:30 to 16:00
    t = df.index.time
    df = df[(t >= RTH_OPEN) & (t <= time(15, 59))]
    print(f"  {len(df):,} RTH bars  {df.index[0].date()} -> {df.index[-1].date()}")
    return df


# ─────────────────────────────────────────────
# DAILY AGGREGATION (extract signal + outcome)
# ─────────────────────────────────────────────

def build_daily_table(df: pd.DataFrame, signal_window_min: int) -> pd.DataFrame:
    """
    For each trading day, compute:
      - open_price     : price at 09:30 ET
      - signal_close   : price at 09:30 + signal_window_min
      - signal_ret_pts : signal_close - open_price (ES points)
      - entry_price    : price at 15:00 ET
      - exit_price     : price at 15:55 ET
      - daily_atr      : 14-day ATR (causal, shifted by 1 day)
    """
    df = df.copy()
    df["date"] = df.index.date
    df["t"]    = df.index.time

    signal_end = (pd.Timestamp("2000-01-01 09:30") +
                  pd.Timedelta(minutes=signal_window_min)).time()

    # For each day pull the four bars we care about
    def _first_at_or_after(group: pd.DataFrame, target: time) -> float:
        mask = group["t"] >= target
        return group.loc[mask, "open"].iloc[0] if mask.any() else np.nan

    def _last_at_or_before(group: pd.DataFrame, target: time) -> float:
        mask = group["t"] <= target
        return group.loc[mask, "close"].iloc[-1] if mask.any() else np.nan

    daily_rows = []
    for date, grp in df.groupby("date"):
        open_px   = _first_at_or_after(grp, RTH_OPEN)
        sig_close = _last_at_or_before(grp, signal_end)
        entry_px  = _first_at_or_after(grp, ENTRY_TIME)
        exit_px   = _last_at_or_before(grp, FORCE_EXIT)
        day_high  = grp["high"].max()
        day_low   = grp["low"].min()

        daily_rows.append({
            "date":       date,
            "open":       open_px,
            "signal_end": sig_close,
            "entry":      entry_px,
            "exit":       exit_px,
            "high":       day_high,
            "low":        day_low,
        })

    daily = pd.DataFrame(daily_rows).dropna().reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.set_index("date").sort_index()

    # Signal = return over first signal_window_min
    daily["signal_ret_pts"] = daily["signal_end"] - daily["open"]

    # Causal daily ATR (for magnitude filter)
    prev_close = daily["exit"].shift(1)
    tr = np.maximum(daily["high"] - daily["low"],
         np.maximum(abs(daily["high"] - prev_close),
                    abs(daily["low"]  - prev_close)))
    daily["daily_atr"] = tr.rolling(DAILY_ATR_PERIOD,
                                     min_periods=DAILY_ATR_PERIOD).mean().shift(1)

    return daily.dropna()


# ─────────────────────────────────────────────
# VARIANT RUNNER
# ─────────────────────────────────────────────

def run_variant(daily: pd.DataFrame,
                signal_window_min: int,
                magnitude_filt) -> pd.DataFrame:
    """
    Entry : 15:00 ET, direction = sign(signal_ret_pts)
    Filter: if magnitude_filt is not None, require
            |signal_ret_pts| >= magnitude_filt × daily_atr
    Exit  : 15:55 ET close
    """
    d = daily.copy()

    # Filter on magnitude
    if magnitude_filt is not None:
        threshold = magnitude_filt * d["daily_atr"]
        d = d[abs(d["signal_ret_pts"]) >= threshold]

    # Drop zero-signal days
    d = d[d["signal_ret_pts"] != 0]

    if len(d) == 0:
        return _empty_trades()

    direction = np.sign(d["signal_ret_pts"]).astype(int)
    gross_pnl = (d["exit"] - d["entry"]) * direction
    net_pnl   = gross_pnl - COST_PTS

    trades = pd.DataFrame({
        "date":           d.index,
        "entry_price":    d["entry"].values,
        "exit_price":     d["exit"].values,
        "direction":      direction.values,
        "signal_ret_pts": d["signal_ret_pts"].values,
        "pnl_pts":        net_pnl.values,
        "signal_window":  signal_window_min,
        "magnitude_filt": magnitude_filt if magnitude_filt is not None else 0.0,
    })
    return trades


def _empty_trades():
    return pd.DataFrame(columns=["date","entry_price","exit_price","direction",
                                  "signal_ret_pts","pnl_pts",
                                  "signal_window","magnitude_filt"])


# ─────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, label: str) -> dict:
    if len(trades) < 30:
        return {"label": label, "n_trades": len(trades), "PASSED": False,
                "status": "INSUFFICIENT DATA", "mean_pnl": -999}

    pnl  = trades["pnl_pts"].values
    n    = len(pnl)
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]

    mean_pnl  = pnl.mean()
    win_rate  = len(wins) / n
    gross_win = wins.sum() if len(wins) else 0.0
    gross_los = abs(losses.sum()) if len(losses) else 1e-9
    pf        = gross_win / gross_los

    # One-sided t-test: H0: mean_pnl <= 0
    t_stat, p2 = stats.ttest_1samp(pnl, 0)
    p_one = p2 / 2 if t_stat > 0 else 1.0

    equity = np.cumsum(pnl)
    max_dd = abs((equity - np.maximum.accumulate(equity)).min())

    # "Stop" equivalent for intraday-momentum = average absolute loss
    loss_sizes = abs(losses) if len(losses) else np.array([1.0])
    avg_stop   = max(loss_sizes.mean(), 0.01)
    dd_ratio   = max_dd / avg_stop

    # Annualised Sharpe (~252 trades/year, 1 per day)
    mid = n // 2
    def sharpe(x):
        return x.mean() / (x.std() + 1e-9) * np.sqrt(252)
    sh1 = sharpe(pnl[:mid]); sh2 = sharpe(pnl[mid:])

    criteria = {
        "mean_pnl_positive": mean_pnl > 0,
        "p_value":           p_one < BONFERRONI_P,
        "profit_factor":     pf >= MIN_PROFIT_FACTOR,
        "win_rate":          win_rate >= MIN_WIN_RATE,
        "dd_ratio":          dd_ratio <= MAX_DD_STOPS,
        "both_halves_pos":   sh1 > 0 and sh2 > 0,
    }
    passed = all(criteria.values())

    return {
        "label": label, "n_trades": n,
        "mean_pnl": round(mean_pnl, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 3),
        "p_one_sided": round(p_one, 5),
        "max_dd_pts": round(max_dd, 2),
        "avg_stop_pts": round(avg_stop, 2),
        "dd_ratio": round(dd_ratio, 1),
        "sharpe_h1": round(sh1, 3),
        "sharpe_h2": round(sh2, 3),
        "criteria": criteria,
        "PASSED": passed,
        "status": "PASS" if passed else "FAIL",
    }


def print_metrics(m: dict) -> None:
    if m.get("status") == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA ({m['n_trades']} trades)"); return
    icon = "[PASS]" if m["PASSED"] else "[FAIL]"
    print(f"\n  [{icon} {m['status']}] {m['label']}")
    print(f"    n={m['n_trades']:4d}  mean={m['mean_pnl']:+.4f}pts  "
          f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.3f}  "
          f"p={m['p_one_sided']:.5f}")
    print(f"    MaxDD={m['max_dd_pts']:.1f}pts  AvgLoss={m['avg_stop_pts']:.2f}pts  "
          f"DD/AvgLoss={m['dd_ratio']:.1f}  "
          f"Sharpe[H1/H2]={m['sharpe_h1']:.3f}/{m['sharpe_h2']:.3f}")
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED: {', '.join(fails)}")


def sep(title=""):
    print(f"\n{'='*70}\n  {title}\n{'='*70}" if title else f"\n{'='*70}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

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

    df = load_data(args.input, args.source_tz, args.col_timestamp,
                   args.col_open, args.col_high, args.col_low,
                   args.col_close, args.col_volume)

    # Build daily tables for each signal window
    daily_30 = build_daily_table(df, 30)
    daily_60 = build_daily_table(df, 60)

    sep("INTRADAY MOMENTUM – HYPOTHESIS 4")
    print(f"  Replication of Gao, Han, Li & Zhou (2018), JFE 129(2)")
    print(f"  Pre-registered. 4 variants only. No post-hoc additions.")
    print(f"  Signal: first N-min return -> predicts sign of entry->exit return")
    print(f"  Entry: 15:00 ET  Exit: 15:55 ET  Cost: {COST_PTS}pts/RT")
    print(f"  Bonferroni alpha: {BONFERRONI_P:.5f}")

    # ── Quick diagnostic: predictive correlation ──
    sep("DIAGNOSTIC – Signal/Outcome Correlation (full sample)")
    for window, daily_tbl in [(30, daily_30), (60, daily_60)]:
        outcome = daily_tbl["exit"] - daily_tbl["entry"]
        signal  = daily_tbl["signal_ret_pts"]
        corr    = signal.corr(outcome)
        # Spearman rank correlation for robustness
        rho, p_rho = stats.spearmanr(signal, outcome)
        # Sign agreement
        sign_match = (np.sign(signal) == np.sign(outcome)).mean()
        print(f"  signal_window={window}m  n={len(daily_tbl):4d}  "
              f"Pearson={corr:+.4f}  Spearman_rho={rho:+.4f} (p={p_rho:.4f})  "
              f"sign_match={sign_match:.1%}")

    # Split IS/OOS
    is_mask  = lambda d: d.index <= IS_END
    oos_mask = lambda d: (d.index >= OOS_START) & (d.index <= OOS_END)

    is_res, oos_res = [], []

    for sw, mf in product(SIGNAL_WINDOWS, MAGNITUDE_FILTS):
        daily_tbl = daily_30 if sw == 30 else daily_60
        d_is  = daily_tbl[is_mask(daily_tbl)]
        d_oos = daily_tbl[oos_mask(daily_tbl)]

        mf_str = "no_filter" if mf is None else f"|sig|>={mf}×dATR"
        lbl    = f"window={sw}m  {mf_str}"

        is_res.append( compute_metrics(run_variant(d_is,  sw, mf), f"IS  | {lbl}"))
        oos_res.append(compute_metrics(run_variant(d_oos, sw, mf), f"OOS | {lbl}"))

    sep(f"IN-SAMPLE RESULTS  (2010 – 2018)")
    for m in is_res:
        print_metrics(m)

    sep(f"OUT-OF-SAMPLE RESULTS  (2019 – 2024)")
    for m in oos_res:
        print_metrics(m)

    sep("VERDICT")
    oos_pass = [m for m in oos_res if m.get("PASSED")]

    if oos_pass:
        print(f"\n  [PASS] SIGNAL DETECTED — {len(oos_pass)} variant(s) passed OOS:")
        for m in oos_pass:
            print(f"    -> {m['label']}")
        print("\n  DO NOT proceed to infrastructure yet.")
        print("  Next: robustness checks (walk-forward, regime decomp, bootstrap CIs).")
        print("  Paste full output to research partner.")
    else:
        best = max(oos_res, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  [FAIL] HYPOTHESIS REJECTED.")
        print(f"\n  Best OOS: {best['label']}")
        print(f"    mean={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}  "
              f"WR={best.get('win_rate',0):.1%}")
        print(f"\n  Pattern now spans H1–H4. Four rejections is a data point.")
        print(f"  Recommend PAUSE before H5: review whether continued single-factor")
        print(f"  testing is the right direction, or whether we need to reassess the")
        print(f"  premise (e.g. move to risk-premia strategies, or alternative data).")
        print(f"\n  Do NOT reflexively code H5. Discuss with research partner first.")

    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()