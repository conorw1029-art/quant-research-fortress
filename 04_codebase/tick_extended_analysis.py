#!/usr/bin/env python3
"""
L2 Extended Analysis — Full Real-World Robustness Suite (Part 2)
================================================================
Picks up where tick_deep_analysis.py left off. Adds:

  1. MAE/MFE per-trade quality analysis + stop optimisation
  2. Time-of-day breakdown (UTC hourly P&L, WR, Sharpe)
  3. Day-of-week analysis
  4. Market-session breakdown (Asian / London / US)
  5. Consecutive-trade / streak analysis
  6. Personal $2,000 drawdown MC  — starting from current -$1,000 per-account state
  7. Hours-filtered backtest       — trade only in net-positive hours
  8. Tighter-stop sensitivity      — re-run with stop_mult 0.75 / 1.0 / 1.5 / 2.0
  9. Account assignment plan for 10 funded accounts
  10. Final GO/NO-GO table per strategy

Usage:
  python tick_extended_analysis.py
"""

import json
import sys
import warnings
from pathlib import Path
from datetime import datetime
from collections import Counter

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT       = Path(__file__).parent.parent
RESULT_DIR = ROOT / "05_backtests"
BAR_DIR    = ROOT / "01_data" / "tick_bars"
OHLCV_PNL  = RESULT_DIR / "daily_portfolio_pnl.csv"

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, compute_atr
from tick_strategies import STRATEGY_MAP

# ── User-specific constraints ─────────────────────────────────────────────────
CURRENT_DD_PER_ACCOUNT = -1_000   # each account currently at -$1,000 from peak
PERSONAL_STOP          = -2_000   # personal hard stop per account
TOPSTEP_DAILY          =  4_500   # Topstep absolute daily loss limit
N_ACCOUNTS             = 10

# ── Analysis knobs ────────────────────────────────────────────────────────────
TOP_N      = 15       # de-duped survivors to analyse
MC_SIMS    = 10_000
SLIP_TICKS = 0.5      # baseline slippage

RNG = np.random.default_rng(42)

# ── UTC session windows (hour >= start and hour < end) ───────────────────────
SESSIONS = {
    "Asian":  (22, 8),   # wraps midnight: 22-23 + 0-7
    "London": (7,  13),
    "US":     (13, 21),
}

def in_session(hour: int, session: str) -> bool:
    s, e = SESSIONS[session]
    if s < e:
        return s <= hour < e
    return hour >= s or hour < e   # wraps midnight


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def tick_dollar(symbol: str) -> float:
    s = SPECS[symbol]
    return s["tick_size"] * s["point_value"]


def sharpe_from_daily(d: pd.Series) -> float:
    if len(d) < 5 or d.std() == 0:
        return 0.0
    return (d.mean() / d.std()) * np.sqrt(252)


def daily_pnl_series(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    t = trades.copy()
    t["date"] = pd.to_datetime(t["entry_time"]).dt.date
    s = t.groupby("date")["dollar_pnl"].sum()
    s.index = pd.to_datetime(s.index)
    return s


# ═══════════════════════════════════════════════════════════════════════════════
#  Modified backtest: tracks MAE, MFE, hold_bars, exit_reason per trade
# ═══════════════════════════════════════════════════════════════════════════════

def run_backtest_full(bars: pd.DataFrame, signals: pd.Series, symbol: str,
                      stop_mult: float = 1.5, tp_mult: float = 3.0,
                      max_hold: int = 50, atr_window: int = 14,
                      extra_ticks: float = 0.5,
                      commission_per_side: float = 3.0,
                      allowed_hours: set | None = None) -> pd.DataFrame:
    """
    Full backtest with MAE/MFE tracking and optional hour filter.
    allowed_hours: set of UTC hours (0-23) to accept signals in.
                   None = all hours.
    """
    spec = SPECS[symbol]
    pv   = spec["point_value"]
    slip = extra_ticks * spec["tick_size"]

    hi  = bars["high"].values
    lo  = bars["low"].values
    cl  = bars["close"].values
    sig = signals.reindex(bars.index).fillna(0).astype(int).values
    ts  = bars.index                    # DatetimeIndex, UTC
    n   = len(cl)

    atr = compute_atr(hi, lo, cl, atr_window)
    trades = []
    in_pos = False
    direction = 0; entry_bar = -1; entry_px = 0.0; stop_px = 0.0; target_px = 0.0
    mae = 0.0; mfe = 0.0

    for i in range(n):
        if not in_pos:
            if sig[i] != 0 and not np.isnan(atr[i]):
                if allowed_hours is not None and ts[i].hour not in allowed_hours:
                    continue
                direction  = int(sig[i])
                entry_bar  = i
                entry_px   = cl[i] + direction * slip
                a          = atr[i]
                stop_px    = entry_px - direction * stop_mult * a
                target_px  = entry_px + direction * tp_mult   * a
                mae        = 0.0
                mfe        = 0.0
                in_pos     = True
            continue

        hold = i - entry_bar

        # Update MAE / MFE using this bar's high/low
        if direction == 1:
            exc_adv = lo[i] - entry_px   # negative when below entry
            exc_fav = hi[i] - entry_px   # positive when above entry
        else:
            exc_adv = -(hi[i] - entry_px)  # entry - hi
            exc_fav = -(lo[i] - entry_px)  # entry - lo

        mae = min(mae, exc_adv)  # most negative
        mfe = max(mfe, exc_fav)  # most positive

        exit_px = None; exit_reason = None

        if direction == 1 and lo[i] <= stop_px:
            exit_px, exit_reason = stop_px, "stop"
        elif direction == -1 and hi[i] >= stop_px:
            exit_px, exit_reason = stop_px, "stop"
        elif direction == 1 and hi[i] >= target_px:
            exit_px, exit_reason = target_px, "target"
        elif direction == -1 and lo[i] <= target_px:
            exit_px, exit_reason = target_px, "target"
        elif hold >= max_hold:
            exit_px, exit_reason = cl[i], "timeout"
        elif sig[i] != 0 and sig[i] != direction:
            exit_px, exit_reason = cl[i], "signal"

        if exit_px is not None:
            actual_exit = exit_px - direction * slip
            raw_pnl     = direction * (actual_exit - entry_px) * pv
            dollar_pnl  = raw_pnl - 2.0 * commission_per_side

            trades.append({
                "entry_bar":   entry_bar,
                "exit_bar":    i,
                "entry_time":  ts[entry_bar],
                "exit_time":   ts[i],
                "direction":   direction,
                "entry_px":    entry_px,
                "exit_px":     actual_exit,
                "hold_bars":   hold,
                "exit_reason": exit_reason,
                "dollar_pnl":  dollar_pnl,
                "mae_pts":     round(mae, 6),    # adverse (negative = went against)
                "mfe_pts":     round(mfe, 6),    # favorable (positive = went for)
                "mae_usd":     round(mae * pv, 2),
                "mfe_usd":     round(mfe * pv, 2),
                "entry_hour":  ts[entry_bar].hour,
                "entry_dow":   ts[entry_bar].dayofweek,  # 0=Mon, 4=Fri
            })

            in_pos = False
            if sig[i] != 0 and sig[i] != direction:
                if allowed_hours is None or ts[i].hour in allowed_hours:
                    direction  = int(sig[i])
                    entry_bar  = i
                    entry_px   = cl[i] + direction * slip
                    a          = atr[i] if not np.isnan(atr[i]) else (atr[i-1] if i > 0 else 0)
                    stop_px    = entry_px - direction * stop_mult * a
                    target_px  = entry_px + direction * tp_mult   * a
                    mae        = 0.0; mfe = 0.0
                    in_pos     = True

    return pd.DataFrame(trades)


# ═══════════════════════════════════════════════════════════════════════════════
#  Load survivors + best params (mirrors tick_deep_analysis)
# ═══════════════════════════════════════════════════════════════════════════════

def load_deduped_survivors(n: int = TOP_N) -> list[dict]:
    files = sorted(RESULT_DIR.glob("tick_results_*.json"))
    all_rows = []
    for f in files:
        with open(f) as fh:
            rows = json.load(fh)
        all_rows.extend(rows)

    df   = pd.DataFrame(all_rows)
    surv = df[df["grade"].isin(["EXCELLENT", "GOOD", "MARGINAL"])].copy()
    surv = surv.sort_values("dsr", ascending=False).reset_index(drop=True)

    seen = {}; deduped = []
    for _, row in surv.iterrows():
        key = (row["symbol"], row["strategy"])
        if key not in seen:
            seen[key] = True
            deduped.append(row.to_dict())

    return deduped[:n]


def best_params(row: dict, strat: dict) -> dict:
    fold_results = row.get("fold_results", [])
    if not fold_results or not isinstance(fold_results, list):
        return {}
    param_keys = list(strat["param_grid"].keys())
    bp = {}
    for k in param_keys:
        vals = [f["best_params"].get(k)
                for f in fold_results
                if isinstance(f, dict) and "best_params" in f and k in f["best_params"]]
        if vals:
            bp[k] = Counter(vals).most_common(1)[0][0]
    if not bp and isinstance(fold_results[-1], dict):
        bp = fold_results[-1].get("best_params", {})
    return bp


# ═══════════════════════════════════════════════════════════════════════════════
#  1. MAE / MFE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def mae_mfe_analysis(trades: pd.DataFrame, symbol: str) -> dict:
    if trades.empty:
        return {}

    winners = trades[trades["dollar_pnl"] > 0]
    losers  = trades[trades["dollar_pnl"] <= 0]
    pv = SPECS[symbol]["point_value"]

    def pct(mask): return round(100 * mask.mean(), 1) if len(mask) > 0 else 0

    result = {
        "n_trades": len(trades),
        "n_winners": len(winners),
        "n_losers":  len(losers),
        "win_rate":  round(len(winners) / max(len(trades), 1), 3),
        # Winners: how much did they go against before winning?
        "winner_avg_mae_usd":   round(winners["mae_usd"].mean(), 0) if len(winners) else 0,
        "winner_avg_mfe_usd":   round(winners["mfe_usd"].mean(), 0) if len(winners) else 0,
        "winner_pct_mae_lt_500": pct(winners["mae_usd"] > -500) if len(winners) else 0,
        "winner_pct_mae_lt_1000": pct(winners["mae_usd"] > -1000) if len(winners) else 0,
        # Losers: how much did they favor before losing?
        "loser_avg_mae_usd":    round(losers["mae_usd"].mean(), 0) if len(losers) else 0,
        "loser_avg_mfe_usd":    round(losers["mfe_usd"].mean(), 0) if len(losers) else 0,
        "loser_pct_mfe_gt_500": pct(losers["mfe_usd"] > 500) if len(losers) else 0,
        # Exit reason breakdown
        "pct_stop":    pct(trades["exit_reason"] == "stop"),
        "pct_target":  pct(trades["exit_reason"] == "target"),
        "pct_timeout": pct(trades["exit_reason"] == "timeout"),
        "pct_signal":  pct(trades["exit_reason"] == "signal"),
        # Average hold
        "avg_hold_bars": round(trades["hold_bars"].mean(), 1),
        "median_hold_bars": round(trades["hold_bars"].median(), 1),
    }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  2. TIME-OF-DAY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def time_of_day_analysis(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    rows = []
    for h in range(24):
        t = trades[trades["entry_hour"] == h]
        if len(t) < 5:
            continue
        d = daily_pnl_series(t)
        rows.append({
            "hour_utc": h,
            "n_trades": len(t),
            "total_pnl": round(t["dollar_pnl"].sum(), 0),
            "avg_pnl_per_trade": round(t["dollar_pnl"].mean(), 0),
            "win_rate": round((t["dollar_pnl"] > 0).mean(), 3),
            "sharpe": round(sharpe_from_daily(d), 2) if len(d) >= 5 else 0,
        })

    return pd.DataFrame(rows).sort_values("hour_utc")


# ═══════════════════════════════════════════════════════════════════════════════
#  3. DAY-OF-WEEK ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def day_of_week_analysis(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    rows = []
    for dow in range(5):
        t = trades[trades["entry_dow"] == dow]
        if len(t) < 5:
            continue
        rows.append({
            "day": days[dow],
            "n_trades": len(t),
            "total_pnl": round(t["dollar_pnl"].sum(), 0),
            "avg_pnl_per_trade": round(t["dollar_pnl"].mean(), 0),
            "win_rate": round((t["dollar_pnl"] > 0).mean(), 3),
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. SESSION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def session_analysis(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    result = {}
    for sess in ["Asian", "London", "US"]:
        mask = trades["entry_hour"].apply(lambda h: in_session(h, sess))
        t = trades[mask]
        if len(t) < 5:
            continue
        result[sess] = {
            "n_trades": len(t),
            "total_pnl": round(t["dollar_pnl"].sum(), 0),
            "avg_pnl_per_trade": round(t["dollar_pnl"].mean(), 0),
            "win_rate": round((t["dollar_pnl"] > 0).mean(), 3),
        }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  5. CONSECUTIVE TRADE (STREAK) ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def streak_analysis(trades: pd.DataFrame) -> dict:
    if len(trades) < 20:
        return {}

    wins = (trades["dollar_pnl"] > 0).astype(int).values
    n    = len(wins)

    # Max win/loss streak
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for w in wins:
        if w:
            cur_win += 1; cur_loss = 0
        else:
            cur_loss += 1; cur_win = 0
        max_win_streak  = max(max_win_streak,  cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    # P(win | previous was win) vs P(win | previous was loss)
    prev_win  = wins[:-1] == 1
    prev_loss = wins[:-1] == 0
    curr      = wins[1:]

    p_win_after_win  = curr[prev_win].mean()  if prev_win.sum()  > 0 else 0
    p_win_after_loss = curr[prev_loss].mean() if prev_loss.sum() > 0 else 0

    # Average P&L after N consecutive losses (recovery tendency)
    consec_loss_pnl = {}
    for n_losses in [1, 2, 3, 4]:
        pnls = []
        i = 0
        while i < len(wins) - n_losses:
            if all(wins[i:i+n_losses] == 0):
                if i + n_losses < len(trades):
                    pnls.append(trades.iloc[i + n_losses]["dollar_pnl"])
            i += 1
        if pnls:
            consec_loss_pnl[n_losses] = round(np.mean(pnls), 0)

    return {
        "max_win_streak":       max_win_streak,
        "max_loss_streak":      max_loss_streak,
        "p_win_after_win":      round(p_win_after_win, 3),
        "p_win_after_loss":     round(p_win_after_loss, 3),
        "autocorrelation":      round(pd.Series(wins).autocorr(1), 3),
        "avg_pnl_after_n_losses": consec_loss_pnl,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  6. PERSONAL $2,000 DRAWDOWN MC
#     Starting state: -$1,000 per account (already in drawdown)
#     Hard stop: -$2,000 (personal limit — $1,000 remaining budget)
#     Recovery target: $0 (break-even from starting equity)
# ═══════════════════════════════════════════════════════════════════════════════

def personal_stop_mc(daily: pd.Series, label: str,
                     start: float = CURRENT_DD_PER_ACCOUNT,
                     stop:  float = PERSONAL_STOP,
                     target: float = 0.0,
                     n_sims: int = MC_SIMS) -> dict:
    """
    Bootstrap daily P&L from `daily`.
    Each simulation starts at `start` equity (-$1,000).
    Stops when equity <= stop (-$2,000) or >= target ($0).
    """
    pnl = daily.values
    if len(pnl) < 10:
        return {}

    remaining = stop - start          # negative: how much more can we lose = -$1,000
    to_recover = target - start       # positive: how much we need to gain   = +$1,000

    hit_stop    = np.zeros(n_sims, dtype=bool)
    hit_target  = np.zeros(n_sims, dtype=bool)
    days_to_out = np.full(n_sims, len(pnl), dtype=float)
    final_equity = np.zeros(n_sims)

    for i in range(n_sims):
        eq = start
        sim = RNG.choice(pnl, size=len(pnl), replace=True)
        resolved = False
        for d_idx, p in enumerate(sim):
            eq += p
            if eq <= stop:
                hit_stop[i]    = True
                days_to_out[i] = d_idx + 1
                resolved = True
                break
            if eq >= target:
                hit_target[i]  = True
                days_to_out[i] = d_idx + 1
                resolved = True
                break
        final_equity[i] = eq

    p_stop   = hit_stop.mean()
    p_target = hit_target.mean()
    p_neither = 1 - p_stop - p_target

    survivors_equity = final_equity[~hit_stop & ~hit_target]

    return {
        "label":             label,
        "p_hit_stop":        round(float(p_stop),   4),   # P(lose another $1k before recovering)
        "p_hit_target":      round(float(p_target), 4),   # P(recover to break-even first)
        "p_still_running":   round(float(p_neither), 4),  # P(neither in sim window)
        "median_days_to_outcome": round(float(np.median(days_to_out)), 0),
        "p10_days":          round(float(np.percentile(days_to_out, 10)), 0),
        "p90_days":          round(float(np.percentile(days_to_out, 90)), 0),
        "median_final_equity_if_running": round(float(np.median(survivors_equity)), 0) if len(survivors_equity) > 0 else 0,
        # Distribution of outcomes over first 30 / 60 / 90 days
        **{f"p_positive_after_{d}d": round(float(
            (np.cumsum(RNG.choice(pnl, size=(1000, d), replace=True), axis=1)[:, -1] + start > 0).mean()
        ), 3) for d in [30, 60, 90]},
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  7. HOURS-FILTERED BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════

def hours_filter_test(bars: pd.DataFrame, strat: dict, params: dict,
                      symbol: str, tof_df: pd.DataFrame) -> dict:
    """
    Re-run backtest restricted to profitable UTC hours.
    Returns: full vs filtered comparison.
    """
    if tof_df.empty:
        return {}

    # Profitable hours: avg_pnl_per_trade > 0
    good_hours = set(tof_df[tof_df["avg_pnl_per_trade"] > 0]["hour_utc"].tolist())
    if not good_hours or len(good_hours) >= 23:
        return {"note": "all hours profitable or no filter possible"}

    sig = strat["compute"](bars, **params)

    trades_all  = run_backtest_full(bars, sig, symbol, allowed_hours=None)
    trades_filt = run_backtest_full(bars, sig, symbol, allowed_hours=good_hours)

    def summary(t):
        d = daily_pnl_series(t)
        return {
            "n_trades":  len(t),
            "total_pnl": round(d.sum(), 0) if len(d) else 0,
            "sharpe":    round(sharpe_from_daily(d), 2),
            "worst_day": round(d.min(), 0) if len(d) else 0,
            "win_rate":  round((t["dollar_pnl"] > 0).mean(), 3) if len(t) else 0,
        }

    return {
        "good_hours_utc": sorted(good_hours),
        "full":           summary(trades_all),
        "filtered":       summary(trades_filt),
        "trade_reduction_pct": round(100 * (1 - len(trades_filt) / max(len(trades_all), 1)), 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  8. STOP MULTIPLIER SENSITIVITY
# ═══════════════════════════════════════════════════════════════════════════════

def stop_sensitivity(bars: pd.DataFrame, strat: dict, params: dict,
                     symbol: str) -> dict:
    sig = strat["compute"](bars, **params)
    results = {}
    for sm in [0.75, 1.0, 1.5, 2.0, 2.5]:
        t = run_backtest_full(bars, sig, symbol, stop_mult=sm, tp_mult=sm * 2.0)
        d = daily_pnl_series(t)
        results[f"stop_{sm}x"] = {
            "sharpe":    round(sharpe_from_daily(d), 2),
            "total_pnl": round(d.sum(), 0) if len(d) else 0,
            "worst_day": round(d.min(), 0) if len(d) else 0,
            "win_rate":  round((t["dollar_pnl"] > 0).mean(), 3) if len(t) else 0,
            "n_trades":  len(t),
        }
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  PRINT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def hr(char="─", n=92): print(char * n)
def section(t): hr("═"); print(f"  {t}"); hr("═")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*92}")
    print(f"  L2 EXTENDED ANALYSIS — TIME-OF-DAY / MAE-MFE / PERSONAL-STOP MC / ACCOUNT PLAN")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Personal stop: ${abs(PERSONAL_STOP):,.0f}  |  Current DD per account: ${abs(CURRENT_DD_PER_ACCOUNT):,.0f}")
    print(f"  Remaining budget per account: ${abs(PERSONAL_STOP - CURRENT_DD_PER_ACCOUNT):,.0f}")
    print(f"{'='*92}\n")

    survivors = load_deduped_survivors(TOP_N)
    print(f"  Analysing top {len(survivors)} de-duplicated survivors\n")

    all_results = []
    all_trades_cache: dict[str, pd.DataFrame] = {}

    for rank, row in enumerate(survivors, 1):
        symbol    = row["symbol"]
        strat_nm  = row["strategy"]
        bar_min   = int(row["bar_minutes"])
        dsr       = row["dsr"]
        grade     = row["grade"]
        key       = f"{symbol}/{strat_nm}/{bar_min}m"

        bar_path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
        if not bar_path.exists():
            continue
        strat = STRATEGY_MAP.get(strat_nm)
        if strat is None:
            continue
        params = best_params(row, strat)
        if not params:
            continue

        try:
            bars = pd.read_parquet(bar_path)
            bars.index = pd.to_datetime(bars.index, utc=True)
        except Exception:
            continue

        print(f"  [{rank:02d}] {key}  DSR={dsr:.2f}  [{grade}]")

        # ── Reconstruct trades with full tracking ──────────────────────────
        try:
            sig    = strat["compute"](bars, **params)
            trades = run_backtest_full(bars, sig, symbol, extra_ticks=SLIP_TICKS)
        except Exception as e:
            print(f"       ERROR: {e}")
            continue

        if trades.empty:
            print(f"       No trades")
            continue

        all_trades_cache[key] = trades
        daily = daily_pnl_series(trades)

        # ── 1. MAE / MFE ───────────────────────────────────────────────────
        mf = mae_mfe_analysis(trades, symbol)

        # ── 2. Time-of-day ─────────────────────────────────────────────────
        tof = time_of_day_analysis(trades)

        # ── 3. Day-of-week ─────────────────────────────────────────────────
        dow = day_of_week_analysis(trades)

        # ── 4. Session ─────────────────────────────────────────────────────
        sess = session_analysis(trades)

        # ── 5. Streak analysis ─────────────────────────────────────────────
        streak = streak_analysis(trades)

        # ── 6. Personal stop MC ────────────────────────────────────────────
        mc = personal_stop_mc(daily, key)

        # ── 7. Hours-filtered backtest ─────────────────────────────────────
        hf = hours_filter_test(bars, strat, params, symbol, tof)

        # ── 8. Stop sensitivity ────────────────────────────────────────────
        ss = stop_sensitivity(bars, strat, params, symbol)

        # ── Print per-strategy summary ─────────────────────────────────────
        worst_day = round(daily.min(), 0) if len(daily) else 0
        ts_comply = round(100 * (daily >= -TOPSTEP_DAILY).mean(), 1) if len(daily) else 0
        ps_comply = round(100 * (daily >= (PERSONAL_STOP - CURRENT_DD_PER_ACCOUNT)).mean(), 1) if len(daily) else 0
        # ps_comply = % of days that don't wipe remaining $1,000 budget in one day
        # (personal stop distance = $1,000, so daily loss > $1,000 hits personal stop in one day)

        print(f"       MAE/MFE  winners: avg_mae=${mf.get('winner_avg_mae_usd',0):+,.0f}  avg_mfe=${mf.get('winner_avg_mfe_usd',0):+,.0f}")
        print(f"       MAE/MFE  losers:  avg_mae=${mf.get('loser_avg_mae_usd',0):+,.0f}   avg_mfe=${mf.get('loser_avg_mfe_usd',0):+,.0f}")
        print(f"       Exit:  stop={mf.get('pct_stop',0)}%  target={mf.get('pct_target',0)}%  timeout={mf.get('pct_timeout',0)}%  signal={mf.get('pct_signal',0)}%")
        print(f"       Topstep daily comply: {ts_comply}%  |  Personal-$1k/day comply: {ps_comply}%  |  Worst day: ${worst_day:,.0f}")

        if mc:
            print(f"       Personal-stop MC:  P(stop first)={mc['p_hit_stop']:.1%}  P(recover first)={mc['p_hit_target']:.1%}  median_days={mc['median_days_to_outcome']:.0f}")

        if not tof.empty:
            best_h = tof.loc[tof["avg_pnl_per_trade"].idxmax()]
            worst_h = tof.loc[tof["avg_pnl_per_trade"].idxmin()]
            print(f"       Best hour:  UTC {int(best_h['hour_utc']):02d}:00  avg=${best_h['avg_pnl_per_trade']:.0f}/trade  n={int(best_h['n_trades'])}")
            print(f"       Worst hour: UTC {int(worst_h['hour_utc']):02d}:00  avg=${worst_h['avg_pnl_per_trade']:.0f}/trade  n={int(worst_h['n_trades'])}")

        if hf and "filtered" in hf and "full" in hf:
            delta_sh = hf["filtered"]["sharpe"] - hf["full"]["sharpe"]
            delta_wd = hf["filtered"]["worst_day"] - hf["full"]["worst_day"]
            print(f"       Hours filter ({len(hf.get('good_hours_utc',[]))} good hrs): Sharpe {hf['full']['sharpe']:.2f}→{hf['filtered']['sharpe']:.2f} ({delta_sh:+.2f})  WorstDay ${hf['filtered']['worst_day']:,.0f} ({delta_wd:+.0f})")

        if streak:
            print(f"       Streak: max_loss={streak['max_loss_streak']}  autocorr={streak['autocorrelation']:.3f}  P(win|prev_win)={streak['p_win_after_win']:.1%}  P(win|prev_loss)={streak['p_win_after_loss']:.1%}")

        print()

        all_results.append({
            "key":      key,
            "symbol":   symbol,
            "strategy": strat_nm,
            "bar_min":  bar_min,
            "dsr":      round(dsr, 3),
            "grade":    grade,
            "params":   params,
            "mae_mfe":  mf,
            "tof":      tof.to_dict(orient="records") if not tof.empty else [],
            "dow":      dow.to_dict(orient="records") if not dow.empty else [],
            "sessions": sess,
            "streak":   streak,
            "personal_stop_mc": mc,
            "hours_filter": hf,
            "stop_sensitivity": ss,
            "daily_stats": {
                "worst_day":      worst_day,
                "ts_comply_pct":  ts_comply,
                "ps_comply_pct":  ps_comply,
                "sharpe":         round(sharpe_from_daily(daily), 2),
                "total_pnl":      round(daily.sum(), 0),
                "n_days":         len(daily),
            },
        })

    # ═══════════════════════════════════════════════════════════════════════════
    #  FULL TABLES
    # ═══════════════════════════════════════════════════════════════════════════

    section("TIME-OF-DAY BREAKDOWN (best / worst hour per strategy)")
    for r in all_results:
        tof_rows = r["tof"]
        if not tof_rows:
            continue
        tof_df = pd.DataFrame(tof_rows).sort_values("avg_pnl_per_trade", ascending=False)
        print(f"\n  {r['key']}:")
        hr()
        print(f"  {'Hour(UTC)':>10} {'Trades':>8} {'TotalPnL':>12} {'AvgPnL/Tr':>12} {'WinRate':>10} {'Sharpe':>8}")
        hr()
        for _, ro in tof_df.iterrows():
            flag = " ★" if ro["avg_pnl_per_trade"] > 0 else " ✗"
            print(f"  {int(ro['hour_utc']):>10}:00 {int(ro['n_trades']):>8} {ro['total_pnl']:>12,.0f} {ro['avg_pnl_per_trade']:>12,.0f} {ro['win_rate']:>10.1%} {ro['sharpe']:>8.2f}{flag}")

    section("DAY-OF-WEEK ANALYSIS")
    print(f"  {'Strategy':<44} {'Mon':>10} {'Tue':>10} {'Wed':>10} {'Thu':>10} {'Fri':>10}")
    hr()
    for r in all_results:
        dow_rows = r["dow"]
        if not dow_rows:
            continue
        dow_df = pd.DataFrame(dow_rows).set_index("day")
        avgs = {d: dow_df.loc[d, "avg_pnl_per_trade"] if d in dow_df.index else 0 for d in ["Mon","Tue","Wed","Thu","Fri"]}
        vals = "".join(f"{avgs[d]:>+10,.0f}" for d in ["Mon","Tue","Wed","Thu","Fri"])
        print(f"  {r['key']:<44}{vals}")

    section("SESSION BREAKDOWN (Asian / London / US)")
    for r in all_results:
        s = r["sessions"]
        if not s:
            continue
        print(f"\n  {r['key']}:")
        for sess_nm, sv in s.items():
            print(f"    {sess_nm:<10}  n={sv['n_trades']:>5}  avg_pnl=${sv['avg_pnl_per_trade']:>+7,.0f}  wr={sv['win_rate']:.1%}  total=${sv['total_pnl']:>+12,.0f}")

    section("MAE / MFE TRADE QUALITY")
    print(f"  {'Strategy':<44} {'WR%':>7} {'WinMAE':>9} {'WinMFE':>9} {'LosMAE':>9} {'LosMFE':>9} {'Stop%':>7} {'Tgt%':>7} {'TO%':>7}")
    hr()
    for r in all_results:
        mf = r["mae_mfe"]
        if not mf:
            continue
        print(f"  {r['key']:<44} {mf['win_rate']:>7.1%} "
              f"${mf['winner_avg_mae_usd']:>8,.0f} ${mf['winner_avg_mfe_usd']:>8,.0f} "
              f"${mf['loser_avg_mae_usd']:>8,.0f} ${mf['loser_avg_mfe_usd']:>8,.0f} "
              f"{mf['pct_stop']:>7.1f}% {mf['pct_target']:>7.1f}% {mf['pct_timeout']:>7.1f}%")

    section("STOP MULTIPLIER SENSITIVITY (Sharpe / WorstDay)")
    print(f"  {'Strategy':<44} {'0.75x':>12} {'1.0x':>12} {'1.5x':>12} {'2.0x':>12} {'2.5x':>12}")
    hr()
    for r in all_results:
        ss = r["stop_sensitivity"]
        if not ss:
            continue
        vals = ""
        for k in ["stop_0.75x", "stop_1.0x", "stop_1.5x", "stop_2.0x", "stop_2.5x"]:
            v = ss.get(k, {})
            vals += f"  {v.get('sharpe', 0):>5.2f}/{v.get('worst_day', 0)/1000:>+5.1f}k"
        print(f"  {r['key']:<44}{vals}")

    section("STREAK / AUTOCORRELATION ANALYSIS")
    print(f"  {'Strategy':<44} {'MaxLoss':>9} {'AutoCorr':>10} {'P(win|W)':>10} {'P(win|L)':>10}")
    hr()
    for r in all_results:
        st = r["streak"]
        if not st:
            continue
        print(f"  {r['key']:<44} {st['max_loss_streak']:>9} "
              f"{st['autocorrelation']:>10.3f} "
              f"{st['p_win_after_win']:>10.1%} "
              f"{st['p_win_after_loss']:>10.1%}")

    section("PERSONAL $2,000 STOP MC — STARTING FROM -$1,000 (remaining budget: $1,000)")
    print(f"  {'Strategy':<44} {'P(stop)':>9} {'P(recvr)':>9} {'MedDays':>9} {'P30d+':>9} {'P60d+':>9} {'P90d+':>9}")
    hr()
    for r in all_results:
        mc = r["personal_stop_mc"]
        if not mc:
            continue
        flag = " ← SAFE" if mc["p_hit_stop"] < 0.20 else (" ← WARNING" if mc["p_hit_stop"] < 0.40 else " ← DANGER")
        print(f"  {r['key']:<44} {mc['p_hit_stop']:>9.1%} "
              f"{mc['p_hit_target']:>9.1%} "
              f"{mc['median_days_to_outcome']:>9.0f} "
              f"{mc.get('p_positive_after_30d', 0):>9.1%} "
              f"{mc.get('p_positive_after_60d', 0):>9.1%} "
              f"{mc.get('p_positive_after_90d', 0):>9.1%}"
              f"{flag}")

    section("HOURS FILTER — FULL vs PROFITABLE-HOURS-ONLY")
    print(f"  {'Strategy':<44} {'FullSh':>8} {'FiltSh':>8} {'Delta':>8} {'FiltWD':>10} {'TrRed%':>8}")
    hr()
    for r in all_results:
        hf = r["hours_filter"]
        if not hf or "full" not in hf:
            continue
        print(f"  {r['key']:<44} {hf['full']['sharpe']:>8.2f} "
              f"{hf['filtered']['sharpe']:>8.2f} "
              f"{hf['filtered']['sharpe'] - hf['full']['sharpe']:>+8.2f} "
              f"${hf['filtered']['worst_day']:>9,.0f} "
              f"{hf['trade_reduction_pct']:>8.1f}%")

    # ═══════════════════════════════════════════════════════════════════════════
    #  ACCOUNT ASSIGNMENT PLAN (10 funded accounts)
    # ═══════════════════════════════════════════════════════════════════════════

    section(f"ACCOUNT ASSIGNMENT PLAN — {N_ACCOUNTS} FUNDED ACCOUNTS")
    print(f"  Current state: ~$1,000 DD per account  |  Personal stop: $2,000")
    print(f"  Remaining budget per account: ~$1,000\n")
    print(f"  TOPSTEP DAILY LIMIT: ${TOPSTEP_DAILY:,.0f}")
    print(f"  TOPSTEP TRAILING DD: $7,500\n")
    hr()

    # Build a ranked list suitable for account assignment
    # Exclude strategies incompatible with Topstep (worst_day > TOPSTEP_DAILY * 0.95)
    # Prioritise: low P(stop), high P(recover), good Topstep compliance
    eligible = []
    for r in all_results:
        ds = r["daily_stats"]
        mc = r.get("personal_stop_mc", {})
        worst_day = ds["worst_day"]

        topstep_ok = ds["ts_comply_pct"] >= 95
        personal_ok = mc.get("p_hit_stop", 1.0) < 0.50

        eligible.append({
            "key":          r["key"],
            "symbol":       r["symbol"],
            "strategy":     r["strategy"],
            "bar_min":      r["bar_min"],
            "dsr":          r["dsr"],
            "sharpe":       ds["sharpe"],
            "worst_day":    worst_day,
            "ts_comply":    ds["ts_comply_pct"],
            "ps_comply":    ds["ps_comply_pct"],
            "p_stop":       mc.get("p_hit_stop", 1.0),
            "p_recover":    mc.get("p_hit_target", 0.0),
            "topstep_ok":   topstep_ok,
            "personal_ok":  personal_ok,
            "params":       r["params"],
        })

    eligible.sort(key=lambda x: (x["p_stop"], -x["p_recover"]))

    print(f"  {'#':<4} {'Strategy':<44} {'Worst Day':>12} {'TS-OK':>7} {'P(stop)':>9} {'P(recvr)':>9} {'Assign?':>10}")
    hr()

    account_plan = []
    for i, e in enumerate(eligible, 1):
        ts_flag  = "YES" if e["topstep_ok"] else "NO"
        ps_flag  = "YES" if e["personal_ok"] else "RISKY"
        print(f"  {i:<4} {e['key']:<44} ${e['worst_day']:>11,.0f} "
              f"{ts_flag:>7} {e['p_stop']:>9.1%} {e['p_recover']:>9.1%}  {ps_flag:>10}")
        if e["topstep_ok"] and e["personal_ok"]:
            account_plan.append(e)

    # Assign strategies to 10 accounts (rotate through eligible strategies)
    print(f"\n  RECOMMENDED ASSIGNMENT ({N_ACCOUNTS} accounts):")
    hr()
    if not account_plan:
        print("  WARNING: No strategy passes both Topstep and personal-stop criteria.")
        print("  Recommend pausing all accounts until market conditions improve.")
    else:
        print(f"  {'Acct':>5}  {'Strategy':<44}  {'Params'}")
        hr()
        for acct in range(1, N_ACCOUNTS + 1):
            strat_entry = account_plan[(acct - 1) % len(account_plan)]
            params_str = ", ".join(f"{k}={v}" for k, v in strat_entry["params"].items())
            print(f"  #{acct:>4}  {strat_entry['key']:<44}  {params_str}")

    # ── Risk summary ───────────────────────────────────────────────────────────
    section("FINAL GO / NO-GO TABLE (per strategy, given current -$1,000 state)")
    print(f"  {'Strategy':<44} {'Sharpe':>8} {'WorstDay':>10} {'TS%':>7} {'P(stop)':>9} {'P(rec)':>9} {'VERDICT':>12}")
    hr()
    for e in eligible:
        if e["topstep_ok"] and e["personal_ok"]:
            verdict = "GO"
        elif e["topstep_ok"] and not e["personal_ok"]:
            verdict = "CAUTION"
        elif not e["topstep_ok"]:
            verdict = "NO (TS limit)"
        else:
            verdict = "NO"
        print(f"  {e['key']:<44} {e['sharpe']:>8.2f} ${e['worst_day']:>9,.0f} "
              f"{e['ts_comply']:>7.1f}% {e['p_stop']:>9.1%} {e['p_recover']:>9.1%}  {verdict:>12}")

    # ═══════════════════════════════════════════════════════════════════════════
    #  SAVE
    # ═══════════════════════════════════════════════════════════════════════════

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = RESULT_DIR / f"tick_extended_analysis_{ts}.json"

    save_data = {
        "timestamp": ts,
        "personal_stop": PERSONAL_STOP,
        "current_dd_per_account": CURRENT_DD_PER_ACCOUNT,
        "n_accounts": N_ACCOUNTS,
        "strategies": all_results,
        "account_plan": account_plan,
    }

    with open(out_path, "w") as fh:
        json.dump(save_data, fh, indent=2, default=str)

    print(f"\n  Full results saved: {out_path}")
    hr("═")


if __name__ == "__main__":
    main()
