"""
HYPOTHESIS 3 v2: VWAP Mean Reversion (Corrected Stop Model)
=============================================================
Amendment: Stop changed from 1.5×band_width to ATR-based fixed stop.
           Volume filter removed (shown to be noise in v1).
           ATR multiplier added as third search axis.
Pre-registered: 2026-04-23 (amendment to H3 v1).
One retest permitted. No further amendments.

Instrument : ES continuous futures (1-minute bars)
Costs      : $30/round-turn = 1.2 pts on ES
"""

import argparse
import warnings
from itertools import product

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# FROZEN PARAMETERS
# ─────────────────────────────────────────────
IS_END       = "2018-12-31"
OOS_START    = "2019-01-01"
OOS_END      = "2024-12-31"

RTH_START_H  = 9;  RTH_START_M  = 31   # first tradeable minute
ENTRY_CUTOFF_H = 15; ENTRY_CUTOFF_M = 0
FORCE_EXIT_H = 15;  FORCE_EXIT_M = 55

COST_PTS     = 1.2          # round-turn in ES points
ATR_PERIOD   = 5            # fast ATR for intraday stop sizing
MAX_TRADES   = 4            # per day ceiling

# 9 variants: band_mult × atr_stop_mult
BAND_MULTS   = [1.5, 2.0, 2.5]
ATR_MULTS    = [0.5, 1.0, 1.5]
N_VARIANTS   = len(BAND_MULTS) * len(ATR_MULTS)

BONFERRONI_P      = 0.10 / N_VARIANTS   # 0.0111
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

    # RTH only
    t = df.index.time
    from datetime import time
    df = df[(t >= time(RTH_START_H, RTH_START_M)) & (t <= time(15, 59))]
    print(f"  {len(df):,} RTH bars  {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ─────────────────────────────────────────────
# FEATURE ENGINEERING (strictly causal)
# ─────────────────────────────────────────────

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = df.index.date

    # ── Session VWAP (reset each day) ──
    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    df["cum_tpv"] = df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"] = df.groupby("date")["volume"].cumsum()
    df["vwap"]    = df["cum_tpv"] / df["cum_vol"]

    # ── Session expanding σ of close (for band calculation) ──
    def _expanding_std(grp):
        return grp["close"].expanding().std()
    df["sess_std"] = df.groupby("date", group_keys=False).apply(_expanding_std)
    df["sess_std"] = df["sess_std"].bfill().clip(lower=1e-6)

    # ── VWAP Z-score ──
    df["vwap_z"] = (df["close"] - df["vwap"]) / df["sess_std"]

    # ── ATR-5 (causal rolling, NOT reset per session) ──
    # True range
    prev_close = df["close"].shift(1)
    df["tr"] = np.maximum(df["high"] - df["low"],
               np.maximum(abs(df["high"] - prev_close),
                          abs(df["low"]  - prev_close)))
    df["atr5"] = df["tr"].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    return df


# ─────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────

def run_variant(df: pd.DataFrame, band_mult: float, atr_mult: float) -> pd.DataFrame:
    """
    Entry : |vwap_z| > band_mult → fade (short if z>0, long if z<0)
    Stop  : atr_mult × ATR-5 beyond entry price  (fixed at entry, not expanding)
    Target: price crosses session VWAP
    Force : 15:55 ET bar close
    """
    from datetime import time
    entry_cutoff = time(ENTRY_CUTOFF_H, ENTRY_CUTOFF_M)
    force_exit   = time(FORCE_EXIT_H,  FORCE_EXIT_M)

    trades   = []
    in_trade = False
    ep = direction = stop = None
    entry_time = None
    daily_n  = 0
    prev_date = None

    for ts, row in df.iterrows():
        bar_time = ts.time()
        bar_date = ts.date()

        if bar_date != prev_date:
            daily_n   = 0
            prev_date = bar_date

        if np.isnan(row["atr5"]) or np.isnan(row["vwap_z"]):
            continue

        # ── Force exit ──
        if in_trade and bar_time >= force_exit:
            pnl = (row["close"] - ep) * direction - COST_PTS
            trades.append(_trade(entry_time, ts, ep, row["close"],
                                 direction, pnl, "EOD", band_mult, atr_mult))
            in_trade = False
            continue

        # ── Manage open trade ──
        if in_trade:
            # Stop check (use bar extremes)
            stopped = (direction == 1  and row["low"]  <= stop) or \
                      (direction == -1 and row["high"] >= stop)
            if stopped:
                pnl = (stop - ep) * direction - COST_PTS
                trades.append(_trade(entry_time, ts, ep, stop,
                                     direction, pnl, "STOP", band_mult, atr_mult))
                in_trade = False
                continue

            # Target: VWAP reversion
            vwap = row["vwap"]
            crossed = (direction == 1  and row["high"] >= vwap) or \
                      (direction == -1 and row["low"]  <= vwap)
            if crossed:
                pnl = (vwap - ep) * direction - COST_PTS
                trades.append(_trade(entry_time, ts, ep, vwap,
                                     direction, pnl, "TARGET", band_mult, atr_mult))
                in_trade = False
                continue

        # ── Entry ──
        if (not in_trade
                and bar_time <= entry_cutoff
                and daily_n < MAX_TRADES
                and abs(row["vwap_z"]) > band_mult):

            direction   = -1 if row["vwap_z"] > 0 else 1
            ep          = row["close"]
            entry_time  = ts
            stop_dist   = atr_mult * row["atr5"]
            stop        = ep - direction * stop_dist
            in_trade    = True
            daily_n    += 1

    return pd.DataFrame(trades) if trades else _empty_trades()


def _trade(et, xt, ep, xp, d, pnl, reason, bm, am):
    return {"entry_time": et, "exit_time": xt, "entry_price": ep,
            "exit_price": xp, "direction": d, "pnl_pts": pnl,
            "exit_reason": reason, "band_mult": bm, "atr_mult": am}


def _empty_trades():
    return pd.DataFrame(columns=["entry_time","exit_time","entry_price",
                                  "exit_price","direction","pnl_pts",
                                  "exit_reason","band_mult","atr_mult"])


# ─────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, label: str) -> dict:
    if len(trades) < 10:
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

    t_stat, p2 = stats.ttest_1samp(pnl, 0)
    p_one = p2 / 2 if t_stat > 0 else 1.0

    equity = np.cumsum(pnl)
    max_dd = abs((equity - np.maximum.accumulate(equity)).min())

    stop_trades = trades[trades["exit_reason"] == "STOP"]
    avg_stop = abs((stop_trades["pnl_pts"] + COST_PTS)).mean() \
               if len(stop_trades) > 0 else max(abs(pnl).mean(), 0.01)
    avg_stop = max(avg_stop, 0.01)
    dd_ratio = max_dd / avg_stop

    mid = n // 2
    def sharpe(x): return x.mean() / (x.std() + 1e-9) * np.sqrt(252 * 6.5 * 60)
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
    if m["status"] == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA"); return
    icon = "✓" if m["PASSED"] else "✗"
    print(f"\n  [{icon} {m['status']}] {m['label']}")
    print(f"    n={m['n_trades']:4d}  mean={m['mean_pnl']:+.4f}pts  "
          f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.3f}  "
          f"p={m['p_one_sided']:.5f}")
    print(f"    MaxDD={m['max_dd_pts']:.1f}pts  AvgStop={m['avg_stop_pts']:.2f}pts  "
          f"DD/Stop={m['dd_ratio']:.1f}  "
          f"Sharpe[H1/H2]={m['sharpe_h1']:.3f}/{m['sharpe_h2']:.3f}")
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED: {', '.join(fails)}")


def sep(title=""): print(f"\n{'='*70}\n  {title}\n{'='*70}" if title
                         else f"\n{'='*70}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-tz",      default="UTC")
    parser.add_argument("--col-timestamp",  default="ts_event")
    parser.add_argument("--col-open",       default="open")
    parser.add_argument("--col-high",       default="high")
    parser.add_argument("--col-low",        default="low")
    parser.add_argument("--col-close",      default="close")
    parser.add_argument("--col-volume",     default="volume")
    args = parser.parse_args()

    df_raw  = load_data(args.input, args.source_tz, args.col_timestamp,
                        args.col_open, args.col_high, args.col_low,
                        args.col_close, args.col_volume)
    df_feat = add_features(df_raw)

    df_is  = df_feat[df_feat.index <= IS_END]
    df_oos = df_feat[(df_feat.index >= OOS_START) & (df_feat.index <= OOS_END)]

    sep(f"VWAP MEAN REVERSION v2 – H3 AMENDED (ATR STOPS)")
    print(f"  IS : {df_is.index[0].date()} → {df_is.index[-1].date()} "
          f"({len(df_is):,} bars)")
    print(f"  OOS: {df_oos.index[0].date()} → {df_oos.index[-1].date()} "
          f"({len(df_oos):,} bars)")
    print(f"  Variants: {N_VARIANTS}  |  Bonferroni α: {BONFERRONI_P:.5f}"
          f"  |  Cost: {COST_PTS}pts/RT")
    print(f"  Stop model: atr_mult × ATR-{ATR_PERIOD}  "
          f"(fixed at entry, not expanding)")

    is_res  = []
    oos_res = []

    for bm, am in product(BAND_MULTS, ATR_MULTS):
        lbl = f"band={bm:.1f}σ  atr_stop={am:.1f}×ATR5"
        is_res.append( compute_metrics(run_variant(df_is,  bm, am), f"IS  | {lbl}"))
        oos_res.append(compute_metrics(run_variant(df_oos, bm, am), f"OOS | {lbl}"))

    sep("IN-SAMPLE RESULTS (2010 – 2018)")
    for m in is_res:
        print_metrics(m)

    sep("OUT-OF-SAMPLE RESULTS (2019 – 2024)")
    for m in oos_res:
        print_metrics(m)

    sep("VERDICT")
    oos_pass = [m for m in oos_res if m.get("PASSED")]

    if oos_pass:
        print(f"\n  ✓ SIGNAL DETECTED — {len(oos_pass)} variant(s) passed OOS:")
        for m in oos_pass:
            print(f"    → {m['label']}")
        print("\n  NEXT: robustness checks before any infrastructure.")
        print("  Paste full output to research partner.")
    else:
        best = max(oos_res, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  ✗ HYPOTHESIS REJECTED (H3 final — no further amendments).")
        print(f"\n  Best OOS: {best['label']}")
        print(f"    mean={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}")
        print(f"\n  Pattern across H1–H3: simple price-level strategies"
              f" (gap, breakout, reversion)")
        print(f"  do not survive ES costs. Suggests edge requires either:")
        print(f"    (a) time-of-day/calendar structure, or")
        print(f"    (b) a secondary confirming signal beyond price alone.")
        print(f"\n  NEXT HYPOTHESIS: Late-Session Drift (MOC imbalance effect).")
        print(f"  Paste full output to research partner.")

    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()