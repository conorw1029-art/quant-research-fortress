"""
Prior Day High/Low Sweep Reversal
===================================
THESIS: Prior day high/low levels are widely watched by institutional
desks. A "sweep" — a brief breach beyond the level — followed by a
reversal back inside the prior range, is one of the highest-probability
intraday setups in futures (SMC / ICT literature; Sperandeo 1994). The
sweep lures breakout buyers/sellers, then the real direction reverses.

This is DISTINCT from prior_day_breakout (which trades the breakout
continuation). This strategy trades the REVERSAL after the breach.

SIGNAL:
  - Compute prior_day_high and prior_day_low from the previous session.
  - High sweep short: current bar's high exceeds prior_day_high by ≤
    sweep_buffer × ATR, AND the bar closes back BELOW prior_day_high
    → short signal (fakeout of the highs)
  - Low sweep long: current bar's low exceeds prior_day_low by ≤
    sweep_buffer × ATR, AND the bar closes back ABOVE prior_day_low
    → long signal (fakeout of the lows)
  - Only during RTH (09:30-16:00 ET). First occurrence per session.
  - Do NOT trade if the session OPEN is already beyond the prior range
    (pre-existing breakout, not a sweep).

ENTRY / EXIT:
  - Entry at next bar open.
  - Target: prior day midpoint (prior_day_high + prior_day_low) / 2
  - Stop: the sweep extreme (bar's high for short, bar's low for long)
  - Also apply rr_ratio as an alternative target if midpoint is close
  - Timeout: 24 bars (2 hours of 5-min bars)

PARAM GRID: 2 × 2 × 2 = 8 combos
  sweep_buffer_atr: [0.25, 0.5]
  rr_ratio:         [1.0, 1.5]
  hold_bars:        [12, 24]

LOOKAHEAD RISK:
  prior_day_high/low use shift=1 on daily groupby — causal.
  ATR uses prior completed bars — causal.
"""

from typing import Any, Dict, List, Optional
from datetime import time

import numpy as np
import pandas as pd

from src.strategies.base import BaseStrategy


_RTH_START = time(9, 30)
_RTH_END   = time(16, 0)


class PriorDayHLSweepStrategy(BaseStrategy):

    name        = "Prior_Day_HL_Sweep"
    description = "Prior-day high/low sweep reversal (fakeout of key levels)"
    category    = "level_reaction"
    timeframe   = "5min"
    version     = "1.0"
    max_trades_per_day = 1

    param_grid = {
        "sweep_buffer_atr": [0.25, 0.5],
        "rr_ratio":         [1.0, 1.5],
        "hold_bars":        [12, 24],
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.params = params or {
            "sweep_buffer_atr": 0.5,
            "rr_ratio": 1.0,
            "hold_bars": 24,
        }

    def _build_prior_day_levels(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute prior session high/low and midpoint, aligned to 5-min bars."""
        if "session_date" not in data.columns:
            return data.assign(
                prior_day_high=np.nan,
                prior_day_low=np.nan,
                prior_day_mid=np.nan,
            )

        daily = (
            data.groupby("session_date")
            .agg(day_high=("high", "max"), day_low=("low", "min"))
            .reset_index()
        )
        daily["prior_day_high"] = daily["day_high"].shift(1)
        daily["prior_day_low"]  = daily["day_low"].shift(1)
        daily["prior_day_mid"]  = (daily["prior_day_high"] + daily["prior_day_low"]) / 2
        daily = daily[["session_date", "prior_day_high", "prior_day_low", "prior_day_mid"]]

        out = data.merge(daily, on="session_date", how="left")
        out.index = data.index
        return out

    def _session_open_prices(self, data: pd.DataFrame) -> pd.Series:
        """First bar's open per session — to filter pre-existing breakouts."""
        if "session_date" not in data.columns:
            return pd.Series(np.nan, index=data.index)
        first_open = data.groupby("session_date")["open"].transform("first")
        return first_open

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        df = self._build_prior_day_levels(data)
        session_open = self._session_open_prices(data)

        atr = df["atr"] if "atr" in df.columns else pd.Series(0.0, index=df.index)
        sweep_buf = float(self.params["sweep_buffer_atr"])
        signals   = pd.Series(0, index=df.index)

        # Time gate
        t = df.index.time
        in_rth = (t >= _RTH_START) & (t < _RTH_END)

        pdh = df["prior_day_high"]
        pdl = df["prior_day_low"]

        # Pre-existing breakout filter: session opened beyond prior range
        open_above_pdh = (session_open > pdh).fillna(False).astype(bool)
        open_below_pdl = (session_open < pdl).fillna(False).astype(bool)

        # High sweep → short reversal
        # bar.high breaches pdh by ≤ sweep_buffer×ATR but closes back below pdh
        high_sweep = (
            (df["high"] > pdh) &
            (df["high"] <= pdh + sweep_buf * atr) &
            (df["close"] < pdh) &
            (~open_above_pdh) &
            in_rth &
            pdh.notna() &
            (atr > 0)
        )
        # Low sweep → long reversal
        low_sweep = (
            (df["low"] < pdl) &
            (df["low"] >= pdl - sweep_buf * atr) &
            (df["close"] > pdl) &
            (~open_below_pdl) &
            in_rth &
            pdl.notna() &
            (atr > 0)
        )

        signals[high_sweep] = -1
        signals[low_sweep]  =  1

        # Keep only first signal per session
        if "session_date" in df.columns:
            signals = self._one_per_session(signals, df["session_date"])

        return signals

    @staticmethod
    def _one_per_session(signals: pd.Series, session_date: pd.Series) -> pd.Series:
        result = signals.copy()
        seen = set()
        for idx, val in signals.items():
            if val == 0:
                continue
            sd = session_date.loc[idx]
            if sd in seen:
                result.loc[idx] = 0
            else:
                seen.add(sd)
        return result

    def signals_to_trades(self, data: pd.DataFrame, signals: pd.Series,
                          max_bars_per_trade: int = 78) -> List[Dict]:
        df        = self._build_prior_day_levels(data)
        rr_ratio  = float(self.params["rr_ratio"])
        hold_bars = int(self.params["hold_bars"])
        timeout   = min(hold_bars, max_bars_per_trade)

        trades = []
        for idx in signals[signals != 0].index:
            try:
                direction = int(signals[idx])
                sig_loc   = data.index.get_loc(idx)
                if sig_loc + 1 >= len(data):
                    continue

                entry_bar   = data.iloc[sig_loc + 1]
                entry_price = entry_bar["open"]
                entry_time  = entry_bar.name

                sig_bar    = data.loc[idx]
                pdh_val    = df.loc[idx, "prior_day_high"]
                pdl_val    = df.loc[idx, "prior_day_low"]
                pdm_val    = df.loc[idx, "prior_day_mid"]

                # Stop = sweep extreme
                stop_price = sig_bar["high"] if direction == -1 else sig_bar["low"]
                stop_dist  = abs(entry_price - stop_price)
                if stop_dist <= 0:
                    continue

                # Target: midpoint or rr_ratio × stop, whichever is closer
                mid_dist = abs(entry_price - pdm_val) if not np.isnan(pdm_val) else np.inf
                rr_dist  = rr_ratio * stop_dist
                # Use midpoint only if it's in the right direction and reachable
                if direction == -1 and not np.isnan(pdm_val) and pdm_val < entry_price:
                    target_price = max(pdm_val, entry_price - rr_dist)
                elif direction == 1 and not np.isnan(pdm_val) and pdm_val > entry_price:
                    target_price = min(pdm_val, entry_price + rr_dist)
                else:
                    target_price = entry_price + direction * rr_dist

                exit_price = None
                exit_time  = None
                exit_type  = "timeout"

                for i in range(1, timeout + 1):
                    loc = sig_loc + 1 + i
                    if loc >= len(data):
                        break
                    bar = data.iloc[loc]

                    if direction == -1:
                        if bar["high"] >= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["low"] <= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break
                    else:
                        if bar["low"] <= stop_price:
                            exit_price = stop_price; exit_time = bar.name; exit_type = "stop"; break
                        if bar["high"] >= target_price:
                            exit_price = target_price; exit_time = bar.name; exit_type = "target"; break

                if exit_price is None:
                    last_loc   = min(sig_loc + 1 + timeout, len(data) - 1)
                    exit_price = data.iloc[last_loc]["close"]
                    exit_time  = data.iloc[last_loc].name
                    exit_type  = "timeout"

                trades.append({
                    "entry_time":  entry_time,  "entry_price": entry_price,
                    "exit_time":   exit_time,   "exit_price":  exit_price,
                    "direction":   direction,    "exit_type":   exit_type,
                    "gross_pnl":   (exit_price - entry_price) * direction,
                    "stop_price":  stop_price,   "target_price": target_price,
                    "pdh": pdh_val, "pdl": pdl_val,
                })
            except Exception:
                continue
        return trades

    def trades_to_dataframe(self, trades: List[Dict]) -> pd.DataFrame:
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        df["entry_time"] = pd.to_datetime(df["entry_time"])
        df["exit_time"]  = pd.to_datetime(df["exit_time"])
        return df
