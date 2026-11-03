import json,sys; s=json.load(open(sys.argv[1] if len(sys.argv)>1 else "results/latest.json"))
print(s["buy_and_hold_pct"])
for sym,r in s["results"].items():
    print("==",sym, "tested",r["genomes_tested"], r["gates"], r["noise"]); print(json.dumps(r["portfolio"]))
    for v in r["survivors"]: print(v["weight"], v["desc"][:150]); print("   T",{k:v["train"][k] for k in ["n","net_r","pf","win","max_dd_pct"]}," V",{k:v["valid"][k] for k in ["n","net_r","pf"]}," H",{k:v["holdout"][k] for k in ["n","net_r","ret_pct","pf","win","max_dd_pct"]}, {k:v[k] for k in v if k.startswith("adj")})
for sym,w in s["walk_forward"].items(): print(sym,"WF stitched",w["stitched"])
