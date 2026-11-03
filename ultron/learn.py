"""Daily online-learning tick for the live PAPER floor (runs as a separate low-priority process).
1) kill: live bots breaching live performance rules die. (Manual tool; the always-on loop is ultron.breeder.)
2) fitness: blended = historical fitness + live paper t-stat (weight grows with live trade count, capped).
3) breed: children of the best live bots + random immigrants, scored on rolling TRAIN (history up to now-180d).
4) held-out gate: a child is promoted only if it passes plateau + the last-180-days held-out window (never used for fitness)
   + correlation check vs roster. Max 2 promotions/day so the held-out window isn't over-queried.
5) allocator: rebalance weights across the roster.
Usage: python -m ultron.learn [--seed]  (--seed loads the latest research run's survivors into an empty roster)"""
import json, sys, time
import numpy as np, pandas as pd
from .db import connect, rfile
from .market import Market, load_history
from .engine import random_genome, mutate, crossover, metrics, FAMILIES, describe
from . import evolve as ev

MAX_PER_SYM = 8; MAX_PROMOTE = 2; HELD_DAYS = 180
KILL = dict(min_n=10, net_r=-6.0, dd_r=-10.0, pf_n=20, pf=0.8)
CAP = 0.25

def now_s(): return time.strftime("%Y-%m-%dT%H:%M:%S%z")

class LiveCtx(ev.Ctx):
    """Backtest context on the full history plus the live floor's recent 1m bars."""
    def __init__(self, sym):
        super().__init__(sym, Market(sym, load_history(sym)))

def seed(db):
    run_id = db.execute("SELECT run_id FROM runs WHERE kind='phase1_research' AND summary IS NOT NULL ORDER BY started DESC LIMIT 1").fetchone()[0]
    rows = db.execute("SELECT b.bot_id,b.symbol,b.genome,b.family,a.weight FROM bots b JOIN allocations a ON a.bot_id=b.bot_id AND a.run_id=b.run_id WHERE b.run_id=? AND b.status='survivor'", (run_id,)).fetchall()
    for bid, sym, g, fam, w in rows:
        db.execute("INSERT OR REPLACE INTO live_bots(bot_id,symbol,genome,family,status,weight,added_ts,origin) VALUES(?,?,?,?,?,?,?,?)", (bid, sym, g, fam, "live", w, now_s(), f"survivor:{run_id}"))
        db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), "birth", bid, json.dumps(dict(sym=sym, family=fam, origin="backtest survivor", run=run_id))))
    db.commit(); return len(rows)

def live_stats(db, bid):
    R = np.array([r[0] for r in db.execute("SELECT r FROM live_trades WHERE bot_id=? ORDER BY exit_ts", (bid,))])
    return R

def tick(db, rng):
    t_end = pd.Timestamp.now("UTC").tz_localize(None).normalize() + pd.Timedelta(days=1)
    held = (str((t_end - pd.Timedelta(days=HELD_DAYS)).date()), str(t_end.date()))
    train = ("2023-11-01", held[0])
    # live generations continue after the backtest's last generation: one generation per live learning day
    first = db.execute("SELECT MIN(added_ts) FROM live_bots").fetchone()[0]
    d0 = pd.Timestamp(first[:10]) if first else pd.Timestamp.now("UTC").tz_localize(None).normalize()
    last_bt = db.execute("SELECT MAX(gen) FROM generations").fetchone()[0] or 0
    day_gen = int(last_bt) + 1 + max(0, (pd.Timestamp.now("UTC").tz_localize(None).normalize() - d0.normalize()).days)
    roster = db.execute("SELECT bot_id,symbol,genome,status,live_n,live_r,live_dd_r FROM live_bots WHERE status IN ('live','probation')").fetchall()
    # 1) kills
    for bid, sym, g, st, n, r, dd in roster:
        R = live_stats(db, bid); m = metrics(R)
        why = None
        if n >= KILL["min_n"] and r <= KILL["net_r"]: why = f"live net {r:.1f}R"
        elif dd <= KILL["dd_r"]: why = f"live drawdown {dd:.1f}R"
        elif n >= KILL["pf_n"] and m["pf"] < KILL["pf"]: why = f"live PF {m['pf']}"
        if why:
            db.execute("UPDATE live_bots SET status='killed', removed_ts=?, reason=?, weight=0 WHERE bot_id=?", (now_s(), why, bid))
            db.execute("UPDATE bots SET status='killed', died_gen=? WHERE bot_id=?", (day_gen, bid))
            db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), "kill", bid, json.dumps(dict(sym=sym, reason=why))))
        elif st == "probation" and n >= 10:
            db.execute("UPDATE live_bots SET status='live' WHERE bot_id=?", (bid,))
    db.commit()
    roster = db.execute("SELECT bot_id,symbol,genome,status,live_n FROM live_bots WHERE status IN ('live','probation')").fetchall()
    promoted = 0; scores = {}
    for sym in ("XAUUSD", "BTCUSD"):
        ctx = LiveCtx(sym); promoted = 0   # promotion budget is per symbol
        mine = [(bid, json.loads(g), st, n) for bid, s, g, st, n in roster if s == sym]
        # 2) blended fitness
        for bid, g, st, n in mine:
            hist = ev.fitness(ctx, g, train)[0]
            hm = metrics(ctx.bt(g, held)[ev.KEY]); R = live_stats(db, bid); lm = metrics(R)
            live_w = min(1.0, len(R) / 30)
            scores[bid] = dict(sym=sym, g=g, blended=0.5 * hist + 0.5 * (1 - live_w) * hm["t"] + live_w * lm["t"], held=hm, live=lm,
                               vol=max(ev.daily_r(ctx.bt(g, held), *held, ev.KEY).std(), 1e-6), probation=st == "probation")
        # 3) breed
        parents = sorted([s["g"] for b, s in scores.items() if s["sym"] == sym], key=lambda g: -1)
        if not parents: parents = [random_genome(rng) for _ in range(4)]
        kids = []
        for _ in range(60):
            a, b = parents[rng.integers(len(parents))], parents[rng.integers(len(parents))]
            kids.append(mutate(crossover(a, b, rng), rng))
        kids += [random_genome(rng) for _ in range(30)]
        scored = [(ev.fitness(ctx, k, train)[0], k) for k in kids]
        if len(mine) < 3:   # thin roster: run a small full evolution (incubator) on the rolling train window
            _, seen = ev.evolve_island(ctx, rng, 120, 15, train, ev.CFG["min_train_trades"])
            scored += [(v[0], dict(k)) for k, v in seen.items()]
        scored = sorted(scored, key=lambda x: -x[0])
        n_live = len(mine)
        for fit, k in scored[:60]:
            if promoted >= MAX_PROMOTE or fit <= 0: break
            ok, _ = ev.plateau_ok(ctx, k, fit, rng, train)
            if not ok: continue
            hm = metrics(ctx.bt(k, held)[ev.KEY])
            if not (hm["n"] >= 15 and hm["pf"] >= 1.15 and hm["net_r"] > 0 and hm["t"] >= 1.0): continue
            dk = ev.daily_r(ctx.bt(k, train), *train, ev.KEY)
            if any(abs(np.nan_to_num(np.corrcoef(dk, ev.daily_r(ctx.bt(s["g"], train), *train, ev.KEY))[0, 1])) > 0.7 for s in scores.values() if s["sym"] == sym): continue
            if n_live >= MAX_PER_SYM:
                worst = min((b for b, s in scores.items() if s["sym"] == sym), key=lambda b: scores[b]["blended"])
                if scores[worst]["blended"] >= 0.5 * fit + 0.5 * hm["t"]: continue
                db.execute("UPDATE live_bots SET status='killed', removed_ts=?, reason='replaced by stronger child', weight=0 WHERE bot_id=?", (now_s(), worst))
                db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), "kill", worst, json.dumps(dict(sym=sym, reason="replaced by stronger child"))))
                scores.pop(worst); n_live -= 1
            cur = db.execute("INSERT INTO bots(run_id,symbol,island,born_gen,origin,family,genome,train_fit,status) VALUES(?,?,?,?,?,?,?,?,?)",
                             (f"live-{held[1]}", sym, 99, day_gen, "live-child", FAMILIES[k["fam"]], json.dumps(k), fit, "alive"))
            bid = cur.lastrowid
            db.execute("INSERT INTO live_bots(bot_id,symbol,genome,family,status,weight,added_ts,origin) VALUES(?,?,?,?,?,?,?,?)", (bid, sym, json.dumps(k), FAMILIES[k["fam"]], "probation", 0, now_s(), "live-child"))
            db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), "birth", bid, json.dumps(dict(sym=sym, family=FAMILIES[k["fam"]], desc=describe(k), held=hm))))
            scores[bid] = dict(sym=sym, g=k, blended=0.5 * fit + 0.5 * hm["t"], held=hm, live=metrics(np.array([])), vol=max(ev.daily_r(ctx.bt(k, held), *held, ev.KEY).std(), 1e-6), probation=True)
            promoted += 1; n_live += 1
    # 5) allocator
    ids = list(scores)
    raw = np.array([max(scores[b]["blended"], 0) / scores[b]["vol"] * (0.5 if scores[b]["probation"] else 1) for b in ids])
    w = raw / raw.sum() if raw.sum() > 0 else np.ones(len(ids)) / max(len(ids), 1)
    for _ in range(20):
        over = w > CAP
        if not over.any() or (~over).sum() == 0: break
        ex = (w[over] - CAP).sum(); w[over] = CAP; w[~over] += ex * w[~over] / w[~over].sum()
    for b, x in zip(ids, w):
        db.execute("UPDATE live_bots SET weight=? WHERE bot_id=?", (float(x), b))
        db.execute("INSERT INTO allocations VALUES(?,?,?,?,?,?)", ("live", now_s(), b, scores[b]["sym"], float(x), "blended (hist+held+live t) / held vol, cap 25%, probation x0.5"))
    db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), "rebalance", None, json.dumps({str(b): round(float(x), 3) for b, x in zip(ids, w)})))
    db.commit()
    open(rfile("roster_changed"), "w").write(now_s())
    print(now_s(), "learn tick: roster", len(ids), "promoted", promoted, flush=True)

if __name__ == "__main__":
    db = connect(); rng = np.random.default_rng(int(time.time()))
    if "--seed" in sys.argv and db.execute("SELECT COUNT(*) FROM live_bots WHERE status IN ('live','probation')").fetchone()[0] == 0:
        print("seeded", seed(db))
    if "--no-tick" not in sys.argv:
        tick(db, rng)
