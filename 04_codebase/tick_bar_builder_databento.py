"""
tick_bar_builder_databento.py — Live Bar Builder via Databento MBO Feed
=======================================================================
Connects to Databento's real-time CME Globex MDP3 feed, subscribes using
the mbp-10 schema (GC/SI — need DOM) and trades schema (ES/NQ — no DOM),
builds N-minute bars with true exchange-tagged aggressor side for CVD,
and appends bars to the same parquet files used by tick_live_executor.py.

Key advantage over tick_bar_builder.py (Tradovate feed):
  CVD is computed from CME's native aggressor-side tagging — the same
  source as the historical Databento backtest data. No tick-rule
  approximation. Live CVD will match backtest CVD exactly.

L2 features computed per bar:
  OHLCV, buy_vol, sell_vol, cvd_delta, cvd (cumulative), n_trades,
  trade_rate, large_buys, large_sells
  GC/SI only: spread_mean, bid_sz_mean, ask_sz_mean, book_pressure, obi_5

Requirements:
  pip install databento

Setup:
  Set environment variable (or pass --api-key):
    DATABENTO_API_KEY=db-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

Run:
  python tick_bar_builder_databento.py
  python tick_bar_builder_databento.py --api-key db-xxx...
  python tick_bar_builder_databento.py --symbol GC SI         # subset
  python tick_bar_builder_databento.py --bar-sizes 1 5 15 30
  python tick_bar_builder_databento.py --debug

Bar files written (identical format to Tradovate builder):
  01_data/tick_bars/{SYM}_bars_{N}m.parquet

The executor (tick_live_executor.py) reads these files on each pass.
Run this builder and the executor as two separate processes in parallel.

Schema notes:
  GC, SI → mbp-10  (market-by-price, 10 levels): gives trades + full DOM
  ES, NQ → trades  (trade prints only, lower message volume for liquid contracts)

Side encoding (CME MDP3 via Databento):
  'B' = bid-side aggressor = buyer-initiated trade → CVD += size
  'A' = ask-side aggressor = seller-initiated trade → CVD -= size
  'N' = undefined / crossed market → skipped

Rollover: update SYMBOL_MAP each quarterly expiry (Mar/Jun/Sep/Dec).
  Current: M5 = June 2026. Roll to U5 around June 6–13, 2026.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import databento as db
except ImportError:
    print("ERROR: databento package not installed.")
    print("  Run: pip install databento")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DBBarBuilder")

# ── Constants ─────────────────────────────────────────────────────────────────

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
BAR_DIR.mkdir(parents=True, exist_ok=True)

DATABENTO_DATASET = "GLBX.MDP3"   # CME Globex MDP3

# Strategy base symbol → Databento raw symbol (current front month)
# UPDATE EACH QUARTERLY ROLLOVER (M5=Jun → U5=Sep → Z5=Dec → H6=Mar)
SYMBOL_MAP: dict[str, str] = {
    "GC": "MGCM5",   # micro gold  — June 2026
    "ES": "MESM5",   # micro S&P   — June 2026
    "NQ": "MNQM5",   # micro NQ    — June 2026
    "SI": "SILM5",   # micro silver — June 2026
    "CL": "MCLM5",   # micro crude oil — June 2026
}

# Symbols that use mbp-10 (need DOM). Others get trades schema.
DOM_SYMBOLS: frozenset[str] = frozenset({"GC", "SI", "CL"})

DEFAULT_BAR_SIZES: list[int] = [1, 3, 5, 15, 30]
DEFAULT_SYMBOLS:   list[str]  = ["GC", "ES", "NQ", "SI", "CL"]

# Minimum trade size to classify as "large" (institutional print)
LARGE_TRADE_THRESH: dict[str, int] = {
    "GC": 10, "SI": 20, "ES": 30, "NQ": 20, "CL": 10, "default": 20
}

OBI_LEVELS       = 5     # book levels to use for OBI computation
BAR_WRITE_DELAY  = 5.0   # seconds between parquet flush cycles
MAX_RETRIES      = 20    # reconnection attempts before giving up

# Fixed-point price scale used by CME MDP3 in Databento
_PRICE_SCALE = 1_000_000_000  # divide int64 price by this to get float


# ── Parquet schema ────────────────────────────────────────────────────────────

GC_SI_COLS: list[str] = [
    "open", "high", "low", "close", "volume",
    "buy_vol", "sell_vol", "cvd_delta", "cvd", "n_trades", "trade_rate",
    "large_buys", "large_sells",
    "spread_mean", "bid_sz_mean", "ask_sz_mean", "book_pressure", "obi_5",
]
BASE_COLS: list[str] = [
    "open", "high", "low", "close", "volume",
    "buy_vol", "sell_vol", "cvd_delta", "cvd", "n_trades", "trade_rate",
    "large_buys", "large_sells",
]
DTYPES: dict[str, type] = {
    "open": float, "high": float, "low": float, "close": float,
    "volume": int, "buy_vol": int, "sell_vol": int, "cvd_delta": int,
    "cvd": int, "n_trades": int, "trade_rate": int,
    "large_buys": int, "large_sells": int,
    "spread_mean": float, "bid_sz_mean": float, "ask_sz_mean": float,
    "book_pressure": float, "obi_5": float,
}


# ── Bar accumulator ───────────────────────────────────────────────────────────

@dataclass
class BarState:
    """Accumulates ticks for one N-minute bar window."""
    bar_open: pd.Timestamp
    bar_size: int

    open:   float = 0.0
    high:   float = -1e18
    low:    float = 1e18
    close:  float = 0.0
    volume: int   = 0

    buy_vol:     int = 0
    sell_vol:    int = 0
    cvd_delta:   int = 0
    n_trades:    int = 0
    large_buys:  int = 0
    large_sells: int = 0

    # DOM samples (GC/SI only)
    spread_samples:  list = field(default_factory=list)
    bid_sz_samples:  list = field(default_factory=list)
    ask_sz_samples:  list = field(default_factory=list)
    book_p_samples:  list = field(default_factory=list)
    obi5_samples:    list = field(default_factory=list)

    initialized: bool = False

    def add_trade(self, price: float, size: int, side: str, large_thresh: int):
        """
        side: "buy"  → buyer-initiated (bid aggressor, CVD += size)
              "sell" → seller-initiated (ask aggressor, CVD -= size)
        """
        if not self.initialized:
            self.open = self.high = self.low = price
            self.initialized = True
        self.close   = price
        self.high    = max(self.high, price)
        self.low     = min(self.low,  price)
        self.volume += size
        self.n_trades += 1

        if side == "buy":
            self.buy_vol   += size
            self.cvd_delta += size
            if size >= large_thresh:
                self.large_buys += 1
        elif side == "sell":
            self.sell_vol  += size
            self.cvd_delta -= size
            if size >= large_thresh:
                self.large_sells += 1

    def add_dom(self, bids: list[tuple[float, int]], asks: list[tuple[float, int]]):
        """bids/asks: [(price, size), ...] sorted best-first."""
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
            obi = (bid_sz - ask_sz) / total_sz
            self.book_p_samples.append(obi)
            self.obi5_samples.append(obi)

    def to_row(self, has_dom: bool, cumulative_cvd: int) -> dict:
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
            "trade_rate": self.n_trades,
            "large_buys":  self.large_buys,
            "large_sells": self.large_sells,
        }
        if has_dom:
            row["spread_mean"]   = float(np.mean(self.spread_samples))  if self.spread_samples  else 0.0
            row["bid_sz_mean"]   = float(np.mean(self.bid_sz_samples))  if self.bid_sz_samples  else 0.0
            row["ask_sz_mean"]   = float(np.mean(self.ask_sz_samples))  if self.ask_sz_samples  else 0.0
            row["book_pressure"] = float(np.mean(self.book_p_samples))  if self.book_p_samples  else 0.0
            row["obi_5"]         = float(np.mean(self.obi5_samples))    if self.obi5_samples    else 0.0
        return row

    @property
    def bar_end(self) -> pd.Timestamp:
        return self.bar_open + pd.Timedelta(minutes=self.bar_size)


# ── Per-symbol live state ─────────────────────────────────────────────────────

class SymbolState:
    """
    Tracks live tick state for one symbol across all bar sizes.
    Unlike the Tradovate builder, side is passed in directly from the
    exchange (no tick-rule approximation needed).
    """

    def __init__(self, base_sym: str, bar_sizes: list[int]):
        self.base_sym     = base_sym
        self.has_dom      = base_sym in DOM_SYMBOLS
        self.large_thresh = LARGE_TRADE_THRESH.get(base_sym, LARGE_TRADE_THRESH["default"])
        self.bar_sizes    = bar_sizes

        self.bars:      dict[int, BarState]                  = {}
        self.cum_cvd:   dict[int, int]                       = defaultdict(int)
        self.completed: list[tuple[int, pd.Timestamp, dict]] = []

    def _bar_open_ts(self, ts: pd.Timestamp, bar_size: int) -> pd.Timestamp:
        minutes = ts.hour * 60 + ts.minute
        slot    = (minutes // bar_size) * bar_size
        return ts.floor("T").replace(
            hour=slot // 60, minute=slot % 60, second=0, microsecond=0
        )

    def on_trade(self, price: float, size: int, side: str, ts: pd.Timestamp):
        """
        side: "buy" or "sell" — exchange-tagged, no approximation.
        side == "skip" for undefined/crossed trades.
        """
        if side == "skip":
            return

        for bs in self.bar_sizes:
            bar_ts = self._bar_open_ts(ts, bs)

            # Finalize previous bar if we've crossed into a new window
            if bs in self.bars and self.bars[bs].bar_open < bar_ts:
                old = self.bars.pop(bs)
                if old.initialized:
                    self.cum_cvd[bs] += old.cvd_delta
                    row = old.to_row(self.has_dom, self.cum_cvd[bs])
                    self.completed.append((bs, old.bar_open, row))
                    log.debug(
                        f"  Closed: {self.base_sym}/{bs}m  {old.bar_open}  "
                        f"vol={old.volume}  cvd_delta={old.cvd_delta:+d}"
                    )

            if bs not in self.bars:
                self.bars[bs] = BarState(bar_open=bar_ts, bar_size=bs)

            self.bars[bs].add_trade(price, size, side, self.large_thresh)

    def on_dom(self, bids: list[tuple[float, int]], asks: list[tuple[float, int]]):
        if not self.has_dom:
            return
        for bar in self.bars.values():
            bar.add_dom(bids, asks)

    def flush_completed(self) -> list[tuple[int, pd.Timestamp, dict]]:
        out, self.completed = self.completed, []
        return out


# ── Parquet writer ────────────────────────────────────────────────────────────

def append_bar(base_sym: str, bar_size: int, bar_ts: pd.Timestamp, row: dict):
    """Append one completed bar to the correct parquet file."""
    path = BAR_DIR / f"{base_sym}_bars_{bar_size}m.parquet"
    cols = GC_SI_COLS if base_sym in DOM_SYMBOLS else BASE_COLS

    for c in cols:
        if c not in row:
            row[c] = 0.0 if DTYPES.get(c) == float else 0

    new_row = pd.DataFrame(
        [row],
        index=pd.DatetimeIndex([bar_ts], name="ts_event", tz="UTC"),
    )[cols]

    if path.exists():
        existing = pd.read_parquet(path)
        existing.index = pd.to_datetime(existing.index, utc=True)
        if bar_ts in existing.index:
            log.debug(f"  Duplicate bar {base_sym}/{bar_size}m {bar_ts} — skipping")
            return
        combined = pd.concat([existing, new_row]).sort_index()
    else:
        combined = new_row

    for c in cols:
        if c in DTYPES:
            try:
                combined[c] = combined[c].astype(DTYPES[c])
            except Exception:
                pass

    combined.to_parquet(path)
    log.info(
        f"  Wrote {base_sym}/{bar_size}m @ {bar_ts}  "
        f"vol={row['volume']}  cvd_delta={row.get('cvd_delta', 0):+d}  "
        f"cvd={row.get('cvd', 0):+,}"
    )


# ── Main bar builder ──────────────────────────────────────────────────────────

class DatabentoBarBuilder:
    """
    Connects to Databento Live (GLBX.MDP3), builds L2 bars with exchange-
    tagged aggressor side, and writes to parquet files.

    Subscriptions:
      mbp-10 → GC, SI  (trades + 10-level DOM in one stream)
      trades  → ES, NQ  (trade prints only; book updates not needed)
    """

    def __init__(self, api_key: str, symbols: list[str], bar_sizes: list[int]):
        self.api_key   = api_key
        self.symbols   = [s for s in symbols if s in SYMBOL_MAP]
        self.bar_sizes = bar_sizes

        # base_sym → SymbolState
        self.states: dict[str, SymbolState] = {
            s: SymbolState(s, bar_sizes) for s in self.symbols
        }
        # Databento instrument_id → base_sym (populated from InstrumentDefMsg)
        self._id_to_base: dict[int, str] = {}
        # raw_symbol (e.g. "MGCM5") → base_sym
        self._raw_to_base: dict[str, str] = {
            v: k for k, v in SYMBOL_MAP.items() if k in self.symbols
        }

    def _seed_cvd_from_parquet(self):
        """Load last CVD from existing parquets so cumulative CVD is continuous."""
        for base, state in self.states.items():
            for bs in self.bar_sizes:
                path = BAR_DIR / f"{base}_bars_{bs}m.parquet"
                if not path.exists():
                    continue
                try:
                    df = pd.read_parquet(path, columns=["cvd"])
                    if not df.empty:
                        last_cvd = int(df["cvd"].iloc[-1])
                        state.cum_cvd[bs] = last_cvd
                        log.debug(f"  Seeded CVD {base}/{bs}m: {last_cvd:+,}")
                except Exception as e:
                    log.warning(f"  Could not seed CVD {base}/{bs}m: {e}")

    # ── Databento record handlers ─────────────────────────────────────────────

    def _on_instrument_def(self, record) -> None:
        raw = getattr(record, "raw_symbol", None)
        if raw and raw in self._raw_to_base:
            iid  = record.instrument_id
            base = self._raw_to_base[raw]
            if iid not in self._id_to_base:
                self._id_to_base[iid] = base
                log.info(f"  Mapped instrument_id={iid} → {base} ({raw})")

    def _decode_side(self, side_char: str) -> str:
        """
        CME MDP3 aggressor-side convention (Databento):
          'B' = bid-side aggressor = buyer hit the ask → buy-initiated → CVD +
          'A' = ask-side aggressor = seller hit the bid → sell-initiated → CVD -
          'N' = undefined / no aggressor
        """
        if side_char == "B":
            return "buy"
        if side_char == "A":
            return "sell"
        return "skip"

    def _on_trade(self, record) -> None:
        base = self._id_to_base.get(record.instrument_id)
        if not base:
            return
        state = self.states.get(base)
        if not state:
            return

        price = record.price / _PRICE_SCALE
        size  = int(record.size)
        side  = self._decode_side(str(getattr(record, "side", "N")))
        ts    = pd.Timestamp(record.ts_event, unit="ns", tz="UTC")

        state.on_trade(price, size, side, ts)

    def _on_mbp10(self, record) -> None:
        """Handle mbp-10 record: extract embedded trade and/or book snapshot."""
        base = self._id_to_base.get(record.instrument_id)
        if not base:
            return
        state = self.states.get(base)
        if not state:
            return

        ts = pd.Timestamp(record.ts_event, unit="ns", tz="UTC")

        # If this is a trade event, extract it
        action = str(getattr(record, "action", ""))
        if action == "T":
            price = record.price / _PRICE_SCALE
            size  = int(record.size)
            side  = self._decode_side(str(getattr(record, "side", "N")))
            state.on_trade(price, size, side, ts)

        # Always sample the book (for DOM/OBI on GC/SI)
        if state.has_dom:
            levels = getattr(record, "levels", [])
            if levels:
                try:
                    bids = [
                        (lv.bid_px / _PRICE_SCALE, int(lv.bid_sz))
                        for lv in levels
                        if lv.bid_px > 0 and lv.bid_sz > 0
                    ]
                    asks = [
                        (lv.ask_px / _PRICE_SCALE, int(lv.ask_sz))
                        for lv in levels
                        if lv.ask_px > 0 and lv.ask_sz > 0
                    ]
                    state.on_dom(bids, asks)
                except Exception as e:
                    log.debug(f"  DOM parse error {base}: {e}")

    def _flush_bars(self) -> None:
        for base, state in self.states.items():
            for (bs, bar_ts, row) in state.flush_completed():
                try:
                    append_bar(base, bs, bar_ts, row)
                except Exception as e:
                    log.error(f"  Write failed {base}/{bs}m: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Single connection attempt. Connect, subscribe, iterate records until
        disconnected or error. Caller wraps this in retry logic.
        """
        self._seed_cvd_from_parquet()
        self._id_to_base.clear()

        dom_syms    = [SYMBOL_MAP[s] for s in self.symbols if s in DOM_SYMBOLS]
        trade_syms  = [SYMBOL_MAP[s] for s in self.symbols if s not in DOM_SYMBOLS]

        live = db.Live(key=self.api_key)

        if dom_syms:
            live.subscribe(
                dataset=DATABENTO_DATASET,
                schema="mbp-10",
                stype_in="raw_symbol",
                symbols=dom_syms,
            )
            log.info(f"  Subscribed mbp-10: {dom_syms}")

        if trade_syms:
            live.subscribe(
                dataset=DATABENTO_DATASET,
                schema="trades",
                stype_in="raw_symbol",
                symbols=trade_syms,
            )
            log.info(f"  Subscribed trades: {trade_syms}")

        log.info("Listening for market data... (Ctrl+C to stop)")
        last_flush = time.time()

        for record in live:
            rtype = type(record).__name__

            if rtype == "InstrumentDefMsg":
                self._on_instrument_def(record)

            elif rtype == "MBP10Msg":
                self._on_mbp10(record)

            elif rtype == "TradeMsg":
                self._on_trade(record)

            # SystemMsg, ErrorMsg, ImbalanceMsg, StatMsg — log errors only
            elif rtype == "ErrorMsg":
                log.error(f"  Databento error: {record}")

            # Periodic bar flush
            now = time.time()
            if now - last_flush >= BAR_WRITE_DELAY:
                self._flush_bars()
                last_flush = now

        # Final flush on disconnect
        self._flush_bars()
        log.info("Disconnected — final flush complete.")

    def run_with_reconnect(self) -> None:
        """Run with exponential backoff reconnection up to MAX_RETRIES."""
        retries = 0
        while retries < MAX_RETRIES:
            try:
                log.info(
                    f"Connecting to Databento {DATABENTO_DATASET} "
                    f"(attempt {retries + 1}/{MAX_RETRIES})"
                )
                self.run()
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                return
            except Exception as e:
                log.error(f"Run error: {type(e).__name__}: {e}")

            retries += 1
            wait = min(2 ** retries, 120)
            log.info(f"Reconnecting in {wait}s...")
            time.sleep(wait)

        log.error("Max retries reached — exiting.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Databento Live Bar Builder — CME futures (GC/ES/NQ/SI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tick_bar_builder_databento.py
  python tick_bar_builder_databento.py --symbol GC SI
  python tick_bar_builder_databento.py --bar-sizes 1 5 15 30
  python tick_bar_builder_databento.py --debug

Env vars:
  DATABENTO_API_KEY    your Databento API key
        """,
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DATABENTO_API_KEY", ""),
        help="Databento API key (or set DATABENTO_API_KEY env var)",
    )
    parser.add_argument(
        "--symbol",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        dest="symbols",
        help="Strategy base symbols to build bars for (default: GC ES NQ SI)",
    )
    parser.add_argument(
        "--bar-sizes",
        nargs="+",
        type=int,
        default=DEFAULT_BAR_SIZES,
        dest="bar_sizes",
        help="Bar sizes in minutes (default: 1 3 5 15 30)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.api_key:
        print("\nERROR: Databento API key required.")
        print("  Set env var: DATABENTO_API_KEY=db-xxxxxxxxxxxxxxxx")
        print("  Or pass:     --api-key db-xxxxxxxxxxxxxxxx")
        print("\nGet your key at: https://databento.com/portal/keys")
        sys.exit(1)

    unknown = [s for s in args.symbols if s not in SYMBOL_MAP]
    if unknown:
        print(f"\nERROR: Unknown symbol(s): {unknown}")
        print(f"  Valid symbols: {list(SYMBOL_MAP.keys())}")
        sys.exit(1)

    print(f"\n{'='*62}")
    print(f"  DATABENTO LIVE BAR BUILDER")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*62}")
    print(f"  Dataset:    {DATABENTO_DATASET}")
    print(f"  Symbols:    {args.symbols}")
    print(f"  Bar sizes:  {args.bar_sizes}m")
    print(f"  Output dir: {BAR_DIR}")

    dom  = [s for s in args.symbols if s in DOM_SYMBOLS]
    trd  = [s for s in args.symbols if s not in DOM_SYMBOLS]
    if dom:
        print(f"  mbp-10:     {dom}  (trades + 10-level DOM)")
    if trd:
        print(f"  trades:     {trd}  (trade prints only)")
    print(f"  CVD source: Exchange-tagged aggressor side (true CVD, no tick rule)")
    print()

    builder = DatabentoBarBuilder(
        api_key   = args.api_key,
        symbols   = args.symbols,
        bar_sizes = args.bar_sizes,
    )
    builder.run_with_reconnect()


if __name__ == "__main__":
    main()
