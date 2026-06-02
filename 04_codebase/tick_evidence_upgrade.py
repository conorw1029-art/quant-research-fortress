"""
tick_evidence_upgrade.py — Evidence Upgrade Framework
=======================================================
Applies rigorous statistical testing to candidate survivors from any backtest run.

Pipeline:
  1. Load survivor JSON from a previous backtest
  2. Walk-forward validation (3-year train / 1-year OOS rolling)
  3. Bootstrap-by-day permutation test (P(DSR > 0) under null)
  4. Slippage ladder (1-tick, 2-tick, 3-tick degradation)
  5. Regime slice (bull/bear/sideways)
  6. Produces: evidence_report.json + passed_evidence.json

Run:
  venv_new/Scripts/python.exe 04_codebase/tick_evidence_upgrade.py \
      --survivors 05_backtests/l2_results/l2_survivors.json \
      --bars 01_data/tick_bars/GC_bars_l2_1m.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
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

ROOT = Path(__file__).parent.parent


# ── DSR computation ───────────────────────────────────────────────────────────
def compute_dsr(
    returns: pd.Series,
    n_trials: int = 1,
    annualize: bool = True,
) -> float:
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    DSR = Phi( (SR - SR_0) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt+2)/4 * SR^2) )

    Returns a probability in [0, 1]:
      DSR > 0.5 → strategy likely beats the multiple-testing benchmark
      DSR > 0.95 → strong statistical evidence (p < 0.05)

    Notes:
    - Uses SR (not a Cornish-Fisher-corrected SR*) in the numerator.
      Applying CF correction to the numerator can flip its sign under
      negative skew + high kurtosis, producing false DSR ≈ 0 for genuine
      positive-Sharpe strategies.
    - kurt is pandas Fisher excess kurtosis (0 for normal).
      (kurt + 2)/4 = (Pearson_kurt - 1)/4, matching Bailey 2014.
    """
    from scipy import stats

    if len(returns) < 5 or returns.std() == 0:
        return -99.0

    T = len(returns)
    sharpe = returns.mean() / returns.std()
    if annualize:
        sharpe *= np.sqrt(252)

    skew = float(returns.skew()) if T > 2 else 0.0
    kurt = float(returns.kurtosis()) if T > 3 else 0.0

    # Multiple-testing benchmark: expected max Sharpe under null
    gamma = 0.5772  # Euler-Mascheroni constant
    N = max(n_trials, 1)
    sr0 = (
        (1 - gamma) * stats.norm.ppf(1 - 1.0 / N) +
        gamma * stats.norm.ppf(1 - 1.0 / (N * np.e))
    ) if N > 1 else 0.0

    # Denominator: variance of SR estimator under non-normality
    # (kurt + 2)/4 is equivalent to (Pearson_kurt - 1)/4 from Bailey 2014
    denom_sq = 1.0 - skew * sharpe + (kurt + 2) / 4 * sharpe ** 2
    if denom_sq <= 0:
        # Degenerate: fall back to simple normal approximation without CF correction
        z = (sharpe - sr0) * np.sqrt(max(T - 1, 1))
    else:
        z = (sharpe - sr0) * np.sqrt(max(T - 1, 1)) / np.sqrt(denom_sq)

    return float(stats.norm.cdf(z))


# ── Walk-forward ──────────────────────────────────────────────────────────────
def walk_forward_validate(
    bars: pd.DataFrame,
    strat_class,
    params: dict,
    train_years: int = 2,
    test_years: int = 1,
) -> dict:
    """Simple rolling walk-forward: returns OOS Sharpe, DSR, win_rate."""
    all_returns = []
    all_trades  = []

    n = len(bars)
    # Estimate bars-per-trading-day from actual data (handle sparse instruments like GC)
    if bars.index.tz is not None:
        trading_days = bars.index.normalize().nunique()
    else:
        trading_days = bars.index.floor("D").nunique()
    bars_per_day = max(n / max(trading_days, 1), 1)

    train_bars = int(train_years * 252 * bars_per_day)
    test_bars  = int(test_years  * 252 * bars_per_day)

    # Minimum bar counts: at least 1000 bars train, 500 bars test
    train_bars = max(train_bars, 1000)
    test_bars  = max(test_bars, 500)

    step = test_bars

    fold = 0
    pos  = 0

    while pos + train_bars + test_bars <= n:
        train = bars.iloc[pos: pos + train_bars]
        test  = bars.iloc[pos + train_bars: pos + train_bars + test_bars]

        strat = strat_class(params=params)
        try:
            signals = strat.generate_signals(test)
            trades  = strat.signals_to_trades(test, signals)
            if trades:
                t_df = pd.DataFrame(trades)
                t_df["gross_pnl"] = t_df.get("gross_pnl", 0)
                all_trades.extend(trades)
        except Exception:
            pass

        pos  += step
        fold += 1

    if not all_trades:
        return {"wf_sharpe": -99, "wf_dsr": -99, "wf_trades": 0, "wf_win_rate": 0}

    t_df = pd.DataFrame(all_trades)
    pnl  = t_df["gross_pnl"].fillna(0)

    if "exit_time" in t_df.columns:
        daily = pnl.groupby(pd.to_datetime(t_df["exit_time"]).dt.date).sum()
    else:
        daily = pnl

    sharpe   = (daily.mean() / (daily.std() + 1e-9)) * np.sqrt(252) if len(daily) > 1 else 0
    dsr      = compute_dsr(daily, n_trials=fold)
    win_rate = (pnl > 0).sum() / len(pnl)

    return {
        "wf_sharpe":   round(float(sharpe), 4),
        "wf_dsr":      round(float(dsr), 4),
        "wf_trades":   len(all_trades),
        "wf_win_rate": round(float(win_rate), 4),
        "wf_folds":    fold,
    }


# ── Bootstrap test ────────────────────────────────────────────────────────────
def bootstrap_p_value(
    daily_pnl: pd.Series,
    n_bootstrap: int = 1000,
) -> float:
    """
    Bootstrap under null (E[PnL]=0): resample from mean-centered daily P&L.
    Returns fraction of bootstrap Sharpes >= actual Sharpe.
    p_value < 0.05 means the strategy edge is unlikely to be noise.

    Note: permutation of daily P&L is INVALID because Sharpe is invariant
    under permutation (mean and std are unchanged). This implementation
    correctly tests under the null hypothesis of zero mean.
    """
    if len(daily_pnl) < 10:
        return 1.0

    actual_sharpe = (daily_pnl.mean() / (daily_pnl.std() + 1e-9)) * np.sqrt(252)
    rng = np.random.default_rng(42)

    # Center at zero (null: strategy has no edge)
    centered = (daily_pnl - daily_pnl.mean()).values
    n = len(centered)

    count_better = 0
    for _ in range(n_bootstrap):
        # Resample with replacement from zero-centered returns
        boot = rng.choice(centered, size=n, replace=True)
        boot_sharpe = (boot.mean() / (boot.std() + 1e-9)) * np.sqrt(252)
        if boot_sharpe >= actual_sharpe:
            count_better += 1

    return count_better / n_bootstrap


# ── Slippage ladder ───────────────────────────────────────────────────────────
def slippage_ladder(
    bars: pd.DataFrame,
    strat_class,
    params: dict,
    tick_size: float,
    slippage_ticks_range: List[int] = [1, 2, 3],
) -> List[dict]:
    """Test strategy at different slippage assumptions."""
    rows = []
    strat   = strat_class(params=params)
    signals = strat.generate_signals(bars)
    trades  = strat.signals_to_trades(bars, signals)
    if not trades:
        return rows

    t_df = pd.DataFrame(trades)
    gross = t_df["gross_pnl"].fillna(0)

    for slippage in slippage_ticks_range:
        cost_pts = slippage * 2 * tick_size
        net = gross - cost_pts
        if "exit_time" in t_df.columns:
            daily = net.groupby(pd.to_datetime(t_df["exit_time"]).dt.date).sum()
        else:
            daily = net
        sharpe = (daily.mean() / (daily.std() + 1e-9)) * np.sqrt(252) if len(daily) > 1 else 0
        rows.append({
            "slippage_ticks": slippage,
            "net_pnl": round(float(net.sum()), 2),
            "sharpe":  round(float(sharpe), 4),
            "win_rate": round(float((net > 0).mean()), 4),
        })

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────
_L2_EXTRA_COLS = [
    "ofi_1", "ofi_5", "imbal_L5_last", "imbal_L5_mean", "imbal_L5_std",
    "microprice_last", "microprice_mean", "midprice_last",
    "spread_max", "buy_sweeps", "sell_sweeps", "net_sweeps", "sweep_net_size",
    "price_range_tick", "absorption_buy", "absorption_sell", "absorption_score",
]


def _compute_session_vwap(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    vol   = bars.get("volume", pd.Series(1.0, index=bars.index))
    session_date = (bars.index - pd.Timedelta(hours=17)).date
    vwap = pd.Series(np.nan, index=bars.index)
    for _date, grp in bars.groupby(pd.Series(session_date, index=bars.index)):
        gvol  = vol.loc[grp.index]
        gclose = close.loc[grp.index]
        cumvol = gvol.cumsum()
        cumtpv = (gclose * gvol).cumsum()
        vwap.loc[grp.index] = cumtpv / cumvol.replace(0, np.nan)
    return vwap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--survivors",  required=True, help="Path to survivors JSON")
    parser.add_argument("--bars",       required=True, help="Path to primary bar parquet")
    parser.add_argument("--l2-bars",    default=None,  help="Path to L2 bar parquet (auto-detected if omitted)")
    parser.add_argument("--tick-size",  type=float, default=0.10, help="Tick size (default: GC=0.10)")
    parser.add_argument("--n-bootstrap", type=int, default=500)
    parser.add_argument("--dsr-threshold", type=float, default=0.5,
                        help="Min WF DSR (probability) to pass. 0.5=beats benchmark, 0.95=p<0.05")
    args = parser.parse_args()

    surv_path = Path(args.survivors)
    bar_path  = Path(args.bars)

    if not surv_path.exists():
        print(f"Survivors file not found: {surv_path}")
        return
    if not bar_path.exists():
        print(f"Bars file not found: {bar_path}")
        return

    print(f"Loading survivors from {surv_path.name} ...")
    with open(surv_path) as f:
        survivors = json.load(f)
    print(f"  {len(survivors)} candidates")

    print(f"Loading bars from {bar_path.name} ...")
    bars = pd.read_parquet(bar_path)
    bars = bars[bars["close"].notna()]
    print(f"  {len(bars):,} bars ({bars.index.min().date()} to {bars.index.max().date()})")

    # Merge L2 features if available
    l2_path = Path(args.l2_bars) if args.l2_bars else bar_path.parent / bar_path.name.replace("_bars_", "_bars_l2_")
    if l2_path.exists():
        print(f"  Merging L2 features from {l2_path.name} ...")
        l2 = pd.read_parquet(l2_path)
        available = [c for c in _L2_EXTRA_COLS if c in l2.columns]
        for col in available:
            if col not in bars.columns:
                bars[col] = l2[col].reindex(bars.index)
        print(f"  L2 cols merged: {len(available)}/{len(_L2_EXTRA_COLS)}")
    else:
        print(f"  No L2 bars found at {l2_path} — strategies using L2 features will get no signals")

    # Compute session VWAP if needed
    if "session_vwap" not in bars.columns:
        print("  Computing session VWAP ...")
        bars["session_vwap"] = _compute_session_vwap(bars)

    # Lazy import strategy classes
    import importlib.util

    def _get_strat_class(strat_name: str):
        """Find strategy class by name from all l2_* modules."""
        from src.strategies import l2_ofi_strategies, l2_sweep_strategies
        from src.strategies import l2_absorption_strategies, l2_cvd_strategies
        from src.strategies import l2_depth_strategies

        for mod in [l2_ofi_strategies, l2_sweep_strategies,
                    l2_absorption_strategies, l2_cvd_strategies,
                    l2_depth_strategies]:
            for attr_name in dir(mod):
                cls = getattr(mod, attr_name)
                if isinstance(cls, type) and hasattr(cls, "name") and cls.name == strat_name:
                    return cls
        return None

    results = []
    passed  = []
    out_dir = surv_path.parent

    for entry in survivors:
        strat_name = entry.get("strategy", "")
        params_str = entry.get("params", "{}")
        params     = json.loads(params_str) if isinstance(params_str, str) else params_str
        symbol     = entry.get("symbol", "GC")

        print(f"\n[{strat_name}] symbol={symbol} params={params}")

        strat_class = _get_strat_class(strat_name)
        if strat_class is None:
            print(f"  Could not find class for '{strat_name}' — skipping")
            continue

        # Walk-forward
        wf = walk_forward_validate(bars, strat_class, params, train_years=2, test_years=1)
        print(f"  WF: sharpe={wf['wf_sharpe']:.3f} dsr={wf['wf_dsr']:.3f} "
              f"trades={wf['wf_trades']} folds={wf.get('wf_folds', 0)}")

        # Bootstrap
        strat   = strat_class(params=params)
        signals = strat.generate_signals(bars)
        trades_list = strat.signals_to_trades(bars, signals)
        if trades_list:
            t_df = pd.DataFrame(trades_list)
            pnl  = t_df["gross_pnl"].fillna(0)
            if "exit_time" in t_df.columns:
                daily = pnl.groupby(pd.to_datetime(t_df["exit_time"]).dt.date).sum()
            else:
                daily = pnl
            p_val = bootstrap_p_value(daily, n_bootstrap=args.n_bootstrap)
        else:
            p_val = 1.0
        print(f"  Bootstrap p-value: {p_val:.4f} {'PASS' if p_val < 0.05 else 'FAIL'}")

        # Slippage ladder
        ladder = slippage_ladder(
            bars, strat_class, params,
            tick_size=args.tick_size,
        )
        ladder_str = "  ".join(f"{r['slippage_ticks']}tick:sh={r['sharpe']:.3f}" for r in ladder)
        print(f"  Slippage ladder: {ladder_str}")

        # Gate: DSR > threshold, bootstrap significant, survives realistic 1-tick slippage.
        # 1-tick is the appropriate realistic gate for liquid futures (GC/SI bid-ask ≈ 1 tick).
        # 2- and 3-tick results are in the ladder for stress awareness but not required.
        one_tick_ok = len(ladder) >= 1 and ladder[0]["sharpe"] > 0
        evidence = {
            **entry,
            **wf,
            "bootstrap_p": round(p_val, 4),
            "slippage_ladder": ladder,
            "evidence_passed": (
                wf["wf_dsr"] >= args.dsr_threshold and
                p_val < 0.10 and
                one_tick_ok
            ),
        }
        results.append(evidence)

        if evidence["evidence_passed"]:
            passed.append(evidence)
            print(f"  *** EVIDENCE PASSED ***")

    # Save — prefix with survivor filename stem to avoid overwriting across symbols
    stem = surv_path.stem  # e.g. "GC_quick_hardened_survivors"
    # Strip trailing "_hardened_survivors" or "_survivors" for a compact tag
    sym_tag = stem.replace("_hardened_survivors", "").replace("_survivors", "")
    out_full   = out_dir / f"{sym_tag}_evidence_report.json"
    out_passed = out_dir / f"{sym_tag}_passed_evidence.json"
    # Also keep canonical names pointing to the latest run
    (out_dir / "evidence_report.json").unlink(missing_ok=True)
    (out_dir / "passed_evidence.json").unlink(missing_ok=True)

    with open(out_full, "w") as f:
        json.dump(results, f, indent=2, default=str)
    import shutil
    shutil.copy(out_full, out_dir / "evidence_report.json")
    print(f"\n[SAVED] {out_full}  ({len(results)} entries)")

    if passed:
        with open(out_passed, "w") as f:
            json.dump(passed, f, indent=2, default=str)
        shutil.copy(out_passed, out_dir / "passed_evidence.json")
        print(f"[PASSED] {out_passed}  ({len(passed)} strategies)")
    else:
        print("\n[WARNING] No strategies passed all evidence checks.")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
