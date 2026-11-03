"""Build clean 5m OHLC + spread datasets (real data only) and record provenance.
XAUUSD: Dukascopy spot bid + per-bar spread (ask-bid). Existing 2020-01..2026-10-21 set from
/workspace/xauusd/research/data (downloaded earlier via dukascopy-node), topped up with fresh
bid+ask m5 pulled by dukascopy-node into data/raw.
BTCUSD: Binance spot BTCUSDT 5m klines from data.binance.vision (monthly + daily zips), topped up via REST.
Output: data/{sym}_m5.pkl with columns o,h,l,c,spread (price units), UTC index (bar open time)."""
import glob, json, os, zipfile, io, time
import numpy as np, pandas as pd, httpx
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")

def build_xau():
    old = pd.read_csv("/workspace/xauusd/research/data/xauusd_m5.csv")
    old.index = pd.to_datetime(old.time_utc, utc=True)
    old = old.rename(columns={"o": "o", "h": "h", "l": "l", "c": "c"})[["o", "h", "l", "c", "spread"]]
    bid = pd.read_csv(sorted(glob.glob(f"{D}/raw/xauusd-m5-bid-*.csv"))[-1])
    ask = pd.read_csv(sorted(glob.glob(f"{D}/raw/xauusd-m5-ask-*.csv"))[-1])
    bid.index = pd.to_datetime(bid.timestamp, unit="ms", utc=True)
    ask.index = pd.to_datetime(ask.timestamp, unit="ms", utc=True)
    new = bid.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
    new["spread"] = (ask["close"] - bid["close"]).reindex(new.index)
    df = pd.concat([old, new[new.index > old.index[-1]]])
    df = df[~df.index.duplicated()].sort_index()
    df["spread"] = df["spread"].clip(lower=0).fillna(df["spread"].median())
    return df

def build_btc():
    parts = []
    for f in sorted(glob.glob(f"{D}/raw/btc/*.zip")):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0])
        x = pd.read_csv(io.BytesIO(raw), header=None, usecols=[0, 1, 2, 3, 4])
        if not str(x.iloc[0, 0]).isdigit():
            x = x.iloc[1:].astype(float)
        ts = x[0].astype("int64")
        unit = np.where(ts > 1e14, "us", "ms")
        idx = pd.to_datetime(ts // np.where(unit == "us", 1000, 1), unit="ms", utc=True)
        parts.append(pd.DataFrame({"o": x[1].values, "h": x[2].values, "l": x[3].values, "c": x[4].values}, index=idx))
    df = pd.concat(parts)
    # REST top-up to now
    start = int(df.index[-1].timestamp() * 1000) + 1
    rows = []
    while True:
        r = httpx.get("https://api.binance.com/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "5m", "startTime": start, "limit": 1000}, timeout=20).json()
        if not r: break
        rows += r; start = r[-1][0] + 1
        if len(r) < 1000: break
    if rows:
        t = pd.DataFrame(rows).iloc[:, :5].astype(float)
        t.index = pd.to_datetime(t[0].astype("int64"), unit="ms", utc=True)
        t = t.iloc[:-1]  # drop still-forming bar
        df = pd.concat([df, t.rename(columns={1: "o", 2: "h", 3: "l", 4: "c"})[["o", "h", "l", "c"]]])
    df = df[~df.index.duplicated()].sort_index().astype(float)
    df["spread"] = np.nan  # Binance trade prices ~ mid; CFD spread modelled in costs.py
    return df

if __name__ == "__main__":
    prov = {}
    for sym, fn, src in [("XAUUSD", build_xau, "Dukascopy spot XAU/USD m5 bid + ask (spread=ask-bid close), via dukascopy-node"),
                         ("BTCUSD", build_btc, "Binance spot BTCUSDT 5m klines, data.binance.vision + api.binance.com/api/v3/klines")]:
        df = fn()
        df.to_pickle(f"{D}/{sym}_m5.pkl")
        gaps = df.index.to_series().diff().dt.total_seconds().div(60)
        prov[sym] = {"source": src, "bars_m5": len(df), "first_utc": str(df.index[0]), "last_utc": str(df.index[-1]),
                     "median_spread": None if df.spread.isna().all() else round(float(df.spread.median()), 3),
                     "gaps_over_3d": int((gaps > 3 * 1440).sum()), "built": time.strftime("%Y-%m-%d %H:%M %Z")}
        print(sym, prov[sym])
    json.dump(prov, open(f"{D}/provenance.json", "w"), indent=1)
