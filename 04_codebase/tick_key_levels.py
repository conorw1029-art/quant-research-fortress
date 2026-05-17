"""
tick_key_levels.py — Key Level Computation for Signal Annotation
================================================================
Provides four market context layers:

  1. PDH / PDL / PDC   — previous day high, low, close
  2. Intraday VWAP      — cumulative for today's UTC session
  3. Volume POC         — price with highest cumulative volume (rolling N bars)
  4. Round numbers      — nearest significant price increment

These are CONTEXTUAL only. They annotate executor alerts so the
trader can see whether a signal fires near a significant level.
They do NOT filter or block signals — the strategy entry criteria
already encode mean-reversion / breakout logic.

Usage in executor:
    from tick_key_levels import compute_key_levels, annotate_alert
    kl    = compute_key_levels(df, "ES")
    alert = annotate_alert(alert, kl)
    print(alert["key_level_context"])
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ── Round-number step by instrument ──────────────────────────────────────────

_ROUND_STEPS: dict[str, float] = {
    "ES": 25.0,  "MES": 25.0,
    "NQ": 100.0, "MNQ": 100.0,
    "GC": 50.0,  "MGC": 50.0,
    "SI": 0.50,  "SIL": 0.50,
}


def _round_step(symbol: str) -> float:
    """Return significant round-number increment for symbol."""
    # Exact match first, then prefix
    if symbol in _ROUND_STEPS:
        return _ROUND_STEPS[symbol]
    for prefix in ("MES", "MNQ", "MGC", "SIL", "ES", "NQ", "GC", "SI"):
        if symbol.startswith(prefix):
            return _ROUND_STEPS[prefix]
    return 25.0


def _nearest_round(price: float, step: float) -> float:
    return round(round(price / step) * step, 4)


# ── POC computation ───────────────────────────────────────────────────────────

def _compute_poc(df: pd.DataFrame, bins: int = 150) -> Optional[float]:
    """
    Find the price level with the highest cumulative volume across `df`.
    Volume is distributed uniformly across each bar's high-low range.
    Returns None if data is insufficient.
    """
    if "volume" not in df.columns or len(df) < 5:
        return None
    lo_all = float(df["low"].min())
    hi_all = float(df["high"].max())
    if hi_all <= lo_all:
        return None

    edges      = np.linspace(lo_all, hi_all, bins + 1)
    vol_profile = np.zeros(bins)

    for _, row in df.iterrows():
        vol = row.get("volume", 0)
        if vol <= 0 or pd.isna(vol):
            continue
        blo = max(0, int(np.searchsorted(edges, row["low"],  side="left"))  - 1)
        bhi = min(bins - 1, int(np.searchsorted(edges, row["high"], side="right")) - 1)
        n   = bhi - blo + 1
        if n > 0:
            vol_profile[blo:bhi + 1] += vol / n

    poc_bin = int(np.argmax(vol_profile))
    poc_px  = float((edges[poc_bin] + edges[poc_bin + 1]) / 2)
    return round(poc_px, 4)


# ── VWAP computation ──────────────────────────────────────────────────────────

def _compute_vwap(df: pd.DataFrame) -> Optional[float]:
    """
    Today-to-now VWAP (all bars whose index.date == today UTC).
    Returns None if no today bars or no volume.
    """
    if "volume" not in df.columns:
        return None
    today = pd.Timestamp.now(tz="UTC").date()
    today_df = df[df.index.date == today]
    if today_df.empty:
        return None
    tp  = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
    vol = today_df["volume"].replace(0, np.nan).fillna(0)
    total_vol = vol.sum()
    if total_vol <= 0:
        return None
    vwap = float((tp * vol).sum() / total_vol)
    return round(vwap, 4)


# ── Key levels dataclass ──────────────────────────────────────────────────────

@dataclass
class KeyLevels:
    symbol:        str
    as_of:         str             # ISO timestamp
    pdh:           Optional[float] # previous day high
    pdl:           Optional[float] # previous day low
    pdc:           Optional[float] # previous day close
    vwap:          Optional[float] # today's VWAP
    poc:           Optional[float] # volume POC (rolling N bars)
    nearest_round: Optional[float] # nearest significant round number
    round_step:    float = 25.0    # round number granularity

    def nearby(self, price: float, atr: float,
               max_r: float = 2.0) -> list[dict]:
        """
        Return all levels within max_r × ATR of `price`, sorted by distance.
        Each item: {type, level, distance_pts, distance_r, side}
        """
        candidates = []
        for label, val in [
            ("PDH",   self.pdh),
            ("PDL",   self.pdl),
            ("PDC",   self.pdc),
            ("VWAP",  self.vwap),
            ("POC",   self.poc),
            ("ROUND", self.nearest_round),
        ]:
            if val is None:
                continue
            dist = abs(price - val)
            if atr > 0 and dist <= max_r * atr:
                candidates.append({
                    "type":         label,
                    "level":        round(val, 4),
                    "distance_pts": round(dist, 4),
                    "distance_r":   round(dist / atr, 2),
                    "side":         "above" if val > price else "below",
                })
        return sorted(candidates, key=lambda x: x["distance_pts"])

    def to_dict(self) -> dict:
        return {
            "pdh":   self.pdh,
            "pdl":   self.pdl,
            "pdc":   self.pdc,
            "vwap":  self.vwap,
            "poc":   self.poc,
            "round": self.nearest_round,
        }


# ── Main computation function ─────────────────────────────────────────────────

def compute_key_levels(df: pd.DataFrame, symbol: str,
                       poc_bars: int = 200) -> KeyLevels:
    """
    Compute all key levels from a bar DataFrame (UTC-indexed, OHLCV).

    Parameters
    ----------
    df       : bar DataFrame; must have open/high/low/close, ideally volume
    symbol   : base instrument symbol (e.g. "ES", "GC", "MES")
    poc_bars : rolling lookback for volume POC
    """
    now   = pd.Timestamp.now(tz="UTC")
    today = now.date()

    # Ensure UTC index
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")

    # ── PDH / PDL / PDC ───────────────────────────────────────────────────
    yday = today - pd.Timedelta(days=1)
    # Skip Saturday/Sunday (no trading day)
    if yday.weekday() == 6:   # Sunday → go back to Friday
        yday = yday - pd.Timedelta(days=2)
    elif yday.weekday() == 5: # Saturday → go back to Friday
        yday = yday - pd.Timedelta(days=1)

    yday_mask = df.index.date == yday
    yday_df   = df[yday_mask]
    if not yday_df.empty:
        pdh = round(float(yday_df["high"].max()),  4)
        pdl = round(float(yday_df["low"].min()),   4)
        pdc = round(float(yday_df["close"].iloc[-1]), 4)
    else:
        pdh = pdl = pdc = None

    # ── VWAP ──────────────────────────────────────────────────────────────
    vwap = _compute_vwap(df)

    # ── Volume POC ────────────────────────────────────────────────────────
    recent = df.iloc[-poc_bars:]
    poc = _compute_poc(recent)

    # ── Round number ──────────────────────────────────────────────────────
    last_px    = float(df["close"].iloc[-1])
    step       = _round_step(symbol)
    near_round = _nearest_round(last_px, step)

    return KeyLevels(
        symbol=symbol, as_of=now.isoformat(),
        pdh=pdh, pdl=pdl, pdc=pdc,
        vwap=vwap, poc=poc,
        nearest_round=near_round, round_step=step,
    )


# ── Alert annotation ──────────────────────────────────────────────────────────

def annotate_alert(alert: dict, levels: KeyLevels) -> dict:
    """
    Add key level context to an alert dict in-place.
    Adds: alert["key_levels"], alert["key_level_context"].
    Never raises — key levels are informational, never block execution.
    """
    entry = float(alert.get("entry_px", 0.0))
    atr   = float(alert.get("atr",      1.0))
    stop  = float(alert.get("stop_px",  entry))

    nearby_levels = levels.nearby(entry, atr, max_r=2.0)

    alert["key_levels"] = {
        **levels.to_dict(),
        "nearby": nearby_levels,
    }

    # Human-readable context line
    if nearby_levels:
        parts = []
        for lv in nearby_levels[:3]:  # top 3 closest
            parts.append(
                f"{lv['type']}({lv['level']}) {lv['side']} {lv['distance_r']:.1f}R"
            )
        alert["key_level_context"] = " | ".join(parts)
    else:
        alert["key_level_context"] = "no key levels within 2R"

    # Flag if entry is between PDH and a key level (potential trap)
    stop_side = "below" if alert.get("direction", 1) == 1 else "above"
    for lv in nearby_levels:
        if lv["type"] in ("PDH", "PDL", "VWAP", "POC") and lv["side"] == stop_side:
            if lv["distance_r"] < 0.8:
                alert["key_level_context"] += (
                    f"  ⚠ {lv['type']} {lv['distance_r']:.1f}R behind stop"
                )
            break

    return alert


# ── Standalone demo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pandas as pd
    from pathlib import Path

    bar_dir = Path(__file__).parent.parent / "01_data" / "tick_bars"

    for sym in ("ES", "GC", "NQ"):
        path = bar_dir / f"{sym}_bars_15m.parquet"
        if not path.exists():
            print(f"  {sym}: no bar file")
            continue
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        df.sort_index(inplace=True)

        kl = compute_key_levels(df, sym)
        price = float(df["close"].iloc[-1])

        print(f"\n{sym} @ {price:.2f}")
        print(f"  PDH={kl.pdh}  PDL={kl.pdl}  PDC={kl.pdc}")
        print(f"  VWAP={kl.vwap}  POC={kl.poc}")
        print(f"  Round({kl.round_step:.0f})={kl.nearest_round}")

        atr_approx = (df["high"] - df["low"]).iloc[-20:].mean() * 1.0
        nearby = kl.nearby(price, atr_approx)
        if nearby:
            print(f"  Nearby levels (within 2 ATR):")
            for lv in nearby:
                print(f"    {lv['type']:6s} {lv['level']:>10.2f}  "
                      f"{lv['side']:5s}  {lv['distance_r']:.2f}R")
        else:
            print(f"  No key levels within 2 ATR")
