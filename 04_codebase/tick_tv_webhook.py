#!/usr/bin/env python3
"""
tick_tv_webhook.py — TradingView alert webhook receiver
========================================================
Receives bar-close webhooks from TradingView and writes them into the
fortress parquet files, replacing the 15-20 minute delayed yfinance data.

TradingView sends a POST request with JSON body on every bar close.
We receive it, parse the OHLCV, compute synthetic CVD/L2, and update
the parquets that tick_live_executor.py reads.

Gives the executor real-time data (2-5 second delay from bar close)
without needing NinjaTrader, a Windows PC, or any other local software.

Listen: 0.0.0.0:8765  (firewall: ufw allow 8765/tcp)

DATA QUALITY MONITOR:
  http://46.225.110.190:8765/data-quality   ← live feed status
  http://46.225.110.190:8765/status         ← bar counts
  http://46.225.110.190:8765/test           ← connectivity check

TRADINGVIEW ALERT SETUP (do this once from iPad or browser):
─────────────────────────────────────────────────────────────
  TradingView plan required: Plus ($14.95/mo) or higher for webhooks.

  Step 1 — Open TradingView chart for the symbol/timeframe.
  Step 2 — Click the alert (clock) icon → Create Alert.
  Step 3 — Set:
      Condition:   Any indicator → On Bar Close
      Webhook URL: http://46.225.110.190:8765/bar
      Message:     paste the JSON below (copy exactly — no line breaks):

  {"sym":"{{ticker}}","tf":"{{interval}}","ts":"{{time}}","o":{{open}},"h":{{high}},"l":{{low}},"c":{{close}},"v":{{volume}}}

  Step 4 — Set alert to "Open-ended" (no expiry).
  Step 5 — Click Create.
  Step 6 — Repeat for each symbol × timeframe below.

ALERTS TO CREATE (20 total — 4 symbols × 5 timeframes):
─────────────────────────────────────────────────────────────
  Symbol in TradingView    Timeframe    Alert name
  COMEX:GC1!               1            GC_1m
  COMEX:GC1!               3            GC_3m
  COMEX:GC1!               5            GC_5m
  COMEX:GC1!               15           GC_15m
  COMEX:GC1!               30           GC_30m
  COMEX:SI1!               1            SI_1m
  COMEX:SI1!               3            SI_3m
  COMEX:SI1!               5            SI_5m
  COMEX:SI1!               15           SI_15m
  COMEX:SI1!               30           SI_30m
  CME_MINI:ES1!            1            ES_1m
  CME_MINI:ES1!            3            ES_3m
  CME_MINI:ES1!            5            ES_5m
  CME_MINI:ES1!            15           ES_15m
  CME_MINI:ES1!            30           ES_30m
  CME_MINI:NQ1!            1            NQ_1m
  CME_MINI:NQ1!            3            NQ_3m
  CME_MINI:NQ1!            5            NQ_5m
  CME_MINI:NQ1!            15           NQ_15m
  CME_MINI:NQ1!            30           NQ_30m

OPTIONAL AUTH (add TV_WEBHOOK_TOKEN to .env to require ?token=XXX on requests):
  TV_WEBHOOK_TOKEN=your_secret_here
  Then use webhook URL: http://46.225.110.190:8765/bar?token=your_secret_here

TELEGRAM NOTIFICATIONS:
  Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in /opt/fortress/.env.
  First bar received per symbol triggers a Telegram alert.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests as http_requests
from flask import Flask, request, jsonify

# ── Paths ──────────────────────────────────────────────────────────────────────
BAR_DIR  = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = Path("/opt/fortress/.env")
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

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── Config from env ────────────────────────────────────────────────────────────
TV_TOKEN        = os.environ.get("TV_WEBHOOK_TOKEN", "")       # optional auth
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── TradingView ticker → fortress base symbol ──────────────────────────────────
TV_SYM_MAP = {
    "GC1!": "GC",  "COMEX:GC1!": "GC",  "GC": "GC",
    "SI1!": "SI",  "COMEX:SI1!": "SI",  "SI": "SI",
    "ES1!": "ES",  "CME_MINI:ES1!": "ES", "CME:ES1!": "ES", "ES": "ES",
    "NQ1!": "NQ",  "CME_MINI:NQ1!": "NQ", "CME:NQ1!": "NQ", "NQ": "NQ",
    # Micro symbols also accepted
    "MGC1!": "GC", "COMEX:MGC1!": "GC",
    "MES1!": "ES", "CME_MINI:MES1!": "ES",
    "MNQ1!": "NQ", "CME_MINI:MNQ1!": "NQ",
    "SIL1!": "SI", "COMEX:SIL1!": "SI",
}

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

# ── Stats + data quality tracking ─────────────────────────────────────────────
_received:          dict[str, int]              = {}   # key → bar count
_last_bar_time:     dict[str, pd.Timestamp]     = {}   # key → last bar ts (bar's own timestamp)
_last_arrival:      dict[str, datetime]         = {}   # key → wall-clock time bar arrived
_first_notified:    set[str]                    = set() # symbols already notified
_errors:            int = 0

# ── Telegram ──────────────────────────────────────────────────────────────────
def _telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": ""},
            timeout=5,
        )
    except Exception:
        pass

# ── Synthetic CVD from OHLCV ──────────────────────────────────────────────────
def _synthetic_cvd_delta(o: float, h: float, l: float, c: float, v: float) -> int:
    rng = h - l
    if rng < 1e-9:
        return 0
    buy_frac  = (c - l) / rng
    sell_frac = (h - c) / rng
    return int(v * buy_frac) - int(v * sell_frac)

# ── Synthetic L2 fields ────────────────────────────────────────────────────────
def _synthetic_l2(buy_v: float, sel_v: float, h: float, l: float, c: float) -> dict:
    obi    = (buy_v - sel_v) / (buy_v + sel_v + 1)
    spread = (h - l) * 0.05
    return {
        "spread": spread, "bid_sz_00": float(buy_v), "ask_sz_00": float(sel_v),
        "book_pressure": obi, "obi_5": obi, "microprice": c,
        "imbal_L5_last": obi, "microprice_last": c,
        "spread_mean": spread, "bid_sz_mean": float(buy_v), "ask_sz_mean": float(sel_v),
    }

# ── Core: write one bar to parquet ────────────────────────────────────────────
def _write_bar(sym: str, bar_min: int, ts: pd.Timestamp,
               o: float, h: float, l: float, c: float, v: float):
    cvd_delta = _synthetic_cvd_delta(o, h, l, c, v)
    buy_vol   = max(0, int(v * ((c - l) / (h - l + 1e-9))))
    sell_vol  = max(0, int(v) - buy_vol)

    row = {
        "open": o, "high": h, "low": l, "close": c, "volume": int(v),
        "buy_vol": buy_vol, "sell_vol": sell_vol,
        "cvd_delta": cvd_delta, "cvd": 0, "n_trades": int(v),
        **_synthetic_l2(buy_vol, sell_vol, h, l, c),
    }
    new_df = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="ts"))

    pq_path = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    l2_path = BAR_DIR / f"{sym}_bars_l2_{bar_min}m.parquet"

    with _lock(f"{sym}_{bar_min}"):
        for path in (pq_path, l2_path):
            _upsert(path, new_df)

    key = f"{sym}/{bar_min}m"
    _received[key]       = _received.get(key, 0) + 1
    _last_bar_time[key]  = ts
    _last_arrival[key]   = datetime.now(timezone.utc)

    # Telegram once per symbol (not per tf — too noisy)
    if sym not in _first_notified:
        _first_notified.add(sym)
        _telegram(
            f"FORTRESS TV LIVE\n"
            f"{sym} {bar_min}m  C={c:.2f}  V={int(v)}\n"
            f"Real-time data is flowing! 20 alerts = no more delay."
        )
        log.info(f"FIRST BAR for {sym} — Telegram notified")


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
            combined["cvd"] = combined["cvd_delta"].cumsum()
        except Exception:
            combined = new_df
    else:
        combined = new_df
        combined["cvd"] = combined["cvd_delta"].cumsum()
    combined.to_parquet(path, engine="pyarrow", compression="snappy")


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


def _check_token() -> bool:
    """Return True if auth passes (or auth is disabled)."""
    if not TV_TOKEN:
        return True
    q_token  = request.args.get("token", "")
    auth_hdr = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return q_token == TV_TOKEN or auth_hdr == TV_TOKEN


@app.route("/bar", methods=["POST"])
def receive_bar():
    global _errors
    if not _check_token():
        return jsonify({"error": "unauthorized"}), 401

    try:
        raw_body = request.data
        # Normalize Unicode smart/curly quotes → ASCII straight quotes.
        # TradingView's alert message editor autocorrects "..." → "..." so
        # the closing quote of values becomes U+201D (\xe2\x80\x9d) instead of
        # the ASCII " (0x22), producing invalid JSON.
        raw_body = (raw_body
            .replace(b'\xe2\x80\x9c', b'"')   # " LEFT DOUBLE QUOTATION MARK
            .replace(b'\xe2\x80\x9d', b'"')   # " RIGHT DOUBLE QUOTATION MARK
            .replace(b'\xe2\x80\x98', b"'")   # ' LEFT SINGLE QUOTATION MARK
            .replace(b'\xe2\x80\x99', b"'"))  # ' RIGHT SINGLE QUOTATION MARK

        data = None
        try:
            data = json.loads(raw_body)
        except Exception:
            pass

        if data is None:
            try:
                # Also strip newlines/spaces embedded in JSON key names
                # (copy-paste from some editors wraps key text with line breaks).
                import re as _re
                cleaned = _re.sub(
                    rb'"([^"]{0,40})"',
                    lambda m: b'"' + m.group(1).replace(b'\n', b'').replace(b'\r', b'').strip() + b'"',
                    raw_body,
                )
                data = json.loads(cleaned)
            except Exception:
                _errors += 1
                log.error(f"PARSE_FAIL body={raw_body[:300]!r}")
                return jsonify({"error": "invalid JSON"}), 400

        # ── Parse symbol ──────────────────────────────────────────────────────
        raw_sym = str(data.get("sym", "")).strip().upper()
        if ":" in raw_sym:
            raw_sym = raw_sym.split(":")[-1]
        sym = TV_SYM_MAP.get(raw_sym) or TV_SYM_MAP.get(raw_sym.split(":")[0])
        if sym is None:
            stripped = raw_sym.rstrip("!1234567890")
            sym = TV_SYM_MAP.get(stripped)
        if sym is None:
            log.warning(f"Unknown symbol: {data.get('sym')!r}")
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
        # TradingView sends {{time}} as ISO-8601 string ("2026-06-25T17:52:00Z")
        # or as Unix seconds (integer). Handle both.
        raw_ts = data.get("ts")
        ts = None
        if raw_ts is not None:
            try:
                ts = pd.to_datetime(str(raw_ts), utc=True)
            except Exception:
                pass
        if ts is None:
            try:
                ts = pd.Timestamp(int(raw_ts), unit="s", tz="UTC")
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

        return jsonify({"ok": True, "sym": sym, "bar_min": bar_min, "ts": str(ts)}), 200

    except Exception as e:
        _errors += 1
        log.error(f"Handler error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/data-quality", methods=["GET"])
def data_quality():
    """Per-symbol/tf feed health — shows last bar time and whether alerts are arriving."""
    now_dt  = datetime.now(timezone.utc)
    now_ts  = pd.Timestamp.now(tz="UTC")
    quality = {}
    for key in sorted(set(_last_bar_time) | set(_last_arrival)):
        ts       = _last_bar_time.get(key)
        arrived  = _last_arrival.get(key)
        bar_min  = int(key.split("/")[1].replace("m", ""))
        # is_live = alert arrived recently (2× bar period + 2min grace)
        # Use arrival time so 10-min delayed data still shows as "live"
        if arrived is not None:
            since_arrival = (now_dt - arrived).total_seconds()
            is_live = since_arrival < (bar_min * 60 * 2 + 120)
        else:
            is_live = False
        data_lag = round((now_ts - ts).total_seconds()) if ts else None
        quality[key] = {
            "last_bar":   ts.strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "never",
            "data_lag_s": data_lag,
            "since_recv": round((now_dt - arrived).total_seconds()) if arrived else None,
            "is_live":    is_live,
            "bars_recv":  _received.get(key, 0),
        }

    live_count = sum(1 for v in quality.values() if v["is_live"])
    total      = len(quality)
    if total == 0:
        overall = "no_data"
    elif live_count >= 16:
        overall = "live"     # all 20 alerts flowing
    elif live_count >= 4:
        overall = "partial"  # some symbols flowing
    else:
        overall = "degraded"

    return jsonify({
        "overall":     overall,
        "live_feeds":  f"{live_count}/{total}",
        "quality":     quality,
        "errors":      _errors,
        "time_utc":    now_dt.isoformat(),
        "setup_url":   "See tick_tv_webhook.py docstring for alert setup",
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "status":        "running",
        "bars_received": _received,
        "errors":        _errors,
        "bar_dir":       str(BAR_DIR),
        "auth_enabled":  bool(TV_TOKEN),
        "time_utc":      datetime.now(timezone.utc).isoformat(),
    })


@app.route("/test", methods=["GET", "POST"])
def test():
    """Quick connectivity test — visit http://46.225.110.190:8765/test in browser."""
    return "Fortress TV Webhook OK — server is reachable.", 200


@app.route("/ping", methods=["GET", "POST"])
def ping():
    return jsonify({"pong": True, "time_utc": datetime.now(timezone.utc).isoformat()})


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Fortress TradingView Webhook Server v2")
    log.info(f"Listening on 0.0.0.0:8765")
    log.info(f"Parquets: {BAR_DIR}")
    log.info(f"Auth token: {'enabled' if TV_TOKEN else 'disabled (open)'}")
    log.info(f"Telegram: {'enabled' if TELEGRAM_TOKEN else 'disabled'}")
    log.info("")
    log.info("Endpoints:")
    log.info("  POST /bar           — receive bar from TradingView")
    log.info("  GET  /data-quality  — live feed health per symbol/tf")
    log.info("  GET  /status        — bar counts")
    log.info("  GET  /test          — connectivity check")
    log.info("")
    log.info("SETUP: Create 20 TradingView alerts (one per symbol × timeframe)")
    log.info('  Webhook URL: http://46.225.110.190:8765/bar')
    log.info('  Message:     {"sym":"{{ticker}}","tf":"{{interval}}","ts":"{{time}}","o":{{open}},"h":{{high}},"l":{{low}},"c":{{close}},"v":{{volume}}}')
    app.run(host="0.0.0.0", port=8765, threaded=True)
