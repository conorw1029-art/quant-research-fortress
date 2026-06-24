#!/usr/bin/env python3
"""
tick_tradovate_streamer.py — Real-time bar data from Tradovate market data API
==============================================================================
Replaces yfinance (15-20 min delay) and TradingView webhooks (manual setup).
Uses the same Tradovate account already needed for order execution — zero
additional cost or manual steps once CID/secret are in .env.

DATA LATENCY:
  yfinance updater    : 15-20 minutes stale
  TradingView webhook : 2-5 seconds (but requires 20 manual alerts to set up)
  This service        : 15-30 seconds (fully automatic, zero manual setup)

HOW IT WORKS:
  1. Authenticates with Tradovate REST API (same as trading client)
  2. Polls /md/getquotesnapshot every POLL_INTERVAL seconds for current bid/ask/last
  3. Accumulates ticks into OHLCV bars aligned to bar boundaries (1m, 3m, 5m, 15m, 30m)
  4. On bar close, writes completed bar to parquets + computes synthetic CVD
  5. Replaces the parquet row if it already exists (dedup by timestamp)

CREDENTIALS: reads from environment or .env file (same keys as trading):
  TV_USERNAME  — Tradovate account email
  TV_PASSWORD  — Tradovate account password
  TV_CID       — OAuth2 client ID (from prop firm / Tradovate settings)
  TV_SECRET    — OAuth2 secret
  TV_DEMO      — "true" for demo account, "false" for live (default: false)

RUN:
  python3 tick_tradovate_streamer.py            # one-shot: update and exit
  python3 tick_tradovate_streamer.py --loop     # run forever (production mode)
  systemctl start fortress-tradovate-streamer   # via systemd (auto-started on boot)

UPGRADE PATH:
  Once this service is stable, disable fortress-yfinance service:
    systemctl disable --now fortress-yfinance
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
BAR_DIR = Path("/opt/fortress/01_data/tick_bars")
if not BAR_DIR.exists():
    BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = ROOT / ".env"

# ── Logging ───────────────────────────────────────────────────────────────────
import logging

LOG_FILE = BAR_DIR.parent / "logs" / "tradovate_streamer.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TVStream] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL  = 15          # seconds between quote polls
TIMEFRAMES     = [1, 3, 5, 15, 30]   # bar sizes to build
HISTORY_BARS   = 200         # bars to backfill on startup

# Micro contracts to poll (price identical to full-size, same for OHLCV purposes)
# Maps fortress base symbol → Tradovate contract name
CONTRACTS = {
    "GC": "MGCU6",   # micro gold Sep 2026
    "SI": "SILU6",   # micro silver Sep 2026
    "ES": "MESU6",   # micro S&P Sep 2026
    "NQ": "MNQU6",   # micro NQ Sep 2026
}

TRADOVATE_LIVE_URL = "https://live.tradovateapi.com/v1"
TRADOVATE_DEMO_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_MD_URL   = "https://md.tradovateapi.com/v1"


# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Tradovate auth + quote fetcher ────────────────────────────────────────────
import urllib.request
import urllib.error

class TradovateQuoteFetcher:
    """
    Authenticates with Tradovate and polls /md/getquotesnapshot.
    Thin wrapper — only fetches what this script needs.
    """

    def __init__(self):
        self.username  = os.environ.get("TV_USERNAME", "")
        self.password  = os.environ.get("TV_PASSWORD", "")
        self.cid       = int(os.environ.get("TV_CID", "0"))
        self.secret    = os.environ.get("TV_SECRET", "")
        self.demo      = os.environ.get("TV_DEMO", "false").lower() == "true"
        self.base_url  = TRADOVATE_DEMO_URL if self.demo else TRADOVATE_LIVE_URL

        self.access_token: Optional[str] = None
        self.token_expires: float = 0.0
        self._lock = threading.Lock()

        # Contract name → contract ID cache
        self._contract_id_cache: dict[str, int] = {}

    def is_configured(self) -> bool:
        return bool(self.username and self.password and self.cid and self.secret)

    def authenticate(self) -> bool:
        if not self.is_configured():
            log.error("Tradovate credentials not set. Check TV_USERNAME/TV_PASSWORD/TV_CID/TV_SECRET in .env")
            return False
        try:
            payload = {
                "name":       self.username,
                "password":   self.password,
                "appId":      "FortressTrader",
                "appVersion": "1.0",
                "cid":        self.cid,
                "sec":        self.secret,
            }
            resp = self._post("/auth/accesstokenrequest", payload, auth=False)
            self.access_token  = resp.get("accessToken")
            expiry_ms = resp.get("expirationTime", 0)
            if expiry_ms:
                self.token_expires = expiry_ms / 1000.0
            else:
                self.token_expires = time.time() + 3600  # 1h fallback
            log.info(f"Authenticated as {self.username} ({'demo' if self.demo else 'live'})")
            return bool(self.access_token)
        except Exception as e:
            log.error(f"Auth failed: {e}")
            return False

    def _ensure_auth(self):
        if not self.access_token or time.time() > self.token_expires - 60:
            self.authenticate()

    def _post(self, endpoint: str, payload: dict, auth: bool = True) -> dict:
        url  = self.base_url + endpoint
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _get(self, endpoint: str, base: str = None, **params) -> dict | list:
        base_url = base or self.base_url
        url = base_url + endpoint
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url += "?" + qs
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def get_contract_id(self, symbol: str) -> Optional[int]:
        if symbol in self._contract_id_cache:
            return self._contract_id_cache[symbol]
        self._ensure_auth()
        try:
            result = self._get(f"/contract/find", name=symbol)
            if isinstance(result, dict):
                cid = result.get("id")
                if cid:
                    self._contract_id_cache[symbol] = cid
                    return cid
        except Exception as e:
            log.warning(f"get_contract_id({symbol}): {e}")
        return None

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Return latest quote dict with keys: last, bid, ask, totalVolume, timestamp."""
        self._ensure_auth()
        try:
            contract_id = self.get_contract_id(symbol)
            if not contract_id:
                return None
            result = self._get("/md/getquotesnapshot",
                               base=TRADOVATE_MD_URL,
                               contractIds=contract_id)
            if isinstance(result, list) and result:
                return result[0]
            if isinstance(result, dict):
                return result
        except Exception as e:
            log.warning(f"get_quote({symbol}): {e}")
        return None

    def get_chart_bars(self, symbol: str, bar_min: int, n_bars: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetch last n_bars of bar data via Tradovate chart endpoint.
        Returns DataFrame with columns: open, high, low, close, volume, indexed by UTC timestamp.
        Returns None if endpoint unavailable.
        """
        self._ensure_auth()
        try:
            contract_id = self.get_contract_id(symbol)
            if not contract_id:
                return None
            payload = {
                "symbol": symbol,
                "chartDescription": {
                    "underlyingType": "Minute",
                    "value": bar_min,
                    "volumeType": "Up",
                },
                "timeRange": {
                    "asMuchAsElements": n_bars,
                },
            }
            result = self._post("/md/getchart", payload, auth=True)
            bars = result.get("bars") or result.get("data", [])
            if not bars:
                return None

            rows = []
            for b in bars:
                ts  = pd.to_datetime(b.get("timestamp") or b.get("ts"), utc=True)
                rows.append({
                    "ts":     ts,
                    "open":   float(b.get("open",  b.get("o", 0))),
                    "high":   float(b.get("high",  b.get("h", 0))),
                    "low":    float(b.get("low",   b.get("l", 0))),
                    "close":  float(b.get("close", b.get("c", 0))),
                    "volume": int(b.get("volume",  b.get("v", 0))),
                })

            df = pd.DataFrame(rows).set_index("ts")
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "ts"
            return df.sort_index()
        except Exception as e:
            log.debug(f"get_chart_bars({symbol},{bar_min}m): {e}")
            return None


# ── Bar builder: accumulates quote ticks into OHLCV bars ──────────────────────
class BarBuilder:
    """
    Accumulates price ticks (from quote polls) into aligned OHLCV bars.
    Flushes (returns) a completed bar when the bar boundary is crossed.
    """

    def __init__(self, sym: str, bar_min: int):
        self.sym     = sym
        self.bar_min = bar_min
        self._bar_start: Optional[pd.Timestamp] = None
        self._open = self._high = self._low = self._close = 0.0
        self._volume   = 0
        self._prev_tot = 0   # previous total day volume (for delta)

    def _bar_floor(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC").floor(f"{self.bar_min}min")

    def feed(self, price: float, total_volume: int) -> Optional[dict]:
        """
        Feed a new price snapshot. Returns completed bar dict if bar just closed,
        otherwise returns None.
        """
        if price <= 0:
            return None

        bar_start = self._bar_floor()

        # Volume delta since last tick
        vol_delta = max(0, total_volume - self._prev_tot)
        self._prev_tot = total_volume

        if self._bar_start is None:
            # First tick ever
            self._bar_start = bar_start
            self._open = self._high = self._low = self._close = price
            self._volume = vol_delta
            return None

        if bar_start > self._bar_start:
            # Bar boundary crossed — flush completed bar
            completed = {
                "ts":     self._bar_start,
                "open":   self._open,
                "high":   self._high,
                "low":    self._low,
                "close":  self._close,
                "volume": self._volume,
            }
            # Start new bar
            self._bar_start = bar_start
            self._open = self._high = self._low = self._close = price
            self._volume = vol_delta
            return completed

        # Same bar — update
        self._high  = max(self._high, price)
        self._low   = min(self._low,  price)
        self._close = price
        self._volume += vol_delta
        return None


# ── Parquet writer (same logic as TV webhook) ─────────────────────────────────
_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_mu:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _synthetic_cvd(o: float, h: float, l: float, c: float, v: int) -> int:
    rng = h - l
    if rng < 1e-9:
        return 0
    return int(v * ((c - l) / rng)) - int(v * ((h - c) / rng))


def _write_bar_to_parquet(sym: str, bar_min: int, bar: dict):
    """Write a completed bar dict to parquets (standard + L2)."""
    ts  = bar["ts"]
    o, h, l, c, v = bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]

    cvd   = _synthetic_cvd(o, h, l, c, v)
    buy_v = max(0, int(v * ((c - l) / (h - l + 1e-9))))
    sel_v = max(0, v - buy_v)

    row = {
        "open":      o,  "high":      h,  "low":       l,  "close":     c,
        "volume":    v,
        "buy_vol":   buy_v, "sell_vol": sel_v, "cvd_delta": cvd, "cvd": 0,
        "n_trades":  v,
        "spread": 0, "bid_sz_00": 0, "ask_sz_00": 0,
        "book_pressure": 0, "obi_5": 0, "microprice": c,
        "imbal_L5_last": 0, "microprice_last": c,
        "spread_mean": 0, "bid_sz_mean": 0, "ask_sz_mean": 0,
    }
    new_df = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="ts"))

    pq  = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    l2  = BAR_DIR / f"{sym}_bars_l2_{bar_min}m.parquet"

    with _lock(f"{sym}_{bar_min}"):
        for path in (pq, l2):
            _upsert(path, new_df)

    log.info(f"Bar: {sym} {bar_min}m  {ts.strftime('%H:%M')}  "
             f"C={c:.2f}  V={v}  CVD={cvd:+d}")


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


def _write_historical_bars(sym: str, bar_min: int, df: pd.DataFrame):
    """Write a DataFrame of historical bars (from get_chart_bars) to parquets."""
    for ts, row in df.iterrows():
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]
        v = int(row.get("volume", 0))
        _write_bar_to_parquet(sym, bar_min, {
            "ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v
        })


# ── Main streaming loop ───────────────────────────────────────────────────────

def run_once(fetcher: TradovateQuoteFetcher):
    """
    Single poll pass: fetch one quote per symbol, feed into all timeframe builders.
    Returns dict of {sym: price} for logging.
    """
    prices = {}
    for base_sym, contract in CONTRACTS.items():
        try:
            q = fetcher.get_quote(contract)
            if not q:
                continue
            price = float(q.get("last") or q.get("ask") or q.get("bid") or 0)
            total_vol = int(q.get("totalVolume") or q.get("volume") or 0)
            if price <= 0:
                continue
            prices[base_sym] = price

            for tf in TIMEFRAMES:
                key = f"{base_sym}_{tf}"
                completed = _builders[key].feed(price, total_vol)
                if completed:
                    _write_bar_to_parquet(base_sym, tf, completed)

        except Exception as e:
            log.warning(f"Poll error {base_sym}: {e}")

    return prices


def backfill(fetcher: TradovateQuoteFetcher):
    """
    Try to fetch historical bars from Tradovate chart endpoint.
    Falls back silently if endpoint isn't available.
    """
    log.info("Attempting historical backfill from Tradovate chart endpoint...")
    any_success = False
    for base_sym, contract in CONTRACTS.items():
        for tf in TIMEFRAMES:
            try:
                df = fetcher.get_chart_bars(contract, tf, n_bars=HISTORY_BARS)
                if df is not None and len(df) > 0:
                    log.info(f"  Backfill {base_sym} {tf}m: {len(df)} bars")
                    _write_historical_bars(base_sym, tf, df)
                    any_success = True
                else:
                    log.debug(f"  No chart data for {base_sym} {tf}m (endpoint may need subscription)")
            except Exception as e:
                log.debug(f"  Chart backfill {base_sym} {tf}m: {e}")
    if not any_success:
        log.info("  Chart backfill unavailable (credentials or endpoint not supported) — "
                 "relying on live quote polling only")


def main():
    parser = argparse.ArgumentParser(description="Tradovate real-time bar streamer")
    parser.add_argument("--loop",    action="store_true", help="Run forever")
    parser.add_argument("--no-backfill", action="store_true", help="Skip historical backfill")
    args = parser.parse_args()

    fetcher = TradovateQuoteFetcher()

    if not fetcher.is_configured():
        log.error(
            "Tradovate credentials not configured.\n"
            "  Set in .env:\n"
            "    TV_USERNAME=your@email.com\n"
            "    TV_PASSWORD=yourpassword\n"
            "    TV_CID=12345\n"
            "    TV_SECRET=your_secret\n"
            "  Get CID/secret from Tradeify or TakeProfitTrader chat support.\n"
            "  (Same credentials as needed for order execution.)"
        )
        sys.exit(1)

    if not fetcher.authenticate():
        log.error("Authentication failed — check credentials in .env")
        sys.exit(1)

    # Optional: backfill historical bars from chart endpoint
    if not args.no_backfill:
        backfill(fetcher)

    if not args.loop:
        # One-shot: just poll quotes once
        prices = run_once(fetcher)
        log.info(f"Quotes: {prices}")
        return

    # Continuous loop
    log.info(f"Streaming live. Polling every {POLL_INTERVAL}s × "
             f"{len(CONTRACTS)} symbols × {len(TIMEFRAMES)} timeframes")
    log.info(f"Parquets: {BAR_DIR}")

    last_auth_refresh = time.time()
    poll_count = 0

    while True:
        try:
            # Re-auth every 50 minutes (tokens expire in ~60 min)
            if time.time() - last_auth_refresh > 3000:
                fetcher.authenticate()
                last_auth_refresh = time.time()

            prices = run_once(fetcher)
            poll_count += 1

            if poll_count % 20 == 0:   # log prices every 5 minutes
                price_str = "  ".join(f"{s}={p:.2f}" for s, p in prices.items())
                log.info(f"Poll #{poll_count}  [{price_str}]")

        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)


# ── Global bar builders (one per symbol × timeframe) ─────────────────────────
_builders: dict[str, BarBuilder] = {
    f"{sym}_{tf}": BarBuilder(sym, tf)
    for sym in CONTRACTS
    for tf in TIMEFRAMES
}


if __name__ == "__main__":
    main()
