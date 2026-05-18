"""
tick_state_manager.py — Persistent State Manager
=================================================
Provides atomic read/write helpers for all live trading state files.

NO broker connections. NO API calls. NO orders.
Pure file I/O with atomic writes to prevent corrupt state on crash.

State files live in:
  06_live_trading/state/

Usage:
  from tick_state_manager import StateManager
  sm = StateManager()
  sm.update_heartbeat()
  positions = sm.load_positions()
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────────

_CODE_DIR  = Path(__file__).parent
_STATE_DIR = _CODE_DIR.parent / "06_live_trading" / "state"


# ── Atomic I/O ────────────────────────────────────────────────────────────────

def load_json(path: Path, default: Any = None) -> Any:
    """Read JSON file; return default if missing or corrupt."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        return default if default is not None else {}
    except (json.JSONDecodeError, OSError):
        # Corrupt file — return safe default, do not raise
        return default if default is not None else {}


def atomic_write_json(path: Path, data: Any) -> None:
    """
    Write JSON atomically: write to .tmp then os.replace().
    Safe on NTFS (near-atomic) and POSIX (atomic).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ── StateManager ──────────────────────────────────────────────────────────────

class StateManager:
    """
    Central manager for all live trading state files.

    All writes are atomic. All reads return safe defaults on missing/corrupt files.
    No broker connection, no API calls, no orders.
    """

    def __init__(self, state_dir: Path = None):
        self.state_dir = Path(state_dir) if state_dir else _STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_skeleton_files()

    def _p(self, filename: str) -> Path:
        return self.state_dir / filename

    def _ensure_skeleton_files(self):
        """Create missing state files with safe empty defaults."""
        defaults = {
            "positions.json":          {"last_updated": None, "source": "init", "positions": {}},
            "open_orders.json":        {"last_updated": None, "orders": {}},
            "daily_pnl.json":          {"date": None, "last_updated": None, "realized_pnl": 0.0,
                                        "per_strategy": {}, "daily_loss_limit": -600.0,
                                        "daily_loss_remaining": -600.0, "halt_triggered": False},
            "strategy_halts.json":     {"last_updated": None, "halts": {}},
            "account_state.json":      {"last_updated": None, "account_id": None,
                                        "account_halt": False, "account_halt_reason": None,
                                        "daily_loss_triggered": False,
                                        "trailing_drawdown_remaining": 800.0,
                                        "max_drawdown_limit": 800.0, "session_open": False},
            "last_seen_bar.json":      {"last_updated": None, "bars": {}},
            "heartbeat.json":          {"timestamp": None, "pid": None, "mode": "DRY_RUN",
                                        "uptime_seconds": 0, "bar_loop_count": 0,
                                        "last_signal_time": None, "broker_connected": False,
                                        "data_fresh": False},
            "active_brackets.json":    {"last_updated": None, "brackets": {}},
            "processed_signals.json":  {"last_updated": None, "session_date": None,
                                        "processed_ids": []},
        }
        for filename, default in defaults.items():
            p = self._p(filename)
            if not p.exists():
                atomic_write_json(p, default)

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def update_heartbeat(self, mode: str = "DRY_RUN",
                         broker_connected: bool = False,
                         data_fresh: bool = False,
                         bar_loop_count: int = 0,
                         last_signal_time: str = None) -> None:
        existing = load_json(self._p("heartbeat.json"), {})
        start_ts = existing.get("_start_timestamp", time.time())
        data = {
            "_start_timestamp":  start_ts,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "pid":               os.getpid(),
            "mode":              mode,
            "uptime_seconds":    int(time.time() - start_ts),
            "bar_loop_count":    bar_loop_count,
            "last_signal_time":  last_signal_time,
            "broker_connected":  broker_connected,
            "data_fresh":        data_fresh,
        }
        atomic_write_json(self._p("heartbeat.json"), data)

    def is_heartbeat_stale(self, max_age_seconds: int = 120) -> bool:
        """Return True if heartbeat is older than max_age_seconds."""
        hb = load_json(self._p("heartbeat.json"), {})
        ts_str = hb.get("timestamp")
        if not ts_str:
            return True
        try:
            ts = datetime.fromisoformat(ts_str)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age > max_age_seconds
        except Exception:
            return True

    # ── Positions ─────────────────────────────────────────────────────────────

    def load_positions(self) -> dict:
        """Return {symbol: {net_pos, entry_px, stop_px, target_px, ...}}."""
        return load_json(self._p("positions.json"),
                         {"last_updated": None, "source": "init", "positions": {}})

    def save_positions(self, positions: dict, source: str = "local_unconfirmed") -> None:
        data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source":       source,
            "positions":    positions,
        }
        atomic_write_json(self._p("positions.json"), data)

    def get_local_position(self, symbol: str) -> dict:
        """Return position dict for symbol, or {} if flat."""
        state = self.load_positions()
        return state.get("positions", {}).get(symbol, {})

    def is_locally_flat(self) -> bool:
        """Return True if local state shows no open positions."""
        state = self.load_positions()
        positions = state.get("positions", {})
        return all(p.get("net_pos", 0) == 0 for p in positions.values())

    # ── Open Orders ───────────────────────────────────────────────────────────

    def load_open_orders(self) -> dict:
        return load_json(self._p("open_orders.json"),
                         {"last_updated": None, "orders": {}})

    def save_open_orders(self, orders: dict) -> None:
        data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "orders":       orders,
        }
        atomic_write_json(self._p("open_orders.json"), data)

    # ── Active Brackets ───────────────────────────────────────────────────────

    def load_active_brackets(self) -> dict:
        return load_json(self._p("active_brackets.json"),
                         {"last_updated": None, "brackets": {}})

    def save_active_brackets(self, brackets: dict) -> None:
        data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "brackets":     brackets,
        }
        atomic_write_json(self._p("active_brackets.json"), data)

    def add_bracket(self, strategy_id: str, bracket: dict) -> None:
        state = self.load_active_brackets()
        brackets = state.get("brackets", {})
        brackets[str(strategy_id)] = bracket
        self.save_active_brackets(brackets)

    def remove_bracket(self, strategy_id: str) -> None:
        state = self.load_active_brackets()
        brackets = state.get("brackets", {})
        brackets.pop(str(strategy_id), None)
        self.save_active_brackets(brackets)

    def get_bracket(self, strategy_id: str) -> dict:
        state = self.load_active_brackets()
        return state.get("brackets", {}).get(str(strategy_id), {})

    # ── Daily P&L ─────────────────────────────────────────────────────────────

    def load_daily_pnl(self) -> dict:
        return load_json(self._p("daily_pnl.json"),
                         {"date": None, "last_updated": None, "realized_pnl": 0.0,
                          "per_strategy": {}, "daily_loss_limit": -600.0,
                          "daily_loss_remaining": -600.0, "halt_triggered": False})

    def save_daily_pnl(self, pnl_state: dict) -> None:
        pnl_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self._p("daily_pnl.json"), pnl_state)

    def record_trade_pnl(self, strategy_id: str, pnl: float) -> None:
        state = self.load_daily_pnl()
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("date") != today:
            state = {"date": today, "realized_pnl": 0.0, "per_strategy": {},
                     "daily_loss_limit": state.get("daily_loss_limit", -600.0),
                     "daily_loss_remaining": state.get("daily_loss_limit", -600.0),
                     "halt_triggered": False}
        state["realized_pnl"] = round(state.get("realized_pnl", 0.0) + pnl, 2)
        sid = str(strategy_id)
        s = state.setdefault("per_strategy", {}).setdefault(sid, {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
        s["pnl"] = round(s["pnl"] + pnl, 2)
        s["trades"] += 1
        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
        limit = state.get("daily_loss_limit", -600.0)
        state["daily_loss_remaining"] = round(limit - state["realized_pnl"], 2)
        if state["realized_pnl"] < limit:
            state["halt_triggered"] = True
        self.save_daily_pnl(state)

    # ── Strategy Halts ────────────────────────────────────────────────────────

    def load_strategy_halts(self) -> dict:
        return load_json(self._p("strategy_halts.json"),
                         {"last_updated": None, "halts": {}})

    def record_strategy_halt(self, strategy_id: str, reason: str) -> None:
        state = self.load_strategy_halts()
        halts = state.get("halts", {})
        halts[str(strategy_id)] = {
            "halted":    True,
            "reason":    reason,
            "halted_at": datetime.now(timezone.utc).isoformat(),
        }
        data = {"last_updated": datetime.now(timezone.utc).isoformat(), "halts": halts}
        atomic_write_json(self._p("strategy_halts.json"), data)

    def clear_strategy_halt(self, strategy_id: str) -> None:
        state = self.load_strategy_halts()
        halts = state.get("halts", {})
        halts[str(strategy_id)] = {"halted": False, "reason": None, "halted_at": None}
        data = {"last_updated": datetime.now(timezone.utc).isoformat(), "halts": halts}
        atomic_write_json(self._p("strategy_halts.json"), data)

    def is_strategy_halted(self, strategy_id: str) -> bool:
        state = self.load_strategy_halts()
        return state.get("halts", {}).get(str(strategy_id), {}).get("halted", False)

    # ── Account State ─────────────────────────────────────────────────────────

    def load_account_state(self) -> dict:
        return load_json(self._p("account_state.json"),
                         {"last_updated": None, "account_id": None,
                          "account_halt": False, "account_halt_reason": None,
                          "daily_loss_triggered": False,
                          "trailing_drawdown_remaining": 800.0,
                          "max_drawdown_limit": 800.0, "session_open": False})

    def save_account_state(self, state: dict) -> None:
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self._p("account_state.json"), state)

    def is_account_halted(self) -> bool:
        return self.load_account_state().get("account_halt", False)

    def is_session_open(self) -> bool:
        return self.load_account_state().get("session_open", False)

    # ── Processed Signals (duplicate protection) ──────────────────────────────

    def load_processed_signals(self) -> dict:
        return load_json(self._p("processed_signals.json"),
                         {"last_updated": None, "session_date": None, "processed_ids": []})

    def save_processed_signals(self, state: dict) -> None:
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self._p("processed_signals.json"), state)

    def is_signal_processed(self, signal_id: str) -> bool:
        """Return True if this signal_id was already processed today."""
        state = self.load_processed_signals()
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("session_date") != today:
            return False
        return signal_id in state.get("processed_ids", [])

    def mark_signal_processed(self, signal_id: str) -> None:
        state = self.load_processed_signals()
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("session_date") != today:
            state = {"session_date": today, "processed_ids": []}
        ids = state.get("processed_ids", [])
        if signal_id not in ids:
            ids.append(signal_id)
        state["processed_ids"] = ids
        self.save_processed_signals(state)

    # ── Last Seen Bar ─────────────────────────────────────────────────────────

    def update_last_seen_bar(self, symbol: str, timestamp: str, bar_minutes: int) -> None:
        state = load_json(self._p("last_seen_bar.json"), {"last_updated": None, "bars": {}})
        bars = state.get("bars", {})
        bars[symbol] = {
            "timestamp":    timestamp,
            "bar_minutes":  bar_minutes,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        data = {"last_updated": datetime.now(timezone.utc).isoformat(), "bars": bars}
        atomic_write_json(self._p("last_seen_bar.json"), data)

    def get_last_seen_bar(self, symbol: str) -> dict:
        state = load_json(self._p("last_seen_bar.json"), {"bars": {}})
        return state.get("bars", {}).get(symbol, {})


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.update_heartbeat(mode="DRY_RUN", bar_loop_count=5)
        hb = load_json(Path(tmp) / "heartbeat.json")
        print(f"Heartbeat pid={hb['pid']} mode={hb['mode']}")

        sm.save_positions({"MESM5": {"net_pos": 1, "entry_px": 5320.0}}, source="test")
        pos = sm.load_positions()
        print(f"Positions: {pos['positions']}")

        sm.mark_signal_processed("strat2_MESM5_20260518_143000")
        dup = sm.is_signal_processed("strat2_MESM5_20260518_143000")
        new = sm.is_signal_processed("strat2_MESM5_20260518_143100")
        print(f"Duplicate detection: dup={dup} new={new}")

        sm.record_strategy_halt("7", "consecutive_losses_3")
        print(f"Strategy 7 halted: {sm.is_strategy_halted('7')}")
        print(f"Strategy 2 halted: {sm.is_strategy_halted('2')}")

        sm.record_trade_pnl("2", 47.50)
        sm.record_trade_pnl("2", -25.00)
        pnl = sm.load_daily_pnl()
        print(f"Daily P&L: {pnl['realized_pnl']} | strat 2: {pnl['per_strategy']['2']}")

        print("OK — standalone test passed")
