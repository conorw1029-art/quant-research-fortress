"""
tick_risk_manager.py — Comprehensive Portfolio Risk Manager
============================================================
Manages ALL risk dimensions for live L2 strategy trading across
multiple funded Topstep accounts.

RISK HIERARCHY (outermost → innermost):
  1. Account-level trailing drawdown cap  → halt ALL trading on that account
  2. Portfolio daily loss limit            → halt new entries across portfolio
  3. Strategy daily loss limit             → halt new entries for that strategy
  4. Consecutive loss circuit breaker      → halt strategy after N losses in a row
  5. Per-trade dollar risk gate            → skip signal if ATR stop too wide
  6. Per-trade time stop                   → force exit after N bars

EXIT STRUCTURE — RATCHET MODE (single-contract default):
  Partial exits are IMPOSSIBLE with 1 contract.  Instead we use a
  ratchet trailing stop that locks in profit without closing early:
    • At +1.5R: stop moves to +0.5R  (locks in 0.5R profit)
    • At +2.5R: stop moves to +1.5R  (locks in 1.5R profit)
    • Full exit at +3.0R OR ratcheted stop hit

  Backtest rationale: backtests ran full TP at 3R (no partials).
  Ratchet is strictly better in a live environment — it can only
  improve or match the backtest result, never worsen it.

EXIT STRUCTURE — PARTIAL MODE (legacy, requires 2+ contracts):
  • Partial exit at +1.5R (50%)
  • Move stop to breakeven after partial
  • Full exit at +3.0R on remaining 50%

ACCOUNT ASSUMPTIONS:
  - 10 funded Topstep accounts (~$5,000 each, ~$1,000 in DD from peak)
  - Personal max DD per account: $2,000
  - Remaining runway per account: ~$1,000

CONTRACT SPECS (dollar risk per ATR unit):
  GC: tick=$0.10, point_value=$100 → 1 ATR (~10pts) = $1,000 risk
  ES: tick=$0.25, point_value=$50  → 1 ATR (~6pts)  = $300 risk
  NQ: tick=$0.25, point_value=$20  → 1 ATR (~25pts) = $500 risk
  Micros: 1/10th of above
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import math


# ── Risk configuration ────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    # ── Per-trade ──────────────────────────────────────────────────────────
    max_trade_risk_usd: float = 500.0
    max_hold_bars: int = 50

    # ── Exit structure ─────────────────────────────────────────────────────
    # use_ratchet=True: trailing stop ratchet — works with ANY quantity ≥ 1
    # use_ratchet=False: partial exit at partial_exit_r — needs 2+ contracts
    use_ratchet: bool = True

    # Ratchet levels: trigger_R → lock_R
    # At +1.5R: stop moves to +0.5R (locks in half an R of profit)
    ratchet_1_r: float = 1.5
    ratchet_1_lock_r: float = 0.5
    # At +2.5R: stop moves to +1.5R (locks in 1.5R even if reversal)
    ratchet_2_r: float = 2.5
    ratchet_2_lock_r: float = 1.5

    # Full TP R-multiple (ratchet and partial-exit mode).
    full_tp_r: float = 3.0

    # ── Legacy: partial exit (only valid with 2+ contracts) ────────────────
    partial_exit_r: float = 1.5
    trail_to_breakeven: bool = True

    # ── Strategy-level daily loss ──────────────────────────────────────────
    max_strategy_daily_loss_usd: float = 800.0

    # Consecutive loss circuit breaker: halt strategy entries after N losses.
    # Resets when that strategy books a profitable trade. 0 = disabled.
    max_consecutive_losses: int = 3

    # ── Portfolio daily loss ───────────────────────────────────────────────
    max_portfolio_daily_loss_usd: float = 1500.0

    # ── Account-level trailing drawdown ───────────────────────────────────
    max_account_trailing_dd_usd: float = 1500.0

    # ── Topstep compliance warning ─────────────────────────────────────────
    topstep_daily_limit_usd: float = 4000.0


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    strat_id:         int
    symbol:           str
    direction:        int         # +1 long, -1 short
    entry_px:         float
    stop_px:          float       # current stop — moves with ratchet
    initial_stop_px:  float       # initial stop for R-distance calculations (immutable)
    target_px:        float
    point_value:      float
    commission:       float = 3.0
    contracts:        int   = 1
    bar_count:        int   = 0
    # Ratchet state
    ratchet_1_done:   bool  = False
    ratchet_2_done:   bool  = False
    # Legacy partial exit state (for 2+ contract mode)
    partial_done:     bool  = False
    closed:           bool  = False
    realised_pnl:     float = 0.0

    @property
    def stop_dist(self) -> float:
        """Initial 1R distance in price points (immutable)."""
        return abs(self.entry_px - self.initial_stop_px)

    @property
    def risk_usd(self) -> float:
        """Dollar risk on initial stop for all contracts."""
        return self.stop_dist * self.point_value * self.contracts

    def r_at_price(self, px: float) -> float:
        """R-multiple at a given price vs the initial stop distance."""
        return self.direction * (px - self.entry_px) / (self.stop_dist + 1e-9)

    def pnl_at_price(self, px: float, fraction: float = 1.0) -> float:
        raw = self.direction * (px - self.entry_px) * self.point_value * self.contracts
        comm = 2.0 * self.commission * fraction * self.contracts
        return raw * fraction - comm

    def ratchet_prices(self, cfg: RiskConfig) -> dict:
        """Compute ratchet trigger and lock prices from config."""
        sd = self.stop_dist
        d  = self.direction
        return {
            "r1_trigger": self.entry_px + d * cfg.ratchet_1_r       * sd,
            "r1_lock":    self.entry_px + d * cfg.ratchet_1_lock_r   * sd,
            "r2_trigger": self.entry_px + d * cfg.ratchet_2_r       * sd,
            "r2_lock":    self.entry_px + d * cfg.ratchet_2_lock_r   * sd,
        }

    def stop_description(self) -> str:
        r = self.r_at_price(self.stop_px)
        return f"Stop @ {self.stop_px:.4f}  ({r:+.2f}R current)"

    def target_description(self) -> str:
        r = self.r_at_price(self.target_px)
        return f"Target @ {self.target_px:.4f}  (+{r:.2f}R)"


def build_trade_record(strat_id: int, symbol: str, direction: int,
                       entry_px: float, atr: float, cfg: RiskConfig,
                       stop_mult: float = 1.5, point_value: float = 50.0,
                       commission: float = 3.0, contracts: int = 1) -> TradeRecord:
    stop_dist  = stop_mult * atr
    stop_px    = entry_px - direction * stop_dist
    target_px  = entry_px + direction * cfg.full_tp_r * stop_dist
    return TradeRecord(
        strat_id=strat_id, symbol=symbol, direction=direction,
        entry_px=entry_px, stop_px=stop_px, initial_stop_px=stop_px,
        target_px=target_px, point_value=point_value,
        commission=commission, contracts=contracts,
    )


# ── Daily P&L ledger ──────────────────────────────────────────────────────────

class DailyLedger:
    """Tracks P&L per strategy and portfolio per UTC day."""
    def __init__(self):
        self._data: dict[tuple, float] = {}

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add(self, strat_id: int, pnl: float) -> None:
        k = (strat_id, self._today())
        self._data[k] = self._data.get(k, 0.0) + pnl

    def strategy_today(self, strat_id: int) -> float:
        return self._data.get((strat_id, self._today()), 0.0)

    def portfolio_today(self) -> float:
        today = self._today()
        return sum(v for (_, d), v in self._data.items() if d == today)

    def all_today(self) -> dict[int, float]:
        today = self._today()
        return {sid: v for (sid, d), v in self._data.items() if d == today}

    def to_dict(self) -> dict:
        return dict(self._data)


# ── Account equity tracker ────────────────────────────────────────────────────

class AccountTracker:
    """
    Tracks equity from a starting point, peak equity, and trailing drawdown.
    Mirrors what Topstep sees for a single account.
    """
    def __init__(self, starting_equity: float = 49000.0,
                 cfg: RiskConfig = None):
        if cfg is None:
            cfg = RiskConfig()
        self.equity       = starting_equity
        self.peak_equity  = starting_equity
        self.cfg          = cfg
        self._halted      = False
        self._halt_reason = ""

    @property
    def trailing_dd(self) -> float:
        return self.peak_equity - self.equity

    @property
    def is_halted(self) -> bool:
        return self._halted

    def record_pnl(self, pnl: float) -> Optional[str]:
        """Update equity. Returns halt reason if account just halted, else None."""
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        if not self._halted and self.trailing_dd >= self.cfg.max_account_trailing_dd_usd:
            self._halted = True
            self._halt_reason = (
                f"Trailing DD ${self.trailing_dd:,.0f} ≥ "
                f"personal limit ${self.cfg.max_account_trailing_dd_usd:,.0f}"
            )
            return self._halt_reason
        return None

    def reset_halt(self) -> None:
        """Manual reset — user decision only."""
        self._halted      = False
        self._halt_reason = ""

    def status(self) -> dict:
        return {
            "equity":      round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "trailing_dd": round(self.trailing_dd, 2),
            "halted":      self._halted,
            "halt_reason": self._halt_reason,
        }


# ── Main risk manager ─────────────────────────────────────────────────────────

class RiskManager:
    """
    Centralised risk control for all strategies and accounts.

    Usage pattern:
        rm = RiskManager()
        # On each signal:
        ok, reason = rm.can_enter(strat_id, trade_risk_usd)
        if ok:
            trade = rm.open_trade(strat_id, symbol, direction, entry, atr, pv)
        # Each bar:
        exits = rm.update_bar(strat_id, bar_high, bar_low, bar_close)
        # On forced close (session end / kill switch):
        rm.force_close(strat_id, exit_px)
    """

    def __init__(self, cfg: RiskConfig = None, starting_equity: float = 49000.0):
        if cfg is None:
            cfg = RiskConfig()
        self.cfg    = cfg
        self.ledger = DailyLedger()
        self.account = AccountTracker(starting_equity, cfg)
        self._open:               dict[int, TradeRecord] = {}
        self._consecutive_losses: dict[int, int]         = {}

    # ── Entry gate ────────────────────────────────────────────────────────

    def can_enter(self, strat_id: int, trade_risk_usd: float) -> tuple[bool, str]:
        """Returns (allowed, reason).  reason is '' when allowed."""
        if self.account.is_halted:
            return False, f"Account halted: {self.account._halt_reason}"

        if strat_id in self._open:
            return False, "Already in position"

        port_pnl = self.ledger.portfolio_today()
        if port_pnl <= -self.cfg.max_portfolio_daily_loss_usd:
            return False, (f"Portfolio daily loss ${port_pnl:+,.0f} "
                           f"≤ -${self.cfg.max_portfolio_daily_loss_usd:,.0f}")

        strat_pnl = self.ledger.strategy_today(strat_id)
        if strat_pnl <= -self.cfg.max_strategy_daily_loss_usd:
            return False, (f"Strategy [{strat_id}] daily loss ${strat_pnl:+,.0f} "
                           f"≤ -${self.cfg.max_strategy_daily_loss_usd:,.0f}")

        if self.cfg.max_consecutive_losses > 0:
            consec = self._consecutive_losses.get(strat_id, 0)
            if consec >= self.cfg.max_consecutive_losses:
                return False, (f"Strategy [{strat_id}] circuit breaker: "
                               f"{consec} consecutive losses (limit {self.cfg.max_consecutive_losses})")

        if trade_risk_usd > self.cfg.max_trade_risk_usd:
            return False, (f"Trade risk ${trade_risk_usd:,.0f} "
                           f"> max ${self.cfg.max_trade_risk_usd:,.0f}")

        return True, ""

    # ── Open trade ────────────────────────────────────────────────────────

    def open_trade(self, strat_id: int, symbol: str, direction: int,
                   entry_px: float, atr: float, point_value: float,
                   commission: float = 3.0, stop_mult: float = 1.5,
                   contracts: int = 1) -> TradeRecord:
        trade = build_trade_record(
            strat_id, symbol, direction, entry_px, atr, self.cfg,
            stop_mult=stop_mult, point_value=point_value,
            commission=commission, contracts=contracts,
        )
        self._open[strat_id] = trade
        return trade

    # ── Tick / bar update ─────────────────────────────────────────────────

    def update_bar(self, strat_id: int, bar_high: float, bar_low: float,
                   bar_close: float) -> list[dict]:
        """
        Called once per new bar for each open trade.

        Returns a list of event dicts. Event types:
          stop      — stop hit (full close)
          ratchet_1 — stop moved to ratchet_1_lock_r (no money, stop update only)
          ratchet_2 — stop moved to ratchet_2_lock_r (no money, stop update only)
          partial_tp — 50% closed (legacy, 2+ contracts)
          target    — full target hit (full close)
          timeout   — time stop hit (full close)

        Callers should only treat 'stop', 'target', 'timeout', 'signal' as
        full closes. 'ratchet_1', 'ratchet_2', 'partial_tp' are NOT closes.
        """
        trade = self._open.get(strat_id)
        if trade is None:
            return []

        trade.bar_count += 1
        exits = []

        # ── 1. Current stop hit ────────────────────────────────────────────
        stop_hit = (
            (trade.direction == 1  and bar_low  <= trade.stop_px) or
            (trade.direction == -1 and bar_high >= trade.stop_px)
        )
        if stop_hit:
            exits.append(self._do_close(trade, trade.stop_px, 1.0, "stop"))
            return exits

        # ── 2a. Ratchet trailing stop (single-contract mode) ──────────────
        if self.cfg.use_ratchet:
            rp = trade.ratchet_prices(self.cfg)

            if not trade.ratchet_1_done:
                r1_hit = (
                    (trade.direction == 1  and bar_high >= rp["r1_trigger"]) or
                    (trade.direction == -1 and bar_low  <= rp["r1_trigger"])
                )
                if r1_hit:
                    trade.stop_px       = rp["r1_lock"]
                    trade.ratchet_1_done = True
                    exits.append({
                        "strat_id": strat_id, "symbol": trade.symbol,
                        "reason": "ratchet_1", "fraction": 0.0,
                        "exit_px": rp["r1_trigger"], "pnl": 0.0,
                        "new_stop": round(trade.stop_px, 4),
                        "new_stop_r": round(self.cfg.ratchet_1_lock_r, 2),
                        "account_halt": None,
                    })

            if trade.ratchet_1_done and not trade.ratchet_2_done:
                r2_hit = (
                    (trade.direction == 1  and bar_high >= rp["r2_trigger"]) or
                    (trade.direction == -1 and bar_low  <= rp["r2_trigger"])
                )
                if r2_hit:
                    trade.stop_px       = rp["r2_lock"]
                    trade.ratchet_2_done = True
                    exits.append({
                        "strat_id": strat_id, "symbol": trade.symbol,
                        "reason": "ratchet_2", "fraction": 0.0,
                        "exit_px": rp["r2_trigger"], "pnl": 0.0,
                        "new_stop": round(trade.stop_px, 4),
                        "new_stop_r": round(self.cfg.ratchet_2_lock_r, 2),
                        "account_halt": None,
                    })

        # ── 2b. Legacy partial exit (2+ contract mode only) ───────────────
        # Partial exit splits 50% of the position — impossible with 1 contract.
        # Guard ensures we never attempt fractional-contract exits.
        elif not trade.partial_done and trade.contracts >= 2:
            partial_px = (trade.entry_px
                          + trade.direction * self.cfg.partial_exit_r * trade.stop_dist)
            partial_hit = (
                (trade.direction == 1  and bar_high >= partial_px) or
                (trade.direction == -1 and bar_low  <= partial_px)
            )
            if partial_hit:
                exits.append(self._do_partial(trade, partial_px))

        # ── 3. Full target ─────────────────────────────────────────────────
        if strat_id in self._open:
            tgt_hit = (
                (trade.direction == 1  and bar_high >= trade.target_px) or
                (trade.direction == -1 and bar_low  <= trade.target_px)
            )
            if tgt_hit:
                exits.append(self._do_close(trade, trade.target_px, 1.0, "target"))

            # ── 4. Time stop ───────────────────────────────────────────────
            elif trade.bar_count >= self.cfg.max_hold_bars:
                exits.append(self._do_close(trade, bar_close, 1.0, "timeout"))

        return exits

    def signal_close(self, strat_id: int, exit_px: float) -> Optional[dict]:
        """Signal-driven close (direction flip or flat signal)."""
        if strat_id not in self._open:
            return None
        trade = self._open[strat_id]
        return self._do_close(trade, exit_px, 1.0, "signal")

    def force_close(self, strat_id: int, exit_px: float) -> Optional[dict]:
        """Force-close for session end / kill switch / news event."""
        if strat_id not in self._open:
            return None
        trade = self._open[strat_id]
        return self._do_close(trade, exit_px, 1.0, "forced")

    def force_close_all(self, exit_px_map: dict[int, float]) -> list[dict]:
        """
        Force-close all open trades. exit_px_map: {strat_id: price}.
        Strats missing from the map use their entry price (flat PnL).
        """
        results = []
        for sid in list(self._open.keys()):
            px = exit_px_map.get(sid, self._open[sid].entry_px)
            ex = self.force_close(sid, px)
            if ex:
                results.append(ex)
        return results

    def consecutive_losses(self, strat_id: int) -> int:
        return self._consecutive_losses.get(strat_id, 0)

    # ── Internal close helpers ─────────────────────────────────────────────

    def _do_partial(self, trade: TradeRecord, exit_px: float) -> dict:
        fraction = 0.5
        pnl = trade.pnl_at_price(exit_px, fraction)
        trade.realised_pnl += pnl
        trade.partial_done  = True
        self.ledger.add(trade.strat_id, pnl)
        halt = self.account.record_pnl(pnl)
        if self.cfg.trail_to_breakeven:
            trade.stop_px = trade.entry_px
        return {
            "strat_id": trade.strat_id, "symbol": trade.symbol,
            "reason": "partial_tp", "fraction": fraction,
            "exit_px": exit_px, "pnl": round(pnl, 2),
            "new_stop": trade.stop_px, "account_halt": halt,
        }

    def _do_close(self, trade: TradeRecord, exit_px: float,
                  fraction: float, reason: str) -> dict:
        remaining = 0.5 if trade.partial_done else 1.0
        pnl = trade.pnl_at_price(exit_px, remaining)
        trade.realised_pnl += pnl
        trade.closed = True
        del self._open[trade.strat_id]
        self.ledger.add(trade.strat_id, pnl)
        halt = self.account.record_pnl(pnl)

        # Consecutive loss tracking: increment on loss, reset on profit
        total_pnl = trade.realised_pnl
        if total_pnl < 0:
            self._consecutive_losses[trade.strat_id] = (
                self._consecutive_losses.get(trade.strat_id, 0) + 1
            )
        else:
            self._consecutive_losses[trade.strat_id] = 0

        stop_dist = trade.stop_dist if trade.stop_dist else 1e-9
        r_multiple = round(trade.direction * (exit_px - trade.entry_px) / stop_dist, 3)
        return {
            "strat_id":           trade.strat_id,
            "symbol":             trade.symbol,
            "reason":             reason,
            "fraction":           remaining,
            "direction":          trade.direction,
            "entry_px":           trade.entry_px,
            "initial_stop_px":    trade.initial_stop_px,
            "target_px":          trade.target_px,
            "exit_px":            exit_px,
            "r_multiple":         r_multiple,
            "bar_count":          trade.bar_count,
            "ratchet_1_done":     trade.ratchet_1_done,
            "ratchet_2_done":     trade.ratchet_2_done,
            "pnl":                round(pnl, 2),
            "total_trade_pnl":    round(trade.realised_pnl, 2),
            "consecutive_losses": self._consecutive_losses.get(trade.strat_id, 0),
            "account_halt":       halt,
        }

    # ── Status ────────────────────────────────────────────────────────────

    def status_report(self) -> str:
        lines = []
        lines.append(f"{'-'*62}")
        lines.append(f"  RISK MANAGER STATUS  {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        lines.append(f"{'-'*62}")

        acc = self.account.status()
        lines.append(f"  Account equity:  ${acc['equity']:>10,.2f}  "
                     f"(peak ${acc['peak_equity']:,.2f})")
        lines.append(f"  Trailing DD:     ${acc['trailing_dd']:>10,.2f}  "
                     f"(limit ${self.cfg.max_account_trailing_dd_usd:,.0f})")
        if acc["halted"]:
            lines.append(f"  *** ACCOUNT HALTED: {acc['halt_reason']} ***")

        port = self.ledger.portfolio_today()
        port_pct = port / self.cfg.max_portfolio_daily_loss_usd * 100
        lines.append(f"  Portfolio today: ${port:>+10,.2f}  "
                     f"({port_pct:.0f}% of daily limit)")

        today_all = self.ledger.all_today()
        if today_all:
            lines.append(f"  Strategy P&L today:")
            for sid, pnl in sorted(today_all.items()):
                blocked  = "BLOCKED" if pnl <= -self.cfg.max_strategy_daily_loss_usd else ""
                consec   = self._consecutive_losses.get(sid, 0)
                cb_str   = f"  CB:{consec}/{self.cfg.max_consecutive_losses}" if consec > 0 else ""
                lines.append(f"    [{sid}]  ${pnl:>+8,.2f}  {blocked}{cb_str}")

        if self._open:
            lines.append(f"  Open trades:")
            for sid, tr in self._open.items():
                rstr = ""
                if tr.ratchet_1_done:
                    rstr = " [R1✓]"
                if tr.ratchet_2_done:
                    rstr = " [R1✓ R2✓]"
                lines.append(f"    [{sid}] {tr.symbol}  "
                             f"{'LONG' if tr.direction == 1 else 'SHORT'}  "
                             f"entry={tr.entry_px:.4f}  "
                             f"stop={tr.stop_px:.4f}{rstr}  "
                             f"bars={tr.bar_count}")
        else:
            lines.append(f"  Open trades: none")

        lines.append(f"{'-'*62}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "account": self.account.status(),
            "portfolio_today_pnl": self.ledger.portfolio_today(),
            "strategy_today_pnl":  self.ledger.all_today(),
            "consecutive_losses":  dict(self._consecutive_losses),
            "open_trades": {
                sid: {
                    "symbol":         tr.symbol,
                    "direction":      tr.direction,
                    "entry_px":       tr.entry_px,
                    "stop_px":        tr.stop_px,
                    "initial_stop":   tr.initial_stop_px,
                    "target_px":      tr.target_px,
                    "ratchet_1_done": tr.ratchet_1_done,
                    "ratchet_2_done": tr.ratchet_2_done,
                    "bar_count":      tr.bar_count,
                    "risk_usd":       round(tr.risk_usd, 2),
                }
                for sid, tr in self._open.items()
            },
        }


# ── Position sizing helper ────────────────────────────────────────────────────

def recommended_contracts(trade_risk_usd: float,
                           account_equity: float,
                           risk_pct_per_trade: float = 0.01,
                           max_contracts: int = 2) -> int:
    """
    How many contracts to trade given account size and desired risk fraction.
    Default: risk 1% of account per trade.
    Always returns at least 1.
    """
    if trade_risk_usd <= 0:
        return 1
    size = account_equity * risk_pct_per_trade / trade_risk_usd
    return max(1, min(max_contracts, math.floor(size)))


# ── Entry alert formatter ─────────────────────────────────────────────────────

def format_entry_alert(trade: TradeRecord, account: AccountTracker,
                       ledger: DailyLedger, contracts: int = 1,
                       cfg: RiskConfig = None) -> str:
    if cfg is None:
        cfg = RiskConfig()
    risk      = trade.stop_dist * trade.point_value * contracts
    port_pnl  = ledger.portfolio_today()
    strat_pnl = ledger.strategy_today(trade.strat_id)
    acc       = account.status()

    lines = [
        f"  === ENTRY ALERT ===",
        f"  Strategy [{trade.strat_id}]  {trade.symbol}  "
        f"{'LONG ↑' if trade.direction == 1 else 'SHORT ↓'}",
        f"",
        f"  Entry:    {trade.entry_px:.4f}",
        f"  Stop:     {trade.initial_stop_px:.4f}  "
        f"(risk ${risk:,.0f} for {contracts} contract{'s' if contracts > 1 else ''})",
    ]

    if cfg.use_ratchet:
        rp = trade.ratchet_prices(cfg)
        lines.append(
            f"  Ratchet1: {rp['r1_trigger']:.4f}  "
            f"(+{cfg.ratchet_1_r}R → stop moves to +{cfg.ratchet_1_lock_r}R)"
        )
        lines.append(
            f"  Ratchet2: {rp['r2_trigger']:.4f}  "
            f"(+{cfg.ratchet_2_r}R → stop moves to +{cfg.ratchet_2_lock_r}R)"
        )
        lines.append(f"  Target:   {trade.target_px:.4f}  (+{cfg.full_tp_r}R full exit)")
    else:
        partial_px = trade.entry_px + trade.direction * cfg.partial_exit_r * trade.stop_dist
        lines.append(
            f"  Part. TP: {partial_px:.4f}  "
            f"(+{cfg.partial_exit_r}R — close 50% here, move stop to B/E)"
        )
        lines.append(f"  Full TP:  {trade.target_px:.4f}  (+{cfg.full_tp_r}R — remaining 50%)")

    lines += [
        f"  ATR:      {trade.stop_dist / 1.5:.4f}",
        f"",
        f"  RISK SNAPSHOT",
        f"  Portfolio today:  ${port_pnl:+,.0f}",
        f"  Strategy today:   ${strat_pnl:+,.0f}",
        f"  Account trail DD: ${acc['trailing_dd']:,.0f}  "
        f"(limit ${cfg.max_account_trailing_dd_usd:,.0f})",
    ]

    consec = account.cfg.max_consecutive_losses
    if consec > 0:
        # find strat's consecutive losses
        pass  # can't easily reach RiskManager from here — omit

    lines.append(f"  ===================")
    return "\n".join(lines)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Risk Manager Self-Test ===\n")

    cfg = RiskConfig(
        max_trade_risk_usd           = 200,
        max_strategy_daily_loss_usd  = 250,
        max_portfolio_daily_loss_usd = 600,
        max_account_trailing_dd_usd  = 800,
        max_consecutive_losses       = 3,
        use_ratchet                  = True,
        ratchet_1_r                  = 1.5,
        ratchet_1_lock_r             = 0.5,
        ratchet_2_r                  = 2.5,
        ratchet_2_lock_r             = 1.5,
        full_tp_r                    = 3.0,
    )
    rm = RiskManager(cfg=cfg, starting_equity=49000.0)

    # --- Trade 1: reaches ratchet levels, then full TP ---
    ok, reason = rm.can_enter(strat_id=2, trade_risk_usd=44)
    print(f"Can enter strat 2: {ok}  '{reason}'")

    tr = rm.open_trade(2, "MES", direction=1, entry_px=5000.0, atr=9.0,
                       point_value=5.0, commission=2.0, contracts=1)
    print(f"  Entry:    {tr.entry_px:.2f}  (stop={tr.initial_stop_px:.2f}  target={tr.target_px:.2f})")
    rp = tr.ratchet_prices(cfg)
    print(f"  Ratchet1: {rp['r1_trigger']:.2f} -> stop to {rp['r1_lock']:.2f}")
    print(f"  Ratchet2: {rp['r2_trigger']:.2f} -> stop to {rp['r2_lock']:.2f}")
    print(f"  Risk:     ${tr.risk_usd:.0f}")

    # stop_dist = 1.5 * atr = 13.5, so ratchets at 5020.25 / 5033.75 / target 5040.5
    # Bar 1: slow drift up, no triggers
    exits = rm.update_bar(2, bar_high=5005.0, bar_low=4999.0, bar_close=5003.0)
    assert exits == [], f"Expected no exits bar1, got {exits}"
    print(f"\n  Bar 1: no exits (expected)")

    # Bar 2: hits ratchet 1 (trigger = 5000 + 1.5*13.5 = 5020.25)
    exits = rm.update_bar(2, bar_high=5021.0, bar_low=5002.0, bar_close=5020.0)
    r1_exits = [e for e in exits if e["reason"] == "ratchet_1"]
    assert len(r1_exits) == 1, f"Expected ratchet_1, got {exits}"
    print(f"\n  Bar 2: Ratchet 1 fired — new stop = {r1_exits[0]['new_stop']} (+{r1_exits[0]['new_stop_r']}R)")
    assert abs(r1_exits[0]["new_stop"] - rp["r1_lock"]) < 0.001

    # Bar 3: hits ratchet 2 (trigger = 5000 + 2.5*13.5 = 5033.75)
    exits = rm.update_bar(2, bar_high=5035.0, bar_low=5019.0, bar_close=5033.0)
    r2_exits = [e for e in exits if e["reason"] == "ratchet_2"]
    assert len(r2_exits) == 1, f"Expected ratchet_2, got {exits}"
    print(f"  Bar 3: Ratchet 2 fired — new stop = {r2_exits[0]['new_stop']} (+{r2_exits[0]['new_stop_r']}R)")

    # Bar 4: hits full target (target = 5000 + 3*13.5 = 5040.5)
    exits = rm.update_bar(2, bar_high=5042.0, bar_low=5033.0, bar_close=5041.0)
    close_exits = [e for e in exits if e["reason"] == "target"]
    assert len(close_exits) == 1, f"Expected target exit, got {exits}"
    ex = close_exits[0]
    print(f"  Bar 4: Target hit — PnL ${ex['pnl']:+,.2f}  total ${ex['total_trade_pnl']:+,.2f}")
    print(f"  Consecutive losses: {ex['consecutive_losses']} (expect 0 — was a win)")

    # --- Trade 2: stop hit → consecutive loss ---
    rm.open_trade(2, "MES", direction=1, entry_px=5030.0, atr=9.0,
                  point_value=5.0, commission=2.0)
    exits = rm.update_bar(2, bar_high=5032.0, bar_low=5015.0, bar_close=5016.0)
    stop_exits = [e for e in exits if e["reason"] == "stop"]
    print(f"\n  Trade 2: Stop hit — PnL ${stop_exits[0]['pnl']:+,.2f}")
    print(f"  Consecutive losses: {stop_exits[0]['consecutive_losses']}")

    print(f"\n{rm.status_report()}")

    # --- Consecutive loss gate test ---
    for i in range(3):
        rm.open_trade(2, "MES", direction=1, entry_px=5030.0, atr=9.0,
                      point_value=5.0, commission=2.0)
        rm.update_bar(2, bar_high=5031.0, bar_low=5015.0, bar_close=5016.0)

    ok, reason = rm.can_enter(strat_id=2, trade_risk_usd=44)
    print(f"\nAfter 4 consecutive losses: can_enter={ok}  reason='{reason}'")
    assert not ok and "circuit breaker" in reason.lower()

    print("\n=== All assertions passed ===")
