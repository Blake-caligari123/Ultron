"""Indicators, strategy genome -> signals, numba trade simulator, metrics."""
import numpy as np, pandas as pd
from numba import njit

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

class Ind:
    """per-(market,tf) indicator cache"""
    def __init__(self, t):
        self.t = t; self.cache = {}
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
            if len(self.cache) > 300: self.cache.clear()
            self.cache[k] = v
        return v

# ---------------- genome ----------------
FAMILIES = ["donchian_breakout", "bollinger_reversion", "ema_trend_cross", "session_orb", "rsi_pullback"]
# gene: (lo, hi, is_int)
COMMON = {"tf": (0, 1, 1), "sl": (0.5, 6.0, 0), "tp": (0.5, 12.0, 0), "hold": (3, 300, 1),
          "ss": (0, 23, 1), "slen": (1, 24, 1), "vlo": (0.3, 1.2, 0), "vw": (0.4, 3.0, 0),
          "trend": (0, 2, 1), "side": (0, 2, 1), "flip": (0, 1, 1)}
FAM = {0: {"p1": (10, 150, 1)},
       1: {"p1": (10, 100, 1), "p2": (1.0, 3.5, 0)},
       2: {"p1": (3, 50, 1), "p2": (1.5, 6.0, 0)},
       3: {"p1": (0, 23, 1), "p2": (1, 8, 1)},
       4: {"p1": (2, 30, 1), "p2": (5, 40, 1), "p3": (50, 300, 1)}}

def random_genome(rng):
    g = {"fam": int(rng.integers(0, len(FAMILIES)))}
    for k, (lo, hi, isint) in {**COMMON, **FAM[g["fam"]]}.items():
        g[k] = int(rng.integers(lo, hi + 1)) if isint else round(float(rng.uniform(lo, hi)), 3)
    if rng.random() < 0.5: g["slen"] = 24    # half the bots trade all hours
    if rng.random() < 0.5: g["vlo"], g["vw"] = 0.3, 3.0   # half have no vol filter
    if rng.random() < 0.5: g["trend"] = 0
    return g

def clip_gene(k, v, spec):
    lo, hi, isint = spec
    v = min(max(v, lo), hi)
    return int(round(v)) if isint else round(float(v), 3)

def mutate(g, rng, rate=0.25, scale=0.15):
    g = dict(g)
    if rng.random() < 0.05:          # family jump
        n = random_genome(rng); n.update({k: g[k] for k in COMMON}); return n
    for k, spec in {**COMMON, **FAM[g["fam"]]}.items():
        if rng.random() < rate:
            lo, hi, isint = spec
            if k in ("tf", "trend", "side", "flip", "ss"):
                g[k] = int(rng.integers(lo, hi + 1))
            else:
                g[k] = clip_gene(k, g[k] + rng.normal(0, scale * (hi - lo)), spec)
    return g

def crossover(a, b, rng):
    child = {}
    fam_src = a if rng.random() < 0.5 else b
    child["fam"] = fam_src["fam"]
    for k in COMMON: child[k] = (a if rng.random() < 0.5 else b)[k]
    for k in FAM[child["fam"]]: child[k] = fam_src[k]
    return child

def gkey(g):
    return tuple(sorted(g.items()))

def describe(g):
    f = FAMILIES[g["fam"]]
    tf = ["15m", "1h"][g["tf"]]
    fp = ",".join(f"{k}={g[k]}" for k in FAM[g["fam"]])
    sess = "all hours" if g["slen"] >= 24 else f"{g['ss']:02d}:00+{g['slen']}h UTC"
    return (f"{f}[{fp}] {tf} SL {g['sl']}ATR TP {g['tp']}ATR hold<={g['hold']} | {sess} | vol {g['vlo']}-{round(g['vlo']+g['vw'],2)}x ATR/avg"
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

def signals(g, t, ind):
    c = t["c"]; f = g["fam"]
    with np.errstate(invalid="ignore"):
        if f == 0:
            hh, ll = ind.get("hh", g["p1"]), ind.get("ll", g["p1"])
            up = c > hh; dn = c < ll
            s = (up & ~np.r_[False, up[:-1]]).astype(np.int8) - (dn & ~np.r_[False, dn[:-1]]).astype(np.int8)
        elif f == 1:
            z = ind.get("z", g["p1"]); k = g["p2"]; zp = np.r_[0, z[:-1]]
            s = ((z < -k) & (zp >= -k)).astype(np.int8) - ((z > k) & (zp <= k)).astype(np.int8)
        elif f == 2:
            fa = ind.get("ema", g["p1"]); sl = ind.get("ema", max(g["p1"] + 1, int(g["p1"] * g["p2"])))
            d = np.sign(fa - sl); dp = np.r_[0, d[:-1]]
            s = ((d > 0) & (dp <= 0)).astype(np.int8) - ((d < 0) & (dp >= 0)).astype(np.int8)
        elif f == 3:
            s = _orb(t["h"], t["l"], c, t["hour"], t["minute"], t["day"], g["p1"], g["p2"] if g["tf"] == 0 else max(1, g["p2"] // 4), float(g["slen"]))
        else:
            r = ind.get("rsi", g["p1"]); e = ind.get("ema", g["p3"]); rp = np.r_[50, r[:-1]]
            lo = g["p2"]; hi = 100 - lo
            s = ((r < lo) & (rp >= lo) & (c > e)).astype(np.int8) - ((r > hi) & (rp <= hi) & (c < e)).astype(np.int8)
        s = s.astype(np.int8)
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
@njit(cache=True)
def simulate(o, h, l, c, cost, slip, day, at, sig, a, b, sl_m, tp_m, hold, flip, swap):
    """Signal at close of bar i -> market entry at open of i+1. Stop/TP from ATR at signal bar.
    Intrabar: if both stop and TP in same bar -> stop (conservative). Gaps through levels fill at open.
    Costs: per-side spread/2+commission on both legs, slippage on entry and stop/time exits, swap per day held.
    Returns arrays of trades: entry_i, exit_i, dir, entry_px, exit_px, R, reason(0 sl,1 tp,2 time,3 flip,4 end)."""
    m = b - a
    ei = np.empty(m, np.int64); xi = np.empty(m, np.int64); dr = np.empty(m, np.int8)
    ep = np.empty(m); xp = np.empty(m); R = np.empty(m); rs = np.empty(m, np.int8); rk = np.empty(m)
    n = 0; pos = 0; entry = 0.0; stop = 0.0; tgt = 0.0; risk = 0.0; ent_i = 0
    start = a + 1 if a > 0 else 1
    for i in range(start, b):
        if pos != 0:
            px = 0.0; why = -1
            if pos == 1:
                if o[i] <= stop: px = o[i]; why = 0
                elif o[i] >= tgt: px = o[i]; why = 1
                elif l[i] <= stop: px = stop; why = 0
                elif h[i] >= tgt: px = tgt; why = 1
            else:
                if o[i] >= stop: px = o[i]; why = 0
                elif o[i] <= tgt: px = o[i]; why = 1
                elif h[i] >= stop: px = stop; why = 0
                elif l[i] <= tgt: px = tgt; why = 1
            if why < 0 and flip == 1 and sig[i - 1] == -pos:
                px = o[i]; why = 3
            if why < 0 and i - ent_i >= hold:
                px = o[i]; why = 2
            if why < 0 and i == b - 1:
                px = c[i]; why = 4
            if why >= 0:
                fill = px - pos * (cost[i] + (slip[i] if why != 1 else 0.0))
                days = day[i] - day[ent_i]
                pnl = pos * (fill - entry) - days * swap * entry
                ei[n] = ent_i; xi[n] = i; dr[n] = pos; ep[n] = entry; xp[n] = fill; R[n] = pnl / risk; rs[n] = why; rk[n] = risk; n += 1
                pos = 0
        if pos == 0 and i < b - 1 and sig[i - 1] != 0 and at[i - 1] > 0:
            pos = sig[i - 1]
            entry = o[i] + pos * (cost[i] + slip[i])
            d = sl_m * at[i - 1]
            stop = o[i] - pos * d; tgt = o[i] + pos * tp_m * at[i - 1]
            risk = d + cost[i] + slip[i]   # planned loss incl. entry costs = 1R
            ent_i = i
    return ei[:n], xi[:n], dr[:n], ep[:n], xp[:n], R[:n], rs[:n], rk[:n]

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

def run(g, mk, inds, a_ts, b_ts):
    """Backtest genome g on market mk over [a_ts,b_ts). Returns trade dict."""
    t = mk.tf[g["tf"]]; ind = inds[g["tf"]]
    a, b = mk.window(g["tf"], a_ts, b_ts)
    s = signals(g, t, ind)
    hold = g["hold"] if g["tf"] == 1 else g["hold"] * 2   # hold measured ~ in 30-min units on 15m
    ei, xi, dr, ep, xp, R, rs, rk = simulate(t["o"], t["h"], t["l"], t["c"], t["cost"], t["slip"], t["day"], ind.get("atr"), s,
                                       a, b, g["sl"], g["tp"], hold, g["flip"], t["swap"])
    # drift-adjusted R: remove the passive buy-and-hold drift of this window from each trade (anti-beta)
    c = t["c"]; mu = (np.log(c[b - 1]) - np.log(c[a])) / max(b - a, 1) if b > a else 0.0
    Ra = R - dr * ep * (np.exp(mu * (xi - ei)) - 1) / rk if len(R) else R
    # copy: numba slices would otherwise pin the full-size scratch buffers in memory
    return dict(ei=ei.copy(), xi=xi.copy(), dir=dr.copy(), ep=ep.copy(), xp=xp.copy(), R=R.copy(), Ra=np.ascontiguousarray(Ra), reason=rs.copy(), ts=t["ts"])
