"""Scalp Pit level families: 1m entries around higher-timeframe levels (PAPER research only).
Levels (all known at the bar's open, no lookahead):
  0 previous completed 1h high/low   1 previous completed 4h high/low   2 previous UTC day high/low
  3 Asia session (00-07 UTC) high/low, usable from 07:00 UTC   4 London (07-12 UTC) high/low, usable from 12:00 UTC
  5 nearest round numbers above/below the previous close (gold $10, bitcoin $500)
A bot watches one or more of these at once (gene lvm, a bitmask; p1 = its primary level). After an entry it stays armed:
each fresh touch (retest) or pullback of the same level within the window is a new setup, on both sides (longs above
resistance breaks, shorts below support breaks).
Families (signal on a bar close (1m or 5m), market entry next bar open):
  8 lvl_retest  : close breaks the level, then within p2 bars price comes back to within p3 ATR of it and closes on the break side.
  9 lvl_sweep   : a wick pierces the level (by at most 3*p3 ATR) but the bar closes back inside -> fade it.
 10 lvl_pullback: after a break, the first 1m pullback, then a close above the prior bar's high (below its low for shorts).
Stop: just beyond the level / sweep wick / pullback extreme by sl*ATR. Target: tp x R. Max hold: hold_h.
A signal is skipped when the target is closer than 3x the round-trip trading cost, the stop is > 3 ATR away, the stop is < 8x the
round-trip cost, the round-trip cost is > 0.3 ATR, or (gold) the entry falls 20:00-22:59 UTC."""
import numpy as np, pandas as pd
from numba import njit

LEVEL_NAMES = ["1-hour", "4-hour", "previous-day", "Asia-session", "London-session", "round-number"]
ROUND = {"XAUUSD": 10.0, "BTCUSD": 500.0}
MIN_TP_COST_MULT = 3.0; MAX_STOP_ATR = 3.0

def _prev_period(s_h, s_l, idx, rule):
    H = s_h.resample(rule).max().dropna(); L = s_l.resample(rule).min().dropna()
    key = idx.floor(rule)
    return H.shift(1).reindex(key).values, L.shift(1).reindex(key).values

def _session(s_h, s_l, idx, h0, h1):
    m = (idx.hour >= h0) & (idx.hour < h1)
    H = s_h[m].resample("1D").max().dropna(); L = s_l[m].resample("1D").min().dropna()
    avail = (H.index + pd.Timedelta(hours=h1)).as_unit("s").asi8
    bt = idx.as_unit("s").asi8; j = np.searchsorted(avail, bt, side="right") - 1
    hv = np.where(j >= 0, H.values[np.clip(j, 0, None)], np.nan); lv = np.where(j >= 0, L.values[np.clip(j, 0, None)], np.nan)
    return hv, lv

def level_arrays(t, sym):
    if "_lv" in t: return t["_lv"]
    idx = t["idx"]; s_h = pd.Series(t["h"], idx); s_l = pd.Series(t["l"], idx); n = len(idx)
    LH = np.full((6, n), np.nan); LL = np.full((6, n), np.nan)
    for k, rule in enumerate(("1h", "4h", "1D")): LH[k], LL[k] = _prev_period(s_h, s_l, idx, rule)
    LH[3], LL[3] = _session(s_h, s_l, idx, 0, 7); LH[4], LL[4] = _session(s_h, s_l, idx, 7, 12)
    st = ROUND[sym]; pc = np.r_[t["c"][0], t["c"][:-1]]
    LH[5] = np.ceil(pc / st) * st; LL[5] = np.floor(pc / st) * st
    LL[5] = np.where(LL[5] == LH[5], LL[5] - st, LL[5])
    t["_lv"] = (LH, LL); return t["_lv"]

@njit(cache=True)
def lv_signals(o, h, l, c, H, L, at, fam, win, tol, sbuf):
    n = len(c); sig = np.zeros(n, np.int8); stp = np.zeros(n)
    bu = -1; bd = -1; lu = 0.0; ld = 0.0; pbu = False; pbd = False; plu = 0.0; pld = 0.0; wu = False; wd = False
    for i in range(1, n):
        a = at[i]
        if not (a > 0): continue
        lg = False; sh = False; sl_l = 0.0; sl_s = 0.0
        hi = H[i]; lo = L[i]
        if fam == 9:
            if hi == hi and h[i] > hi and c[i] < hi and c[i - 1] < hi and h[i] - hi <= 3 * tol * a:
                sh = True; sl_s = h[i] + sbuf * a
            if lo == lo and l[i] < lo and c[i] > lo and c[i - 1] > lo and lo - l[i] <= 3 * tol * a:
                lg = True; sl_l = l[i] - sbuf * a
        else:
            # resistance side -> longs
            if bu >= 0:
                if i - bu > win or c[i] < lu - tol * a: bu = -1
                elif fam == 8:
                    if wu:                                  # after a retest entry, price must leave the touch zone first
                        if l[i] > lu + tol * a: wu = False
                    elif l[i] <= lu + tol * a and c[i] > lu and i > bu:
                        lg = True; sl_l = min(l[i], lu) - sbuf * a; bu = i; wu = True     # stay armed: next touch = fresh entry
                else:
                    if not pbu and c[i] < c[i - 1]: pbu = True; plu = l[i]
                    elif pbu:
                        plu = min(plu, l[i])
                        if c[i] > h[i - 1] and c[i] > lu:
                            lg = True; sl_l = min(plu, lu) - sbuf * a; bu = i; pbu = False   # stay armed for the next pullback
            if bu < 0 and hi == hi and c[i] > hi and c[i - 1] <= hi and not lg:
                bu = i; lu = hi; pbu = False; wu = False
            # support side -> shorts
            if bd >= 0:
                if i - bd > win or c[i] > ld + tol * a: bd = -1
                elif fam == 8:
                    if wd:
                        if h[i] < ld - tol * a: wd = False
                    elif h[i] >= ld - tol * a and c[i] < ld and i > bd:
                        sh = True; sl_s = max(h[i], ld) + sbuf * a; bd = i; wd = True
                else:
                    if not pbd and c[i] > c[i - 1]: pbd = True; pld = h[i]
                    elif pbd:
                        pld = max(pld, h[i])
                        if c[i] < l[i - 1] and c[i] < ld:
                            sh = True; sl_s = max(pld, ld) + sbuf * a; bd = i; pbd = False
            if bd < 0 and lo == lo and c[i] < lo and c[i - 1] >= lo and not sh:
                bd = i; ld = lo; pbd = False; wd = False
        if lg and not sh: sig[i] = 1; stp[i] = sl_l
        elif sh and not lg: sig[i] = -1; stp[i] = sl_s
    return sig, stp

@njit(cache=True)
def simulate_lv(o, h, l, c, cost, slip, ts, roll, blk, at, sig, stp, a, b, tpR, hold_sec, swap, min_mult, max_stop, min_stop, max_cost_atr):
    """Same fill/cost rules as engine.simulate (entry-bar check, no same-bar re-entry, wall-clock hold, broker rollovers,
    entry filters), with the stop at the level/wick (stp) and the target at tpR x the stop distance."""
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
                adv = min(l[i], o[i])
            else:
                if o[i] >= stop: px = o[i]; why = 0
                elif o[i] <= tgt: px = o[i]; why = 1
                elif h[i] >= stop: px = stop; why = 0; intrabar = True
                elif l[i] <= tgt: px = tgt; why = 1; intrabar = True
                adv = max(h[i], o[i])
            ex = pos * ((adv - pos * (cost[i] + slip[i])) - entry)
            if ex < worst: worst = ex
            if why < 0 and ts[i] >= ts[ent_i] + hold_sec: px = o[i]; why = 2
            if why < 0 and i == b - 1: px = c[i]; why = 4
            if why >= 0:
                fill = px - pos * (cost[i] + (slip[i] if why != 1 else 0.0))
                pnl = pos * (fill - entry) - (roll[i] - roll[ent_i]) * swap * entry
                ei[n] = ent_i; xi[n] = i; dr[n] = pos; ep[n] = entry; xp[n] = fill; R[n] = pnl / risk; rs[n] = why; rk[n] = risk
                mae[n] = min(worst / risk, R[n], 0.0); n += 1
                pos = 0
        if pos == 0 and not intrabar and i < b - 1 and sig[i - 1] != 0 and at[i - 1] > 0 and blk[i] == 0:
            p = sig[i - 1]; d = p * (o[i] - stp[i - 1]); rt = 2 * (cost[i] + slip[i])
            if d > 0 and d <= max_stop * at[i - 1] and tpR * d >= min_mult * rt and d >= min_stop * rt and rt <= max_cost_atr * at[i - 1]:
                pos = p; entry = o[i] + pos * (cost[i] + slip[i])
                stop = stp[i - 1]; tgt = o[i] + pos * tpR * d
                risk = d + cost[i] + slip[i]; ent_i = i; worst = 0.0
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

def level_mask(g):
    """Levels a bot watches: gene lvm (bitmask over LEVEL_NAMES, 1-63) plus its primary level p1. Old genomes: p1 only."""
    return (int(g.get("lvm", 0)) | (1 << int(g["p1"]))) & 63

def raw(g, t, ind, sym):
    """Signals from every level in the bot's mask. Same-bar signals that agree keep the nearest stop; opposite ones cancel."""
    LH, LL = level_arrays(t, sym); at = ind.get("atr"); n = len(t["c"])
    up = np.zeros(n, bool); dn = np.zeros(n, bool); su = np.full(n, -np.inf); sd = np.full(n, np.inf)
    m = level_mask(g)
    for k in range(6):
        if not (m >> k) & 1: continue
        sg, sp = lv_signals(t["o"], t["h"], t["l"], t["c"], LH[k], LL[k], at, int(g["fam"]), int(g["p2"]), float(g["p3"]), float(g["sl"]))
        L = sg > 0; S = sg < 0
        up |= L; dn |= S; su = np.where(L, np.maximum(su, sp), su); sd = np.where(S, np.minimum(sd, sp), sd)
    sig = np.zeros(n, np.int8); stp = np.zeros(n)
    lg = up & ~dn; sh = dn & ~up
    sig[lg] = 1; stp[lg] = su[lg]; sig[sh] = -1; stp[sh] = sd[sh]
    return sig, stp
