"""Indicator library for combination entry genes. Each indicator gives a per-bar STATE: +1 bullish, -1 bearish, 0 neutral,
computed only from bars up to and including the current close (no lookahead). A 'fade' condition flips the sign.
No volume indicators (OBV, true VWAP): neither price feed carries real traded volume, so 'VWAP' is a time-weighted
daily average price and OBV is left out rather than faked."""
import numpy as np, pandas as pd
from numba import njit

NAMES = ["RSI", "MACD", "EMA cross", "SMA cross", "Bollinger", "Keltner", "ATR expansion", "ADX", "Stochastic",
         "VWAP (time-weighted)", "Supertrend", "Ichimoku", "Donchian", "Daily pivot"]
PER = [5, 8, 10, 14, 20, 30, 50]
N_IND = len(NAMES)

def _ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values
def _sma(x, n): return pd.Series(x).rolling(n, min_periods=n).mean().values
def _sgn(x): return np.nan_to_num(np.sign(x)).astype(np.int8)
def _band(c, up, lo):
    s = np.zeros(len(c), np.int8)
    with np.errstate(invalid="ignore"): s[c > up] = 1; s[c < lo] = -1
    return s

@njit(cache=True)
def _supertrend(h, l, c, at, k):
    n = len(c); d = np.zeros(n, np.int8); ub = 0.0; lb = 0.0; cur = 1
    for i in range(n):
        if not (at[i] > 0): continue
        m = (h[i] + l[i]) / 2; u = m + k * at[i]; b = m - k * at[i]
        if i > 0 and d[i - 1] != 0:
            u = min(u, ub) if c[i - 1] <= ub else u
            b = max(b, lb) if c[i - 1] >= lb else b
        if cur == 1 and c[i] < b: cur = -1
        elif cur == -1 and c[i] > u: cur = 1
        ub = u; lb = b; d[i] = cur
    return d

def state(ind, k, n):
    t = ind.t; h, l, c = t["h"], t["l"], t["c"]
    with np.errstate(invalid="ignore", divide="ignore"):
        if k == 0:
            r = ind.get("rsi", n); return _band(r, 55.0, 45.0)
        if k == 1:
            m = _ema(c, n) - _ema(c, int(n * 2.2) + 1); return _sgn(m - _ema(m, 9))
        if k == 2: return _sgn(_ema(c, n) - _ema(c, 3 * n))
        if k == 3: return _sgn(_sma(c, n) - _sma(c, 3 * n))
        if k == 4:
            m = _sma(c, n); sd = pd.Series(c).rolling(n).std().values; return _band(c, m + 2 * sd, m - 2 * sd)
        if k == 5:
            m = _ema(c, n); a = ind.get("atr"); return _band(c, m + 1.5 * a, m - 1.5 * a)
        if k == 6:
            ar = ind.get("atr_ratio"); mv = c - np.r_[np.full(n, np.nan), c[:-n]]
            s = _sgn(mv); s[~(ar > 1.0)] = 0; return s
        if k == 7:
            up = h - np.r_[h[0], h[:-1]]; dn = np.r_[l[0], l[:-1]] - l
            pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
            tr = ind.get("atr"); pdi = _ema(pdm, n) / tr; mdi = _ema(mdm, n) / tr
            dx = np.abs(pdi - mdi) / (pdi + mdi + 1e-12) * 100; adx = _ema(dx, n)
            s = _sgn(pdi - mdi); s[~(adx > 20)] = 0; return s
        if k == 8:
            hh = pd.Series(h).rolling(n).max().values; ll = pd.Series(l).rolling(n).min().values
            kk = _sma((c - ll) / (hh - ll + 1e-12) * 100, 3); return _band(kk, 80.0, 20.0)
        if k == 9: return _sgn(c - ind.get("twap", 0))
        if k == 10: return _supertrend(h, l, c, ind.get("atr"), 1.0 + n / 10.0)
        if k == 11:
            f = max(n // 2, 3); s_ = n; L = 2 * n
            mid = lambda w: (pd.Series(h).rolling(w).max().values + pd.Series(l).rolling(w).min().values) / 2
            ten, kij = mid(f), mid(s_); spa = np.r_[np.full(s_, np.nan), ((ten + kij) / 2)[:-s_]]
            spb = np.r_[np.full(s_, np.nan), mid(L)[:-s_]]        # cloud plotted s_ bars ahead -> value at t uses data from t-s_
            top = np.fmax(spa, spb); bot = np.fmin(spa, spb)
            s = np.zeros(len(c), np.int8); s[(c > top) & (ten > kij)] = 1; s[(c < bot) & (ten < kij)] = -1; return s
        if k == 12:
            return _band(c, ind.get("hh", n * 2) - 1e-12, ind.get("ll", n * 2) + 1e-12)
        if k == 13:
            idx = t["idx"]; s_h = pd.Series(h, idx); s_l = pd.Series(l, idx); s_c = pd.Series(c, idx)
            D = pd.DataFrame(dict(h=s_h.resample("1D").max(), l=s_l.resample("1D").min(), c=s_c.resample("1D").last())).dropna()
            P = ((D.h + D.l + D.c) / 3).shift(1).reindex(idx.floor("1D")).values   # previous completed day's pivot
            return _sgn(c - P)
    raise ValueError(k)

def conds(g):
    nc = int(g.get("nc", 0))
    return [(int(g[f"i{j}"]), int(g[f"n{j}"]), int(g[f"m{j}"])) for j in range(1, nc + 1)]

def combined(g, ind):
    """+1/-1 where the 1-3 conditions agree (AND) or where at least one fires and none disagrees (OR)."""
    cs = conds(g)
    if not cs: return None
    S = [ind.get("st", k, PER[n]) * (-1 if m else 1) for k, n, m in cs]
    S = np.array(S, np.int8)
    if int(g.get("lg", 0)) == 0:
        out = np.where((S == 1).all(0), 1, np.where((S == -1).all(0), -1, 0))
    else:
        out = np.where((S == 1).any(0) & ~(S == -1).any(0), 1, np.where((S == -1).any(0) & ~(S == 1).any(0), -1, 0))
    return out.astype(np.int8)

def text(g):
    cs = conds(g)
    if not cs: return ""
    j = " AND " if int(g.get("lg", 0)) == 0 else " OR "
    return j.join(f"{NAMES[k]}{'' if k == 9 else f'({PER[n]})'}{' fade' if m else ''}" for k, n, m in cs)

def signature(cs):
    return "+".join(sorted({NAMES[k] for k, _, _ in cs}))
