# Personal Broker Selection Matrix

**Project:** The Fortress — Broker Selection for Personal Account Live Trading  
**Date:** 2026-06-03  
**Status:** Decision document

---

## Comparison Matrix

| Criterion | NinjaTrader Brokerage | IBKR | Tradovate Personal |
|---|---|---|---|
| **Python API quality** | Poor (C# native; Python requires TCP bridge or third-party) | Excellent (`ib_insync` asyncio library; battle-tested) | Good (REST + WebSocket; existing client in codebase) |
| **Bracket order support** | Yes (OCO native in NT8 order types) | Yes (`BracketOrder` object; OCO guaranteed) | Yes (`placeOrder` with stop/target legs) |
| **Simulator quality** | Best-in-class (tick-accurate replay, used throughout this project) | Good (24/7 paper account, near-live fills) | Good (demo account with identical API to live) |
| **Micro futures support** | Yes (MGC, SIL via CME) | Yes (MGC, SIL, MES, MNQ) | Yes (MGC, SIL) |
| **Commissions GC (per side)** | $1.09 | ~$0.85 (tiered) | $0.79 (membership) / $1.99 (pay-per-trade) |
| **Commissions SI (per side)** | $1.09 | ~$0.85 (tiered) | $0.79 (membership) / $1.99 (pay-per-trade) |
| **Commissions ES (per side)** | $1.09 | ~$0.85 (tiered) | $0.79 / $1.99 |
| **Market data cost** | Included in platform fee or low add-on | Level 1 free; Level 2 CME ~$30/month | Bundled with membership plan |
| **Capital required** | ~$5,000 practical (GC margin) | $10,000 practical (PDT-adjacent guidance) | $3,000–$5,000 (micro); ~$12,000 (full GC) |
| **API documentation quality** | Good (NT8 docs, active forum) | Excellent (TWS API docs + ib_insync community) | Moderate (REST documented; WS edge cases require testing) |
| **Community / support** | Large NinjaTrader user base; NinjaScript forum active | Very large; professional algo trading community | Small but growing; Tradovate Discord |
| **Automation friendliness** | Moderate (C# native; Python requires extra layer) | Highest (Python first-class; `ib_insync` mature) | High (REST/WS Python client exists in this codebase) |
| **Broker overhead** | Desktop app required (NT8 must run) | Desktop app or IB Gateway must run locally | No local app; cloud API |
| **Recommended for this project** | No — porting cost too high for Python-first stack | Yes (long-term) — best API, lowest commissions at scale | **Yes (near-term)** — lowest friction given existing code |

---

## Scoring Summary

| Criterion Weight | NinjaTrader | IBKR | Tradovate |
|---|---|---|---|
| Python API (weight 25%) | 2/10 | 10/10 | 8/10 |
| Bracket order support (15%) | 8/10 | 10/10 | 8/10 |
| Simulator quality (10%) | 10/10 | 7/10 | 7/10 |
| Commissions (15%) | 6/10 | 9/10 | 8/10 |
| Capital required (10%) | 6/10 | 5/10 | 7/10 |
| API documentation (10%) | 7/10 | 10/10 | 6/10 |
| Automation friendliness (15%) | 4/10 | 10/10 | 8/10 |
| **Weighted score** | **5.35** | **9.05** | **7.65** |

---

## Broker Profiles

### NinjaTrader Brokerage

**Strengths:**
- Best simulator in the industry for futures. This project's entire OHLCV research phase used NT8 historical replay.
- Tight integration with Rithmic data feed (already in use at Apex/Tradeify).
- Low commissions for the platform tier.
- Works with existing NinjaScript automation from the prop firm phase.

**Weaknesses:**
- C# native. Porting the Python research stack to live execution requires either a TCP bridge (fragile, latency-adding) or rewriting execution logic in C#.
- Desktop application must be running — no headless server mode without hacks.
- Python community support is minimal compared to IBKR.

**Verdict:** Ruled out for the Python-first automation stack. Retain for simulation and data replay.

---

### Interactive Brokers (IBKR)

**Strengths:**
- `ib_insync` (Python) is the most mature algo-trading library for futures in any language. Full async order management, fills, account state, P&L streaming.
- Lowest commissions at scale (tiered pricing rewards volume).
- Bracket orders are a first-class object: `BracketOrder(parent, takeProfit, stopLoss)` with guaranteed OCO linkage.
- Best suited for expansion beyond GC/SI (CL, NQ, ES, 6E all available with the same API).
- No desktop required when using IB Gateway in headless mode on a server.

**Weaknesses:**
- Setup complexity: IB Gateway must run as a subprocess; TWS or Gateway version drift causes connection issues; paper vs. live port numbers must be managed carefully.
- $10,000 practical minimum to avoid PDT-adjacent concerns (even for futures, IBKR enforces margin requirements that are higher than Tradovate).
- Account opening takes 1–3 business days; wire transfers add another 1–3 days.
- Some users report fill quality issues on illiquid instruments during off-hours.

**Verdict:** Best choice for the long-term live system. Target once the paper proof is complete on Tradovate.

---

### Tradovate Personal Account

**Strengths:**
- Existing Python client is in the codebase. The Tradovate adapter, authentication, order placement, and position tracking are already implemented and tested in dry-run mode.
- REST + WebSocket API runs headlessly — no local desktop process required.
- Demo account uses the identical API as live; transition from paper to live is a config change (live vs. demo base URL).
- Membership plan reduces commissions significantly for active strategies (80+ round trips/month).
- Fastest path to a funded live account: apply online, instant approval for demo; fund with wire or ACH.

**Weaknesses:**
- WebSocket protocol has some undocumented edge cases (e.g., reconnection after 24-hour token expiry, partial fill callbacks) that required workarounds in the existing client.
- No multi-account management in the standard plan; each account is a separate API session.
- Community is smaller; fewer examples of complex algo workflows.
- Commissions at pay-per-trade rate ($1.99/side) are expensive without the membership plan.

**Verdict:** Primary recommendation for near-term personal broker live trading.

---

## Recommendation

### Near-Term (Next 60 Days): Tradovate Personal Account

1. Open a Tradovate personal account (demo first, then fund with $5,000–$10,000).
2. Complete the 30-day paper proof using the existing `TradovateAdapter` against the personal account demo API.
3. Verify all 5 safety gates (bracket orders, state persistence, reconciliation, kill switch, 30-day proof).
4. Go live at micro size (MGC, SIL) for 30 days.
5. Promote to full-size GC/SI after micro live validation.

### Long-Term (After 6 Months): IBKR

1. Open an IBKR account in parallel once Tradovate live is validated.
2. Build `IBKRAdapter` implementing the `BrokerAdapter` abstract interface.
3. Run Tradovate and IBKR in parallel for 30 days (paper on IBKR, live on Tradovate).
4. Switch primary execution to IBKR when fill quality and commission savings are confirmed.

### Never: NinjaTrader Brokerage as Primary Execution

Keep NT8 exclusively for:
- Historical data replay and tick-accurate simulation
- Validating strategy behavior on exact historical ticks
- Any NinjaScript strategies that are not ported to Python

---

## Key Numbers

| | Tradovate (membership) | IBKR (tiered) |
|---|---|---|
| GC round-trip cost (1 tick slip) | $0.10×2 slip + $0.79×2 comm = $1.78 | $0.10×2 + $0.85×2 = $1.90 |
| SI round-trip cost (1 tick slip) | $0.005×2×5000 slip + $1.58 = $51.58 | $50 slip + $1.70 = $51.70 |
| Monthly platform cost (membership) | ~$99/month | ~$0 (waived at $10 commissions) |
| Break-even trades/month for membership | ~63 round trips | N/A |

At 2+ strategies running 5 trades/week each, the Tradovate membership pays for itself immediately.
