"""
tick_contract_rollover.py — Quarterly Contract Rollover Helper
==============================================================
Automatically updates TV_CONTRACT_MAP, SYMBOL_MAP, and MICRO_SYMBOLS
in all relevant files when the front-month futures contract changes.

Run ~7-10 days before expiry. Files updated:
  - tick_live_executor.py   (TV_CONTRACT_MAP + _CONTRACT_EXPIRY)
  - tick_bar_builder.py     (SYMBOL_MAP)
  - tick_tradovate_client.py (MICRO_SYMBOLS)

CME Quarterly contract months:
  H (March) — expires 3rd Friday of March
  M (June)  — expires 3rd Friday of June
  U (Sep)   — expires 3rd Friday of September
  Z (Dec)   — expires 3rd Friday of December

Rollover calendar (approximate):
  M5 (June 2026) → U5 (Sep 2026)  — MESM5→MESU5, MGCM5→MGCU5, MNQM5→MNQU5
  U5 (Sep 2026)  → Z5 (Dec 2026)  — MESU5→MESZ5, MGCU5→MGCZ5, MNQU5→MNQZ5

Usage:
  python tick_contract_rollover.py --show             # show current contracts + expiry
  python tick_contract_rollover.py --to U5            # roll to September contracts
  python tick_contract_rollover.py --to U5 --dry-run  # preview changes only
  python tick_contract_rollover.py --to Z5            # roll to December contracts
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

CODE_DIR = Path(__file__).parent

# ── Contract definitions ──────────────────────────────────────────────────────

# month_code → (human-readable, approximate expiry for MES/MNQ and MGC)
_MONTH_INFO = {
    "H": ("March",     "2026-03-20", "2026-03-27"),  # (month, MES/NQ expiry, GC expiry)
    "M": ("June",      "2026-06-20", "2026-06-27"),
    "U": ("September", "2026-09-19", "2026-09-26"),
    "Z": ("December",  "2026-12-18", "2026-12-28"),
    # 2027
}

# Micro contract base names
_MICROS = {
    "MES": "ES",
    "MNQ": "NQ",
    "MGC": "GC",
}

# All files that reference contract symbols
_FILES = {
    "executor":  CODE_DIR / "tick_live_executor.py",
    "builder":   CODE_DIR / "tick_bar_builder.py",
    "client":    CODE_DIR / "tick_tradovate_client.py",
}


def _current_month_code(content: str, sym: str) -> str | None:
    """Extract current month code from a symbol map like '"MES": "MESM5"' """
    pattern = rf'"{sym}":\s*"{sym}([A-Z]\d)"'
    m = re.search(pattern, content)
    return m.group(1) if m else None


def show_current():
    """Display current contract state across all files."""
    print(f"\n{'─' * 60}")
    print("  Current contract state")
    print(f"{'─' * 60}")

    now = datetime.now(timezone.utc).date()
    for name, path in _FILES.items():
        if not path.exists():
            print(f"  {name}: file not found")
            continue
        content = path.read_text(encoding="utf-8")
        symbols_found = []
        # Try micro symbol keys first (executor/client), then base symbol keys (builder)
        for sym, base_keys in [
            ("MES", ("MES", "ES")),
            ("MNQ", ("MNQ", "NQ")),
            ("MGC", ("MGC", "GC")),
            ("SIL", ("SIL", "SI")),
        ]:
            found = False
            for key in base_keys:
                for q in ('"', "'"):
                    p = rf'{q}{key}{q}:\s*{q}(M[A-Z]{{1,3}}[A-Z]\d+){q}'
                    m = re.search(p, content)
                    if m:
                        symbols_found.append(f"{sym}→{m.group(1)}")
                        found = True
                        break
                if found:
                    break

        print(f"  {name:<12}  {', '.join(symbols_found) or 'no symbols found'}")

    # Check expiry
    try:
        from tick_live_executor import TV_CONTRACT_MAP, _CONTRACT_EXPIRY
        print(f"\n  Expiry dates:")
        for base, tv_sym in TV_CONTRACT_MAP.items():
            expiry = _CONTRACT_EXPIRY.get(tv_sym, "?")
            if expiry == "?":
                continue
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            days = (expiry_date - now).days
            flag = "  ***EXPIRED***" if days < 0 else f"  ({days} days)" if days <= 21 else f"  ({days} days)"
            print(f"    {tv_sym:<10}  {expiry}{flag}")
    except ImportError:
        pass
    print()


def _update_file(path: Path, old_suffix: str, new_suffix: str,
                 dry_run: bool = False) -> tuple[int, list[str]]:
    """
    Replace contract symbol suffixes (e.g. 'M5' → 'U5') in contract map definitions.
    Skips lines that are part of _CONTRACT_EXPIRY (those have correct dates already
    pre-populated and should not be modified by this function).
    Returns (change_count, changed_lines).
    """
    if not path.exists():
        return 0, []

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Detect _CONTRACT_EXPIRY section: skip lines between its opening { and closing }
    in_expiry_section = False
    new_lines = []
    changed   = []
    pattern   = rf'(["\'])([A-Z]{{2,4}})({re.escape(old_suffix)})(["\'])'

    for i, line in enumerate(lines):
        # Track whether we're inside _CONTRACT_EXPIRY = { ... }
        if "_CONTRACT_EXPIRY" in line and "=" in line and "{" in line:
            in_expiry_section = True
        if in_expiry_section and "}" in line:
            in_expiry_section = False
            new_lines.append(line)
            continue

        if in_expiry_section:
            # Leave _CONTRACT_EXPIRY entries untouched
            new_lines.append(line)
            continue

        new_line = re.sub(pattern,
                          lambda m: f"{m.group(1)}{m.group(2)}{new_suffix}{m.group(4)}",
                          line)
        if new_line != line:
            changed.append(f"  Line {i+1}: {line.rstrip()} → {new_line.rstrip()}")
        new_lines.append(new_line)

    if not changed:
        return 0, []

    new_content = "".join(new_lines)
    if dry_run:
        print(f"\n  {path.name}: {len(changed)} change(s) (dry-run, not written)")
    else:
        path.write_text(new_content, encoding="utf-8")
        print(f"\n  {path.name}: {len(changed)} change(s) written")

    for line in changed:
        print(line)

    return len(changed), changed


def _update_expiry(executor_path: Path, new_suffix: str, dry_run: bool = False):
    """
    Add or update expiry dates for the new contract month in _CONTRACT_EXPIRY.
    Pre-populates the new month's expiry so rollover warnings work immediately.
    """
    if not executor_path.exists():
        return

    content = executor_path.read_text(encoding="utf-8")

    # Get month code from suffix like "U5" → "U"
    new_month = new_suffix[0].upper()
    info = _MONTH_INFO.get(new_month)
    if not info:
        print(f"  [expiry] Unknown month code '{new_month}' — skipping expiry update")
        return

    _, es_nq_expiry, gc_expiry = info

    additions = {
        f"MES{new_suffix}":  es_nq_expiry,
        f"MNQ{new_suffix}":  es_nq_expiry,
        f"MGC{new_suffix}":  gc_expiry,
    }

    added = []
    for tv_sym, expiry in additions.items():
        if tv_sym in content:
            continue
        # Insert after the last entry in _CONTRACT_EXPIRY
        insert_pat = r'(_CONTRACT_EXPIRY\s*=\s*\{[^}]+?)(\n\})'
        new_line   = f'\n    "{tv_sym}": "{expiry}",'
        new_content = re.sub(insert_pat,
                              lambda m: m.group(1) + new_line + m.group(2),
                              content)
        if new_content != content:
            content = new_content
            added.append(f'  "{tv_sym}": "{expiry}"')

    if added:
        if not dry_run:
            executor_path.write_text(content, encoding="utf-8")
        print(f"  tick_live_executor.py: expiry entries {'(dry-run) ' if dry_run else ''}added:")
        for a in added:
            print(f"    {a}")
    else:
        print(f"  tick_live_executor.py: expiry entries already present")


def do_rollover(new_suffix: str, dry_run: bool = False):
    """Roll all contract symbols to new_suffix (e.g. 'U5' for September 2026)."""
    # Detect current suffix from executor
    executor = _FILES["executor"]
    if not executor.exists():
        print(f"ERROR: {executor} not found")
        sys.exit(1)

    content     = executor.read_text(encoding="utf-8")
    current_map = {}
    for sym in ("MESM5", "MESU5", "MESZ5", "MESH5"):
        if f'"{sym}"' in content or f"'{sym}'" in content:
            current_map["MES"] = sym[-2:]  # get suffix like "M5"
            break

    old_suffix = current_map.get("MES", "M5")
    if old_suffix == new_suffix:
        print(f"Already on {new_suffix} — no changes needed.")
        return

    month = new_suffix[0].upper()
    info  = _MONTH_INFO.get(month)
    month_name = info[0] if info else f"month {month}"

    print(f"\n{'═' * 60}")
    print(f"  CONTRACT ROLLOVER: {old_suffix} → {new_suffix} ({month_name})")
    if dry_run:
        print(f"  DRY-RUN — no files will be modified")
    print(f"{'═' * 60}")

    total_changes = 0
    for name, path in _FILES.items():
        n, _ = _update_file(path, old_suffix, new_suffix, dry_run=dry_run)
        total_changes += n

    _update_expiry(_FILES["executor"], new_suffix, dry_run=dry_run)

    print(f"\n  {'─' * 58}")
    print(f"  Total changes: {total_changes} symbol references updated")
    if dry_run:
        print(f"\n  Run without --dry-run to apply changes.")
    else:
        print(f"\n  Done. Verify with: python tick_contract_rollover.py --show")
        print(f"\n  NEXT STEPS:")
        print(f"    1. Run tick_startup_checklist.py to verify no expiry warnings")
        print(f"    2. Restart tick_bar_builder.py so it subscribes to new contracts")
        print(f"    3. Verify new contract IDs are available at Tradovate")
    print()


def main():
    parser = argparse.ArgumentParser(description="Contract rollover helper")
    parser.add_argument("--show",    action="store_true",
                        help="Show current contract state and expiry dates")
    parser.add_argument("--to",      type=str, default=None,
                        help="Target contract suffix, e.g. U5 (Sep), Z5 (Dec), H6 (Mar 2027)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying files")
    args = parser.parse_args()

    if args.show or not args.to:
        show_current()
        if not args.to:
            print("  Usage: python tick_contract_rollover.py --to U5")
            print("  (rolls June M5 → September U5 across all files)")
        return

    if len(args.to) not in (2, 3) or not args.to[0].isalpha() or not args.to[1:].isdigit():
        print(f"ERROR: invalid suffix '{args.to}' — expected format like 'U5' or 'Z5'")
        sys.exit(1)

    do_rollover(args.to.upper(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
