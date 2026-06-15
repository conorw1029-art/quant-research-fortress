"""
tick_tradovate_live_feed.py — Tradovate L2 Live Data Feed
=========================================================
Connects to Tradovate's market data WebSocket, subscribes to DOM (order book)
+ tape (time & sales) for micro futures, and writes 1-minute JSONL bar files
in the same format as FortressBarWriter.cs — no NinjaTrader required.

tick_live_bar_reader.py picks up the JSONL files and appends to parquets,
so all 44 strategies (including V10 L2 strategies) get live data.

Requires:
    pip install websocket-client

Credentials (set as environment variables or add to start_fortress.bat):
    TV_USERNAME   — Tradovate / Lucid email
    TV_PASSWORD   — password
    TV_APP_ID     — app name shown in Tradovate dev portal (e.g. FortressFeed)
    TV_APP_VER    — version string (e.g. 1.0)
    TV_CID        — numeric client ID from Tradovate dev portal
    TV_SECRET     — client secret from Tradovate dev portal

Usage:
    python tick_tradovate_live_feed.py             # live account
    python tick_tradovate_live_feed.py --demo      # demo / paper account
    python tick_tradovate_live_feed.py --symbols GC SI   # GC + SI only
    python tick_tradovate_live_feed.py --verbose   # print every trade tick

Output files (one per symbol, appended every bar close):
    01_data/tick_bars/live/GC_1m_live.jsonl
    01_data/tick_bars/live/SI_1m_live.jsonl
    01_data/tick_bars/live/ES_1m_live.jsonl
    01_data/tick_bars/live/NQ_1m_live.jsonl
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
import urllib.request
import urllib.error

try:
    import websocket  # pip install websocket-client
except ImportError:
    print("ERROR: websocket-client not installed.")
    print("Run:   pip install websocket-client")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT     = Path(__file__).parent.parent
LIVE_DIR = ROOT / "01_data" / "tick_bars" / "live"
LIVE_DIR.mkdir(parents=True, exist_ok=True)

# ── Tradovate endpoints ────────────────────────────────────────────────────────

LIVE_REST = "https://live.tradovateapi.com/v1"
DEMO_REST = "https://demo.tradovateapi.com/v1"
MD_WS_URL = "wss://md.tradovateapi.com/v1/websocket"

# Sep 2026 front-month contracts → base symbol for output files
# Update when contracts roll (next roll ~Sep 19-26 2026)
ALL_CONTRACTS = {
    "MGCU5": "GC",   # Micro Gold
    "SILU5": "SI",   # Micro Silver
    "MESU5": "ES",   # Micro S&P 500
    "MNQU5": "NQ",   # Micro Nasdaq
}


# ── Bar accumulator ────────────────────────────────────────────────────────────

class BarState:
    """Accumulates tape + DOM events into completed 1-minute bars."""

    DOM_LEVELS = 5

    def __init__(self, base_symbol: str, verbose: bool = False):
        self.base_symbol = base_symbol
        self.verbose     = verbose
        self.bar_ts: Optional[datetime] = None
        self._cumcvd = 0
        self._prev_price = 0.0

        # DOM snapshot — updated on every subscribeDOM event
        self.bid_px = [0.0] * self.DOM_LEVELS
        self.ask_px = [0.0] * self.DOM_LEVELS
        self.bid_sz = [0.0] * self.DOM_LEVELS
        self.ask_sz = [0.0] * self.DOM_LEVELS

        # Current bar accumulators
        self.open = self.high = self.low = self.close = 0.0
        self.volume = 0
        self.buy_vol = 0
        self.sell_vol = 0
        self.n_trades = 0

    def on_trade(self, price: float, size: int, side: Optional[str], ts: datetime):
        bar_min = ts.replace(second=0, microsecond=0)

        if self.bar_ts is not None and bar_min > self.bar_ts:
            self._flush()

        if self.bar_ts is None or bar_min > self.bar_ts:
            self.bar_ts = bar_min
            self.open  = price
            self.high  = price
            self.low   = price
            self.volume    = 0
            self.buy_vol   = 0
            self.sell_vol  = 0
            self.n_trades  = 0

        if price > self.high: self.high = price
        if price < self.low:  self.low  = price
        self.close  = price
        self.volume += size
        self.n_trades += size

        # Aggressor side classification
        norm = (side or "").lower()
        if norm in ("buy", "bid"):
            self.buy_vol += size
        elif norm in ("sell", "ask"):
            self.sell_vol += size
        elif self._prev_price > 0:
            if price >= self._prev_price:
                self.buy_vol += size
            else:
                self.sell_vol += size

        self._prev_price = price

        if self.verbose:
            print(f"  tick {self.base_symbol} {price:.2f} x{size} {side or '?'}")

    def on_dom(self, bids: list, asks: list):
        """Replace DOM snapshot with latest levels."""
        for i in range(self.DOM_LEVELS):
            if i < len(bids):
                b = bids[i]
                self.bid_px[i] = float(b.get("price", b.get("px", 0)))
                self.bid_sz[i] = float(b.get("size", b.get("sz", 0)))
            else:
                self.bid_px[i] = self.bid_sz[i] = 0.0
            if i < len(asks):
                a = asks[i]
                self.ask_px[i] = float(a.get("price", a.get("px", 0)))
                self.ask_sz[i] = float(a.get("size", a.get("sz", 0)))
            else:
                self.ask_px[i] = self.ask_sz[i] = 0.0

    def flush_current(self):
        """Force-write in-progress bar (called on clean shutdown)."""
        if self.bar_ts and self.close > 0:
            self._flush()

    def _flush(self):
        """Write completed bar to JSONL."""
        if self.close == 0 or self.bar_ts is None:
            return

        row = self._build_row()
        path = LIVE_DIR / f"{self.base_symbol}_1m_live.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(f"  [Feed] {self.base_symbol} {row['ts']}  "
                  f"O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} C={row['close']:.2f} "
                  f"vol={row['volume']}  cvd={row['cvd_delta']:+d}  obi={row['obi_5']:.3f}")
        except Exception as e:
            print(f"  [Feed] Write error {self.base_symbol}: {e}")

    def _build_row(self) -> dict:
        bid0, ask0 = self.bid_px[0], self.ask_px[0]
        bsz0, asz0 = self.bid_sz[0], self.ask_sz[0]

        spread = (ask0 - bid0) if (ask0 > 0 and bid0 > 0) else 0.0

        tot1 = bsz0 + asz0
        bp   = (bsz0 - asz0) / tot1 if tot1 > 0 else 0.0

        bTot5 = sum(self.bid_sz)
        aTot5 = sum(self.ask_sz)
        tot5  = bTot5 + aTot5
        obi5  = (bTot5 - aTot5) / tot5 if tot5 > 0 else 0.0

        micro = (bid0 * asz0 + ask0 * bsz0) / tot1 if tot1 > 0 else ((bid0 + ask0) / 2.0 if bid0 > 0 else self.close)

        cvd_delta = self.buy_vol - self.sell_vol
        self._cumcvd += cvd_delta

        row: dict = {
            "ts":           self.bar_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open":         self.open,
            "high":         self.high,
            "low":          self.low,
            "close":        self.close,
            "volume":       self.volume,
            "buy_vol":      self.buy_vol,
            "sell_vol":     self.sell_vol,
            "cvd_delta":    cvd_delta,
            "cvd":          self._cumcvd,
            "spread":       round(spread, 6),
            "bid_sz_00":    int(bsz0),
            "ask_sz_00":    int(asz0),
            "book_pressure": round(bp, 6),
            "obi_5":        round(obi5, 6),
            "microprice":   round(micro, 6),
            "n_trades":     self.n_trades,
        }
        for i in range(self.DOM_LEVELS):
            row[f"bid_px_{i:02d}"] = self.bid_px[i]
            row[f"ask_px_{i:02d}"] = self.ask_px[i]
            row[f"bid_sz_{i:02d}"] = int(self.bid_sz[i])
            row[f"ask_sz_{i:02d}"] = int(self.ask_sz[i])

        return row


# ── Live feed ──────────────────────────────────────────────────────────────────

class TradovateLiveFeed:
    """
    Connects to Tradovate market data WebSocket.
    Subscribes to DOM + tape for each configured contract.
    Writes 1-minute JSONL bars to LIVE_DIR.
    """

    def __init__(self, demo: bool = True,
                 symbols: Optional[list[str]] = None,
                 verbose: bool = False):
        self.demo    = demo
        self.verbose = verbose
        self.rest_url = DEMO_REST if demo else LIVE_REST

        if symbols:
            self.contracts = {k: v for k, v in ALL_CONTRACTS.items()
                              if v in [s.upper() for s in symbols]}
        else:
            self.contracts = ALL_CONTRACTS.copy()

        # Bar state per base symbol
        self.bars: dict[str, BarState] = {
            base: BarState(base, verbose=verbose)
            for base in self.contracts.values()
        }

        # contract_id → BarState (populated in resolve_contract_ids)
        self.cid_to_bar: dict[int, BarState] = {}

        self.access_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self._ws: Optional[websocket.WebSocketApp] = None
        self._stop   = threading.Event()
        self._lock   = threading.Lock()
        self._subscribed = False

    # ── Auth ───────────────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        username = os.environ.get("TV_USERNAME", "")
        password = os.environ.get("TV_PASSWORD", "")
        app_id   = os.environ.get("TV_APP_ID", "FortressFeed")
        app_ver  = os.environ.get("TV_APP_VER", "1.0")
        cid      = int(os.environ.get("TV_CID", "0"))
        secret   = os.environ.get("TV_SECRET", "")

        if not username or not password:
            print("[Feed] ERROR: TV_USERNAME and TV_PASSWORD must be set.")
            print("       Add them to start_fortress.bat or set as env vars.")
            return False

        payload = {
            "name":       username,
            "password":   password,
            "appId":      app_id,
            "appVersion": app_ver,
            "cid":        cid,
            "sec":        secret,
            "deviceId":   "fortress-feed-001",
        }
        url  = self.rest_url + "/auth/accesstokenrequest"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"},
                                       method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            print(f"[Feed] Auth HTTP error {e.code}: {e.read().decode()}")
            return False
        except Exception as e:
            print(f"[Feed] Auth error: {e}")
            return False

        if "accessToken" not in body:
            print(f"[Feed] Auth failed: {body}")
            return False

        self.access_token = body["accessToken"]
        exp_ms = body.get("expirationTime", 86_400_000)
        self.token_expiry = time.time() + exp_ms / 1000 - 60
        acct = body.get("userId", "unknown")
        print(f"[Feed] Authenticated (user={acct}, "
              f"expires in {exp_ms // 60_000:.0f} min, "
              f"{'DEMO' if self.demo else 'LIVE'})")
        return True

    # ── Contract ID resolution ─────────────────────────────────────────────────

    def resolve_contract_ids(self):
        """Map each TV symbol to its numeric contract ID for WS event routing."""
        for tv_sym, base in self.contracts.items():
            url = self.rest_url + f"/contract/find?name={tv_sym}"
            req = urllib.request.Request(url, headers={
                "Authorization":  f"Bearer {self.access_token}",
                "Content-Type":   "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                cid = data.get("id") if isinstance(data, dict) else None
                if cid:
                    self.cid_to_bar[cid] = self.bars[base]
                    print(f"[Feed] {tv_sym} ({base}) → contractId={cid}")
                else:
                    print(f"[Feed] WARNING: no contract ID for {tv_sym}: {data}")
            except Exception as e:
                print(f"[Feed] WARNING: contract lookup failed for {tv_sym}: {e}")

    # ── WebSocket handlers ─────────────────────────────────────────────────────

    def _on_open(self, ws):
        print(f"[Feed] WebSocket open → authorizing...")
        self._subscribed = False
        msg = json.dumps([{"url": "authorize", "body": {"token": self.access_token}}])
        ws.send(msg)
        # Start heartbeat
        t = threading.Thread(target=self._heartbeat, args=(ws,), daemon=True)
        t.start()

    def _heartbeat(self, ws):
        """Tradovate WS requires [] heartbeat every ~2.5 s."""
        while not self._stop.is_set():
            try:
                ws.send("[]")
            except Exception:
                break
            time.sleep(2.5)

    def _on_message(self, ws, message: str):
        if message in ("o", "h"):
            return  # SockJS open / heartbeat

        if not message.startswith("a"):
            return

        try:
            events = json.loads(message[1:])
        except json.JSONDecodeError:
            return

        if not isinstance(events, list):
            events = [events]

        for ev in events:
            if not isinstance(ev, dict):
                continue

            e_type = ev.get("e", "")
            status = ev.get("s", 0)
            d      = ev.get("d", {})

            # Auth success — subscribe now
            if status == 200 and not self._subscribed:
                self._subscribed = True
                print("[Feed] Authorized. Subscribing to feeds...")
                self._subscribe_all(ws)
                continue

            if e_type == "dom":
                self._handle_dom(d)
            elif e_type == "tape":
                self._handle_tape(d)
            elif e_type == "clock":
                pass  # server time sync, ignore
            elif e_type and self.verbose:
                print(f"  [Feed] unhandled event: {e_type} {str(d)[:120]}")

    def _subscribe_all(self, ws):
        for tv_sym in self.contracts:
            body: dict = {"symbol": tv_sym}
            # Include contractId if resolved — helps Tradovate route faster
            for cid, bar in self.cid_to_bar.items():
                if bar.base_symbol == self.contracts[tv_sym]:
                    body["contractId"] = cid
                    break
            ws.send(json.dumps([{"url": "md/subscribeDOM",  "body": body}]))
            ws.send(json.dumps([{"url": "md/subscribeTape", "body": body}]))
            print(f"[Feed] Subscribed → {tv_sym}")

    def _handle_dom(self, d: dict):
        bar = self._bar_for(d)
        if bar is None:
            return
        bids = d.get("bids", [])
        asks = d.get("asks", [])
        with self._lock:
            bar.on_dom(bids, asks)

    def _handle_tape(self, d: dict):
        bar = self._bar_for(d)
        if bar is None:
            return

        price = float(d.get("price", 0))
        size  = int(d.get("size", 1))
        side  = d.get("aggressorSide") or d.get("side") or d.get("traderSide")

        ts_raw = (d.get("timestamp") or d.get("tradedate")
                  or d.get("tradingDate") or "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            ts = datetime.now(timezone.utc)

        if price <= 0:
            return

        with self._lock:
            bar.on_trade(price, size, side, ts)

    def _bar_for(self, d: dict) -> Optional[BarState]:
        """Find the BarState for an event dict, by contractId or symbol."""
        cid = d.get("contractId")
        if cid is not None:
            return self.cid_to_bar.get(cid)
        # Fallback: check if the event has a symbol field
        sym = d.get("symbol", "")
        base = ALL_CONTRACTS.get(sym.upper())
        if base:
            return self.bars.get(base)
        return None

    def _on_error(self, ws, error):
        print(f"[Feed] WebSocket error: {error}")

    def _on_close(self, ws, code, reason):
        print(f"[Feed] WebSocket closed: {code} {reason}")

    # ── Run loop ───────────────────────────────────────────────────────────────

    def run_forever(self):
        """Connect and stream, reconnecting automatically on disconnect."""
        print(f"[Feed] Watching: {', '.join(f'{v}({k})' for k, v in self.contracts.items())}")
        print(f"[Feed] Output:   {LIVE_DIR}")
        print()

        while not self._stop.is_set():
            # Re-auth if token expired
            if time.time() > self.token_expiry:
                if not self.authenticate():
                    print("[Feed] Auth failed — retrying in 30s")
                    time.sleep(30)
                    continue
                self.resolve_contract_ids()

            self._ws = websocket.WebSocketApp(
                MD_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[Feed] run_forever error: {e}")

            if not self._stop.is_set():
                print("[Feed] Reconnecting in 10s...")
                time.sleep(10)

        # Flush any in-progress bars
        with self._lock:
            for bar in self.bars.values():
                bar.flush_current()
        print("[Feed] Stopped.")

    def stop(self):
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tradovate L2 live feed — writes JSONL bars for the executor")
    parser.add_argument("--demo",    action="store_true",
                        help="Use demo/paper account (default: live)")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Base symbols to watch (e.g. GC SI). Default: all 4")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every individual trade tick")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    feed = TradovateLiveFeed(demo=args.demo,
                              symbols=args.symbols,
                              verbose=args.verbose)

    if not feed.authenticate():
        sys.exit(1)

    feed.resolve_contract_ids()
    feed.run_forever()


if __name__ == "__main__":
    main()
