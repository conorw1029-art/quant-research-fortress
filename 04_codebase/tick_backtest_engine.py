"""
Tick Bar Backtesting Engine
===========================
Vectorized bar-level backtest with ATR stops, take profits, max hold,
and signal reversal exits. Returns a trades DataFrame and full metrics.

Contract specs (dollar P&L per 1-point price move):
  GC  = $100/point   (100 oz × $1/oz)
  SI  = $5000/point  (5000 oz × $1/oz)  — use price carefully
  ES  = $50/point
  NQ  = $20/point
"""

import numpy as np
import pandas as pd

# ── Contract specifications ──────────────────────────────────────────────────
SPECS = {
    "GC":  {"point_value": 100.0,  "tick_size": 0.10,  "commission": 3.0},
    "SI":  {"point_value": 5000.0, "tick_size": 0.005, "commission": 3.0},
    "ES":  {"point_value": 50.0,   "tick_size": 0.25,  "commission": 3.0},
    "NQ":  {"point_value": 20.0,   "tick_size": 0.25,  "commission": 3.0},
    "CL":  {"point_value": 1000.0, "tick_size": 0.01,  "commission": 3.0},
    "MCL": {"point_value": 100.0,  "tick_size": 0.01,  "commission": 2.5},
}


# ── ATR helper ───────────────────────────────────────────────────────────────
def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                window: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i]  - close[i - 1]))
    atr = np.zeros(n)
    atr[:window] = np.nan
    atr[window - 1] = np.mean(tr[:window])
    alpha = 1.0 / window
    for i in range(window, n):
        atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
    return atr


# ── Core backtest loop ───────────────────────────────────────────────────────
def run_backtest(
    bars: pd.DataFrame,
    signals: pd.Series,
    symbol: str,
    stop_atr_mult: float = 1.5,
    tp_atr_mult: float   = 3.0,
    max_hold_bars: int   = 50,
    atr_window: int      = 14,
) -> pd.DataFrame:
    """
    Run a single backtest given a signal series.

    signals: pd.Series aligned with bars.index
             +1 = go long, -1 = go short, 0 = flat
    Entries: at close of signal bar.
    Exits: stop/target/timeout/signal-flip, tested bar by bar.

    Returns DataFrame of trades with dollar_pnl column.
    """
    spec = SPECS[symbol]
    pv   = spec["point_value"]
    comm = spec["commission"]

    hi    = bars["high"].values
    lo    = bars["low"].values
    cl    = bars["close"].values
    sig   = signals.reindex(bars.index).fillna(0).astype(int).values
    n     = len(cl)

    atr = compute_atr(hi, lo, cl, atr_window)

    trades = []
    in_pos     = False
    direction  = 0
    entry_bar  = -1
    entry_px   = 0.0
    stop_px    = 0.0
    target_px  = 0.0

    for i in range(n):
        if not in_pos:
            if sig[i] != 0 and not np.isnan(atr[i]):
                direction  = int(sig[i])
                entry_bar  = i
                entry_px   = cl[i]
                a          = atr[i]
                stop_px    = entry_px - direction * stop_atr_mult * a
                target_px  = entry_px + direction * tp_atr_mult   * a
                in_pos     = True
            continue

        hold = i - entry_bar
        exit_px     = None
        exit_reason = None

        # Stop loss
        if direction == 1 and lo[i] <= stop_px:
            exit_px, exit_reason = stop_px, "stop"
        elif direction == -1 and hi[i] >= stop_px:
            exit_px, exit_reason = stop_px, "stop"

        # Take profit
        elif direction == 1 and hi[i] >= target_px:
            exit_px, exit_reason = target_px, "target"
        elif direction == -1 and lo[i] <= target_px:
            exit_px, exit_reason = target_px, "target"

        # Max hold
        elif hold >= max_hold_bars:
            exit_px, exit_reason = cl[i], "timeout"

        # Signal flip
        elif sig[i] != 0 and sig[i] != direction:
            exit_px, exit_reason = cl[i], "signal"

        if exit_px is not None:
            raw_pnl    = direction * (exit_px - entry_px) * pv
            dollar_pnl = raw_pnl - 2.0 * comm
            trades.append({
                "entry_bar":   entry_bar,
                "exit_bar":    i,
                "entry_time":  bars.index[entry_bar],
                "exit_time":   bars.index[i],
                "direction":   direction,
                "entry_px":    entry_px,
                "exit_px":     exit_px,
                "hold_bars":   hold,
                "exit_reason": exit_reason,
                "dollar_pnl":  dollar_pnl,
            })
            in_pos = False
            # Immediately re-enter if current bar has new signal
            if sig[i] != 0 and sig[i] != direction:
                direction  = int(sig[i])
                entry_bar  = i
                entry_px   = cl[i]
                a          = atr[i] if not np.isnan(atr[i]) else atr[i - 1]
                stop_px    = entry_px - direction * stop_atr_mult * a
                target_px  = entry_px + direction * tp_atr_mult   * a
                in_pos     = True

    return pd.DataFrame(trades)


# ── Metrics ──────────────────────────────────────────────────────────────────
def compute_metrics(trades: pd.DataFrame, n_params: int = 1) -> dict:
    if trades.empty or len(trades) < 5:
        return _empty_metrics()

    pnl = trades["dollar_pnl"].values
    n   = len(pnl)

    wins     = pnl[pnl > 0]
    losses   = pnl[pnl < 0]
    win_rate = len(wins) / n
    avg_win  = wins.mean()  if len(wins)  > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0
    payoff   = abs(avg_win / avg_loss) if avg_loss != 0 else np.nan

    cum       = np.cumsum(pnl)
    peak      = np.maximum.accumulate(cum)
    drawdown  = peak - cum
    max_dd    = drawdown.max()

    # Daily P&L (approximate: use entry_time date)
    if "entry_time" in trades.columns:
        daily = trades.set_index("entry_time")["dollar_pnl"].resample("D").sum()
        daily_std = daily.std()
        daily_mean = daily.mean()
    else:
        daily_std  = pnl.std()
        daily_mean = pnl.mean()

    sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std > 0 else 0.0

    # DSR — deflated Sharpe accounting for multiple trials
    # Sharpe* = (1 - γ_E) × E[maxSR] where E[maxSR] ≈ (1-γ_E)×Z((1-1/n_params))
    # Simplified: penalise by sqrt(log(n_params))
    dsr = sharpe / np.sqrt(np.log(max(n_params, 2)))

    # Calmar
    calmar = (daily_mean * 252 / max_dd) if max_dd > 0 else 0.0

    # Max consecutive losses
    loss_mask  = (pnl < 0).astype(int)
    max_consec = 0
    cur        = 0
    for l in loss_mask:
        cur = cur + 1 if l else 0
        max_consec = max(max_consec, cur)

    return {
        "n_trades":      n,
        "win_rate":      win_rate,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "payoff":        payoff,
        "total_pnl":     pnl.sum(),
        "max_dd":        max_dd,
        "sharpe":        sharpe,
        "dsr":           dsr,
        "calmar":        calmar,
        "max_consec_loss": max_consec,
    }


def _empty_metrics() -> dict:
    return {k: np.nan for k in [
        "n_trades", "win_rate", "avg_win", "avg_loss", "payoff",
        "total_pnl", "max_dd", "sharpe", "dsr", "calmar", "max_consec_loss"
    ]}
