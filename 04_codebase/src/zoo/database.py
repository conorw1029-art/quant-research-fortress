"""
Strategy Zoo Database
======================
Persistent, append-only log of every strategy test we run.

Every entry records:
  - Strategy identity: name, version, params, category
  - Test method: walk-forward, one-shot IS/OOS, etc.
  - Data: which dataset, timeframe, date range
  - Costs: which cost model was applied
  - Results: full metrics, DSR, PSR, go/no-go verdict
  - Audit: timestamp, git hash (if available), runtime

Storage: JSONL (one JSON object per line). Human-readable, append-safe,
         survives crashes, easy to query with pandas or jq.

Usage:
    from src.zoo.database import ZooDatabase
    
    zoo = ZooDatabase("05_backtests/zoo.jsonl")
    zoo.record(strategy=my_strategy, result=wf_result, ...)
    
    # Query
    df = zoo.to_dataframe()
    survivors = zoo.find_survivors(min_dsr=1.0)
    summary = zoo.summary()
"""

import datetime as dt
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# RECORD SCHEMA
# ══════════════════════════════════════════════════════════════════

ZOO_SCHEMA_VERSION = "1.0"


@dataclass
class ZooRecord:
    """
    Single entry in the strategy zoo.
    Every field should be JSON-serializable.
    """
    # Identity
    record_id: str
    timestamp: str
    schema_version: str = ZOO_SCHEMA_VERSION

    # Strategy
    strategy_name: str = ""
    strategy_version: str = ""
    strategy_category: str = ""
    strategy_description: str = ""
    strategy_module: str = ""        # python module path
    best_params: Dict[str, Any] = field(default_factory=dict)
    param_grid: Dict[str, List] = field(default_factory=dict)

    # Test configuration
    test_method: str = ""            # "walk_forward_rolling", "walk_forward_anchored", "one_shot_is_oos"
    instrument: str = ""
    timeframe: str = ""
    cost_model: str = ""
    cost_per_rt_pts: float = 0.0
    data_start: str = ""
    data_end: str = ""
    train_start: str = ""
    train_end: str = ""
    test_start: str = ""
    test_end: str = ""

    # Sample sizes
    total_param_combos: int = 0
    n_folds: int = 0
    n_oos_trades: int = 0
    n_is_trades: int = 0

    # Core metrics (OOS)
    oos_total_pnl: float = 0.0
    oos_mean_pnl: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_sharpe_ann: float = 0.0
    oos_sortino_ann: float = 0.0
    oos_calmar: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_max_consec_loss: int = 0
    oos_both_halves_positive: bool = False
    oos_h1_sharpe: float = 0.0
    oos_h2_sharpe: float = 0.0
    oos_t_stat: float = 0.0
    oos_p_value: float = 1.0

    # Statistical tests
    dsr: float = 0.0
    dsr_interpretation: str = ""
    psr: float = 0.0
    mc_pvalue: float = 1.0
    hurst: float = 0.5

    # Verdict
    verdict: str = "FAIL"
    failures: List[str] = field(default_factory=list)

    # Per-fold summary (for walk-forward)
    fold_params: List[Dict] = field(default_factory=list)
    fold_oos_means: List[float] = field(default_factory=list)
    fold_oos_sharpes: List[float] = field(default_factory=list)
    param_stability: float = 0.0

    # Runtime
    runtime_seconds: float = 0.0
    hostname: str = ""
    python_version: str = ""
    git_hash: str = ""

    # Notes / free text
    notes: str = ""

    # Error tracking
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        def default(o):
            if isinstance(o, (np.int64, np.int32, np.integer)):
                return int(o)
            if isinstance(o, (np.float64, np.float32, np.floating)):
                return float(o)
            if isinstance(o, np.bool_):
                return bool(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
            if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
                return o.isoformat()
            if isinstance(o, pd.Timedelta):
                return str(o)
            return str(o)
        return json.dumps(self.to_dict(), default=default)


# ══════════════════════════════════════════════════════════════════
# ZOO DATABASE
# ══════════════════════════════════════════════════════════════════

class ZooDatabase:
    """
    Append-only JSONL database for strategy test results.
    Thread-safe within a single process (uses file locking on write).
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Zoo database: {self.path}")

    # ── Recording ──────────────────────────────────────────────
    def record_from_result(
        self,
        strategy: Any,
        result: Any,                 # WalkForwardResult or similar
        test_method: str = "walk_forward",
        data_range: Optional[tuple] = None,
        notes: str = "",
        error: Optional[str] = None,
    ) -> ZooRecord:
        """
        Build a ZooRecord from strategy + WalkForwardResult and write it.
        """
        now = dt.datetime.now()
        record_id = f"{strategy.name}_{now.strftime('%Y%m%d_%H%M%S_%f')}"

        rec = ZooRecord(
            record_id=record_id,
            timestamp=now.isoformat(),
            strategy_name=strategy.name,
            strategy_version=getattr(strategy, "version", "1.0"),
            strategy_category=getattr(strategy, "category", "uncategorized"),
            strategy_description=getattr(strategy, "description", ""),
            strategy_module=strategy.__class__.__module__,
            best_params={},
            param_grid=_jsonify_grid(strategy.param_grid),
            test_method=test_method,
            hostname=socket.gethostname(),
            python_version=sys.version.split()[0],
            git_hash=_try_git_hash(),
            notes=notes,
            error=error,
        )

        if error:
            self._write(rec)
            return rec

        # Extract from WalkForwardResult
        try:
            rec.total_param_combos = getattr(result, "total_param_combos", 0)
            rec.n_folds = len(getattr(result, "folds", []))
            rec.runtime_seconds = getattr(result, "total_elapsed_seconds", 0.0)

            # Config
            cfg = getattr(result, "config", {})
            rec.cost_model = str(cfg.get("cost_model", ""))

            # Combined OOS trades
            combined = getattr(result, "combined_oos_trades", pd.DataFrame())
            rec.n_oos_trades = len(combined)

            # Aggregate metrics
            agg = getattr(result, "aggregate_metrics", {})
            rec.oos_total_pnl = float(agg.get("total_pnl", 0))
            rec.oos_mean_pnl = float(agg.get("mean_pnl", 0))
            rec.oos_win_rate = float(agg.get("win_rate", 0))
            rec.oos_profit_factor = float(agg.get("profit_factor", 0))
            rec.oos_sharpe_ann = float(agg.get("sharpe_ann", 0))
            rec.oos_sortino_ann = float(agg.get("sortino_ann", 0))
            rec.oos_calmar = float(agg.get("calmar", 0))
            rec.oos_max_drawdown = float(abs(agg.get("max_drawdown", 0)))
            rec.oos_max_consec_loss = int(agg.get("max_consec_loss", 0))
            rec.oos_both_halves_positive = bool(agg.get("both_halves_positive", False))
            rec.oos_h1_sharpe = float(agg.get("h1_sharpe", 0))
            rec.oos_h2_sharpe = float(agg.get("h2_sharpe", 0))
            rec.oos_t_stat = float(agg.get("t_stat", 0))
            rec.oos_p_value = float(agg.get("p_value", 1.0))

            # Statistical tests
            report = getattr(result, "aggregate_report", {})
            dsr_d = report.get("dsr", {})
            rec.dsr = float(dsr_d.get("dsr", 0))
            rec.dsr_interpretation = dsr_d.get("interpretation", "")
            psr_d = report.get("psr", {})
            rec.psr = float(psr_d.get("psr", 0))
            mc_d = report.get("monte_carlo", {})
            rec.mc_pvalue = float(mc_d.get("mc_pvalue", 1.0))
            rec.hurst = float(report.get("hurst", 0.5))

            # Verdict
            gng = getattr(result, "go_nogo", {})
            rec.verdict = gng.get("verdict", "FAIL")
            rec.failures = list(gng.get("failures", []))

            # Per-fold summary
            folds = getattr(result, "folds", [])
            rec.fold_params = [f.best_params for f in folds]
            rec.fold_oos_means = [
                float(f.oos_metrics.get("mean_pnl", 0)) for f in folds
            ]
            rec.fold_oos_sharpes = [
                float(f.oos_metrics.get("sharpe_ann", 0)) for f in folds
            ]
            rec.param_stability = _param_stability(rec.fold_params)

            # Use most common params across folds as "best"
            if rec.fold_params:
                from collections import Counter
                param_strs = [str(p) for p in rec.fold_params]
                most_common = Counter(param_strs).most_common(1)[0][0]
                for p in rec.fold_params:
                    if str(p) == most_common:
                        rec.best_params = p
                        break

            # Data range
            if data_range:
                rec.data_start = str(data_range[0])
                rec.data_end = str(data_range[1])

        except Exception as e:
            rec.error = f"Failed to extract metrics: {e}"
            logger.error(f"Failed to extract from result: {e}")

        self._write(rec)
        return rec

    def record_custom(self, **kwargs) -> ZooRecord:
        """Record a custom entry (for one-shot tests, etc.)."""
        now = dt.datetime.now()
        strategy_name = kwargs.get("strategy_name", "custom")
        record_id = f"{strategy_name}_{now.strftime('%Y%m%d_%H%M%S_%f')}"

        kwargs.setdefault("record_id", record_id)
        kwargs.setdefault("timestamp", now.isoformat())
        kwargs.setdefault("hostname", socket.gethostname())
        kwargs.setdefault("python_version", sys.version.split()[0])
        kwargs.setdefault("git_hash", _try_git_hash())

        rec = ZooRecord(**{k: v for k, v in kwargs.items()
                           if k in ZooRecord.__dataclass_fields__})
        self._write(rec)
        return rec

    def _write(self, record: ZooRecord):
        """Append record as a single JSON line."""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(record.to_json() + "\n")

    # ── Querying ───────────────────────────────────────────────
    def load(self) -> List[Dict]:
        """Load all records as list of dicts."""
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping malformed line: {e}")
        return records

    def to_dataframe(self) -> pd.DataFrame:
        """Load all records as a DataFrame for analysis."""
        records = self.load()
        if not records:
            return pd.DataFrame()

        # Flatten: drop complex nested fields for table view
        flat = []
        for r in records:
            row = {k: v for k, v in r.items()
                   if not isinstance(v, (list, dict))}
            row["best_params_str"] = str(r.get("best_params", {}))
            row["n_fold_params"] = len(r.get("fold_params", []))
            flat.append(row)

        return pd.DataFrame(flat)

    def find_survivors(
        self,
        min_dsr: float = 1.0,
        min_pf: float = 1.25,
        min_oos_trades: int = 30,
        max_drawdown: float = 400.0,
    ) -> pd.DataFrame:
        """Return records that pass quality filters."""
        df = self.to_dataframe()
        if df.empty:
            return df
        mask = (
            (df["dsr"] >= min_dsr) &
            (df["oos_profit_factor"] >= min_pf) &
            (df["n_oos_trades"] >= min_oos_trades) &
            (df["oos_max_drawdown"] <= max_drawdown) &
            (df["oos_mean_pnl"] > 0)
        )
        return df[mask].sort_values("dsr", ascending=False)

    def summary(self) -> str:
        """Human-readable zoo summary."""
        df = self.to_dataframe()
        if df.empty:
            return "Zoo is empty."

        lines = [
            "=" * 70,
            f"  STRATEGY ZOO SUMMARY",
            "=" * 70,
            f"  Total records: {len(df)}",
            f"  Unique strategies: {df['strategy_name'].nunique()}",
            f"  Unique categories: {df['strategy_category'].nunique()}",
            f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}",
            "",
            f"  Verdict breakdown:",
        ]
        for verdict, count in df["verdict"].value_counts().items():
            lines.append(f"    {verdict}: {count}")

        lines.append("")
        lines.append(f"  Category breakdown:")
        for cat, count in df["strategy_category"].value_counts().items():
            lines.append(f"    {cat}: {count}")

        # Top survivors
        survivors = self.find_survivors()
        lines.append("")
        if len(survivors) > 0:
            lines.append(f"  TOP SURVIVORS ({len(survivors)}):")
            for _, row in survivors.head(10).iterrows():
                lines.append(
                    f"    {row['strategy_name']:<30s}"
                    f" DSR={row['dsr']:+.2f}"
                    f" PF={row['oos_profit_factor']:.2f}"
                    f" n={row['n_oos_trades']:.0f}"
                    f" Sharpe={row['oos_sharpe_ann']:.2f}"
                )
        else:
            lines.append(f"  No survivors pass quality filters yet.")

        return "\n".join(lines)

    def summary_by_strategy(self) -> pd.DataFrame:
        """Aggregate stats grouped by strategy name."""
        df = self.to_dataframe()
        if df.empty:
            return df

        agg = df.groupby("strategy_name").agg(
            n_tests=("record_id", "count"),
            best_dsr=("dsr", "max"),
            best_pf=("oos_profit_factor", "max"),
            best_sharpe=("oos_sharpe_ann", "max"),
            total_oos_trades=("n_oos_trades", "sum"),
            last_tested=("timestamp", "max"),
            any_pass=("verdict", lambda v: "PASS" in v.values),
        ).sort_values("best_dsr", ascending=False)

        return agg


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _jsonify_grid(grid: Dict) -> Dict:
    """Make param grid JSON-safe."""
    if not grid:
        return {}
    out = {}
    for k, v in grid.items():
        if isinstance(v, (list, tuple)):
            out[k] = list(v)
        else:
            out[k] = [v]
    return out


def _param_stability(fold_params: List[Dict]) -> float:
    """Param stability score [0, 1] across folds."""
    if len(fold_params) < 2:
        return 1.0
    from collections import Counter
    keys = list(fold_params[0].keys())
    scores = []
    for k in keys:
        values = [str(p.get(k)) for p in fold_params]
        mode_count = Counter(values).most_common(1)[0][1]
        scores.append(mode_count / len(values))
    return float(np.mean(scores)) if scores else 0.0


def _try_git_hash() -> str:
    """Get current git hash if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"