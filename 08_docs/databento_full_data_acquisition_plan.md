# Databento Full Data Acquisition Plan
**Project:** The Fortress — Quantitative Trading Research  
**Date:** 2026-06-03  
**Status:** LIVE PLANNING DOCUMENT — update after every purchase  

---

## 0. Budget Status

| Item | Amount |
|---|---|
| Total budget | $125.00 |
| Spent to date | $120.14 |
| **Remaining** | **$4.86** |

**Budget is essentially exhausted. No new downloads without explicit cost approval and new budget allocation.**  
All scripts default to `--dry-run`. Use `--approve-download` only after reviewing the cost estimate output.

---

## 1. Data Already Available

| Symbol | Schema | Date Range | Location |
|---|---|---|---|
| GC (Gold) | mbp-10 | 2020-01-01 – 2026-present | `01_data/raw/GC/` |
| SI (Silver) | mbp-10 | 2020-01-01 – 2026-present | `01_data/raw/SI/` |

These files are the foundation. Do not re-download or overwrite.  
Derived tick-bar features live in `01_data/tick_bars/`.

---

## 2. Symbol Priority Tiers

### Tier 1 — Core Liquid Futures (Highest Priority)
These are the most liquid U.S. futures markets. Order flow features are most reliable here due to tight spreads and continuous participation.

| Symbol | Description | Exchange | Dataset |
|---|---|---|---|
| GC | Gold futures (100 oz) | COMEX / CME | GLBX.MDP3 |
| SI | Silver futures (5,000 oz) | COMEX / CME | GLBX.MDP3 |
| ES | E-mini S&P 500 | CME | GLBX.MDP3 |
| NQ | E-mini Nasdaq 100 | CME | GLBX.MDP3 |
| CL | WTI Crude Oil | NYMEX / CME | GLBX.MDP3 |

### Tier 2 — Micro Contracts (Second Priority)
Same underlying as Tier 1 but smaller multiplier. Useful for micro-structure research where tick count differs from full contracts.

| Symbol | Description | Exchange | Dataset |
|---|---|---|---|
| MGC | Micro Gold (10 oz) | COMEX / CME | GLBX.MDP3 |
| SIL | Micro Silver (1,000 oz) | COMEX / CME | GLBX.MDP3 |
| MES | Micro E-mini S&P 500 | CME | GLBX.MDP3 |
| MNQ | Micro E-mini Nasdaq 100 | CME | GLBX.MDP3 |
| MCL | Micro WTI Crude Oil | NYMEX / CME | GLBX.MDP3 |

### Tier 3 — Deferred (Later, Separate Budget Approval)
These require significant budget. Do not acquire until Tier 1 and 2 strategies are validated.

| Symbol | Description | Exchange |
|---|---|---|
| ZN | 10-Year Treasury Note | CBOT |
| ZB | 30-Year Treasury Bond | CBOT |
| 6E | Euro FX | CME |
| 6J | Japanese Yen | CME |
| 6B | British Pound | CME |
| RTY / M2K | Russell 2000 / Micro Russell | CME |
| YM / MYM | Dow Jones / Micro Dow | CBOT |

---

## 3. Schema Reference

### 3.1 Schema Types Available

| Schema | Description | Relative Cost | Primary Use |
|---|---|---|---|
| `trades` | Individual trade prints (price, size, aggressor side) | Low | CVD, trade flow, buy/sell volume |
| `mbp-1` | Best bid/ask + sizes after every quote change | Medium | Microprice, spread, L1 imbalance |
| `mbp-10` | Full 10-level depth after every book event | High | OFI, depth imbalance, sweeps, walls |
| `mbo` | Individual order events (add, modify, cancel, fill) | Very High | True queue position, iceberg detection |

### 3.2 Schema Selection Rules

- **Start with `trades`** when you only need CVD and directional flow. Cheapest schema.
- **Use `mbp-1`** when you need spread and microprice but not full depth.
- **Use `mbp-10`** for full L2 feature engine. Required for OFI, imbalance, sweeps, walls, absorption.
- **Never download `mbo`** without contacting Databento for a firm quote. It is the most expensive schema and may exceed entire remaining budget for a single day.
- GC and SI `mbp-10` 2020-2026 are already downloaded. Do not re-query these.

---

## 4. Historical Date Range Guidelines

| Priority | Range | Use Case |
|---|---|---|
| Minimum | 2022-01-01 – present | Enough for basic strategy validation |
| Target | 2020-01-01 – present | Covers COVID volatility regime |
| Ideal | 2018-01-01 – present | Pre-COVID baseline included |
| Do not go earlier than | 2018-01-01 | Data quality and schema compatibility uncertain |

**Current holdings (GC, SI mbp-10) cover 2020-present — this is the "target" tier.**  
For new symbols, begin with a 1-day sample, then 1-month, before committing to multi-year.

---

## 5. Absolute Data Rules

These rules are enforced in all download scripts and must never be bypassed:

1. **Cost estimate first.** Always run `tick_databento_cost_planner.py` before any download. Never download blind.
2. **Tiny sample first.** Download 1 day, inspect output, confirm feature engineering works, then expand.
3. **Never overwrite raw files.** If the target file already exists, skip with a warning. Raw files are the ground truth.
4. **Validate after download.** Check file size > 0. Read first record. Confirm schema matches expectation.
5. **Log every download.** Every completed download writes to `01_data/raw/MANIFEST.jsonl`.
6. **No mbo without explicit quote.** Contact Databento support for MBO pricing before requesting a cost estimate via API.
7. **Budget gate: $4.86 remaining.** No download proceeds without a new explicit budget allocation from the user.

---

## 6. Download Workflow

Execute these phases strictly in order. Never skip a phase.

### Phase 1 — 1-Day Sample

```
python tick_databento_cost_planner.py --symbols ES --schema mbp-10 --start 2024-01-02 --end 2024-01-03
```

Review cost estimate. If acceptable, proceed:

```
python tick_databento_download_manager.py \
    --symbols ES --schema mbp-10 \
    --start 2024-01-02 --end 2024-01-03 \
    --approve-download --max-cost-usd 2.00
```

Validate: inspect file, run `tick_databento_to_features.py`, confirm output columns match expectation.

### Phase 2 — 1-Month Sample

Only proceed if Phase 1 succeeded and features look correct.

```
python tick_databento_cost_planner.py --symbols ES --schema mbp-10 --start 2024-01-01 --end 2024-02-01
```

Review cost. Approve explicitly.

### Phase 3 — 6-Month or 1-Year

Only proceed after Phase 2 validation is complete. Requires updated budget.

```
python tick_databento_cost_planner.py --symbols ES --schema mbp-10 --start 2023-01-01 --end 2024-01-01
```

### Phase 4 — Multi-Year

Only after strategies trained on Phase 3 data show positive out-of-sample results.  
Requires explicit budget approval session.

---

## 7. Manifest Logging Requirements

Every download writes one JSON line to `01_data/raw/MANIFEST.jsonl`.

Required fields per entry:

```json
{
  "timestamp": "2026-06-03T12:00:00Z",
  "symbol": "ES",
  "schema": "mbp-10",
  "start": "2024-01-02",
  "end": "2024-01-03",
  "file_path": "01_data/raw/ES/mbp-10/2024-01-02_2024-01-03.dbn.zst",
  "record_count": 1450000,
  "estimated_cost_usd": 1.23,
  "actual_cost_usd": null,
  "downloaded_by": "tick_databento_download_manager.py",
  "validated": true,
  "notes": ""
}
```

`actual_cost_usd` is populated manually from the Databento billing dashboard after the fact.  
`validated` is set to `true` only if the post-download validation check passed.

---

## 8. Cost Approval Gates

| Estimated Cost | Required Action |
|---|---|
| < $1.00 | Proceed with `--approve-download --max-cost-usd 1.00` |
| $1.00 – $10.00 | Must explicitly confirm in session before running |
| $10.00 – $50.00 | Requires new budget allocation discussion |
| > $50.00 | Stop. Do not proceed without explicit multi-message approval and budget confirmation |

Given current remaining budget of $4.86, the effective gate is: **any download must cost < $4.86 total**.  
A new budget allocation is required before any purchase that would exceed this.

---

## 9. Recommended First Purchase (When Budget Allows)

**Target:** ES trades schema, 6-month sample (2024-01-01 to 2024-07-01)

Rationale:
- `trades` is the cheapest schema — suitable for CVD, directional flow, buy/sell volume features
- ES has the highest liquidity of any U.S. futures market — lowest noise in L1 features
- 6 months gives enough data to train and cross-validate a single strategy family
- Estimated cost: $15–$35 (see cost matrix for detailed estimate)

After validating `trades` features on ES, the second priority is `mbp-10` for ES (adds full depth features).

---

## 10. File Location Conventions

| Data Type | Path |
|---|---|
| Raw DBN files | `01_data/raw/{SYMBOL}/{SCHEMA}/{start}_{end}.dbn.zst` |
| Feature bar parquets | `01_data/tick_bars/{SYMBOL}_{SCHEMA}_{bar_freq}_bars.parquet` |
| Download manifest | `01_data/raw/MANIFEST.jsonl` |
| Cost estimate JSONs | `08_docs/cost_estimates/{timestamp}_estimate.json` |

---

*Last updated: 2026-06-03. Regenerate after each download session.*
