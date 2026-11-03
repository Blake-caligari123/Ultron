"""ULTRON continuous breeder: cuts losers and breeds/promotes new 1m-15m strategies NONSTOP (PAPER only, no broker, no orders).
Runs as a sibling of ultron.live under run.sh (nice 19, memory-capped, duty-cycled so it uses well under one CPU core).

Loop (alternating XAUUSD / BTCUSD):
  * every ~20 s: KILL check on the live roster, performance only: live net <= -6R after 10 trades, live drawdown <= -10R,
    PF < 0.8 after 20 trades.
  * BREED a batch: steady-state GA per symbol. Children = crossover+mutation of an elite pool (tournament), children of live
    bots, and random immigrants from all 8 families, on 1m/5m/15m. Fitness = rolling TRAIN only (2023-11 -> now-180d):
    fold t-stat of drift-adjusted R (capped, floored sd) + small drawdown term. No prop-firm terms.
  * GATE the best untested candidates: parameter plateau -> rolling 180-day HOLDOUT (never used in fitness; n>=15, PF>=1.15,
    net>0, t>=1, max DD >= -15%) -> correlation < 0.7 vs roster -> hourly promotion cap.
  * PROMOTE passers immediately as trial ('probation') bots at 25% of their allocator weight. Full capital only after a
    minimum live trial (10 paper trades AND 48 h).
  * REBALANCE the allocator whenever the roster changes; signal ultron.live via results/roster_changed.
Stats for the floor view: results/breeder_stats.json."""
import json, os, sys, time, signal, hashlib
from collections import deque, Counter
import numpy as np, pandas as pd
from .db import connect, FLOOR, rfile, write_json, read_json, SYMBOLS
from .engine import random_genome, mutate, crossover, metrics, FAMILIES, gkey, describe, signature, LEVEL_FAMS, rr_fix
from . import evolve as ev
from .learn import LiveCtx, live_stats, KILL, now_s
from .risk import load_cfg
from . import learned

GUARDS = dict(
    max_promotions_per_hour=3,      # across both symbols
    max_per_symbol=15,             # plus the floor-wide max_bots_on_floor from config/floor_risk.json
    holdout_days=180,               # most recent 180 days, never used for breeding fitness, split in two (review H1):
    selection_days=90,              #   older 90 days = SELECTION holdout: the breeder may query it (once per genome per window)
    sealed_days=90,                 #   newest 90 days = SEALED final test: each genome sees it ONCE, at promotion; never used to rank/replace/allocate
    holdout_gate=dict(n=25, pf=1.2, net_r=0.0, t=1.0, dd_pct=-15.0),
    max_same_style=2,               # at most 2 live bots with the same symbol + family + timeframe + long/short setting
    min_fit=0.3,                    # rolling-TRAIN fitness needed before a candidate may see the holdout
    corr_max=0.7,                   # daily-P&L correlation vs every roster bot, checked on BOTH rolling train and holdout
    trial_min_trades=10, trial_min_hours=48, trial_weight_mult=0.25,
    alloc_cap=0.25,
    kill=dict(net_r=KILL["net_r"], min_n=KILL["min_n"], dd_r=KILL["dd_r"], pf=KILL["pf"], pf_n=KILL["pf_n"]),
)
# SCALP PIT (ULTRON_FLOOR=scalp): same pipeline (breed -> train fitness -> plateau -> holdout with costs -> style/corr -> cap),
# but 1m only, holds 3-30 min, tight stops/targets, and a deliberately LENIENT holdout bar (experimental, paper only).
SCALP_GUARDS = dict(GUARDS,
    max_promotions_per_hour=4, max_per_symbol=15,
    # LENIENT (measured 3 Nov: random 1m scalpers after costs score holdout PF ~0.33 BTC / ~0.6 gold, none profitable):
    # allow near-breakeven-to-losing bots in, but they must clearly beat random scalpers on the same unseen 6 months.
    # 3 Nov 14:3x: the lenient gate (holdout PF 0.85, net > -25R, nudge slack) let a bot that LOST -127.6R in training in. Restored:
    # same holdout rules as the main floor (PF >= 1.2, profitable, t >= 1, worst drop >= -15%) but needing 150 trades,
    # plus training PF >= 1 and profitable after costs, plus beating 75% of random bots. No nudge slack.
    holdout_gate=dict(n=150, pf=1.2, net_r=0.0, t=1.0, dd_pct=-15.0), beat_random_pct=75,
    max_same_style=3, min_fit=-2.5, corr_max=0.85,
    trial_min_trades=30, trial_min_hours=24,
    kill=dict(net_r=-8.0, min_n=20, dd_r=-12.0, pf=0.8, pf_n=40))
# Scalp Pit = 1m entries around higher-timeframe levels (ultron/levels.py): sl = stop buffer beyond the level/wick (x ATR),
# tp = target in R (2-3R), holds 6-45 min.
# more setups per bot (3 Nov): 1m or 5m bars (5m round-trip cost is ~0.3x ATR vs ~0.85x on 1m, so far fewer entries are
# cost-skipped), stop buffer up to 2 ATR so more stops clear the 8x-cost minimum, and every bot trades both directions.
SCALP_GENES = dict(tf=(0, 1), hold_h=(0.1, 0.75), sl=(0.05, 2.0), tp=(2.0, 3.0))
GUARDS["train_gate"] = dict(pf=1.0, net_r=0.0); SCALP_GUARDS["train_gate"] = dict(pf=1.0, net_r=0.0)
GUARDS["max_same_combo"] = 2; SCALP_GUARDS["max_same_combo"] = 3       # diversity: max live bots per indicator combination
FAMS = None
if FLOOR == "scalp":
    GUARDS = SCALP_GUARDS; FAMS = LEVEL_FAMS
    # fitness on 1m: the old 60-trade minimum with a mild penalty made ~59-trade bots 'best' (then failed the 150-trade holdout).
    ev.CFG["min_train_trades"] = 300; ev.CFG["low_n_penalty"] = 30.0

def scalpify(g):
    if FLOOR != "scalp": return g
    g["side"] = 0                                   # both directions
    for k, (lo, hi) in SCALP_GENES.items():
        g[k] = int(min(max(g[k], lo), hi)) if k == "tf" else round(float(min(max(g[k], lo), hi)), 3)
    return rr_fix(g)

def code_hash():
    """sha1 over the mtimes of every ultron/*.py module: a breeder must never keep gating with code that is no longer on disk."""
    d = os.path.dirname(os.path.abspath(__file__)); h = hashlib.sha1()
    for f in sorted(os.listdir(d)):
        if f.endswith(".py"): h.update(f"{f}:{os.stat(os.path.join(d, f)).st_mtime_ns};".encode())
    return h.hexdigest()[:12]
CODE_HASH = code_hash()        # taken when this process imported its modules

HKEY = "R"     # holdout / sealed gates use raw R after costs (what live earns), not the drift-adjusted fitness metric (review B3)
BATCH = 24; POOL = 60; DUTY = 0.5 if FLOOR == "main" else 0.4     # DUTY: fraction of wall time spent computing (rest sleeping)
RELOAD_S = 1800; KILL_EVERY_S = 20; REGATE_EVERY_S = 600; STATS = rfile("breeder_stats.json")
POOLF = rfile("breeder_pool.json")
N_RANDOM = 60                 # random bots per symbol for the "vs random" comparison
LOG_FRAC = 0.35; LOG_KEEP_S = 3 * 86400; LESSONS_EVERY_S = 600

def syd_day(): return pd.Timestamp.now(tz="Australia/Sydney").strftime("%Y-%m-%d")

class Breeder:
    def __init__(self):
        self.db = connect(); self.rng = np.random.default_rng(int(time.time()))
        self.ctx = {}; self.loaded = {}; self.win = {}
        self.pool = {s: [] for s in SYMBOLS}          # [(fit, genome)] elite pool per symbol
        for s, lst in (read_json(POOLF) or {}).items(): self.pool[s] = [(f, g) for f, g in lst]
        for tb in ("held_tested", "sealed_tested"):
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {tb}(gh TEXT, wend TEXT, sym TEXT, ts INTEGER, passed INTEGER, PRIMARY KEY(gh, wend))")
        self.db.commit()
        self.held_tested = set()      # (sym, genome) already sent to the selection holdout today
        self.meta = {}                # bot_id -> cached train fit / holdout metrics / daily R / vol
        self.seen_today = set()       # hashes of distinct genomes tested today
        self.promos = deque(); self.tested_hour = deque()
        for (ts,) in self.db.execute("SELECT added_ts FROM live_bots WHERE origin='breeder' ORDER BY added_ts"):   # hourly cap survives restarts
            t = pd.Timestamp(ts).timestamp()
            if time.time() - t < 3600: self.promos.append(t)
        self.gen = int(self.db.execute("SELECT MAX(gen) FROM generations").fetchone()[0] or 0)
        mx = self.db.execute("SELECT MAX(born_gen) FROM bots WHERE origin IN ('live-child','breeder')").fetchone()[0]
        self.gen = max(self.gen, int(mx or 0))
        self.stats = dict(day=syd_day(), tested_today=0, promoted_today=0, cut_today=0, gate_today={})
        old = read_json(STATS) or {}
        if old.get("day") == self.stats["day"]:
            for k in ("tested_today", "promoted_today", "cut_today", "gate_today"): self.stats[k] = old.get(k, self.stats[k])
        self.rej = deque(); self.hb = {}; self.rej_today = Counter(old.get("reject_today", {}) if old.get("day") == self.stats["day"] else {})
        self.last_regate = 0.0
        self.started = time.time(); self.busy = 0.0; self.last_kill = 0; self.rand = {}; self.last_lessons = 0

    def random_baseline(self, sym, wk="held"):
        """Results (raw R after costs) of N_RANDOM random bots on window wk, for the 'vs random bots' comparison."""
        if not self.rand.get((sym, wk)):
            c = self.get_ctx(sym); w = self.win[sym]; rng = np.random.default_rng(7)
            self.rand[(sym, wk)] = sorted(metrics(c.bt(scalpify(random_genome(rng, FAMS)), w[wk])[HKEY])["net_r"] for _ in range(N_RANDOM))
        return self.rand[(sym, wk)]

    # ---------- holdout bookkeeping (review H1): persisted, so restarts / day rolls don't re-open the holdout ----------
    @staticmethod
    def ghash(sym, g): return hashlib.sha1((sym + json.dumps(g, sort_keys=True)).encode()).hexdigest()[:20]
    def was_tested(self, table, sym, g, wend=""):
        return self.db.execute(f"SELECT 1 FROM {table} WHERE gh=? AND wend=?", (self.ghash(sym, g), wend)).fetchone() is not None
    def mark_tested(self, table, sym, g, wend="", passed=0):
        self.db.execute(f"INSERT OR REPLACE INTO {table}(gh,wend,sym,ts,passed) VALUES(?,?,?,?,?)", (self.ghash(sym, g), wend, sym, int(time.time()), int(passed)))

    def evidence(self, sym, g, fit, hm=None, backfilled=False, sm=None):
        c = self.get_ctx(sym); w = self.win[sym]
        tm = metrics(c.bt(g, w["train"])[ev.KEY]); hm = hm or metrics(c.bt(g, w["held"])[HKEY])
        rb = self.random_baseline(sym); beat = round(100 * sum(x < hm["net_r"] for x in rb) / len(rb), 0)
        wins = lambda m: int(round(m["n"] * m["win"] / 100))
        return dict(floor=FLOOR, train_window=list(w["train"]), holdout_window=list(w["held"]), fitness=round(float(fit), 3),
                    train=dict(tm, wins=wins(tm), losses=tm["n"] - wins(tm)), holdout=dict(hm, wins=wins(hm), losses=hm["n"] - wins(hm)),
                    random=dict(n=len(rb), median_net_r=round(float(np.median(rb)), 2), beat_pct=beat),
                    sealed_window=list(w["sealed"]), sealed=(dict(sm, wins=wins(sm), losses=sm["n"] - wins(sm)) if sm else None),
                    gate=dict(GUARDS["holdout_gate"], beat_random_pct=GUARDS.get("beat_random_pct")), costs="spread + commission + slippage per fill, overnight financing", backfilled=backfilled,
                    risk_pct_basis=1.0, code_hash=CODE_HASH, gate_windows=dict(train=list(w["train"]), selection=list(w["held"]), sealed=list(w["sealed"])))

    def backfill_evidence(self):
        for bid, sym, g in self.db.execute("SELECT bot_id,symbol,genome FROM live_bots WHERE status IN ('live','probation') AND evidence IS NULL").fetchall():
            g = json.loads(g); fit = ev.fitness(self.get_ctx(sym), g, self.win[sym]["train"])[0]
            self.db.execute("UPDATE live_bots SET evidence=? WHERE bot_id=?", (json.dumps(self.evidence(sym, g, fit, backfilled=True)), bid))
        self.db.commit()

    # ---------- data / windows ----------
    def get_ctx(self, sym):
        if sym not in self.ctx or time.time() - self.loaded[sym] > RELOAD_S:
            self.ctx.pop(sym, None)
            t_end = pd.Timestamp.now("UTC").tz_localize(None).normalize() + pd.Timedelta(days=1)
            h0 = t_end - pd.Timedelta(days=GUARDS["holdout_days"]); s0 = t_end - pd.Timedelta(days=GUARDS["sealed_days"])
            held = (str(h0.date()), str(s0.date())); sealed = (str(s0.date()), str(t_end.date()))
            self.win[sym] = dict(train=("2023-11-01", held[0]), held=held, sealed=sealed)
            self.ctx[sym] = LiveCtx(sym); self.loaded[sym] = time.time()
            for b in [b for b, m in self.meta.items() if m["sym"] == sym]: self.meta.pop(b)     # refresh scores on new data
            self.rand = {k: v for k, v in self.rand.items() if k[0] != sym}
        return self.ctx[sym]

    def bot_meta(self, bid, sym, g):
        if bid not in self.meta:
            c = self.get_ctx(sym); w = self.win[sym]
            hist = ev.fitness(c, g, w["train"])[0]; rh = c.bt(g, w["held"]); hm = metrics(rh[HKEY])
            self.meta[bid] = dict(sym=sym, g=g, hist=hist, held=hm, vol=max(ev.daily_r(c.bt(g, w["train"]), *w["train"], ev.KEY).std(), 1e-6),   # vol from TRAIN only (review H1)
                                  dtrain=ev.daily_r(c.bt(g, w["train"]), *w["train"], ev.KEY).values, dheld=ev.daily_r(rh, *w["held"], ev.KEY).values)
        return self.meta[bid]

    def event(self, kind, bid, d):
        self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), kind, bid, json.dumps(d)))

    # ---------- kills ----------
    def roster(self):
        return self.db.execute("SELECT bot_id,symbol,genome,status,live_n,live_r,live_dd_r,added_ts FROM live_bots WHERE status IN ('live','probation')").fetchall()

    def kill_check(self):
        changed = False; K = GUARDS["kill"]
        for bid, sym, g, st, n, r, dd, added in self.roster():
            why = None
            if n >= K["min_n"] and r <= K["net_r"]: why = f"lost {r:.1f}R live (limit {K['net_r']}R)"
            elif dd <= K["dd_r"]: why = f"live drawdown {dd:.1f}R (limit {K['dd_r']}R)"
            elif n >= K["pf_n"]:
                pf = metrics(live_stats(self.db, bid))["pf"]           # only query trades when the PF rule can apply
                if pf < K["pf"]: why = f"live profit factor {pf} after {n} trades"
            if why:
                self.cut(bid, sym, why); changed = True
            elif st == "probation":
                age_h = (time.time() - pd.Timestamp(added).timestamp()) / 3600
                if n >= GUARDS["trial_min_trades"] and age_h >= GUARDS["trial_min_hours"]:
                    self.db.execute("UPDATE live_bots SET status='live' WHERE bot_id=?", (bid,))
                    self.event("graduated", bid, dict(sym=sym, trades=n, hours=round(age_h, 1))); changed = True
        self.db.commit()
        return changed

    def selection_check(self, sym, g):
        """THE train + selection-holdout rule, shared by promotion (_gate_one) and the 10-minute re-check (passes_today):
        same window (self.win[sym]['held']), same cached backtest (same data, costs and entry filters), same thresholds.
        Returns (stage, plain reason, train metrics, holdout metrics, holdout trades); stage None = passed."""
        G = GUARDS; gh = G["holdout_gate"]; tg = G["train_gate"]; ctx = self.get_ctx(sym); w = self.win[sym]
        tm = metrics(ctx.bt(g, w["train"])[ev.KEY]); rh = ctx.bt(g, w["held"]); hm = metrics(rh[HKEY])
        if not (tm["pf"] >= tg["pf"] and tm["net_r"] > tg["net_r"]):
            return "train", f"training PF {tm['pf']:.2f}, {tm['net_r']:+.1f}R after costs (needs PF >= {tg['pf']} and a profit)", tm, hm, rh
        if not (hm["n"] >= gh["n"] and hm["pf"] >= gh["pf"] and hm["net_r"] > gh["net_r"] and hm["t"] >= gh["t"] and hm["max_dd_pct"] >= gh["dd_pct"]):
            return "held", (f"selection holdout {w['held'][0]}..{w['held'][1]}: {hm['n']} trades, PF {hm['pf']:.2f}, {hm['net_r']:+.1f}R, worst drop {hm['max_dd_pct']:.1f}%"
                            f" (needs >= {gh['n']} trades, PF >= {gh['pf']}, a profit, worst drop >= {gh['dd_pct']}%)"), tm, hm, rh
        if "beat_random_pct" in G:
            rb = self.random_baseline(sym); b = 100 * sum(x < hm["net_r"] for x in rb) / len(rb)
            if b < G["beat_random_pct"]: return "random", f"beat only {b:.0f}% of random bots (needs {G['beat_random_pct']}%)", tm, hm, rh
        return None, "", tm, hm, rh

    def passes_today(self, sym, g):
        """Same rules a new bot must pass, on TODAY's rolling windows. Returns (ok, plain reason)."""
        st, why, *_ = self.selection_check(sym, g)      # train + selection holdout only; the sealed test is never re-queried
        return (True, "") if st is None else (False, "failed today's re-check: " + why)

    def regate(self):
        """Re-gate every bot on the floor with today's rules; cut those that no longer pass."""
        changed = False
        for bid, sym, g, st, *_ in self.roster():
            ok, why = self.passes_today(sym, json.loads(g))
            if not ok: self.cut(bid, sym, why); changed = True
        self.db.commit(); self.last_regate = time.time()
        return changed

    def reject(self, why):
        self.rej.append((time.time(), why)); self.rej_today[why] += 1

    def cut(self, bid, sym, why):
        self.db.execute("UPDATE live_bots SET status='killed', removed_ts=?, reason=?, weight=0 WHERE bot_id=?", (now_s(), why, bid))
        self.db.execute("UPDATE bots SET status='killed', died_gen=? WHERE bot_id=?", (self.gen, bid))
        self.event("kill", bid, dict(sym=sym, reason=why)); self.meta.pop(bid, None)
        self.stats["cut_today"] += 1
        print(now_s(), "CUT", bid, sym, why, flush=True)

    # ---------- breeding ----------
    def tour(self, sym):
        P = self.pool[sym]
        a, b = self.rng.integers(len(P), size=2)
        return P[min(a, b)][1]          # pool is sorted best-first: lower index wins

    def breed(self, sym):
        ctx = self.get_ctx(sym); w = self.win[sym]; rng = self.rng
        kids = []; orig = []
        for _ in range(BATCH):
            u = rng.random()
            if len(self.pool[sym]) >= 4 and u < 0.5:   kids.append(mutate(crossover(self.tour(sym), self.tour(sym), rng), rng)); orig.append("pool")
            elif len(self.pool[sym]) >= 4 and u < 0.75: kids.append(mutate(self.tour(sym), rng, rate=0.4)); orig.append("pool")   # no children of floor bots: they were picked on the holdout (review H1)
            else: kids.append(random_genome(rng, FAMS)); orig.append("random")
        kids = [scalpify(k) for k in kids]
        self.gen += 1
        scored = []; logrows = []; now_i = int(time.time())
        mf = GUARDS["min_fit"]; mn = ev.CFG["min_train_trades"]; best = -99.0
        for k, o in zip(kids, orig):
            fit, m = ev.fitness(ctx, k, w["train"]); scored.append((float(fit), k)); best = max(best, float(fit))
            if fit < mf:                      # why this candidate never reached the gate (plain words, for the floor + report)
                r = ctx.bt(k, w["train"]); n = len(r["R"]); net = float(r["Ra"].sum()) if n else 0.0; gross = net + (float(r["cost_r"].sum()) if n else 0.0)   # drift-adjusted, costs actually charged
                why = ("too few trades" if n < mn else "spread costs (profitable before costs, loses after)" if gross > 0 >= net
                       else "loses even before costs" if net <= 0 else "inconsistent across periods")
                self.reject(why)
            if rng.random() < LOG_FRAC:       # sample of every tested candidate, for the 'What Ultron learned' tab
                r = ctx.bt(k, w["train"]); n = len(r["R"])
                logrows.append((now_i, sym, self.gen, k["fam"], k["tf"], k["side"], k["trend"], k["sl"], k["tp"], k["hold_h"], k["ss"], k["slen"],
                                float(fit), n, float(r["R"].sum()) if n else 0.0, float(r["cost_r"].sum()) if n else 0.0, o))
        if logrows:
            self.db.executemany("INSERT INTO cand_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", logrows); self.db.commit()
        new = 0
        for k in kids:
            h = hash((sym, gkey(k)))
            if h not in self.seen_today: self.seen_today.add(h); new += 1
        self.stats["tested_today"] += new; now = time.time(); self.tested_hour.extend([now] * new)
        seen = {gkey(g) for _, g in self.pool[sym]}
        for f, k in scored:
            if gkey(k) not in seen: self.pool[sym].append((f, k)); seen.add(gkey(k))
        out, cnt = [], {}
        for f, g in sorted(self.pool[sym], key=lambda x: -x[0]):      # niche cap keeps the pool from collapsing onto one style
            st = (signature(g), g["tf"], g["side"]); cnt[st] = cnt.get(st, 0) + 1
            if cnt[st] <= 6: out.append((f, g))
        self.pool[sym] = out[:POOL]
        self.hb = dict(sym=sym, batch=len(kids), new=new, best=round(best, 2))
        return scored

    def gate(self, sym, cands):
        G = GUARDS; gh = G["holdout_gate"]; ctx = self.get_ctx(sym); w = self.win[sym]
        gt = self.stats["gate_today"].setdefault(sym, {})
        for k in ("fit", "plateau", "holdout", "style", "corr", "promoted", "capped"): gt.setdefault(k, 0)
        roster = [(b, json.loads(g), st) for b, s, g, st, *_ in self.roster() if s == sym]
        wend = w["held"][1]
        cands = sorted([c for c in cands if c[0] >= G["min_fit"] and (sym, gkey(c[1])) not in self.held_tested], key=lambda x: -x[0])
        cands = [c for c in cands if not self.was_tested("held_tested", sym, c[1], wend)][:3]
        changed = False
        for fit, k in cands:
            self.held_tested.add((sym, gkey(k))); gt["fit"] += 1; self.mark_tested("held_tested", sym, k, wend)
            self._why = None
            stage = self._gate_one(sym, k, fit, roster, gt, G, gh, ctx, w)
            if stage != "promoted": self.reject(self._why or stage)
            self.db.execute("INSERT INTO gate_log VALUES(?,?,?,?,?)", (int(time.time()), sym, k["fam"], k["tf"], stage))
            if stage == "promoted": changed = True
        self.db.commit()
        return changed

    def _gate_one(self, sym, k, fit, roster, gt, G, gh, ctx, w):
        """Runs one candidate through the gate; returns the furthest stage it reached ('fit' = failed plateau)."""
        ok, med = ev.plateau_ok(ctx, k, fit, self.rng, w["train"], k=6)
        if "plateau_slack" in G: ok = med >= fit - G["plateau_slack"] * max(1.0, abs(fit))     # scalp: nudged settings must not collapse
        if not ok: self._why = "unstable when settings are nudged"; return "fit"
        gt["plateau"] += 1
        st, _w, tm, hm, rh = self.selection_check(sym, k)          # identical to the 10-minute re-check (passes_today)
        if st == "train": self._why = "lost money in training after costs"; return "fit"
        if st == "held": self._why = "failed the unseen selection test" + (" (too few trades)" if hm["n"] < gh["n"] else ""); return "plateau"
        if st == "random": self._why = f"did not beat {G['beat_random_pct']}% of random bots"; return "plateau"
        gt["holdout"] += 1
        style = lambda g: (g["fam"], g["tf"], g["side"])
        if sum(style(g) == style(k) for _, g, _ in roster) >= G["max_same_style"]: self._why = "same style already on the floor"; return "holdout"
        allg = [json.loads(g) for _, _, g, *_ in self.roster()]
        if sum(signature(g) == signature(k) for g in allg) >= G["max_same_combo"]: self._why = f"indicator combo '{signature(k)}' already on the floor {G['max_same_combo']}x"; return "holdout"
        gt["style"] += 1
        dk = ev.daily_r(ctx.bt(k, w["train"]), *w["train"], ev.KEY).values; dh = ev.daily_r(rh, *w["held"], ev.KEY).values
        cc = lambda a, b: abs(float(np.nan_to_num(np.corrcoef(a, b)[0, 1])))
        if any(max(cc(dk, self.bot_meta(b, sym, g)["dtrain"]), cc(dh, self.bot_meta(b, sym, g)["dheld"])) > G["corr_max"] for b, g, _ in roster): self._why = "trades too much like a bot already on the floor"; return "style"
        gt["corr"] += 1
        while self.promos and time.time() - self.promos[0] > 3600: self.promos.popleft()
        if len(self.roster()) >= load_cfg()["max_bots_on_floor"] and len(roster) < G["max_per_symbol"]:
            gt.setdefault("floor_full", 0); gt["floor_full"] += 1; self._why = "floor full"; return "capped"
        if len(self.promos) >= G["max_promotions_per_hour"]:
            gt.setdefault("capped", 0); gt["capped"] += 1; self._why = "hourly promotion cap"; return "capped"
        # SEALED final test (review H1): newest 90 days, one look per genome ever, same strict rules as the selection holdout.
        if self.was_tested("sealed_tested", sym, k):
            self._why = "already used its one look at the sealed test"; return "corr"
        sm = metrics(ctx.bt(k, w["sealed"])[HKEY])
        passed = (sm["n"] >= gh["n"] and sm["pf"] >= gh["pf"] and sm["net_r"] > gh["net_r"] and sm["t"] >= gh["t"] and sm["max_dd_pct"] >= gh["dd_pct"])
        if passed and "beat_random_pct" in G:
            rbs = self.random_baseline(sym, "sealed"); passed = 100 * sum(x < sm["net_r"] for x in rbs) / len(rbs) >= G["beat_random_pct"]
        self.mark_tested("sealed_tested", sym, k, "", passed)
        gt.setdefault("sealed", 0)
        if not passed:
            self._why = "failed the sealed final test" + (" (too few trades)" if sm["n"] < gh["n"] else ""); return "corr"
        gt["sealed"] += 1
        score = fit                       # replacement ranks on TRAIN fitness only, never on holdout results (review H1)
        if len(roster) >= G["max_per_symbol"]:
            worst = min(roster, key=lambda x: self.blended(x[0], sym, x[1]))
            if self.blended(worst[0], sym, worst[1]) >= score: self._why = "not stronger than the weakest bot"; return "not_stronger"
            self.cut(worst[0], sym, "replaced by a stronger new bot"); roster.remove(worst)
        bid = self.promote(sym, k, fit, hm, sm); roster.append((bid, k, "probation"))
        self.promos.append(time.time()); gt["promoted"] += 1
        return "promoted"

    def promote(self, sym, k, fit, hm, sm=None):
        cur = self.db.execute("INSERT INTO bots(run_id,symbol,island,born_gen,origin,family,genome,train_fit,status) VALUES(?,?,?,?,?,?,?,?,?)",
                              ("breeder", sym, 99, self.gen, "breeder", FAMILIES[k["fam"]], json.dumps(k), fit, "alive"))
        bid = cur.lastrowid
        self.db.execute("INSERT INTO live_bots(bot_id,symbol,genome,family,status,weight,added_ts,origin,evidence) VALUES(?,?,?,?,?,?,?,?,?)",
                        (bid, sym, json.dumps(k), FAMILIES[k["fam"]], "probation", 0, now_s(), "breeder", json.dumps(self.evidence(sym, k, fit, hm, sm=sm))))
        self.event("birth", bid, dict(sym=sym, family=FAMILIES[k["fam"]], desc=describe(k), gen=self.gen,
                                      holdout=dict(n=hm["n"], pf=hm["pf"], net_r=hm["net_r"]), sealed=(dict(n=sm["n"], pf=sm["pf"], net_r=sm["net_r"]) if sm else None)))
        self.db.commit(); self.stats["promoted_today"] += 1
        print(now_s(), "PROMOTED", bid, sym, describe(k), "fit", round(fit, 2), "held", hm["n"], hm["pf"], hm["net_r"], flush=True)
        return bid

    # ---------- allocator ----------
    def blended(self, bid, sym, g):
        m = self.bot_meta(bid, sym, g); lm = metrics(live_stats(self.db, bid)); lw = min(1.0, lm["n"] / 30)
        return (1 - lw) * m["hist"] + lw * lm["t"]          # TRAIN fitness + live paper t only; no holdout results in ranking (review H1)

    def rebalance(self):
        R = self.roster(); ids = [r[0] for r in R]
        if not ids:                                   # empty roster: still tell ultron.live, so cut bots stop trading
            open(rfile("roster_changed"), "w").write(now_s()); return
        raw = []
        for bid, sym, g, st, *_ in R:
            m = self.bot_meta(bid, sym, json.loads(g))
            raw.append(max(self.blended(bid, sym, json.loads(g)), 0) / m["vol"] * (GUARDS["trial_weight_mult"] if st == "probation" else 1.0))
        raw = np.array(raw); w = raw / raw.sum() if raw.sum() > 0 else np.ones(len(ids)) / len(ids)
        cap = GUARDS["alloc_cap"]
        for _ in range(20):
            over = w > cap
            if not over.any() or (~over).sum() == 0: break
            ex = (w[over] - cap).sum(); w[over] = cap; w[~over] += ex * w[~over] / w[~over].sum()
        for b, x, r in zip(ids, w, R):
            self.db.execute("UPDATE live_bots SET weight=? WHERE bot_id=?", (float(x), b))
            self.db.execute("INSERT INTO allocations VALUES(?,?,?,?,?,?)", ("live", now_s(), b, r[1], float(x), f"breeder[{FLOOR}]: train fit + live t / train vol, cap 25%, trial x0.25"))
        self.event("rebalance", None, {str(b): round(float(x), 3) for b, x in zip(ids, w)})
        self.db.commit()
        open(rfile("roster_changed"), "w").write(now_s())

    # ---------- bookkeeping ----------
    def save_stats(self):
        if self.stats["day"] != syd_day():
            self.stats.update(day=syd_day(), tested_today=0, promoted_today=0, cut_today=0, gate_today={})
            self.held_tested.clear(); self.seen_today.clear(); self.rej_today.clear()   # in-memory only; the persisted held_tested/sealed_tested tables are kept
        now = time.time()
        while self.tested_hour and now - self.tested_hour[0] > 3600: self.tested_hour.popleft()
        up = now - self.started
        tpd = {}
        for bid, sym, g, *_ in self.roster():
            m = self.bot_meta(bid, sym, json.loads(g)); tpd[str(bid)] = round(m["held"]["n"] / GUARDS["selection_days"], 2)
        while self.rej and now - self.rej[0][0] > 3600: self.rej.popleft()
        rh = Counter(r for _, r in self.rej); top = rh.most_common(1)
        s = dict(self.stats, updated=int(now), reject_last_hour=dict(rh.most_common()), reject_today=dict(self.rej_today),
                 top_reject=(top[0][0] if top else None), top_reject_share=(round(100 * top[0][1] / max(sum(rh.values()), 1)) if top else None), expected_trades_per_day=tpd, expected_trades_per_hour=round(sum(tpd.values()) / 24, 2), running_s=int(up), tested_last_hour=len(self.tested_hour), generation=self.gen,
                 cpu_duty=round(self.busy / max(up, 1), 3), guards=GUARDS, pool_best={k: (round(v[0][0], 2) if v else None) for k, v in self.pool.items()},
                 windows=self.win)
        write_json(STATS, s)
        if now - self.last_lessons > LESSONS_EVERY_S and now - self.started > 120:
            self.last_lessons = now
            try:
                self.db.execute("DELETE FROM cand_log WHERE ts<?", (int(now - LOG_KEEP_S),)); self.db.execute("DELETE FROM gate_log WHERE ts<?", (int(now - LOG_KEEP_S),))
                n = learned.record_lessons(self.db, learned.compute(rfile("ultron.db"), FLOOR)); print(now_s(), "lessons +", n, flush=True)
            except Exception as e: print(now_s(), "lessons error", e, flush=True)
        write_json(POOLF, self.pool)

    def run(self):
        print(now_s(), "breeder start; floor", FLOOR, "code", CODE_HASH, "guards", json.dumps(GUARDS), flush=True)
        for sym in SYMBOLS: self.get_ctx(sym)
        self.backfill_evidence()
        changed = True; i = 0
        while True:
            if code_hash() != CODE_HASH:      # code changed on disk since start: exit so run.sh respawns us on the new code
                print(now_s(), "code changed on disk (hash", CODE_HASH, "->", code_hash() + "); exiting rc=3 for a respawn", flush=True); sys.exit(3)
            for sym in SYMBOLS:
                t0 = time.time()
                if time.time() - self.last_kill > KILL_EVERY_S:
                    changed |= self.kill_check(); self.last_kill = time.time()
                if time.time() - self.last_regate > REGATE_EVERY_S: changed |= self.regate()
                cands = self.breed(sym)
                changed |= self.gate(sym, cands)
                while self.rej and time.time() - self.rej[0][0] > 3600: self.rej.popleft()
                rh = Counter(r for _, r in self.rej).most_common(3)
                print(now_s(), f"HB {FLOOR} {sym} gen {self.gen} batch {self.hb.get('batch')} new {self.hb.get('new')} best_fit {self.hb.get('best')}"
                      f" | last hour tested {len(self.tested_hour)} | rejects " + "; ".join(f"{k}: {v}" for k, v in rh), flush=True)
                if changed: self.rebalance(); changed = False
                i += 1
                if i % 5 == 0: self.save_stats()
                dt = time.time() - t0; self.busy += dt
                time.sleep(dt * (1 / DUTY - 1))

if __name__ == "__main__":
    os.nice(9)   # on top of run.sh's nice 10 -> 19
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    Breeder().run()
