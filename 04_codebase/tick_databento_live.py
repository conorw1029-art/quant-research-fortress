#!/usr/bin/env python3
"""
tick_databento_live.py — Real-time bar feed from Databento ohlcv-1m
=====================================================================
Subscribes to Databento GLBX.MDP3 ohlcv-1m using continuous contracts.
Each 1-minute bar arrives 1-2 seconds after the minute closes.

COST: ~$0.31/month for GC+ES+NQ+SI. $4.86 balance covers 15+ months.
      DO NOT change schema to 'trades' ($70/mo) or 'mbp-10' ($329/mo).

On startup: backfills last 60 days ($0.36 one-time).
Then streams live 1m bars and resamples to 3m/5m/15m/30m parquets.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import databento as db

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
BAR_DIR = Path("/opt/fortress/01_data/tick_bars")
if not BAR_DIR.exists():
    BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = ROOT / ".env"
LOG_FILE = ROOT / "01_data" / "logs" / "databento_live.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DB] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DATASET       = "GLBX.MDP3"
SCHEMA        = "ohlcv-1m"
STYPE         = "continuous"
BACKFILL_DAYS = 60

# Continuous contract symbols (no rollover needed — always front month)
SYMBOLS = {
    "GC": "MGC.c.0",
    "ES": "MES.c.0",
    "NQ": "MNQ.c.0",
    "SI": "SIL.c.0",
}
# Reverse map: "MES.c.0" → "ES"
SYM_REVERSE = {v: k for k, v in SYMBOLS.items()}

RESAMPLE_TFS = [3, 5, 15, 30]

# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── Write locks ───────────────────────────────────────────────────────────────
_locks: dict[str, threading.Lock] = {}
_mu = threading.Lock()

def _lock(key: str) -> threading.Lock:
    with _mu:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]

# ── Synthetic CVD ─────────────────────────────────────────────────────────────
def _cvd_delta(o: float, h: float, l: float, c: float, v: int) -> int:
    rng = h - l
    if rng < 1e-9:
        return 0
    return int(v * (c - l) / rng) - int(v * (h - c) / rng)

# ── Parquet upsert ────────────────────────────────────────────────────────────
def _upsert(path: Path, new_df: pd.DataFrame):
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            existing.index = pd.to_datetime(existing.index, utc=True)
            for col in new_df.columns:
                if col not in existing.columns:
                    existing[col] = np.nan
            for col in existing.columns:
                if col not in new_df.columns:
                    new_df[col] = np.nan
            combined = pd.concat([existing, new_df]).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
        except Exception:
            combined = new_df
    else:
        combined = new_df
    combined["cvd"] = combined["cvd_delta"].cumsum()
    combined.to_parquet(path, engine="pyarrow", compression="snappy")


def write_bar(sym: str, bar_min: int, ts: pd.Timestamp,
              o: float, h: float, l: float, c: float, v: int):
    cvd   = _cvd_delta(o, h, l, c, v)
    buy_v = max(0, int(v * (c - l) / (h - l + 1e-9)))
    sel_v = max(0, v - buy_v)
    row = {
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "buy_vol": buy_v, "sell_vol": sel_v, "cvd_delta": cvd, "cvd": 0,
        "n_trades": v,
        "spread": 0.0, "bid_sz_00": 0.0, "ask_sz_00": 0.0,
        "book_pressure": 0.0, "obi_5": 0.0, "microprice": c,
        "imbal_L5_last": 0.0, "microprice_last": c,
        "spread_mean": 0.0, "bid_sz_mean": 0.0, "ask_sz_mean": 0.0,
    }
    new_df = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="ts"))
    pq = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    l2 = BAR_DIR / f"{sym}_bars_l2_{bar_min}m.parquet"
    with _lock(f"{sym}_{bar_min}"):
        for path in (pq, l2):
            _upsert(path, new_df)

# ── Multi-timeframe resampler ─────────────────────────────────────────────────
class TFResampler:
    def __init__(self, sym: str, tf: int):
        self.sym = sym
        self.tf  = tf
        self._bars: list[tuple] = []

    def feed(self, ts: pd.Timestamp, o: float, h: float,
             l: float, c: float, v: int):
        self._bars.append((ts, o, h, l, c, v))
        bar_open = ts.floor(f"{self.tf}min")
        window = [b for b in self._bars if b[0].floor(f"{self.tf}min") == bar_open]
        if len(window) >= self.tf:
            agg_o = window[0][1]
            agg_h = max(b[2] for b in window)
            agg_l = min(b[3] for b in window)
            agg_c = window[-1][4]
            agg_v = sum(b[5] for b in window)
            write_bar(self.sym, self.tf, bar_open, agg_o, agg_h, agg_l, agg_c, agg_v)
            flush_ts = {b[0] for b in window}
            self._bars = [b for b in self._bars if b[0] not in flush_ts]

_resamplers: dict[str, TFResampler] = {
    f"{sym}_{tf}": TFResampler(sym, tf)
    for sym in SYMBOLS
    for tf in RESAMPLE_TFS
}

# ── Backfill ──────────────────────────────────────────────────────────────────
def _bulk_upsert(path: Path, new_df: pd.DataFrame):
    """Upsert an entire DataFrame of bars into a parquet file in one shot."""
    new_df = new_df.copy()
    new_df.index = pd.to_datetime(new_df.index, utc=True)
    new_df.index.name = "ts"
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            existing.index = pd.to_datetime(existing.index, utc=True)
            for col in new_df.columns:
                if col not in existing.columns:
                    existing[col] = np.nan
            for col in existing.columns:
                if col not in new_df.columns:
                    new_df[col] = np.nan
            combined = pd.concat([existing, new_df]).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
        except Exception as e:
            log.warning(f"  Merge error for {path.name}: {e} — using new data only")
            combined = new_df
    else:
        combined = new_df
    combined["cvd"] = combined["cvd_delta"].cumsum()
    combined.to_parquet(path, engine="pyarrow", compression="snappy")
    return len(combined)


def backfill(api_key: str):
    log.info(f"Backfill: last {BACKFILL_DAYS} days of ohlcv-1m...")
    end   = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=BACKFILL_DAYS)
    client = db.Historical(key=api_key)

    try:
        cost = client.metadata.get_cost(
            dataset=DATASET, schema=SCHEMA,
            symbols=list(SYMBOLS.values()), stype_in=STYPE,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        log.info(f"  Cost: ${cost:.4f}")
        if cost > 2.00:
            log.warning(f"  ${cost:.4f} exceeds $2 safety cap — skipping backfill")
            return
    except Exception as e:
        log.warning(f"  Cost check error: {e} — skipping")
        return

    try:
        data = client.timeseries.get_range(
            dataset=DATASET, schema=SCHEMA,
            symbols=list(SYMBOLS.values()), stype_in=STYPE,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        df = data.to_df()
    except Exception as e:
        log.error(f"  Download error: {e}")
        return

    if df is None or len(df) == 0:
        log.warning("  No data returned")
        return

    # Build per-symbol 1m DataFrames in memory, then bulk-upsert once per symbol
    sym_dfs: dict[str, list] = {sym: [] for sym in SYMBOLS}
    for ts, row in df.iterrows():
        raw_sym = str(row.get("symbol", "")).strip()
        base    = SYM_REVERSE.get(raw_sym)
        if base is None:
            continue
        ts = pd.Timestamp(ts).tz_convert("UTC") if getattr(ts, "tzinfo", None) else pd.Timestamp(ts, tz="UTC")
        o = float(row["open"]); h = float(row["high"])
        l = float(row["low"]);  c = float(row["close"])
        v = int(row.get("volume", 0))
        if c <= 0:
            continue
        cvd   = _cvd_delta(o, h, l, c, v)
        buy_v = max(0, int(v * (c - l) / (h - l + 1e-9)))
        sel_v = max(0, v - buy_v)
        sym_dfs[base].append({
            "ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "buy_vol": buy_v, "sell_vol": sel_v, "cvd_delta": cvd, "cvd": 0,
            "n_trades": v, "spread": 0.0, "bid_sz_00": 0.0, "ask_sz_00": 0.0,
            "book_pressure": 0.0, "obi_5": 0.0, "microprice": c,
            "imbal_L5_last": 0.0, "microprice_last": c,
            "spread_mean": 0.0, "bid_sz_mean": 0.0, "ask_sz_mean": 0.0,
        })

    # Bulk upsert 1m data, then resample to higher timeframes
    for base, rows in sym_dfs.items():
        if not rows:
            log.warning(f"  No data for {base}")
            continue
        new_1m = pd.DataFrame(rows).set_index("ts")
        new_1m.index = pd.to_datetime(new_1m.index, utc=True)
        new_1m = new_1m.sort_index()

        pq = BAR_DIR / f"{base}_bars_1m.parquet"
        l2 = BAR_DIR / f"{base}_bars_l2_1m.parquet"
        with _lock(f"{base}_1"):
            n = _bulk_upsert(pq, new_1m)
            _bulk_upsert(l2, new_1m)
        log.info(f"  {base} 1m: {len(rows)} new bars → {n} total")

        # Resample to higher timeframes
        for tf in RESAMPLE_TFS:
            agg = new_1m.resample(f"{tf}min", closed="left", label="left").agg({
                "open":      "first",
                "high":      "max",
                "low":       "min",
                "close":     "last",
                "volume":    "sum",
                "buy_vol":   "sum",
                "sell_vol":  "sum",
                "cvd_delta": "sum",
                "n_trades":  "sum",
            }).dropna(subset=["close"])
            # Fill derived fields
            for col in ["spread","bid_sz_00","ask_sz_00","book_pressure","obi_5",
                        "imbal_L5_last","spread_mean","bid_sz_mean","ask_sz_mean"]:
                agg[col] = 0.0
            agg["microprice"] = agg["close"]
            agg["microprice_last"] = agg["close"]
            agg["cvd"] = 0
            pq_tf = BAR_DIR / f"{base}_bars_{tf}m.parquet"
            l2_tf = BAR_DIR / f"{base}_bars_l2_{tf}m.parquet"
            with _lock(f"{base}_{tf}"):
                _bulk_upsert(pq_tf, agg)
                _bulk_upsert(l2_tf, agg)
        log.info(f"  {base} resampled to {RESAMPLE_TFS}m")

# ── Historical-API poller (replaces Live stream — no live license needed) ──────
POLL_SECS    = 60    # fetch last 5 bars every 60s — ~60s latency, ~$0.50/month
POLL_LOOKBACK = 5    # minutes of recent bars to fetch each poll


def _fetch_recent(client: db.Historical, lookback_min: int = POLL_LOOKBACK) -> pd.DataFrame | None:
    """Fetch the last lookback_min 1m bars for all symbols via Historical API."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_min + 2)   # small buffer
    try:
        data = client.timeseries.get_range(
            dataset=DATASET, schema=SCHEMA,
            symbols=list(SYMBOLS.values()), stype_in=STYPE,
            start=start.isoformat(), end=end.isoformat(),
        )
        df = data.to_df()
        return df if df is not None and len(df) > 0 else None
    except Exception as e:
        log.debug(f"Poll error: {e}")
        return None


def poll_loop(api_key: str):
    """
    Poll Databento Historical API every POLL_SECS for new 1m bars.
    Gives ~60s data latency vs 15-20min for yfinance.
    Uses only Historical API — no live license required.
    Monthly cost: ~$0.50 (60-day backfill $0.52 + ~$0.30/month polling).
    """
    client = db.Historical(key=api_key)
    seen: set[tuple] = set()   # (sym, ts) pairs already written
    polls = 0
    bars_total = 0

    log.info(f"Poll mode: Historical API every {POLL_SECS}s  (no live license needed)")
    log.info(f"  Latency: ~{POLL_SECS}s  |  Cost: ~$0.50/month  |  Symbols: {list(SYMBOLS)}")

    while True:
        df = _fetch_recent(client)
        if df is not None:
            new_bars = 0
            for ts, row in df.iterrows():
                raw_sym = str(row.get("symbol", "")).strip()
                base    = SYM_REVERSE.get(raw_sym)
                if base is None:
                    continue
                ts_utc = pd.Timestamp(ts).tz_convert("UTC") if getattr(ts, "tzinfo", None) else pd.Timestamp(ts, tz="UTC")
                key = (base, ts_utc)
                if key in seen:
                    continue
                seen.add(key)

                o = float(row["open"]); h = float(row["high"])
                l = float(row["low"]);  c = float(row["close"])
                v = int(row.get("volume", 0))
                if c <= 0:
                    continue

                write_bar(base, 1, ts_utc, o, h, l, c, v)
                for tf in RESAMPLE_TFS:
                    _resamplers[f"{base}_{tf}"].feed(ts_utc, o, h, l, c, v)

                lag = (datetime.now(timezone.utc) - ts_utc.to_pydatetime()).total_seconds()
                log.info(f"  {base} 1m  {ts_utc.strftime('%H:%M')}  C={c:.2f}  V={v}  lag={lag:.0f}s")
                new_bars += 1

            bars_total += new_bars
            polls += 1
            if polls % 10 == 0:
                log.info(f"  [poll #{polls}]  {bars_total} total bars written")

            # Prune seen set — keep only last 10 minutes
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            seen = {(s, t) for s, t in seen if t > cutoff}

        time.sleep(POLL_SECS)


def run_forever(api_key: str):
    try:
        poll_loop(api_key)
    except KeyboardInterrupt:
        log.info("Stopped.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop",          action="store_true")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--no-backfill",   action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("DATABENTO_API_KEY", "")
    if not api_key.startswith("db-"):
        log.error("DATABENTO_API_KEY missing or invalid in /opt/fortress/.env")
        sys.exit(1)

    log.info(f"Fortress Databento Live  schema=ohlcv-1m  cost=~$0.31/month")
    log.info(f"Symbols: {SYMBOLS}")
    log.info(f"Parquets: {BAR_DIR}")

    if not args.no_backfill:
        backfill(api_key)

    if args.backfill_only:
        return

    # Always use poll mode — Live API requires separate license ($50+/month)
    run_forever(api_key)


if __name__ == "__main__":
    main()
