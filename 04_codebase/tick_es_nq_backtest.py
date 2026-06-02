"""
tick_es_nq_backtest.py — ES/NQ Extended History Backtest
==========================================================
Runs all price-action + OHLCV strategies against extended ES/NQ bars
(2020-2025 after download, plus existing Dec 2025-present data).

Adds VWAP and session-based features before running the battery.

Run:
  venv_new/Scripts/python.exe 04_codebase/tick_es_nq_backtest.py
  venv_new/Scripts/python.exe 04_codebase/tick_es_nq_backtest.py --symbol ES
  venv_new/Scripts/python.exe 04_codebase/tick_es_nq_backtest.py --tf 5m  (use 5m bars)
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
OUT_DIR = ROOT / "05_backtests" / "es_nq_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONTRACT_SPECS = {
    "ES": {"tick_size": 0.25, "tick_value": 12.5, "name": "E-mini S&P 500"},
    "NQ": {"tick_size": 0.25, "tick_value": 5.0,  "name": "E-mini Nasdaq 100"},
}

TF_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
}


def _atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low = data["high"], data["low"]
    prev_close = data["close"].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _add_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Add VWAP, ATR, and session features to bar DataFrame."""
    data = bars.copy()
    close = data["close"]
    vol   = data.get("volume", pd.Series(1, index=data.index))

    # Session VWAP (resets at 17:00 UTC CME close)
    session_key = (data.index - pd.Timedelta(hours=17)).date
    vwap = pd.Series(np.nan, index=data.index)
    s_series = pd.Series(session_key, index=data.index)
    for date, grp_idx in s_series.groupby(s_series).groups.items():
        v = vol.loc[grp_idx]
        c = close.loc[grp_idx]
        cum_vol  = v.cumsum()
        cum_tpv  = (c * v).cumsum()
        vwap.loc[grp_idx] = cum_tpv / cum_vol.replace(0, np.nan)
    data["session_vwap"] = vwap

    # ATR
    data["atr14"] = _atr(data, 14)

    # Hour of day (UTC)
    data["hour_utc"] = data.index.hour

    # Session date
    data["session_date"] = pd.Series(session_key, index=data.index)

    # Buy/sell volume if not present (approximate from CVD delta)
    if "buy_vol" not in data.columns and "cvd_delta" in data.columns:
        data["buy_vol"]  = (data["volume"] + data["cvd_delta"]) // 2
        data["sell_vol"] = (data["volume"] - data["cvd_delta"]) // 2

    return data


def _load_bars(symbol: str, tf: str) -> pd.DataFrame:
    path = BAR_DIR / f"{symbol}_bars_{tf}.parquet"
    if not path.exists():
        print(f"  [WARNING] {path.name} not found — skipping {symbol} {tf}")
        return pd.DataFrame()
    df = pd.read_parquet(path)
    print(f"  Loaded {symbol}_{tf}: {len(df):,} bars ({df.index.min().date()} to {df.index.max().date()})")
    return df


def _apply_costs(trades_df, tick_size, slippage_ticks=1, commission=2.25):
    out = trades_df.copy()
    cost_pts = slippage_ticks * 2 * tick_size
    out["net_pnl"] = out.get("gross_pnl", 0) - cost_pts
    return out


def _compute_metrics(trades_df: pd.DataFrame, spec: dict) -> dict:
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return {}
    tick_val = spec["tick_value"]
    tick_sz  = spec["tick_size"]
    dollar_pnl = trades_df["net_pnl"] * (tick_val / tick_sz)

    n      = len(trades_df)
    wins   = (dollar_pnl > 0).sum()
    losses = (dollar_pnl < 0).sum()
    wr     = wins / n if n > 0 else 0
    total  = dollar_pnl.sum()
    avg_w  = dollar_pnl[dollar_pnl > 0].mean() if wins > 0 else 0
    avg_l  = dollar_pnl[dollar_pnl < 0].mean() if losses > 0 else 0
    pf     = abs(avg_w * wins / (avg_l * losses + 1e-9)) if losses > 0 else np.nan

    if "exit_time" in trades_df.columns:
        daily = dollar_pnl.groupby(pd.to_datetime(trades_df["exit_time"]).dt.date).sum()
    else:
        daily = pd.Series(dollar_pnl.values)

    sharpe = (daily.mean() / (daily.std() + 1e-9)) * np.sqrt(252) if len(daily) > 1 else 0
    cum    = dollar_pnl.cumsum()
    max_dd = (cum.cummax() - cum).max()
    T      = len(daily)
    skew   = daily.skew() if len(daily) > 2 else 0
    kurt   = daily.kurtosis() if len(daily) > 3 else 0
    dsr    = max(sharpe * (1 - skew / 6 * sharpe - (kurt - 3) / 24 * sharpe**2), 0) - 0.2 / np.sqrt(max(T, 1))

    return {
        "n_trades": n, "win_rate": round(wr, 4),
        "total_pnl": round(total, 2), "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
        "profit_factor": round(pf, 3) if not np.isnan(pf) else 0,
        "sharpe": round(sharpe, 4), "dsr": round(dsr, 4), "max_dd": round(max_dd, 2),
    }


def _load_all_strategies():
    """Load all strategy classes that work on OHLCV bars."""
    from src.strategies.es_nq_price_action import (
        EnhancedORBStrategy, VWAPDeviationStrategy, PrevDayHLSweepRevStrategy,
        RangeContractionBreakoutStrategy, MultiDayMomentumStrategy
    )
    from src.strategies.vwap_reclaim import VWAPReclaimStrategy
    from src.strategies.rth_orb import RTHORBStrategy

    strats = [
        EnhancedORBStrategy,
        VWAPDeviationStrategy,
        PrevDayHLSweepRevStrategy,
        RangeContractionBreakoutStrategy,
        MultiDayMomentumStrategy,
        VWAPReclaimStrategy,
    ]

    try:
        strats.append(RTHORBStrategy)
    except Exception:
        pass

    return strats


def _run_battery(bars: pd.DataFrame, symbol: str, tf: str,
                 strategy_classes: list, min_trades: int = 30) -> pd.DataFrame:
    spec = CONTRACT_SPECS[symbol]
    rows = []

    for StratClass in strategy_classes:
        grid   = StratClass.param_grid
        combos = list(product(*grid.values()))

        for combo in combos:
            params = dict(zip(grid.keys(), combo))
            strat  = StratClass(params=params)

            try:
                signals = strat.generate_signals(bars)
                if (signals != 0).sum() < 5:
                    continue

                trades = strat.signals_to_trades(bars, signals)
                if len(trades) < min_trades:
                    continue

                trades_df = pd.DataFrame(trades)
                trades_df = _apply_costs(trades_df, spec["tick_size"])
                metrics   = _compute_metrics(trades_df, spec)
                if not metrics:
                    continue

                row = {
                    "strategy": strat.name, "symbol": symbol, "tf": tf,
                    "params": json.dumps(params), **metrics,
                }
                rows.append(row)
                print(f"  {strat.name:<35} {symbol}/{tf}  "
                      f"t={metrics['n_trades']:>4}  "
                      f"dsr={metrics['dsr']:+.3f}  "
                      f"pnl=${metrics['total_pnl']:>9,.0f}")
            except Exception:
                continue

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",     default="ALL")
    parser.add_argument("--tf",         default="5m",
                        help="Bar timeframe: 1m,3m,5m,15m,30m")
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()

    symbols = ["ES", "NQ"] if args.symbol == "ALL" else [args.symbol]
    tf      = args.tf
    strategy_classes = _load_all_strategies()

    print("=" * 70)
    print(f" ES/NQ PRICE ACTION BACKTEST  tf={tf}  strategies={len(strategy_classes)}")
    print("=" * 70)

    all_results = []

    for sym in symbols:
        bars = _load_bars(sym, tf)
        if bars.empty:
            continue

        bars = _add_features(bars)
        print(f"\n[{sym}/{tf}] {len(bars):,} bars with features. Running battery...")
        results = _run_battery(bars, sym, tf, strategy_classes, min_trades=args.min_trades)

        if not results.empty:
            all_results.append(results)

    if not all_results:
        print("\nNo results — check data availability.")
        return

    combined = pd.concat(all_results).sort_values("dsr", ascending=False)
    out_path = OUT_DIR / f"es_nq_results_{tf}.csv"
    combined.to_csv(out_path, index=False)
    print(f"\n[SAVED] {out_path}  ({len(combined)} rows)")

    survivors = combined[
        (combined["dsr"] > 0.3) &
        (combined["n_trades"] >= 30) &
        (combined["win_rate"] >= 0.40)
    ]
    if not survivors.empty:
        survivors.to_json(OUT_DIR / f"es_nq_survivors_{tf}.json",
                          orient="records", indent=2)
        print(f"\n[SURVIVORS] {len(survivors)}")
        print(survivors[["strategy", "symbol", "tf", "dsr", "total_pnl", "win_rate", "n_trades"]]
              .head(20).to_string(index=False))
    else:
        print("\n[NO SURVIVORS]")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
