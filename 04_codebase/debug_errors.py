import subprocess, sys
SAMPLES = [
    "orb_es","gap_fill_es","fib_retracement_es","inside_bar_es",
    "nr7_breakout_es","ib_fade_es","vol_macd_es","connors_rsi_es",
]
for key in SAMPLES:
    print(f"\n{'='*60}\nTESTING: {key}\n{'='*60}")
    r = subprocess.run(
        [sys.executable,"run_strategy.py","--key",key,"--cost-scenario","realistic"],
        capture_output=True,text=True,cwd="."
    )
    print("STDOUT:", r.stdout[:1000] if r.stdout else "(empty)")
    print("STDERR:", r.stderr[:1000] if r.stderr else "(empty)")
    print("RETURNCODE:", r.returncode)
    if r.returncode==0:
        print(">>> This one works!")
        break
    if "Traceback" in r.stderr or "Error" in r.stderr:
        print("\n>>> Error found, stopping.")
        break
