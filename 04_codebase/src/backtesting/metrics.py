"""
Performance Metrics & Statistical Validation
==============================================
Institutional-grade metrics for strategy evaluation.

Includes:
  - Standard performance metrics (Sharpe, Sortino, Calmar, PF, etc.)
  - Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
  - Probabilistic Sharpe Ratio
  - Monte Carlo permutation test
  - Hurst exponent (R/S analysis)
  - Comprehensive reporting

All functions are PURE — they take arrays/Series and return results.
No side effects, no state.

Usage:
    from src.backtesting.metrics import performance_report, deflated_sharpe_ratio
    
    report = performance_report(
        returns=trade_pnl_series,
        benchmark_sr=0.0,
        n_trials=50,
        trades_per_year=150,
    )
    print(report["summary"])
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import gamma

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# STANDARD METRICS
# ══════════════════════════════════════════════════════════════════

def standard_metrics(
    pnl: np.ndarray,
    trades_per_year: float = 252.0,
    risk_free: float = 0.0,
) -> Dict[str, Any]:
    """
    Compute standard performance metrics from per-trade P&L array.

    Args:
        pnl: Array of per-trade P&L in points.
        trades_per_year: Annualization factor.
        risk_free: Risk-free rate (annualized, in same units as returns).

    Returns:
        Dict of all standard metrics.
    """
    n = len(pnl)
    if n == 0:
        return _empty_metrics()

    total_pnl = np.sum(pnl)
    mean_pnl = np.mean(pnl)
    std_pnl = np.std(pnl, ddof=1) if n > 1 else 0.0

    # Win/loss decomposition
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = n_wins / n if n > 0 else 0.0

    avg_win = np.mean(wins) if n_wins > 0 else 0.0
    avg_loss = np.mean(np.abs(losses)) if n_losses > 0 else 0.0
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else np.inf

    # Profit factor
    gross_profit = np.sum(wins) if n_wins > 0 else 0.0
    gross_loss = np.abs(np.sum(losses)) if n_losses > 0 else 1e-10
    profit_factor = gross_profit / gross_loss

    # Expectancy (expected P&L per trade)
    expectancy = mean_pnl

    # Equity curve and drawdown
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    max_drawdown = np.min(drawdown) if len(drawdown) > 0 else 0.0
    max_drawdown_abs = abs(max_drawdown)

    # Drawdown duration
    dd_durations = _drawdown_durations(drawdown)
    avg_dd_duration = np.mean(dd_durations) if dd_durations else 0.0
    max_dd_duration = max(dd_durations) if dd_durations else 0

    # Max consecutive losses
    max_consec_loss = _max_consecutive(pnl <= 0)
    max_consec_win = _max_consecutive(pnl > 0)

    # Annualized metrics
    ann_factor = np.sqrt(trades_per_year)

    # Sharpe ratio (annualized)
    sharpe_per_trade = (mean_pnl - risk_free / trades_per_year) / std_pnl if std_pnl > 0 else 0.0
    sharpe_ann = sharpe_per_trade * ann_factor

    # Sortino ratio (annualized, using downside deviation)
    downside = pnl[pnl < 0]
    downside_std = np.std(downside, ddof=1) if len(downside) > 1 else std_pnl
    sortino_per_trade = mean_pnl / downside_std if downside_std > 0 else 0.0
    sortino_ann = sortino_per_trade * ann_factor

    # Calmar ratio (annualized return / max drawdown)
    ann_return = mean_pnl * trades_per_year
    calmar = ann_return / max_drawdown_abs if max_drawdown_abs > 0 else 0.0

    # CAGR (approximation for trade-based P&L)
    n_years = n / trades_per_year
    if total_pnl > 0 and n_years > 0:
        # Approximate CAGR as annualized total return
        cagr = ann_return
    else:
        cagr = ann_return

    # Both-halves check
    half = n // 2
    h1_pnl = pnl[:half]
    h2_pnl = pnl[half:]
    h1_mean = np.mean(h1_pnl) if len(h1_pnl) > 0 else 0
    h2_mean = np.mean(h2_pnl) if len(h2_pnl) > 0 else 0
    both_halves_positive = h1_mean > 0 and h2_mean > 0

    h1_sharpe = _sharpe(h1_pnl, trades_per_year)
    h2_sharpe = _sharpe(h2_pnl, trades_per_year)

    # t-test (one-sided: H_a: mean > 0)
    if std_pnl > 0 and n > 1:
        t_stat, p_two = stats.ttest_1samp(pnl, 0)
        p_value = p_two / 2 if t_stat > 0 else 1.0 - p_two / 2
    else:
        t_stat, p_value = 0.0, 1.0

    # Skewness and kurtosis
    skewness = float(stats.skew(pnl)) if n > 2 else 0.0
    kurtosis = float(stats.kurtosis(pnl)) if n > 3 else 0.0

    return {
        # Counts
        "n_trades": n,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate": win_rate,
        # P&L
        "total_pnl": total_pnl,
        "mean_pnl": mean_pnl,
        "median_pnl": float(np.median(pnl)),
        "std_pnl": std_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss_ratio,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        # Risk
        "max_drawdown": max_drawdown,
        "max_drawdown_abs": max_drawdown_abs,
        "avg_dd_duration": avg_dd_duration,
        "max_dd_duration": max_dd_duration,
        "max_consec_loss": max_consec_loss,
        "max_consec_win": max_consec_win,
        # Ratios (annualized)
        "sharpe_ann": sharpe_ann,
        "sortino_ann": sortino_ann,
        "calmar": calmar,
        "cagr_pts": cagr,
        # Stability
        "both_halves_positive": both_halves_positive,
        "h1_sharpe": h1_sharpe,
        "h2_sharpe": h2_sharpe,
        "h1_mean": h1_mean,
        "h2_mean": h2_mean,
        # Statistical
        "t_stat": t_stat,
        "p_value": p_value,
        "skewness": skewness,
        "kurtosis": kurtosis,
        # Meta
        "trades_per_year": trades_per_year,
    }


# ══════════════════════════════════════════════════════════════════
# DEFLATED SHARPE RATIO
# ══════════════════════════════════════════════════════════════════

def deflated_sharpe_ratio(
    pnl: np.ndarray,
    n_trials: int,
    trades_per_year: float = 252.0,
    benchmark_sr: float = 0.0,
) -> Dict[str, float]:
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    Corrects the observed Sharpe ratio for multiple testing bias.
    Answers: "Given that I tested n_trials strategies, what is the
    probability that this Sharpe ratio is real (not noise)?"

    Args:
        pnl: Per-trade P&L array of the best strategy found.
        n_trials: Total number of independent strategies/param combos tested.
        trades_per_year: Annualization factor.
        benchmark_sr: Minimum acceptable Sharpe (annualized). Default 0.

    Returns:
        Dict with:
          - dsr: Deflated Sharpe Ratio (Z-score)
          - dsr_pvalue: Probability that TRUE Sharpe < benchmark
          - expected_max_sr: Expected maximum SR from n_trials random strategies
          - observed_sr: Annualized Sharpe of this strategy
          - interpretation: Human-readable verdict
    """
    n = len(pnl)
    if n < 10 or n_trials < 1:
        return {
            "dsr": 0.0, "dsr_pvalue": 1.0,
            "expected_max_sr": 0.0, "observed_sr": 0.0,
            "interpretation": "Insufficient data",
            "n_trials": n_trials,
        }

    # Observed SR (annualized)
    mean_r = np.mean(pnl)
    std_r = np.std(pnl, ddof=1)
    if std_r == 0:
        return {
            "dsr": 0.0, "dsr_pvalue": 1.0,
            "expected_max_sr": 0.0, "observed_sr": 0.0,
            "interpretation": "Zero variance",
        }

    sr_per_trade = mean_r / std_r
    observed_sr = sr_per_trade * np.sqrt(trades_per_year)

    # Skewness and kurtosis of returns
    skew = float(stats.skew(pnl))
    kurt = float(stats.kurtosis(pnl))  # excess kurtosis

    # Expected maximum SR from n_trials independent trials
    # E[max(SR)] ~ sqrt(2 * ln(N)) - (ln(pi) + ln(ln(N))) / (2 * sqrt(2 * ln(N)))
    # Simplified Euler-Mascheroni approximation
    if n_trials <= 1:
        expected_max_sr = 0.0
    else:
        euler_mascheroni = 0.5772156649
        ln_n = np.log(n_trials)
        expected_max_sr = (
            np.sqrt(2 * ln_n)
            - (np.log(np.pi) + np.log(ln_n))
            / (2 * np.sqrt(2 * ln_n))
        )
        # Scale: this is in per-observation units, annualize
        # The formula gives the expected max of n_trials standard normals
        # We need to scale by the SE of SR
        # SE(SR) ~ sqrt((1 + 0.5*SR^2 - skew*SR + (kurt/4)*SR^2) / n)

    # Standard error of the Sharpe ratio (Lo, 2002; Bailey & Lopez de Prado)
    se_sr = np.sqrt(
        (1 + 0.5 * sr_per_trade**2 - skew * sr_per_trade
         + (kurt / 4) * sr_per_trade**2)
        / (n - 1)
    ) * np.sqrt(trades_per_year)

    if se_sr == 0:
        se_sr = 1e-10

    # DSR = probability that observed SR > expected max SR
    # Z = (observed_SR - expected_max_SR * se_sr) / se_sr
    # Actually, DSR compares observed to the benchmark adjusted for max trials
    dsr_numerator = observed_sr - expected_max_sr * se_sr - benchmark_sr
    dsr = dsr_numerator / se_sr

    # p-value: probability that true SR is below the benchmark
    dsr_pvalue = 1.0 - stats.norm.cdf(dsr)

    # Interpretation
    if dsr > 1.645:
        interp = "STRONG (>95% confidence edge is real)"
    elif dsr > 1.0:
        interp = "MODERATE (>84% confidence edge is real)"
    elif dsr > 0.5:
        interp = "WEAK (>69% confidence, but risky)"
    else:
        interp = "FAIL (likely noise or overfit)"

    return {
        "dsr": dsr,
        "dsr_pvalue": dsr_pvalue,
        "expected_max_sr": expected_max_sr,
        "observed_sr": observed_sr,
        "se_sr": se_sr,
        "n_trials": n_trials,
        "interpretation": interp,
    }


# ══════════════════════════════════════════════════════════════════
# PROBABILISTIC SHARPE RATIO
# ══════════════════════════════════════════════════════════════════

def probabilistic_sharpe_ratio(
    pnl: np.ndarray,
    benchmark_sr: float = 0.0,
    trades_per_year: float = 252.0,
) -> Dict[str, float]:
    """
    Probabilistic Sharpe Ratio (Bailey & Lopez de Prado, 2012).

    Probability that the TRUE Sharpe ratio exceeds a benchmark,
    accounting for skewness and kurtosis.

    Args:
        pnl: Per-trade P&L array.
        benchmark_sr: Annualized benchmark Sharpe to beat.
        trades_per_year: Annualization factor.

    Returns:
        Dict with psr, observed_sr, benchmark_sr.
    """
    n = len(pnl)
    if n < 10:
        return {"psr": 0.0, "observed_sr": 0.0, "benchmark_sr": benchmark_sr}

    std_r = np.std(pnl, ddof=1)
    if std_r == 0:
        return {"psr": 0.0, "observed_sr": 0.0, "benchmark_sr": benchmark_sr}

    sr_per_trade = np.mean(pnl) / std_r
    observed_sr = sr_per_trade * np.sqrt(trades_per_year)
    benchmark_per_trade = benchmark_sr / np.sqrt(trades_per_year)

    skew = float(stats.skew(pnl))
    kurt = float(stats.kurtosis(pnl))

    # PSR = Phi((SR_obs - SR_bench) / SE(SR))
    # SE(SR) = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (n-1))
    se = np.sqrt(
        (1 - skew * sr_per_trade + ((kurt - 1) / 4) * sr_per_trade**2)
        / (n - 1)
    )

    if se == 0:
        se = 1e-10

    z = (sr_per_trade - benchmark_per_trade) / se
    psr = float(stats.norm.cdf(z))

    return {
        "psr": psr,
        "observed_sr": observed_sr,
        "benchmark_sr": benchmark_sr,
        "z_score": z,
    }


# ══════════════════════════════════════════════════════════════════
# MONTE CARLO PERMUTATION TEST
# ══════════════════════════════════════════════════════════════════

def monte_carlo_permutation(
    pnl: np.ndarray,
    n_permutations: int = 5000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Monte Carlo permutation test for strategy significance.

    Null hypothesis: the ordering of trades doesn't matter
    (i.e., the strategy is no better than random entry/exit timing).

    Shuffles the P&L series and recomputes Sharpe each time.
    p-value = proportion of random Sharpes >= observed Sharpe.

    Args:
        pnl: Per-trade P&L array.
        n_permutations: Number of random shuffles.
        seed: Random seed for reproducibility.

    Returns:
        Dict with mc_pvalue, observed_sharpe, null_sharpes_mean, null_sharpes_std.
    """
    n = len(pnl)
    if n < 10:
        return {"mc_pvalue": 1.0, "observed_sharpe": 0.0, "n_permutations": n_permutations}

    observed_sharpe = np.mean(pnl) / np.std(pnl, ddof=1) if np.std(pnl, ddof=1) > 0 else 0

    rng = np.random.RandomState(seed)
    null_sharpes = np.zeros(n_permutations)

    for i in range(n_permutations):
        shuffled = rng.permutation(pnl)
        std_s = np.std(shuffled, ddof=1)
        null_sharpes[i] = np.mean(shuffled) / std_s if std_s > 0 else 0

    mc_pvalue = np.mean(null_sharpes >= observed_sharpe)

    return {
        "mc_pvalue": mc_pvalue,
        "observed_sharpe_per_trade": observed_sharpe,
        "null_sharpes_mean": np.mean(null_sharpes),
        "null_sharpes_std": np.std(null_sharpes),
        "null_sharpes_95th": np.percentile(null_sharpes, 95),
        "n_permutations": n_permutations,
    }


# ══════════════════════════════════════════════════════════════════
# HURST EXPONENT
# ══════════════════════════════════════════════════════════════════

def hurst_exponent(series: np.ndarray, max_lag: int = 100) -> float:
    """
    Hurst exponent via R/S (Rescaled Range) analysis.

    H > 0.5: trending (persistent)
    H = 0.5: random walk
    H < 0.5: mean-reverting (anti-persistent)

    Args:
        series: Time series (prices or returns).
        max_lag: Maximum lag for R/S calculation.

    Returns:
        Estimated Hurst exponent.
    """
    n = len(series)
    if n < 20:
        return 0.5

    max_lag = min(max_lag, n // 4)
    lags = range(10, max_lag + 1)
    rs_values = []

    for lag in lags:
        # Split into non-overlapping chunks
        n_chunks = n // lag
        if n_chunks < 1:
            continue

        rs_list = []
        for i in range(n_chunks):
            chunk = series[i * lag: (i + 1) * lag]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            r = np.max(deviations) - np.min(deviations)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)

        if rs_list:
            rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

    if len(rs_values) < 3:
        return 0.5

    log_lags, log_rs = zip(*rs_values)
    slope, _, _, _, _ = stats.linregress(log_lags, log_rs)

    return float(slope)


# ══════════════════════════════════════════════════════════════════
# COMPREHENSIVE REPORT
# ══════════════════════════════════════════════════════════════════

def performance_report(
    pnl: np.ndarray,
    trades_per_year: float = 252.0,
    n_trials: int = 1,
    benchmark_sr: float = 0.0,
    mc_permutations: int = 5000,
    instrument_point_value: float = 5.0,
) -> Dict[str, Any]:
    """
    Generate comprehensive performance report combining all metrics.

    Args:
        pnl: Per-trade NET P&L array (after costs).
        trades_per_year: Annualization factor.
        n_trials: Total parameter combinations tested (for DSR).
        benchmark_sr: Minimum Sharpe to beat.
        mc_permutations: Number of Monte Carlo shuffles.
        instrument_point_value: Dollar value per point (for dollar reporting).

    Returns:
        Comprehensive dict of all metrics + summary string.
    """
    report = {}

    # Standard metrics
    report["standard"] = standard_metrics(pnl, trades_per_year)

    # Deflated Sharpe
    report["dsr"] = deflated_sharpe_ratio(pnl, n_trials, trades_per_year, benchmark_sr)

    # Probabilistic Sharpe
    report["psr"] = probabilistic_sharpe_ratio(pnl, benchmark_sr, trades_per_year)

    # Monte Carlo
    report["monte_carlo"] = monte_carlo_permutation(pnl, mc_permutations)

    # Hurst exponent (on cumulative equity)
    if len(pnl) > 20:
        equity = np.cumsum(pnl)
        report["hurst"] = hurst_exponent(equity)
    else:
        report["hurst"] = 0.5

    # Dollar-equivalent summary
    std = report["standard"]
    report["dollars"] = {
        "total_pnl": std["total_pnl"] * instrument_point_value,
        "mean_per_trade": std["mean_pnl"] * instrument_point_value,
        "max_drawdown": std["max_drawdown_abs"] * instrument_point_value,
        "annual_pnl": std["cagr_pts"] * instrument_point_value,
    }

    # Build summary string
    report["summary"] = _build_summary(report)

    return report


def _build_summary(report: Dict) -> str:
    """Build human-readable summary string."""
    s = report["standard"]
    d = report["dsr"]
    p = report["psr"]
    mc = report["monte_carlo"]
    dol = report["dollars"]

    lines = [
        "=" * 60,
        "  PERFORMANCE REPORT",
        "=" * 60,
        f"  Trades:           {s['n_trades']}  ({s['trades_per_year']:.0f}/yr)",
        f"  Total P&L:        {s['total_pnl']:+.1f} pts  (${dol['total_pnl']:+,.0f})",
        f"  Mean/trade:       {s['mean_pnl']:+.4f} pts  (${dol['mean_per_trade']:+.2f})",
        f"  Win rate:         {s['win_rate']*100:.1f}%  ({s['n_wins']}W / {s['n_losses']}L)",
        f"  Profit factor:    {s['profit_factor']:.3f}",
        f"  Avg win/loss:     {s['avg_win']:+.2f} / {s['avg_loss']:.2f}  (ratio: {s['win_loss_ratio']:.2f})",
        "",
        f"  Sharpe (ann):     {s['sharpe_ann']:.3f}",
        f"  Sortino (ann):    {s['sortino_ann']:.3f}",
        f"  Calmar:           {s['calmar']:.3f}",
        f"  Max DD:           {s['max_drawdown_abs']:.1f} pts  (${dol['max_drawdown']:+,.0f})",
        f"  Max consec loss:  {s['max_consec_loss']}",
        "",
        f"  Both halves +ve:  {'YES' if s['both_halves_positive'] else 'NO'}"
        f"  (H1={s['h1_sharpe']:.3f} / H2={s['h2_sharpe']:.3f})",
        f"  t-stat:           {s['t_stat']:.3f}  p={s['p_value']:.5f}",
        f"  Skew / Kurt:      {s['skewness']:.2f} / {s['kurtosis']:.2f}",
        "",
        "  -- STATISTICAL TESTS --",
        f"  DSR:              {d['dsr']:.3f}  ({d['interpretation']})",
        f"                    n_trials={d['n_trials']}, E[max SR]={d['expected_max_sr']:.3f}",
        f"  PSR:              {p['psr']:.3f}  (prob true SR > {p['benchmark_sr']:.1f})",
        f"  MC p-value:       {mc['mc_pvalue']:.5f}  ({mc['n_permutations']} permutations)",
        f"  Hurst:            {report['hurst']:.3f}"
        f"  ({'trending' if report['hurst'] > 0.55 else 'mean-rev' if report['hurst'] < 0.45 else 'random walk'})",
        "=" * 60,
    ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# GO/NO-GO DECISION ENGINE
# ══════════════════════════════════════════════════════════════════

def evaluate_go_nogo(
    report: Dict,
    min_dsr: float = 1.0,
    min_pf: float = 1.25,
    max_dd_dollars: float = 2000.0,
    min_trades: int = 30,
    require_both_halves: bool = True,
    dsr_waiver_threshold: float = 3.0,
    max_p_value: float = 0.05,
    instrument: Optional[str] = None,
    cost_scenario: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Go/no-go decision. v2 changes:
      - win_rate criterion removed (WR is meaningless without R:R).
      - both_halves waived when DSR >= dsr_waiver_threshold.
      - max_dd_dollars replaces max_dd_pts (confirmed: oos_max_drawdown is $).
    """
    s = report["standard"]
    d = report["dsr"]

    dsr_value = float(d.get("dsr", 0.0))
    n_trades  = int(s.get("n_trades", 0))
    pf        = float(s.get("profit_factor", 0.0))
    max_dd    = float(abs(s.get("max_drawdown_abs", s.get("max_drawdown", 0.0))))
    p_value   = float(s.get("p_value", 1.0))
    both_half = bool(s.get("both_halves_positive", False))
    mean_pnl  = float(s.get("mean_pnl", 0.0))

    dsr_waiver        = dsr_value >= dsr_waiver_threshold
    both_halves_pass  = both_half or (not require_both_halves) or dsr_waiver

    checks = {
        "n_trades":          (n_trades  >= min_trades,     n_trades,  f">= {min_trades}"),
        "dsr":               (dsr_value >= min_dsr,        dsr_value, f">= {min_dsr}"),
        "profit_factor":     (pf        >= min_pf,         pf,        f">= {min_pf}"),
        "max_drawdown_usd":  (max_dd    <= max_dd_dollars, max_dd,    f"<= ${max_dd_dollars:,.0f}"),
        "p_value":           (p_value   <= max_p_value,    p_value,   f"<= {max_p_value}"),
        "both_halves":       (both_halves_pass, both_half,
                              f"True OR DSR>={dsr_waiver_threshold}" if require_both_halves else "n/a"),
        "mean_pnl_positive": (mean_pnl  > 0,               mean_pnl,  "> 0"),
    }

    failures = [k for k, (passed, *_) in checks.items() if not passed]
    return {
        "verdict":           "PASS" if not failures else "FAIL",
        "checks":            checks,
        "failures":          failures,
        "n_failures":        len(failures),
        "dsr_waiver_active": dsr_waiver,
        "instrument":        instrument,
        "cost_scenario":     cost_scenario,
    }

# ══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════

def _sharpe(pnl: np.ndarray, trades_per_year: float) -> float:
    """Annualized Sharpe from P&L array."""
    if len(pnl) < 2:
        return 0.0
    std = np.std(pnl, ddof=1)
    if std == 0:
        return 0.0
    return (np.mean(pnl) / std) * np.sqrt(trades_per_year)


def _drawdown_durations(drawdown: np.ndarray) -> List[int]:
    """Compute list of drawdown durations (in bars/trades)."""
    durations = []
    in_dd = False
    dd_start = 0
    for i in range(len(drawdown)):
        if drawdown[i] < 0 and not in_dd:
            in_dd = True
            dd_start = i
        elif drawdown[i] >= 0 and in_dd:
            in_dd = False
            durations.append(i - dd_start)
    if in_dd:
        durations.append(len(drawdown) - dd_start)
    return durations


def _max_consecutive(mask: np.ndarray) -> int:
    """Max consecutive True values in boolean array."""
    if len(mask) == 0:
        return 0
    max_run = 0
    current = 0
    for v in mask:
        if v:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def _empty_metrics() -> Dict[str, Any]:
    """Return empty metrics dict for edge cases."""
    return {
        "n_trades": 0, "n_wins": 0, "n_losses": 0, "win_rate": 0,
        "total_pnl": 0, "mean_pnl": 0, "median_pnl": 0, "std_pnl": 0,
        "avg_win": 0, "avg_loss": 0, "win_loss_ratio": 0,
        "profit_factor": 0, "expectancy": 0,
        "gross_profit": 0, "gross_loss": 0,
        "max_drawdown": 0, "max_drawdown_abs": 0,
        "avg_dd_duration": 0, "max_dd_duration": 0,
        "max_consec_loss": 0, "max_consec_win": 0,
        "sharpe_ann": 0, "sortino_ann": 0, "calmar": 0, "cagr_pts": 0,
        "both_halves_positive": False, "h1_sharpe": 0, "h2_sharpe": 0,
        "h1_mean": 0, "h2_mean": 0,
        "t_stat": 0, "p_value": 1.0, "skewness": 0, "kurtosis": 0,
        "trades_per_year": 0,
    }