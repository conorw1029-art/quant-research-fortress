"""
tick_dashboard_server.py — Fortress Real-Time Web Dashboard
============================================================
Serves a live trading terminal at http://localhost:5050

Features:
  - SSE stream pushes full system snapshot every 3 seconds
  - Reads signal logs, bar files, state files in real-time
  - AI terminal via Claude (set ANTHROPIC_API_KEY)
  - All 44 strategies displayed with live P&L and status

Run:
  python tick_dashboard_server.py
  python tick_dashboard_server.py --port 5050 --host 0.0.0.0
  ANTHROPIC_API_KEY=sk-ant-... python tick_dashboard_server.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None

from flask import Flask, Response, request, jsonify, send_from_directory

sys.path.insert(0, str(Path(__file__).parent))

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).parent.parent
LOG_DIR       = ROOT / "06_live_trading" / "logs"
STATE_DIR     = ROOT / "06_live_trading" / "state"
BAR_DIR       = ROOT / "01_data" / "tick_bars"
CODE_DIR      = Path(__file__).parent
DASHBOARD_DIR = CODE_DIR / "tick_dashboard"

app = Flask(__name__, static_folder=None)

# ── Shared state ──────────────────────────────────────────────────────────────

_snapshot: dict = {}
_snap_lock      = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: Path, default=None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _parse_signal_line(line: str) -> dict | None:
    """Parse one JSONL line, tolerating NaN/Infinity written by numpy/pandas."""
    line = line.strip()
    if not line:
        return None
    # Replace bare NaN/Infinity (invalid JSON) with null before parsing
    import re as _re
    line = _re.sub(r':\s*NaN\b',      ': null', line)
    line = _re.sub(r':\s*Infinity\b', ': null', line)
    line = _re.sub(r':\s*-Infinity\b', ': null', line)
    try:
        return json.loads(line)
    except Exception:
        return None


def _normalize_record(r: dict) -> dict:
    """Map executor's native signal format to the fields _aggregate_signals expects."""
    # Time: executor writes 'timestamp', dashboard reads 'alert_time' / 'bar_time'
    if "alert_time" not in r and "bar_time" not in r:
        r["alert_time"] = r.get("timestamp", "")

    # Action: derive from event_type / signal / reason
    if "action" not in r:
        event_type = r.get("event_type", "")
        signal_val = r.get("signal")
        accepted   = r.get("accepted", False)
        reason     = r.get("reason", "").lower()

        if event_type == "exit":
            if reason == "stop":
                r["action"] = "STOP"
            elif reason == "target":
                r["action"] = "TARGET"
            elif reason in ("timeout", "time"):
                r["action"] = "TIMEOUT"
            else:
                r["action"] = "EXIT"
        elif signal_val is not None:
            if accepted:
                r["action"] = "BUY" if signal_val == 1 else "SELL"
            else:
                r["action"] = "__REJECTED__"
        elif event_type in ("coordinator_decision",):
            r["action"] = "__SKIP__"
        else:
            r["action"] = event_type.upper() if event_type else ""

    # Price aliases: executor uses 'entry' for entry price
    if "entry_px" not in r and "entry" in r:
        r["entry_px"] = r["entry"]

    return r


def _read_signal_logs(n_days: int = 7) -> list[dict]:
    records = []
    for d in range(n_days):
        dt = datetime.now(timezone.utc) - timedelta(days=d)
        p  = LOG_DIR / f"signals_{dt.strftime('%Y%m%d')}.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                r = _parse_signal_line(line)
                if r:
                    records.append(_normalize_record(r))
    return records


def _latest_prices() -> dict:
    prices = {}
    for sym in ("GC", "ES", "NQ", "SI", "CL"):
        p = BAR_DIR / f"{sym}_bars_30m.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
            if df.empty:
                continue
            last       = df.iloc[-1]
            prev       = df.iloc[-2] if len(df) > 1 else last
            close      = float(last["close"])
            prev_close = float(prev["close"])
            change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0
            prices[sym] = {
                "price":      round(close, 2),
                "change_pct": round(change_pct, 3),
                "volume":     int(last.get("volume", 0)),
                "cvd":        int(last.get("cvd", 0)),
                "bar_time":   str(df.index[-1])[:16],
            }
        except Exception:
            pass
    return prices


def _load_allowlist() -> dict:
    path = CODE_DIR / "live_strategy_allowlist.yaml"
    if yaml is None or not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {int(k): v for k, v in (data.get("strategies") or {}).items()}
    except Exception:
        return {}


def _load_portfolio() -> list:
    """Parse PORTFOLIO from source without importing the executor."""
    import re, ast
    try:
        src = (CODE_DIR / "tick_live_executor.py").read_text(encoding="utf-8")
        m = re.search(r"^PORTFOLIO\s*=\s*(\[.+?^\])", src, re.MULTILINE | re.DOTALL)
        if not m:
            return []
        block = re.sub(r"#[^\n]*", "", m.group(1))  # strip inline comments
        return list(ast.literal_eval(block))
    except Exception:
        return []


def _aggregate_signals(records: list[dict]) -> tuple[dict, list, list]:
    today = datetime.now(timezone.utc).date().isoformat()
    _close_actions = {"CLOSE", "EXIT", "TIMEOUT", "TARGET", "STOP", "FILLED"}

    per_strat: dict = defaultdict(lambda: {
        "entries_today": 0, "exits_today": 0,
        "pnl_today": 0.0,   "pnl_7d": 0.0,
        "wins_today": 0,    "losses_today": 0,
        "last_action": None, "last_time": None,
        "halted": False,
    })

    equity_by_hour: dict = defaultdict(float)
    recent: list = []

    for r in sorted(records, key=lambda x: x.get("alert_time", ""), reverse=False):
        sid    = r.get("strategy_id", 0)
        action = r.get("action", r.get("type", ""))
        if action in ("__REJECTED__", "__SKIP__", ""):
            continue
        pnl    = r.get("pnl", r.get("dollar_pnl"))
        ts     = r.get("alert_time", r.get("bar_time", ""))
        is_td  = ts[:10] == today if ts else False

        if is_td:
            if action in ("BUY", "SELL"):
                per_strat[sid]["entries_today"] += 1
            if action in _close_actions:
                per_strat[sid]["exits_today"] += 1
                if pnl is not None:
                    pf = float(pnl)
                    per_strat[sid]["pnl_today"] += pf
                    per_strat[sid]["wins_today"]   += (pf >= 0)
                    per_strat[sid]["losses_today"] += (pf < 0)

        if pnl is not None and action in _close_actions:
            per_strat[sid]["pnl_7d"] += float(pnl)
            if ts:
                equity_by_hour[ts[:13]] += float(pnl)

        if action == "HALTED":
            per_strat[sid]["halted"] = True

        if ts and (per_strat[sid]["last_time"] is None or ts > per_strat[sid]["last_time"]):
            per_strat[sid]["last_action"] = action
            per_strat[sid]["last_time"]   = ts

        if action in ("BUY", "SELL", "CLOSE", "EXIT", "TARGET", "STOP", "HALTED", "TIMEOUT"):
            recent.append({
                "id":     sid,
                "action": action,
                "symbol": r.get("symbol", "?"),
                "time":   ts[11:19] if ts else "?",
                "price":  r.get("entry_px", r.get("exit_px")),
                "pnl":    round(float(pnl), 2) if pnl is not None else None,
                "today":  is_td,
            })

    # Equity curve — cumulative closed-trade P&L over time
    cum = 0.0
    equity_curve = []
    for k in sorted(equity_by_hour.keys()):
        cum += equity_by_hour[k]
        equity_curve.append({"t": k + ":00Z", "v": round(cum, 2)})

    return dict(per_strat), list(reversed(recent[-60:])), equity_curve


_FOMC_FALLBACK = [
    "2026-06-10", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]

def _fomc_countdown() -> dict:
    """Parse FOMC dates from source without importing tick_strategies_v9."""
    import re
    today = datetime.now(timezone.utc).date()
    try:
        src = (CODE_DIR / "tick_strategies_v9.py").read_text(encoding="utf-8", errors="replace")
        m = re.search(r"_FOMC_ANNOUNCEMENT_DATES\s*=\s*pd\.to_datetime\(\[(.+?)\]\)", src, re.DOTALL)
        if m:
            dates_str = re.findall(r'"(\d{4}-\d{2}-\d{2})"', m.group(1))
            future = [date.fromisoformat(d) for d in dates_str if date.fromisoformat(d) >= today]
            if future:
                nxt = min(future)
                return {"date": str(nxt), "days": (nxt - today).days}
    except Exception:
        pass
    # Fallback to hardcoded upcoming dates
    try:
        future = [date.fromisoformat(d) for d in _FOMC_FALLBACK if date.fromisoformat(d) >= today]
        if future:
            nxt = min(future)
            return {"date": str(nxt), "days": (nxt - today).days}
    except Exception:
        pass
    return {"date": "?", "days": "?"}


def _expiry_status() -> list:
    """Parse TV_CONTRACT_MAP and _CONTRACT_EXPIRY from source without importing."""
    import re, ast
    try:
        src = (CODE_DIR / "tick_live_executor.py").read_text(encoding="utf-8")

        m_map = re.search(r"^TV_CONTRACT_MAP\s*=\s*(\{.+?\})", src, re.MULTILINE | re.DOTALL)
        m_exp = re.search(r"^_CONTRACT_EXPIRY\s*=\s*(\{.+?\})", src, re.MULTILINE | re.DOTALL)
        if not m_map or not m_exp:
            return []

        tv_contract_map = ast.literal_eval(re.sub(r"#[^\n]*", "", m_map.group(1)))
        contract_expiry = ast.literal_eval(re.sub(r"#[^\n]*", "", m_exp.group(1)))

        today, seen, result = datetime.now(timezone.utc).date(), set(), []
        for tv_sym in set(tv_contract_map.values()):
            if tv_sym in seen:
                continue
            exp = contract_expiry.get(tv_sym)
            if not exp:
                continue
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            days = (exp_date - today).days
            seen.add(tv_sym)
            result.append({"sym": tv_sym, "expiry": exp, "days": days,
                           "urgent": days <= 7, "warning": days <= 21})
        return sorted(result, key=lambda x: x["days"])
    except Exception:
        return []


def _build_snapshot() -> dict:
    records  = _read_signal_logs(7)
    hb       = _read_json(STATE_DIR / "heartbeat.json")
    raw_pos  = _read_json(STATE_DIR / "positions.json")
    dpnl     = _read_json(STATE_DIR / "daily_pnl.json")
    acct     = _read_json(STATE_DIR / "account_state.json")
    halts    = _read_json(STATE_DIR / "strategy_halts.json", {})
    testing  = _read_json(STATE_DIR / "testing_pnl.json", {})

    allowlist = _load_allowlist()
    portfolio = _load_portfolio()
    prices    = _latest_prices()
    per_strat, recent_sigs, equity_curve = _aggregate_signals(records)
    fomc      = _fomc_countdown()
    expiry    = _expiry_status()

    strategies = []
    for entry in portfolio:
        sid, sym, bar_min, strat_name, params, allowed_hrs, sess_block, version = entry
        al = allowlist.get(sid, {})
        ps = per_strat.get(sid, {})
        strategies.append({
            "id":         sid,
            "symbol":     sym,
            "bar_min":    bar_min,
            "name":       strat_name,
            "version":    version,
            "status":     al.get("status", "UNKNOWN"),
            "entries_today":  ps.get("entries_today", 0),
            "exits_today":    ps.get("exits_today", 0),
            "pnl_today":      round(ps.get("pnl_today", 0.0), 2),
            "pnl_7d":         round(ps.get("pnl_7d", 0.0), 2),
            "wins_today":     ps.get("wins_today", 0),
            "losses_today":   ps.get("losses_today", 0),
            "last_action":    ps.get("last_action"),
            "last_time":      (ps.get("last_time") or "")[-8:],
            "halted":         bool(halts.get(str(sid))) or ps.get("halted", False),
            "hours_filtered": allowed_hrs is not None,
        })

    total_pnl_today = sum(ps.get("pnl_today", 0) for ps in per_strat.values())
    total_pnl_7d    = sum(ps.get("pnl_7d", 0)    for ps in per_strat.values())

    # Parse open positions
    positions = []
    try:
        def _dig(obj):
            if isinstance(obj, dict):
                if "net_pos" in obj:
                    return obj
                for v in obj.values():
                    r = _dig(v)
                    if r:
                        return r
            return None
        pos_dict = raw_pos
        for _ in range(4):
            pos_dict = pos_dict.get("positions", pos_dict) if isinstance(pos_dict, dict) else {}
        for sym, p in pos_dict.items():
            if isinstance(p, dict) and "net_pos" in p:
                positions.append({
                    "sym": sym, "net_pos": p["net_pos"],
                    "entry_px": p.get("entry_px"),
                    "strategy_id": p.get("strategy_id"),
                })
    except Exception:
        pass

    dd_used    = acct.get("max_drawdown_limit", 800) - acct.get("trailing_drawdown_remaining", 800)
    daily_pct  = abs(total_pnl_today) / abs(dpnl.get("daily_loss_limit", 600)) * 100 if total_pnl_today < 0 else 0
    dd_pct     = dd_used / acct.get("max_drawdown_limit", 800) * 100

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "heartbeat": {
            "running":          bool(hb.get("mode")),
            "mode":             hb.get("mode", "OFFLINE"),
            "last_check":       (hb.get("timestamp", hb.get("ts", "")) or "")[-8:],
            "data_fresh":       hb.get("data_fresh", False),
            "broker_connected": hb.get("broker_connected", False),
        },
        "portfolio": {
            "pnl_today":      round(total_pnl_today, 2),
            "pnl_7d":         round(total_pnl_7d, 2),
            "daily_limit":    dpnl.get("daily_loss_limit", -600),
            "daily_pct_used": round(daily_pct, 1),
            "halt_triggered": dpnl.get("halt_triggered", False),
            "open_positions": len(positions),
            "trail_dd_used":  round(dd_used, 2),
            "trail_dd_limit": acct.get("max_drawdown_limit", 800),
            "trail_dd_pct":   round(dd_pct, 1),
            "account_halt":   acct.get("account_halt", False),
        },
        "strategies": strategies,
        "positions":   positions,
        "signals":     recent_sigs,
        "equity_curve": equity_curve,
        "market":      prices,
        "fomc":        fomc,
        "expiry":      expiry,
        "risk": {
            "strategies_active":  sum(1 for s in strategies
                                      if s["status"] in ("DEMO_CANDIDATE","ENABLED_DRY_RUN","REVIEW_REQUIRED")
                                      and not s["halted"]),
            "strategies_halted":  sum(1 for s in strategies if s["halted"]),
            "strategies_disabled": sum(1 for s in strategies if s["status"] in ("DISABLED_FOR_LIVE","RESEARCH_ONLY")),
        },
        "testing": {
            "net_pnl":  round(float(testing.get("cumulative_realized_pnl", 0.0)), 2),
            "trades":   testing.get("trades", 0),
            "wins":     testing.get("wins", 0),
            "losses":   testing.get("losses", 0),
            "win_rate": (round(100.0 * testing.get("wins", 0) / testing.get("trades", 1), 1)
                         if testing.get("trades", 0) else 0.0),
            "since":    testing.get("testing_start"),
            "per_strategy": testing.get("per_strategy", {}),
        },
    }


# ── Background thread ─────────────────────────────────────────────────────────

def _updater():
    while True:
        try:
            snap = _build_snapshot()
            with _snap_lock:
                global _snapshot
                _snapshot = snap
        except Exception:
            pass
        time.sleep(3)


# ── Kill switch path (must match executor's ROOT / KILL_SWITCH.txt) ───────────
KILL_SWITCH_PATH = ROOT / "KILL_SWITCH.txt"


def _ks_status() -> str:
    try:
        for line in KILL_SWITCH_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.upper()
    except FileNotFoundError:
        pass
    return "RUN"


# ── Optional auth for mutating / costly endpoints ──────────────────────────────
# Defaults to OPEN (preserves existing browser access). Set DASHBOARD_TOKEN in
# /opt/fortress/.env to require ?token=XXX or  X-Dash-Token: XXX  on
# /api/halt, /api/resume and /api/chat (halt/resume = control, chat = $ API spend).
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")


def _dash_auth() -> bool:
    """True if the request is authorised (or auth is disabled)."""
    if not DASHBOARD_TOKEN:
        return True
    tok = (request.args.get("token", "")
           or request.headers.get("X-Dash-Token", "")
           or request.headers.get("Authorization", "").replace("Bearer ", "").strip())
    return tok == DASHBOARD_TOKEN


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(DASHBOARD_DIR), "index.html")


@app.route("/api/snapshot")
def api_snapshot():
    with _snap_lock:
        return jsonify(_snapshot)


@app.route("/api/stream")
def api_stream():
    def generate():
        while True:
            with _snap_lock:
                data = json.dumps(_snapshot, default=str)
            yield f"data: {data}\n\n"
            time.sleep(3)
    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/kill-switch")
def api_kill_switch_status():
    return jsonify({"status": _ks_status()})


@app.route("/api/halt", methods=["POST"])
def api_halt():
    if not _dash_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        KILL_SWITCH_PATH.write_text("STOP\n", encoding="utf-8")
        return jsonify({"ok": True, "status": "STOP"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/resume", methods=["POST"])
def api_resume():
    if not _dash_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        KILL_SWITCH_PATH.write_text("RUN\n", encoding="utf-8")
        return jsonify({"ok": True, "status": "RUN"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not _dash_auth():
        return jsonify({"error": "unauthorized"}), 401
    body    = request.json or {}
    user_msg = body.get("message", "").strip()
    if not user_msg:
        return jsonify({"error": "empty"}), 400

    with _snap_lock:
        snap = dict(_snapshot)

    strat_lines = [
        f"ID{s['id']:>3} {s['symbol']:<2}/{s['name']:<30}/{s['bar_min']}m "
        f"status={s['status']:<20} today={s['pnl_today']:+.2f} 7d={s['pnl_7d']:+.2f}"
        for s in snap.get("strategies", [])
    ]

    system_ctx = f"""You are the AI trading assistant for Fortress, a quantitative futures trading system.
You trade 44 strategies across GC (gold), ES (S&P 500), NQ (Nasdaq), SI (silver), CL (crude oil) futures using micro contracts. Strategies V1-V9 are OHLCV/CVD. V10 (IDs 40-44) are L2 tick microstructure strategies on GC and SI requiring L2 bar files.

LIVE STATE ({snap.get('ts','')[:16]} UTC):
Mode: {snap.get('heartbeat',{}).get('mode','OFFLINE')} | Broker: {'CONNECTED' if snap.get('heartbeat',{}).get('broker_connected') else 'DISCONNECTED'} | Data: {'FRESH' if snap.get('heartbeat',{}).get('data_fresh') else 'STALE'}
Today P&L: ${snap.get('portfolio',{}).get('pnl_today', 0):+.2f} | 7-Day: ${snap.get('portfolio',{}).get('pnl_7d', 0):+.2f}
Open positions: {snap.get('portfolio',{}).get('open_positions', 0)} | Daily limit used: {snap.get('portfolio',{}).get('daily_pct_used', 0):.1f}%
Trail DD used: ${snap.get('portfolio',{}).get('trail_dd_used', 0):.2f} / ${snap.get('portfolio',{}).get('trail_dd_limit', 800):.0f}
Next FOMC: {snap.get('fomc',{}).get('date','?')} ({snap.get('fomc',{}).get('days','?')} days)

MARKET PRICES: {json.dumps({k: f"${v.get('price',0):,.2f} ({v.get('change_pct',0):+.2f}%)" for k,v in snap.get('market',{}).items()})}

STRATEGIES ({len(snap.get('strategies',[]))} total):
{chr(10).join(strat_lines)}

OPEN POSITIONS: {json.dumps(snap.get('positions',[]))}

Be concise and direct. Answer in under 150 words unless asked for detail. Focus on actionable insights."""

    def generate():
        try:
            import anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                yield "data: " + json.dumps({"t": "Set ANTHROPIC_API_KEY to enable the AI terminal.", "done": True}) + "\n\n"
                return
            client = anthropic.Anthropic(api_key=api_key)
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=system_ctx,
                messages=[{"role": "user", "content": user_msg}],
            ) as s:
                for chunk in s.text_stream:
                    yield "data: " + json.dumps({"t": chunk, "done": False}) + "\n\n"
            yield "data: " + json.dumps({"t": "", "done": True}) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"t": f"Error: {e}", "done": True}) + "\n\n"

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5050)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    threading.Thread(target=_updater, daemon=True).start()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  {'='*50}")
    print(f"  FORTRESS DASHBOARD  {url}")
    print(f"  {'='*50}")
    print(f"  AI Terminal: set ANTHROPIC_API_KEY env var")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
