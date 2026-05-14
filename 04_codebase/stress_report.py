"""Print Step 1 conservative stress test results from zoo_stress_conservative.jsonl."""
import json
from pathlib import Path

ZOO = Path("../05_backtests/zoo_stress_conservative.jsonl")
MIN_DSR=1.0; MIN_PF=1.25; MAX_P=0.05; MAX_DD=2000.0; MIN_N=30; DSR_WAIVER=3.0

BASELINE = {
    "bollinger_rsi_fxe":    (11.254, 1.465,  2,    9453, "regime-dep"),
    "bollinger_rsi_gc":     ( 4.590, 1.497,  382,  2314, ""),
    "donchian_breakout_cl": ( 4.476, 2.998,  140,  236,  ""),
    "fomc_drift":           ( 1.627, 2.893,  136,  57,   ""),
    "fomc_drift_zn":        ( 1.107, 2.055,  3,    57,   ""),
}

def verdict(r):
    n   = int(r.get("n_oos_trades", 0))
    dsr = float(r.get("dsr", 0))
    pf  = float(r.get("oos_profit_factor", 0))
    dd  = float(abs(r.get("oos_max_drawdown", 0)))
    p   = float(r.get("oos_p_value", 1))
    bh  = bool(r.get("oos_both_halves_positive", False))
    mp  = float(r.get("oos_mean_pnl", 0))
    waiver = dsr >= DSR_WAIVER
    fails = []
    if n   < MIN_N:   fails.append(f"n({n})")
    if dsr < MIN_DSR: fails.append(f"dsr({dsr:+.3f})")
    if pf  < MIN_PF:  fails.append(f"pf({pf:.3f})")
    if dd  > MAX_DD:  fails.append(f"dd(${dd:,.0f})")
    if p   > MAX_P:   fails.append(f"p({p:.3g})")
    if not bh and not waiver: fails.append("both_halves")
    if mp  <= 0:      fails.append("mean_pnl<=0")
    return ("PASS" if not fails else "FAIL"), fails

records = {}
with open(ZOO) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        name = r.get("strategy_name", "")
        ts   = r.get("timestamp", "")
        if name not in records or ts > records[name].get("timestamp", ""):
            records[name] = r

print("=" * 105)
print("  STEP 1: Conservative Cost Stress Test  (realistic 1 tick/side  ->  conservative 2 ticks/side)")
print("  Criteria: DSR>=1.0 | PF>=1.25 | DD<=$2,000 | p<=0.05 | both_halves | mean_pnl>0")
print("=" * 105)
print()
hdr = f"  {'':4} {'Strategy':<28} {'DSR_base':>9} {'DSR_cons':>9} {'dDSR':>7}  {'PF_base':>8} {'PF_cons':>8} {'dPF':>7}  {'DD_base':>9} {'DD_cons':>9}  {'n_cons':>7}  {'Verdict':<8}  Failures"
print(hdr)
print("  " + "-" * 110)

for key, (bd, bp, bdd, bn, tag) in BASELINE.items():
    r   = records.get(key, {})
    v, fails = verdict(r)
    sd  = float(r.get("dsr", 0))
    sp  = float(r.get("oos_profit_factor", 0))
    sdd = float(abs(r.get("oos_max_drawdown", 0)))
    sn  = int(r.get("n_oos_trades", 0))
    sym = "PASS" if v == "PASS" else "FAIL"
    tag_str = f"[{tag}]" if tag else ""
    fails_str = "|".join(fails) if fails else "-"
    print(
        f"  {sym:<4} {key:<28} {bd:>+9.3f} {sd:>+9.3f} {sd-bd:>+7.3f}"
        f"  {bp:>8.3f} {sp:>8.3f} {sp-bp:>+7.3f}"
        f"  {bdd:>8,.0f} {sdd:>8,.0f}  {sn:>7}  {v:<8}  {fails_str}  {tag_str}"
    )

print()
survivors = [k for k in BASELINE if verdict(records.get(k, {}))[0] == "PASS"]
killed    = [k for k in BASELINE if verdict(records.get(k, {}))[0] != "PASS"]

print(f"  SURVIVORS POST-CONSERVATIVE STRESS: {len(survivors)}/5")
for s in survivors:
    r = records.get(s, {})
    sd  = float(r.get("dsr", 0))
    sp  = float(r.get("oos_profit_factor", 0))
    sdd = float(abs(r.get("oos_max_drawdown", 0)))
    sn  = int(r.get("n_oos_trades", 0))
    print(f"    PASS  {s:<28}  DSR={sd:+.3f}  PF={sp:.3f}  DD=${sdd:,.0f}  n={sn}")

if killed:
    print(f"\n  KILLED BY CONSERVATIVE COSTS: {len(killed)}")
    for k in killed:
        r = records.get(k, {})
        _, fails = verdict(r)
        print(f"    FAIL  {k:<28}  {' | '.join(fails)}")
else:
    print()
    print("  CONCLUSION: All 5 survivors held PASS under conservative slippage.")
    print("  The edge is robust to 2x slippage. Step 1 cleared.")

print("=" * 105)
