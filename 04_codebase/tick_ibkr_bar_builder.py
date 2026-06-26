"""
tick_ibkr_bar_builder.py — Interactive Brokers Real-Time Bar Builder
=====================================================================
Connects to IB Gateway via ib_insync and streams real-time bars for
GC, SI, ES, NQ into Fortress parquet files.

Provides full data that replaces TradingView (no 10-min delay) and
yfinance (adds real CVD, L2 depth, microprice).

Data per bar:
  OHLCV       — from reqRealTimeBars (5-sec aggregation)
  buy/sell vol — from reqTickByTickData (Lee-Ready tick direction)
  CVD         — cumulative (buys - sells) since session start
  L2 depth    — from reqMktDepth (5 levels: imbalance, microprice, OBI)
  Large trades — prints >= LARGE_TRADE_THRESHOLD contracts

Output:
  /opt/fortress/01_data/tick_bars/{SYM}_bars_{N}m.parquet  (N=1,3,5,15,30)
  /opt/fortress/01_data/tick_bars/{SYM}_bars_l2_{N}m.parquet (same data, L2 alias)

Prerequisites:
  1. pip install ib_insync  (already installed in /opt/fortress/venv)
  2. IB Gateway running: ibgateway --mode=paper (port 7497)
     See tick_ibkr_setup.sh for automated installation on Linux.
  3. Market data subscriptions: CME Non-Professional + COMEX (via IBKR account)
  4. Set in /opt/fortress/.env:
       IBKR_MODE=paper          # paper or live
       IBKR_HOST=127.0.0.1
       IBKR_PORT=7497           # 7497=paper, 7496=live
       IBKR_CLIENT_ID=10

Run:
  /opt/fortress/venv/bin/python3 tick_ibkr_bar_builder.py
  Or via systemd: systemctl start fortress-ibkr
"""

import asyncio
import os
import sys
import time
import threading
import signal
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import nest_asyncio
nest_asyncio.apply()

import pandas as pd
import numpy as np

try:
    from ib_insync import IB, Future, Contract, util, RealTimeBar
    from ib_insync import MarketOrder
except ImportError:
    print("ERROR: ib_insync not installed. Run: /opt/fortress/venv/bin/pip install ib_insync")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

BAR_DIR   = Path(os.environ.get("BAR_DIR", "/opt/fortress/01_data/tick_bars"))
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))   # 7497=paper, 7496=live
IBKR_CID  = int(os.environ.get("IBKR_CLIENT_ID", "10"))
IBKR_MODE = os.environ.get("IBKR_MODE", "paper")

TIMEFRAMES = [1, 3, 5, 15, 30]   # bar sizes in minutes to write

# Large-trade thresholds (contracts) for large_buys / large_sells
LARGE_TRADE = {"GC": 10, "SI": 5, "ES": 10, "NQ": 10}

# Symbols to subscribe: base_symbol → IBKR contract spec
# Using full (non-micro) contracts for best L2 depth
# Update expiry each quarterly rollover (run tick_contract_rollover.py)
SYMBOL_CONTRACTS = {
    "GC": {"symbol": "GC",  "exchange": "COMEX", "currency": "USD", "lastTradeDateOrContractMonth": "202609"},
    "SI": {"symbol": "SI",  "exchange": "COMEX", "currency": "USD", "lastTradeDateOrContractMonth": "202609"},
    "ES": {"symbol": "ES",  "exchange": "CME",   "currency": "USD", "lastTradeDateOrContractMonth": "202609"},
    "NQ": {"symbol": "NQ",  "exchange": "CME",   "currency": "USD", "lastTradeDateOrContractMonth": "202609"},
}

# Reconnect delay
RECONNECT_DELAY = 30   # seconds

# ── Parquet write lock (one per file path) ────────────────────────────────────
_write_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

# ── Per-symbol state ──────────────────────────────────────────────────────────
class SymbolState:
    def __init__(self, sym: str):
        self.sym = sym
        # 5-second bar accumulator
        self.current_bars: dict[int, dict] = {tf: {} for tf in TIMEFRAMES}
        # Tick accumulators (reset each bar)
        self.tick_buy_vol:  dict[int, float] = {tf: 0.0 for tf in TIMEFRAMES}
        self.tick_sell_vol: dict[int, float] = {tf: 0.0 for tf in TIMEFRAMES}
        self.tick_n_trades: dict[int, int]   = {tf: 0   for tf in TIMEFRAMES}
        self.tick_large_buy:  dict[int, int] = {tf: 0   for tf in TIMEFRAMES}
        self.tick_large_sell: dict[int, int] = {tf: 0   for tf in TIMEFRAMES}
        self.tick_spread:   dict[int, list]  = {tf: []  for tf in TIMEFRAMES}
        self.tick_bid_sz:   dict[int, list]  = {tf: []  for tf in TIMEFRAMES}
        self.tick_ask_sz:   dict[int, list]  = {tf: []  for tf in TIMEFRAMES}
        # CVD (cumulative, session-scoped)
        self.session_cvd: float = 0.0
        # L2 snapshot
        self.bids: dict[int, tuple] = {}   # level → (price, size)
        self.asks: dict[int, tuple] = {}
        # Last trade price (for tick test)
        self.last_trade_price: Optional[float] = None
        # Bar timestamps (minute-aligned UTC)
        self.bar_open_ts: dict[int, Optional[datetime]] = {tf: None for tf in TIMEFRAMES}

# ── Parquet write ─────────────────────────────────────────────────────────────

def _bar_ts_floor(ts: datetime, tf_min: int) -> datetime:
    """Floor timestamp to tf_min-minute boundary."""
    total_min = ts.hour * 60 + ts.minute
    floored   = (total_min // tf_min) * tf_min
    return ts.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)

def _write_bar(sym: str, tf_min: int, bar_ts: datetime, state: SymbolState, ohlcv: dict):
    """Write one completed bar to parquet."""
    buy_vol  = state.tick_buy_vol[tf_min]
    sell_vol = state.tick_sell_vol[tf_min]
    n_trades = state.tick_n_trades[tf_min]
    cvd_delta = buy_vol - sell_vol

    # Compute L2 features
    bid_szs = [state.bids.get(i, (0, 0))[1] for i in range(5)]
    ask_szs = [state.asks.get(i, (0, 0))[1] for i in range(5)]
    bid_sum = sum(bid_szs) or 1e-9
    ask_sum = sum(ask_szs) or 1e-9
    total   = bid_sum + ask_sum
    obi_5   = (bid_sum - ask_sum) / total if total > 0 else 0.0
    imbal   = obi_5

    bid_p0 = state.bids.get(0, (ohlcv["close"], 0))[0]
    ask_p0 = state.asks.get(0, (ohlcv["close"], 0))[0]
    micro   = (bid_sum * ask_p0 + ask_sum * bid_p0) / total if total > 0 else ohlcv["close"]

    bid_sz_mean = np.mean([b for b in bid_szs if b > 0]) if any(b > 0 for b in bid_szs) else 0.0
    ask_sz_mean = np.mean([a for a in ask_szs if a > 0]) if any(a > 0 for a in ask_szs) else 0.0

    spread_vals = state.tick_spread[tf_min]
    spread_mean = float(np.mean(spread_vals)) if spread_vals else 0.0

    book_pressure = (bid_sz_mean - ask_sz_mean) / (bid_sz_mean + ask_sz_mean + 1e-9)

    row = {
        "ts":              pd.Timestamp(bar_ts, tz="UTC"),
        "open":            ohlcv["open"],
        "high":            ohlcv["high"],
        "low":             ohlcv["low"],
        "close":           ohlcv["close"],
        "volume":          ohlcv["volume"],
        "buy_vol":         buy_vol,
        "sell_vol":        sell_vol,
        "cvd_delta":       cvd_delta,
        "cvd":             state.session_cvd,
        "n_trades":        n_trades,
        "spread":          spread_mean,
        "bid_sz_00":       float(bid_szs[0]) if bid_szs else 0.0,
        "ask_sz_00":       float(ask_szs[0]) if ask_szs else 0.0,
        "book_pressure":   book_pressure,
        "obi_5":           obi_5,
        "microprice":      micro,
        "imbal_L5_last":   imbal,
        "microprice_last": micro,
        "spread_mean":     spread_mean,
        "bid_sz_mean":     bid_sz_mean,
        "ask_sz_mean":     ask_sz_mean,
    }

    new_df = pd.DataFrame([row]).set_index("ts")

    for path_str in [
        str(BAR_DIR / f"{sym}_bars_{tf_min}m.parquet"),
        str(BAR_DIR / f"{sym}_bars_l2_{tf_min}m.parquet"),
    ]:
        with _write_locks[path_str]:
            try:
                path = Path(path_str)
                if path.exists():
                    existing = pd.read_parquet(path)
                    # Align columns
                    for col in new_df.columns:
                        if col not in existing.columns:
                            existing[col] = 0.0
                    for col in existing.columns:
                        if col not in new_df.columns:
                            new_df[col] = 0.0
                    combined = pd.concat([existing, new_df])
                    combined = combined[~combined.index.duplicated(keep="last")]
                    combined.sort_index(inplace=True)
                    # Keep rolling 180 days max
                    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=180)
                    combined = combined[combined.index >= cutoff]
                else:
                    combined = new_df
                combined.to_parquet(path, engine="pyarrow", compression="snappy")
            except Exception as e:
                print(f"  [IBKR] Write error {path_str}: {e}")

    # Reset accumulators for next bar
    state.tick_buy_vol[tf_min]   = 0.0
    state.tick_sell_vol[tf_min]  = 0.0
    state.tick_n_trades[tf_min]  = 0
    state.tick_large_buy[tf_min] = 0
    state.tick_large_sell[tf_min]= 0
    state.tick_spread[tf_min]    = []
    state.tick_bid_sz[tf_min]    = []
    state.tick_ask_sz[tf_min]    = []

    print(f"  [IBKR] {sym}/{tf_min}m {bar_ts.strftime('%H:%M')} "
          f"O={ohlcv['open']:.2f} H={ohlcv['high']:.2f} "
          f"L={ohlcv['low']:.2f} C={ohlcv['close']:.2f} "
          f"V={int(ohlcv['volume'])} CVDd={cvd_delta:+.0f}")

# ── Main bar builder ──────────────────────────────────────────────────────────

class IBKRBarBuilder:
    def __init__(self):
        self.ib      = IB()
        self.states  = {sym: SymbolState(sym) for sym in SYMBOL_CONTRACTS}
        self.running = True
        self._contracts: dict[str, Contract] = {}

    def _make_contract(self, sym: str) -> Future:
        spec = SYMBOL_CONTRACTS[sym]
        c = Future(
            symbol=spec["symbol"],
            lastTradeDateOrContractMonth=spec["lastTradeDateOrContractMonth"],
            exchange=spec["exchange"],
            currency=spec["currency"],
        )
        return c

    async def run(self):
        print(f"[IBKR] Starting bar builder — mode={IBKR_MODE} host={IBKR_HOST}:{IBKR_PORT}")
        BAR_DIR.mkdir(parents=True, exist_ok=True)

        while self.running:
            try:
                await self._connect_and_stream()
            except Exception as e:
                print(f"[IBKR] Connection error: {e}")
            if self.running:
                print(f"[IBKR] Reconnecting in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_and_stream(self):
        print(f"[IBKR] Connecting to IB Gateway at {IBKR_HOST}:{IBKR_PORT} clientId={IBKR_CID}")
        await self.ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CID)
        print("[IBKR] Connected.")

        # Qualify contracts
        for sym in SYMBOL_CONTRACTS:
            c = self._make_contract(sym)
            [qc] = await self.ib.qualifyContractsAsync(c)
            self._contracts[sym] = qc
            print(f"  [IBKR] Qualified: {sym} → {qc.localSymbol} ({qc.exchange})")

        # Subscribe to real-time 5-sec bars
        rt_bar_map: dict[int, str] = {}  # reqId → sym
        for sym, contract in self._contracts.items():
            bars = self.ib.reqRealTimeBars(contract, 5, "TRADES", False)
            bars.updateEvent += lambda b, sym=sym: self._on_rt_bar(b, sym)
            print(f"  [IBKR] Subscribed real-time bars: {sym}")

        # Subscribe to L2 market depth
        for sym, contract in self._contracts.items():
            self.ib.reqMktDepth(contract, numRows=5)
            self.ib.updateMktDepthEvent += lambda *args, sym=sym: self._on_depth(sym, *args)
            print(f"  [IBKR] Subscribed market depth: {sym}")

        # Subscribe to tick-by-tick for CVD
        for sym, contract in self._contracts.items():
            self.ib.reqTickByTickData(contract, "AllLast", 0, False)
            print(f"  [IBKR] Subscribed tick-by-tick: {sym}")

        self.ib.pendingTickersEvent += self._on_pending_tickers

        print("[IBKR] All subscriptions active. Streaming bars...")

        while self.running and self.ib.isConnected():
            await asyncio.sleep(1)

        self.ib.disconnect()
        print("[IBKR] Disconnected.")

    def _on_rt_bar(self, bar: RealTimeBar, sym: str):
        """Called every 5 seconds with a new OHLCV bar."""
        state = self.states[sym]
        now = datetime.now(timezone.utc)

        for tf in TIMEFRAMES:
            bar_ts = _bar_ts_floor(now, tf)

            if state.bar_open_ts[tf] is None:
                # First bar for this timeframe
                state.bar_open_ts[tf] = bar_ts
                state.current_bars[tf] = {
                    "open": bar.open_, "high": bar.high,
                    "low": bar.low,   "close": bar.close,
                    "volume": bar.volume,
                }
                continue

            if bar_ts > state.bar_open_ts[tf]:
                # Bar closed — write it
                completed = state.current_bars[tf]
                _write_bar(sym, tf, state.bar_open_ts[tf], state, completed)
                # Start new bar
                state.bar_open_ts[tf] = bar_ts
                state.current_bars[tf] = {
                    "open": bar.open_, "high": bar.high,
                    "low": bar.low,   "close": bar.close,
                    "volume": bar.volume,
                }
            else:
                # Still in same bar — update OHLCV
                cb = state.current_bars[tf]
                if not cb:
                    cb.update({"open": bar.open_, "high": bar.high,
                               "low": bar.low, "close": bar.close, "volume": bar.volume})
                else:
                    cb["high"]    = max(cb["high"], bar.high)
                    cb["low"]     = min(cb["low"],  bar.low)
                    cb["close"]   = bar.close
                    cb["volume"]  = cb.get("volume", 0) + bar.volume

    def _on_pending_tickers(self, tickers):
        """Handles tick-by-tick trade data for CVD computation."""
        for ticker in tickers:
            if not ticker.contract:
                continue
            sym = ticker.contract.symbol
            if sym not in self.states:
                continue
            state = self.states[sym]
            threshold = LARGE_TRADE.get(sym, 10)

            for tick in ticker.tickByTicks:
                if not hasattr(tick, 'price'):
                    continue
                size = getattr(tick, 'size', 0)
                price = tick.price

                # Tick test for direction (Lee-Ready)
                if state.last_trade_price is None:
                    direction = 1  # assume buy on first tick
                elif price > state.last_trade_price:
                    direction = 1   # uptick = buy
                elif price < state.last_trade_price:
                    direction = -1  # downtick = sell
                else:
                    direction = 0   # zero-tick, keep last
                state.last_trade_price = price

                if direction == 0:
                    continue

                buy  = float(size) if direction > 0 else 0.0
                sell = float(size) if direction < 0 else 0.0

                for tf in TIMEFRAMES:
                    state.tick_buy_vol[tf]  += buy
                    state.tick_sell_vol[tf] += sell
                    state.tick_n_trades[tf] += 1
                    if size >= threshold:
                        if direction > 0:
                            state.tick_large_buy[tf] += 1
                        else:
                            state.tick_large_sell[tf] += 1

                # Update session CVD
                state.session_cvd += (buy - sell)

    def _on_depth(self, sym: str, ticker, side: int, op: int, pos: int, price: float, size: float):
        """L2 market depth update."""
        if sym not in self.states:
            return
        state = self.states[sym]
        book = state.bids if side == 1 else state.asks
        if op in (0, 1):  # insert or update
            book[pos] = (price, size)
            # Update spread on each L2 update
            if state.bids and state.asks:
                best_bid = state.bids.get(0, (0, 0))[0]
                best_ask = state.asks.get(0, (0, 0))[0]
                spread = best_ask - best_bid
                for tf in TIMEFRAMES:
                    if spread > 0:
                        state.tick_spread[tf].append(spread)
        elif op == 2:  # delete
            book.pop(pos, None)

    def stop(self, *_):
        print("[IBKR] Shutdown signal received.")
        self.running = False
        self.ib.disconnect()

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Load .env
    env_file = Path("/opt/fortress/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    builder = IBKRBarBuilder()
    signal.signal(signal.SIGTERM, builder.stop)
    signal.signal(signal.SIGINT, builder.stop)

    print("=" * 60)
    print("  FORTRESS IBKR BAR BUILDER")
    print(f"  Mode: {IBKR_MODE.upper()} | {IBKR_HOST}:{IBKR_PORT}")
    print(f"  Symbols: {', '.join(SYMBOL_CONTRACTS)}")
    print(f"  Timeframes: {TIMEFRAMES}m")
    print(f"  Output: {BAR_DIR}")
    print("=" * 60)
    print()
    print("  Requires IB Gateway running — see tick_ibkr_setup.sh")
    print()

    util.startLoop()
    asyncio.get_event_loop().run_until_complete(builder.run())

if __name__ == "__main__":
    main()
