"""
tick_strategies_l2.py — L2 Strategy Map for Live Executor
==========================================================
Bridges tick_live_executor.py (v10 dispatch) to the L2 strategy classes
in src/strategies/. Each entry exposes a "compute" callable that matches
the executor's expected signature: compute(df, **params) -> pd.Series.

L2 strategies require bars with extra columns:
  imbal_L5_last  — bid/ask imbalance at top 5 levels
  cvd_delta      — cumulative volume delta for the bar
  microprice_last — microprice (bid*ask_sz + ask*bid_sz)/(bid_sz + ask_sz)
  session_vwap   — intraday VWAP

These come from: {symbol}_bars_l2_1m.parquet (loaded via load_bars_l2).

Confirmed survivors (news-filtered evidence 2026-06-03):
  Depth_Imbalance_Momentum GC — WF Sharpe=4.516, DSR=1.000, 3t-Sharpe=1.006
  Depth_Imbalance_Momentum SI — WF Sharpe=3.647, DSR=1.000, 3t-Sharpe=0.284
  CVD_Microprice SI           — WF Sharpe=2.522, DSR=1.000, 3t-Sharpe=0.935
  CVD_Acceleration GC        — WF Sharpe≈3.4,   DSR=1.000, 3t-Sharpe≈0.6
  Repeated_Replenishment GC  — WF Sharpe=4.102, DSR=1.000, 3t-Sharpe=0.899
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies.l2_depth_strategies import DepthImbalanceMomentumStrategy
from src.strategies.l2_cvd_strategies import CVDMicropriceStrategy, CVDAccelerationStrategy
from src.strategies.l2_absorption_strategies import RepeatedReplenishmentStrategy


L2_REQUIRED_COLUMNS = {"cvd_delta", "imbal_L5_last", "microprice_last", "buy_vol"}

def _make_entry(cls):
    """Wrap an L2 strategy class into the executor's dispatch dict format.
    Guards against yfinance bars that lack L2 columns — returns flat (all 0)
    when none of the required L2 columns are present."""
    def compute(df, **params):
        import pandas as pd
        has_l2 = any(c in df.columns for c in L2_REQUIRED_COLUMNS)
        if not has_l2:
            return pd.Series(0, index=df.index, dtype=int)
        return cls(params).generate_signals(df)
    return {"compute": compute, "class": cls}


STRAT_MAP_L2 = {
    "Depth_Imbalance_Momentum": _make_entry(DepthImbalanceMomentumStrategy),
    "CVD_Microprice":           _make_entry(CVDMicropriceStrategy),
    "CVD_Acceleration":        _make_entry(CVDAccelerationStrategy),
    "Repeated_Replenishment":  _make_entry(RepeatedReplenishmentStrategy),
}
