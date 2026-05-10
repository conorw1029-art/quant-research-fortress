import argparse, json, sys
from pathlib import Path
import pandas as pd

MIN_DSR=1.0; MIN_PF=1.25; MAX_P=0.05; MAX_DD_USD=2000.0; MIN_TRADES=30; DSR_WAIVER=3.0

def new_verdict(r):
    if r.get("error"): return "ERROR", ["error"]
    dsr=float(r.get("dsr",0)); n=int(r.get("n_oos_trades",0)); pf=float(r.get("oos_profit_factor",0))
    max_dd=float(abs(r.get("oos_max_drawdown",0))); p=float(r.get("oos_p_value",1))
    both=bool(r.get("oos_both_halves_positive",False)); mean_pnl=float(r.get("oos_mean_pnl",0))
    waiver=dsr>=DSR_WAIVER; fails=[]
    if n<MIN_TRADES: fails.append(f"n_trades({n})")
    if dsr<MIN_DSR: fails.append(f"dsr({dsr:+.3f})")
    if pf<MIN_PF: fails.append(f"pf({pf:.3f})")
    if max_dd>MAX_DD_USD: fails.append(f"max_dd(${max_dd:,.0f})")
    if p>MAX_P: fails.append(f"p({p:.2g})")
    if not both and not waiver: fails.append("both_halves")
    if mean_pnl<=0: fails.append(f"mean_pnl({mean_pnl:.5f})")
    return ("PASS" if not fails else "FAIL"), fails

def load_latest(zoo_path):
    latest={}
    with open(zoo_path,encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except: continue
            key=f"{r.get('strategy_name','?')}__{r.get('instrument','?')}"
            if key not in latest or r.get("timestamp","") > latest[key].get("timestamp",""):
                latest[key]=r
    return list(latest.values())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--zoo",default="../05_backtests/zoo.jsonl")
    ap.add_argument("--out-csv",default="../05_backtests/zoo_reevaluation.csv")
    ap.add_argument("--promoted",action="store_true")
    ap.add_argument("--all",action="store_true")
    ap.add_argument("--strategy",default="")
    args=ap.parse_args()
    records=load_latest(Path(args.zoo))
    if args.strategy:
        records=[r for r in records if args.strategy.lower() in r.get("strategy_name","").lower()]
    rows,promoted,demoted=[],[],[]
    for r in records:
        old_v=r.get("verdict","?"); old_f=r.get("failures",[])
        if isinstance(old_f,str): old_f=[old_f]
        new_v,new_f=new_verdict(r); dsr=float(r.get("dsr",0))
        row={
            "strategy":r.get("strategy_name","?"),"instrument":r.get("instrument","?"),
            "old_verdict":old_v,"new_verdict":new_v,"dsr":round(dsr,3),
            "pf":round(float(r.get("oos_profit_factor",0)),3),
            "win_rate":round(float(r.get("oos_win_rate",0)),3),
            "max_dd_usd":round(float(abs(r.get("oos_max_drawdown",0))),2),
            "n_trades":int(r.get("n_oos_trades",0)),
            "both_halves":bool(r.get("oos_both_halves_positive",False)),
            "p_value":float(r.get("oos_p_value",1)),
            "dsr_waiver":dsr>=DSR_WAIVER,
            "old_failures":"|".join(old_f),
            "new_failures":"|".join(new_f),
            "changed":old_v!=new_v,
        }
        rows.append(row)
        if old_v=="FAIL" and new_v=="PASS": promoted.append(row)
        elif old_v=="PASS" and new_v=="FAIL": demoted.append(row)
    df=pd.DataFrame(rows).sort_values(["new_verdict","dsr"],ascending=[True,False])
    print("="*100)
    print(f"ZOO REEVALUATION — {len(df)} entries")
    print(f"Criteria: DSR>={MIN_DSR} | PF>={MIN_PF} | DD<=${MAX_DD_USD:,.0f} | p<={MAX_P} | both_halves (waived DSR>={DSR_WAIVER}) | mean_pnl>0")
    print("REMOVED: win_rate threshold")
    print("="*100)
    if args.all or args.promoted:
        show=df[df["changed"]] if args.promoted else df
        print(show[["strategy","instrument","old_verdict","new_verdict","dsr","pf","win_rate","max_dd_usd","n_trades","new_failures"]].to_string(index=False,max_colwidth=30))
    print(f"\nPROMOTED (FAIL->PASS): {len(promoted)}")
    for r in promoted:
        waiver=" [DSR-waiver]" if r["dsr_waiver"] and not r["both_halves"] else ""
        print(f"  + {r['strategy']:<25} {r['instrument']:<6} DSR={r['dsr']:+.3f} PF={r['pf']:.3f} WR={r['win_rate']:.1%} DD=${r['max_dd_usd']:,.0f} n={r['n_trades']} was_failing=[{r['old_failures']}]{waiver}")
    if demoted:
        print(f"\nDEMOTED (PASS->FAIL): {len(demoted)}")
        for r in demoted:
            print(f"  - {r['strategy']:<25} {r['instrument']:<6} new_failures=[{r['new_failures']}]")
    survivors=df[df["new_verdict"]=="PASS"].sort_values("dsr",ascending=False)
    print(f"\nTOTAL SURVIVORS – NEW CRITERIA: {len(survivors)}")
    for _,r in survivors.iterrows():
        tag=" (NEW)" if r["old_verdict"]=="FAIL" else ""
        regime=" [regime-dependent]" if r["dsr_waiver"] and not r["both_halves"] else ""
        print(f"  * {r['strategy']:<25} {r['instrument']:<6} DSR={r['dsr']:+.3f} PF={r['pf']:.3f} WR={r['win_rate']:.1%} DD=${r['max_dd_usd']:,.0f} n={r['n_trades']}{tag}{regime}")
    Path(args.out_csv).parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(args.out_csv,index=False)
    print(f"\nCSV saved -> {args.out_csv}")
    print("="*100)

if __name__=="__main__":
    main()
