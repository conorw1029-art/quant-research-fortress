"""
Audit data coverage for every market CSV.
Shows date range, bar count, and any gaps > 5 days.

Run from 04_codebase/:
  python audit_data.py
"""
import sys, pandas as pd
from pathlib import Path

sys.path.insert(0, '.')
from src.data.data_schema import DATA_PATHS

RAW_DIR = Path('..') / '01_data' / 'raw'

print(f"{'Market':<6} {'Bars':>12}  {'Start':<12}  {'End':<12}  {'Years':>6}  Notes")
print("-" * 80)

issues = []

for key in sorted(DATA_PATHS.keys()):
    csv = DATA_PATHS[key]
    path = RAW_DIR / csv
    if not path.exists():
        print(f"{key:<6}  MISSING FILE: {csv}")
        issues.append(f"{key}: missing")
        continue
    
    try:
        # Read just timestamp column for speed
        df = pd.read_csv(path, usecols=[0])
        df.columns = [c.lower() for c in df.columns]
        ts_col = df.columns[0]
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True, errors='coerce')
        df = df.dropna()
        
        start = df[ts_col].min()
        end   = df[ts_col].max()
        years = (end - start).days / 365.25
        n     = len(df)
        
        notes = []
        # Flag short coverage
        if years < 5:
            notes.append("SHORT COVERAGE")
            issues.append(f"{key}: only {years:.1f} years")
        # Flag stale data
        days_stale = (pd.Timestamp.now(tz='UTC') - end).days
        if days_stale > 60:
            notes.append(f"STALE ({days_stale}d old)")
            issues.append(f"{key}: stale by {days_stale} days")
        
        # Check largest gap
        diffs = df[ts_col].diff().dropna()
        max_gap_days = diffs.max().days if len(diffs) > 0 else 0
        if max_gap_days > 14:
            notes.append(f"GAP {max_gap_days}d")
        
        note_str = "; ".join(notes) if notes else "OK"
        print(f"{key:<6} {n:>12,}  {start.strftime('%Y-%m-%d')}  {end.strftime('%Y-%m-%d')}  {years:>5.1f}y  {note_str}")
    except Exception as e:
        print(f"{key:<6}  ERROR reading: {e}")
        issues.append(f"{key}: read error")

print("\n" + "=" * 80)
if issues:
    print(f"⚠ ISSUES FOUND ({len(issues)}):")
    for i in issues: print(f"  - {i}")
else:
    print("✓ All markets have full coverage")
