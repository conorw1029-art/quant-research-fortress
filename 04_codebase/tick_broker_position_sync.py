"""
tick_broker_position_sync.py — Broker ↔ Coordinator State Bridge
=================================================================
Converts raw Tradovate position and open-order data into
BrokerNetPosition objects that the PortfolioCoordinator can consume.

No API calls. No broker connections. Pure computation on data already
fetched by tick_live_executor.py.

Usage (inside tick_live_executor.py):
    from tick_broker_position_sync import build_broker_net_positions, bracket_ids_from_orders
    tv_positions = tv_client.get_positions()
    bracket_ids  = tv_client.get_bracket_order_ids_by_symbol()
    sm_brackets  = sm.load_active_brackets().get("brackets", {})
    broker_pos   = build_broker_net_positions(tv_positions, bracket_ids, sm_brackets)
    # Pass broker_pos to coordinator.evaluate_single_signal(broker_positions=broker_pos)
"""

from __future__ import annotations

from typing import Any

# ── Symbol utilities ──────────────────────────────────────────────────────────

_MONTH_CODES: frozenset[str] = frozenset("FGHJKMNQUVXZ")

# Tradovate micro-contract base → strategy base symbol
# (i.e. the symbol used inside the PORTFOLIO tuple and strategy keys)
_MICRO_TO_BASE: dict[str, str] = {
    "MGC": "GC",
    "MES": "ES",
    "MNQ": "NQ",
    "SIL": "SI",
}


def strip_month_code(tv_symbol: str) -> str:
    """
    Remove the month/year suffix from a Tradovate contract symbol.

    Examples:
        "MESM5"  → "MES"
        "MGCM5"  → "MGC"
        "MNQZ4"  → "MNQ"
        "ESM5"   → "ES"
        "GCJ6"   → "GC"
        "SI"     → "SI"     (no suffix — returned unchanged)
        "MGCU25" → "MGC"    (two-digit year)
    """
    s = tv_symbol.upper().strip()
    i = len(s) - 1
    # Walk back past trailing digits (year digits: 5, 25, etc.)
    while i > 0 and s[i].isdigit():
        i -= 1
    # One letter before the digits should be a month code
    if i > 0 and s[i] in _MONTH_CODES:
        return s[:i]
    return s


def micro_to_base(sym: str) -> str:
    """
    Map a Tradovate micro-contract base symbol to the strategy base symbol.

    Examples:
        "MES" → "ES"
        "MGC" → "GC"
        "MNQ" → "NQ"
        "SIL" → "SI"
        "ES"  → "ES"   (pass-through — already a base symbol)
    """
    return _MICRO_TO_BASE.get(sym.upper(), sym.upper())


def tv_contract_to_coordinator_symbol(tv_contract: str) -> str:
    """
    Convert a full Tradovate contract symbol to the coordinator-level base symbol.

    Examples:
        "MESM5"  → "ES"
        "MGCM5"  → "GC"
        "MNQM5"  → "NQ"
        "SILM5"  → "SI"
        "ESM5"   → "ES"   (full-size, not micro)
        "GCJ6"   → "GC"
    """
    return micro_to_base(strip_month_code(tv_contract))


# ── Bracket order ID extraction ───────────────────────────────────────────────

# Tradovate ordStatus values that indicate an active bracket leg
_BRACKET_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {"Working", "ContingencyOrder", "PendingNew", "Accepted", "PendingReplace"}
)


def bracket_ids_from_orders(raw_orders: list[dict]) -> dict[str, list[int]]:
    """
    Extract active bracket leg order IDs from a list of raw Tradovate order dicts.

    Bracket legs are identified by ordStatus in _BRACKET_ACTIVE_STATUSES.
    Entry orders that are already filled will not appear in the open-order list
    (or will have ordStatus "Filled"), so this naturally captures only the
    stop/target legs.

    Args:
        raw_orders: List of order dicts as returned by TradovateClient.get_open_orders()
                    or /order/list. Each dict should contain at minimum:
                      - "id" or "orderId"     (int)
                      - "ordStatus"           (str)
                      - "symbol"              (str, optional)
                      - "contractId"          (int, optional — fallback when symbol missing)

    Returns:
        {base_symbol: [order_id, ...]} for each symbol with active bracket legs.
        base_symbol is the stripped, non-micro symbol (e.g. "GC", "ES", "MES").
        Returns {} if raw_orders is empty or None.

    Note:
        When only contractId is present (no symbol field), the order is skipped.
        The caller is responsible for resolving contractId → symbol before calling
        this function if full coverage is needed (see TradovateClient.get_bracket_order_ids_by_symbol).
    """
    if not raw_orders:
        return {}

    result: dict[str, list[int]] = {}
    for o in raw_orders:
        if o.get("ordStatus") not in _BRACKET_ACTIVE_STATUSES:
            continue

        order_id = o.get("id") or o.get("orderId")
        if not order_id:
            continue

        sym = o.get("symbol", "")
        if not sym:
            # contractId-only orders: caller should pre-resolve; skip here
            continue

        base = strip_month_code(str(sym))
        result.setdefault(base, []).append(int(order_id))

    return result


# ── Main conversion function ──────────────────────────────────────────────────

def build_broker_net_positions(
    tv_positions: list,
    bracket_ids_by_sym: dict[str, list[int]],
    sm_brackets: dict[str, dict] | None = None,
) -> list:
    """
    Build a list of BrokerNetPosition objects for the PortfolioCoordinator.

    Sources of bracket IDs (priority order):
        1. bracket_ids_by_sym keyed by base symbol ("MES", "MGC")
        2. bracket_ids_by_sym keyed by coordinator symbol ("ES", "GC")
        3. StateManager bracket records (sm_brackets) as fallback
           when the live API returned no active orders yet
           (e.g. immediately after entry fill, before bracket activates)

    Args:
        tv_positions:       List of Position objects from TradovateClient.get_positions().
                            Flat positions (net_pos == 0) are skipped.
        bracket_ids_by_sym: {base_symbol: [order_id, ...]} from
                            TradovateClient.get_bracket_order_ids_by_symbol().
        sm_brackets:        StateManager bracket dict keyed by strategy_id (str).
                            Each value is a dict with at least:
                              symbol, stop_order_id, target_order_id
                            Pass None or {} if StateManager is unavailable.

    Returns:
        List of BrokerNetPosition objects (from tick_portfolio_coordinator).
        Returns [] if tick_portfolio_coordinator is not importable.
        Returns [] if tv_positions is empty.

    Coordinator symbol is always the base strategy symbol (GC, ES, NQ, SI),
    not the micro-contract base (MGC, MES, MNQ, SIL).
    """
    try:
        from tick_portfolio_coordinator import BrokerNetPosition
    except ImportError:
        return []

    if not tv_positions:
        return []

    # Pre-process StateManager brackets: {base_sym: [stop_id, target_id]}
    sm_by_base: dict[str, list] = {}
    if sm_brackets:
        for _sid_key, b in sm_brackets.items():
            if not isinstance(b, dict):
                continue
            sym = b.get("symbol", "")
            if not sym:
                continue
            base = strip_month_code(str(sym))
            ids: list = []
            sid_val = b.get("stop_order_id")
            tid_val = b.get("target_order_id")
            if sid_val is not None:
                ids.append(sid_val)
            if tid_val is not None:
                ids.append(tid_val)
            if ids:
                sm_by_base.setdefault(base, []).extend(ids)

    result: list[BrokerNetPosition] = []

    for pos in tv_positions:
        if getattr(pos, "net_pos", 0) == 0:
            continue

        base_sym  = strip_month_code(str(pos.symbol))   # "MESM5" → "MES"
        coord_sym = micro_to_base(base_sym)              # "MES"   → "ES"

        # Priority 1: API bracket IDs keyed by micro base
        bracket_ids: list = list(bracket_ids_by_sym.get(base_sym, []))
        # Priority 2: API bracket IDs keyed by coordinator base
        if not bracket_ids:
            bracket_ids = list(bracket_ids_by_sym.get(coord_sym, []))
        # Priority 3: StateManager fallback
        if not bracket_ids:
            bracket_ids = list(sm_by_base.get(base_sym, []))
        if not bracket_ids:
            bracket_ids = list(sm_by_base.get(coord_sym, []))

        result.append(BrokerNetPosition(
            account_id=str(getattr(pos, "account_id", "")),
            symbol=coord_sym,
            contract=str(pos.symbol),
            net_qty=int(pos.net_pos),
            avg_price=float(getattr(pos, "avg_price", 0.0)),
            active_bracket_ids=[str(bid) for bid in bracket_ids],
            state="LONG" if pos.net_pos > 0 else "SHORT",
        ))

    return result
