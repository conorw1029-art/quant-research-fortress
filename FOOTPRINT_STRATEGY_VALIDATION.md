# Footprint / L2 Strategy Validation & Deployment Plan
**Generated 2026-06-28** — consolidates the real 5-year Databento L2 backtests, the L2 backlog,
and live Databento cost probes. Purpose: when you top up Databento, you already know *exactly*
which footprint strategies to run and on which data tier.

---

## 1. Real Databento data costs (measured 2026-06-28, free metadata probe)
4 continuous symbols (MGC/MES/MNQ/SIL), GLBX.MDP3, 1 month:

| Schema | Cost/mo | Records/mo | Gives you |
|---|---|---|---|
| `ohlcv-1m` | **$0.31** | 85K | OHLCV bars only (no footprint) |
| `trades` | **$43.68** | 34.9M | **Real footprint: CVD, delta, buy/sell vol, absorption, large prints** |
| `mbp-10` | **$180.96** | 1.06B | Full order-book depth + imbalance (obi_5, imbal_L5, replenishment) |

⚠️ **Polling overlap warning:** the live poller re-pulls a 15-min window every 60s. Harmless for
`ohlcv` (tiny), but for `trades` that overlap would multiply the bill. The trades path must poll
**overlap-minimal** (advance from last-seen ts). Validate with a 1-day `--backfill-only` first
(~$1.50) before enabling the loop.

---

## 2. Strategy → data-tier mapping

| Strategy family | Needs | Tier |
|---|---|---|
| CVD divergence/microprice/VWAP, tape absorption, large-print velocity | trades (buy/sell vol, CVD) | **`trades` $44** |
| Depth imbalance, repeated replenishment, OFI multi-level, depth walls | resting book depth (obi/imbal_L5) | **`mbp-10` $181** |

**Most of your trusted footprint edge lives in the $44 `trades` tier.** Only the order-book-depth
strategies need the $181 tier.

---

## 3. Hardened survivors (real 5-yr L2 backtest, slippage + $4.50 RT commission applied)

### SILVER (SI) — strongest, most robust
| Strategy (params) | DSR | WR | Trades | Note |
|---|---|---|---|---|
| **CVD_Microprice** (cvd60, mp1.0, rr2.0, hold5) | 0.49 | 40.6% | 2,937 | ✅ **Survives 3-tick slippage — most robust edge we have.** trades tier |
| CVD_VWAP (vwap0.5, cvd70, hold12) | 0.43 | 38.7% | 6,645 | trades tier |
| CVD_Microprice (…hold10) | 0.42 | 40.0% | 2,937 | trades tier |
| CVD_VWAP (vwap0.25, cvd70, hold12) | 0.41 | 39.4% | 4,696 | trades tier |
| **Sweep_Continuation SI** | — | — | — | ✅ Appears genuine over 5 years. trades tier |

### GOLD (GC)
| Strategy (params) | DSR | WR | Trades | Note |
|---|---|---|---|---|
| CVD_VWAP (vwap0.25, hold12) | 0.51 | 41.4% | 6,405 | trades tier |
| Depth_Imbalance_Momentum (imbal0.3, persist3, hold5) | 0.47 | 41.9% | 27,104 | ⚠️ **overlap-inflated — REQUIRES strict 1-contract**. mbp-10 |
| Depth_Imbalance_Momentum (imbal0.3, persist2, hold10) | 0.44 | 41.1% | 30,312 | ⚠️ same caveat. mbp-10 |
| OFI_Continuation (ofi_pct93) | 0.37 | 40.9% | 2,349 | mbp-10 |
| Repeated_Replenishment (imbal0.4, persist5, hold8) | 0.36 | 41.7% | 5,307 | mbp-10 |

### ❌ Do NOT deploy
- **Sweep_Absorption_Reversal GC** — recency artifact, fails out-of-sample. Drop it.

---

## 4. Honest read on the edge
- DSR values are **modest (0.36–0.51)** but these are *post-cost, post-slippage* — real, not the
  pre-cost "Sharpe 4–6" headline numbers. Survival-grade edges, not lottery tickets.
- On a **$1k runway, 1-contract enforcement is mandatory** (the Depth_Imbalance overlap inflation
  bug means its backtest assumed stacking you can't afford anyway).
- The raw 5-yr L2 data is **not on the VPS** (only condensed feature parquets). Re-validate on fresh
  live `trades` data after ~2–4 weeks of flow before sizing up.

---

## 5. Recommended phased rollout (lowest spend first)
1. **Phase 1 — `trades` ($44/mo):** run the SI CVD_Microprice + CVD_VWAP (GC+SI) + Sweep_Continuation SI.
   These are the validated, slippage-resilient footprint edges. DRY_RUN on live data 2–4 weeks.
2. **Phase 2 — add `mbp-10` ($181/mo):** only if Phase 1 proves out, add Depth_Imbalance / OFI /
   Replenishment (with 1-contract enforcement). The 4× data cost must be earned by Phase 1 first.
3. Execution (Tradovate API keys) is the *separate* gate — Phase 1 can validate on live data in
   DRY_RUN before any prop-firm API access arrives.

**Net:** start at **$44/mo trades**, run the silver CVD edges, prove it, then decide on $181 depth.
