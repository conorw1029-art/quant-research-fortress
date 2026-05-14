"""
STEP 1: Conservative Cost Stress Test
======================================
Re-runs the 5 zoo survivors with slippage bumped from 'realistic'
(1 tick/side) to 'conservative' (2 ticks/side). Writes results to
zoo_stress_conservative.jsonl so the main zoo is never polluted.

Pass/fail uses the same go/no-go criteria as zoo_reevaluate.py.
"""

import sys, json, logging
from pathlib import Path

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from run_strategy import test_strategy
from src.zoo.database import ZooDatabase
from src.zoo.registry import get_by_key

# ── Go/no-go thresholds (must match zoo_reevaluate.py) ────────────
MIN_DSR   = 1.0
MIN_PF    = 1.25
MAX_P     = 0.05
MAX_DD    = 2000.0
MIN_N     = 30
DSR_WAIVER = 3.0

# ── Survivors from the latest zoo_reevaluate run ──────────────────
SURVIVORS = [
    # key                    DSR    PF     DD     n      notes
    ("bollinger_rsi_fxe",  11.254, 1.465, 2,    9453,  "regime-dep"),
    ("bollinger_rsi_gc",    4.590, 1.497, 382,  2314,  ""),
    ("donchian_breakout_cl",4.476, 2.998, 140,  236,   ""),
    ("fomc_drift",          1.627, 2.893, 136,  57,    ""),
    ("fomc_drift_zn",       1.107, 2.055, 3,    57,    ""),
]

ZOO_OUT = THIS_DIR.parent / "05_backtests" / "zoo_stress_conservative.jsonl"

def load_zoo_latest(zoo_path):
    """Load the most recent record per strategy from main zoo.jsonl."""
    main_zoo = THIS_DIR.parent / "05_backtests" / "zoo.jsonl"
    latest = {}
    if not main_zoo.exists():
        return latest
    with open(main_zoo, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            name = r.get("strategy_name", "")
            ts   = r.get("timestamp", "")
            if name not in latest or ts > latest[name].get("timestamp", ""):
                latest[name] = r
    return latest

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

def main():
    logger.info("=" * 70)
    logger.info("  STEP 1: CONSERVATIVE COST STRESS TEST")
    logger.info("  Slippage: realistic(1 tick/side) → conservative(2 ticks/side)")
    logger.info("=" * 70)

    zoo = ZooDatabase(ZOO_OUT)
    baseline = load_zoo_latest(THIS_DIR.parent / "05_backtests" / "zoo.jsonl")

    results = []

    for key, base_dsr, base_pf, base_dd, base_n, tag in SURVIVORS:
        entry = get_by_key(key)
        if entry is None:
            logger.error(f"  Registry key not found: {key}")
            results.append({
                "key": key, "status": "KEY_MISSING",
                "base_dsr": base_dsr, "stress_dsr": None,
                "base_pf": base_pf, "stress_pf": None,
                "verdict_base": "PASS", "verdict_stress": "ERROR",
                "survived": False, "new_failures": [],
            })
            continue

        logger.info(f"\n>>> {key} ({entry.instrument})  base DSR={base_dsr:+.3f} PF={base_pf:.3f}")
        r = test_strategy(
            entry=entry,
            zoo=zoo,
            cost_scenario="conservative",
        )

        # Pull the freshly written record back out
        stress_records = zoo.load()
        # Most recent entry for this strategy
        latest = None
        for rec in reversed(stress_records):
            if rec.get("strategy_name", "") == key:
                latest = rec
                break

        if latest is None or r.get("error"):
            logger.error(f"  No record written for {key}")
            results.append({
                "key": key, "status": "ERROR",
                "base_dsr": base_dsr, "stress_dsr": 0.0,
                "base_pf": base_pf, "stress_pf": 0.0,
                "verdict_base": "PASS", "verdict_stress": "ERROR",
                "survived": False, "new_failures": [str(r.get("error", "unknown"))],
            })
            continue

        v, fails = verdict(latest)
        survived = (v == "PASS")

        results.append({
            "key": key, "status": "OK",
            "tag": tag,
            "base_dsr":   base_dsr,
            "stress_dsr": float(latest.get("dsr", 0)),
            "dsr_delta":  float(latest.get("dsr", 0)) - base_dsr,
            "base_pf":   base_pf,
            "stress_pf": float(latest.get("oos_profit_factor", 0)),
            "pf_delta":  float(latest.get("oos_profit_factor", 0)) - base_pf,
            "base_dd":   base_dd,
            "stress_dd": float(abs(latest.get("oos_max_drawdown", 0))),
            "base_n":    base_n,
            "stress_n":  int(latest.get("n_oos_trades", 0)),
            "stress_wr": float(latest.get("oos_win_rate", 0)),
            "stress_p":  float(latest.get("oos_p_value", 1)),
            "verdict_base":   "PASS",
            "verdict_stress": v,
            "survived": survived,
            "new_failures": fails,
        })

    # ── REPORT ────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("  STEP 1 RESULTS: Conservative Cost Stress Test")
    print("  Criteria: DSR>=1.0 | PF>=1.25 | DD<=$2,000 | p<=0.05 | both_halves|mean_pnl>0")
    print("=" * 100)

    survivors_post = [r for r in results if r.get("survived")]
    killed = [r for r in results if not r.get("survived")]

    print(f"\n  {'Key':<28} {'Tag':<12} {'DSR base->stress':>21} {'PF base->stress':>18} {'DD base->stress':>16} {'Verdict':>10} {'Failures'}")
    print(f"  {'-'*110}")
    for r in results:
        tag_str = r.get("tag", "")
        if r["status"] == "OK":
            dsr_str = f"{r['base_dsr']:+.3f} → {r['stress_dsr']:+.3f} ({r['dsr_delta']:+.3f})"
            pf_str  = f"{r['base_pf']:.3f} → {r['stress_pf']:.3f} ({r['pf_delta']:+.3f})"
            dd_str  = f"${r['base_dd']:,.0f} → ${r['stress_dd']:,.0f}"
        else:
            dsr_str = "N/A"
            pf_str  = "N/A"
            dd_str  = "N/A"

        symbol = "✓" if r.get("survived") else "✗"
        fails_str = "|".join(r.get("new_failures", [])) or ""
        print(f"  {symbol} {r['key']:<27} {tag_str:<12} {dsr_str:>20} {pf_str:>18} {dd_str:>16} "
              f"{r.get('verdict_stress','?'):>10}  {fails_str}")

    print(f"\n  SURVIVORS POST-STRESS: {len(survivors_post)}/{len(results)}")
    for r in survivors_post:
        print(f"    ✓ {r['key']:<28}  DSR={r['stress_dsr']:+.3f}  PF={r['stress_pf']:.3f}  DD=${r['stress_dd']:,.0f}")

    if killed:
        print(f"\n  KILLED BY CONSERVATIVE COSTS: {len(killed)}")
        for r in killed:
            fails = ", ".join(r.get("new_failures", [r.get("status", "error")]))
            print(f"    ✗ {r['key']:<28}  {fails}")

    print(f"\n  Results written to: {ZOO_OUT}")
    print("=" * 100)

    return survivors_post

if __name__ == "__main__":
    main()
