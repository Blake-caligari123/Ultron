"""Indicators, intraday strategy genome -> signals, numba trade simulator (with MAE), metrics.
Timeframes: 0=1m, 1=5m, 2=15m. Max holding time is a gene in hours."""
import numpy as np, pandas as pd
from numba import njit

TF_MIN = {0: 1, 1: 5, 2: 15, 3: 30}
TF_NAME = {0: "1m", 1: "5m", 2: "15m", 3: "30m"}

# ---------------- indicators (causal) ----------------
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values

def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values

def rsi(c, n):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    return 100 - 100 / (1 + up / np.maximum(dn, 1e-12))

@njit(cache=True)
def _anchored_twap(h, l, c, ts, anchor_h):
    """session 'VWAP' anchored daily at anchor_h UTC. Uses typical price, time-weighted (no reliable volume for spot gold)."""
    n = len(c); out = np.empty(n); cur = -1; s = 0.0; k = 0
    for i in range(n):
        g = (ts[i] - anchor_h * 3600) // 86400
        if g != cur: cur = g; s = 0.0; k = 0
        s += (h[i] + l[i] + c[i]) / 3.0; k += 1
        out[i] = s / k
    return out

class Ind:
    """per-(market,tf) indicator cache (bounded: 1m arrays are ~10 MB each)"""
    def __init__(self, t, cap=40):
        self.t = t; self.cache = {}; self.cap = cap
    def get(self, name, *p):
        k = (name,) + p
        v = self.cache.get(k)
        if v is None:
            t = self.t
            if name == "atr": v = atr(t["h"], t["l"], t["c"], 14)
            elif name == "atr_ratio":
                a = self.get("atr"); v = a / pd.Series(a).rolling(500, min_periods=50).mean().values
            elif name == "ema": v = ema(t["c"], p[0])
            elif name == "hh": v = pd.Series(t["h"]).rolling(p[0]).max().shift(1).values
            elif name == "ll": v = pd.Series(t["l"]).rolling(p[0]).min().shift(1).values
            elif name == "z":
                s = pd.Series(t["c"]); m = s.rolling(p[0]).mean(); sd = s.rolling(p[0]).std()
                v = ((s - m) / sd).values
            elif name == "rsi": v = rsi(t["c"], p[0])
            elif name == "twap": v = _anchored_twap(t["h"], t["l"], t["c"], t["ts"], p[0])
            elif name == "mom": c = t["c"]; v = c - np.r_[np.full(p[0], np.nan), c[:-p[0]]]
            elif name == "st":
                from . import indicators; v = indicators.state(self, p[0], p[1])
            if len(self.cache) >= self.cap:
                for kk in [kk for kk in self.cache if kk[0] not in ("atr", "atr_ratio")][: self.cap // 2]: self.cache.pop(kk)
            self.cache[k] = v
        return v

# ---------------- genome ----------------
FAMILIES = ["donchian_breakout", "bollinger_reversion", "ema_trend_cross", "session_orb", "rsi_pullback",
            "vwap_reversion", "momentum_burst", "ema_pullback",
            "lvl_retest", "lvl_sweep", "lvl_pullback",           # 8-10: Scalp Pit level families (ultron/levels.py)
            "indicator_combo"]                                    # 11: 1-3 indicator conditions joined by AND/OR (ultron/indicators.py)
MAIN_FAMS = list(range(8)); LEVEL_FAMS = [8, 9, 10]; COMBO = 11
NEW_MAIN_FAMS = MAIN_FAMS + [COMBO] * 8      # new random main-floor bots: half are indicator combos
RR = (2.0, 3.0)                              # take-profit = 2x-3x the stop distance (reward:risk 1:2 to 1:3)
CG = {"nc": (1, 3, 1), "lg": (0, 1, 1), "i1": (0, 13, 1), "n1": (0, 6, 1), "m1": (0, 1, 1),
      "i2": (0, 13, 1), "n2": (0, 6, 1), "m2": (0, 1, 1), "i3": (0, 13, 1), "n3": (0, 6, 1), "m3": (0, 1, 1)}
# gene: (lo, hi, is_int)
COMMON = {"tf": (0, 3, 1), "sl": (0.5, 5.0, 0), "tp": (1.0, 15.0, 0), "hold_h": (0.25, 8.0, 0),
          "ss": (0, 23, 1), "slen": (1, 24, 1), "vlo": (0.3, 1.2, 0), "vw": (0.4, 3.0, 0),
          "trend": (0, 2, 1), "side": (0, 2, 1), "flip": (0, 1, 1), "risk": (0.25, 2.0, 0)}
FAM = {0: {"p1": (10, 240, 1)},
       1: {"p1": (10, 200, 1), "p2": (1.0, 3.5, 0)},
       2: {"p1": (3, 60, 1), "p2": (1.5, 6.0, 0)},
       3: {"p1": (0, 23, 1), "p2": (5, 120, 1)},                 # session start hour UTC, range minutes
       4: {"p1": (2, 30, 1), "p2": (5, 40, 1), "p3": (50, 400, 1)},
       5: {"p1": (0.5, 4.0, 0), "p2": (0, 23, 1)},              # deviation in ATRs, anchor hour UTC
       6: {"p1": (1, 20, 1), "p2": (0.5, 4.0, 0)},              # lookback bars, burst size in ATRs
       7: {"p1": (5, 50, 1), "p2": (2.0, 8.0, 0)},              # fast EMA, slow = fast * p2
       8: {"p1": (0, 5, 1), "p2": (3, 60, 1), "p3": (0.05, 1.5, 0), "lvm": (1, 63, 1)},   # level type, window bars, tolerance ATR
       9: {"p1": (0, 5, 1), "p2": (3, 60, 1), "p3": (0.05, 1.5, 0), "lvm": (1, 63, 1)},   # level type, (unused), max sweep depth = 3*p3 ATR
       10: {"p1": (0, 5, 1), "p2": (3, 60, 1), "p3": (0.05, 1.5, 0), "lvm": (1, 63, 1)},  # level type, window bars, invalidation ATR
       11: dict(CG)}
for _f in LEVEL_FAMS: FAM[_f].update(CG, nc=(0, 2, 1))     # level bots may add 0-2 indicator confirmations
DISCRETE_C = {k for k in CG if k[0] in "inml"}

def rr_fix(g):
    """Force take-profit into 2R-3R. ATR families: tp = rr * sl. Level families: tp is already the R multiple."""
    if g["fam"] in LEVEL_FAMS: g["tp"] = round(min(max(g["tp"], RR[0]), RR[1]), 3)
    else: g["tp"] = round(min(max(g["tp"] / g["sl"], RR[0]), RR[1]) * g["sl"], 3)
    return g

def rr(g):
    return round(g["tp"] if g["fam"] in LEVEL_FAMS else g["tp"] / g["sl"], 2)

IND_OF = ["Donchian", "Bollinger", "EMA cross", "Opening range", "RSI + EMA", "VWAP (time-weighted)", "Momentum", "EMA pullback"]
def indicators_text(g):
    from . import indicators, levels
    f = g["fam"]
    if f == COMBO: return indicators.text(g)
    if f in LEVEL_FAMS:
        m = levels.level_mask(g); nm = [levels.LEVEL_NAMES[k] for k in range(6) if (m >> k) & 1]
        base = f"{' / '.join(nm)} level{'s' if len(nm) > 1 else ''} {['retest', 'sweep', 'pullback'][f - 8]}"
        x = indicators.text(g); return base + (f" + {x}" if x else "")
    return IND_OF[f]

def signature(g):
    """Indicator-combo identity used by the floor's diversity rule."""
    from . import indicators
    f = g["fam"]
    if f == COMBO: return indicators.signature(indicators.conds(g))
    if f in LEVEL_FAMS:
        x = indicators.signature(indicators.conds(g)); return FAMILIES[f] + (f"+{x}" if x else "")
    return IND_OF[f]
DISCRETE = ("tf", "trend", "side", "flip", "ss", "slen")

def random_genome(rng, fams=None):
    fams = fams or NEW_MAIN_FAMS
    g = {"fam": int(fams[int(rng.integers(0, len(fams)))])}
    for k, (lo, hi, isint) in {**COMMON, **FAM[g["fam"]]}.items():
        g[k] = int(rng.integers(lo, hi + 1)) if isint else round(float(rng.uniform(lo, hi)), 3)
    if rng.random() < 0.4: g["slen"] = 24    # some bots trade all hours
    if rng.random() < 0.5: g["vlo"], g["vw"] = 0.3, 3.0   # half have no vol filter
    if rng.random() < 0.5: g["trend"] = 0
    return rr_fix(g)

def clip_gene(k, v, spec):
    lo, hi, isint = spec
    v = min(max(v, lo), hi)
    return int(round(v)) if isint else round(float(v), 3)

def fill_genes(g, rng=None):
    """Genomes saved before a gene existed get its neutral default (lvm: primary level only)."""
    g = dict(g)
    for k, (lo, hi, isint) in FAM.get(g["fam"], {}).items():
        if k not in g: g[k] = (1 << int(g["p1"])) if k == "lvm" else (lo if rng is None else (int(rng.integers(lo, hi + 1)) if isint else round(float(rng.uniform(lo, hi)), 3)))
    return g

def mutate(g, rng, rate=0.25, scale=0.15):
    g = fill_genes(g, rng)
    if rng.random() < 0.05:          # family jump
        n = random_genome(rng, LEVEL_FAMS if g["fam"] in LEVEL_FAMS else None); n.update({k: g[k] for k in COMMON}); return rr_fix(n)
    for k, spec in {**COMMON, **FAM[g["fam"]]}.items():
        if rng.random() < rate:
            lo, hi, isint = spec
            if k == "lvm": g[k] = (g[k] ^ (1 << int(rng.integers(0, 6)))) or (1 << int(g["p1"])); continue
            if k in ("tf", "trend", "side", "flip", "ss") or k in DISCRETE_C or (g["fam"] in (3, 5, 8, 9, 10) and k == "p1" and isint) or (g["fam"] == 5 and k == "p2"):
                g[k] = int(rng.integers(lo, hi + 1))
            else:
                g[k] = clip_gene(k, g[k] + rng.normal(0, scale * (hi - lo)), spec)
    return rr_fix(g)

def crossover(a, b, rng):
    a = fill_genes(a, rng); b = fill_genes(b, rng)
    child = {}
    fam_src = a if rng.random() < 0.5 else b
    child["fam"] = fam_src["fam"]
    for k in COMMON: child[k] = (a if rng.random() < 0.5 else b)[k]
    for k in FAM[child["fam"]]: child[k] = fam_src[k]
    if "i1" in a and "i1" in b and child["fam"] in (a["fam"], b["fam"]):      # swap whole indicator conditions between parents
        for j in (1, 2, 3):
            src = a if rng.random() < 0.5 else b
            for x in "inm": child[f"{x}{j}"] = src[f"{x}{j}"]
        child["nc"] = (a if rng.random() < 0.5 else b)["nc"]; child["lg"] = (a if rng.random() < 0.5 else b)["lg"]
        lo, hi, _ = FAM[child["fam"]]["nc"]; child["nc"] = min(max(child["nc"], lo), hi)
    return rr_fix(child)

def gkey(g):
    return tuple(sorted(g.items()))

def hold_bars(g):
    return max(1, int(round(g["hold_h"] * 60 / TF_MIN[g["tf"]])))

def describe(g):
    f = FAMILIES[g["fam"]]
    fp = indicators_text(g) if g["fam"] in LEVEL_FAMS + [COMBO] else ",".join(f"{k}={g[k]}" for k in FAM[g["fam"]])
    sess = "all hours" if g["slen"] >= 24 else f"{g['ss']:02d}:00+{g['slen']}h UTC"
    stop = f"stop {g['sl']}ATR beyond level" if g["fam"] in LEVEL_FAMS else f"SL {g['sl']}ATR TP {g['tp']}ATR"
    return (f"{f}[{fp}] {TF_NAME[g['tf']]} {stop} R:R 1:{rr(g)} hold<={g.get('hold_h', 8)}h risk {g['risk']}% | {sess} | vol {g['vlo']}-{round(g['vlo']+g['vw'],2)}x"
            f" | trend={['off','with','against'][g['trend']]} side={['both','long','short'][g['side']]}{' flip-exit' if g['flip'] else ''}")

# ---------------- signals ----------------
@njit(cache=True)
def _orb(h, l, c, hour, minute, day, start_h, nbars, win_h):
    n = len(c); sig = np.zeros(n, np.int8)
    rh = -1e18; rl = 1e18; cnt = 0; cur = -1; done_l = False; done_s = False; end_bar_hour = -1.0
    for i in range(n):
        if day[i] != cur:
            cur = day[i]; cnt = 0; rh = -1e18; rl = 1e18; done_l = False; done_s = False
        hh = hour[i] + minute[i] / 60.0
        if cnt < nbars:
            if hour[i] == start_h or (cnt > 0):
                rh = max(rh, h[i]); rl = min(rl, l[i]); cnt += 1
                if cnt == nbars: end_bar_hour = hh
            continue
        rel = hh - end_bar_hour
        if rel < 0: rel += 24
        if rel > win_h: continue
        if not done_l and c[i] > rh: sig[i] = 1; done_l = True
        elif not done_s and c[i] < rl: sig[i] = -1; done_s = True
    return sig

def _edge(x):
    return x & ~np.r_[False, x[:-1]]

def signals(g, t, ind):
    c = t["c"]; f = g["fam"]
    with np.errstate(invalid="ignore"):
        if f == 0:
            hh, ll = ind.get("hh", g["p1"]), ind.get("ll", g["p1"])
            s = _edge(c > hh).astype(np.int8) - _edge(c < ll).astype(np.int8)
        elif f == 1:
            z = ind.get("z", g["p1"]); k = g["p2"]; zp = np.r_[0, z[:-1]]
            s = ((z < -k) & (zp >= -k)).astype(np.int8) - ((z > k) & (zp <= k)).astype(np.int8)
        elif f == 2:
            fa = ind.get("ema", g["p1"]); sl = ind.get("ema", max(g["p1"] + 1, int(g["p1"] * g["p2"])))
            d = np.sign(fa - sl); dp = np.r_[0, d[:-1]]
            s = ((d > 0) & (dp <= 0)).astype(np.int8) - ((d < 0) & (dp >= 0)).astype(np.int8)
        elif f == 3:
            nb = max(1, int(round(g["p2"] / TF_MIN[g["tf"]])))
            s = _orb(t["h"], t["l"], c, t["hour"], t["minute"], t["day"], g["p1"], nb, float(g["slen"]))
        elif f == 4:
            r = ind.get("rsi", g["p1"]); e = ind.get("ema", g["p3"]); rp = np.r_[50, r[:-1]]
            lo = g["p2"]; hi = 100 - lo
            s = ((r < lo) & (rp >= lo) & (c > e)).astype(np.int8) - ((r > hi) & (rp <= hi) & (c < e)).astype(np.int8)
        elif f == 5:
            v = ind.get("twap", g["p2"]); a = ind.get("atr"); dev = (c - v) / a; dp = np.r_[0, dev[:-1]]; k = g["p1"]
            s = ((dev < -k) & (dp >= -k)).astype(np.int8) - ((dev > k) & (dp <= k)).astype(np.int8)
        elif f == 6:
            m = ind.get("mom", g["p1"]); a = ind.get("atr"); k = g["p2"]
            s = _edge(m > k * a).astype(np.int8) - _edge(m < -k * a).astype(np.int8)
        elif f == COMBO:
            from . import indicators
            cb = indicators.combined(g, ind); prev = np.r_[0, cb[:-1]]
            s = np.where((cb != 0) & (cb != prev), cb, 0)
        elif f >= 8:
            raise ValueError("level families need levels.raw(); use run() or live_level_signal()")
        else:
            ef = ind.get("ema", g["p1"]); es = ind.get("ema", max(g["p1"] + 2, int(g["p1"] * g["p2"])))
            up = (ef > es) & (t["l"] <= ef) & (c > ef); dn = (ef < es) & (t["h"] >= ef) & (c < ef)
            s = _edge(up).astype(np.int8) - _edge(dn).astype(np.int8)
        s = s.astype(np.int8)
    return apply_filters(g, t, ind, s)

def apply_filters(g, t, ind, s):
    c = t["c"]; f = g["fam"]
    with np.errstate(invalid="ignore"):
        if f != 3 and g["slen"] < 24:
            rel = (t["hour"] - g["ss"]) % 24
            s[rel >= g["slen"]] = 0
        ar = ind.get("atr_ratio")
        s[~((ar >= g["vlo"]) & (ar <= g["vlo"] + g["vw"]))] = 0
        if g["trend"]:
            e = ind.get("ema", 200); tr = np.sign(c - e).astype(np.int8)
            s[(s != (tr if g["trend"] == 1 else -tr))] = 0
        if g["side"] == 1: s[s < 0] = 0
        elif g["side"] == 2: s[s > 0] = 0
    return s

# ---------------- simulator ----------------
# Entry rules shared by backtest and live (quant research memo, 3 Nov):
MIN_STOP_COST_MULT = 8.0     # stop distance must be >= 8x the round-trip trading cost (spread + commission + slippage, both legs)
MAX_COST_ATR = 0.3           # skip entries where round-trip cost > 0.3x the 14-bar ATR at the signal bar
# gold entries 20:00-22:59 UTC are blocked (Market 'blk' array / live entry_blocked)

def entry_blocked(sym, entry_ts):
    h = int(entry_ts // 3600) % 24
    return sym == "XAUUSD" and 20 <= h <= 22

def entry_ok(dist, rt, atr):
    """dist = stop distance (price), rt = round-trip cost (price), atr = 14-bar ATR at the signal bar."""
    return dist > 0 and rt <= MAX_COST_ATR * atr and dist >= MIN_STOP_COST_MULT * rt

@njit(cache=True)
def simulate(o, h, l, c, cost, slip, ts, roll, blk, at, sig, a, b, sl_m, tp_m, hold_sec, flip, swap, min_stop, max_cost_atr):
    """Signal at close of bar i -> market entry at open of i+1. Stop/TP from ATR at signal bar.
    Intrabar: if both stop and TP in same bar -> stop (conservative). Gaps through levels fill at open.
    The entry bar's own range is checked against stop/target after the open fill (review B1). No re-entry on a bar that
    had an intrabar exit (review B2). Max hold is wall-clock seconds, like live (review B4).
    Costs: per-side spread/2+commission on both legs, slippage on entry and stop/time exits, swap per broker rollover (review S1).
    Entry filters: no entry on blocked bars (gold 20-23 UTC), round-trip cost <= max_cost_atr x ATR, stop >= min_stop x round-trip cost.
    MAE: worst adverse excursion during the trade (bar lows/highs vs entry incl. exit costs), in R (<=0)."""
    m = 2
    for i in range(max(a, 0), b):
        if sig[i] != 0: m += 1
    ei = np.empty(m, np.int64); xi = np.empty(m, np.int64); dr = np.empty(m, np.int8)
    ep = np.empty(m); xp = np.empty(m); R = np.empty(m); rs = np.empty(m, np.int8); rk = np.empty(m); mae = np.empty(m)
    n = 0; pos = 0; entry = 0.0; stop = 0.0; tgt = 0.0; risk = 0.0; ent_i = 0; worst = 0.0
    start = a + 1 if a > 0 else 1
    for i in range(start, b):
        intrabar = False
        if pos != 0:
            px = 0.0; why = -1
            if pos == 1:
                if o[i] <= stop: px = o[i]; why = 0
                elif o[i] >= tgt: px = o[i]; why = 1
                elif l[i] <= stop: px = stop; why = 0; intrabar = True
                elif h[i] >= tgt: px = tgt; why = 1; intrabar = True
                adv = min(l[i], o[i]) if why != 1 else o[i]
            else:
                if o[i] >= stop: px = o[i]; why = 0
                elif o[i] <= tgt: px = o[i]; why = 1
                elif h[i] >= stop: px = stop; why = 0; intrabar = True
                elif l[i] <= tgt: px = tgt; why = 1; intrabar = True
                adv = max(h[i], o[i]) if why != 1 else o[i]
            if why == 0: adv = px
            ex = pos * ((adv - pos * (cost[i] + slip[i])) - entry)
            if ex < worst: worst = ex
            if why < 0 and flip == 1 and sig[i - 1] == -pos:
                px = o[i]; why = 3
            if why < 0 and ts[i] >= ts[ent_i] + hold_sec:
                px = o[i]; why = 2
            if why < 0 and i == b - 1:
                px = c[i]; why = 4
            if why >= 0:
                fill = px - pos * (cost[i] + (slip[i] if why != 1 else 0.0))
                pnl = pos * (fill - entry) - (roll[i] - roll[ent_i]) * swap * entry
                ei[n] = ent_i; xi[n] = i; dr[n] = pos; ep[n] = entry; xp[n] = fill; R[n] = pnl / risk; rs[n] = why; rk[n] = risk
                mae[n] = min(worst / risk, R[n], 0.0); n += 1
                pos = 0
        if pos == 0 and not intrabar and i < b - 1 and sig[i - 1] != 0 and at[i - 1] > 0 and blk[i] == 0:
            d = sl_m * at[i - 1]; rt = 2 * (cost[i] + slip[i])
            if rt <= max_cost_atr * at[i - 1] and d >= min_stop * rt:
                pos = sig[i - 1]
                entry = o[i] + pos * (cost[i] + slip[i])
                stop = o[i] - pos * d; tgt = o[i] + pos * tp_m * at[i - 1]
                risk = d + cost[i] + slip[i]   # planned loss incl. entry costs = 1R
                ent_i = i; worst = 0.0
                px = 0.0; why = -1                       # entry bar's own range after the open fill (stop wins ties)
                if pos == 1:
                    if l[i] <= stop: px = stop; why = 0
                    elif h[i] >= tgt: px = tgt; why = 1
                else:
                    if h[i] >= stop: px = stop; why = 0
                    elif l[i] <= tgt: px = tgt; why = 1
                if why >= 0:
                    fill = px - pos * (cost[i] + (slip[i] if why != 1 else 0.0))
                    ei[n] = ent_i; xi[n] = i; dr[n] = pos; ep[n] = entry; xp[n] = fill; R[n] = pos * (fill - entry) / risk; rs[n] = why; rk[n] = risk
                    mae[n] = min(R[n], 0.0); n += 1
                    pos = 0
    return ei[:n].copy(), xi[:n].copy(), dr[:n].copy(), ep[:n].copy(), xp[:n].copy(), R[:n].copy(), rs[:n].copy(), rk[:n].copy(), mae[:n].copy()

def metrics(R, risk_pct=1.0):
    n = len(R)
    if n == 0:
        return dict(n=0, net_r=0.0, ret_pct=0.0, max_dd_pct=0.0, pf=0.0, win=0.0, exp_r=0.0, t=0.0)
    eq = np.cumprod(1 + R * risk_pct / 100)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    dd = (eq / peak - 1).min() * 100
    gp = R[R > 0].sum(); gl = -R[R < 0].sum()
    sd = R.std() if n > 1 else 1.0
    return dict(n=int(n), net_r=round(float(R.sum()), 2), ret_pct=round(float((eq[-1] - 1) * 100), 2),
                max_dd_pct=round(float(dd), 2), pf=round(float(gp / gl) if gl > 0 else 99.0, 3),
                win=round(float((R > 0).mean() * 100), 1), exp_r=round(float(R.mean()), 4),
                t=round(float(R.mean() / (sd if sd > 0 else 1) * np.sqrt(n)), 3))

def level_signal(g, t, ind, sym):
    from . import levels
    from . import indicators
    sig, stp = levels.raw(g, t, ind, sym); sig = sig.copy()
    cb = indicators.combined(g, ind)
    if cb is not None: sig[cb != sig] = 0           # indicator confirmation must agree with the level trade's direction
    return apply_filters(g, t, ind, sig), stp

def _post(t, ei, xi, dr, ep, xp, R, rs, rk, mae):
    """Drift-adjusted R (anti-beta) with a CAUSAL drift estimate known at the signal bar (review B3), and the trading cost
    actually charged per trade in R (entry spread+comm+slip, exit spread+comm, exit slip only on non-target exits)."""
    if len(R):
        mu = t["mu"][np.maximum(ei - 1, 0)]
        Ra = R - dr * ep * (np.exp(mu * (xi - ei)) - 1) / rk
        cr = (t["cost"][ei] + t["slip"][ei] + t["cost"][xi] + np.where(rs != 1, t["slip"][xi], 0.0)) / rk
    else: Ra = R; cr = R
    return dict(ei=ei, xi=xi, dir=dr, ep=ep, xp=xp, R=R, Ra=np.ascontiguousarray(Ra), reason=rs, mae=mae, ts=t["ts"], cost_r=cr)

def run(g, mk, inds, a_ts, b_ts):
    """Backtest genome g on market mk over [a_ts,b_ts). Returns trade dict."""
    t = mk.tf[g["tf"]]; ind = inds[g["tf"]]
    a, b = mk.window(g["tf"], a_ts, b_ts)
    hold_sec = hold_bars(g) * TF_MIN[g["tf"]] * 60
    if g["fam"] in LEVEL_FAMS:
        from . import levels
        s, stp = level_signal(g, t, ind, mk.sym)
        out = levels.simulate_lv(t["o"], t["h"], t["l"], t["c"], t["cost"], t["slip"], t["ts"], t["roll"], t["blk"], ind.get("atr"), s, stp,
                                 a, b, float(g["tp"]), hold_sec, t["swap"], levels.MIN_TP_COST_MULT, levels.MAX_STOP_ATR, MIN_STOP_COST_MULT, MAX_COST_ATR)
        return _post(t, *out)
    s = signals(g, t, ind)
    out = simulate(t["o"], t["h"], t["l"], t["c"], t["cost"], t["slip"], t["ts"], t["roll"], t["blk"], ind.get("atr"), s,
                   a, b, g["sl"], g["tp"], hold_sec, g["flip"], t["swap"], MIN_STOP_COST_MULT, MAX_COST_ATR)
    return _post(t, *out)
