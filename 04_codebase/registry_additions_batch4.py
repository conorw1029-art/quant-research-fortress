# ════════════════════════════════════════════════════════════════════════
# REGISTRY ADDITIONS — BATCH 4
# Append to src/zoo/registry.py after the BATCH 3 entries
# ════════════════════════════════════════════════════════════════════════

# ----- BATCH 4: VIX REGIME OVERLAYS ON SURVIVORS -----
# Wraps each existing survivor with a high-vol or low-vol regime filter.
# Could promote near-misses and improve survivors via regime selection.
_STRATEGIES.extend(_multi(
    "vix_bollinger_rsi", "src.strategies.vix_overlay", "VIXBollingerRSI",
    "regime_filter", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["GC", "6E"], [],   # only markets where bollinger_rsi survived
    "Batch4. VIX regime overlay on Bollinger RSI survivors.",
))
_STRATEGIES.extend(_multi(
    "vix_donchian", "src.strategies.vix_overlay", "VIXDonchianBreakout",
    "regime_filter", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["CL"], [],   # CL is the donchian survivor
    "Batch4. VIX regime overlay on Donchian breakout CL.",
))
_STRATEGIES.extend(_multi(
    "vix_fomc", "src.strategies.vix_overlay", "VIXFomcDrift",
    "regime_filter", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "ZN"], [],   # FOMC survives on ES + ZN
    "Batch4. VIX regime overlay on FOMC drift survivors.",
))

# ----- BATCH 4: CALENDAR EVENT EXPANSION -----
# Targeted: only test on markets where each event is most relevant
_STRATEGIES.extend(_multi(
    "boj_drift", "src.strategies.calendar_events_batch4", "BOJDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["6J", "NQ"], [],
    "Batch4. BOJ rate decision drift. ~8/yr.",
))
_STRATEGIES.extend(_multi(
    "boe_drift", "src.strategies.calendar_events_batch4", "BOEDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["6B"], [],
    "Batch4. BOE rate decision drift. 8/yr.",
))
_STRATEGIES.extend(_multi(
    "ism_drift", "src.strategies.calendar_events_batch4", "ISMDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES", "NQ", "ZN"], [],
    "Batch4. ISM Manufacturing PMI drift. 12/yr.",
))
_STRATEGIES.extend(_multi(
    "ppi_drift", "src.strategies.calendar_events_batch4", "PPIDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES", "ZN", "ZB"], [],
    "Batch4. PPI release drift. 12/yr.",
))
_STRATEGIES.extend(_multi(
    "gdp_drift", "src.strategies.calendar_events_batch4", "GDPDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES", "NQ", "ZN", "ZB"], [],
    "Batch4. Advance GDP drift. 4/yr.",
))
_STRATEGIES.extend(_multi(
    "retail_sales_drift", "src.strategies.calendar_events_batch4", "RetailSalesDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.ONE_SHOT_IS_OOS,
    "5min", ["ES", "NQ", "ZN"], [],
    "Batch4. Retail sales drift. 12/yr.",
))
