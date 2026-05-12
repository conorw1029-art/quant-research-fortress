import sys; sys.path.insert(0, '.')
from src.zoo.registry import get_by_status, Status

# Read the current registry
with open('src/zoo/registry.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check if Batch 4 entries are already there
if 'BATCH 4' in content:
    print('Registry already contains Batch 4 entries. Skipping.')
    sys.exit(0)

# Batch 4 additions – VIX overlay on existing survivors + calendar expansion
addition = '''

# ----- BATCH 4: VIX OVERLAY ON SURVIVORS -----
_STRATEGIES.extend([
    # VIX overlay wrapping each survivor strategy
    StrategyEntry(
        key="vix_overlay_bollinger_rsi_fxe",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="mean_reversion",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="M6E",
        data_path_key="6E",
        requires_features=["atr", "rsi"],
        notes="Batch4. VIX overlay on Bollinger RSI 6E.",
    ),
    StrategyEntry(
        key="vix_overlay_bollinger_rsi_gc",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="mean_reversion",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MGC",
        data_path_key="GC",
        requires_features=["atr", "rsi"],
        notes="Batch4. VIX overlay on Bollinger RSI GC.",
    ),
    StrategyEntry(
        key="vix_overlay_donchian_cl",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="trend",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MCL",
        data_path_key="CL",
        requires_features=["atr"],
        notes="Batch4. VIX overlay on Donchian breakout CL.",
    ),
    StrategyEntry(
        key="vix_overlay_fomc_es",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="calendar",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="MES",
        data_path_key="ES",
        requires_features=[],
        notes="Batch4. VIX overlay on FOMC drift ES.",
    ),
    StrategyEntry(
        key="vix_overlay_fomc_zn",
        module_path="src.strategies.vix_overlay",
        class_name="VIXOverlayStrategy",
        category="calendar",
        status=Status.EXPERIMENTAL,
        test_method=TestMethod.WALK_FORWARD,
        timeframe="5min",
        instrument="ZN",
        data_path_key="ZN",
        requires_features=[],
        notes="Batch4. VIX overlay on FOMC drift ZN.",
    ),
])

# ----- BATCH 4: CALENDAR EVENT EXPANSION -----
_STRATEGIES.extend(_multi(
    "boj", "src.strategies.calendar_events_batch4", "BOJStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["6J", "NQ"], [],
    "Batch4. Bank of Japan policy decision drift.",
))
_STRATEGIES.extend(_multi(
    "boe", "src.strategies.calendar_events_batch4", "BOEStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["6B"], [],
    "Batch4. Bank of England policy decision drift.",
))
_STRATEGIES.extend(_multi(
    "ism", "src.strategies.calendar_events_batch4", "ISMStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. ISM Manufacturing PMI drift.",
))
_STRATEGIES.extend(_multi(
    "ppi", "src.strategies.calendar_events_batch4", "PPIStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. PPI release drift.",
))
_STRATEGIES.extend(_multi(
    "gdp", "src.strategies.calendar_events_batch4", "GDPStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. GDP release drift.",
))
_STRATEGIES.extend(_multi(
    "retail_sales", "src.strategies.calendar_events_batch4", "RetailSalesStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min", ["ES", "NQ"], [],
    "Batch4. Retail Sales release drift.",
))
'''

with open('src/zoo/registry.py', 'a', encoding='utf-8') as f:
    f.write(addition)

print('Registry patched with Batch 4 entries.')

# Verify
exp = get_by_status(Status.EXPERIMENTAL)
batch4 = [e for e in exp if 'vix_overlay' in e.key or any(e.key.startswith(p) for p in ['boj_','boe_','ism_','ppi_','gdp_','retail_sales_'])]
print(f'Batch 4 entries: {len(batch4)}')
for e in batch4:
    print(f'  {e.key}')
