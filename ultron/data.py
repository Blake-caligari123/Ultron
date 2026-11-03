"""Build clean 1m OHLC + spread datasets (real data only) and record provenance.
XAUUSD: Dukascopy spot XAU/USD m1 bid + ask via dukascopy-node (2023-11-01 -> now); stored as bid OHLC + spread (ask-bid close).
BTCUSD: Binance spot BTCUSDT 1m klines from data.binance.vision (monthly + daily zips), topped up via REST api/v3/klines.
Output: data/{sym}_m1.pkl with columns o,h,l,c,spread (price units), UTC index (bar open time)."""
import glob, json, os, zipfile, io, time
import numpy as np, pandas as pd, httpx
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")

def build_xau():
    bid = pd.read_csv(sorted(glob.glob(f"{D}/raw/xau1m/xauusd-m1-bid-*.csv"))[-1])
    ask = pd.read_csv(sorted(glob.glob(f"{D}/raw/xau1m/xauusd-m1-ask-*.csv"))[-1])
    bid.index = pd.to_datetime(bid.timestamp, unit="ms", utc=True)
    ask.index = pd.to_datetime(ask.timestamp, unit="ms", utc=True)
    df = bid.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
    df["spread"] = (ask["close"] - bid["close"]).reindex(df.index)
    df = df[~df.index.duplicated()].sort_index()
    df["spread"] = df["spread"].clip(lower=0).fillna(df["spread"].median())
    return df

def read_binance_zip(f):
    with zipfile.ZipFile(f) as z:
        raw = z.read(z.namelist()[0])
    x = pd.read_csv(io.BytesIO(raw), header=None, usecols=[0, 1, 2, 3, 4])
    if not str(x.iloc[0, 0]).isdigit():
        x = x.iloc[1:].astype(float)
    ts = x[0].astype("int64").values
    ts = np.where(ts > 1e14, ts // 1000, ts)     # 2025+ files are in microseconds
    idx = pd.to_datetime(ts, unit="ms", utc=True)
    return pd.DataFrame({"o": x[1].values, "h": x[2].values, "l": x[3].values, "c": x[4].values}, index=idx).astype(float)

def rest_klines(start_ms, interval="1m"):
    rows = []
    while True:
        r = httpx.get("https://api.binance.com/api/v3/klines", params={"symbol": "BTCUSDT", "interval": interval, "startTime": start_ms, "limit": 1000}, timeout=20).json()
        if not r: break
        rows += r; start_ms = r[-1][0] + 1
        if len(r) < 1000: break
    if not rows: return None
    t = pd.DataFrame(rows).iloc[:-1, :5].astype(float)   # drop still-forming bar
    t.index = pd.to_datetime(t[0].astype("int64"), unit="ms", utc=True)
    return t.rename(columns={1: "o", 2: "h", 3: "l", 4: "c"})[["o", "h", "l", "c"]]

def build_btc():
    df = pd.concat([read_binance_zip(f) for f in sorted(glob.glob(f"{D}/raw/btc1m/*.zip"))])
    df = df[~df.index.duplicated()].sort_index()
    t = rest_klines(int(df.index[-1].timestamp() * 1000) + 1)
    if t is not None: df = pd.concat([df, t])
    df = df[~df.index.duplicated()].sort_index().astype(float)
    df["spread"] = np.nan  # Binance trade prices ~ mid; CFD spread modelled in costs.py
    return df

if __name__ == "__main__":
    prov = {}
    for sym, fn, src in [("XAUUSD", build_xau, "Dukascopy spot XAU/USD m1 bid + ask (spread = ask-bid close), via dukascopy-node"),
                         ("BTCUSD", build_btc, "Binance spot BTCUSDT 1m klines, data.binance.vision monthly/daily zips + api.binance.com/api/v3/klines")]:
        df = fn()
        df.to_pickle(f"{D}/{sym}_m1.pkl")
        gaps = df.index.to_series().diff().dt.total_seconds().div(60)
        prov[sym] = {"source": src, "bars_m1": len(df), "first_utc": str(df.index[0]), "last_utc": str(df.index[-1]),
                     "median_spread": None if df.spread.isna().all() else round(float(df.spread.median()), 3),
                     "gaps_over_3d": int((gaps > 3 * 1440).sum()), "gaps_over_1h_weekday": int(((gaps > 60) & (gaps < 2 * 1440)).sum()),
                     "built": time.strftime("%Y-%m-%d %H:%M %Z")}
        print(sym, prov[sym], flush=True)
    json.dump(prov, open(f"{D}/provenance.json", "w"), indent=1)
