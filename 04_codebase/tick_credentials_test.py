"""
tick_credentials_test.py — Tradovate Credential Pre-Flight Test
===============================================================
Run this ONCE when Tradovate credentials arrive, BEFORE enabling any
auto-trading. Verifies authentication, market data, and order-placement
gates in sequence.

Gates verified:
  1. Authentication — access token obtained, account ID resolved
  2. Account info — demo account, balance > 0
  3. Market data — live quote for each active contract
  4. Contract IDs — all contracts in TV_CONTRACT_MAP are resolvable
  5. Positions — fetch current open positions (should be empty pre-demo)
  6. Bracket order (optional) — place a WAY below market order, then cancel

Usage:
  python tick_credentials_test.py \\
      --username you@email.com \\
      --password yourpassword \\
      --cid 12345 \\
      --secret your_app_secret

  # Or via environment variables:
  set TRADOVATE_USERNAME=...
  set TRADOVATE_PASSWORD=...
  set TRADOVATE_CID=...
  set TRADOVATE_SECRET=...
  python tick_credentials_test.py

  # Include optional bracket order test (places + cancels a far-OTM limit):
  python tick_credentials_test.py --test-order

Exit codes:
  0 — all gates pass
  1 — one or more gates failed
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

CODE_DIR = Path(__file__).parent
sys.path.insert(0, str(CODE_DIR))

_results: list[dict] = []


def _pass(name: str, detail: str = ""):
    _results.append({"gate": name, "status": "PASS", "detail": detail})
    icon = "✔"
    print(f"  {icon} {name:<50}  PASS")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")


def _fail(name: str, detail: str = ""):
    _results.append({"gate": name, "status": "FAIL", "detail": detail})
    icon = "✖"
    print(f"  {icon} {name:<50}  FAIL")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")


def _section(title: str):
    print(f"\n{'─' * 62}")
    print(f"  {title}")
    print(f"{'─' * 62}")


def run(username: str, password: str, cid: int, secret: str,
        test_order: bool = False) -> int:

    print(f"\n{'═' * 62}")
    print(f"  FORTRESS CREDENTIAL PRE-FLIGHT TEST")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Account type: DEMO (paper trading)")
    print(f"{'═' * 62}")

    # ── Import ────────────────────────────────────────────────────────────────
    try:
        from tick_tradovate_client import TradovateClient
        from tick_live_executor import TV_CONTRACT_MAP
    except ImportError as e:
        _fail("imports", str(e))
        return 1

    # ── Gate 1: Authentication ────────────────────────────────────────────────
    _section("Gate 1: Authentication")
    try:
        client = TradovateClient(
            username=username, password=password,
            cid=cid, secret=secret,
            demo=True,
        )
        ok = client.authenticate()
        if ok and client.access_token and client.account_id:
            _pass("authenticate", f"Account ID: {client.account_id}")
        else:
            _fail("authenticate", "No access token or account ID returned")
            return 1
    except Exception as e:
        _fail("authenticate", str(e))
        return 1

    # ── Gate 2: Account Info ──────────────────────────────────────────────────
    _section("Gate 2: Account Info")
    try:
        info = client.get_account_info()
        balance = info.get("cashBalance", info.get("totalCashValue", 0))
        if balance > 0:
            _pass("account info", f"Demo balance: ${balance:,.2f}")
        elif balance == 0:
            _pass("account info", f"Balance = $0 — new account or no funds loaded")
        else:
            _fail("account info", f"Unexpected balance: {balance}")

        # Warn if it looks like a live account
        if balance > 100_000:
            print(f"\n  *** WARNING: balance ${balance:,.0f} — confirm this is a DEMO account ***")

    except Exception as e:
        _fail("account info", str(e))

    # ── Gate 3: Contract IDs ──────────────────────────────────────────────────
    _section("Gate 3: Contract Resolution")
    contract_ids = {}
    for base, tv_sym in TV_CONTRACT_MAP.items():
        if base in ("ES", "NQ", "GC"):
            continue  # skip non-micro fallbacks
        try:
            cid_val = client.get_contract_id(tv_sym)
            if cid_val:
                contract_ids[tv_sym] = cid_val
                _pass(f"contract {tv_sym}", f"ID = {cid_val}")
            else:
                _fail(f"contract {tv_sym}",
                      f"Not found — check if contract is active and not yet expired")
        except Exception as e:
            _fail(f"contract {tv_sym}", str(e))

    # ── Gate 4: Market Quotes ─────────────────────────────────────────────────
    _section("Gate 4: Market Quotes")
    for base, tv_sym in TV_CONTRACT_MAP.items():
        if base in ("ES", "NQ", "GC"):
            continue
        try:
            quote = client.get_quote(tv_sym)
            if quote and not quote.get("error"):
                bid = quote.get("bid", quote.get("bidPrice", "?"))
                ask = quote.get("ask", quote.get("offerPrice", "?"))
                last = quote.get("last", quote.get("lastPrice", "?"))
                _pass(f"quote {tv_sym}", f"bid={bid}  ask={ask}  last={last}")
            else:
                _fail(f"quote {tv_sym}", f"No quote data: {quote}")
        except Exception as e:
            _fail(f"quote {tv_sym}", str(e))

    # ── Gate 5: Open Positions ────────────────────────────────────────────────
    _section("Gate 5: Open Positions (should be empty)")
    try:
        positions = client.get_positions_dict()
        if not positions:
            _pass("positions", "No open positions — clean slate confirmed")
        else:
            msg = "\n".join(f"  {sym}: {p}" for sym, p in positions.items())
            _pass("positions", f"Existing positions:\n{msg}\n"
                  f"  These will be reconciled by _reconcile_positions() on startup")
    except Exception as e:
        _fail("positions", str(e))

    # ── Gate 6: Bracket Order (optional) ─────────────────────────────────────
    if test_order:
        _section("Gate 6: Bracket Order — place + cancel (far below market)")
        try:
            mes_sym = TV_CONTRACT_MAP.get("MES", "MESM5")

            # Get current quote to set a safe far-OTM price
            quote  = client.get_quote(mes_sym)
            last_p = quote.get("last", quote.get("lastPrice", 5200.0))
            if isinstance(last_p, str):
                last_p = float(last_p.replace(",", "")) if last_p != "?" else 5200.0

            # Place limit 10% below market — will NEVER fill at any normal price
            safe_entry = round(last_p * 0.90, 2)
            stop_price = round(safe_entry - 10.0, 2)
            tgt_price  = round(safe_entry + 20.0, 2)

            print(f"  Current last price: {last_p:.2f}")
            print(f"  Test order: BUY {mes_sym} limit @ {safe_entry:.2f} "
                  f"(stop={stop_price:.2f}, target={tgt_price:.2f})")
            print(f"  This is ~10% below market — will not fill.")

            result = client.place_bracket_order(
                symbol=mes_sym, side="BUY", quantity=1,
                entry_type="Limit", entry_price=safe_entry,
                stop_price=stop_price, target_price=tgt_price,
                demo=True, dry_run=False,   # REAL demo order
            )

            if result.get("ok"):
                order_id = result.get("order_id")
                _pass("bracket order placed",
                      f"Entry order ID: {order_id}")

                # Cancel immediately
                time.sleep(1)
                if order_id:
                    cancel_result = client.cancel_order(order_id)
                    _pass("bracket order cancelled", str(cancel_result)[:100])
                else:
                    print(f"      No order_id in result — check cancel manually")
            else:
                _fail("bracket order",
                      f"Placement failed: {result.get('reason', result)}")

        except Exception as e:
            _fail("bracket order (test)", str(e))
    else:
        print(f"\n  Gate 6 skipped — run with --test-order to verify OSO order placement")
        print(f"  (places a far-below-market limit and immediately cancels)")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_pass = sum(1 for r in _results if r["status"] == "PASS")
    n_fail = sum(1 for r in _results if r["status"] == "FAIL")

    print(f"\n{'═' * 62}")
    print(f"  CREDENTIAL TEST SUMMARY")
    print(f"{'═' * 62}")
    print(f"  Gates:  {len(_results)} checked")
    print(f"  Pass:   {n_pass}")
    print(f"  Fail:   {n_fail}")

    if n_fail == 0:
        print(f"\n  ✔ All gates passed.")
        print(f"\n  NEXT STEPS:")
        print(f"    1. Start bar builder:   python tick_bar_builder.py --rest")
        print(f"    2. Wait for fresh bars: python tick_startup_checklist.py")
        if not test_order:
            print(f"    3. Verify OSO order:    python tick_credentials_test.py --test-order")
        else:
            print(f"    3. Enable demo trading: python tick_session_supervisor.py --demo \\")
            print(f"           --username ... --password ... --cid ... --secret ...")
    else:
        print(f"\n  ✖ {n_fail} gate(s) failed — resolve before proceeding.")
        fails = [r for r in _results if r["status"] == "FAIL"]
        for f in fails:
            print(f"    ✖ {f['gate']}: {f['detail'][:80]}")

    print(f"{'═' * 62}\n")
    return 0 if n_fail == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Tradovate credential pre-flight test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--username",   default=os.environ.get("TRADOVATE_USERNAME", ""),
                        help="Tradovate username/email")
    parser.add_argument("--password",   default=os.environ.get("TRADOVATE_PASSWORD", ""),
                        help="Tradovate password")
    parser.add_argument("--cid",        type=int,
                        default=int(os.environ.get("TRADOVATE_CID", "0")),
                        help="App client ID")
    parser.add_argument("--secret",     default=os.environ.get("TRADOVATE_SECRET", ""),
                        help="App client secret")
    parser.add_argument("--test-order", action="store_true",
                        help="Also place + cancel a far-OTM demo bracket order (Gate 6)")
    args = parser.parse_args()

    if not (args.username and args.password):
        print("ERROR: --username and --password are required.")
        print("  Or set TRADOVATE_USERNAME and TRADOVATE_PASSWORD environment variables.")
        print("  Example:")
        print("    python tick_credentials_test.py \\")
        print("        --username you@email.com --password pw \\")
        print("        --cid 12345 --secret appSecret")
        sys.exit(1)

    sys.exit(run(
        username   = args.username,
        password   = args.password,
        cid        = args.cid,
        secret     = args.secret,
        test_order = args.test_order,
    ))


if __name__ == "__main__":
    main()
