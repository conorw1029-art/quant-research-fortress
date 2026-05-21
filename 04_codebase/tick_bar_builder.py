"""
tick_bar_builder.py — Live Bar Builder via Tradovate WebSocket
==============================================================
Connects to Tradovate's market data WebSocket, subscribes to real-time quote
and DOM data for all portfolio symbols, aggregates ticks into N-minute bars
with full L2 features, and appends bars to existing parquet files used by
tick_live_executor.py.

L2 features computed per bar:
  OHLCV, buy_vol, sell_vol, cvd_delta, cvd (cumulative), n_trades,
  trade_rate, large_buys, large_sells
  GC/SI only: spread_mean, bid_sz_mean, ask_sz_mean, book_pressure, obi_5

Requirements:
  pip install aiohttp  (already installed)
  tick_tradovate_client.py must be importable

Setup:
  Set environment variables (or pass as CLI args):
    TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SECRET

Run:
  python tick_bar_builder.py                          # uses env vars, demo mode
  python tick_bar_builder.py --live                   # LIVE account (real money)
  python tick_bar_builder.py --bar-sizes 1 5 15 30   # override bar sizes
  python tick_bar_builder.py --symbol GC ES           # override symbols

Bar files updated:
  01_data/tick_bars/{SYM}_bars_{N}m.parquet

The executor (tick_live_executor.py) reads these files on each pass, so the
bar builder and executor run as two separate processes in parallel.

Protocol notes:
  Tradovate uses a SockJS-over-WebSocket transport at wss://md.tradovateapi.com/v1/websocket
  SockJS framing: server opens with 'o', client/server wrap JSON in 'a[...]'
  heartbeats sent as 'h' and echoed back
"""

import argparse
import asyncio
import json
import os
import sys
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from tick_tradovate_client import TradovateClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("BarBuilder")

# ── Constants ─────────────────────────────────────────────────────────────────

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

MD_WS_URL    = "wss://md.tradovateapi.com/v1/websocket"
MD_REST_URL  = "https://md.tradovateapi.com/v1"

# Symbols and contract month — UPDATE EACH QUARTERLY ROLLOVER
SYMBOL_MAP = {
    "GC": "MGCM5",   # micro gold  — June 2026
    "ES": "MESM5",   # micro S&P   — June 2026
    "NQ": "MNQM5",   # micro NQ    — June 2026
    "SI": "SILM5",   # micro silver — June 2026
    "CL": "MCLM5",   # micro crude oil — June 2026
}

DEFAULT_BAR_SIZES = [1, 3, 5, 15, 30]  # minutes
DEFAULT_SYMBOLS   = ["GC", "ES", "NQ", "SI", "CL"]

# Minimum trade size to classify as "large" (institutional print)
LARGE_TRADE_THRESH = {"GC": 10, "SI": 20, "ES": 30, "NQ": 20, "CL": 10, "default": 20}

# DOM depth levels to use for OBI computation
OBI_LEVELS = 5

# How long a completed bar is kept in memory before writing (seconds)
BAR_WRITE_DELAY = 5

# ── Bar accumulator ───────────────────────────────────────────────────────────

@dataclass
class BarState:
    """Accumulates ticks for one N-minute bar window."""
    bar_open:  pd.Timestamp       # start of this bar (floor to N minutes)
    bar_size:  int                # minutes

    # OHLCV
    open:      float = 0.0
    high:      float = -1e18
    low:       float = 1e18
    close:     float = 0.0
    volume:    int   = 0

    # Order flow
    buy_vol:     int = 0
    sell_vol:    int = 0
    cvd_delta:   int = 0
    n_trades:    int = 0
    large_buys:  int = 0
    large_sells: int = 0

    # DOM (GC/SI only)
    spread_samples:   list = field(default_factory=list)
    bid_sz_samples:   list = field(default_factory=list)
    ask_sz_samples:   list = field(default_factory=list)
    book_p_samples:   list = field(default_factory=list)
    obi5_samples:     list = field(default_factory=list)

    initialized: bool = False

    def add_trade(self, price: float, size: int, side: str, large_thresh: int):
        if not self.initialized:
            self.open  = price
            self.high  = price
            self.low   = price
            self.initialized = True
        self.close   = price
        self.high    = max(self.high, price)
        self.low     = min(self.low,  price)
        self.volume += size
        self.n_trades += 1

        if side == "buy":
            self.buy_vol    += size
            self.cvd_delta  += size
            if size >= large_thresh:
                self.large_buys += 1
        elif side == "sell":
            self.sell_vol   += size
            self.cvd_delta  -= size
            if size >= large_thresh:
                self.large_sells += 1

    def add_dom(self, bids: list, asks: list):
        """bids/asks: list of (price, size) tuples sorted best-first."""
        if not bids or not asks:
            return
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread   = best_ask - best_bid
        bid_sz   = sum(s for _, s in bids[:OBI_LEVELS])
        ask_sz   = sum(s for _, s in asks[:OBI_LEVELS])
        total_sz = bid_sz + ask_sz

        self.spread_samples.append(spread)
        self.bid_sz_samples.append(bid_sz)
        self.ask_sz_samples.append(ask_sz)
        if total_sz > 0:
            self.book_p_samples.append((bid_sz - ask_sz) / total_sz)
            self.obi5_samples.append((bid_sz - ask_sz) / total_sz)

    def to_row(self, has_dom: bool, cumulative_cvd: int) -> dict:
        """Return dict matching parquet schema."""
        n = self.n_trades if self.n_trades > 0 else 1
        row = {
            "open":       self.open,
            "high":       self.high,
            "low":        self.low,
            "close":      self.close,
            "volume":     self.volume,
            "buy_vol":    self.buy_vol,
            "sell_vol":   self.sell_vol,
            "cvd_delta":  self.cvd_delta,
            "cvd":        cumulative_cvd,
            "n_trades":   self.n_trades,
            "trade_rate": self.n_trades,  # trades per bar; matches historical format
            "large_buys":  self.large_buys,
            "large_sells": self.large_sells,
        }
        if has_dom:
            row["spread_mean"]  = float(np.mean(self.spread_samples))   if self.spread_samples  else 0.0
            row["bid_sz_mean"]  = float(np.mean(self.bid_sz_samples))   if self.bid_sz_samples  else 0.0
            row["ask_sz_mean"]  = float(np.mean(self.ask_sz_samples))   if self.ask_sz_samples  else 0.0
            row["book_pressure"]= float(np.mean(self.book_p_samples))   if self.book_p_samples  else 0.0
            row["obi_5"]        = float(np.mean(self.obi5_samples))     if self.obi5_samples    else 0.0
        return row

    @property
    def bar_end(self) -> pd.Timestamp:
        return self.bar_open + pd.Timedelta(minutes=self.bar_size)


# ── Per-symbol state ──────────────────────────────────────────────────────────

class SymbolState:
    """
    Tracks live tick state for one symbol across all bar sizes.
    Maintains prev_price for tick rule (buy/sell classification).
    """

    def __init__(self, base_sym: str, bar_sizes: list[int]):
        self.base_sym    = base_sym
        self.has_dom     = base_sym in ("GC", "SI")
        self.large_thresh= LARGE_TRADE_THRESH.get(base_sym, LARGE_TRADE_THRESH["default"])

        # Active bar per bar_size
        self.bars: dict[int, BarState] = {}
        # Cumulative CVD across all time (running)
        self.cum_cvd: dict[int, int] = defaultdict(int)
        # Completed bars awaiting write
        self.completed: list[tuple[int, pd.Timestamp, dict]] = []  # (bar_size, ts, row)

        self.bar_sizes   = bar_sizes
        self.prev_price: Optional[float] = None
        self.prev_ts: Optional[pd.Timestamp] = None

    def _bar_open_ts(self, ts: pd.Timestamp, bar_size: int) -> pd.Timestamp:
        minutes = ts.hour * 60 + ts.minute
        slot    = (minutes // bar_size) * bar_size
        return ts.floor("T").replace(hour=slot // 60, minute=slot % 60, second=0, microsecond=0)

    def on_trade(self, price: float, size: int, ts: pd.Timestamp):
        # Classify side via tick rule
        if self.prev_price is None or price > self.prev_price:
            side = "buy"
        elif price < self.prev_price:
            side = "sell"
        else:
            side = "buy" if self._last_side() == "buy" else "sell"  # carry forward

        self.prev_price = price
        self._last_known_side = side

        for bs in self.bar_sizes:
            bar_ts = self._bar_open_ts(ts, bs)

            # If existing bar and we've crossed into a new window — finalize it
            if bs in self.bars and self.bars[bs].bar_open < bar_ts:
                old_bar = self.bars.pop(bs)
                if old_bar.initialized:
                    self.cum_cvd[bs] += old_bar.cvd_delta
                    row = old_bar.to_row(self.has_dom, self.cum_cvd[bs])
                    self.completed.append((bs, old_bar.bar_open, row))
                    log.debug(f"  Bar closed: {self.base_sym}/{bs}m  {old_bar.bar_open}  vol={old_bar.volume}")

            if bs not in self.bars:
                self.bars[bs] = BarState(bar_open=bar_ts, bar_size=bs)

            self.bars[bs].add_trade(price, size, side, self.large_thresh)

    def on_dom(self, bids: list, asks: list):
        if not self.has_dom:
            return
        for bar in self.bars.values():
            bar.add_dom(bids, asks)

    def _last_side(self) -> str:
        return getattr(self, "_last_known_side", "buy")

    def flush_completed(self) -> list[tuple[int, pd.Timestamp, dict]]:
        """Pop and return all completed bars."""
        out, self.completed = self.completed, []
        return out


# ── Parquet writer ────────────────────────────────────────────────────────────

GC_SI_COLS = [
    "open","high","low","close","volume",
    "buy_vol","sell_vol","cvd_delta","cvd","n_trades","trade_rate",
    "large_buys","large_sells",
    "spread_mean","bid_sz_mean","ask_sz_mean","book_pressure","obi_5",
]
BASE_COLS = [
    "open","high","low","close","volume",
    "buy_vol","sell_vol","cvd_delta","cvd","n_trades","trade_rate",
    "large_buys","large_sells",
]

DTYPES = {
    "open": float,"high": float,"low": float,"close": float,
    "volume": int,"buy_vol": int,"sell_vol": int,"cvd_delta": int,"cvd": int,
    "n_trades": int,"trade_rate": int,"large_buys": int,"large_sells": int,
    "spread_mean": float,"bid_sz_mean": float,"ask_sz_mean": float,
    "book_pressure": float,"obi_5": float,
}


def append_bar(base_sym: str, bar_size: int, bar_ts: pd.Timestamp, row: dict):
    """Append a completed bar row to the correct parquet file."""
    path = BAR_DIR / f"{base_sym}_bars_{bar_size}m.parquet"
    cols = GC_SI_COLS if base_sym in ("GC", "SI") else BASE_COLS

    # Fill missing DOM cols with 0
    for c in cols:
        if c not in row:
            row[c] = 0.0 if DTYPES.get(c) == float else 0

    new_row = pd.DataFrame([row], index=pd.DatetimeIndex([bar_ts], name="ts_event", tz="UTC"))
    new_row = new_row[cols]

    if path.exists():
        existing = pd.read_parquet(path)
        existing.index = pd.to_datetime(existing.index, utc=True)
        if bar_ts in existing.index:
            log.debug(f"  Duplicate bar {base_sym}/{bar_size}m {bar_ts} — skipping")
            return
        combined = pd.concat([existing, new_row]).sort_index()
    else:
        combined = new_row

    # Apply dtypes
    for c in cols:
        if c in DTYPES:
            try:
                combined[c] = combined[c].astype(DTYPES[c])
            except Exception:
                pass

    combined.to_parquet(path)
    log.info(f"  Wrote {base_sym}/{bar_size}m bar @ {bar_ts}  vol={row['volume']}  cvd={row['cvd_delta']:+d}")


# ── Tradovate WebSocket handler ───────────────────────────────────────────────

class TradovateBarBuilder:
    """
    Connects to Tradovate WebSocket market data feed,
    builds L2 bars, and writes to parquet files.
    """

    def __init__(self, username: str, password: str, cid: int, secret: str,
                 symbols: list[str], bar_sizes: list[int], demo: bool = True):
        self.symbols   = symbols
        self.bar_sizes = bar_sizes
        self.demo      = demo

        self.client = TradovateClient(
            username=username, password=password,
            cid=cid, secret=secret, demo=demo,
        )
        # base_sym → tv_symbol (e.g. "GC" → "MGCM5")
        self.tv_symbols: dict[str, str] = {}
        # contractId → base_sym
        self.id_to_sym: dict[int, str] = {}
        # base_sym → SymbolState
        self.states: dict[str, SymbolState] = {}

        self._running  = False
        self._ws       = None

    def _seed_cvd_from_parquet(self, base: str, state: SymbolState):
        """
        Load the last stored CVD value from each parquet file so the
        cumulative CVD continues smoothly after a restart instead of
        resetting to 0.
        """
        for bs in self.bar_sizes:
            path = BAR_DIR / f"{base}_bars_{bs}m.parquet"
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path, columns=["cvd"])
                if not df.empty:
                    last_cvd = int(df["cvd"].iloc[-1])
                    state.cum_cvd[bs] = last_cvd
                    log.debug(f"  Seeded CVD {base}/{bs}m from parquet: {last_cvd:+,}")
            except Exception as e:
                log.warning(f"  Could not seed CVD for {base}/{bs}m: {e}")

    # ── Setup ──────────────────────────────────────────────────────────────

    def _auth_and_resolve(self) -> bool:
        log.info("Authenticating with Tradovate...")
        if not self.client.authenticate():
            log.error("Authentication failed")
            return False

        for base in self.symbols:
            tv_sym = SYMBOL_MAP.get(base)
            if not tv_sym:
                log.warning(f"  No symbol mapping for {base} — skipping")
                continue
            cid = self.client.get_contract_id(tv_sym)
            if not cid:
                log.warning(f"  Contract not found: {tv_sym} — skipping {base}")
                continue
            self.tv_symbols[base]  = tv_sym
            self.id_to_sym[cid]    = base
            state = SymbolState(base, self.bar_sizes)
            self._seed_cvd_from_parquet(base, state)
            self.states[base]      = state
            log.info(f"  {base} → {tv_sym}  contractId={cid}")

        return bool(self.tv_symbols)

    # ── WebSocket send ─────────────────────────────────────────────────────

    async def _ws_send(self, op: str, args: list):
        """Send a SockJS-wrapped message to Tradovate WebSocket."""
        msg = json.dumps({"op": op, "args": args})
        frame = json.dumps([msg])   # SockJS wraps in array
        await self._ws.send_str(frame)

    # ── Quote / DOM parsing ────────────────────────────────────────────────

    def _on_quote_update(self, quotes: list):
        now = datetime.now(timezone.utc)
        ts  = pd.Timestamp(now)
        for q in quotes:
            cid  = q.get("contractId") or q.get("id")
            base = self.id_to_sym.get(cid)
            if not base:
                continue
            lp = q.get("lp") or q.get("lastPrice")
            ls = q.get("ls") or q.get("lastSize") or 0
            if lp and ls and ls > 0:
                self.states[base].on_trade(float(lp), int(ls), ts)

    def _on_dom_update(self, doms: list):
        for d in doms:
            cid  = d.get("contractId") or d.get("id")
            base = self.id_to_sym.get(cid)
            if not base or base not in ("GC", "SI"):
                continue
            bids = [(b["price"], b["size"]) for b in d.get("bids", [])]
            asks = [(a["price"], a["size"]) for a in d.get("offers", d.get("asks", []))]
            self.states[base].on_dom(bids, asks)

    def _handle_md_event(self, data: dict):
        quotes = data.get("quotes") or data.get("quote") or []
        if isinstance(quotes, dict):
            quotes = [quotes]
        if quotes:
            self._on_quote_update(quotes)

        doms = data.get("doms") or data.get("dom") or []
        if isinstance(doms, dict):
            doms = [doms]
        if doms:
            self._on_dom_update(doms)

    def _dispatch_message(self, raw: str):
        """Parse SockJS frame and dispatch events."""
        raw = raw.strip()
        if raw == "o":
            log.info("WebSocket opened")
            return
        if raw == "h":
            return  # heartbeat, ignore
        if raw.startswith("c"):
            log.warning(f"WebSocket closed by server: {raw}")
            return
        if raw.startswith("a"):
            try:
                payload = json.loads(raw[1:])  # strip leading 'a'
            except json.JSONDecodeError:
                return
            for item in payload:
                if isinstance(item, str):
                    try:
                        item = json.loads(item)
                    except Exception:
                        continue
                if not isinstance(item, dict):
                    continue
                event = item.get("e")
                data  = item.get("d") or {}
                if event == "md":
                    self._handle_md_event(data)
                # status responses (i, s fields) — log errors only
                elif item.get("s") and item.get("s") != 200:
                    log.warning(f"WS response error: {item}")

    # ── Bar writer task ────────────────────────────────────────────────────

    async def _bar_writer(self):
        """Background task: flush completed bars to parquet every 5 s."""
        while self._running:
            await asyncio.sleep(BAR_WRITE_DELAY)
            for base, state in self.states.items():
                for (bs, bar_ts, row) in state.flush_completed():
                    try:
                        append_bar(base, bs, bar_ts, row)
                    except Exception as e:
                        log.error(f"  Failed to write {base}/{bs}m: {e}")

    # ── Main WebSocket loop ────────────────────────────────────────────────

    async def run(self):
        if not self._auth_and_resolve():
            return

        self._running = True
        token = self.client.access_token

        log.info(f"Connecting to {MD_WS_URL}")
        timeout  = aiohttp.ClientTimeout(total=None, connect=15)
        connector= aiohttp.TCPConnector(ssl=True)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            headers = {"Authorization": f"Bearer {token}"}
            try:
                async with session.ws_connect(MD_WS_URL, headers=headers,
                                              heartbeat=20.0) as ws:
                    self._ws = ws
                    log.info("Connected. Authenticating WebSocket...")

                    # 1. Send WS auth
                    await self._ws_send("auth", [token])
                    await asyncio.sleep(0.5)

                    # 2. Subscribe to quotes + DOM for each symbol
                    for base, tv_sym in self.tv_symbols.items():
                        await self._ws_send("subscribe", [f"md/subscribeQuote+{tv_sym}"])
                        log.info(f"  Subscribed quote: {tv_sym}")
                        if base in ("GC", "SI"):
                            await self._ws_send("subscribe", [f"md/subscribeDom+{tv_sym}"])
                            log.info(f"  Subscribed DOM:   {tv_sym}")
                        await asyncio.sleep(0.1)

                    # 3. Start bar writer task
                    writer_task = asyncio.create_task(self._bar_writer())

                    log.info("Listening for market data... (Ctrl+C to stop)")

                    # 4. Message loop
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._dispatch_message(msg.data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            log.error(f"WS error: {ws.exception()}")
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE,
                                          aiohttp.WSMsgType.CLOSING,
                                          aiohttp.WSMsgType.CLOSED):
                            log.warning("WebSocket closed")
                            break

                    writer_task.cancel()

            except aiohttp.ClientConnectorError as e:
                log.error(f"Connection failed: {e}")
            except asyncio.CancelledError:
                log.info("Cancelled.")
            finally:
                self._running = False
                # Final flush
                for base, state in self.states.items():
                    for (bs, bar_ts, row) in state.flush_completed():
                        try:
                            append_bar(base, bs, bar_ts, row)
                        except Exception:
                            pass
                log.info("Bar builder stopped.")

    async def run_with_reconnect(self, max_retries: int = 10):
        """Run with exponential backoff reconnection."""
        retries = 0
        while retries < max_retries:
            try:
                await self.run()
            except Exception as e:
                log.error(f"Run error: {e}")
            retries += 1
            wait = min(2 ** retries, 120)
            log.info(f"Reconnecting in {wait}s... (attempt {retries}/{max_retries})")
            # Re-authenticate before reconnecting
            if not self.client.authenticate():
                log.error("Re-authentication failed — cannot reconnect")
                break
            await asyncio.sleep(wait)
        log.info("Max retries reached. Exiting.")


# ── Approx bar builder (fallback: REST polling) ───────────────────────────────

class RestBarBuilder:
    """
    Fallback: Polls Tradovate's chart REST endpoint every bar_size minutes.
    Uses volume-weighted approximation for L2 features (no tick-level data).
    Use this if WebSocket fails or for initial testing.
    """

    def __init__(self, client: TradovateClient, symbols: list[str],
                 bar_sizes: list[int]):
        self.client    = client
        self.symbols   = symbols
        self.bar_sizes = bar_sizes
        self.cum_cvd: dict[tuple, int] = defaultdict(int)
        self._seed_cvd_from_parquet()

    def _seed_cvd_from_parquet(self):
        """Load last CVD values from existing parquet files so cumulative CVD continues correctly after restart."""
        for base in self.symbols:
            for bs in self.bar_sizes:
                path = BAR_DIR / f"{base}_bars_{bs}m.parquet"
                if not path.exists():
                    continue
                try:
                    df = pd.read_parquet(path, columns=["cvd"])
                    if not df.empty:
                        self.cum_cvd[(base, bs)] = int(df["cvd"].iloc[-1])
                        log.debug(f"  CVD seeded {base}/{bs}m: {self.cum_cvd[(base, bs)]:+,}")
                except Exception as e:
                    log.warning(f"  Could not seed CVD for {base}/{bs}m: {e}")

    def fetch_recent_bars(self, tv_sym: str, bar_size: int, n_bars: int = 5) -> list[dict]:
        """Fetch recent N bars from Tradovate chart endpoint."""
        try:
            payload = {
                "symbol": tv_sym,
                "chartDescription": {
                    "underlyingType": "MinuteBar",
                    "elementSize": bar_size,
                    "elementSizeUnit": "UnderlyingUnits",
                    "withHistogram": False,
                },
                "timeRange": {
                    "asMuchAsElements": n_bars,
                },
            }
            result = self.client._post("/md/getChart", payload)
            bars   = []
            charts = result.get("charts") or []
            for chart in charts:
                for bar in chart.get("bars") or []:
                    # Tradovate bar: [timestamp, open, high, low, close, upVol, downVol, ...]
                    # Exact indices depend on API version — try both formats
                    if isinstance(bar, list) and len(bar) >= 6:
                        ts_raw  = bar[0]
                        try:
                            ts = pd.Timestamp(ts_raw, unit="ms", tz="UTC") if isinstance(ts_raw, (int, float)) else pd.Timestamp(ts_raw, tz="UTC")
                        except Exception:
                            continue
                        o, h, l, c = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
                        vol    = int(bar[5]) if len(bar) > 5 else 0
                        up_vol = int(bar[6]) if len(bar) > 6 else 0
                        dn_vol = int(bar[7]) if len(bar) > 7 else 0
                        # If no buy/sell breakdown, approximate from candle direction
                        if up_vol == 0 and dn_vol == 0:
                            ratio  = (c - l) / (h - l + 1e-9)
                            up_vol = int(vol * ratio)
                            dn_vol = vol - up_vol
                        bars.append({
                            "ts": ts, "open": o, "high": h, "low": l, "close": c,
                            "volume": vol, "buy_vol": up_vol, "sell_vol": dn_vol,
                        })
                    elif isinstance(bar, dict):
                        ts_raw = bar.get("timestamp") or bar.get("ts")
                        try:
                            ts = pd.Timestamp(ts_raw, unit="ms", tz="UTC") if isinstance(ts_raw, (int, float)) else pd.Timestamp(ts_raw, tz="UTC")
                        except Exception:
                            continue
                        o = float(bar.get("open", 0))
                        h = float(bar.get("high", 0))
                        l = float(bar.get("low", 0))
                        c = float(bar.get("close", 0))
                        vol = int(bar.get("volume", 0))
                        up_vol = int(bar.get("upVol", bar.get("buyVol", 0)))
                        dn_vol = int(bar.get("downVol", bar.get("sellVol", 0)))
                        if up_vol == 0 and dn_vol == 0:
                            ratio  = (c - l) / (h - l + 1e-9)
                            up_vol = int(vol * ratio)
                            dn_vol = vol - up_vol
                        bars.append({
                            "ts": ts, "open": o, "high": h, "low": l, "close": c,
                            "volume": vol, "buy_vol": up_vol, "sell_vol": dn_vol,
                        })
            return bars
        except Exception as e:
            log.error(f"  Chart fetch failed for {tv_sym}/{bar_size}m: {e}")
            return []

    def run_once(self, base_sym: str, tv_sym: str):
        """Fetch latest bars for one symbol and write any new ones."""
        for bs in self.bar_sizes:
            key  = (base_sym, bs)
            path = BAR_DIR / f"{base_sym}_bars_{bs}m.parquet"

            last_ts = None
            if path.exists():
                existing = pd.read_parquet(path)
                existing.index = pd.to_datetime(existing.index, utc=True)
                if not existing.empty:
                    last_ts = existing.index[-1]

            bars = self.fetch_recent_bars(tv_sym, bs, n_bars=10)
            for b in bars:
                ts = b["ts"]
                if last_ts is not None and ts <= last_ts:
                    continue
                cvd_delta = b["buy_vol"] - b["sell_vol"]
                self.cum_cvd[key] += cvd_delta
                row = {
                    "open": b["open"], "high": b["high"], "low": b["low"],
                    "close": b["close"], "volume": b["volume"],
                    "buy_vol": b["buy_vol"], "sell_vol": b["sell_vol"],
                    "cvd_delta": cvd_delta, "cvd": self.cum_cvd[key],
                    "n_trades": max(1, b["volume"] // 5),  # rough estimate
                    "trade_rate": max(1, b["volume"] // 5),
                    "large_buys": 0, "large_sells": 0,
                }
                append_bar(base_sym, bs, ts, row)

    def run_loop(self, poll_seconds: int = 60):
        log.info(f"REST polling every {poll_seconds}s")
        while True:
            for base, tv_sym in SYMBOL_MAP.items():
                if base not in self.symbols:
                    continue
                self.run_once(base, tv_sym)
            time.sleep(poll_seconds)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tradovate Live Bar Builder")
    parser.add_argument("--username",   default=os.environ.get("TRADOVATE_USERNAME", ""))
    parser.add_argument("--password",   default=os.environ.get("TRADOVATE_PASSWORD", ""))
    parser.add_argument("--cid",        type=int, default=int(os.environ.get("TRADOVATE_CID", "0")))
    parser.add_argument("--secret",     default=os.environ.get("TRADOVATE_SECRET", ""))
    parser.add_argument("--live",       action="store_true", help="Use live account (NOT demo)")
    parser.add_argument("--symbol",     nargs="+", default=DEFAULT_SYMBOLS, dest="symbols")
    parser.add_argument("--bar-sizes",  nargs="+", type=int, default=DEFAULT_BAR_SIZES, dest="bar_sizes")
    parser.add_argument("--rest",       action="store_true", help="Use REST polling instead of WebSocket")
    parser.add_argument("--poll",       type=int, default=60, help="REST poll interval seconds")
    parser.add_argument("--debug",      action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.username or not args.password:
        print("\nERROR: Credentials required.")
        print("Set TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SECRET")
        print("Or pass --username --password --cid --secret")
        sys.exit(1)

    mode = "LIVE" if args.live else "DEMO"
    log.info(f"Bar Builder starting — {mode} mode")
    log.info(f"Symbols: {args.symbols}  |  Bar sizes: {args.bar_sizes}m")

    if args.rest:
        # REST polling fallback
        client = TradovateClient(
            username=args.username, password=args.password,
            cid=args.cid, secret=args.secret, demo=not args.live,
        )
        if not client.authenticate():
            sys.exit(1)
        builder = RestBarBuilder(client, args.symbols, args.bar_sizes)
        builder.run_loop(poll_seconds=args.poll)
    else:
        # WebSocket (primary)
        builder = TradovateBarBuilder(
            username=args.username, password=args.password,
            cid=args.cid, secret=args.secret,
            symbols=args.symbols, bar_sizes=args.bar_sizes,
            demo=not args.live,
        )
        try:
            asyncio.run(builder.run_with_reconnect())
        except KeyboardInterrupt:
            log.info("Stopped by user.")


if __name__ == "__main__":
    main()
