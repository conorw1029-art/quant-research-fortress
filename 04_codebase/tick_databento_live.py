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
SCHEMA        = "ohlcv-1m"          # override with DATABENTO_SCHEMA env: ohlcv-1m | trades | mbp-10
STYPE         = "continuous"
BACKFILL_DAYS = 60
COST_CAP_USD  = 2.00               # hard safety cap on any single fetch; override with DATABENTO_COST_CAP env
# Measured 2026-06-28 (4 syms, 1mo): ohlcv-1m=$0.31  trades=$43.68  mbp-10=$180.96
# 'trades' gives REAL footprint (buy/sell vol, CVD) via process_trades(); depth stays synthetic.

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

# ── Synthetic L2 ──────────────────────────────────────────────────────────────
def _synthetic_l2(buy_v: float, sel_v: float, h: float, l: float, c: float) -> dict:
    """Compute synthetic L2 fields from OHLCV volume split.
    obi_5 = (buy - sell) / (buy + sell + 1) — same sign/range as real DOM OBI.
    spread proxy = (high - low) * 5% — typical bid/ask spread as fraction of range.
    """
    obi    = (buy_v - sel_v) / (buy_v + sel_v + 1)
    spread = (h - l) * 0.05
    return {
        "spread":        spread,
        "bid_sz_00":     float(buy_v),
        "ask_sz_00":     float(sel_v),
        "book_pressure": obi,
        "obi_5":         obi,
        "microprice":    c,
        "imbal_L5_last": obi,
        "microprice_last": c,
        "spread_mean":   spread,
        "bid_sz_mean":   float(buy_v),
        "ask_sz_mean":   float(sel_v),
    }

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
        **_synthetic_l2(buy_v, sel_v, h, l, c),
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


def _trades_to_sym_rows(df) -> dict:
    """Convert a raw Databento 'trades' DataFrame into per-symbol lists of 1m bar
    dicts with REAL footprint (buy_vol/sell_vol/cvd derived from the aggressor side),
    reusing the validated process_trades() feature builder. Depth columns remain
    synthetic (real order-book depth requires the mbp-10 schema)."""
    from tick_databento_to_features import process_trades
    out: dict[str, list] = {sym: [] for sym in SYMBOLS}
    sym_col = df["symbol"].astype(str).str.strip()
    for raw_sym, g in df.groupby(sym_col):
        base = SYM_REVERSE.get(raw_sym)
        if base is None:
            continue
        bars = process_trades(g.copy(), "1min")        # real OHLCV + buy/sell/cvd
        for ts, r in bars.iterrows():
            ts = pd.Timestamp(ts)
            ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            if c <= 0:
                continue
            v  = int(r.get("volume", 0) or 0)
            bv = float(r.get("buy_vol", 0) or 0)
            sv = float(r.get("sell_vol", 0) or 0)
            out[base].append({
                "ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v,
                "buy_vol": bv, "sell_vol": sv, "cvd_delta": bv - sv, "cvd": 0,
                "n_trades": int(r.get("n_trades", v) or v),
                **_synthetic_l2(bv, sv, h, l, c),
            })
    return out


def backfill(api_key: str):
    log.info(f"Backfill: last {BACKFILL_DAYS} days of {SCHEMA}...")
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
        log.info(f"  Cost: ${cost:.4f}  (schema={SCHEMA}, cap=${COST_CAP_USD:.2f})")
        if cost > COST_CAP_USD:
            log.warning(f"  ${cost:.4f} exceeds ${COST_CAP_USD:.2f} safety cap — skipping. "
                        f"For 'trades' use a short window, e.g. --backfill-days 1 (~$1.50).")
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
    if SCHEMA == "trades":
        # REAL footprint path: derive buy/sell vol + CVD from the aggressor side.
        sym_dfs = _trades_to_sym_rows(df)
    else:
        # ohlcv path: buy/sell vol estimated from bar shape (synthetic footprint).
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
                "n_trades": v, **_synthetic_l2(buy_v, sel_v, h, l, c),
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
            # Synthetic L2 from aggregated volume
            obi = (agg["buy_vol"] - agg["sell_vol"]) / (agg["buy_vol"] + agg["sell_vol"] + 1)
            spread_col = (agg["high"] - agg["low"]) * 0.05
            agg["obi_5"]          = obi
            agg["book_pressure"]  = obi
            agg["imbal_L5_last"]  = obi
            agg["spread"]         = spread_col
            agg["spread_mean"]    = spread_col
            agg["bid_sz_00"]      = agg["buy_vol"].astype(float)
            agg["ask_sz_00"]      = agg["sell_vol"].astype(float)
            agg["bid_sz_mean"]    = agg["buy_vol"].astype(float)
            agg["ask_sz_mean"]    = agg["sell_vol"].astype(float)
            agg["microprice"]     = agg["close"]
            agg["microprice_last"] = agg["close"]
            agg["cvd"] = 0
            pq_tf = BAR_DIR / f"{base}_bars_{tf}m.parquet"
            l2_tf = BAR_DIR / f"{base}_bars_l2_{tf}m.parquet"
            with _lock(f"{base}_{tf}"):
                _bulk_upsert(pq_tf, agg)
                _bulk_upsert(l2_tf, agg)
        log.info(f"  {base} resampled to {RESAMPLE_TFS}m")

# ── Historical-API poller (replaces Live stream — no live license needed) ──────
POLL_SECS    = 60    # fetch last 15 bars every 60s — ~60s latency, ~$0.50/month
POLL_LOOKBACK = 15   # minutes — covers ~7min Databento publication lag


def _fetch_recent(client: db.Historical, lookback_min: int = POLL_LOOKBACK) -> pd.DataFrame | None:
    """Fetch the last lookback_min 1m bars for all symbols via Historical API."""
    end   = datetime.now(timezone.utc) - timedelta(minutes=15)   # cap: Databento lags ~4-7min
    start = end - timedelta(minutes=lookback_min)
    try:
        data = client.timeseries.get_range(
            dataset=DATASET, schema=SCHEMA,
            symbols=list(SYMBOLS.values()), stype_in=STYPE,
            start=start.isoformat(), end=end.isoformat(),
        )
        df = data.to_df()
        return df if df is not None and len(df) > 0 else None
    except Exception as e:
        err = str(e)
        if "data_end_after_available_end" in err:
            log.debug("Poll: data not yet published, will retry")
        elif "dataset_unavailable_range" in err or "subscription" in err or "license" in err:
            log.debug("Poll: subscription gap, backing off 5min")
        else:
            log.warning(f"Poll error: {e}")
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


def patch_all_parquets():
    """Retroactively recompute synthetic L2 fields in all existing parquets."""
    pq_files = sorted(BAR_DIR.glob("*.parquet"))
    log.info(f"Patching {len(pq_files)} parquets with synthetic L2...")
    patched = 0
    for pq in pq_files:
        try:
            df = pd.read_parquet(pq)
            df.index = pd.to_datetime(df.index, utc=True)
            if "buy_vol" not in df.columns or "close" not in df.columns:
                continue
            buy_v = df["buy_vol"].fillna(0).astype(float)
            sel_v = df["sell_vol"].fillna(0).astype(float)
            obi   = (buy_v - sel_v) / (buy_v + sel_v + 1)
            spread_col = (df["high"] - df["low"]) * 0.05
            df["obi_5"]           = obi
            df["book_pressure"]   = obi
            df["imbal_L5_last"]   = obi
            df["spread"]          = spread_col
            df["spread_mean"]     = spread_col
            df["bid_sz_00"]       = buy_v
            df["ask_sz_00"]       = sel_v
            df["bid_sz_mean"]     = buy_v
            df["ask_sz_mean"]     = sel_v
            df["microprice"]      = df["close"]
            df["microprice_last"] = df["close"]
            df["cvd"] = df["cvd_delta"].cumsum()
            df.to_parquet(pq, engine="pyarrow", compression="snappy")
            patched += 1
        except Exception as e:
            log.warning(f"  Patch failed {pq.name}: {e}")
    log.info(f"  Patched {patched}/{len(pq_files)} parquets — synthetic L2 live on all bars")


def run_forever(api_key: str):
    try:
        poll_loop(api_key)
    except KeyboardInterrupt:
        log.info("Stopped.")


def probe_cost(api_key: str):
    """Zero-data-cost: print the real monthly cost of each schema for the
    configured symbols, so you can decide a tier before paying a cent."""
    client = db.Historical(key=api_key)
    end   = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=30)
    log.info(f"Cost probe — {start:%Y-%m-%d}..{end:%Y-%m-%d}, symbols={list(SYMBOLS)}")
    for sch in ("ohlcv-1m", "trades", "mbp-10"):
        try:
            cost = client.metadata.get_cost(
                dataset=DATASET, schema=sch, symbols=list(SYMBOLS.values()),
                stype_in=STYPE, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
            log.info(f"  {sch:10s}  ~${cost:.2f}/month")
        except Exception as e:
            log.info(f"  {sch:10s}  probe error: {repr(e)[:120]}")


def main():
    global SCHEMA, BACKFILL_DAYS, COST_CAP_USD
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop",          action="store_true")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--no-backfill",   action="store_true")
    parser.add_argument("--backfill-days", type=int, default=None,
                        help="Override backfill window (use 1 for a cheap 'trades' test)")
    parser.add_argument("--probe-cost",    action="store_true",
                        help="Print real monthly cost of every schema, then exit (no data fetched)")
    parser.add_argument("--patch-l2",      action="store_true",
                        help="Retroactively patch all parquets with synthetic L2, then exit")
    args = parser.parse_args()

    # Schema / cost-cap / window come from env + flags (.env already loaded at import)
    SCHEMA       = os.environ.get("DATABENTO_SCHEMA", SCHEMA)
    COST_CAP_USD = float(os.environ.get("DATABENTO_COST_CAP", COST_CAP_USD))
    if args.backfill_days is not None:
        BACKFILL_DAYS = args.backfill_days

    api_key = os.environ.get("DATABENTO_API_KEY", "")
    if not api_key.startswith("db-") and not args.patch_l2:
        log.error("DATABENTO_API_KEY missing or invalid in /opt/fortress/.env")
        sys.exit(1)

    if args.patch_l2:
        patch_all_parquets()
        return

    if args.probe_cost:
        probe_cost(api_key)
        return

    _cost_hint = {"ohlcv-1m": "~$0.31/mo", "trades": "~$44/mo", "mbp-10": "~$181/mo"}.get(SCHEMA, "?")
    log.info(f"Fortress Databento Live  schema={SCHEMA}  cost={_cost_hint}  cap=${COST_CAP_USD:.2f}")
    log.info(f"Symbols: {SYMBOLS}")
    log.info(f"Parquets: {BAR_DIR}")

    if not args.no_backfill:
        backfill(api_key)

    if args.backfill_only:
        return

    # Live poll loop currently handles row-per-bar schemas (ohlcv). The 'trades'
    # poller needs overlap-minimal windowing (else it multiplies the bill) and must
    # be validated against real trade records — wire it after a --backfill-only test.
    if SCHEMA == "trades":
        log.warning("Live poll for 'trades' not yet enabled. Validate first: "
                    "DATABENTO_SCHEMA=trades python tick_databento_live.py --backfill-only --backfill-days 1")
        return

    # Always use poll mode — Live API requires separate license ($50+/month)
    run_forever(api_key)


if __name__ == "__main__":
    main()
