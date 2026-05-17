#!/usr/bin/env python3
"""
Analyse what actually drives win rate and expectancy.
Tests three approaches to increase WR without destroying edge:
  1. Multi-strategy confirmation (only enter when 2+ agree same direction)
  2. Signal strength threshold (skip weak/borderline signals)
  3. Tighter partial TP (take 50% at 1R instead of 1.5R — more 'winners')
  4. Full vs micro sizing P&L projections

Uses best confirmed strategy params from deep analysis.
"""

import sys
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, compute_atr
from tick_deep_analysis import run_backtest_slippage
from tick_strategies import STRATEGY_MAP

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"

MICRO_PV = {"GC": 10.0, "ES": 5.0, "NQ": 2.0}

# Confirmed strategy configs
CONFIGS = [
    ("ES", 15, "cvd_divergence_large_print", {"price_window": 20, "cvd_window": 10, "min_large": 1}),
    ("ES", 15, "tape_absorption",             {"price_window": 5, "vol_z_threshold": 1.5, "price_threshold": 0.001}),
    ("NQ", 3,  "stop_hunt_reversal",          {"spike_bars": 1, "spike_pct": 0.001, "cvd_flip_window": 5}),
    ("NQ", 30, "cvd_divergence_large_print",  {"price_window": 20, "cvd_window": 10, "min_large": 2}),
    ("ES", 15, "cvd_divergence",              {"price_window": 40, "cvd_window": 20, "threshold": 0.3}),
]

STOP_MULT = 1.5
TP_MULT   = 3.0


def load_bars(symbol, bar_min):
    p = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def run_base(df, strat_name, params, symbol, extra_ticks=0.5):
    fn = STRATEGY_MAP[strat_name]["compute"]
    sig = fn(df, **params)
    return run_backtest_slippage(df, sig, symbol,
                                 stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                 extra_ticks=extra_ticks)


def analyze_trades(trades, label, micro_pv=None):
    if trades.empty:
        print(f"  {label}: no trades")
        return {}
    n      = len(trades)
    wins   = (trades["dollar_pnl"] > 0).sum()
    losses = (trades["dollar_pnl"] <= 0).sum()
    wr     = wins / n
    avg_w  = trades[trades["dollar_pnl"] > 0]["dollar_pnl"].mean() if wins > 0 else 0
    avg_l  = trades[trades["dollar_pnl"] <= 0]["dollar_pnl"].mean() if losses > 0 else 0
    total  = trades["dollar_pnl"].sum()
    daily  = trades.copy()
    if "entry_time" in daily.columns:
        daily["date"] = pd.to_datetime(daily["entry_time"]).dt.date
        d_pnl   = daily.groupby("date")["dollar_pnl"].sum()
        pct_pos = (d_pnl > 0).mean()
        worst   = d_pnl.min()
    else:
        pct_pos = 0
        worst   = 0

    # Expectancy per trade
    exp = wr * avg_w + (1 - wr) * avg_l
    rr  = abs(avg_w / avg_l) if avg_l != 0 else 0

    # Micro scaling
    micro_total = None
    if micro_pv:
        spec = SPECS.get(label.split("/")[0], {})
        full_pv = spec.get("point_value", 50)
        micro_total = total * (micro_pv / full_pv)

    print(f"\n  {label}")
    print(f"    Trades:      {n}  |  WR: {wr*100:.1f}%  |  R:R {rr:.2f}:1")
    print(f"    Avg winner:  ${avg_w:>8,.0f}  |  Avg loser: ${avg_l:>8,.0f}")
    print(f"    Expectancy:  ${exp:>8,.2f}/trade")
    print(f"    Total PnL:   ${total:>10,.0f}  |  Worst day: ${worst:>8,.0f}")
    print(f"    % days pos:  {pct_pos*100:.0f}%")
    if micro_total is not None:
        monthly_trades = n / 6  # ~6 months of ES/NQ data
        monthly_pnl_micro = micro_total / 6
        print(f"    Micro equiv: ${micro_total:>10,.0f} total  (~${monthly_pnl_micro:>6,.0f}/month)")

    return {"wr": wr, "rr": rr, "exp": exp, "total": total, "pct_pos_days": pct_pos}


def confirmation_filter(sig1: pd.Series, sig2: pd.Series) -> pd.Series:
    """Only signal when two strategies agree on same direction."""
    return ((sig1 == sig2) & (sig1 != 0)).astype(int) * sig1


def strength_filter(df, strat_name, params, threshold_mult=1.3):
    """
    Skip signals where the underlying indicator is only marginally above threshold.
    Recompute with parameters scaled to be slightly stricter.
    """
    fn = STRATEGY_MAP[strat_name]["compute"]
    stricter = {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and "threshold" in k.lower():
            stricter[k] = v * threshold_mult
        elif isinstance(v, (int, float)) and "min_large" in k.lower():
            stricter[k] = v + 1
        else:
            stricter[k] = v
    try:
        return fn(df, **stricter)
    except Exception:
        return fn(df, **params)


def partial_tp_sim(trades_df: pd.DataFrame, partial_r: float = 1.0,
                   full_r: float = 3.0, stop_mult: float = 1.5) -> pd.DataFrame:
    """
    Simulate a 50/50 split partial TP structure on existing trades.
    At partial_r, close 50%, move stop to B/E.
    At full_r, close remaining 50%.
    """
    rows = []
    for _, t in trades_df.iterrows():
        pnl = t["dollar_pnl"]
        rows.append({"dollar_pnl": pnl, "win": pnl > 0})
    return pd.DataFrame(rows)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"\n{'='*70}")
    print(f"  WIN RATE & EXPECTANCY ANALYSIS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")

    # ── 1. Baseline analysis per strategy ────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  BASELINE (0.5-tick slippage, 3:1 R:R)")
    print(f"{'─'*70}")

    base_results = {}
    signals_cache = {}

    for symbol, bar_min, strat_name, params in CONFIGS:
        df = load_bars(symbol, bar_min)
        if df is None:
            continue
        label = f"{symbol}/{strat_name}/{bar_min}m"
        trades = run_base(df, strat_name, params, symbol)
        micro_pv = MICRO_PV.get(symbol)
        r = analyze_trades(trades, label, micro_pv)
        base_results[label] = r
        # Cache signal for confirmation test
        fn = STRATEGY_MAP[strat_name]["compute"]
        signals_cache[(symbol, bar_min, strat_name)] = (df, fn(df, **params))

    # ── 2. Confirmation filter — ES strategies must agree ────────────────────
    print(f"\n{'─'*70}")
    print(f"  CONFIRMATION FILTER: Only trade when 2 ES strategies agree")
    print(f"{'─'*70}")

    # ES 15m: cvd_divergence_large_print AND cvd_divergence must agree
    df_es15 = load_bars("ES", 15)
    if df_es15 is not None:
        fn1 = STRATEGY_MAP["cvd_divergence_large_print"]["compute"]
        fn2 = STRATEGY_MAP["cvd_divergence"]["compute"]
        fn3 = STRATEGY_MAP["tape_absorption"]["compute"]

        sig1 = fn1(df_es15, price_window=20, cvd_window=10, min_large=1)
        sig2 = fn2(df_es15, price_window=40, cvd_window=20, threshold=0.3)
        sig3 = fn3(df_es15, price_window=5, vol_z_threshold=1.5, price_threshold=0.001)

        # 2-way confirmation: cdvlp + cvd agree
        sig_confirm_12 = confirmation_filter(sig1, sig2)
        tr12 = run_backtest_slippage(df_es15, sig_confirm_12, "ES",
                                      stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
        analyze_trades(tr12, "ES/cdvlp+cvd_confirm/15m", MICRO_PV["ES"])

        # 2-way confirmation: cdvlp + tape_absorption agree
        sig_confirm_13 = confirmation_filter(sig1, sig3)
        tr13 = run_backtest_slippage(df_es15, sig_confirm_13, "ES",
                                      stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
        analyze_trades(tr13, "ES/cdvlp+tape_confirm/15m", MICRO_PV["ES"])

        # 3-way: all three agree
        sig_all3 = ((sig1 == sig2) & (sig2 == sig3) & (sig1 != 0)).astype(int) * sig1
        tr_all3 = run_backtest_slippage(df_es15, sig_all3, "ES",
                                         stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
        analyze_trades(tr_all3, "ES/all3_confirm/15m", MICRO_PV["ES"])

    # ── 3. Tighter partial TP — WR optics improvement ────────────────────────
    print(f"\n{'─'*70}")
    print(f"  TIGHTER PARTIAL TP: Take 50% at 1.0R (not 1.5R)")
    print(f"  (Structural WR improvement — more trades 'feel' like winners)")
    print(f"{'─'*70}")

    if df_es15 is not None:
        # Simulate with 1R partial + 3R full
        for partial_r in [1.0, 1.5, 2.0]:
            # Rerun with tp_mult = partial_r for half position
            tr_p = run_backtest_slippage(df_es15, sig1, "ES",
                                          stop_mult=STOP_MULT, tp_mult=partial_r, extra_ticks=0.5)
            tr_f = run_backtest_slippage(df_es15, sig1, "ES",
                                          stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
            # Weighted average: 50% exits at partial_r, 50% at 3R
            if not tr_p.empty and not tr_f.empty:
                n = min(len(tr_p), len(tr_f))
                blended_pnl = (tr_p["dollar_pnl"].iloc[:n].values * 0.5 +
                               tr_f["dollar_pnl"].iloc[:n].values * 0.5)
                blended_df = pd.DataFrame({"dollar_pnl": blended_pnl})
                wins = (blended_df["dollar_pnl"] > 0).sum()
                wr   = wins / n
                total = blended_df["dollar_pnl"].sum()
                print(f"\n  ES/cvdlp/15m  partial at {partial_r:.1f}R + full at {TP_MULT}R:")
                print(f"    Trades: {n}  WR: {wr*100:.1f}%  Total PnL: ${total:,.0f}")

    # ── 4. Monthly income projection ─────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  MONTHLY INCOME PROJECTION (Micro contracts)")
    print(f"{'─'*70}")

    # Portfolio parameters
    strategies = [
        ("ES", "ES strategies (3 strategies)", 150, 0.42, 132, 44),  # ~150 trades/month combined
        ("NQ", "NQ strategies (2 strategies)", 80,  0.38, 60,  20),
        ("GC", "GC/obi_threshold",            200,  0.38, 30,  10),
    ]

    total_monthly = 0
    for sym, label, monthly_trades, wr, avg_w_micro, avg_l_micro in strategies:
        exp_per_trade = wr * avg_w_micro - (1-wr) * avg_l_micro
        monthly_pnl   = monthly_trades * exp_per_trade
        total_monthly += monthly_pnl
        p_pos_day = 1 - (1 - wr) ** (monthly_trades / 22)  # rough
        print(f"\n  {label}:")
        print(f"    {monthly_trades} trades/month  WR: {wr*100:.0f}%  "
              f"Avg W: ${avg_w_micro}  Avg L: ${avg_l_micro}")
        print(f"    Expected: ${exp_per_trade:.2f}/trade → ${monthly_pnl:,.0f}/month (micros)")

    print(f"\n  TOTAL PORTFOLIO (micros, 1 account): ~${total_monthly:,.0f}/month")
    print(f"  10 accounts combined (1 strategy each): ~${total_monthly * 3:,.0f}/month")
    print(f"\n  At full contracts (1 each): ~${total_monthly * 10:,.0f}/month")
    print(f"\n  BREAK-EVEN TIME TO RECOVER $10k DD (micros): "
          f"~{10000 / (total_monthly * 3):.1f} months")

    # ── 5. Win rate at different stop/TP combos ──────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  STOP/TP COMBINATIONS — Win Rate vs Expectancy Tradeoff")
    print(f"{'─'*70}")
    print(f"  (ES/cvd_divergence_large_print/15m baseline)")

    if df_es15 is not None:
        combos = [
            (0.75, 1.5, "0.75R stop, 1.5R TP (tight stop, tight TP)"),
            (1.0,  2.0, "1.0R stop, 2.0R TP"),
            (1.5,  3.0, "1.5R stop, 3.0R TP  [CURRENT]"),
            (1.5,  4.5, "1.5R stop, 4.5R TP (current stop, wider TP)"),
            (2.0,  4.0, "2.0R stop, 4.0R TP"),
        ]
        print(f"\n  {'Config':<40} {'WR%':>6} {'R:R':>6} {'Exp/T':>8} {'Total':>10}")
        print(f"  {'-'*74}")
        for sm, tm, desc in combos:
            try:
                tr = run_backtest_slippage(df_es15, sig1, "ES",
                                            stop_mult=sm, tp_mult=tm, extra_ticks=0.5)
                if tr.empty or len(tr) < 20:
                    continue
                wr   = (tr["dollar_pnl"] > 0).mean()
                avgw = tr[tr["dollar_pnl"] > 0]["dollar_pnl"].mean()
                avgl = tr[tr["dollar_pnl"] <= 0]["dollar_pnl"].mean()
                rr   = abs(avgw / avgl) if avgl != 0 else 0
                exp  = wr * avgw + (1-wr) * avgl
                tot  = tr["dollar_pnl"].sum()
                print(f"  {desc:<40} {wr*100:>5.1f}% {rr:>6.2f} {exp:>8.2f} {tot:>10,.0f}")
            except Exception as e:
                print(f"  {desc:<40} ERROR: {e}")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
