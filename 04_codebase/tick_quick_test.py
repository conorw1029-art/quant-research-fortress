"""Quick test — ES 5m battery timing."""
import sys, time
sys.path.insert(0, 'C:/Users/conor/Desktop/quant-research/04_codebase')
import pandas as pd, numpy as np
from itertools import product

t0 = time.time()
bars = pd.read_parquet('C:/Users/conor/Desktop/quant-research/01_data/tick_bars/ES_bars_5m.parquet')
close = bars['close']; vol = bars.get('volume', pd.Series(1, index=bars.index))
session_key = (bars.index - pd.Timedelta(hours=17)).date
vwap = pd.Series(np.nan, index=bars.index)
s_series = pd.Series(session_key, index=bars.index)
print(f"ES 5m bars: {len(bars):,}. Computing VWAP...", flush=True)
for date, grp_idx in s_series.groupby(s_series).groups.items():
    v = vol.loc[grp_idx]; c = close.loc[grp_idx]
    vwap.loc[grp_idx] = (c * v).cumsum() / v.cumsum().replace(0, np.nan)
bars['session_vwap'] = vwap
print(f"VWAP done in {time.time()-t0:.1f}s", flush=True)

from src.strategies.es_nq_price_action import (
    VWAPDeviationStrategy, PrevDayHLSweepRevStrategy,
    RangeContractionBreakoutStrategy, EnhancedORBStrategy
)
spec = {'tick_size': 0.25, 'tick_value': 12.5}

results = []
t1 = time.time()
for StratClass in [VWAPDeviationStrategy, PrevDayHLSweepRevStrategy,
                   RangeContractionBreakoutStrategy, EnhancedORBStrategy]:
    grid = StratClass.param_grid
    combos = list(product(*grid.values()))
    for combo in combos:
        params = dict(zip(grid.keys(), combo))
        strat = StratClass(params=params)
        try:
            sigs = strat.generate_signals(bars)
            trades = strat.signals_to_trades(bars, sigs)
            if len(trades) < 30:
                continue
            t = pd.DataFrame(trades)
            net = t['gross_pnl'] - 2 * spec['tick_size']
            dollar_net = net * (spec['tick_value'] / spec['tick_size'])
            if 'exit_time' in t.columns:
                daily = dollar_net.groupby(pd.to_datetime(t['exit_time']).dt.date).sum()
            else:
                daily = dollar_net
            sharpe = (daily.mean()/(daily.std()+1e-9)) * 252**0.5
            wr = (t['gross_pnl']>0).mean()
            dsr = max(sharpe * 0.85 - 0.05, -99)
            results.append({'strategy': strat.name, 'wr': wr, 'sharpe': sharpe,
                           'dsr': dsr, 'total_net': dollar_net.sum(), 'n': len(trades)})
            print(f"  {strat.name:<35} {len(trades):>5}t  wr={wr:.1%}  dsr={dsr:+.2f}  ${dollar_net.sum():>9,.0f}", flush=True)
        except Exception as e:
            print(f"  {StratClass.__name__} ERROR: {e}", flush=True)

print(f"\nTotal combos: {len(results)} in {time.time()-t1:.1f}s", flush=True)
surv = [r for r in results if r['dsr'] > 0.0 and r['wr'] >= 0.40]
print(f"Positive-DSR combos: {len(surv)}")
for r in sorted(surv, key=lambda x: x['dsr'], reverse=True)[:5]:
    print(f"  {r['strategy']:<35} dsr={r['dsr']:+.3f}  wr={r['wr']:.1%}  ${r['total_net']:>9,.0f}")
