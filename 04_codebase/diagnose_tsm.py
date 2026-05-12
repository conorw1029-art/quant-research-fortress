"""
Diagnostic for TSM and overnight_drift implementations.
Inspects what they actually produce, not what we hope they produce.
"""
import sys, pandas as pd
sys.path.insert(0, '.')

DATA_PATH = '../01_data/raw/CL_1min.csv'

def load_data(path):
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    ts = next(c for c in df.columns if 'ts_event' in c or 'timestamp' in c)
    df[ts] = pd.to_datetime(df[ts], utc=True)
    df = df.set_index(ts)
    df.index = df.index.tz_convert('America/New_York').tz_localize(None)
    return df

# ─────────────────────────────────────────────────────────────────────────
# DIAGNOSE TSM
# ─────────────────────────────────────────────────────────────────────────
print("="*70)
print("TSM DIAGNOSTIC on CL")
print("="*70)
from src.strategies.tsm import TimeSeriesMomentumStrategy

df = load_data(DATA_PATH)
print(f"Data: {len(df):,} bars, {df.index.min()} to {df.index.max()}")

s = TimeSeriesMomentumStrategy(params={'lookback_days': 252})
sig = s.generate_signals(df)
print(f"\nSignals generated:")
print(f"  Total non-zero: {(sig != 0).sum()}")
print(f"  Long (+1):      {(sig == 1).sum()}")
print(f"  Short (-1):     {(sig == -1).sum()}")
print(f"\nFirst 10 signal dates:")
print(sig[sig != 0].head(10))

trades = s.signals_to_trades(df, sig)
print(f"\nTrades generated: {len(trades)}")

if trades:
    tdf = pd.DataFrame(trades)
    print(f"\nFirst 5 trades:")
    print(tdf[['entry_time','exit_time','direction','entry_price','exit_price','gross_pnl','size_factor']].head())
    print(f"\nP&L summary:")
    print(f"  Total raw P&L (price points): {tdf['gross_pnl'].sum():.2f}")
    print(f"  Mean trade P&L:               {tdf['gross_pnl'].mean():.4f}")
    print(f"  Win rate:                     {(tdf['gross_pnl'] > 0).mean():.1%}")
    print(f"  Max winner:                   {tdf['gross_pnl'].max():.2f}")
    print(f"  Max loser:                    {tdf['gross_pnl'].min():.2f}")
    print(f"\nSize factor distribution:")
    print(f"  min:  {tdf['size_factor'].min():.3f}")
    print(f"  mean: {tdf['size_factor'].mean():.3f}")
    print(f"  max:  {tdf['size_factor'].max():.3f}")
    print(f"\nDirection distribution:")
    print(f"  Long:  {(tdf['direction'] == 1).sum()}")
    print(f"  Short: {(tdf['direction'] == -1).sum()}")

# ─────────────────────────────────────────────────────────────────────────
# DIAGNOSE OVERNIGHT DRIFT
# ─────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("OVERNIGHT DRIFT DIAGNOSTIC on ES")
print("="*70)
from src.strategies.overnight_drift import BondarenkoOvernightDriftStrategy

df_es = load_data('../01_data/raw/ES_1min.csv')
print(f"Data: {len(df_es):,} bars, {df_es.index.min()} to {df_es.index.max()}")

s2 = BondarenkoOvernightDriftStrategy(params={'window': (23, 30, 3, 30)})
sig2 = s2.generate_signals(df_es)
print(f"\nSignals generated:")
print(f"  Total non-zero: {(sig2 != 0).sum()}")
print(f"\nFirst 5 signal timestamps:")
print(sig2[sig2 != 0].head())
print(f"\nLast 5 signal timestamps:")
print(sig2[sig2 != 0].tail())

trades2 = s2.signals_to_trades(df_es, sig2)
print(f"\nTrades generated: {len(trades2)}")

if trades2:
    tdf2 = pd.DataFrame(trades2)
    print(f"\nFirst 5 trades:")
    print(tdf2[['entry_time','exit_time','direction','entry_price','exit_price','gross_pnl']].head())
    print(f"\nP&L summary:")
    print(f"  Total raw P&L (price points): {tdf2['gross_pnl'].sum():.2f}")
    print(f"  Mean trade P&L:               {tdf2['gross_pnl'].mean():.4f}")
    print(f"  Win rate:                     {(tdf2['gross_pnl'] > 0).mean():.1%}")
    print(f"  Max winner:                   {tdf2['gross_pnl'].max():.2f}")
    print(f"  Max loser:                    {tdf2['gross_pnl'].min():.2f}")

    # Sanity check entry/exit hour distribution
    print(f"\nEntry hour distribution (should all be ~23):")
    entry_hours = pd.to_datetime(tdf2['entry_time']).dt.hour
    print(entry_hours.value_counts().sort_index().head(10))
    print(f"\nExit hour distribution (should all be ~3):")
    exit_hours = pd.to_datetime(tdf2['exit_time']).dt.hour
    print(exit_hours.value_counts().sort_index().head(10))

    # Day of week distribution
    print(f"\nEntry day-of-week (0=Mon, 4=Fri, should NOT include Fri-Sat-Sun):")
    dow = pd.to_datetime(tdf2['entry_time']).dt.dayofweek
    print(dow.value_counts().sort_index())
