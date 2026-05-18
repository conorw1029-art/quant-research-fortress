"""
test_state_manager.py — Tests for tick_state_manager.py
========================================================
All tests use temp directories — no production state is read or written.

Run:
  venv_new\Scripts\python.exe -X utf8 test_state_manager.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tick_state_manager import StateManager, load_json, atomic_write_json

PASS_COUNT = 0
FAIL_COUNT = 0


def _check(name: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}  {detail}")


def test_skeleton_files_created():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        expected = [
            "positions.json", "open_orders.json", "daily_pnl.json",
            "strategy_halts.json", "account_state.json", "last_seen_bar.json",
            "heartbeat.json", "active_brackets.json", "processed_signals.json",
        ]
        for fname in expected:
            _check(f"skeleton file exists: {fname}",
                   (Path(tmp) / fname).exists())


def test_atomic_write_produces_valid_json():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.json"
        atomic_write_json(p, {"foo": 42, "bar": [1, 2, 3]})
        data = load_json(p)
        _check("atomic write + read round-trip", data == {"foo": 42, "bar": [1, 2, 3]})


def test_atomic_write_no_tmp_file_left():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test2.json"
        atomic_write_json(p, {"x": 1})
        tmp_path = p.with_suffix(".tmp")
        _check("no .tmp file remains after write", not tmp_path.exists())


def test_load_json_missing_file_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "nonexistent.json"
        result = load_json(p, default={"fallback": True})
        _check("missing file returns default", result == {"fallback": True})


def test_load_json_corrupt_file_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "corrupt.json"
        p.write_text("{this is not json!!!}", encoding="utf-8")
        result = load_json(p, default={"safe": True})
        _check("corrupt JSON returns default", result == {"safe": True})


def test_heartbeat_updates():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.update_heartbeat(mode="DRY_RUN", bar_loop_count=10)
        hb = load_json(Path(tmp) / "heartbeat.json")
        _check("heartbeat has pid", hb.get("pid") == os.getpid())
        _check("heartbeat has mode", hb.get("mode") == "DRY_RUN")
        _check("heartbeat bar_loop_count", hb.get("bar_loop_count") == 10)
        _check("heartbeat timestamp set", hb.get("timestamp") is not None)


def test_heartbeat_not_stale_immediately():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.update_heartbeat()
        _check("fresh heartbeat not stale", not sm.is_heartbeat_stale(max_age_seconds=60))


def test_positions_save_load():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        positions = {"MESM5": {"net_pos": 1, "entry_px": 5320.25, "stop_px": 5308.0}}
        sm.save_positions(positions, source="broker_confirmed")
        loaded = sm.load_positions()
        _check("positions round-trip", loaded["positions"] == positions)
        _check("positions source", loaded["source"] == "broker_confirmed")


def test_is_locally_flat():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        _check("empty positions is flat", sm.is_locally_flat())
        sm.save_positions({"MESM5": {"net_pos": 1}})
        _check("non-zero position not flat", not sm.is_locally_flat())
        sm.save_positions({"MESM5": {"net_pos": 0}})
        _check("zero position is flat", sm.is_locally_flat())


def test_active_brackets_add_remove():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        bracket = {
            "symbol": "MESM5",
            "entry_order_id": "1001",
            "stop_order_id": "1002",
            "target_order_id": "1003",
            "entry_filled": False,
        }
        sm.add_bracket("2", bracket)
        loaded = sm.get_bracket("2")
        _check("bracket add and load", loaded["symbol"] == "MESM5")
        _check("bracket stop_order_id", loaded["stop_order_id"] == "1002")

        sm.remove_bracket("2")
        after_remove = sm.get_bracket("2")
        _check("bracket removed", after_remove == {})


def test_processed_signals_duplicate_detection():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sig_id = "strat2_MESM5_20260518_143000"

        _check("new signal not processed", not sm.is_signal_processed(sig_id))
        sm.mark_signal_processed(sig_id)
        _check("processed signal detected", sm.is_signal_processed(sig_id))
        _check("different signal not processed",
               not sm.is_signal_processed("strat2_MESM5_20260518_150000"))


def test_processed_signals_resets_on_new_day():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.mark_signal_processed("strat2_MESM5_20260517_143000")

        # Manually set session_date to yesterday
        p = Path(tmp) / "processed_signals.json"
        data = load_json(p)
        data["session_date"] = "2026-05-17"
        atomic_write_json(p, data)

        # Today's check should return False (different date)
        _check("signal from yesterday not detected today",
               not sm.is_signal_processed("strat2_MESM5_20260517_143000"))


def test_strategy_halts():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        _check("strategy not halted initially", not sm.is_strategy_halted("7"))
        sm.record_strategy_halt("7", "consecutive_losses_3")
        _check("strategy halted after record", sm.is_strategy_halted("7"))
        _check("other strategy not halted", not sm.is_strategy_halted("2"))
        sm.clear_strategy_halt("7")
        _check("strategy not halted after clear", not sm.is_strategy_halted("7"))


def test_record_trade_pnl():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.record_trade_pnl("2", 47.50)
        sm.record_trade_pnl("2", -25.00)
        sm.record_trade_pnl("7", 30.00)
        state = sm.load_daily_pnl()
        _check("total pnl", abs(state["realized_pnl"] - 52.50) < 0.01)
        _check("strategy 2 pnl", abs(state["per_strategy"]["2"]["pnl"] - 22.50) < 0.01)
        _check("strategy 2 trade count", state["per_strategy"]["2"]["trades"] == 2)
        _check("strategy 2 wins", state["per_strategy"]["2"]["wins"] == 1)
        _check("strategy 7 pnl", abs(state["per_strategy"]["7"]["pnl"] - 30.00) < 0.01)


def test_account_state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        _check("account not halted initially", not sm.is_account_halted())
        state = sm.load_account_state()
        state["account_halt"] = True
        state["account_halt_reason"] = "daily_loss_limit"
        sm.save_account_state(state)
        _check("account halted after save", sm.is_account_halted())


def test_last_seen_bar():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(state_dir=tmp)
        sm.update_last_seen_bar("MESM5", "2026-05-18T14:30:00Z", 15)
        bar = sm.get_last_seen_bar("MESM5")
        _check("last seen bar timestamp", bar["timestamp"] == "2026-05-18T14:30:00Z")
        _check("last seen bar minutes", bar["bar_minutes"] == 15)
        _check("unknown symbol returns empty", sm.get_last_seen_bar("UNKNOWN") == {})


# ── Run all tests ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*62}")
    print(f"  STATE MANAGER TEST SUITE")
    print(f"{'='*62}")

    tests = [
        test_skeleton_files_created,
        test_atomic_write_produces_valid_json,
        test_atomic_write_no_tmp_file_left,
        test_load_json_missing_file_returns_default,
        test_load_json_corrupt_file_returns_default,
        test_heartbeat_updates,
        test_heartbeat_not_stale_immediately,
        test_positions_save_load,
        test_is_locally_flat,
        test_active_brackets_add_remove,
        test_processed_signals_duplicate_detection,
        test_processed_signals_resets_on_new_day,
        test_strategy_halts,
        test_record_trade_pnl,
        test_account_state,
        test_last_seen_bar,
    ]

    for test_fn in tests:
        section = test_fn.__name__.replace("test_", "").replace("_", " ").title()
        print(f"\n  [{section}]")
        try:
            test_fn()
        except Exception as e:
            FAIL_COUNT_local = 1
            print(f"  FAIL  {test_fn.__name__} raised exception: {e}")
            global FAIL_COUNT
            FAIL_COUNT += 1

    print(f"\n{'='*62}")
    print(f"  Results: {PASS_COUNT} PASS  {FAIL_COUNT} FAIL")
    print(f"{'='*62}\n")

    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
