#!/usr/bin/env python3
"""
tick_tv_webhook.py — TradingView alert webhook receiver
========================================================
Receives bar-close webhooks from TradingView and writes them into the
fortress parquet files, replacing the 15-20 minute delayed yfinance data.

TradingView sends a POST request with JSON body on every bar close.
We receive it, parse the OHLCV, compute synthetic CVD, and update the
parquets that tick_live_executor.py reads.

This gives the executor real-time data (2-5 second delay from bar close)
without needing NinjaTrader, a Windows PC, or any other local software.

Listen: 0.0.0.0:8765  (open this port in your VPS firewall)

TRADINGVIEW ALERT SETUP (do this from your iPad):
  For each symbol × timeframe combination, create a TradingView alert:

  Condition: <any indicator> → Bar closes  (or use a simple "close > 0" condition)
  Alert name: GC_1m  (or ES_30m etc)
  Webhook URL: http://46.225.110.190:8765/bar
  Message (copy exactly):
  {
    "sym": "{{ticker}}",
    "tf": "{{interval}}",
    "ts": "{{time}}",
    "o": {{open}},
    "h": {{high}},
    "l": {{low}},
    "c": {{close}},
    "v": {{volume}}
  }

SYMBOLS TO SET UP (one alert per row):
  Ticker           Timeframes     → base symbol
  COMEX:GC1!       1,3,5,15,30   → GC
  COMEX:SI1!       1,3,5,15,30   → SI
  CME_MINI:ES1!    1,3,5,15,30   → ES
  CME_MINI:NQ1!    1,3,5,15,30   → NQ

  Total: 20 alerts  (TradingView Plus plan = 100 alert limit)

TRADINGVIEW PLAN NEEDED: Plus ($30/month) for webhooks + 100 alerts.
Free and Essential plans do NOT support webhooks.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, request, jsonify

# ── Paths ──────────────────────────────────────────────────────────────────────
BAR_DIR = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BAR_DIR.parent / "logs" / "tv_webhook.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TV] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger(__name__)

# ── TradingView ticker → fortress base symbol ──────────────────────────────────
TV_SYM_MAP = {
    "GC1!": "GC",  "COMEX:GC1!": "GC",  "GC": "GC",
    "SI1!": "SI",  "COMEX:SI1!": "SI",  "SI": "SI",
    "ES1!": "ES",  "CME_MINI:ES1!": "ES", "CME:ES1!": "ES", "ES": "ES",
    "NQ1!": "NQ",  "CME_MINI:NQ1!": "NQ", "CME:NQ1!": "NQ", "NQ": "NQ",
    # Micro symbols also accepted (same price as full-size)
    "MGC1!": "GC", "COMEX:MGC1!": "GC",
    "MES1!": "ES", "CME_MINI:MES1!": "ES",
    "MNQ1!": "NQ", "CME_MINI:MNQ1!": "NQ",
    "SIL1!": "SI", "COMEX:SIL1!": "SI",
}

# TradingView interval string → minutes
TV_TF_MAP = {
    "1": 1, "3": 3, "5": 5, "10": 10, "15": 15,
    "30": 30, "45": 45, "60": 60, "1H": 60,
    "D": 1440, "1D": 1440,
}

# ── Thread locks per parquet ───────────────────────────────────────────────────
_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_mu:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


# ── Stats ─────────────────────────────────────────────────────────────────────
_received: dict[str, int] = {}
_errors:   int = 0


# ── Synthetic CVD from OHLCV ──────────────────────────────────────────────────
def _synthetic_cvd_delta(o: float, h: float, l: float, c: float, v: float) -> int:
    """
    Estimate CVD delta from OHLCV using the Williams Accumulation/Distribution proxy.
    buy_vol_proxy  = v × (close - low) / (high - low)
    sell_vol_proxy = v × (high - close) / (high - low)
    cvd_delta      = buy_vol - sell_vol

    Returns 0 if high == low (doji bar — indeterminate direction).
    """
    rng = h - l
    if rng < 1e-9:
        return 0
    buy_frac  = (c - l) / rng
    sell_frac = (h - c) / rng
    buy_vol   = int(v * buy_frac)
    sell_vol  = int(v * sell_frac)
    return buy_vol - sell_vol


# ── Core: write one bar to parquet ────────────────────────────────────────────
def _write_bar(sym: str, bar_min: int, ts: pd.Timestamp,
               o: float, h: float, l: float, c: float, v: float):
    cvd_delta = _synthetic_cvd_delta(o, h, l, c, v)
    buy_vol   = max(0, int(v * ((c - l) / (h - l + 1e-9))))
    sell_vol  = max(0, int(v) - buy_vol)

    row = {
        "open":      o,
        "high":      h,
        "low":       l,
        "close":     c,
        "volume":    int(v),
        "buy_vol":   buy_vol,
        "sell_vol":  sell_vol,
        "cvd_delta": cvd_delta,
        "cvd":       0,       # running cumsum updated below
        "n_trades":  int(v),
        # L2 fields — not available from TradingView, left as 0
        "spread": 0, "bid_sz_00": 0, "ask_sz_00": 0,
        "book_pressure": 0, "obi_5": 0, "microprice": c,
        "imbal_L5_last": 0, "microprice_last": c,
        "spread_mean": 0, "bid_sz_mean": 0, "ask_sz_mean": 0,
    }
    new_df = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="ts"))

    pq_path = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    l2_path = BAR_DIR / f"{sym}_bars_l2_{bar_min}m.parquet"

    with _lock(f"{sym}_{bar_min}"):
        for path in (pq_path, l2_path):
            _upsert(path, new_df)

    key = f"{sym}/{bar_min}m"
    _received[key] = _received.get(key, 0) + 1


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
            # Update running CVD
            combined["cvd"] = combined["cvd_delta"].cumsum()
        except Exception:
            combined = new_df
    else:
        combined = new_df
        combined["cvd"] = combined["cvd_delta"].cumsum()

    combined.to_parquet(path, engine="pyarrow", compression="snappy")


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/bar", methods=["POST"])
def receive_bar():
    global _errors
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            # TradingView sometimes sends as text/plain — parse manually
            try:
                data = json.loads(request.data)
            except Exception:
                _errors += 1
                return jsonify({"error": "invalid JSON"}), 400

        # ── Parse symbol ──────────────────────────────────────────────────────
        raw_sym = str(data.get("sym", "")).strip().upper()
        # Remove exchange prefix if present (e.g., "COMEX:GC1!" → "GC1!")
        if ":" in raw_sym:
            raw_sym = raw_sym.split(":")[-1]
        sym = TV_SYM_MAP.get(raw_sym) or TV_SYM_MAP.get(raw_sym.split(":")[0])
        if sym is None:
            # Try stripping numbers from end: "GC1!" → "GC"
            stripped = raw_sym.rstrip("!1234567890")
            sym = TV_SYM_MAP.get(stripped)
        if sym is None:
            log.warning(f"Unknown symbol: {data.get('sym')!r} — ignoring")
            return jsonify({"error": f"unknown symbol {data.get('sym')}"}), 400

        # ── Parse timeframe ───────────────────────────────────────────────────
        raw_tf = str(data.get("tf", "")).strip()
        bar_min = TV_TF_MAP.get(raw_tf)
        if bar_min is None:
            try:
                bar_min = int(raw_tf)
            except ValueError:
                log.warning(f"Unknown timeframe: {raw_tf!r}")
                return jsonify({"error": f"unknown tf {raw_tf}"}), 400

        # ── Parse timestamp ───────────────────────────────────────────────────
        raw_ts = data.get("ts")
        try:
            # TradingView sends {{time}} as Unix seconds (int)
            ts = pd.Timestamp(int(raw_ts), unit="s", tz="UTC")
        except Exception:
            try:
                ts = pd.to_datetime(str(raw_ts), utc=True)
            except Exception:
                ts = pd.Timestamp.now(tz="UTC").floor(f"{bar_min}min")

        # ── Parse OHLCV ───────────────────────────────────────────────────────
        o = float(data.get("o", data.get("open",  0)))
        h = float(data.get("h", data.get("high",  0)))
        l = float(data.get("l", data.get("low",   0)))
        c = float(data.get("c", data.get("close", 0)))
        v = float(data.get("v", data.get("volume",0)))

        if c <= 0:
            return jsonify({"error": "zero close price"}), 400

        _write_bar(sym, bar_min, ts, o, h, l, c, v)

        lag = (pd.Timestamp.now(tz="UTC") - ts).total_seconds()
        log.info(f"Bar: {sym} {bar_min}m  {ts.strftime('%H:%M')}  "
                 f"C={c:.2f}  V={int(v)}  lag={lag:.0f}s")

        return jsonify({"ok": True, "sym": sym, "bar_min": bar_min,
                        "ts": str(ts)}), 200

    except Exception as e:
        _errors += 1
        log.error(f"Handler error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "status": "running",
        "bars_received": _received,
        "errors": _errors,
        "bar_dir": str(BAR_DIR),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/test", methods=["GET", "POST"])
def test():
    """Quick connectivity test — visit http://VPS_IP:8765/test in browser."""
    return "Fortress TV Webhook OK — server is reachable.", 200


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Fortress TradingView Webhook Server")
    log.info(f"Listening on 0.0.0.0:8765")
    log.info(f"Parquets: {BAR_DIR}")
    log.info("Test URL: http://46.225.110.190:8765/test")
    log.info("Bar URL:  http://46.225.110.190:8765/bar  (POST)")
    log.info("Status:   http://46.225.110.190:8765/status")
    app.run(host="0.0.0.0", port=8765, threaded=True)
