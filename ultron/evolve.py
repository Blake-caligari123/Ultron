"""Phase-1 research run: island evolution on TRAIN, survivors gated on VALID + walk-forward + plateau,
allocator, sealed HOLDOUT evaluated once, random-genome noise floor, walk-forward test of the whole process."""
import json, time, uuid, sys, os
import numpy as np, pandas as pd
from .market import load
from .engine import (Ind, random_genome, mutate, crossover, gkey, run, metrics, describe, FAMILIES, COMMON, FAM)
from .db import connect, ROOT
from .prop import challenge_stats, FIRMS

def _s(d): return int(pd.Timestamp(d, tz='UTC').timestamp())

def chal(r, g, w, firm='FTMO'):
    return challenge_stats(r, g['risk'], _s(w[0]), _s(w[1]), firm)

TRAIN = ("2023-11-01", "2025-05-01"); VALID = ("2025-05-01", "2025-11-01"); HOLD = ("2025-11-01", "2026-11-04")
CFG = dict(islands=4, pop=120, gens=25, chal_weight=0.0, elite=0.10, kill=0.5, immigrants=0.10, min_train_trades=60,
           valid_gate=dict(n=25, pf=1.15, net_r=0, t=1.0, dd=-15.0), wf_pos_frac=0.6, plateau_frac=0.5,
           max_survivors=10, corr_max=0.7, alloc_cap=0.25)
KEY = "Ra"   # decisions (fitness, gates, WF, allocator) use drift-adjusted R; reports show both
REASON = {0: "stop", 1: "target", 2: "time", 3: "flip", 4: "window_end"}

class Ctx:
    def __init__(self, sym):
        self.sym = sym; self.mk = load(sym); self.inds = {k: Ind(self.mk.tf[k]) for k in self.mk.tf}; self.cache = {}
    def bt(self, g, w):
        k = (gkey(g), w)
        if k not in self.cache:
            if len(self.cache) > 2500: self.cache.clear()
            self.cache[k] = run(g, self.mk, self.inds, *w)
        return self.cache[k]

def year_split_fitness(r, w, min_n, folds=None):
    R = r[KEY]; n = len(R)
    if n < min_n: P = CFG.get("low_n_penalty", 5.0); return -P + P * n / min_n, {}
    ets = pd.to_datetime(r["ts"][r["ei"]], unit="s")
    if folds is None: folds = ets.year
    ts_ = []
    for y in np.unique(folds):
        Ry = R[folds == y]
        if len(Ry) < 5: ts_.append(-1.0); continue
        sd = max(Ry.std(), 0.5)          # floor: a handful of lucky same-size wins can't look like certainty
        ts_.append(min(Ry.mean() / sd * np.sqrt(len(Ry)), 3.0))   # cap per-fold t so one fold can't dominate
    ts_ = np.array(ts_)
    cum = np.cumsum(R); dd = (cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min()
    fit = ts_.mean() - 0.5 * ts_.std() + 0.002 * dd
    return float(fit), metrics(R)

def fitness(ctx, g, w=TRAIN, min_n=None, half_years=True):
    """t-stat consistency across half-year (or quarter) folds on drift-adjusted R
    + prop-challenge score on raw P&L: chal_weight * (FTMO pass rate - breach rate) over rolling simulated attempts."""
    r = ctx.bt(g, w)
    folds = None
    if len(r["R"]):
        e = pd.to_datetime(r["ts"][r["ei"]], unit="s")
        folds = (e.year * 4 + (e.month - 1) // 3).values if not half_years else (e.year * 2 + (e.month > 6)).values
    fit, m = year_split_fitness(r, w, min_n or CFG["min_train_trades"], folds)
    if m and CFG["chal_weight"]:     # prop-challenge term disabled (chal_weight 0) at the user's request
        c = chal(r, g, w); m["chal"] = c
        fit += CFG["chal_weight"] * (c["pass_rate"] - c["breach_rate"])
    return fit, m

def evolve_island(ctx, rng, pop_n, gens, w, min_n, on_gen=None, half_years=True):
    pop = [random_genome(rng) for _ in range(pop_n)]
    meta = [dict(origin="random", pa=None, pb=None, born=0) for _ in pop]
    seen = {}
    def score(g):
        k = gkey(g)
        if k not in seen: seen[k] = fitness(ctx, g, w, min_n, half_years)
        return seen[k][0]
    for gen in range(gens):
        fits = np.array([score(g) for g in pop])
        order = np.argsort(-fits)
        n_el = max(2, int(CFG["elite"] * pop_n)); n_keep = int((1 - CFG["kill"]) * pop_n)
        keep = order[:n_keep]; killed = order[n_keep:]
        if on_gen: on_gen(gen, pop, meta, fits, keep, killed)
        if gen == gens - 1: break
        parents = [pop[i] for i in keep]; pf = fits[keep]
        newpop = [pop[i] for i in order[:n_el]]; newmeta = [meta[i] for i in order[:n_el]]
        survivors_rest = [i for i in keep[n_el:]]
        newpop += [pop[i] for i in survivors_rest]; newmeta += [meta[i] for i in survivors_rest]
        n_imm = int(CFG["immigrants"] * pop_n)
        while len(newpop) < pop_n - n_imm:
            i1, i2 = [max(rng.choice(len(parents), 3), key=lambda j: pf[j]) for _ in range(2)]
            child = mutate(crossover(parents[i1], parents[i2], rng), rng)
            if gkey(child) in seen and rng.random() < 0.8: child = mutate(child, rng, rate=0.5)
            newpop.append(child); newmeta.append(dict(origin="child", pa=int(keep[i1]), pb=int(keep[i2]), born=gen + 1, pa_g=parents[i1], pb_g=parents[i2]))
        while len(newpop) < pop_n:
            newpop.append(random_genome(rng)); newmeta.append(dict(origin="immigrant", pa=None, pb=None, born=gen + 1))
        pop, meta = newpop, newmeta
    return pop, seen

def plateau_ok(ctx, g, base_fit, rng, w=TRAIN, min_n=None, half_years=True, k=8):
    fs = []
    for _ in range(k):
        m = dict(g)
        for key, (lo, hi, isint) in {**COMMON, **FAM[g["fam"]]}.items():
            if key in ("tf", "trend", "side", "flip", "ss", "slen") or (g["fam"] >= 8 and key == "p1") or rng.random() > 0.4: continue
            v = m[key] * (1 + rng.uniform(-0.12, 0.12))
            m[key] = int(round(min(max(v, lo), hi))) if isint else round(float(min(max(v, lo), hi)), 3)
        fs.append(fitness(ctx, m, w, min_n, half_years)[0])
    med = float(np.median(fs))
    return med > 0 and med >= CFG["plateau_frac"] * base_fit, med

def valid_gate(m, gate=None):
    gate = gate or CFG["valid_gate"]
    return m["n"] >= gate["n"] and m["pf"] >= gate["pf"] and m["net_r"] > gate["net_r"] and m["t"] >= gate["t"] and m["max_dd_pct"] >= gate["dd"]

def daily_r(r, start, end, key="R"):
    day = r["ts"][r["xi"]] // 86400
    d0 = int(pd.Timestamp(start, tz="UTC").timestamp() // 86400); d1 = int(pd.Timestamp(end, tz="UTC").timestamp() // 86400)
    out = np.zeros(d1 - d0)
    np.add.at(out, np.clip(day - d0, 0, d1 - d0 - 1), r[key])
    return pd.Series(out, index=pd.date_range(start, periods=d1 - d0, freq="D"))

def wf_windows(start="2023-11-01", end="2025-11-01"):
    edges = pd.date_range(start, end, freq="3MS")
    return [(str(a.date()), str(b.date())) for a, b in zip(edges[:-1], edges[1:])]

def wf_consistency(ctx, g):
    pos = tot = 0
    for w in wf_windows():
        R = ctx.bt(g, w)[KEY]
        if len(R) >= 3: tot += 1; pos += R.sum() > 0
    return (pos / tot if tot else 0.0), tot

def allocate(rows, cap):
    w = np.array([max(r["valid_adj"]["t"], 0) / max(r["vol"], 1e-6) for r in rows])
    if w.sum() == 0: return np.zeros(len(rows))
    w = w / w.sum()
    for _ in range(20):
        over = w > cap
        if not over.any(): break
        ex = (w[over] - cap).sum(); w[over] = cap; w[~over] += ex * w[~over] / w[~over].sum()
    return w

def portfolio(rows, weights, win, budget_pct, key="R"):
    s = None
    for r, w in zip(rows, weights):
        d = daily_r(r["bt"][win], *WIN[win], key) * w * budget_pct / 100
        s = d if s is None else s.add(d, fill_value=0)
    eq = (1 + s).cumprod(); dd = (eq / eq.cummax() - 1).min() * 100
    yrs = len(s) / 365.25
    return dict(ret_pct=round(float((eq.iloc[-1] - 1) * 100), 2), cagr_pct=round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 2),
                max_dd_pct=round(float(dd), 2), sharpe=round(float(s.mean() / s.std() * np.sqrt(365)) if s.std() > 0 else 0, 2), days=len(s)), eq

WIN = {"train": TRAIN, "valid": VALID, "holdout": HOLD}

def research(sym, db, run_id, rng):
    ctx = Ctx(sym); t0 = time.time()
    bot_ids = {}  # gkey -> bot_id
    cur = db.cursor()
    def bot_id(g, island, born, origin, pa=None, pb=None):
        k = gkey(g)
        if k in bot_ids: return bot_ids[k]
        cur.execute("INSERT INTO bots(run_id,symbol,island,born_gen,parent_a,parent_b,origin,family,genome,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (run_id, sym, island, born, pa, pb, origin, FAMILIES[g["fam"]], json.dumps(g), "alive"))
        bot_ids[k] = cur.lastrowid; return cur.lastrowid
    allseen = {}
    for isl in range(CFG["islands"]):
        def on_gen(gen, pop, meta, fits, keep, killed, isl=isl):
            ids = []
            for g, m in zip(pop, meta):
                pa = bot_ids.get(gkey(m["pa_g"])) if m.get("pa_g") else None
                pb = bot_ids.get(gkey(m["pb_g"])) if m.get("pb_g") else None
                ids.append(bot_id(g, isl, m["born"], m["origin"], pa, pb))
            for i in killed:
                cur.execute("UPDATE bots SET died_gen=?, status='killed' WHERE bot_id=? AND status='alive'", (gen, ids[i]))
            for i in range(len(pop)):
                cur.execute("UPDATE bots SET train_fit=? WHERE bot_id=?", (float(fits[i]), ids[i]))
            born = sum(1 for m in meta if m["born"] == gen)
            cur.execute("INSERT INTO generations VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, sym, isl, gen, float(fits.max()), float(np.median(fits)), len(keep), born, len(killed), ids[int(np.argmax(fits))]))
        pop, seen = evolve_island(ctx, rng, CFG["pop"], CFG["gens"], TRAIN, CFG["min_train_trades"], on_gen)
        allseen.update(seen)
        print(f"  {sym} island {isl}: best train fit {max(v[0] for v in seen.values()):.2f}, {len(seen)} genomes, {time.time()-t0:.0f}s", flush=True)
    db.commit()
    n_tested = len(allseen)
    # candidate pool: top 250 distinct by train fitness
    cands = sorted(((v[0], dict(k)) for k, v in allseen.items()), key=lambda x: -x[0])[:250]
    rows = []; gate_log = dict(pool=len(cands), plateau=0, valid=0, wf=0, decor=0)
    for fit, g in cands:
        if fit <= 0: continue
        ok, pmed = plateau_ok(ctx, g, fit, rng)
        if not ok: continue
        gate_log["plateau"] += 1
        vm = metrics(ctx.bt(g, VALID)[KEY])
        if not valid_gate(vm): continue
        gate_log["valid"] += 1
        wf, wtot = wf_consistency(ctx, g)
        if wf < CFG["wf_pos_frac"]: continue
        gate_log["wf"] += 1
        rows.append(dict(g=g, fit=fit, plateau=pmed, wf=wf, wtot=wtot, valid_adj=vm, valid=metrics(ctx.bt(g, VALID)["R"]), train=metrics(ctx.bt(g, TRAIN)["R"]), train_adj=metrics(ctx.bt(g, TRAIN)[KEY])))
    # decorrelate on train+valid daily R
    rows.sort(key=lambda r: -(r["valid_adj"]["t"] + r["fit"]))
    chosen = []
    for r in rows:
        d = pd.concat([daily_r(ctx.bt(r["g"], TRAIN), *TRAIN, KEY), daily_r(ctx.bt(r["g"], VALID), *VALID, KEY)])
        r["daily"] = d
        if any(abs(np.corrcoef(d, c["daily"])[0, 1]) > CFG["corr_max"] for c in chosen): continue
        chosen.append(r)
        if len(chosen) >= CFG["max_survivors"]: break
    gate_log["decor"] = len(chosen)
    for r in chosen:
        r["vol"] = daily_r(ctx.bt(r["g"], VALID), *VALID, KEY).std()
        r["bt"] = {w: ctx.bt(r["g"], WIN[w]) for w in WIN}
        r["id"] = bot_id(r["g"], -1, -1, "pool")
    # ---- SEALED HOLDOUT: evaluated only now, after survivors and weights are frozen ----
    weights = allocate(chosen, CFG["alloc_cap"]) if chosen else []
    for r, w in zip(chosen, weights):
        r["weight"] = float(w); r["holdout"] = metrics(r["bt"]["holdout"]["R"]); r["holdout_adj"] = metrics(r["bt"]["holdout"][KEY])
        r["challenge"] = {f: {wn: chal(r["bt"][wn], r["g"], WIN[wn], f) for wn in WIN} for f in FIRMS}
        cur.execute("UPDATE bots SET status='survivor', train=? WHERE bot_id=?", (json.dumps(dict(plateau=r["plateau"], wf=r["wf"])), r["id"]))
        cur.execute("INSERT INTO allocations VALUES(?,?,?,?,?,?)", (run_id, time.strftime("%Y-%m-%dT%H:%M:%S%z"), r["id"], sym, r["weight"], "valid t-stat / valid daily vol, cap 25%"))
        for wname in WIN:
            m = metrics(r["bt"][wname]["R"])
            cur.execute("INSERT INTO evals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (r["id"], run_id, wname, *WIN[wname], m["n"], m["net_r"], m["ret_pct"], m["max_dd_pct"], m["pf"], m["win"], m["exp_r"], m["t"]))
            b = r["bt"][wname]; ts = b["ts"]
            cur.executemany("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(r["id"], run_id, wname, sym, int(ts[e]), int(ts[x]), int(d), float(ep), float(xp), float(R), REASON[int(rs)])
                            for e, x, d, ep, xp, R, rs in zip(b["ei"], b["xi"], b["dir"], b["ep"], b["xp"], b["R"], b["reason"])])
    port = {}
    if chosen:
        for wname in WIN:
            for bud in (2, 5):
                port[f"{wname}_budget{bud}pct"] = portfolio(chosen, weights, wname, bud)[0]
                port[f"{wname}_budget{bud}pct_driftadj"] = portfolio(chosen, weights, wname, bud, KEY)[0]
    # ---- noise floor: random genomes through the same VALID gate, then their holdout ----
    nr = []; 
    for _ in range(600):
        g = random_genome(rng); vm = metrics(ctx.bt(g, VALID)[KEY])
        if valid_gate(vm):
            m = metrics(ctx.bt(g, HOLD)[KEY]); m["chal"] = chal(ctx.bt(g, HOLD), g, HOLD); nr.append(m)
    noise = dict(random_tested=600, pass_valid_gate=len(nr),
                 note="drift-adjusted R", their_holdout_median_pf=float(np.median([m["pf"] for m in nr])) if nr else None,
                 their_holdout_frac_profitable=float(np.mean([m["net_r"] > 0 for m in nr])) if nr else None,
                 their_holdout_ftmo_pass_rate=float(np.mean([m["chal"]["pass_rate"] for m in nr])) if nr else None,
                 their_holdout_ftmo_breach_rate=float(np.mean([m["chal"]["breach_rate"] for m in nr])) if nr else None)
    db.commit()
    return dict(symbol=sym, genomes_tested=n_tested, gates=gate_log, noise=noise, portfolio=port,
                survivors=[dict(bot_id=r["id"], desc=describe(r["g"]), genome=r["g"], weight=round(r["weight"], 3), train_fit=round(r["fit"], 2),
                                wf_pos=round(r["wf"], 2), challenge=r["challenge"], train=r["train"], valid=r["valid"], holdout=r["holdout"], adj_train=r["train_adj"], adj_valid=r["valid_adj"], adj_holdout=r["holdout_adj"]) for r in chosen],
                seconds=round(time.time() - t0))

def walk_forward_process(sym, rng, pop=80, gens=15):
    """Re-run the whole machine on rolling windows: evolve on 9m, gate on next 3m (inner valid), trade the following 3m blind."""
    ctx = Ctx(sym); out = []; allR = []
    starts = pd.date_range("2023-11-01", "2025-08-01", freq="3MS")
    for s in starts:
        tr = (str(s.date()), str((s + pd.DateOffset(months=9)).date()))
        iv = (tr[1], str((s + pd.DateOffset(months=12)).date()))
        te = (iv[1], str((s + pd.DateOffset(months=15)).date()))
        if pd.Timestamp(te[0]) >= pd.Timestamp("2026-11-01"): break
        _, seen = evolve_island(ctx, rng, pop, gens, tr, 40, half_years=False)
        cands = sorted(((v[0], dict(k)) for k, v in seen.items()), key=lambda x: -x[0])[:60]
        picks = []
        for fit, g in cands:
            if fit <= 0: continue
            vm = metrics(ctx.bt(g, iv)[KEY])
            if vm["n"] >= 10 and vm["pf"] >= 1.15 and vm["net_r"] > 0:
                picks.append(g)
            if len(picks) >= 5: break
        Rt = np.concatenate([ctx.bt(g, te)["R"] for g in picks]) if picks else np.array([])
        Ra = np.concatenate([ctx.bt(g, te)[KEY] for g in picks]) if picks else np.array([])
        ch = [challenge_stats(ctx.bt(g, te), g["risk"], _s(te[0]) - 0, _s(te[1]), "FTMO", step_days=2, cap_days=60) for g in picks]
        m = metrics(Rt); m.update(test=te, picks=len(picks), adj_net_r=metrics(Ra)["net_r"], adj_pf=metrics(Ra)["pf"],
                                  ftmo_pass_rate=round(float(np.mean([c["pass_rate"] for c in ch])), 3) if ch else None,
                                  ftmo_breach_rate=round(float(np.mean([c["breach_rate"] for c in ch])), 3) if ch else None)
        out.append(m); allR.append((Rt, Ra))
        print(f"  WF {sym} test {te[0]}..{te[1]}: picks {len(picks)} n {m['n']} netR {m['net_r']} pf {m['pf']} adjR {m['adj_net_r']} ftmo pass {m['ftmo_pass_rate']} breach {m['ftmo_breach_rate']}", flush=True)
    R = np.concatenate([x[0] for x in allR]); Ra = np.concatenate([x[1] for x in allR])
    pr = [f["ftmo_pass_rate"] for f in out if f["ftmo_pass_rate"] is not None]; br = [f["ftmo_breach_rate"] for f in out if f["ftmo_breach_rate"] is not None]
    return dict(folds=out, stitched=metrics(R), stitched_driftadj=metrics(Ra), ftmo_pass_rate_mean=round(float(np.mean(pr)), 3) if pr else None,
                ftmo_breach_rate_mean=round(float(np.mean(br)), 3) if br else None,
                stitched_note="equal-weight trades of up-to-5 picks per fold, 1% risk each, sequential; FTMO attempts start every 2 days in each 3-month test, 60-day horizon")

if __name__ == "__main__":
    os.nice(15)
    rng = np.random.default_rng(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
    db = connect(); run_id = time.strftime("p1-%Y%m%d-%H%M%S")
    prov = json.load(open(os.path.join(ROOT, "data", "provenance.json")))
    from .costs import COSTS
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run_id, time.strftime("%Y-%m-%d %H:%M %Z"), "phase1_research", json.dumps(dict(FIRMS=FIRMS, TFS="1m/5m/15m", CFG=CFG, TRAIN=TRAIN, VALID=VALID, HOLD=HOLD, COSTS=COSTS)), json.dumps(prov), None)); db.commit()
    bh = {}
    for sym in ["XAUUSD", "BTCUSD"]:
        c = load(sym).tf[2]; idx = c["idx"]
        bh[sym] = {w: round(float(c["c"][min(idx.searchsorted(pd.Timestamp(b, tz='UTC')), len(idx)-1)] / c["c"][idx.searchsorted(pd.Timestamp(a, tz='UTC'))] - 1) * 100, 1) for w, (a, b) in WIN.items()}
    summary = dict(buy_and_hold_pct=bh, run_id=run_id, windows=WIN, costs=COSTS, provenance=prov, results={}, walk_forward={})
    for sym in ["XAUUSD", "BTCUSD"]:
        print("research", sym, flush=True)
        summary["results"][sym] = research(sym, db, run_id, rng)
        print(json.dumps(summary["results"][sym]["gates"]), json.dumps(summary["results"][sym]["noise"]), flush=True)
    for sym in ["XAUUSD", "BTCUSD"]:
        summary["walk_forward"][sym] = walk_forward_process(sym, rng)
    db.execute("UPDATE runs SET summary=? WHERE run_id=?", (json.dumps(summary, default=str), run_id)); db.commit()
    path = os.path.join(ROOT, "results", f"{run_id}.json"); json.dump(summary, open(path, "w"), indent=1, default=str)
    json.dump(summary, open(os.path.join(ROOT, "results", "latest.json"), "w"), indent=1, default=str)
    print("saved", path)
