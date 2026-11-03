"""Load 1m data, convert to mid, resample to strategy timeframes, attach cost arrays."""
import os, functools
import numpy as np, pandas as pd
from .costs import COSTS, roll_cum
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TFS = {0: "1min", 1: "5min", 2: "15min", 3: "30min"}

def drop_padding(d, min_run=5):
    """Drop runs (>= min_run bars) of flat padding bars (h==l, o==c, unchanged close): closed-market filler, not trading."""
    flat = (d["h"] == d["l"]) & (d["o"] == d["c"]) & (d["c"] == d["c"].shift())
    run_id = (~flat).cumsum(); run_len = flat.groupby(run_id).transform("sum")
    return d[~(flat & (run_len >= min_run))]

class Market:
    def __init__(self, sym, df5):
        self.sym = sym
        self.df5 = df5
        self.tf = {}
        for k, rule in TFS.items():
            self.tf[k] = self._build(df5, rule)

    def _build(self, df5, rule):
        c = COSTS[self.sym]
        d = df5.copy()
        if self.sym == "XAUUSD":
            d = drop_padding(d)                          # review D1: Dukascopy pads closed hours with flat stale bars
            half = d["spread"].fillna(c["spread_floor"]) / 2
            for k in "ohlc": d[k] = d[k] + half          # bid -> mid
        if rule == "1min": r = d.dropna(subset=["o"])
        else: r = d.resample(rule, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "spread": "mean"}).dropna(subset=["o"])
        if self.sym == "XAUUSD":
            sp = np.maximum(r["spread"].fillna(c["spread_floor"]).values, c["spread_floor"])
            cost = sp / 2 + c["commission_side"]                 # per-side spread+commission
            slip = np.full(len(r), c["slip_side"])
        else:
            cost = r["c"].values * c["spread_frac"] / 2 + c["commission_side"]
            slip = r["c"].values * c["slip_frac"]
        idx = r.index
        secs = idx.as_unit("s").asi8.astype(np.int64)   # pandas 3 may store us/ns: normalise to seconds
        # review B3: causal drift per bar (EWMA of 1-bar log returns, ~30-day half-life, known at each bar's close)
        lr = np.r_[0.0, np.diff(np.log(r.c.values.astype(np.float64)))]
        hl = 30 * 1440 / {"1min": 1, "5min": 5, "15min": 15, "30min": 30}[rule]
        mu = pd.Series(lr).ewm(halflife=hl, adjust=False).mean().values
        # quant idea: hard-block gold entries 20:00-22:59 UTC (rollover spread blow-out)
        blk = np.zeros(len(r), np.int8)
        if self.sym == "XAUUSD": blk[(idx.hour >= 20) & (idx.hour <= 22)] = 1
        return dict(ts=secs, idx=idx, mu=mu.astype(np.float64), blk=blk, roll=roll_cum(self.sym, secs),
                    o=r.o.values.astype(np.float64), h=r.h.values.astype(np.float64), l=r.l.values.astype(np.float64), c=r.c.values.astype(np.float64),
                    cost=cost.astype(np.float64), slip=slip.astype(np.float64),
                    hour=idx.hour.values.astype(np.int64), minute=idx.minute.values.astype(np.int64),
                    day=(secs // 86400).astype(np.int64), swap=float(c["swap_day_frac"]))

    def window(self, tf, start, end):
        idx = self.tf[tf]["idx"]
        a = int(idx.searchsorted(pd.Timestamp(start, tz="UTC")))
        b = int(idx.searchsorted(pd.Timestamp(end, tz="UTC")))
        return a, b

@functools.lru_cache(maxsize=4)
def load(sym):
    return Market(sym, pd.read_pickle(os.path.join(ROOT, "data", f"{sym}_m1.pkl")))
