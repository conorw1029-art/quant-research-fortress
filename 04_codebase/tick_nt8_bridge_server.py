#!/usr/bin/env python3
"""
tick_nt8_bridge_server.py — VPS TCP server that receives bars from NT8
=======================================================================
Receives real-time OHLCV+CVD+DOM bars pushed by FortressBarWriter.cs
running inside NinjaTrader 8 on Windows, and merges them into the
fortress parquet files.

This replaces tick_yfinance_updater.py as the real-time data source.
Once running, tick_live_executor.py gets fresh bars every minute (real-time)
instead of 15-20 minute delayed yfinance data.

What this unlocks:
  - OHLCV strategies: real signals instead of delayed signals
  - CVD/buy_vol/sell_vol: real tick data instead of zeros
  - L2/DOM strategies (V10): real obi_5, microprice, imbal_L5_last data

Run:
    python3 tick_nt8_bridge_server.py
    (or: systemctl start fortress-nt8-bridge)

Listen port: 9876  (open this on your VPS firewall)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
BAR_DIR = Path("/opt/fortress/01_data/tick_bars")
if not BAR_DIR.exists():
    BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

LIVE_DIR = BAR_DIR / "live"
LIVE_DIR.mkdir(parents=True, exist_ok=True)

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9876

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Bridge] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Per-symbol write locks (parquet can only have one writer at a time) ────────
_parquet_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_mu:
        if key not in _parquet_locks:
            _parquet_locks[key] = threading.Lock()
        return _parquet_locks[key]


# ── Stats (for status logging) ─────────────────────────────────────────────────
_stats: dict[str, int] = {}
_stats_mu = threading.Lock()


def _incr(key: str):
    with _stats_mu:
        _stats[key] = _stats.get(key, 0) + 1


# ── Core: merge one bar into parquet ──────────────────────────────────────────

def _merge_bar(sym: str, bar_min: str, bar: dict):
    """Merge a single received bar into the parquet file."""
    ts_str = bar.get("ts", "")
    if not ts_str:
        return

    try:
        ts = pd.to_datetime(ts_str, utc=True)
    except Exception:
        return

    # Build a one-row DataFrame
    row = {
        "open":   float(bar.get("open",   0)),
        "high":   float(bar.get("high",   0)),
        "low":    float(bar.get("low",    0)),
        "close":  float(bar.get("close",  0)),
        "volume": int(bar.get("volume",   0)),
        "buy_vol":   int(bar.get("buy_vol",   0)),
        "sell_vol":  int(bar.get("sell_vol",  0)),
        "cvd_delta": int(bar.get("cvd_delta", 0)),
        "cvd":       int(bar.get("cvd",       0)),
        "n_trades":  int(bar.get("n_trades",  0)),
        # L2 fields
        "spread":        float(bar.get("spread",        0)),
        "bid_sz_00":     float(bar.get("bid_sz_00",     0)),
        "ask_sz_00":     float(bar.get("ask_sz_00",     0)),
        "book_pressure": float(bar.get("book_pressure", 0)),
        "obi_5":         float(bar.get("obi_5",         0)),
        "microprice":    float(bar.get("microprice",    0)),
        # L2 aliases used by V10 strategies
        "imbal_L5_last":  float(bar.get("obi_5",      0)),
        "microprice_last":float(bar.get("microprice",  0)),
        "spread_mean":    float(bar.get("spread",      0)),
        "bid_sz_mean":    float(bar.get("bid_sz_00",   0)),
        "ask_sz_mean":    float(bar.get("ask_sz_00",   0)),
    }
    # Add DOM levels
    for i in range(5):
        for side in ("bid", "ask"):
            for typ in ("px", "sz"):
                col = f"{side}_{typ}_{i:02d}"
                row[col] = float(bar.get(col, 0))

    new_df = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="ts"))

    # ── Merge into standard parquet ───────────────────────────────────────────
    pq_path = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    l2_path = BAR_DIR / f"{sym}_bars_l2_{bar_min}m.parquet"

    lock = _get_lock(f"{sym}_{bar_min}")
    with lock:
        for path in (pq_path, l2_path):
            _upsert_row(path, new_df)

    # Also write to JSONL backup
    jsonl_path = LIVE_DIR / f"{sym}_{bar_min}m_live.jsonl"
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(bar) + "\n")
    except Exception:
        pass

    _incr(f"{sym}/{bar_min}m")


def _upsert_row(path: Path, new_df: pd.DataFrame):
    """Append new_df to parquet at path, replacing any row with the same timestamp."""
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            existing.index = pd.to_datetime(existing.index, utc=True)
            # Add missing cols
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

    combined.to_parquet(path, engine="pyarrow", compression="snappy")


# ── TCP connection handler ─────────────────────────────────────────────────────

class BarHandler(socketserver.BaseRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        log.info(f"NT8 connected from {peer}")
        buf = b""
        sym_label = "?"
        try:
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # Process complete lines
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type", "bar")
                    if msg_type == "hello":
                        sym_label = f"{msg.get('sym','?')}/{msg.get('bar_min','?')}m"
                        log.info(f"  Feed registered: {sym_label} from {peer}")
                        continue

                    if msg_type == "bar":
                        sym     = msg.get("sym", "").upper()
                        bar_min = str(msg.get("bar_min", "")).replace("m", "")
                        if sym and bar_min.isdigit():
                            _merge_bar(sym, bar_min, msg)
        except Exception as e:
            log.warning(f"NT8 {sym_label} connection error: {e}")
        finally:
            log.info(f"NT8 {sym_label} disconnected ({peer})")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ── Status reporter ───────────────────────────────────────────────────────────

def _status_loop():
    while True:
        time.sleep(60)
        with _stats_mu:
            if _stats:
                summary = "  ".join(f"{k}:{v}" for k, v in sorted(_stats.items()))
                log.info(f"Bars received (last 60s): {summary}")
                _stats.clear()
            else:
                log.info("No bars received in last 60s — is NT8 running and connected?")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log.info(f"Fortress NT8 Bridge Server starting on {LISTEN_HOST}:{LISTEN_PORT}")
    log.info(f"Bar parquets: {BAR_DIR}")
    log.info(f"Live JSONL backup: {LIVE_DIR}")
    log.info("Waiting for NT8 FortressBarWriter connections...")

    threading.Thread(target=_status_loop, daemon=True).start()

    with ThreadedTCPServer((LISTEN_HOST, LISTEN_PORT), BarHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Stopped.")


if __name__ == "__main__":
    main()
