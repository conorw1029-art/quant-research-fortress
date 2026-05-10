#!/usr/bin/env python3
"""
Pairs Data Loader + ESNQ Pairs Feasibility Test
=================================================
Loads ES and NQ 1-min CSVs, resamples to 1-hour bars,
aligns them on common timestamps, then runs the pairs strategy
through walk-forward.

Usage:
    python run_pairs.py `
        --es-path "../01_data/raw/ES_1min.csv" `
        --nq-path "../01_data/raw/NQ_1min.csv" `
        --source-tz utc --col-timestamp ts_event
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

from src.data.es_data_pipeline import ESDataLoader
import src.data.data_schema as S
from src.data.data_schema import INSTRUMENTS, InstrumentSpec
from src.backtesting.cost_model import TransactionCost
from src.backtesting.metrics import performance_report, evaluate_go_nogo
from src.backtesting.walk_forward import WalkForwardEngine
from src.zoo.database import ZooDatabase
from src.strategies.esnq_pairs import ESNQPairsStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

IS_END    = "2018-12-31"
OOS_START = "2019-01-01"


def load_and_align(
    es_path: str,
    nq_path: str,
    source_tz: str,
    col_ts: str,
    timeframe: str = "1h",
) -> pd.DataFrame:
    """
    Load both instruments, resample, and align on common timestamps.
    Returns DataFrame with columns: open, high, low, close, volume,
    close_es, close_nq, session_date.
    """
    def load_one(path, label):
        loader = ESDataLoader(
            source="csv", data_path=path,
            source_tz=source_tz,
            col_mapping={col_ts: "timestamp"},
        )
        raw = loader.load()
        rth = loader.filter_rth(raw)
        bars = loader.resample(rth, timeframe)
        logger.info(f"  {label}: {len(bars):,} {timeframe} bars")
        return bars

    logger.info("Loading ES...")
    es = load_one(es_path, "ES")

    logger.info("Loading NQ...")
    nq = load_one(nq_path, "NQ")

    # Align on common timestamps
    common_idx = es.index.intersection(nq.index)
    logger.info(f"  Common timestamps: {len(common_idx):,}")

    es_aligned = es.loc[common_idx].copy()
    nq_aligned = nq.loc[common_idx].copy()

    # Build combined DataFrame
    # Primary OHLCV columns are ES (for cost model purposes)
    # NQ close added as close_nq
    combined = es_aligned.copy()
    combined["close_es"] = es_aligned["close"]
    combined["close_nq"] = nq_aligned["close"]
    combined["volume_nq"] = nq_aligned["volume"]

    # session_date
    combined[S.SESSION_DATE] = combined.index.date

    logger.info(f"  Combined: {len(combined):,} aligned bars")
    logger.info(f"  Date range: {combined.index[0]} -> {combined.index[-1]}")

    return combined


def run_cointegration_diagnostic(data: pd.DataFrame):
    """Quick ADF test to confirm ES/NQ are cointegrated."""
    from scipy.stats import pearsonr

    # Use log prices
    log_es = np.log(data["close_es"].dropna())
    log_nq = np.log(data["close_nq"].dropna())

    # Align
    aligned = pd.concat([log_es, log_nq], axis=1).dropna()
    aligned.columns = ["log_es", "log_nq"]

    # OLS: log_ES = alpha + beta * log_NQ
    slope, intercept, r, p, se = stats.linregress(aligned["log_nq"], aligned["log_es"])
    spread = aligned["log_es"] - slope * aligned["log_nq"] - intercept

    # ADF test on spread (test for stationarity = cointegration)
    try:
        from statsmodels.tsa.stattools import adfuller
        adf_result = adfuller(spread.dropna(), maxlags=10)
        adf_stat = adf_result[0]
        adf_pval = adf_result[1]
        adf_available = True
    except ImportError:
        adf_stat = None
        adf_pval = None
        adf_available = False

    print(f"/n{'='*70}")
    print(f"  COINTEGRATION DIAGNOSTIC")
    print(f"{'='*70}")
    print(f"  Hedge ratio (beta): {slope:.4f}")
    print(f"  Correlation (log prices): {r:.4f}")
    print(f"  Spread mean: {spread.mean():.6f}")
    print(f"  Spread std:  {spread.std():.6f}")

    if adf_available:
        print(f"  ADF statistic: {adf_stat:.4f}")
        print(f"  ADF p-value:   {adf_pval:.4f}")
        if adf_pval < 0.05:
            print(f"  COINTEGRATED (p < 0.05) — spread is stationary")
        else:
            print(f"  NOT cointegrated at p=0.05 — spread has unit root")
    else:
        print(f"  (Install statsmodels for ADF test: pip install statsmodels)")
        print(f"  Correlation {r:.4f} suggests {('strong' if abs(r) > 0.95 else 'moderate')} relationship")

    # Rolling correlation check
    ret_es = np.log(data["close_es"]).diff()
    ret_nq = np.log(data["close_nq"]).diff()
    rolling_corr = ret_es.rolling(500).corr(ret_nq)
    print(f"  Rolling 500-bar return correlation: min={rolling_corr.min():.3f}, "
          f"mean={rolling_corr.mean():.3f}, max={rolling_corr.max():.3f}")
    print(f"  (High positive correlation = good pairs candidate)")


def main():
    parser = argparse.ArgumentParser(description="ES/NQ Pairs Test")
    parser.add_argument("--es-path", default=None)
    parser.add_argument("--nq-path", default=None)
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--zoo-path", default=None)
    args = parser.parse_args()

    project_root = THIS_DIR.parent
    es_path = args.es_path or str(project_root / "01_data" / "raw" / "ES_1min.csv")
    nq_path = args.nq_path or str(project_root / "01_data" / "raw" / "NQ_1min.csv")
    zoo_path = args.zoo_path or str(project_root / "05_backtests" / "zoo.jsonl")

    print(f"/n{'='*70}")
    print(f"  ES/NQ PAIRS TRADING — FEASIBILITY TEST")
    print(f"{'='*70}")

    # Load and align
    data = load_and_align(es_path, nq_path, args.source_tz, args.col_timestamp, timeframe="1h")

    # Cointegration diagnostic
    run_cointegration_diagnostic(data)

    # Cost model — MES realistic for ES leg
    base_mes = INSTRUMENTS["MES"]
    mes_realistic = InstrumentSpec(
        symbol="MES",
        tick_size=base_mes.tick_size,
        tick_value=base_mes.tick_value,
        point_value=base_mes.point_value,
        commission_per_side=base_mes.commission_per_side,
        exchange_fee_per_side=getattr(base_mes, 'exchange_fee_per_side', 0.35),
        slippage_ticks_per_side=1,
        rth_start=base_mes.rth_start,
        rth_end=base_mes.rth_end,
    )
    cost_model = TransactionCost(instrument=mes_realistic, slippage_scenario="realistic")
    print(f"/n  Cost model: {cost_model.cost_per_rt():.4f} pts/RT (MES realistic)")
    print(f"  Note: This tests the ES leg only. Full pairs cost = 2x (ES + NQ legs).")

    # Walk-forward engine
    engine = WalkForwardEngine(
        train_days=756,   # 3 years
        test_days=252,    # 1 year
        objective="sharpe",
        cost_model=cost_model,
        anchored=True,
    )

    print(f"/n  Running walk-forward optimization...")
    print(f"  Param grid: {ESNQPairsStrategy.param_grid}")
    print(f"  (This uses 1-hour bars — expect faster runtime than 5-min strategies)")

    t_start = time.time()
    result = engine.run(
        data=data,
        strategy_class=ESNQPairsStrategy,
        param_grid=ESNQPairsStrategy.param_grid,
    )
    elapsed = time.time() - t_start

    # Print results
    print(result.summary())
    print(f"/n  Runtime: {elapsed:.1f}s")

    # Record to zoo
    zoo = ZooDatabase(zoo_path)
    strat_instance = ESNQPairsStrategy()
    strat_instance.name = "esnq_pairs"
    strat_instance.category = "stat_arb"
    record = zoo.record_from_result(
        strategy=strat_instance,
        result=result,
        test_method="walk_forward_anchored",
        notes=f"ES/NQ pairs, 1h bars, ES leg only cost model, realistic MES",
    )
    print(f"/n  Zoo record: verdict={record.verdict}  DSR={record.dsr:+.3f}")
    print(f"/n  IMPORTANT: If this passes, next step is testing BOTH legs simultaneously.")
    print(f"  A real pairs trade costs ~2x in commissions (ES + NQ leg).")
    print(f"  Re-test with doubled cost before declaring a survivor.")


if __name__ == "__main__":
    main()