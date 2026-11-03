"""Load 1m data, convert to mid, resample to strategy timeframes, attach cost arrays."""
import functools
import numpy as np, pandas as pd
from .costs import COSTS, roll_cum
from .db import data_file
TFS = {0: "1min", 1: "5min", 2: "15min", 3: "30min"}
TF_MINUTES = {0: 1, 1: 5, 2: 15, 3: 30}

# Live floor: bars of history each timeframe needs so every INDICATOR-family signal matches the full-history backtest value.
# (Level families are path-dependent with unbounded memory; the live floor gives them its whole window instead.)
# Longest look-backs: 500-bar ATR ratio, EMA spans up to 400 (EWMA start-up weight < 1e-9 after 3000 bars), previous-day
# levels / pivots (>= 2 days). Trimming to this keeps a 1m bar close cheap without changing any signal.
LIVE_WARMUP_BARS = 3000
LIVE_MIN_DAYS = 3

def live_tail(df1, tf):
    """Tail of the 1m frame long enough for timeframe tf, starting on a UTC day boundary (no partial first bar)."""
    if not len(df1): return df1
    mins = max(LIVE_WARMUP_BARS * TF_MINUTES[tf], LIVE_MIN_DAYS * 1440)
    start = (df1.index[-1] - pd.Timedelta(minutes=mins)).floor("1D")
    return df1 if start <= df1.index[0] else df1[df1.index >= start]

def drop_padding(d, min_run=5):
    """Drop runs (>= min_run bars) of flat padding bars (h==l, o==c, unchanged close): closed-market filler, not trading."""
    flat = (d["h"] == d["l"]) & (d["o"] == d["c"]) & (d["c"] == d["c"].shift())
    run_id = (~flat).cumsum(); run_len = flat.groupby(run_id).transform("sum")
    return d[~(flat & (run_len >= min_run))]

class Market:
    def __init__(self, sym, df1, tfs=None):
        self.sym = sym
        self.df5 = df1                                   # (historical name: the source frame is 1m)
        c = COSTS[sym]
        if sym == "XAUUSD":                              # padding drop + bid -> mid once, shared by every timeframe
            d = drop_padding(df1).copy()
            half = d["spread"].fillna(c["spread_floor"]) / 2
            for k in "ohlc": d[k] = d[k] + half
        else:
            d = df1
        self.tf = {k: self._build(d, TFS[k]) for k in (TFS if tfs is None else tfs)}

    def _build(self, d, rule):
        c = COSTS[self.sym]
        if rule == "1min": r = d.dropna(subset=["o"])
        else: r = d.resample(rule, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "spread": "mean"}).dropna(subset=["o"])
        cl = r["c"].values.astype(np.float64)
        if self.sym == "XAUUSD":
            sp = np.maximum(r["spread"].fillna(c["spread_floor"]).values, c["spread_floor"])
            cost = sp / 2 + c["commission_side"]                 # per-side spread+commission
            slip = np.full(len(r), c["slip_side"])
        else:
            cost = cl * c["spread_frac"] / 2 + c["commission_side"]
            slip = cl * c["slip_frac"]
        idx = r.index
        secs = idx.as_unit("s").asi8.astype(np.int64)   # pandas 3 may store us/ns: normalise to seconds
        # review B3: causal drift per bar (EWMA of 1-bar log returns, ~30-day half-life, known at each bar's close)
        lr = np.r_[0.0, np.diff(np.log(cl))]
        hl = 30 * 1440 / {"1min": 1, "5min": 5, "15min": 15, "30min": 30}[rule]
        mu = pd.Series(lr).ewm(halflife=hl, adjust=False).mean().values
        # quant idea: hard-block gold entries 20:00-22:59 UTC (rollover spread blow-out)
        hour = idx.hour.values.astype(np.int64)
        blk = np.zeros(len(r), np.int8)
        if self.sym == "XAUUSD": blk[(hour >= 20) & (hour <= 22)] = 1
        return dict(ts=secs, idx=idx, mu=mu.astype(np.float64), blk=blk, roll=roll_cum(self.sym, secs),
                    o=r.o.values.astype(np.float64), h=r.h.values.astype(np.float64), l=r.l.values.astype(np.float64), c=cl,
                    cost=np.asarray(cost, np.float64), slip=np.asarray(slip, np.float64),
                    hour=hour, minute=idx.minute.values.astype(np.int64),
                    day=(secs // 86400).astype(np.int64), swap=float(c["swap_day_frac"]))

    def window(self, tf, start, end):
        idx = self.tf[tf]["idx"]
        a = int(idx.searchsorted(pd.Timestamp(start, tz="UTC")))
        b = int(idx.searchsorted(pd.Timestamp(end, tz="UTC")))
        return a, b

def load_history(sym, with_live=True):
    """Full 1m history (+ the live floor's recent bar cache when present), de-duplicated and sorted."""
    import os
    df = pd.read_pickle(data_file(f"{sym}_m1.pkl"))
    lp = data_file(f"{sym}_m1_live.pkl")
    if with_live and os.path.exists(lp):
        df = pd.concat([df, pd.read_pickle(lp)]); df = df[~df.index.duplicated(keep="last")].sort_index()
    return df

@functools.lru_cache(maxsize=4)
def load(sym):
    return Market(sym, load_history(sym, with_live=False))
