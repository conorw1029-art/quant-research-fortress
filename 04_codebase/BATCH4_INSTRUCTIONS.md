# BATCH 4 — VIX OVERLAY + CALENDAR EXPANSION

## Files to install

1. `vix_overlay.py` → `src/strategies/vix_overlay.py`
2. `calendar_events_batch4.py` → `src/strategies/calendar_events_batch4.py`
3. `registry_additions_batch4.py` → contents to paste into `src/zoo/registry.py`

## Step 1 — Install strategy files

Copy the two `.py` files into `src/strategies/`.

## Step 2 — Update registry

Open `src/zoo/registry.py`. After the BATCH 3 `_STRATEGIES.extend(...)` blocks, paste contents of `registry_additions_batch4.py`.

This adds:
- 5 VIX overlay entries (2 GC + 6E for bollinger_rsi, 1 CL for donchian, 2 ES + ZN for fomc)
- 18 calendar event entries (BOJ×2, BOE×1, ISM×3, PPI×3, GDP×4, retail×3)

Total new entries: 23.

## Step 3 — Verify registry

```powershell
cd C:\Users\conor\Desktop\quant-research\04_codebase
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c "
import sys; sys.path.insert(0,'.')
from src.zoo.registry import get_by_status, Status
exp = get_by_status(Status.EXPERIMENTAL)
batch4 = [e for e in exp if e.key.startswith(('vix_','boj_','boe_','ism_','ppi_','gdp_','retail_'))]
print(f'Batch 4 entries: {len(batch4)} (should be ~23)')
for e in batch4:
    print(f'  {e.key:<32} {e.instrument} on {e.data_path_key}')
"
```

## Step 4 — Run batch 4

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -c "
import sys, subprocess, time
sys.path.insert(0,'.')
from src.zoo.registry import get_by_status, Status

PREFIXES = ('vix_','boj_','boe_','ism_','ppi_','gdp_','retail_')
entries = [e for e in get_by_status(Status.EXPERIMENTAL) if e.key.startswith(PREFIXES)]
print(f'Running {len(entries)} batch-4 strategies')

t0 = time.time()
passes = []
for i, e in enumerate(entries):
    print(f'[{i+1}/{len(entries)}] {e.key}', end='', flush=True)
    try:
        r = subprocess.run(
            [sys.executable,'run_strategy.py','--key',e.key,'--cost-scenario','realistic'],
            capture_output=True, text=True, timeout=900,
        )
        v, d = 'ERROR', 'N/A'
        for line in r.stdout.split('\n'):
            if 'verdict=' in line.lower():
                for p in line.split():
                    if p.startswith('verdict='): v = p.split('=')[1]
                    if p.startswith('DSR='): d = p.split('=')[1]
        print(f'  -> {v} DSR={d}')
        if v == 'PASS': passes.append((e.key, d))
    except subprocess.TimeoutExpired:
        print('  -> TIMEOUT')

print(f'\\nBATCH 4 DONE in {(time.time()-t0)/60:.0f}min')
print(f'SURVIVORS: {passes}')
"
```

## Step 5 — Re-evaluate

```powershell
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe zoo_reevaluate.py
```

## Expected outcomes

**VIX overlays:**
- `vix_bollinger_rsi_gc` regime=high → likely PASS (mean-rev works in volatility)
- `vix_donchian_cl` regime=low → likely PASS (trends cleaner in low vol)
- `vix_fomc_es` either regime → may improve DSR but already a survivor

**Calendar events:**
- `ism_drift_es` likely PASS (well-documented ES response)
- `gdp_drift_zn` possible PASS (rates respond strongly to GDP)
- BOJ/BOE: lower probability — central bank decisions outside US session add noise

**Realistic survivor count from this batch: 3-7.**

## Honest caveats

1. **Calendar event dates are approximate** — ISM/PPI/GDP/Retail Sales dates I listed are calendar-based (e.g., "1st of month") not actual release dates. Production version needs scraped historical dates from BLS/BEA. For now, this gives a directional signal — if a strategy looks promising, refine the date list.

2. **VIX overlay loads ES_1min.csv on every run** — the cache is per-process so each subprocess re-reads ~5.5M bars. Adds ~20s per VIX overlay test. Acceptable for 5 strategies.

3. **CalendarEventStrategy class signature** — I assumed it has `EVENT_DATES`, `EVENT_HOUR_ET`, `EVENT_MINUTE_ET`, `DRIFT_HOURS` as class attributes. If your existing version uses different names, tell me and I'll adjust.

If the BOJ/BOE/etc. strategies error with attribute issues, paste the traceback and I'll patch quickly.
