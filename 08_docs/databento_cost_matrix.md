# Databento Cost Estimate Matrix
**Project:** The Fortress — Quantitative Trading Research  
**Date:** 2026-06-03  
**Budget remaining:** ~$4.86 of $125.00 (effectively exhausted)

---

## Budget Status

| Item | USD |
|---|---|
| Total allocated budget | $125.00 |
| Spent to date (GC + SI mbp-10 2020-2026) | $120.14 |
| **Remaining** | **$4.86** |

**No download can proceed without a new budget allocation. Current remaining budget is insufficient for any multi-month purchase.**

---

## Pricing Model

Databento charges per message (record). Prices vary by schema:

| Schema | Price range (per 1,000 messages) | Notes |
|---|---|---|
| `trades` | $0.001 – $0.003 | Cheapest. One record per trade print. |
| `mbp-1` | $0.002 – $0.005 | Best bid/ask snapshot after each quote change. |
| `mbp-10` | $0.010 – $0.030 | Full 10-level depth after every book event. Most expensive depth schema. |
| `mbo` | Contact Databento | Market-by-order (individual order events). Estimated 3–10x mbp-10 cost. |

**Message volume estimates per instrument per day (approximate):**

| Symbol | trades msgs/day | mbp-1 msgs/day | mbp-10 msgs/day |
|---|---|---|---|
| ES | ~500,000 | ~300,000 | ~1,000,000 |
| NQ | ~300,000 | ~200,000 | ~600,000 |
| GC | ~150,000 | ~100,000 | ~300,000 |
| SI | ~80,000 | ~60,000 | ~150,000 |
| CL | ~200,000 | ~150,000 | ~500,000 |

Estimates are averages during regular trading hours (RTH). Extended hours add ~20% to daily totals.  
High-volatility days (FOMC, NFP, earnings) can be 3–5x average volume.

---

## Cost Matrix — trades Schema

All costs in USD. Calculated at mid-range pricing ($0.002/1,000 messages).

| Symbol | 1 day | 1 month | 6 months | 1 year | 2020-present (6 yr) |
|---|---|---|---|---|---|
| ES | $1.00 | $22.00 | $110.00 | $220.00 | $1,320.00 |
| NQ | $0.60 | $13.20 | $66.00 | $132.00 | $792.00 |
| GC | $0.30 | $6.60 | $33.00 | $66.00 | $396.00 |
| SI | $0.16 | $3.52 | $17.60 | $35.20 | $211.20 |
| CL | $0.40 | $8.80 | $44.00 | $88.00 | $528.00 |

*GC and SI trades data is not separately needed — mbp-10 files already downloaded contain equivalent trade data.*

---

## Cost Matrix — mbp-1 Schema

All costs in USD. Calculated at mid-range pricing ($0.003/1,000 messages).

| Symbol | 1 day | 1 month | 6 months | 1 year | 2020-present (6 yr) |
|---|---|---|---|---|---|
| ES | $0.90 | $19.80 | $99.00 | $198.00 | $1,188.00 |
| NQ | $0.60 | $13.20 | $66.00 | $132.00 | $792.00 |
| GC | $0.30 | $6.60 | $33.00 | $66.00 | $396.00 |
| SI | $0.18 | $3.96 | $19.80 | $39.60 | $237.60 |
| CL | $0.45 | $9.90 | $49.50 | $99.00 | $594.00 |

---

## Cost Matrix — mbp-10 Schema

All costs in USD. Calculated at mid-range pricing ($0.020/1,000 messages).

| Symbol | 1 day | 1 month | 6 months | 1 year | 2020-present (6 yr) |
|---|---|---|---|---|---|
| ES | $20.00 | $440.00 | $2,200.00 | $4,400.00 | $26,400.00 |
| NQ | $12.00 | $264.00 | $1,320.00 | $2,640.00 | $15,840.00 |
| GC | **OWNED** | **OWNED** | **OWNED** | **OWNED** | **OWNED** |
| SI | **OWNED** | **OWNED** | **OWNED** | **OWNED** | **OWNED** |
| CL | $10.00 | $220.00 | $1,100.00 | $2,200.00 | $13,200.00 |

GC and SI mbp-10 2020-present are already downloaded ($120.14 spent).  
ES and NQ mbp-10 are prohibitively expensive without significant new budget.

---

## Cost Matrix — mbo Schema

**Do not request mbo without contacting Databento support for a firm quote.**  
Estimated 3–10x mbp-10 cost. Shown as ranges only.

| Symbol | 1 day | 1 month | 6 months | 1 year | 2020-present (6 yr) |
|---|---|---|---|---|---|
| ES | $60–$200 | $1,300–$4,400 | $6,600–$22,000 | $13,200–$44,000 | Contact Databento |
| NQ | $36–$120 | $800–$2,600 | $4,000–$13,200 | $8,000–$26,400 | Contact Databento |
| GC | $18–$60 | $400–$1,300 | $2,000–$6,600 | $4,000–$13,200 | Contact Databento |
| SI | $10–$32 | $220–$700 | $1,100–$3,500 | $2,200–$7,000 | Contact Databento |
| CL | $30–$100 | $660–$2,200 | $3,300–$11,000 | $6,600–$22,000 | Contact Databento |

**MBO is not in scope until a dedicated budget of $1,000+ is allocated.**

---

## Recommended First Priority Purchase

**When new budget is available, purchase in this order:**

### Priority 1: ES trades — 6-month sample
- Symbol: ES (`ES.c.0`)
- Schema: `trades`
- Range: 2024-01-01 to 2024-07-01
- Estimated cost: **$30–$50**
- Rationale: Cheapest schema. Enables CVD, directional flow, buy/sell volume features on the most liquid US futures market.

### Priority 2: ES mbp-1 — 6-month sample (same period)
- Symbol: ES (`ES.c.0`)
- Schema: `mbp-1`
- Range: 2024-01-01 to 2024-07-01
- Estimated cost: **$30–$50** (add-on to Priority 1)
- Rationale: Adds microprice and L1 imbalance to ES. Enables the full microprice feature suite without mbp-10 cost.

### Priority 3: NQ trades — 1-year sample
- Symbol: NQ (`NQ.c.0`)
- Schema: `trades`
- Range: 2024-01-01 to 2025-01-01
- Estimated cost: **$50–$80**
- Rationale: Correlates with ES for pairs and inter-market strategies.

### Do Not Purchase (yet)
- ES mbp-10: $2,200+ per 6 months — not feasible without major budget
- Any mbo schema: contact Databento first
- Tier 3 symbols (ZN, ZB, FX): defer until Tier 1 strategies are live

---

## Notes

1. All prices are estimates. Actual cost depends on message volume during the exact date range requested.
2. Always run `tick_databento_cost_planner.py` to get an API-confirmed cost estimate before purchasing.
3. Message counts spike on high-volatility events (FOMC dates, CPI, NFP). Factor in 20% buffer.
4. Databento does not offer partial refunds. Once downloaded, cost is incurred.
5. The cost API endpoint (`metadata.get_cost`) gives a firm estimate before download begins. Always use it.
6. 2020 volumes were lower than 2022+ — cost per year was lower in 2020-2021 than in 2022-2026.

---

*Last updated: 2026-06-03. Update after every purchase with actual costs from billing dashboard.*
