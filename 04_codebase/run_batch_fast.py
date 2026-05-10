"""
run_batch_fast.py - in-process batch runner
Loads each market CSV once, reuses across all strategies on that market.

USAGE:
    python run_batch_fast.py
    python run_batch_fast.py --strategy connors_rsi
    python run_batch_fast.py --market ES
    python run_batch_fast.py --dry-run
"""
import argparse, importlib, sys, time, traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, '.')
import pandas as pd
from src.data.data_schema import DATA_PATHS, INSTRUMENTS
from src.zoo.registry import get_by_status, Status
from src.zoo.database import ZooDatabase

RAW_DIR = Path('..') / '01_data' / 'raw'
_cache = {}

def load_data(key):
    if key in _cache:
        return _cache[key]
    csv = DATA_PATHS.get(key)
    if not csv or not (RAW_DIR/csv).exists():
        return None
    try:
        t0 = time.time()
        df = pd.read_csv(RAW_DIR/csv)
        df.columns = [c.lower() for c in df.columns]
        ts = next((c for c in df.columns if 'ts_event' in c or 'timestamp' in c), None)
        if ts:
            df[ts] = pd.to_datetime(df[ts], utc=True, errors='coerce')
            df = df.set_index(ts)
            df.index = df.index.tz_convert('America/New_York').tz_localize(None)
        _cache[key] = df
        print(f'  [loaded {key}: {len(df):,} bars in {time.time()-t0:.1f}s]')
        return df
    except Exception as e:
        print(f'  [LOAD ERROR {key}: {e}]')
        return None

def run_one(entry, data, cost_scenario, zoo):
    # Import strategy CLASS (engine.run() expects the class, not an instance)
    try:
        mod = importlib.import_module(entry.module_path.replace('/', '.'))
        strategy_cls = getattr(mod, entry.class_name)
        strategy_instance = strategy_cls()  # for zoo recording only
    except Exception as e:
        print(f'  IMPORT ERR: {e}')
        return 'ERROR'

    # Build cost model and engine
    try:
        from src.backtesting.cost_model import TransactionCost, SlippageScenario
        from src.backtesting.walk_forward import WalkForwardEngine
        spec = INSTRUMENTS.get(entry.instrument)
        if not spec:
            print(f'  NO SPEC: {entry.instrument}')
            return 'ERROR'
        sc = {
            'zero':         SlippageScenario.ZERO,
            'optimistic':   SlippageScenario.OPTIMISTIC,
            'realistic':    SlippageScenario.REALISTIC,
            'conservative': SlippageScenario.CONSERVATIVE,
        }.get(cost_scenario, SlippageScenario.REALISTIC)

        engine = WalkForwardEngine(
            cost_model=TransactionCost(spec, sc),
        )
        result = engine.run(data, strategy_cls)  # pass CLASS not instance
    except Exception as e:
        print(f'  RUN ERR: {e}')
        traceback.print_exc()
        try:
            zoo.record_from_result(strategy=strategy_instance, result=None,
                notes=f'instrument={entry.instrument}; fast_batch', error=str(e))
        except Exception:
            pass
        return 'ERROR'

    try:
        rec = zoo.record_from_result(
            strategy=strategy_instance, result=result,
            notes=f'instrument={entry.instrument} cost={cost_scenario}; fast_batch',
        )
        return rec.verdict
    except Exception as e:
        print(f'  RECORD ERR: {e}')
        return 'ERROR'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cost-scenario', default='realistic',
        choices=['zero','optimistic','realistic','conservative'])
    ap.add_argument('--market',   default='')
    ap.add_argument('--strategy', default='')
    ap.add_argument('--zoo',      default='../05_backtests/zoo.jsonl')
    ap.add_argument('--dry-run',  action='store_true')
    args = ap.parse_args()

    zoo = ZooDatabase(args.zoo)
    available = {k for k,f in DATA_PATHS.items() if (RAW_DIR/f).exists()}
    entries = [e for e in get_by_status(Status.EXPERIMENTAL)
               if e.data_path_key in available
               and (not args.market   or args.market.upper()   in e.data_path_key.upper())
               and (not args.strategy or args.strategy.lower() in e.key.lower())]

    print(f'\nFAST BATCH: {len(entries)} strategies | cost={args.cost_scenario}')
    if args.dry_run:
        for e in entries:
            print(f'  {e.key:<42} {e.instrument} on {e.data_path_key}')
        return

    by_market = defaultdict(list)
    for e in entries: by_market[e.data_path_key].append(e)

    results = defaultdict(list)
    done, total = 0, len(entries)
    t_start = time.time()

    for mkt, mkt_entries in sorted(by_market.items()):
        data = load_data(mkt)
        if data is None:
            for e in mkt_entries: results['ERROR'].append(e.key)
            continue
        for entry in mkt_entries:
            done += 1
            print(f'[{done}/{total}] {entry.key}', end='', flush=True)
            t0 = time.time()
            v = run_one(entry, data, args.cost_scenario, zoo)
            print(f'  -> {v} ({time.time()-t0:.0f}s)')
            results[v].append(entry.key)

    elapsed = time.time() - t_start
    print(f'\n{"="*70}')
    print(f'DONE: {total} strategies in {elapsed/60:.1f}min')
    print(f'PASS={len(results["PASS"])}  FAIL={len(results["FAIL"])}  ERROR={len(results["ERROR"])}')
    if results['PASS']:
        print('\nSURVIVORS:')
        for k in results['PASS']: print(f'  + {k}')
    if results['ERROR']:
        print(f'\nERRORS: {results["ERROR"]}')

if __name__ == '__main__':
    main()
