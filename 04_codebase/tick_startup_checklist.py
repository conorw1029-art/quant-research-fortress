"""
tick_startup_checklist.py — Pre-Flight System Check
====================================================
Run before starting the live executor to confirm everything is ready.

Checks:
  1. Python imports and module availability
  2. Bar data freshness (warns if stale, errors if missing)
  3. Kill switch state
  4. Allowlist integrity (every PORTFOLIO strategy has an allowlist entry)
  5. Contract expiry warnings (upcoming rollovers)
  6. News monitor connectivity
  7. Bracket order dry-run validation
  8. Risk config sanity
  9. Strategy compute smoke-test (1 strategy, quick)

Exit code:
  0 — all checks pass (or only warnings)
  1 — one or more CRITICAL failures

Usage:
  python tick_startup_checklist.py             # full check
  python tick_startup_checklist.py --quick     # critical checks only (no net)
  python tick_startup_checklist.py --json      # machine-readable output
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Reconfigure stdout for UTF-8 so box-drawing chars work on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT      = Path(__file__).parent.parent
BAR_DIR   = ROOT / "01_data" / "tick_bars"
CODE_DIR  = Path(__file__).parent

sys.path.insert(0, str(CODE_DIR))

# ── Result accumulator ────────────────────────────────────────────────────────

_results: list[dict] = []

def _record(name: str, status: str, detail: str = "", critical: bool = False):
    _results.append({
        "check":    name,
        "status":   status,    # "PASS" | "WARN" | "FAIL"
        "detail":   detail,
        "critical": critical,
    })
    icon  = {"PASS": "✔", "WARN": "⚠", "FAIL": "✖"}.get(status, "?")
    label = f"[CRITICAL]" if critical and status == "FAIL" else ""
    print(f"  {icon} {name:<45} {status} {label}")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")


def _section(title: str):
    print(f"\n{'─' * 62}")
    print(f"  {title}")
    print(f"{'─' * 62}")


# ── Check 1: Imports ──────────────────────────────────────────────────────────

def check_imports():
    _section("1. Module Imports")
    modules = [
        ("tick_backtest_engine",  "SPECS, compute_atr, run_backtest"),
        ("tick_strategies",       "STRATEGY_MAP"),
        ("tick_strategies_v2",    "STRAT_MAP"),
        ("tick_strategies_v3",    "STRAT_MAP_V3"),
        ("tick_strategies_v4",    "STRAT_MAP_V4"),
        ("tick_risk_manager",     "RiskManager, RiskConfig"),
        ("tick_tradovate_client", "TradovateClient"),
        ("tick_key_levels",       "compute_key_levels"),
        ("tick_live_executor",    "PORTFOLIO, RISK_CFG, PositionTracker"),
    ]
    for mod, symbols in modules:
        try:
            imported = __import__(mod)
            for sym in symbols.split(","):
                sym = sym.strip()
                if not hasattr(imported, sym):
                    _record(f"import {mod}.{sym}", "WARN",
                            f"{sym} not found in {mod}", critical=False)
                    continue
            _record(f"import {mod}", "PASS")
        except Exception as e:
            _record(f"import {mod}", "FAIL", str(e), critical=True)

    # Optional modules
    for mod in ("tick_strategies_v5", "tick_news_monitor"):
        try:
            __import__(mod)
            _record(f"import {mod} (optional)", "PASS")
        except Exception as e:
            _record(f"import {mod} (optional)", "WARN", str(e))


# ── Check 2: Bar Data Freshness ───────────────────────────────────────────────

def check_bar_data(quick: bool = False):
    _section("2. Bar Data Freshness")

    required = [
        ("GC",  1),  ("GC",  3),  ("GC",  5),  ("GC", 15), ("GC", 30),
        ("ES",  3),  ("ES", 15),  ("ES", 30),
        ("NQ",  3),  ("NQ", 15),  ("NQ", 30),
    ]
    stale_threshold_min = 60   # warn if last bar > 60 min old during market hours
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() in (5, 6)

    for sym, bar_min in required:
        path = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
        if not path.exists():
            _record(f"bars {sym}/{bar_min}m", "FAIL",
                    f"File not found: {path}", critical=True)
            continue
        try:
            import pandas as pd
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index, utc=True)
            last_ts = df.index[-1]
            age_min = (now - last_ts).total_seconds() / 60
            n_rows  = len(df)
            if is_weekend:
                _record(f"bars {sym}/{bar_min}m ({n_rows} rows)", "PASS",
                        f"last bar {last_ts.strftime('%Y-%m-%d %H:%M')} UTC "
                        f"({age_min:.0f}min ago — weekend, OK)")
            elif age_min > stale_threshold_min:
                _record(f"bars {sym}/{bar_min}m ({n_rows} rows)", "WARN",
                        f"STALE: last bar {last_ts.strftime('%Y-%m-%d %H:%M')} UTC "
                        f"({age_min:.0f}min ago) — run tick_bar_builder.py to refresh")
            else:
                _record(f"bars {sym}/{bar_min}m ({n_rows} rows)", "PASS",
                        f"last bar {last_ts.strftime('%Y-%m-%d %H:%M')} UTC ({age_min:.0f}min ago)")
        except Exception as e:
            _record(f"bars {sym}/{bar_min}m", "FAIL", str(e), critical=True)


# ── Check 3: Kill Switch ──────────────────────────────────────────────────────

def check_kill_switch():
    _section("3. Kill Switch")
    ks_path = ROOT / "KILL_SWITCH.txt"
    if not ks_path.exists():
        _record("kill switch", "PASS", "KILL_SWITCH.txt not present — OK")
        return
    try:
        for line in ks_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.upper() == "STOP":
                _record("kill switch", "FAIL",
                        f"KILL_SWITCH.txt contains 'STOP' — executor will not start. "
                        f"Remove or clear the file.", critical=True)
                return
        _record("kill switch", "PASS", "KILL_SWITCH.txt present but not active")
    except Exception as e:
        _record("kill switch", "WARN", f"Could not read KILL_SWITCH.txt: {e}")


# ── Check 4: Allowlist Integrity ──────────────────────────────────────────────

def check_allowlist():
    _section("4. Allowlist Integrity")
    allowlist_path = CODE_DIR / "live_strategy_allowlist.yaml"
    if not allowlist_path.exists():
        _record("allowlist file", "WARN", "live_strategy_allowlist.yaml not found — all strategies will run in dry-run")
        return

    try:
        import yaml
        with open(allowlist_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        allowlist = {int(k): v for k, v in (data.get("strategies") or {}).items()}
    except Exception as e:
        _record("allowlist parse", "FAIL", str(e), critical=True)
        return

    _record(f"allowlist file ({len(allowlist)} entries)", "PASS")

    # Cross-check with PORTFOLIO
    try:
        from tick_live_executor import PORTFOLIO
        portfolio_ids = {p[0] for p in PORTFOLIO}
        missing_ids   = portfolio_ids - set(allowlist.keys())
        extra_ids     = set(allowlist.keys()) - portfolio_ids

        if missing_ids:
            _record("allowlist coverage", "WARN",
                    f"Strategy IDs in PORTFOLIO but NOT in allowlist: {sorted(missing_ids)}\n"
                    f"These will run unchecked in dry-run. Add entries to allowlist.")
        else:
            _record("allowlist coverage", "PASS",
                    f"All {len(portfolio_ids)} portfolio strategies have allowlist entries")

        if extra_ids:
            _record("allowlist extra entries", "WARN",
                    f"Strategy IDs in allowlist but NOT in PORTFOLIO: {sorted(extra_ids)}\n"
                    f"Stale entries — can be removed")

        # Check statuses
        demo_candidates = [sid for sid, e in allowlist.items() if e.get("status") == "DEMO_CANDIDATE"]
        if len(demo_candidates) > 1:
            _record("demo candidates", "WARN",
                    f"Multiple DEMO_CANDIDATE strategies: {demo_candidates}\n"
                    f"Only one should be DEMO_CANDIDATE at a time")
        elif len(demo_candidates) == 1:
            sid  = demo_candidates[0]
            key  = allowlist[sid].get("key", f"strategy_{sid}")
            _record(f"demo candidate ({key})", "PASS")
        else:
            _record("demo candidates", "WARN",
                    "No DEMO_CANDIDATE strategy — demo auto-trade will have nothing to run")

    except Exception as e:
        _record("allowlist vs portfolio check", "WARN", str(e))


# ── Check 5: Contract Expiry ──────────────────────────────────────────────────

def check_contract_expiry():
    _section("5. Contract Expiry")
    try:
        from tick_live_executor import TV_CONTRACT_MAP, _CONTRACT_EXPIRY
        now = datetime.now(timezone.utc).date()
        all_ok = True
        for base, tv_sym in TV_CONTRACT_MAP.items():
            expiry_str = _CONTRACT_EXPIRY.get(tv_sym)
            if not expiry_str:
                continue
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            days   = (expiry - now).days
            if days < 0:
                _record(f"contract {tv_sym}", "FAIL",
                        f"EXPIRED {abs(days)} days ago! Update TV_CONTRACT_MAP.", critical=True)
                all_ok = False
            elif days <= 7:
                _record(f"contract {tv_sym}", "WARN",
                        f"Expires in {days} days ({expiry_str}) — UPDATE TV_CONTRACT_MAP SOON")
                all_ok = False
            elif days <= 21:
                _record(f"contract {tv_sym}", "WARN",
                        f"Expires in {days} days ({expiry_str}) — rollover window approaching")
            else:
                _record(f"contract {tv_sym}", "PASS", f"Expires {expiry_str} ({days} days)")
        if all_ok:
            pass  # all printed in loop
    except Exception as e:
        _record("contract expiry check", "WARN", str(e))


# ── Check 6: News Monitor Connectivity ───────────────────────────────────────

def check_news_monitor():
    _section("6. News Monitor Connectivity")
    try:
        from tick_news_monitor import NewsMonitor
        monitor = NewsMonitor.__new__(NewsMonitor)
        monitor.events    = []
        monitor.headlines = []
        monitor.last_calendar = None
        monitor.last_rss      = None
        monitor.cache_minutes = 60
        monitor.errors        = []

        ok = monitor.fetch_calendar()
        if ok and monitor.events:
            _record("ForexFactory calendar", "PASS",
                    f"{len(monitor.events)} events fetched")
        elif ok:
            _record("ForexFactory calendar", "WARN",
                    "Fetched but 0 events — may be weekend/holiday")
        else:
            _record("ForexFactory calendar", "WARN",
                    f"Fetch failed: {monitor.errors[-1] if monitor.errors else 'unknown'}")

        rss_ok = monitor.fetch_headlines()
        if rss_ok:
            _record("RSS headlines", "PASS",
                    f"{len(monitor.headlines)} headlines from {len(set(h['source'] for h in monitor.headlines))} sources")
        else:
            _record("RSS headlines", "WARN",
                    f"No headlines fetched: {monitor.errors[-1] if monitor.errors else 'unknown'}")

    except ImportError:
        _record("news monitor", "WARN", "tick_news_monitor.py not importable — news gate disabled")
    except Exception as e:
        _record("news monitor connectivity", "WARN", str(e))


# ── Check 7: Bracket Order Dry-Run ───────────────────────────────────────────

def check_bracket_order():
    _section("7. Bracket Order Dry-Run Validation")
    try:
        from tick_tradovate_client import TradovateClient
        try:
            from tick_live_executor import TV_CONTRACT_MAP
            mes_sym = TV_CONTRACT_MAP.get("MES", "MESM5")
            mnq_sym = TV_CONTRACT_MAP.get("MNQ", "MNQM5")
            mgc_sym = TV_CONTRACT_MAP.get("MGC", "MGCM5")
        except Exception:
            mes_sym, mnq_sym, mgc_sym = "MESM5", "MNQM5", "MGCM5"

        client = TradovateClient.create_dry_run()

        test_cases = [
            # (symbol, side, qty, entry, stop, target, should_pass)
            (mes_sym, "BUY",  1, 5200.0, 5193.0, 5221.0, True),   # ES long
            (mnq_sym, "SELL", 1, 18500.0, 18560.0, 18380.0, True), # NQ short
            (mgc_sym, "BUY",  1, 2300.0, 2285.0, 2345.0, True),   # GC long
            # Failure cases
            (mes_sym, "BUY",  1, 5200.0, 5210.0, 5221.0, False),  # stop above entry
            (mnq_sym, "BUY",  0, 18500.0, 18440.0, 18620.0, False),# qty=0
        ]

        passed = 0
        for sym, side, qty, entry, stop, tgt, expect_pass in test_cases:
            result = client.place_bracket_order(
                symbol=sym, side=side, quantity=qty,
                entry_type="Limit", entry_price=entry,
                stop_price=stop, target_price=tgt,
                demo=True, dry_run=True,
            )
            ok = result.get("ok", False)
            if ok == expect_pass:
                passed += 1
            else:
                _record(f"bracket {sym} {side}", "FAIL",
                        f"Expected ok={expect_pass}, got ok={ok}: {result.get('reason', '')}",
                        critical=True)

        _record(f"bracket validation ({passed}/{len(test_cases)} passed)", "PASS"
                if passed == len(test_cases) else "FAIL",
                "" if passed == len(test_cases) else "Some test cases failed",
                critical=(passed != len(test_cases)))

    except Exception as e:
        _record("bracket order validation", "FAIL", str(e), critical=True)


# ── Check 8: Risk Config Sanity ───────────────────────────────────────────────

def check_risk_config():
    _section("8. Risk Configuration Sanity")
    try:
        from tick_live_executor import RISK_CFG, USE_MICROS, MAX_CONTRACTS_PER_TRADE, ACCOUNT_EQUITY

        checks = [
            ("USE_MICROS", USE_MICROS, True,
             "Should be True until account reaches $5k+"),
            ("max_trade_risk_usd <= 200", RISK_CFG.max_trade_risk_usd <= 200, True,
             f"Current: ${RISK_CFG.max_trade_risk_usd} — micro contracts keep risk low"),
            ("max_portfolio_daily_loss > 0", RISK_CFG.max_portfolio_daily_loss_usd > 0, True,
             f"Current: ${RISK_CFG.max_portfolio_daily_loss_usd}"),
            ("max_account_trailing_dd > 0", RISK_CFG.max_account_trailing_dd_usd > 0, True,
             f"Current: ${RISK_CFG.max_account_trailing_dd_usd}"),
            ("MAX_CONTRACTS_PER_TRADE == 1", MAX_CONTRACTS_PER_TRADE == 1, True,
             f"Current: {MAX_CONTRACTS_PER_TRADE} — hard limit for micro mode"),
            ("use_ratchet enabled", RISK_CFG.use_ratchet, True,
             "Ratchet stop replaces impossible partial exits at qty=1"),
            ("max_consecutive_losses >= 2", RISK_CFG.max_consecutive_losses >= 2, True,
             f"Current: {RISK_CFG.max_consecutive_losses} losses before circuit breaker"),
            ("ACCOUNT_EQUITY > 0", ACCOUNT_EQUITY > 0, True,
             f"Current: ${ACCOUNT_EQUITY:,.0f}"),
        ]

        for name, val, expected, note in checks:
            ok = (val == expected) if isinstance(val, bool) else val
            status = "PASS" if ok else "WARN"
            _record(f"risk config: {name}", status, note)

        # Summarize key values
        print(f"\n      Risk limits summary:")
        print(f"        USE_MICROS:               {USE_MICROS}")
        print(f"        max_trade_risk_usd:        ${RISK_CFG.max_trade_risk_usd:,.0f}")
        print(f"        max_contracts:             {MAX_CONTRACTS_PER_TRADE}")
        print(f"        max_portfolio_daily_loss:  ${RISK_CFG.max_portfolio_daily_loss_usd:,.0f}")
        print(f"        max_account_trailing_dd:   ${RISK_CFG.max_account_trailing_dd_usd:,.0f}")
        print(f"        max_consecutive_losses:    {RISK_CFG.max_consecutive_losses}")
        print(f"        account_equity:            ${ACCOUNT_EQUITY:,.0f}")

    except Exception as e:
        _record("risk config check", "FAIL", str(e), critical=True)


# ── Check 9: Strategy Smoke-Test ──────────────────────────────────────────────

def check_strategy_smoke():
    _section("9. Strategy Compute Smoke-Test")
    try:
        import pandas as pd
        from tick_live_executor import PORTFOLIO, compute_signal, load_bars, current_atr

        # Test one strategy per version
        versions_tested = set()
        for (sid, sym, bar_min, strat_name, params, _, _, version) in PORTFOLIO:
            if version in versions_tested:
                continue
            df = load_bars(sym, bar_min, lookback=100)
            if df is None or len(df) < 20:
                _record(f"smoke {sid} {sym}/{strat_name} ({version})", "WARN",
                        "Insufficient bar data")
                versions_tested.add(version)
                continue
            try:
                sigs = compute_signal(df, strat_name, params, version)
                atr  = current_atr(df)
                last = int(sigs.iloc[-1]) if not sigs.empty else 0
                _record(f"smoke {sid} {sym}/{strat_name} ({version})", "PASS",
                        f"last_signal={last:+d}  atr={atr:.4f}  n_bars={len(df)}")
                versions_tested.add(version)
            except Exception as e:
                _record(f"smoke {sid} {sym}/{strat_name} ({version})", "FAIL",
                        str(e), critical=True)
                versions_tested.add(version)

    except Exception as e:
        _record("strategy smoke-test", "FAIL", str(e), critical=True)


# ── Check 11: Portfolio Coordinator ──────────────────────────────────────────

def check_coordinator():
    _section("11. Portfolio Coordinator")
    # 1. Import
    try:
        from tick_portfolio_coordinator import (
            PortfolioCoordinator, CoordinatorConfig,
            SignalIntent, Side, VirtualStrategyPosition, BrokerNetPosition,
            CoordinatorAction,
        )
    except Exception as e:
        _record("coordinator import", "FAIL",
                f"tick_portfolio_coordinator.py not importable: {e}", critical=True)
        return

    _record("coordinator import", "PASS")

    # 2. Executor integration — check _COORDINATOR_AVAILABLE flag
    try:
        import tick_live_executor as _exe
        available = getattr(_exe, "_COORDINATOR_AVAILABLE", None)
        if available is True:
            _record("coordinator wired in executor", "PASS",
                    "_COORDINATOR_AVAILABLE=True in tick_live_executor")
        elif available is False:
            _record("coordinator wired in executor", "FAIL",
                    "_COORDINATOR_AVAILABLE=False — import guard failed at executor load time",
                    critical=True)
        else:
            _record("coordinator wired in executor", "WARN",
                    "_COORDINATOR_AVAILABLE not found — executor may be outdated")
    except Exception as e:
        _record("coordinator executor check", "WARN", str(e))

    # 3. Instantiate with DRY_RUN defaults and run two sanity evaluations
    try:
        from datetime import datetime, timezone
        cfg = CoordinatorConfig(
            one_strategy_only_demo=False,
            max_net_contracts_per_symbol=1,
            max_total_open_symbols=10,
            allow_reversal=False,
            dry_run_only=True,
        )
        coord = PortfolioCoordinator(cfg)

        def _make_intent(sid, symbol, side):
            return SignalIntent(
                strategy_id=sid, strategy_key=f"{symbol}/test/1m",
                symbol=symbol, contract=symbol + "M5", side=side,
                desired_qty=1, entry_price=2000.0, stop_price=1990.0,
                target_price=2020.0, estimated_risk_usd=100.0,
                timestamp=datetime.now(timezone.utc),
            )

        # Sanity A: clean signal → ACCEPT_NEW
        dec_a = coord.evaluate_single_signal(
            _make_intent(1, "GC", Side.LONG), [], [], [], kill_switch=False,
        )
        if dec_a.action == CoordinatorAction.ACCEPT_NEW:
            _record("coordinator sanity A (ACCEPT_NEW)", "PASS",
                    "Clean GC long with no open positions → ACCEPT_NEW")
        else:
            _record("coordinator sanity A (ACCEPT_NEW)", "FAIL",
                    f"Expected ACCEPT_NEW, got {dec_a.action}: {dec_a.reason}",
                    critical=True)

        # Sanity B: kill switch → REJECT_CONFLICT
        dec_b = coord.evaluate_single_signal(
            _make_intent(1, "GC", Side.LONG), [], [], [], kill_switch=True,
        )
        if dec_b.action == CoordinatorAction.REJECT_CONFLICT and not dec_b.ok:
            _record("coordinator sanity B (kill switch)", "PASS",
                    "Kill switch active → REJECT_CONFLICT, ok=False")
        else:
            _record("coordinator sanity B (kill switch)", "FAIL",
                    f"Expected REJECT_CONFLICT/ok=False, got {dec_b.action}/ok={dec_b.ok}",
                    critical=True)

        # Sanity C: opposite signals in batch → both rejected
        intents = [
            _make_intent(16, "GC", Side.LONG),
            _make_intent(17, "GC", Side.SHORT),
        ]
        decisions = coord.evaluate_signals(intents, [], [], [])
        all_rejected = all(not d.ok for d in decisions.values())
        if all_rejected:
            _record("coordinator sanity C (conflict detection)", "PASS",
                    "Opposite GC signals → both REJECT_CONFLICT")
        else:
            accepted = [sid for sid, d in decisions.items() if d.ok]
            _record("coordinator sanity C (conflict detection)", "FAIL",
                    f"Opposite signals should both be rejected; accepted: {accepted}",
                    critical=True)

    except Exception as e:
        _record("coordinator sanity checks", "FAIL", str(e), critical=True)
        return

    # 4. Show active DRY_RUN config
    print(f"\n      Coordinator DRY_RUN config:")
    print(f"        max_net_contracts_per_symbol: {cfg.max_net_contracts_per_symbol}")
    print(f"        max_total_open_symbols:       {cfg.max_total_open_symbols}")
    print(f"        allow_reversal:               {cfg.allow_reversal}")
    print(f"        one_strategy_only_demo:       {cfg.one_strategy_only_demo}  (False in DRY_RUN)")
    print(f"        demo_strategy_key:            {cfg.demo_strategy_key}")
    print(f"        max_portfolio_risk_usd:       ${cfg.max_portfolio_risk_usd:,.0f}")


# ── Check 10: Log directory ───────────────────────────────────────────────────

def check_log_directory():
    _section("10. Log Directory")
    log_dir = ROOT / "06_live_trading" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _record("log directory", "PASS", str(log_dir))

    # Count recent signal logs
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    today_log = log_dir / f"signals_{today}.jsonl"
    if today_log.exists():
        n_lines = sum(1 for _ in today_log.open())
        _record(f"today's signal log ({today_log.name})", "PASS",
                f"{n_lines} signals logged so far today")
    else:
        _record("today's signal log", "PASS", "No log yet today — will be created on first run")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(output_json: bool = False):
    n_pass = sum(1 for r in _results if r["status"] == "PASS")
    n_warn = sum(1 for r in _results if r["status"] == "WARN")
    n_fail = sum(1 for r in _results if r["status"] == "FAIL")
    n_crit = sum(1 for r in _results if r["status"] == "FAIL" and r["critical"])

    print(f"\n{'═' * 62}")
    print(f"  STARTUP CHECKLIST SUMMARY")
    print(f"{'═' * 62}")
    print(f"  Checks:   {len(_results)} total")
    print(f"  Pass:     {n_pass}")
    print(f"  Warn:     {n_warn}")
    print(f"  Fail:     {n_fail} ({n_crit} critical)")

    if n_crit > 0:
        print(f"\n  *** DO NOT START — {n_crit} critical failure(s) must be resolved ***")
        fails = [r for r in _results if r["status"] == "FAIL" and r["critical"]]
        for f in fails:
            print(f"    ✖ {f['check']}: {f['detail']}")
    elif n_fail > 0:
        print(f"\n  *** CAUTION — {n_fail} failure(s) detected (not critical) ***")
        print(f"  Safe to start dry-run. Review failures before demo/live mode.")
    elif n_warn > 0:
        print(f"\n  OK to start — {n_warn} warning(s), review recommended.")
    else:
        print(f"\n  ✔ All checks passed — system ready to start.")

    print()
    print(f"  Quick start (dry-run):")
    print(f"    cd {CODE_DIR}")
    print(f"    python tick_live_executor.py --poll 60 --quiet")
    print(f"{'═' * 62}")

    if output_json:
        print("\n" + json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {"pass": n_pass, "warn": n_warn, "fail": n_fail, "critical": n_crit},
            "checks": _results,
            "ready": n_crit == 0,
        }, indent=2))

    return n_crit == 0  # True = safe to start


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fortress pre-flight checklist")
    parser.add_argument("--quick",  action="store_true",
                        help="Critical checks only (skip network and smoke tests)")
    parser.add_argument("--json",   action="store_true",
                        help="Output machine-readable JSON summary at end")
    args = parser.parse_args()

    print(f"\n{'═' * 62}")
    print(f"  FORTRESS STARTUP CHECKLIST")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'═' * 62}")

    check_imports()
    check_bar_data(quick=args.quick)
    check_kill_switch()
    check_allowlist()
    check_contract_expiry()
    check_risk_config()
    check_bracket_order()
    check_coordinator()
    check_log_directory()

    if not args.quick:
        check_news_monitor()
        check_strategy_smoke()

    ready = print_summary(output_json=args.json)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
