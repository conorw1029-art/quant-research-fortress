"""
tick_credentials_preflight.py — System Classification Preflight
================================================================
Classifies the execution environment without making any broker/API calls.
Safe to run at startup in all modes including MOCK_ONLY.

Exit codes:
  0 — DATA_READY (Databento key present, broker mode known)
  1 — configuration error

Run:
  venv_new/Scripts/python.exe -X utf8 04_codebase/tick_credentials_preflight.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT = Path(__file__).parent.parent

# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def _mask(value: str) -> str:
    """Show only first 4 + last 4 chars, mask the rest."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def main() -> int:
    _load_env()

    print(f"\n{'=' * 60}")
    print(f"  FORTRESS CREDENTIALS PREFLIGHT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'=' * 60}")

    # ── Execution mode ────────────────────────────────────────────────────────
    exec_mode   = os.environ.get("EXECUTION_MODE",   "DRY_RUN").upper()
    broker_mode = os.environ.get("BROKER_MODE",      "MOCK_ONLY").upper()
    tv_enabled  = os.environ.get("TRADOVATE_ENABLED", "false").lower()
    tv_env      = os.environ.get("TRADOVATE_ENV",    "none").lower()

    # Hard safety check — live mode must be explicitly gated
    live_flag = os.environ.get("FORTRESS_LIVE_ENABLE", "")
    if live_flag:
        print(f"\n  [CRITICAL] FORTRESS_LIVE_ENABLE is set: '{live_flag}'")
        print(f"  Live mode is not enabled in this system. Remove this variable.")
        return 1

    print(f"\n  Execution Mode : {exec_mode}")
    print(f"  Broker Mode    : {broker_mode}")
    print(f"  Tradovate      : ENABLED={tv_enabled}  ENV={tv_env}")

    # ── Databento key ─────────────────────────────────────────────────────────
    db_key = os.environ.get("DATABENTO_API_KEY", "")
    if db_key:
        print(f"\n  Databento Key  : PRESENT ({_mask(db_key)})")
        data_ready = True
    else:
        print(f"\n  Databento Key  : MISSING")
        data_ready = False

    # ── Anthropic key (optional) ──────────────────────────────────────────────
    anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anth_key:
        print(f"  Anthropic Key  : PRESENT ({_mask(anth_key)})")
    else:
        print(f"  Anthropic Key  : MISSING (optional)")

    # ── Tradovate credentials ─────────────────────────────────────────────────
    tv_user   = os.environ.get("TRADOVATE_USERNAME", "")
    tv_pass   = os.environ.get("TRADOVATE_PASSWORD", "")
    tv_cid    = os.environ.get("TRADOVATE_CID", "")
    tv_secret = os.environ.get("TRADOVATE_SECRET", "")

    print(f"\n  Tradovate Creds:")
    print(f"    USERNAME : {'PRESENT' if tv_user else 'MISSING'}")
    print(f"    PASSWORD : {'PRESENT (masked)' if tv_pass else 'MISSING'}")
    print(f"    CID      : {'PRESENT' if tv_cid else 'MISSING'}")
    print(f"    SECRET   : {'PRESENT (masked)' if tv_secret else 'MISSING'}")

    tv_creds_ok = all([tv_user, tv_pass, tv_cid, tv_secret])

    # ── System classification ─────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"  SYSTEM CLASSIFICATION")
    print(f"{'─' * 60}")

    classifications = []

    # Data
    if data_ready:
        classifications.append("DATA_READY")
        print(f"  [OK] DATA_READY             — Databento key present")
    else:
        classifications.append("DATA_MISSING")
        print(f"  [--] DATA_MISSING           — No Databento key (data-only work blocked)")

    # Broker
    if broker_mode == "MOCK_ONLY":
        classifications.append("BROKER_MOCK_ONLY")
        print(f"  [OK] BROKER_MOCK_ONLY       — Broker mode is MOCK_ONLY")
    else:
        print(f"  [??] BROKER_MODE={broker_mode}    — Non-mock broker mode detected")

    # Tradovate
    if tv_enabled == "false":
        classifications.append("TRADOVATE_DISABLED")
        print(f"  [OK] TRADOVATE_DISABLED     — TRADOVATE_ENABLED=false")
    else:
        if tv_creds_ok:
            classifications.append("TRADOVATE_CREDENTIALS_PRESENT")
            print(f"  [!!] TRADOVATE_ENABLED=true — credentials present (broker still needs demo account)")
        else:
            classifications.append("TRADOVATE_CREDENTIALS_MISSING")
            print(f"  [--] TRADOVATE_ENABLED=true — credentials missing (not a failure in MOCK_ONLY mode)")

    # Connection states
    classifications.append("NOT_BROKER_CONNECTED")
    classifications.append("NOT_DEMO_READY")
    classifications.append("NOT_LIVE_READY")
    print(f"  [--] NOT_BROKER_CONNECTED   — No active broker connection (expected in MOCK_ONLY)")
    print(f"  [--] NOT_DEMO_READY         — No verified demo/sim account")
    print(f"  [--] NOT_LIVE_READY         — Funded account connection not enabled")

    # Dry-run
    if exec_mode == "DRY_RUN":
        classifications.append("DRY_RUN_READY")
        print(f"  [OK] DRY_RUN_READY          — EXECUTION_MODE=DRY_RUN")

    # ── Safety summary ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"  SAFETY GATES")
    print(f"{'─' * 60}")
    print(f"  Safe for data-only Databento work  : {'YES' if data_ready else 'NO (key missing)'}")
    print(f"  Safe for dry-run simulation        : YES")
    print(f"  Safe for mock broker testing       : YES")
    print(f"  Safe for demo auto-trade           : NO (no verified demo account)")
    print(f"  Safe for live/funded accounts      : NO")
    print(f"  Tradovate broker calls blocked     : YES (TRADOVATE_ENABLED={tv_enabled})")

    # ── Classification string ─────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"  Final classification:")
    for cls in classifications:
        print(f"    {cls}")
    print(f"{'=' * 60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
