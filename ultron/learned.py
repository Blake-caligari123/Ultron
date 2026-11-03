"""'What Ultron learned': plain-English findings computed ONLY from logged evolution data (cand_log = random sample of every
tested candidate, gate_log = every gate run, live_bots/live_trades = what happened on the paper floor). Every claim carries
its numbers. Thresholds for making a claim are fixed below; if the data doesn't clear them, no claim is made."""
import json, sqlite3, time
import pandas as pd
from .engine import FAMILIES, TF_NAME
from . import plain

ASSET = plain.ASSET_T
FAMN = {i: plain.FAM_NAME.get(f, f) for i, f in enumerate(FAMILIES)}
TOP_Q = 0.95          # "best-scoring" = top 5% by training fitness, per market
MIN_TOP = 10          # need at least this many best-scoring bots in a group to say anything about it
LIFT_WIN, LIFT_DIE = 1.5, 0.4
SET_DIFF = 0.15       # settings: medians must differ by >= 15% to make a claim
MIN_SAMPLE = 1000    # random-origin candidates per market needed before any trait claim is made (main floor)
MIN_SAMPLE_FLOOR = {"main": 1000, "scalp": 300}   # the Scalp Pit breeds ~5x fewer candidates
PAST = ("holdout", "style", "capped", "not_stronger", "promoted")

def sess(ss, slen):
    if slen >= 24: return "all hours"
    h = (int(ss) + plain.SYD) % 24; b = h // 4 * 4
    return f"session-start {_h(b)}–{_h(b + 4)} Sydney"
def _h(h):
    h %= 24
    return "midnight" if h == 0 else "noon" if h == 12 else (f"{h}am" if h < 12 else f"{h - 12}pm")

def pct(x): return f"{100 * x:.0f}%"

def _lift_claims(df, col, label, sym, out, key):
    if not len(df): return []
    thr = df["fit"].quantile(TOP_Q); top = df[df["fit"] >= thr]; rows = []
    for v, g in df.groupby(col):
        a = len(g) / len(df); t = (top[col] == v).sum(); ts = t / max(len(top), 1); lift = ts / a if a > 0 else 0
        rows.append(dict(value=label(v), tested=int(len(g)), share_all=round(100 * a, 1), share_best=round(100 * ts, 1), lift=round(lift, 2), best=int(t)))
        nm = label(v)
        if t >= MIN_TOP and lift >= LIFT_WIN:
            out.append(dict(key=f"{key}_win:{sym}:{v}", kind="winning", text=f"On {ASSET[sym].lower()}, {nm} bots are {lift:.1f}× over-represented among the best-scoring 5% of random new bots: {pct(ts)} of the best vs {pct(a)} of all {len(df):,} random bots tested."))
        elif a >= 0.05 and len(top) >= 40 and lift <= LIFT_DIE:
            out.append(dict(key=f"{key}_die:{sym}:{v}", kind="dying", text=f"On {ASSET[sym].lower()}, {nm} bots are dying out: {pct(a)} of the {len(df):,} tested but only {pct(ts)} of the best-scoring 5% (random new bots)."))
    return sorted(rows, key=lambda r: -r["lift"])

def compute(path, floor="main", hours=24):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    since = int(time.time() - hours * 3600)
    df = pd.read_sql("SELECT * FROM cand_log WHERE ts>=?", c, params=(since,))
    gl = pd.read_sql("SELECT * FROM gate_log WHERE ts>=?", c, params=(since,))
    bots = pd.read_sql("SELECT bot_id, symbol, family, status, genome, added_ts, removed_ts, reason, live_n, live_r FROM live_bots WHERE status!='retired'", c)
    tr = pd.read_sql("SELECT bot_id, r, risk_usd, weight, cost_usd FROM live_trades", c)
    c.close()
    # $ result per bot (same rule as risk.trade_usd: old rows without risk_usd use weight x 5% x $10k)
    usd = tr["r"] * tr["risk_usd"].where(tr["risk_usd"].notna(), tr["weight"].fillna(0) * 500)
    prof = usd.groupby(tr["bot_id"]).sum().to_dict() if len(tr) else {}
    claims = []; out = dict(floor=floor, hours=hours, sample=int(len(df)), since=since, markets={}, kills={}, shifts={}, claims=claims)
    if len(df):
        df["tfn"] = df["tf"].map(TF_NAME); df["sess"] = [sess(a, b) for a, b in zip(df["ss"], df["slen"])]
        df["rr"] = df["tp"] / df["sl"]
    for sym in ("XAUUSD", "BTCUSD"):
        d_all = df[df["sym"] == sym] if len(df) else df
        # trait claims use ONLY randomly generated candidates: bred children copy their parents' traits, which would
        # make whatever Ultron already favours look like it 'wins'. Random bots are a fair comparison.
        d = d_all[d_all["origin"] == "random"] if len(d_all) else d_all
        ms = MIN_SAMPLE_FLOOR.get(floor, MIN_SAMPLE)
        m = dict(tested_sample=int(len(d_all)), random_sample=int(len(d)), min_sample=ms, enough=bool(len(d) >= ms))
        if len(d) < ms: claims_sink = []
        else: claims_sink = claims
        if len(d):
            claims_real = claims; claims = claims_sink
            m["families"] = _lift_claims(d, "fam", lambda v: FAMN[int(v)], sym, claims, "fam")
            m["timeframes"] = _lift_claims(d, "tfn", lambda v: v, sym, claims, "tf")
            m["sessions"] = _lift_claims(d, "sess", lambda v: v, sym, claims, "sess")
            m["fit_median"] = round(float(d["fit"].median()), 2); m["fit_best"] = round(float(d["fit"].max()), 2)
            m["share_positive_fit"] = round(100 * float((d["fit"] > 0).mean()), 1)
            thr = d["fit"].quantile(TOP_Q); top = d[d["fit"] >= thr]; sets = []
            for k, nm, unit, more, less in (("sl", "stops", "× ATR", "wider", "tighter"), ("tp", "profit targets", "× ATR", "further", "closer"),
                                            ("hold_h", "maximum holding time", " h", "longer", "shorter"), ("rr", "target-to-stop ratio", "", "higher", "lower")):
                a, b = float(d[k].median()), float(top[k].median()); sets.append(dict(setting=nm, all=round(a, 2), best=round(b, 2), unit=unit))
                if len(top) >= 40 and a > 0 and abs(b / a - 1) >= SET_DIFF:
                    claims.append(dict(key=f"set:{sym}:{k}:{'up' if b > a else 'down'}", kind="setting",
                        text=f"On {ASSET[sym].lower()}, the best-scoring bots use {more if b > a else less} {nm}: median {b:.2f}{unit} vs {a:.2f}{unit} across all {len(d):,} random bots tested."))
            m["settings"] = sets
            # costs: profitable before costs but losers after
            q = d[d["n"] >= 30].copy()
            cr = []
            for tf, g in q.groupby("tfn"):
                gross = g["net_r"] + g["cost_r"]; gp = gross > 0; flip = gp & (g["net_r"] <= 0)
                cpt = float((g["cost_r"] / g["n"]).median())
                cr.append(dict(tf=tf, tested=int(len(g)), gross_profitable=int(gp.sum()), turned_losers_by_costs=int(flip.sum()), cost_r_per_trade=round(cpt, 3)))
                if gp.sum() >= 30 and flip.sum() / gp.sum() >= 0.5:
                    claims.append(dict(key=f"cost:{sym}:{tf}", kind="kill pattern",
                        text=f"On {ASSET[sym].lower()} {tf}, trading costs are the killer: {pct(flip.sum() / gp.sum())} of the {int(gp.sum()):,} random bots tested that made money before costs lost money after costs (typical cost {cpt:.2f}R per trade)."))
            m["costs"] = cr
            claims = claims_real
            # gate: how far candidates get, by family
            g2 = gl[gl["sym"] == sym] if len(gl) else gl
            if len(g2):
                m["gate"] = dict(runs=int(len(g2)), passed_holdout=int(g2["stage"].isin(PAST).sum()), promoted=int((g2["stage"] == "promoted").sum()))
        b = bots[bots["symbol"] == sym]
        fams = []
        for f, g in b.groupby("family"):
            fams.append(dict(family=plain.FAM_NAME.get(f, f), alive=int(g["status"].isin(["live", "probation"]).sum()), cut=int((g["status"] == "killed").sum()),
                             live_profit_usd=round(sum(prof.get(i, 0) for i in g["bot_id"]), 2)))
        m["floor_families"] = sorted(fams, key=lambda x: -x["alive"])
        out["markets"][sym] = m
        # trait shift over time: hourly buckets of the best 10% per market
        if len(d_all):
            d = d_all.assign(bucket=(d_all["ts"] // 3600) * 3600); ser = []
            for bk, g in d.groupby("bucket"):
                if len(g) < 50: continue
                t10 = g[g["fit"] >= g["fit"].quantile(0.9)]
                fs = t10["fam"].value_counts(normalize=True)
                ser.append(dict(t=int(bk), n=int(len(g)), best_fit=round(float(t10["fit"].median()), 3), sl=round(float(t10["sl"].median()), 2),
                                tp=round(float(t10["tp"].median()), 2), hold_h=round(float(t10["hold_h"].median()), 2),
                                fam={FAMN[int(k)]: round(100 * float(v), 1) for k, v in fs.items()}))
            out["shifts"][sym] = ser
            if len(ser) >= 3:
                a, z = ser[0], ser[-1]
                if a["sl"] > 0 and abs(z["sl"] / a["sl"] - 1) >= 0.2:
                    claims.append(dict(key=f"shift:{sym}:sl:{'up' if z['sl'] > a['sl'] else 'down'}", kind="trend",
                        text=f"On {ASSET[sym].lower()}, the best 10% of new bots moved to {'wider' if z['sl'] > a['sl'] else 'tighter'} stops: median {a['sl']:.2f}× ATR → {z['sl']:.2f}× ATR over the last {len(ser)} hours."))
    # kills on the floor
    k = bots[bots["status"] == "killed"]
    cats = {}
    for r in k["reason"].fillna(""):
        cat = ("lost too much (net loss limit)" if r.startswith("lost") else "drawdown limit" if "drawdown" in r else "profit factor too low" if "profit factor" in r
               else "replaced by a stronger new bot" if r.startswith("replaced") else "test made stricter" if "stricter" in r else "other")
        cats[cat] = cats.get(cat, 0) + 1
    kt = tr[tr["bot_id"].isin(k["bot_id"])]
    out["kills"] = dict(total=int(len(k)), by_reason=cats, live_trades_of_cut_bots=int(len(kt)), costs_paid_usd=round(float(kt["cost_usd"].fillna(0).sum()), 2))
    if len(k) >= 5:
        top_cat = max(cats, key=cats.get)
        claims.append(dict(key=f"kills:{top_cat}", kind="kill pattern", text=f"Most cut bots so far were cut for: {top_cat} ({cats[top_cat]} of {len(k)})."))
    return out

def record_lessons(db, res):
    """Store each claim the first time it becomes true (dated). Returns number of new lessons."""
    have = {r[0] for r in db.execute("SELECT key FROM lessons")}; n = 0
    for c in res["claims"]:
        if c["key"] not in have:
            db.execute("INSERT INTO lessons VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), c["key"], c["text"], json.dumps(dict(kind=c["kind"], sample=res["sample"])))); n += 1
    db.commit(); return n
