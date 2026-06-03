"""
tick_l2_backtest.py — L2 Strategy Backtest Battery
====================================================
Builds L2 feature bars from GC/SI mbp-10 data, then tests all
L2-based strategies against those bars.

Outputs:
  05_backtests/l2_results/l2_strategy_results.csv
  05_backtests/l2_results/l2_survivors.json

Run:
  venv_new/Scripts/python.exe 04_codebase/tick_l2_backtest.py
  venv_new/Scripts/python.exe 04_codebase/tick_l2_backtest.py --symbol GC --rebuild
  venv_new/Scripts/python.exe 04_codebase/tick_l2_backtest.py --quick  (1-year sample only)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import product
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from src.l2 import build_l2_bars

# ── Output dirs ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
OUT_DIR   = ROOT / "05_backtests" / "l2_results"
BAR_DIR   = ROOT / "01_data" / "tick_bars"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Inline cost model ────────────────────────────────────────────────────────
def _apply_costs(
    trades_df: pd.DataFrame,
    tick_size: float,
    tick_value: float,
    slippage_ticks: int = 1,
    commission_per_side: float = 2.25,
) -> pd.DataFrame:
    """Apply slippage + commission, add net_pnl column (in price points)."""
    out = trades_df.copy()
    slip_pts = slippage_ticks * 2 * tick_size          # round-trip slippage in points
    dollars_per_point = tick_value / tick_size
    commission_pts = (commission_per_side * 2) / dollars_per_point  # RT commission in points
    total_cost_pts = slip_pts + commission_pts
    out["cost_pts"] = total_cost_pts
    out["net_pnl"] = out.get("gross_pnl", 0) - total_cost_pts
    return out


# ── Contract specs ────────────────────────────────────────────────────────────
CONTRACT_SPECS = {
    "GC": {"tick_size": 0.10, "tick_value": 10.0,  "name": "Gold"},
    "SI": {"tick_size": 0.005, "tick_value": 25.0, "name": "Silver"},
    "ES": {"tick_size": 0.25,  "tick_value": 12.5, "name": "E-mini S&P"},
    "NQ": {"tick_size": 0.25,  "tick_value": 5.0,  "name": "E-mini NQ"},
}

# ── Strategy registry ─────────────────────────────────────────────────────────
def _load_strategies():
    from src.strategies.l2_ofi_strategies import (
        OFIContinuationStrategy, OFIReversalStrategy, OFIMicropriceStrategy
    )
    from src.strategies.l2_sweep_strategies import (
        SweepContinuationStrategy, SweepAbsorptionReversalStrategy,
        SessionHighLowSweepReversalStrategy
    )
    from src.strategies.l2_absorption_strategies import (
        AbsorptionReversalStrategy, CVDAbsorptionStrategy,
        RepeatedReplenishmentStrategy
    )
    from src.strategies.l2_cvd_strategies import (
        CVDMicropriceStrategy, CVDSlopeRegimeStrategy,
        CVDAccelerationStrategy, CVDVWAPStrategy
    )
    from src.strategies.l2_depth_strategies import (
        DepthImbalanceMomentumStrategy, DepthImbalanceMeanRevStrategy,
        MultiTimeframeOFIStrategy
    )

    return [
        OFIContinuationStrategy,
        OFIReversalStrategy,
        OFIMicropriceStrategy,
        SweepContinuationStrategy,
        SweepAbsorptionReversalStrategy,
        SessionHighLowSweepReversalStrategy,
        AbsorptionReversalStrategy,
        CVDAbsorptionStrategy,
        RepeatedReplenishmentStrategy,
        CVDMicropriceStrategy,
        CVDSlopeRegimeStrategy,
        CVDAccelerationStrategy,
        CVDVWAPStrategy,
        DepthImbalanceMomentumStrategy,
        DepthImbalanceMeanRevStrategy,
        MultiTimeframeOFIStrategy,
    ]


# ── Core metrics ─────────────────────────────────────────────────────────────
def _compute_metrics(trades_df: pd.DataFrame, spec: dict) -> dict:
    """Compute DSR, Sharpe, win-rate, etc."""
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return {}

    tick_val  = spec["tick_value"]
    tick_sz   = spec["tick_size"]

    # Dollar P&L
    dollar_pnl = trades_df["net_pnl"] * (tick_val / tick_sz)

    n          = len(trades_df)
    wins       = (dollar_pnl > 0).sum()
    losses     = (dollar_pnl < 0).sum()
    win_rate   = wins / n if n > 0 else 0

    total_pnl  = dollar_pnl.sum()
    avg_win    = dollar_pnl[dollar_pnl > 0].mean() if wins > 0 else 0
    avg_loss   = dollar_pnl[dollar_pnl < 0].mean() if losses > 0 else 0
    profit_fac = abs(avg_win * wins / (avg_loss * losses + 1e-9)) if losses > 0 else np.nan

    # Daily P&L for Sharpe
    if "exit_time" in trades_df.columns:
        daily = dollar_pnl.groupby(trades_df["exit_time"].dt.date).sum()
    else:
        daily = pd.Series(dollar_pnl.values)

    sharpe = (daily.mean() / (daily.std() + 1e-9)) * np.sqrt(252) if len(daily) > 1 else 0

    # Max drawdown
    cumulative = dollar_pnl.cumsum()
    roll_max   = cumulative.cummax()
    drawdown   = roll_max - cumulative
    max_dd     = drawdown.max()

    # Simplified Deflated Sharpe Ratio
    T    = len(daily)
    skew = daily.skew()     if len(daily) > 2 else 0.0
    kurt = daily.kurtosis() if len(daily) > 3 else 0.0
    # Simplified DSR for ranking (signed value; not a probability).
    # Uses Bailey 2014 denominator to penalise non-normality, but drops the
    # multiple-testing benchmark sr0 for speed.  Threshold: dsr > 0.3.
    # Denominator: (kurt+2)/4 = (Pearson_kurt-1)/4 per Bailey 2014.
    denom_sq = 1.0 - skew * sharpe + (kurt + 2) / 4 * sharpe ** 2
    denom = np.sqrt(max(denom_sq, 1e-9))
    sharpe_adj = sharpe / denom
    dsr = sharpe_adj - (0.2 / np.sqrt(max(T, 1)))

    return {
        "n_trades":   n,
        "win_rate":   round(win_rate, 4),
        "total_pnl":  round(total_pnl, 2),
        "avg_win":    round(avg_win, 2),
        "avg_loss":   round(avg_loss, 2),
        "profit_factor": round(profit_fac, 3) if not np.isnan(profit_fac) else 0,
        "sharpe":     round(sharpe, 4),
        "dsr":        round(dsr, 4),
        "max_dd":     round(max_dd, 2),
    }


# ── Main backtest loop ────────────────────────────────────────────────────────
def _run_strategy_battery(
    bars: pd.DataFrame,
    symbol: str,
    strategy_classes: list,
    min_trades: int = 20,
) -> pd.DataFrame:
    spec = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])
    rows = []

    for StratClass in strategy_classes:
        grid = StratClass.param_grid
        combos = list(product(*grid.values()))

        for combo in combos:
            params = dict(zip(grid.keys(), combo))
            strat  = StratClass(params=params)

            try:
                signals = strat.generate_signals(bars)
                n_sig = (signals != 0).sum()
                if n_sig < 5:
                    continue

                raw_trades = strat.signals_to_trades(bars, signals)
                if len(raw_trades) < min_trades:
                    continue

                trades_df = pd.DataFrame(raw_trades)

                # Apply costs
                trades_df = _apply_costs(
                    trades_df,
                    tick_size=spec["tick_size"],
                    tick_value=spec["tick_value"],
                    slippage_ticks=1,
                    commission_per_side=2.25,
                )

                metrics = _compute_metrics(trades_df, spec)
                if not metrics:
                    continue

                row = {
                    "strategy":   strat.name,
                    "symbol":     symbol,
                    "params":     json.dumps(params),
                    **metrics,
                }
                rows.append(row)
                print(f"  {strat.name:<35} {symbol}  trades={metrics['n_trades']:>4}  "
                      f"dsr={metrics['dsr']:+.3f}  pnl=${metrics['total_pnl']:>8,.0f}")

            except Exception as e:
                pass  # Skip failed combos silently

    return pd.DataFrame(rows) if rows else pd.DataFrame()


_L2_EXTRA_COLS = [
    "ofi_1", "ofi_5", "imbal_L5_last", "imbal_L5_mean", "imbal_L5_std",
    "microprice_last", "microprice_mean", "midprice_last",
    "spread_max", "buy_sweeps", "sell_sweeps", "net_sweeps", "sweep_net_size",
    "price_range_tick", "absorption_buy", "absorption_sell", "absorption_score",
]


def _load_or_build_l2_bars(symbol: str, rebuild: bool, quick: bool) -> pd.DataFrame:
    # Primary bars (full OHLCV + existing L2 like spread_mean, book_pressure)
    primary_path = BAR_DIR / f"{symbol}_bars_1m.parquet"
    l2_path      = BAR_DIR / f"{symbol}_bars_l2_1m.parquet"

    # If L2 parquet doesn't exist, build it
    if not l2_path.exists() or rebuild:
        print(f"[{symbol}] Building L2 bars from tick data ...")
        start = "2024-01-01" if quick else None
        build_l2_bars(symbol, chunksize=500_000, start=start, save=True)

    # Load primary bars (OHLCV with buy/sell vol from tick data)
    if primary_path.exists():
        bars = pd.read_parquet(primary_path)
        print(f"[{symbol}] Primary bars: {len(bars):,} rows")
    elif l2_path.exists():
        # Fallback: use L2 bars, filter to valid OHLCV rows
        raw = pd.read_parquet(l2_path)
        bars = raw.dropna(subset=["close"])
        print(f"[{symbol}] L2-only bars (no primary): {len(bars):,} rows")
    else:
        print(f"[{symbol}] No bar data found — skipping")
        return pd.DataFrame()

    # Merge in new L2 features
    if l2_path.exists():
        l2 = pd.read_parquet(l2_path)
        available = [c for c in _L2_EXTRA_COLS if c in l2.columns]
        for col in available:
            if col not in bars.columns:
                bars[col] = l2[col].reindex(bars.index)

    if bars.empty:
        print(f"[{symbol}] No bars — skipping")
        return pd.DataFrame()

    # Apply quick filter
    if quick:
        bars = bars[bars.index >= pd.Timestamp("2024-01-01", tz="UTC")]
        print(f"[{symbol}] Quick mode: {len(bars):,} bars from 2024")

    # Add session_vwap
    if "session_vwap" not in bars.columns:
        bars["session_vwap"] = _calc_session_vwap(bars)

    print(f"[{symbol}] {len(bars):,} bars ready. L2 cols present: "
          f"{sum(1 for c in _L2_EXTRA_COLS if c in bars.columns)}/{len(_L2_EXTRA_COLS)}")
    return bars


def _calc_session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Compute session VWAP (resets at 17:00 UTC = CME day session reset)."""
    close = bars["close"]
    vol   = bars.get("volume", pd.Series(1, index=bars.index))
    session_date = (bars.index - pd.Timedelta(hours=17)).date

    vwap = pd.Series(np.nan, index=bars.index)
    for date, grp in bars.groupby(pd.Series(session_date, index=bars.index)):
        grp_vol   = vol.loc[grp.index]
        grp_close = close.loc[grp.index]
        cumvol    = grp_vol.cumsum()
        cumtpvol  = (grp_close * grp_vol).cumsum()
        vwap.loc[grp.index] = cumtpvol / cumvol.replace(0, np.nan)

    return vwap


def _stress_test(results_df: pd.DataFrame, bars: pd.DataFrame,
                 symbol: str, strategy_classes: list) -> pd.DataFrame:
    """Quick stress test survivors: 1-tick extra slippage."""
    if results_df.empty:
        return pd.DataFrame()

    # Select survivors: DSR > 0.3, trades >= 30, win_rate >= 0.40
    surv = results_df[
        (results_df["dsr"] > 0.3) &
        (results_df["n_trades"] >= 30) &
        (results_df["win_rate"] >= 0.40)
    ]

    if surv.empty:
        return pd.DataFrame()

    spec   = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS["GC"])
    rows   = []
    strat_map = {cls.__name__.replace("Strategy", ""): cls for cls in strategy_classes}

    for _, row in surv.iterrows():
        strat_name = row["strategy"]
        params     = json.loads(row["params"])

        # Find matching strategy class
        matched_cls = None
        for cls in strategy_classes:
            if cls.name == strat_name:
                matched_cls = cls
                break
        if matched_cls is None:
            continue

        strat   = matched_cls(params=params)
        signals = strat.generate_signals(bars)
        trades  = strat.signals_to_trades(bars, signals)
        if not trades:
            continue

        trades_df = pd.DataFrame(trades)
        trades_df = _apply_costs(
            trades_df,
            tick_size=spec["tick_size"],
            tick_value=spec["tick_value"],
            slippage_ticks=2,
            commission_per_side=2.25,
        )

        m = _compute_metrics(trades_df, spec)
        if m:
            rows.append({
                "strategy": strat_name, "symbol": symbol,
                "params": json.dumps(params),
                "stress_dsr": m["dsr"], "stress_pnl": m["total_pnl"],
                "stress_wr":  m["win_rate"], "stress_trades": m["n_trades"],
            })

    return pd.DataFrame(rows)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",  default="ALL",  help="GC, SI, or ALL")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild L2 bar cache")
    parser.add_argument("--quick",   action="store_true", help="1-year sample only")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--filter-news", action="store_true",
                        help="Blank out bars within 30 min of FOMC/NFP/CPI/GDP events")
    parser.add_argument("--news-window", type=int, default=30,
                        help="Minutes before/after event to block (default 30)")
    args = parser.parse_args()

    symbols = ["GC", "SI"] if args.symbol == "ALL" else [args.symbol]
    strategy_classes = _load_strategies()

    # News filter setup
    news_filter = None
    if args.filter_news:
        try:
            from tick_news_filter import NewsFilter
            news_filter = NewsFilter()
            print(f"[NEWS FILTER] Active — blocking ±{args.news_window} min around events")
            print(f"  {news_filter.summary()}")
        except ImportError:
            print("[NEWS FILTER] WARNING: tick_news_filter.py not found — running without filter")

    all_results: List[pd.DataFrame] = []
    all_stress:  List[pd.DataFrame] = []

    print("=" * 70)
    print(" FORTRESS L2 STRATEGY BACKTEST BATTERY")
    print(f" Symbols: {symbols}  Strategies: {len(strategy_classes)}")
    print("=" * 70)

    for symbol in symbols:
        bars = _load_or_build_l2_bars(symbol, args.rebuild, args.quick)
        if bars.empty:
            continue

        # Apply news filter: zero-out signals on bars near major events
        if news_filter is not None:
            news_mask = news_filter.build_filter_mask(bars.index, window_minutes=args.news_window)
            n_blocked = news_mask.sum()
            n_total = len(bars)
            pct_blocked = 100 * n_blocked / n_total if n_total > 0 else 0
            print(f"[{symbol}] News filter: {n_blocked:,} bars blocked ({pct_blocked:.1f}% of {n_total:,})")
            # Mark news-blocked bars so strategies skip them
            bars = bars.copy()
            bars["_news_blocked"] = news_mask
        else:
            bars["_news_blocked"] = False

        print(f"\n[{symbol}] Running {len(strategy_classes)} strategy classes ...")
        results = _run_strategy_battery(bars, symbol, strategy_classes,
                                        min_trades=args.min_trades)
        if results.empty:
            print(f"[{symbol}] No results.")
            continue

        all_results.append(results)

        # Stress test
        stress = _stress_test(results, bars, symbol, strategy_classes)
        all_stress.append(stress)

    if not all_results:
        print("\nNo results produced.")
        return

    # Use symbol-specific prefix so parallel/sequential runs don't overwrite each other
    sym_tag = "_".join(symbols) if len(symbols) > 1 else symbols[0]
    mode_tag = "_quick" if args.quick else ""
    news_tag = "_newsfiltered" if args.filter_news else ""
    prefix   = f"{sym_tag}{mode_tag}{news_tag}"

    # Combine and save
    combined = pd.concat(all_results, ignore_index=True)
    combined = combined.sort_values("dsr", ascending=False)
    out_csv  = OUT_DIR / f"{prefix}_strategy_results.csv"
    combined.to_csv(out_csv, index=False)
    # Also save canonical name for evidence_upgrade compatibility
    combined.to_csv(OUT_DIR / "l2_strategy_results.csv", index=False)
    print(f"\n[SAVED] {out_csv}  ({len(combined)} combos)")

    # Survivors
    survivors = combined[
        (combined["dsr"] > 0.3) &
        (combined["n_trades"] >= 30) &
        (combined["win_rate"] >= 0.40)
    ]

    if not survivors.empty:
        surv_path = OUT_DIR / f"{prefix}_survivors.json"
        survivors.to_json(surv_path, orient="records", indent=2)
        survivors.to_json(OUT_DIR / "l2_survivors.json", orient="records", indent=2)
        print(f"[SURVIVORS] {len(survivors)} passed initial filter → {surv_path}")

        if all_stress:
            stress_df = pd.concat(all_stress, ignore_index=True)
            stress_path = OUT_DIR / f"{prefix}_stress_results.csv"
            stress_df.to_csv(stress_path, index=False)
            stress_df.to_csv(OUT_DIR / "l2_stress_results.csv", index=False)
            print(f"[STRESS] {stress_path}")

            # Final hardened survivors (pass stress too)
            hardened = stress_df[
                (stress_df["stress_dsr"] > 0.1) &
                (stress_df["stress_wr"] >= 0.38)
            ]
            if not hardened.empty:
                hard_path = OUT_DIR / f"{prefix}_hardened_survivors.json"
                hardened.to_json(hard_path, orient="records", indent=2)
                hardened.to_json(OUT_DIR / "l2_hardened_survivors.json", orient="records", indent=2)
                print(f"\n[HARDENED] {len(hardened)} strategies survived stress test")
                print(hardened[["strategy", "symbol", "stress_dsr",
                                "stress_pnl", "stress_wr"]].to_string(index=False))
    else:
        print("\n[NO SURVIVORS] No strategies passed the filter.")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
