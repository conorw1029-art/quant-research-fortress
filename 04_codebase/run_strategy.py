#!/usr/bin/env python3
"""
Strategy Runner
================
Unified entry point for testing any strategy in the registry.
Each strategy's instrument and data path come from the registry entry,
so running fomc_drift_nq automatically uses NQ data and MNQ costs.

Usage:
    # Test a single strategy (instrument + data path from registry):
    python run_strategy.py --key fomc_drift_nq

    # Override data path:
    python run_strategy.py --key fomc_drift_nq --data-path "../01_data/raw/NQ_1min.csv"

    # Override cost scenario:
    python run_strategy.py --key fomc_drift --cost-scenario conservative

    # Test all active/experimental strategies:
    python run_strategy.py --all

    # Print registry:
    python run_strategy.py --registry

    # Print zoo summary:
    python run_strategy.py --summary
"""

import argparse
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

from src.data.es_data_pipeline import ESDataLoader
import src.data.data_schema as S
from src.data.data_schema import InstrumentSpec, INSTRUMENTS, DATA_PATHS
from src.backtesting.cost_model import TransactionCost
from src.backtesting.metrics import performance_report, evaluate_go_nogo
from src.backtesting.walk_forward import WalkForwardEngine
from src.zoo.database import ZooDatabase
from src.zoo.registry import (
    get_all, get_active, get_by_key, get_by_status,
    Status, TestMethod, summary as registry_summary,
    StrategyEntry,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# DATA LOADING (CACHED)
# ══════════════════════════════════════════════════════════════════

_DATA_CACHE = {}

def load_data_cached(
    csv_path: str,
    timeframe: str,
    source_tz: str = "utc",
    col_timestamp: str = "ts_event",
):
    cache_key = (csv_path, timeframe)
    if cache_key in _DATA_CACHE:
        logger.info(f"  Data cache hit: {Path(csv_path).name} @ {timeframe}")
        return _DATA_CACHE[cache_key]

    logger.info(f"  Loading data: {Path(csv_path).name} @ {timeframe}")
    loader = ESDataLoader(
        source="csv",
        data_path=csv_path,
        source_tz=source_tz,
        col_mapping={col_timestamp: "timestamp"},
    )
    df_raw = loader.load()
    df_rth = loader.filter_rth(df_raw)
    df_bars = loader.resample(df_rth, timeframe)

    if timeframe == "1D":
        df_feat = loader.add_daily_features(df_bars)
    else:
        df_feat = loader.add_features(df_bars)

    logger.info(f"  Data ready: {len(df_feat):,} bars")
    _DATA_CACHE[cache_key] = (loader, df_feat)
    return loader, df_feat


# ══════════════════════════════════════════════════════════════════
# COST MODEL
# ══════════════════════════════════════════════════════════════════

def build_cost_model(instrument_key: str, scenario: str) -> TransactionCost:
    base = INSTRUMENTS[instrument_key]
    slippage_ticks = {
        "zero": 0,
        "optimistic": 0,
        "realistic": 1,
        "conservative": 2,
    }.get(scenario, 1)
    overridden = InstrumentSpec(
        symbol=base.symbol,
        tick_size=base.tick_size,
        tick_value=base.tick_value,
        point_value=base.point_value,
        commission_per_side=base.commission_per_side,
        exchange_fee_per_side=getattr(base, 'exchange_fee_per_side', 0.0),
        slippage_ticks_per_side=slippage_ticks,
        rth_start=base.rth_start,
        rth_end=base.rth_end,
    )
    return TransactionCost(instrument=overridden)


# ══════════════════════════════════════════════════════════════════
# TEST METHODS
# ══════════════════════════════════════════════════════════════════

def run_walk_forward(entry, data, cost_model, train_days=1000, test_days=252, anchored=True):
    strategy_class = entry.load_class()
    engine = WalkForwardEngine(
        train_days=train_days, test_days=test_days,
        objective="sharpe", cost_model=cost_model, anchored=anchored,
    )
    temp_strat = strategy_class()
    return engine.run(data=data, strategy_class=strategy_class, param_grid=temp_strat.param_grid)


def run_one_shot_is_oos(
    entry, data, cost_model,
    train_end="2018-12-31", test_start="2019-01-02",
):
    strategy_class = entry.load_class()
    temp_strat = strategy_class()

    # Split
    if isinstance(data.index, pd.DatetimeIndex):
        tz = data.index.tz
        train_end_ts = pd.Timestamp(train_end)
        test_start_ts = pd.Timestamp(test_start)
        if tz is not None:
            if train_end_ts.tz is None:
                train_end_ts = train_end_ts.tz_localize(tz)
            if test_start_ts.tz is None:
                test_start_ts = test_start_ts.tz_localize(tz)
        df_train = data[data.index <= train_end_ts].copy()
        df_test = data[data.index >= test_start_ts].copy()
    else:
        raise ValueError("One-shot test requires DatetimeIndex")

    # Grid search on IS
    import itertools
    grid = temp_strat.param_grid
    combos = [dict(zip(grid.keys(), c)) for c in itertools.product(*grid.values())] if grid else [{}]

    best_params, best_score = combos[0], -np.inf
    for params in combos:
        strat = strategy_class(params=params)
        dummy = pd.Series(0, index=df_train.index)
        trades = strat.signals_to_trades(df_train, dummy)
        df_t = strat.trades_to_dataframe(trades)
        if len(df_t) < 5:
            continue
        df_t = cost_model.apply_to_trades(df_t)
        net = df_t["net_pnl"].values
        if len(net) > 1 and np.std(net, ddof=1) > 0:
            score = np.mean(net) / np.std(net, ddof=1)
            if score > best_score:
                best_score, best_params = score, params

    # OOS test
    final_strat = strategy_class(params=best_params)
    dummy_oos = pd.Series(0, index=df_test.index)
    oos_trades = final_strat.signals_to_trades(df_test, dummy_oos)
    oos_df = final_strat.trades_to_dataframe(oos_trades)

    n_oos = len(oos_df)
    if n_oos > 0:
        oos_df = cost_model.apply_to_trades(oos_df)
        oos_pnl = oos_df["net_pnl"].values
        days = max((df_test.index[-1] - df_test.index[0]).days, 1)
        tpy = n_oos / (days / 365.25)
        report = performance_report(oos_pnl, trades_per_year=tpy, n_trials=len(combos),
                                    instrument_point_value=cost_model.instrument.point_value)
        go_nogo = evaluate_go_nogo(report)
    else:
        report = {"standard": {}, "dsr": {}, "psr": {}, "monte_carlo": {},
                  "hurst": 0.5, "dollars": {}, "summary": "No OOS trades"}
        go_nogo = {"verdict": "FAIL", "failures": ["no_trades"]}
        oos_pnl = np.array([])
        oos_df = pd.DataFrame()

    # Wrap as pseudo-WalkForwardResult
    class _R: pass
    r = _R()
    r.total_param_combos = len(combos)
    r.total_elapsed_seconds = 0.0
    r.combined_oos_trades = oos_df
    r.combined_oos_pnl = oos_pnl
    r.aggregate_metrics = report.get("standard", {})
    r.aggregate_report = report
    r.go_nogo = go_nogo
    r.config = {"cost_model": repr(cost_model), "test_method": "one_shot_is_oos"}

    class _F: pass
    f = _F()
    f.best_params = best_params
    f.oos_metrics = report.get("standard", {})
    r.folds = [f]

    return r


# ══════════════════════════════════════════════════════════════════
# SINGLE STRATEGY TEST
# ══════════════════════════════════════════════════════════════════

def test_strategy(
    entry: StrategyEntry,
    zoo: ZooDatabase,
    cost_scenario: str = "realistic",
    source_tz: str = "utc",
    col_timestamp: str = "ts_event",
    train_days: int = 1000,
    test_days: int = 252,
    anchored: bool = True,
    train_end: str = "2018-12-31",
    test_start: str = "2019-01-02",
    data_path_override: Optional[str] = None,
) -> dict:
    logger.info(f"=== Testing: {entry.key} ({entry.test_method.value}) ===")
    start_time = time.time()

    try:
        # Resolve data path from DATA_PATHS in data_schema
        project_root = THIS_DIR.parent
        raw_dir = project_root / "01_data" / "raw"
        fname = DATA_PATHS.get(entry.data_path_key)
        if fname is None:
            raise FileNotFoundError(
                f"No DATA_PATHS entry for '{entry.data_path_key}'. "
                f"Add it to data_schema.py DATA_PATHS dict."
            )
        csv_path = str(raw_dir / fname)
        if not Path(csv_path).exists():
            raise FileNotFoundError(
                f"Data file not found: {csv_path}. "
                f"Download {entry.data_path_key} data first."
            )

        _, data = load_data_cached(csv_path, entry.timeframe, source_tz, col_timestamp)

        # Build cost model from registry instrument
        cost_model = build_cost_model(entry.instrument, cost_scenario)
        logger.info(f"  Instrument: {entry.instrument}  Cost: {cost_model.instrument.cost_per_rt_pts:.4f} pts/RT ({cost_scenario})")

        # Run test
        if entry.test_method == TestMethod.WALK_FORWARD:
            result = run_walk_forward(entry, data, cost_model, train_days, test_days, anchored)
        elif entry.test_method == TestMethod.ONE_SHOT_IS_OOS:
            result = run_one_shot_is_oos(entry, data, cost_model, train_end, test_start)
        else:
            raise NotImplementedError(f"Test method {entry.test_method} not implemented")

        # Record to zoo
        strategy_class = entry.load_class()
        best_params = result.folds[0].best_params if result.folds else {}
        strat_instance = strategy_class(params=best_params)
        strat_instance.name = entry.key
        strat_instance.category = entry.category

        data_range = (
            str(data.index[0].date() if hasattr(data.index[0], 'date') else data.index[0]),
            str(data.index[-1].date() if hasattr(data.index[-1], 'date') else data.index[-1]),
        )
        record = zoo.record_from_result(
            strategy=strat_instance, result=result,
            test_method=entry.test_method.value, data_range=data_range,
            notes=f"instrument={entry.instrument} cost_scenario={cost_scenario}; {entry.notes}",
        )

        elapsed = time.time() - start_time
        logger.info(
            f"  DONE: verdict={record.verdict}  DSR={record.dsr:+.3f}"
            f"  PF={record.oos_profit_factor:.3f}  n_oos={record.n_oos_trades}"
            f"  elapsed={elapsed:.1f}s"
        )
        return {"key": entry.key, "verdict": record.verdict, "dsr": record.dsr,
                "pf": record.oos_profit_factor, "n_oos": record.n_oos_trades,
                "elapsed": elapsed, "error": None}

    except Exception as e:
        elapsed = time.time() - start_time
        tb = traceback.format_exc()
        logger.error(f"  ERROR in {entry.key}: {e}\n{tb}")
        try:
            strategy_class = entry.load_class()
            strat_instance = strategy_class()
            strat_instance.name = entry.key
            zoo.record_from_result(strategy=strat_instance, result=None,
                                   test_method=entry.test_method.value,
                                   notes=f"instrument={entry.instrument}",
                                   error=f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return {"key": entry.key, "verdict": "ERROR", "dsr": 0.0, "pf": 0.0,
                "n_oos": 0, "elapsed": elapsed, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# BATCH RUNNER
# ══════════════════════════════════════════════════════════════════

def run_batch(entries, zoo_path, cost_scenario="realistic", **kwargs):
    zoo = ZooDatabase(zoo_path)
    results = []

    logger.info(f"\n{'='*70}")
    logger.info(f"  BATCH RUN: {len(entries)} strategies, cost={cost_scenario}")
    logger.info(f"{'='*70}\n")

    total_start = time.time()
    for i, entry in enumerate(entries, 1):
        logger.info(f"\n[{i}/{len(entries)}] {entry.key} ({entry.instrument})")
        result = test_strategy(entry=entry, zoo=zoo, cost_scenario=cost_scenario, **kwargs)
        results.append(result)

    total_elapsed = time.time() - total_start
    logger.info(f"\n{'='*70}")
    logger.info(f"  BATCH COMPLETE: {len(results)} strategies in {total_elapsed:.1f}s")
    logger.info(f"{'='*70}")
    logger.info(f"  {'Key':<25s} {'Instr':<6s} {'Verdict':<10s} {'DSR':>8s} {'PF':>8s} {'n_oos':>8s}")
    logger.info(f"  {'-'*70}")
    for r in results:
        instr = get_by_key(r['key']).instrument if get_by_key(r['key']) else "?"
        logger.info(f"  {r['key']:<25s} {instr:<6s} {r['verdict']:<10s} "
                    f"{r['dsr']:>+8.3f} {r['pf']:>8.3f} {r['n_oos']:>8d}")

    logger.info(f"\n{zoo.summary()}")
    return results


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Strategy runner")
    parser.add_argument("--key", help="Strategy key to test")
    parser.add_argument("--all", action="store_true", help="Test all active strategies")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--registry", action="store_true")

    parser.add_argument("--data-path", default=None,
                        help="Override data CSV path (default: from registry)")
    parser.add_argument("--zoo-path", default=None)
    parser.add_argument("--cost-scenario", default="realistic",
                        choices=["zero", "optimistic", "realistic", "conservative"])
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--train-days", type=int, default=1000)
    parser.add_argument("--test-days", type=int, default=252)
    parser.add_argument("--anchored", action="store_true", default=True)
    parser.add_argument("--rolling", dest="anchored", action="store_false")

    args = parser.parse_args()

    project_root = THIS_DIR.parent
    default_zoo = project_root / "05_backtests" / "zoo.jsonl"
    zoo_path = args.zoo_path or str(default_zoo)

    if args.registry:
        print(registry_summary())
        return

    if args.summary:
        zoo = ZooDatabase(zoo_path)
        print(zoo.summary())
        return

    if args.key:
        entry = get_by_key(args.key)
        if entry is None:
            logger.error(f"Unknown strategy key: {args.key}")
            logger.info(f"Available: {[s.key for s in get_all()]}")
            sys.exit(1)
        entries = [entry]
    elif args.all:
        entries = get_active()
        if args.include_rejected:
            entries += get_by_status(Status.REJECTED)
    else:
        parser.print_help()
        sys.exit(0)

    run_batch(
        entries=entries,
        zoo_path=zoo_path,
        cost_scenario=args.cost_scenario,
        source_tz=args.source_tz,
        col_timestamp=args.col_timestamp,
        train_days=args.train_days,
        test_days=args.test_days,
        anchored=args.anchored,
        data_path_override=args.data_path,
    )


if __name__ == "__main__":
    main()