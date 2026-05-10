"""
Fortress Integration Test: RSI Mean‑Rev + FOMC Drift
Uses anchored walk‑forward, MES realistic costs, DSR go/no‑go.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure the codebase root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "04_codebase"))

from src.data.es_data_pipeline import ESDataLoader
from src.data.data_schema import INSTRUMENTS, InstrumentSpec
from src.backtesting.cost_model import TransactionCost
from src.backtesting.metrics import performance_report
from src.backtesting.walk_forward import WalkForwardEngine
from src.strategies.rsi_meanrev import RSIMeanRevStrategy
from src.strategies.fomc_drift import FOMCDriftStrategy


def main():
    # ---------- 1. Data ----------
    data_path = Path(__file__).parent.parent / "01_data" / "raw" / "ES_1min.csv"
    loader = ESDataLoader(
        source="csv",
        data_path=str(data_path),
        source_tz="utc",
        col_mapping={"ts_event": "timestamp"},
    )
    df_1m = loader.load()                         # 1-min raw
    df_rth = loader.filter_rth(df_1m)             # RTH only (9:30-16:00)
    df_5m = loader.resample(df_rth, "5min")       # 5-min
    df_feat = loader.add_features(df_5m)          # adds rsi, atr, session vwap, etc.

    print(f"Data loaded: {len(df_feat)} bars from {df_feat.index[0]} to {df_feat.index[-1]}")

    # ---------- 2. Cost Model (Realistic MES = 1-tick slippage per side) ----------
    base_mes = INSTRUMENTS["MES"]
    realistic_mes = InstrumentSpec(
        symbol="MES",
        tick_size=base_mes.tick_size,
        tick_value=base_mes.tick_value,
        point_value=base_mes.point_value,
        commission_per_side=base_mes.commission_per_side,
        slippage_ticks_per_side=1,           # realistic
        rth_start=base_mes.rth_start,
        rth_end=base_mes.rth_end,
    )
    cost_model = TransactionCost(instrument=realistic_mes)
    print(f"MES realistic cost per round-turn: {realistic_mes.cost_per_rt_pts:.4f} pts")

    # ---------- 3. Walk‑Forward Engine ----------
    # Anchored: training starts at first day, expands; test on 1‑year windows
    wf_engine = WalkForwardEngine(
        train_days=1000,      # ~4 years initial training
        test_days=252,        # 1‑year OOS
        objective="sharpe",
        cost_model=cost_model,
        anchored=True,
    )

    # Strategy 1: RSI Mean‑Rev
    print("\n=== RSI Mean‑Rev Strategy ===")
    rsi_result = wf_engine.run(
        data=df_feat,
        strategy_class=RSIMeanRevStrategy,
        param_grid=RSIMeanRevStrategy.param_grid,
    )
    print(rsi_result.summary())

    # =================================================================
    # Strategy 2: FOMC Drift (Calendar Strategy — One-Shot IS/OOS)
    # Walk‑forward is unsuitable for sparse calendar trades.
    # We optimise exit_offset on IS (2010–2018) and test on OOS (2019–2026).
    # =================================================================
    print("\n=== FOMC Drift Strategy (One-Shot IS/OOS) ===")

    # Split data into IS (2010-2018) and OOS (2019-2026)
    train_end_dt = pd.Timestamp("2018-12-31", tz="US/Eastern")
    test_start_dt = pd.Timestamp("2019-01-02", tz="US/Eastern")

    train_mask = df_feat.index <= train_end_dt
    test_mask = df_feat.index >= test_start_dt
    df_train = df_feat[train_mask].copy()
    df_test = df_feat[test_mask].copy()

    # Param sweep over exit_offset on IS
    best_offset = 0
    best_is_score = -999.0
    for offset in FOMCDriftStrategy.param_grid["exit_offset"]:
        strat = FOMCDriftStrategy(params={"exit_offset": offset})
        # FOMC doesn't use generate_signals; pass empty signals
        dummy_signals = pd.Series(0, index=df_train.index)
        trades = strat.signals_to_trades(df_train, dummy_signals)
        trades_df = strat.trades_to_dataframe(trades)
        if len(trades_df) > 0:
            trades_df = cost_model.apply_to_trades(trades_df)
            net_pnl = trades_df["net_pnl"].values
            if len(net_pnl) > 1 and np.std(net_pnl, ddof=1) > 0:
                score = np.mean(net_pnl) / np.std(net_pnl, ddof=1)
            else:
                score = -999.0
            if score > best_is_score:
                best_is_score = score
                best_offset = offset

    print(f"Best exit_offset on IS (2010–2018): {best_offset}")

    # Apply best params to OOS
    final_strat = FOMCDriftStrategy(params={"exit_offset": best_offset})
    dummy_signals_oos = pd.Series(0, index=df_test.index)
    oos_trades = final_strat.signals_to_trades(df_test, dummy_signals_oos)
    oos_trades_df = final_strat.trades_to_dataframe(oos_trades)

    if len(oos_trades_df) > 0:
        oos_trades_df = cost_model.apply_to_trades(oos_trades_df)
        oos_pnl = oos_trades_df["net_pnl"].values
        days_in_oos = (df_test.index[-1] - df_test.index[0]).days
        n_trades_per_year = len(oos_trades_df) / (days_in_oos / 365.25)
        n_trials = len(FOMCDriftStrategy.param_grid["exit_offset"])
        report = performance_report(oos_pnl, n_trades_per_year, n_trials=n_trials)
        print(report["summary"])
        from src.backtesting.metrics import evaluate_go_nogo
        go_nogo = evaluate_go_nogo(report)
        print(f"Go/No‑Go: {go_nogo['verdict']}")
    else:
        print("No FOMC OOS trades generated — check date availability.")


if __name__ == "__main__":
    main()