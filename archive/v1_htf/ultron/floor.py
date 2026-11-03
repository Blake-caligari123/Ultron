"""ULTRON floor view API (read-only). Serves web/ and JSON from results/ultron.db. Port 8795."""
import json, os, sqlite3, time
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .db import PATH, ROOT
from .costs import COSTS

app = FastAPI(title="ULTRON floor", docs_url=None, redoc_url=None)
WEB = os.path.join(ROOT, "web")

def q(sql, args=()):
    c = sqlite3.connect(f"file:{PATH}?mode=ro", uri=True, timeout=10); c.row_factory = sqlite3.Row
    try: return [dict(r) for r in c.execute(sql, args)]
    finally: c.close()

def latest_run():
    r = q("SELECT run_id, summary FROM runs WHERE kind='phase1_research' AND summary IS NOT NULL ORDER BY started DESC LIMIT 1")
    return r[0] if r else None

_replay_cache = {}
@app.get("/api/replay")
def replay():
    run = latest_run()
    if not run: return {"run_id": None}
    rid = run["run_id"]
    if rid in _replay_cache: return _replay_cache[rid]
    summ = json.loads(run["summary"])
    out = {"run_id": rid, "windows": summ["windows"], "buy_and_hold_pct": summ.get("buy_and_hold_pct"), "symbols": {}}
    for sym in ("XAUUSD", "BTCUSD"):
        gens = q("SELECT island, gen, best, median, alive, born, killed, best_bot FROM generations WHERE run_id=? AND symbol=? ORDER BY island, gen", (rid, sym))
        bots = q("SELECT bot_id, island, born_gen, died_gen, family, train_fit, status, parent_a, parent_b FROM bots WHERE run_id=? AND symbol=? AND island>=0", (rid, sym))
        # per island/gen: top 12 alive bots by train fitness (floor desks)
        frames = []
        by_isl = {}
        for b in bots: by_isl.setdefault(b["island"], []).append(b)
        maxgen = max((g["gen"] for g in gens), default=0)
        for isl in sorted(by_isl):
            for gen in range(maxgen + 1):
                alive = [b for b in by_isl[isl] if b["born_gen"] <= gen and (b["died_gen"] is None or b["died_gen"] >= gen) and b["train_fit"] is not None]
                alive.sort(key=lambda b: -b["train_fit"])
                frames.append(dict(island=isl, gen=gen, desks=[dict(id=b["bot_id"], fam=b["family"], fit=round(b["train_fit"], 2), born=b["born_gen"], dies=b["died_gen"] if b["died_gen"] == gen else None) for b in alive[:12]]))
        surv = q("SELECT b.bot_id, b.family, b.genome, a.weight FROM bots b JOIN allocations a ON a.bot_id=b.bot_id AND a.run_id=b.run_id WHERE b.run_id=? AND b.symbol=? AND b.status='survivor'", (rid, sym))
        curves = {}
        for s in surv:
            tr = q("SELECT window, exit_ts, r FROM trades WHERE bot_id=? AND run_id=? ORDER BY exit_ts", (s["bot_id"], rid))
            cum = 0.0; pts = []
            for t in tr: cum += t["r"]; pts.append([t["exit_ts"], round(cum, 2), t["window"][0]])
            curves[s["bot_id"]] = pts
        res = summ["results"][sym]
        out["symbols"][sym] = dict(generations=gens, frames=frames, survivors=[dict(id=s["bot_id"], fam=s["family"], weight=s["weight"]) for s in surv],
                                   curves=curves, gates=res["gates"], noise=res["noise"], tested=res["genomes_tested"],
                                   survivor_metrics=res["survivors"], walk_forward=summ.get("walk_forward", {}).get(sym, {}).get("stitched"),
                                   walk_forward_adj=summ.get("walk_forward", {}).get(sym, {}).get("stitched_driftadj"))
    _replay_cache.clear(); _replay_cache[rid] = out
    return out

@app.get("/api/state")
def state():
    ticks = {t["symbol"]: t for t in q("SELECT * FROM live_ticks")}
    bots = q("SELECT bot_id, symbol, family, status, weight, live_n, live_r, live_dd_r, added_ts, removed_ts, reason, origin, genome FROM live_bots ORDER BY bot_id")
    gens = {b["bot_id"]: b["born_gen"] for b in q("SELECT bot_id, born_gen FROM bots")}
    pos = {p["bot_id"]: p for p in q("SELECT * FROM live_positions")}
    for b in bots:
        g = json.loads(b.pop("genome")); b["tf"] = ["15m", "1h"][g["tf"]]; b["gen"] = gens.get(b["bot_id"])
        p = pos.get(b["bot_id"])
        if p:
            t = ticks.get(b["symbol"]); u = None
            if t:
                exitpx = t["bid"] if p["dir"] == 1 else t["ask"]
                u = round(p["dir"] * (exitpx - p["entry"]) / p["risk"], 3)
            b["position"] = dict(dir=p["dir"], entry=p["entry"], stop=p["stop"], tgt=p["tgt"], since=p["entry_ts"], unreal_r=u)
    trades = q("SELECT bot_id, symbol, entry_ts, exit_ts, dir, entry, exit, r, reason, weight FROM live_trades ORDER BY exit_ts")
    eq = {"XAUUSD": [], "BTCUSD": []}; bal = {"XAUUSD": 10000.0, "BTCUSD": 10000.0}
    for t in trades:
        bal[t["symbol"]] *= 1 + (t["weight"] or 0) * 0.05 * t["r"]
        eq[t["symbol"]].append([t["exit_ts"], round(bal[t["symbol"]], 2)])
    events = q("SELECT * FROM live_events WHERE kind!='heartbeat' ORDER BY rowid DESC LIMIT 60")
    hb = q("SELECT ts FROM live_events ORDER BY rowid DESC LIMIT 1")
    allocs = q("SELECT ts, bot_id, symbol, weight FROM allocations WHERE run_id='live' ORDER BY ts DESC LIMIT 80")
    return JSONResponse(dict(now=int(time.time()), ticks=ticks, bots=bots, trades=trades[-200:], equity=eq, events=events, last_event=hb[0]["ts"] if hb else None,
                             allocations=allocs, paper=dict(start_equity_per_symbol=10000, risk_budget_pct=5.0, note="PAPER only. No broker, no orders.")))

@app.get("/")
def index(): return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-store"})

app.mount("/static", StaticFiles(directory=WEB), name="static")
