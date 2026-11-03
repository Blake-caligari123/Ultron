"""ULTRON floor view API (read-only). Serves web/ and JSON from results/ultron.db. Port 8795."""
import json, os, sqlite3, time
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .db import PATH, ROOT, rfile, connect
import threading
from .costs import COSTS
from .engine import TF_NAME, indicators_text, rr as rr_of, signature
from . import plain
from .risk import trade_usd, load_cfg
from . import learned as learned_mod
import pandas as pd
BUDGET_PCT = 5.0; START = 10000.0

app = FastAPI(title="ULTRON floor", docs_url=None, redoc_url=None)
WEB = os.path.join(ROOT, "web")

_migrated = set()
_cur = threading.local()          # which floor this request reads ("main" or "scalp"); sync handlers run in one worker thread
def fl(): return getattr(_cur, "floor", "main")
def set_floor(floor):
    _cur.floor = "scalp" if floor == "scalp" else "main"
    p = rfile("ultron.db", fl())
    if p not in _migrated: connect(p).close(); _migrated.add(p)     # schema + migrations (creates the empty scalp DB on first view)
    return p

def q(sql, args=()):
    c = sqlite3.connect(f"file:{rfile('ultron.db', fl())}?mode=ro", uri=True, timeout=10); c.row_factory = sqlite3.Row; c.execute("PRAGMA busy_timeout=10000")
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

def risk_view():
    try:
        r = json.load(open(rfile("risk_state.json", fl())))
    except Exception:
        return None
    L = r.get("limits") or load_cfg(fl())
    return dict(open_risk_pct=round(r["open_risk_pct"], 2), open_trades=r["open_trades"], equity_usd=round(r["equity"], 2),
                day_pnl_pct=round(r["day_pnl_pct"], 2), blocked_today=r.get("blocked_today", 0), halted=r.get("halted", False),
                max_total_open_risk_pct=L["max_total_open_risk_pct"], max_open_trades=L["max_open_trades"],
                max_same_direction_per_symbol=L["max_same_direction_per_symbol"], daily_floor_loss_limit_pct=L["daily_floor_loss_limit_pct"],
                max_bots_on_floor=L["max_bots_on_floor"], risk_per_trade=L["risk_per_trade"], fresh=time.time() - r.get("updated", 0) < 120)

def breeder_view():
    try:
        b = json.load(open(rfile("breeder_stats.json", fl())))
    except Exception:
        return None
    up = time.time() - b.get("updated", 0) < 180
    return dict(running=up, tested_today=b["tested_today"], promoted_today=b["promoted_today"], cut_today=b["cut_today"],
                tested_last_hour=b.get("tested_last_hour"), generation=b.get("generation"),
                guards=b.get("guards"), gate_today=b.get("gate_today"), windows=b.get("windows"),
                expected_tph=b.get("expected_trades_per_hour"), expected_tpd=b.get("expected_trades_per_day", {}),
                top_reject=b.get("top_reject"), top_reject_share=b.get("top_reject_share"), reject_last_hour=b.get("reject_last_hour"),
                reject_today=b.get("reject_today"), pool_best=b.get("pool_best"))

NOCACHE = {"Cache-Control": "no-store, max-age=0"}

@app.get("/api/state")
def state(floor: str = "main"):
    set_floor(floor)
    ticks = {t["symbol"]: t for t in q("SELECT * FROM live_ticks")}
    BV = breeder_view()
    bots = q("SELECT bot_id, symbol, family, status, weight, live_n, live_r, live_dd_r, added_ts, removed_ts, reason, origin, genome FROM live_bots WHERE status!='retired' ORDER BY bot_id")
    gens = {b["bot_id"]: b["born_gen"] for b in q("SELECT bot_id, born_gen FROM bots")}
    pos = {p["bot_id"]: p for p in q("SELECT * FROM live_positions")}
    trades = q("SELECT bot_id, symbol, entry_ts, exit_ts, dir, entry, exit, r, reason, weight, risk_usd, cost_usd FROM live_trades WHERE bot_id IN (SELECT bot_id FROM live_bots WHERE status!='retired') ORDER BY exit_ts")
    usd = {}
    for t in trades: usd[t["bot_id"]] = usd.get(t["bot_id"], 0.0) + trade_usd(t)
    counter = {}
    for b in bots:
        g = json.loads(b.pop("genome")); b["tf"] = TF_NAME[g["tf"]]; b["gen"] = gens.get(b["bot_id"])
        k = (b["symbol"], b["family"]); counter[k] = counter.get(k, 0) + 1
        b["name"] = ("Scalp " if fl() == "scalp" else "") + plain.name(b["symbol"], b["family"], counter[k]); b["rule"] = plain.describe(g, b["symbol"])
        b["indicators"] = indicators_text(g); b["rr"] = rr_of(g); b["combo"] = signature(g)
        b["profit_usd"] = round(usd.get(b["bot_id"], 0.0), 2); b["unreal_usd"] = 0.0
        p = pos.get(b["bot_id"])
        if p:
            t = ticks.get(b["symbol"]); u = None
            if t:
                exitpx = t["bid"] if p["dir"] == 1 else t["ask"]
                u = round(p["dir"] * (exitpx - p["entry"]) / p["risk"], 3)
                b["unreal_usd"] = round(u * (p.get("risk_usd") or 0), 2)
            cur = (t["bid"] if p["dir"] == 1 else t["ask"]) if t else None
            b["position"] = dict(dir=p["dir"], entry=p["entry"], stop=p["stop"], tgt=p["tgt"], since=p["entry_ts"], unreal_r=u, current=cur,
                                 risk_usd=p.get("risk_usd"), size=(p["risk_usd"] / p["risk"]) if p.get("risk_usd") and p["risk"] else None,
                                 unit="oz" if b["symbol"] == "XAUUSD" else "BTC", hold_until=p["hold_until"],
                                 rr=round(abs(p["tgt"] - p["entry"]) / abs(p["entry"] - p["stop"]), 2) if p["entry"] != p["stop"] else None)
            b["doing"] = f"Trading: {'BUY' if p['dir'] > 0 else 'SELL'} {plain.ASSET[b['symbol']]}"
        else:
            b["doing"] = "Waiting for a setup"
        b["expected_trades_per_day"] = (BV or {}).get("expected_tpd", {}).get(str(b["bot_id"]))
    alive = [b for b in bots if b["status"] in ("live", "probation")]
    eq = {"XAUUSD": [], "BTCUSD": [], "ALL": []}; bal = {"XAUUSD": START, "BTCUSD": START}; tot = START
    for t in trades:
        d = trade_usd(t)
        bal[t["symbol"]] += d; tot += d
        eq[t["symbol"]].append([t["exit_ts"], round(bal[t["symbol"]], 2)]); eq["ALL"].append([t["exit_ts"], round(tot, 2)])
    syd0 = int(pd.Timestamp.now(tz="Australia/Sydney").normalize().timestamp())
    realised = sum(usd.values()); unreal = sum(b["unreal_usd"] for b in alive)
    board = sorted([dict(bot_id=b["bot_id"], name=b["name"], symbol=b["symbol"], status=b["status"], profit_usd=round(b["profit_usd"] + b["unreal_usd"], 2),
                         trades=b["live_n"]) for b in alive], key=lambda x: -x["profit_usd"])
    day_ago = time.time() - 86400; t24 = [t for t in trades if t["exit_ts"] >= day_ago]
    first = min([t["entry_ts"] for t in trades], default=None); hrs = min(24.0, max(1.0, (time.time() - first) / 3600)) if first else None
    events = q("SELECT * FROM live_events WHERE kind!='heartbeat' ORDER BY rowid DESC LIMIT 60")
    for e in events:
        nm = next((b["name"] for b in bots if b["bot_id"] == e["bot_id"]), None); e["name"] = nm
    hb = q("SELECT ts, detail FROM live_events WHERE kind='heartbeat' ORDER BY rowid DESC LIMIT 1")      # live trader writes one every 5 min
    hb_age = (time.time() - pd.Timestamp(hb[0]["ts"]).timestamp()) if hb else None
    stale = hb_age is None or hb_age > 600
    allocs = q("SELECT ts, bot_id, symbol, weight FROM allocations WHERE run_id='live' ORDER BY ts DESC LIMIT 80")
    summary = dict(paper_account_usd=START, paper_profit_usd=round(realised + unreal, 2), realised_usd=round(realised, 2), open_usd=round(unreal, 2),
                   bots_alive=len(alive), trades_today=sum(1 for t in trades if t["exit_ts"] >= syd0), open_trades=len([b for b in alive if b.get("position")]),
                   breeder=BV, leaderboard=board, risk=risk_view(),
                   trades_per_hour_expected=(BV or {}).get("expected_tph"),
                   trades_per_hour_actual=round(len(t24) / hrs, 2) if hrs else 0.0, trades_last_24h=len(t24),
                   floor=fl(), experimental=fl() == "scalp",
                   costs_paid_usd=round(sum((t.get("cost_usd") or 0) for t in trades), 2),
                   costs_paid_today_usd=round(sum((t.get("cost_usd") or 0) for t in trades if t["exit_ts"] >= syd0), 2),
                   gross_before_costs_usd=round(realised + sum((t.get("cost_usd") or 0) for t in trades), 2))
    nm = {b["bot_id"]: b["name"] for b in bots}; rrm = {b["bot_id"]: b["rr"] for b in bots}
    for t in trades[-200:]:
        t["name"] = nm.get(t["bot_id"], f"bot #{t['bot_id']}"); t["usd"] = round(trade_usd(t), 2); t["rr"] = rrm.get(t["bot_id"])
    return JSONResponse(dict(now=int(time.time()), ticks=ticks, bots=bots, trades=trades[-200:], equity=eq, events=events, last_event=hb[0]["ts"] if hb else None, last_heartbeat=hb[0]["ts"] if hb else None,
                             heartbeat_age_s=(int(hb_age) if hb_age is not None else None), stale=stale,
                             allocations=allocs, summary=summary,
                             paper=dict(start_equity=START, risk_budget_pct=BUDGET_PCT, note="PAPER only. No broker, no orders.")), headers=NOCACHE)

@app.get("/api/results")
def results(floor: str = "main"):
    """Why each bot was promoted (tests passed, numbers, vs random bots), when, and how it has done live since; plus recent kills."""
    set_floor(floor)
    rows = q("SELECT bot_id, symbol, family, status, genome, added_ts, removed_ts, reason, origin, evidence, live_n, live_r, live_dd_r, weight FROM live_bots WHERE status!='retired' ORDER BY added_ts DESC")
    trades = q("SELECT bot_id, r, risk_usd, weight, cost_usd, exit_ts FROM live_trades")
    by = {}
    for t in trades: by.setdefault(t["bot_id"], []).append(t)
    counter = {}; out = []
    for b in sorted(rows, key=lambda x: x["bot_id"]):
        g = json.loads(b.pop("genome")); k = (b["symbol"], b["family"]); counter[k] = counter.get(k, 0) + 1
        b["name"] = ("Scalp " if fl() == "scalp" else "") + plain.name(b["symbol"], b["family"], counter[k])
        b["rule"] = plain.describe(g, b["symbol"]); b["tf"] = TF_NAME[g["tf"]]; b["indicators"] = indicators_text(g); b["rr"] = rr_of(g)
        ev = json.loads(b.pop("evidence")) if b.get("evidence") else None; b["evidence"] = ev
        tl = by.get(b["bot_id"], []); wins = sum(1 for t in tl if t["r"] > 0)
        b["live"] = dict(trades=len(tl), wins=wins, losses=len(tl) - wins, win_pct=round(100 * wins / len(tl), 1) if tl else None,
                         net_r=round(sum(t["r"] for t in tl), 2), profit_usd=round(sum(trade_usd(t) for t in tl), 2),
                         costs_usd=round(sum((t.get("cost_usd") or 0) for t in tl), 2), worst_drop_r=round(b["live_dd_r"] or 0, 2))
        out.append(b)
    alive = sorted([b for b in out if b["status"] in ("live", "probation")], key=lambda b: b["added_ts"] or "", reverse=True)
    killed = sorted([b for b in out if b["status"] == "killed"], key=lambda b: b["removed_ts"] or "", reverse=True)[:30]
    return JSONResponse(dict(floor=fl(), promoted=alive, killed=killed, guards=(breeder_view() or {}).get("guards")), headers=NOCACHE)

_learn_cache = {}
@app.get("/api/learned")
def learned(floor: str = "main"):
    p = set_floor(floor); f = fl()
    hit = _learn_cache.get(f)
    if hit and time.time() - hit[0] < 120: return JSONResponse(hit[1], headers=NOCACHE)
    res = learned_mod.compute(p, f)
    res["lessons"] = q("SELECT ts, key, text, numbers FROM lessons ORDER BY ts DESC LIMIT 80")
    now_keys = {c["key"] for c in res["claims"]}
    for l in res["lessons"]: l["still_true"] = l["key"] in now_keys
    _learn_cache[f] = (time.time(), res)
    return JSONResponse(res, headers=NOCACHE)

@app.get("/")
def index(): return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-store"})

app.mount("/static", StaticFiles(directory=WEB), name="static")
