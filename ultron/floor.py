"""ULTRON floor view API (read-only). Serves web/ and JSON from results/ultron.db (one DB per floor). Port 8795.

Every endpoint reads through short-lived read-only SQLite connections and aggregates in SQL, so a poll costs the same
whether the floor has 100 or 100,000 closed paper trades. Responses are cached for a couple of seconds per floor, so
any number of open browser tabs cost one query set."""
import json, os, threading, time
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np, pandas as pd
from .db import CODE, rfile, connect, connect_ro, norm_floor, read_json, SYMBOLS
from .engine import TF_NAME, indicators_text, rr as rr_of, signature
from . import plain
from .risk import load_cfg
from . import learned as learned_mod

START = 10000.0; BUDGET_PCT = 5.0
STATE_TTL_S = 2.0; RESULTS_TTL_S = 15.0; LEARNED_TTL_S = 120.0
KILLED_SHOWN = 30; EVENTS_SHOWN = 60; TRADES_SHOWN = 200; SPARK_TRADES = 40; CURVE_POINTS = 240
USD_SQL = "CASE WHEN risk_usd IS NOT NULL THEN r * risk_usd ELSE r * COALESCE(weight, 0) * 500.0 END"   # = risk.trade_usd
NOCACHE = {"Cache-Control": "no-store, max-age=0"}

app = FastAPI(title="ULTRON floor", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2048)
WEB = os.path.join(CODE, "web")

# ---------------- plumbing ----------------
_migrated = set(); _mig_lock = threading.Lock()
def db_path(floor):
    p = rfile("ultron.db", floor)
    if p not in _migrated:
        with _mig_lock:
            if p not in _migrated: connect(p).close(); _migrated.add(p)     # schema + migrations (creates the empty scalp DB on first view)
    return p

class Q:
    """One read-only connection for all queries of a request."""
    def __init__(self, floor): self.c = connect_ro(db_path(floor))
    def __call__(self, sql, args=()): return [dict(r) for r in self.c.execute(sql, args)]
    def one(self, sql, args=()):
        r = self.c.execute(sql, args).fetchone(); return dict(r) if r else None
    def __enter__(self): return self
    def __exit__(self, *a): self.c.close()

_cache = {}; _cache_lock = threading.Lock()
def cached(key, ttl, fn):
    """Per-key TTL cache; concurrent requests for the same key compute once."""
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl: return hit[1]
        lock = _cache.setdefault(("lock",) + key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl: return hit[1]
        v = fn(); _cache[key] = (time.time(), v); return v

class TradeBook:
    """Every closed paper trade of one floor, held in memory and topped up incrementally (only ids above the last one seen),
    so polls never rescan live_trades. Rebuilt if the DB is replaced (ids go backwards)."""
    COLS = ("id", "bot_id", "entry_ts", "exit_ts", "r", "usd", "cost")
    def __init__(self): self.lock = threading.Lock(); self._reset()
    def _reset(self):
        self.last_id = 0; self.buf = {k: [] for k in self.COLS}; self.arr = None
    def sync(self, q):
        with self.lock:
            mx = q.one("SELECT MAX(id) m FROM live_trades")["m"] or 0
            if mx < self.last_id: self._reset()
            if mx > self.last_id:
                for r in q(f"SELECT id, bot_id, entry_ts, exit_ts, r, {USD_SQL} usd, COALESCE(cost_usd, 0) cost FROM live_trades WHERE id > ? ORDER BY id", (self.last_id,)):
                    for k in self.COLS: self.buf[k].append(r[k])
                self.last_id = mx; self.arr = None
            if self.arr is None:
                self.arr = {k: np.asarray(v, np.float64 if k in ("r", "usd", "cost") else np.int64) for k, v in self.buf.items()}
            return self.arr

    @staticmethod
    def per_bot(A, ids):
        """{bot_id: dict(n, wins, net_r, usd, cost)} for the given bots."""
        out = {}
        if not len(A["id"]): return out
        order = np.argsort(A["bot_id"], kind="stable"); b = A["bot_id"][order]
        for bid in ids:
            lo, hi = np.searchsorted(b, bid), np.searchsorted(b, bid, side="right")
            if hi > lo:
                ix = order[lo:hi]; r = A["r"][ix]
                out[bid] = dict(n=int(hi - lo), wins=int((r > 0).sum()), net_r=float(r.sum()), usd=float(A["usd"][ix].sum()), cost=float(A["cost"][ix].sum()),
                                spark=[round(float(x), 3) for x in r[np.argsort(A["exit_ts"][ix], kind="stable")][-SPARK_TRADES:]])
        return out

_books = {}
def book(floor, q):
    return _books.setdefault(floor, TradeBook()).sync(q)

def bot_names(q, floor):
    """Stable display names: '<Asset> <Family> #n', numbered in bot_id order over every bot this floor ever had."""
    counter = {}; out = {}; pre = "Scalp " if floor == "scalp" else ""
    for b in q("SELECT bot_id, symbol, family FROM live_bots WHERE status!='retired' ORDER BY bot_id"):
        k = (b["symbol"], b["family"]); counter[k] = counter.get(k, 0) + 1
        out[b["bot_id"]] = pre + plain.name(b["symbol"], b["family"], counter[k])
    return out

def decorate(b, g, names):
    """Fields every bot card needs, derived from its genome."""
    b["name"] = names.get(b["bot_id"], f"bot #{b['bot_id']}"); b["tf"] = TF_NAME[g["tf"]]
    b["rule"] = plain.describe(g, b["symbol"]); b["indicators"] = indicators_text(g); b["rr"] = rr_of(g); b["combo"] = signature(g)
    return b

def equity_curve(A, mask):
    """Closed-trade paper equity, bucketed to <= CURVE_POINTS points: [[unix_s, equity_usd], ...]."""
    x = A["exit_ts"][mask]; u = A["usd"][mask]
    if not len(x): return []
    a, b = int(x.min()), int(x.max()); step = max(300, (b - a) // CURVE_POINTS + 1)
    k = (x - a) // step; sums = np.bincount(k, weights=u); eq = START + np.cumsum(sums)
    out = [[a - 1, START]] + [[int(min(a + (i + 1) * step, b)), round(float(e), 2)] for i, e in enumerate(eq) if sums[i] != 0 or i == len(eq) - 1]
    return out

def risk_view(floor):
    r = read_json(rfile("risk_state.json", floor))
    if not r: return None
    L = r.get("limits") or load_cfg(floor)
    return dict(open_risk_pct=round(r["open_risk_pct"], 2), open_trades=r["open_trades"], equity_usd=round(r["equity"], 2),
                day_pnl_pct=round(r["day_pnl_pct"], 2), day_pnl_usd=round(r.get("day_pnl_usd", 0), 2), blocked_today=r.get("blocked_today", 0),
                halted=r.get("halted", False), dd_halted=r.get("dd_halted", False),
                max_total_open_risk_pct=L["max_total_open_risk_pct"], max_open_trades=L["max_open_trades"],
                max_same_direction_per_symbol=L["max_same_direction_per_symbol"], daily_floor_loss_limit_pct=L["daily_floor_loss_limit_pct"],
                max_floor_drawdown_pct=L.get("max_floor_drawdown_pct"), max_bots_on_floor=L["max_bots_on_floor"],
                risk_per_trade=L["risk_per_trade"], fresh=time.time() - r.get("updated", 0) < 120)

def breeder_view(floor):
    b = read_json(rfile("breeder_stats.json", floor))
    if not b: return None
    return dict(running=time.time() - b.get("updated", 0) < 180, updated=b.get("updated"), tested_today=b["tested_today"], promoted_today=b["promoted_today"],
                cut_today=b["cut_today"], tested_last_hour=b.get("tested_last_hour"), generation=b.get("generation"),
                guards=b.get("guards"), gate_today=b.get("gate_today"), windows=b.get("windows"),
                expected_tph=b.get("expected_trades_per_hour"), expected_tpd=b.get("expected_trades_per_day", {}),
                top_reject=b.get("top_reject"), top_reject_share=b.get("top_reject_share"), reject_last_hour=b.get("reject_last_hour"),
                reject_today=b.get("reject_today"), pool_best=b.get("pool_best"), cpu_duty=b.get("cpu_duty"))

# ---------------- /api/state ----------------
def build_state(floor):
    now = time.time(); syd0 = int(pd.Timestamp.now(tz="Australia/Sydney").normalize().timestamp())
    BV = breeder_view(floor); tpd = (BV or {}).get("expected_tpd", {})
    with Q(floor) as q:
        names = bot_names(q, floor)
        ticks = {t["symbol"]: t for t in q("SELECT * FROM live_ticks")}
        alive = q("""SELECT l.bot_id, l.symbol, l.family, l.status, l.weight, l.live_n, l.live_r, l.live_dd_r, l.added_ts, l.origin, l.genome, b.born_gen gen
                     FROM live_bots l LEFT JOIN bots b ON b.bot_id = l.bot_id WHERE l.status IN ('live','probation') ORDER BY l.bot_id""")
        killed = q("SELECT bot_id, symbol, family, removed_ts, reason, live_n, live_r FROM live_bots WHERE status='killed' ORDER BY removed_ts DESC LIMIT ?", (KILLED_SHOWN,))
        pos = {p["bot_id"]: p for p in q("SELECT * FROM live_positions")}
        retired = [r["bot_id"] for r in q("SELECT bot_id FROM live_bots WHERE status='retired'")]
        A = book(floor, q)
        recent = q(f"""SELECT bot_id, symbol, entry_ts, exit_ts, dir, entry, exit, r, reason, weight, risk_usd, cost_usd, {USD_SQL} usd
                       FROM live_trades ORDER BY exit_ts DESC, id DESC LIMIT ?""", (TRADES_SHOWN + (100 if retired else 0),))
        events = q("SELECT ts, kind, bot_id, detail FROM live_events WHERE kind!='heartbeat' ORDER BY rowid DESC LIMIT ?", (EVENTS_SHOWN,))
    if retired:
        rs = set(retired); recent = [t for t in recent if t["bot_id"] not in rs][:TRADES_SHOWN]
    mask = ~np.isin(A["bot_id"], retired) if retired else np.ones(len(A["id"]), bool)
    xt = A["exit_ts"][mask]; usd_m = A["usd"][mask]; cost_m = A["cost"][mask]
    tot = dict(n=int(mask.sum()), usd=float(usd_m.sum()), cost=float(cost_m.sum()), wins=int((A["r"][mask] > 0).sum()),
               first=int(A["entry_ts"][mask].min()) if mask.any() else None, today=int((xt >= syd0).sum()),
               usd_today=float(usd_m[xt >= syd0].sum()), cost_today=float(cost_m[xt >= syd0].sum()), n24=int((xt >= now - 86400).sum()))
    agg = TradeBook.per_bot(A, [b["bot_id"] for b in alive])
    curve = cached(("curve", floor), 20, lambda: equity_curve(A, mask))
    bots = []
    for b in alive:
        g = json.loads(b.pop("genome")); decorate(b, g, names)
        a = agg.get(b["bot_id"], {}); b["profit_usd"] = round(a.get("usd") or 0.0, 2); b["unreal_usd"] = 0.0
        b["wins"] = int(a.get("wins") or 0); b["spark"] = a.get("spark", [])
        p = pos.get(b["bot_id"]); t = ticks.get(b["symbol"])
        if p:
            cur = (t["bid"] if p["dir"] == 1 else t["ask"]) if t else None
            u = round(p["dir"] * (cur - p["entry"]) / p["risk"], 3) if cur is not None and p["risk"] else None
            if u is not None: b["unreal_usd"] = round(u * (p.get("risk_usd") or 0), 2)
            b["position"] = dict(dir=p["dir"], entry=p["entry"], stop=p["stop"], tgt=p["tgt"], since=p["entry_ts"], unreal_r=u, current=cur,
                                 risk_usd=p.get("risk_usd"), size=(p["risk_usd"] / p["risk"]) if p.get("risk_usd") and p["risk"] else None,
                                 unit="oz" if b["symbol"] == "XAUUSD" else "BTC", hold_until=p["hold_until"],
                                 rr=round(abs(p["tgt"] - p["entry"]) / abs(p["entry"] - p["stop"]), 2) if p["entry"] != p["stop"] else None)
        b["expected_trades_per_day"] = tpd.get(str(b["bot_id"]))
        bots.append(b)
    for b in killed: b["name"] = names.get(b["bot_id"], f"bot #{b['bot_id']}"); b["status"] = "killed"
    rr_by = {b["bot_id"]: b["rr"] for b in bots}
    for t in recent:
        t["name"] = names.get(t["bot_id"], f"bot #{t['bot_id']}"); t["usd"] = round(t["usd"] or 0, 2); t["rr"] = rr_by.get(t["bot_id"])
    for e in events: e["name"] = names.get(e["bot_id"])
    realised = tot["usd"]; unreal = sum(b["unreal_usd"] for b in bots); cost = tot["cost"]
    first = tot["first"]; hrs = min(24.0, max(1.0, (now - first) / 3600)) if first else None; n24 = tot["n24"]
    board = sorted([dict(bot_id=b["bot_id"], name=b["name"], symbol=b["symbol"], family=b["family"], status=b["status"],
                         profit_usd=round(b["profit_usd"] + b["unreal_usd"], 2), trades=b["live_n"]) for b in bots], key=lambda x: -x["profit_usd"])
    summary = dict(paper_account_usd=START, paper_profit_usd=round(realised + unreal, 2), realised_usd=round(realised, 2), open_usd=round(unreal, 2),
                   bots_alive=len(bots), trades_total=tot["n"], wins_total=tot["wins"],
                   trades_today=tot["today"], profit_today_usd=round(tot["usd_today"], 2),
                   open_trades=sum(1 for b in bots if b.get("position")),
                   breeder=BV, leaderboard=board, risk=risk_view(floor),
                   trades_per_hour_expected=(BV or {}).get("expected_tph"),
                   trades_per_hour_actual=round(n24 / hrs, 2) if hrs else 0.0, trades_last_24h=n24,
                   floor=floor, experimental=floor == "scalp",
                   costs_paid_usd=round(cost, 2), costs_paid_today_usd=round(tot["cost_today"], 2),
                   gross_before_costs_usd=round(realised + cost, 2))
    return dict(now=int(now), floor=floor, ticks=ticks, bots=bots, killed=killed, trades=recent, events=events, curve=curve,
                last_event=events[0]["ts"] if events else None, summary=summary,
                paper=dict(start_equity=START, risk_budget_pct=BUDGET_PCT, note="PAPER only. No broker, no orders."))

@app.get("/api/state")
def state(floor: str = "main"):
    f = norm_floor(floor)
    return JSONResponse(cached(("state", f), STATE_TTL_S, lambda: build_state(f)), headers=NOCACHE)

# ---------------- /api/results ----------------
def build_results(floor):
    with Q(floor) as q:
        names = bot_names(q, floor)
        rows = q("""SELECT bot_id, symbol, family, status, genome, added_ts, removed_ts, reason, origin, evidence, live_n, live_r, live_dd_r, weight
                    FROM live_bots WHERE status IN ('live','probation')
                    UNION ALL SELECT * FROM (SELECT bot_id, symbol, family, status, genome, added_ts, removed_ts, reason, origin, evidence, live_n, live_r, live_dd_r, weight
                    FROM live_bots WHERE status='killed' ORDER BY removed_ts DESC LIMIT ?)""", (KILLED_SHOWN,))
        agg = TradeBook.per_bot(book(floor, q), [r["bot_id"] for r in rows])
    out = []
    for b in rows:
        g = json.loads(b.pop("genome")); decorate(b, g, names)
        b["evidence"] = json.loads(b["evidence"]) if b.get("evidence") else None
        a = agg.get(b["bot_id"], {}); n = int(a.get("n") or 0); wins = int(a.get("wins") or 0)
        b["live"] = dict(trades=n, wins=wins, losses=n - wins, win_pct=round(100 * wins / n, 1) if n else None,
                         net_r=round(a.get("net_r") or 0, 2), profit_usd=round(a.get("usd") or 0, 2),
                         costs_usd=round(a.get("cost") or 0, 2), worst_drop_r=round(b["live_dd_r"] or 0, 2))
        out.append(b)
    alive = sorted([b for b in out if b["status"] in ("live", "probation")], key=lambda b: b["added_ts"] or "", reverse=True)
    killed = sorted([b for b in out if b["status"] == "killed"], key=lambda b: b["removed_ts"] or "", reverse=True)
    return dict(floor=floor, promoted=alive, killed=killed, guards=(breeder_view(floor) or {}).get("guards"))

@app.get("/api/results")
def results(floor: str = "main"):
    """Why each bot was promoted (tests passed, numbers, vs random bots), when, and how it has done live since; plus recent kills."""
    f = norm_floor(floor)
    return JSONResponse(cached(("results", f), RESULTS_TTL_S, lambda: build_results(f)), headers=NOCACHE)

# ---------------- /api/learned ----------------
def build_learned(floor):
    p = db_path(floor); res = learned_mod.compute(p, floor)
    with Q(floor) as q: res["lessons"] = q("SELECT ts, key, text, numbers FROM lessons ORDER BY ts DESC LIMIT 80")
    now_keys = {c["key"] for c in res["claims"]}
    for l in res["lessons"]: l["still_true"] = l["key"] in now_keys
    return res

@app.get("/api/learned")
def learned(floor: str = "main"):
    f = norm_floor(floor)
    return JSONResponse(cached(("learned", f), LEARNED_TTL_S, lambda: build_learned(f)), headers=NOCACHE)

# ---------------- /api/replay (research run, main floor DB) ----------------
def build_replay():
    with Q("main") as q:
        run = q.one("SELECT run_id, summary FROM runs WHERE kind='phase1_research' AND summary IS NOT NULL ORDER BY started DESC LIMIT 1")
        if not run: return {"run_id": None}
        rid = run["run_id"]; summ = json.loads(run["summary"])
        out = {"run_id": rid, "windows": summ["windows"], "buy_and_hold_pct": summ.get("buy_and_hold_pct"), "symbols": {}}
        for sym in SYMBOLS:
            gens = q("SELECT island, gen, best, median, alive, born, killed, best_bot FROM generations WHERE run_id=? AND symbol=? ORDER BY island, gen", (rid, sym))
            bots = q("SELECT bot_id, island, born_gen, died_gen, family, train_fit FROM bots WHERE run_id=? AND symbol=? AND island>=0 AND train_fit IS NOT NULL", (rid, sym))
            frames = []; by_isl = {}
            for b in bots: by_isl.setdefault(b["island"], []).append(b)
            maxgen = max((g["gen"] for g in gens), default=0)
            for isl in sorted(by_isl):                   # per island/gen: top 12 alive bots by train fitness (floor desks)
                for gen in range(maxgen + 1):
                    alive = [b for b in by_isl[isl] if b["born_gen"] <= gen and (b["died_gen"] is None or b["died_gen"] >= gen)]
                    alive.sort(key=lambda b: -b["train_fit"])
                    frames.append(dict(island=isl, gen=gen, desks=[dict(id=b["bot_id"], fam=b["family"], fit=round(b["train_fit"], 2), born=b["born_gen"],
                                                                         dies=b["died_gen"] if b["died_gen"] == gen else None) for b in alive[:12]]))
            surv = q("SELECT b.bot_id, b.family, a.weight FROM bots b JOIN allocations a ON a.bot_id=b.bot_id AND a.run_id=b.run_id WHERE b.run_id=? AND b.symbol=? AND b.status='survivor'", (rid, sym))
            curves = {}
            for s in surv:
                cum = 0.0; pts = []
                for t in q("SELECT window, exit_ts, r FROM trades WHERE bot_id=? AND run_id=? ORDER BY exit_ts", (s["bot_id"], rid)):
                    cum += t["r"]; pts.append([t["exit_ts"], round(cum, 2), t["window"][0]])
                curves[s["bot_id"]] = pts
            res = summ["results"][sym]; wf = summ.get("walk_forward", {}).get(sym, {})
            out["symbols"][sym] = dict(generations=gens, frames=frames, survivors=[dict(id=s["bot_id"], fam=s["family"], weight=s["weight"]) for s in surv],
                                       curves=curves, gates=res["gates"], noise=res["noise"], tested=res["genomes_tested"],
                                       survivor_metrics=res["survivors"], walk_forward=wf.get("stitched"), walk_forward_adj=wf.get("stitched_driftadj"))
    return out

@app.get("/api/replay")
def replay():
    return JSONResponse(cached(("replay",), 600, build_replay))

@app.get("/api/health")
def health():
    out = {}
    for f in ("main", "scalp"):
        r = read_json(rfile("risk_state.json", f)) or {}; b = read_json(rfile("breeder_stats.json", f)) or {}
        out[f] = dict(live_updated=r.get("updated"), live_ok=time.time() - (r.get("updated") or 0) < 120,
                      breeder_updated=b.get("updated"), breeder_ok=time.time() - (b.get("updated") or 0) < 180)
    return JSONResponse(dict(ok=True, now=int(time.time()), floors=out), headers=NOCACHE)

@app.get("/")
def index(): return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-store"})

app.mount("/static", StaticFiles(directory=WEB), name="static")
