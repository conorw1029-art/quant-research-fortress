"""
Walk-Forward Optimization Engine
==================================
The heart of the Backtesting Fortress.

Implements rolling walk-forward optimization:
  1. For each fold: optimize params on training window, test on OOS window.
  2. Concatenate all OOS results into a single honest equity curve.
  3. Apply DSR to penalize for all param combos tested across all folds.

Supports:
  - Rolling window (fixed-size train) or anchored (expanding train)
  - Grid search with arbitrary param grids
  - Multiple objective functions (Sharpe, Calmar, PF, expectancy)
  - Parameter stability tracking across folds
  - Full audit trail (every param combo, every fold, every trade)

Usage:
    from src.backtesting.walk_forward import WalkForwardEngine
    from src.backtesting.cost_model import TransactionCost
    from src.data.data_schema import MES
    
    engine = WalkForwardEngine(
        train_days=756,    # ~3 years
        test_days=252,     # ~1 year
        objective="sharpe",
        cost_model=TransactionCost(instrument=MES),
        anchored=False,
    )
    result = engine.run(data, MyStrategy)
    print(result.summary())
"""

import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
import data_schema as S
from src.backtesting.cost_model import TransactionCost
from src.backtesting.metrics import (
    standard_metrics,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    monte_carlo_permutation,
    performance_report,
    evaluate_go_nogo,
)
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════

@dataclass
class FoldResult:
    """Results from a single walk-forward fold."""
    fold_id: int
    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    best_params: Dict[str, Any]
    best_objective_score: float
    # All param combos tested in this fold
    param_scores: List[Dict[str, Any]]  # [{params: {...}, score: float, n_trades: int}]
    # OOS trades and metrics
    oos_trades: pd.DataFrame  # columns: gross_pnl, net_pnl, cost_pts, etc.
    oos_metrics: Dict[str, Any]
    n_train_bars: int
    n_test_bars: int
    elapsed_seconds: float


@dataclass
class WalkForwardResult:
    """Complete results from walk-forward optimization."""
    strategy_name: str
    folds: List[FoldResult]
    # Combined OOS equity
    combined_oos_trades: pd.DataFrame
    combined_oos_pnl: np.ndarray
    # Aggregate metrics (on combined OOS)
    aggregate_metrics: Dict[str, Any]
    aggregate_report: Dict[str, Any]
    # Go/no-go
    go_nogo: Dict[str, Any]
    # Meta
    total_param_combos: int
    total_elapsed_seconds: float
    config: Dict[str, Any]

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "=" * 70,
            f"  WALK-FORWARD RESULT: {self.strategy_name}",
            "=" * 70,
            f"  Folds: {len(self.folds)}",
            f"  Total OOS trades: {len(self.combined_oos_pnl)}",
            f"  Total param combos tested: {self.total_param_combos}",
            f"  Runtime: {self.total_elapsed_seconds:.1f}s",
            "",
        ]

        # Per-fold summary
        lines.append("  Per-fold results:")
        lines.append(f"  {'Fold':>4s} {'Train':>20s} {'Test':>20s}"
                     f" {'Params':>30s} {'OOS_n':>6s} {'OOS_mean':>10s} {'OOS_PF':>8s}")
        lines.append(f"  {'-'*100}")

        for f in self.folds:
            params_str = str(f.best_params)
            if len(params_str) > 28:
                params_str = params_str[:28] + ".."
            n_oos = len(f.oos_trades)
            oos_mean = f.oos_metrics.get("mean_pnl", 0)
            oos_pf = f.oos_metrics.get("profit_factor", 0)
            lines.append(
                f"  {f.fold_id:4d} {str(f.train_start):>10s}-{str(f.train_end):>8s}"
                f" {str(f.test_start):>10s}-{str(f.test_end):>8s}"
                f" {params_str:>30s} {n_oos:6d} {oos_mean:+10.4f} {oos_pf:8.3f}"
            )

        # Param stability
        lines.append("")
        lines.append("  Parameter stability across folds:")
        all_params = [f.best_params for f in self.folds]
        if all_params:
            param_keys = list(all_params[0].keys())
            for key in param_keys:
                values = [p.get(key) for p in all_params]
                unique = set(str(v) for v in values)
                lines.append(f"    {key}: {[p.get(key) for p in all_params]}"
                             f"  ({len(unique)} unique)")

        # Aggregate report
        lines.append("")
        lines.append(self.aggregate_report.get("summary", ""))

        # Go/no-go
        lines.append("")
        verdict = self.go_nogo["verdict"]
        lines.append(f"  GO/NO-GO VERDICT: {verdict}")
        if self.go_nogo["failures"]:
            lines.append(f"  Failed: {', '.join(self.go_nogo['failures'])}")

        return "\n".join(lines)

    def param_stability_score(self) -> float:
        """
        Score 0-1 measuring parameter stability across folds.
        1.0 = same params chosen every fold (very stable).
        0.0 = completely different params each fold (overfit risk).
        """
        if len(self.folds) < 2:
            return 1.0

        all_params = [f.best_params for f in self.folds]
        param_keys = list(all_params[0].keys())

        stability_scores = []
        for key in param_keys:
            values = [p.get(key) for p in all_params]
            # Mode frequency
            from collections import Counter
            counts = Counter(str(v) for v in values)
            mode_freq = counts.most_common(1)[0][1] / len(values)
            stability_scores.append(mode_freq)

        return np.mean(stability_scores)


# ══════════════════════════════════════════════════════════════════
# OBJECTIVE FUNCTIONS
# ══════════════════════════════════════════════════════════════════

def _objective_sharpe(pnl: np.ndarray) -> float:
    if len(pnl) < 5 or np.std(pnl) == 0:
        return -999.0
    return np.mean(pnl) / np.std(pnl, ddof=1)

def _objective_calmar(pnl: np.ndarray) -> float:
    if len(pnl) < 5:
        return -999.0
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    max_dd = abs(np.min(equity - peak))
    if max_dd == 0:
        return np.mean(pnl) * 252 if np.mean(pnl) > 0 else -999.0
    return (np.mean(pnl) * 252) / max_dd

def _objective_profit_factor(pnl: np.ndarray) -> float:
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    if len(losses) == 0:
        return 999.0 if len(wins) > 0 else -999.0
    gross_loss = abs(np.sum(losses))
    if gross_loss == 0:
        return 999.0
    return np.sum(wins) / gross_loss

def _objective_expectancy(pnl: np.ndarray) -> float:
    if len(pnl) < 5:
        return -999.0
    return np.mean(pnl)

OBJECTIVES = {
    "sharpe": _objective_sharpe,
    "calmar": _objective_calmar,
    "profit_factor": _objective_profit_factor,
    "expectancy": _objective_expectancy,
}


# ══════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE
# ══════════════════════════════════════════════════════════════════

class WalkForwardEngine:
    """
    Rolling or anchored walk-forward optimization engine.

    For each fold:
      1. Optimize strategy params on training data.
      2. Test best params on subsequent OOS data.
      3. Apply costs to OOS trades.
      4. Store everything for audit trail.
    """

    def __init__(
        self,
        train_days: int = 756,           # ~3 years of trading days
        test_days: int = 252,            # ~1 year
        objective: str = "sharpe",
        cost_model: Optional[TransactionCost] = None,
        anchored: bool = False,          # True = expanding window
        min_trades_per_fold: int = 10,   # skip folds with too few trades
        trades_per_year: float = 252.0,  # for metric annualization
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.objective_name = objective
        self.objective_fn = OBJECTIVES[objective]
        self.cost_model = cost_model or TransactionCost()
        self.anchored = anchored
        self.min_trades_per_fold = min_trades_per_fold
        self.trades_per_year = trades_per_year

    def run(
        self,
        data: pd.DataFrame,
        strategy_class: Type[BaseStrategy],
        param_grid: Optional[Dict[str, List]] = None,
        max_bars_per_trade: int = 78,
    ) -> WalkForwardResult:
        """
        Execute walk-forward optimization.

        Args:
            data: Full dataset with features. Must have session_date column.
            strategy_class: Strategy class (not instance) to test.
            param_grid: Override strategy's default param_grid.
            max_bars_per_trade: Max bars to hold per trade.

        Returns:
            WalkForwardResult with all folds, combined OOS, and aggregate metrics.
        """
        t_start = time.time()

        # Get unique trading days
        if S.SESSION_DATE in data.columns:
            unique_days = sorted(data[S.SESSION_DATE].unique())
        elif isinstance(data.index, pd.DatetimeIndex):
            unique_days = sorted(data.index.date)
            unique_days = sorted(set(unique_days))
        else:
            raise ValueError("Data must have session_date column or DatetimeIndex")

        n_days = len(unique_days)
        logger.info(f"WFO: {n_days} trading days, "
                     f"train={self.train_days}d, test={self.test_days}d, "
                     f"{'anchored' if self.anchored else 'rolling'}")

        # Get param grid
        temp_strategy = strategy_class()
        grid = param_grid or temp_strategy.param_grid
        param_combos = list(_grid_to_combos(grid))
        n_combos = len(param_combos)
        logger.info(f"  Strategy: {temp_strategy.name}, {n_combos} param combos")

        # Generate fold boundaries
        folds_spec = self._generate_folds(unique_days)
        logger.info(f"  Folds: {len(folds_spec)}")

        # Run each fold
        folds = []
        total_combos_tested = 0

        for fold_id, (train_start, train_end, test_start, test_end) in enumerate(folds_spec):
            fold_t_start = time.time()

            # Slice data by session_date
            train_data = self._slice_by_dates(data, train_start, train_end)
            test_data = self._slice_by_dates(data, test_start, test_end)

            if len(train_data) == 0 or len(test_data) == 0:
                logger.warning(f"  Fold {fold_id}: empty train or test data, skipping")
                continue

            # ── Grid search on training data ───────────────────
            best_params = param_combos[0] if param_combos else {}
            best_score = -999.0
            param_scores = []

            for combo in param_combos:
                strategy = strategy_class(params=combo)
                signals = strategy.generate_signals(train_data)
                trades = strategy.signals_to_trades(train_data, signals, max_bars_per_trade)
                trades_df = strategy.trades_to_dataframe(trades)

                if len(trades_df) == 0:
                    param_scores.append({"params": combo, "score": -999.0, "n_trades": 0})
                    continue

                # Apply costs to get net P&L
                trades_df = self.cost_model.apply_to_trades(trades_df)
                net_pnl = trades_df["net_pnl"].values
                score = self.objective_fn(net_pnl)

                param_scores.append({
                    "params": combo,
                    "score": score,
                    "n_trades": len(trades_df),
                })

                if score > best_score and len(trades_df) >= self.min_trades_per_fold:
                    best_score = score
                    best_params = combo

            total_combos_tested += len(param_combos)

            # ── Test best params on OOS data ───────────────────
            best_strategy = strategy_class(params=best_params)
            oos_signals = best_strategy.generate_signals(test_data)
            oos_trades = best_strategy.signals_to_trades(test_data, oos_signals, max_bars_per_trade)
            oos_trades_df = best_strategy.trades_to_dataframe(oos_trades)

            if len(oos_trades_df) > 0:
                oos_trades_df = self.cost_model.apply_to_trades(oos_trades_df)
                oos_pnl = oos_trades_df["net_pnl"].values
                oos_metrics = standard_metrics(oos_pnl, self.trades_per_year)
            else:
                oos_pnl = np.array([])
                oos_metrics = {"mean_pnl": 0, "profit_factor": 0, "n_trades": 0}

            fold_elapsed = time.time() - fold_t_start

            fold_result = FoldResult(
                fold_id=fold_id,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                best_params=best_params,
                best_objective_score=best_score,
                param_scores=param_scores,
                oos_trades=oos_trades_df,
                oos_metrics=oos_metrics,
                n_train_bars=len(train_data),
                n_test_bars=len(test_data),
                elapsed_seconds=fold_elapsed,
            )
            folds.append(fold_result)

            n_oos = len(oos_trades_df)
            oos_mean = oos_metrics.get("mean_pnl", 0)
            logger.info(f"  Fold {fold_id}: params={best_params}, "
                        f"train_score={best_score:.4f}, "
                        f"OOS n={n_oos}, OOS mean={oos_mean:+.4f}")

        # ── Combine all OOS results ────────────────────────────
        if folds:
            oos_list = [f.oos_trades for f in folds if len(f.oos_trades) > 0]
            if oos_list:
                combined_trades = pd.concat(oos_list, ignore_index=True)
                combined_pnl = combined_trades["net_pnl"].values if len(combined_trades) > 0 else np.array([])
            else:
                combined_trades = pd.DataFrame()
                combined_pnl = np.array([])
        else:
            combined_trades = pd.DataFrame()
            combined_pnl = np.array([])

        # ── Aggregate metrics with DSR ─────────────────────────
        aggregate_report = performance_report(
            pnl=combined_pnl,
            trades_per_year=self.trades_per_year,
            n_trials=max(total_combos_tested, 1),
            instrument_point_value=self.cost_model.instrument.point_value,
        )

        aggregate_metrics = aggregate_report["standard"]

        # Go/no-go evaluation
        go_nogo = evaluate_go_nogo(aggregate_report)

        total_elapsed = time.time() - t_start

        result = WalkForwardResult(
            strategy_name=temp_strategy.name,
            folds=folds,
            combined_oos_trades=combined_trades,
            combined_oos_pnl=combined_pnl,
            aggregate_metrics=aggregate_metrics,
            aggregate_report=aggregate_report,
            go_nogo=go_nogo,
            total_param_combos=total_combos_tested,
            total_elapsed_seconds=total_elapsed,
            config={
                "train_days": self.train_days,
                "test_days": self.test_days,
                "objective": self.objective_name,
                "anchored": self.anchored,
                "cost_model": repr(self.cost_model),
            },
        )

        return result

    # ── Fold generation ────────────────────────────────────────
    def _generate_folds(
        self, unique_days: List,
    ) -> List[Tuple[Any, Any, Any, Any]]:
        """
        Generate (train_start, train_end, test_start, test_end) tuples.
        """
        n = len(unique_days)
        folds = []

        if self.anchored:
            # Anchored: training always starts at day 0, grows
            start_idx = 0
            test_start_idx = self.train_days
            while test_start_idx + self.test_days <= n:
                train_start = unique_days[start_idx]
                train_end = unique_days[test_start_idx - 1]
                test_start = unique_days[test_start_idx]
                test_end_idx = min(test_start_idx + self.test_days - 1, n - 1)
                test_end = unique_days[test_end_idx]

                folds.append((train_start, train_end, test_start, test_end))
                test_start_idx += self.test_days
        else:
            # Rolling: fixed-size training window slides forward
            fold_start = 0
            while fold_start + self.train_days + self.test_days <= n:
                train_start = unique_days[fold_start]
                train_end = unique_days[fold_start + self.train_days - 1]
                test_start = unique_days[fold_start + self.train_days]
                test_end_idx = min(fold_start + self.train_days + self.test_days - 1, n - 1)
                test_end = unique_days[test_end_idx]

                folds.append((train_start, train_end, test_start, test_end))
                fold_start += self.test_days

        return folds

    def _slice_by_dates(self, data: pd.DataFrame, start, end) -> pd.DataFrame:
        """Slice DataFrame by session_date range."""
        if S.SESSION_DATE in data.columns:
            mask = (data[S.SESSION_DATE] >= start) & (data[S.SESSION_DATE] <= end)
            return data[mask].copy()
        elif isinstance(data.index, pd.DatetimeIndex):
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)
            return data[start_ts:end_ts].copy()
        else:
            raise ValueError("Cannot slice: no session_date or DatetimeIndex")


# ══════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════

def _grid_to_combos(grid: Dict[str, List]) -> List[Dict[str, Any]]:
    """Convert parameter grid to list of all combinations."""
    if not grid:
        return [{}]

    keys = list(grid.keys())
    values = list(grid.values())

    combos = []
    for combo in itertools.product(*values):
        combos.append(dict(zip(keys, combo)))

    return combos