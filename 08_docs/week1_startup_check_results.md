# Week 1 Startup Check Results
**Date:** 2026-05-18  
**Commands run:**
```
venv_new\Scripts\python.exe -X utf8 tick_startup_checklist.py --quick
venv_new\Scripts\python.exe -X utf8 tick_signal_log_reader.py --days 7 --trades
```

---

## Startup Checklist Result

**Overall: 29 PASS / 11 WARN / 0 FAIL — OK TO PROCEED**

| Section | Result | Detail |
|---|---|---|
| Module imports (11 modules) | **PASS** | All imports clean including v5, news monitor, tradovate client |
| Kill switch | **PASS** | KILL_SWITCH.txt present, status = RUN (not active) |
| Allowlist integrity | **PASS** | 15 entries, all 15 strategies covered |
| Demo candidate | **PASS** | ES/cvd_divergence_large_print/15m is the only DEMO_CANDIDATE |
| Contract expiry | **PASS** | MESM5/MNQM5 expire 2026-06-20 (33 days), MGCM5 2026-06-27 (40 days) |
| Risk configuration | **PASS** | USE_MICROS=True, max_trade_risk=$200, max_daily_loss=$600, max_trailing_dd=$800 |
| Bracket order dry-run | **PASS** | 5/5 bracket validations pass |
| Log directory | **PASS** | 195 signals logged today |
| Bar data freshness | **WARN x11** | All bars stale (last update 2026-05-14) — bar builder not running |

### Data Freshness Warning
All bar files are stale (approximately 4,900 minutes / ~3.4 days since last update). This is expected — the bar builder has not been running since the last session. No data is lost; the parquet files remain intact. Start `tick_bar_builder.py` to refresh.

---

## Signal Log (Last 7 Days)

**Total signals: 498 | Accepted: 108 | Rejected: 390 (all due to allowlist)**

| Strategy ID | Key | Accepted | Rejected | Notes |
|---|---|---|---|---|
| 3 | ES/cvd_divergence/15m | 51 | 0 | All signals accepted (ENABLED_DRY_RUN) |
| 4 | ES/tape_absorption/15m | 51 | 0 | All signals accepted (ENABLED_DRY_RUN) |
| 1 | GC/obi_threshold/1m | 3 | 75 | Mostly rejected (DISABLED_FOR_LIVE) |
| 11 | ES/avg_order_size_divergence/30m | 3 | 63 | Mostly rejected (DISABLED_FOR_LIVE) |
| 5,6,10,12 | NQ/GC disabled strategies | 0 | 252 | All rejected (DISABLED_FOR_LIVE) |

**Notable:** Strategy 2 (DEMO_CANDIDATE) did not fire in the 7-day window. Strategies 3 and 4 are the most active dry-run strategies.

All rejections are due to allowlist status (no logic errors, no unexpected rejections).

---

## Whether It Is Safe to Proceed to Mock Bracket Implementation

**YES — safe to proceed to mock bracket implementation.**

No critical failures. Kill switch is active (RUN). Allowlist integrity confirmed. Risk configuration within limits. Bracket dry-run validation passes. The only warnings are bar data staleness, which does not affect mock bracket testing.

**Next:** See `week1_bracket_state_reconciliation_report.md` for full Week 1 implementation results.
