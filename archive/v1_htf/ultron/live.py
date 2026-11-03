"""Always-on PAPER floor. Live prices only, no broker, no orders.
BTCUSD: Binance public websocket (btcusdt@kline_1m + @kline_5m) on port 443.
XAUUSD: Swissquote public BBO quotes (bid/ask) polled every 5s; 5m mid bars built from ticks.
Each roster bot evaluates its genome on every closed bar of its timeframe; paper positions managed on every tick/1m bar.
Learning (kill/breed/rebalance) runs once a day in a separate low-priority process (ultron.learn)."""
import asyncio, json, os, time, subprocess, sys, signal as sg, logging
import numpy as np, pandas as pd, httpx, websockets
from .market import Market, ROOT
from .engine import signals, Ind
from .costs import COSTS
from .db import connect

log = logging.getLogger("ultron.live")
TF_SEC = {0: 900, 1: 3600}
BUDGET_PCT = 5.0     # aggressive: total paper risk budget, split by allocator weights
START_EQUITY = 10000.0
SQ = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
BWS = "wss://stream.binance.com:443/stream?streams=btcusdt@kline_1m/btcusdt@kline_5m"

class Floor:
    def __init__(self):
        self.db = connect()
        self.df5 = {}; self.cur5 = {"XAUUSD": None}; self.quote = {}
        self.last_tf_close = {}
        self.reload_roster()

    # ---------- state ----------
    def reload_roster(self):
        self.db.row_factory = None
        rows = self.db.execute("SELECT bot_id,symbol,genome,weight FROM live_bots WHERE status IN ('live','probation')").fetchall()
        self.bots = {r[0]: dict(id=r[0], sym=r[1], g=json.loads(r[2]), w=r[3] or 0.0) for r in rows}
        self.pos = {r[0]: dict(sym=r[1], dir=r[2], entry_ts=r[3], entry=r[4], stop=r[5], tgt=r[6], risk=r[7], hold_until=r[8])
                    for r in self.db.execute("SELECT * FROM live_positions")}
        log.info("roster %d bots, %d open paper positions", len(self.bots), len(self.pos))

    def event(self, kind, bot, detail):
        self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), kind, bot, json.dumps(detail, default=str)))
        self.db.commit()

    # ---------- history ----------
    def bootstrap(self):
        for sym in ("XAUUSD", "BTCUSD"):
            p = os.path.join(ROOT, "data", f"{sym}_m5.pkl")
            df = pd.read_pickle(p)
            lp = os.path.join(ROOT, "data", f"{sym}_m5_live.pkl")
            if os.path.exists(lp):
                df = pd.concat([df, pd.read_pickle(lp)]); df = df[~df.index.duplicated(keep="last")].sort_index()
            self.df5[sym] = df[df.index >= df.index[-1] - pd.Timedelta(days=75)]
        self.topup_btc(); self.topup_xau()

    def topup_btc(self):
        df = self.df5["BTCUSD"]; start = int(df.index[-1].timestamp() * 1000) + 1; rows = []
        while True:
            r = httpx.get("https://api.binance.com/api/v3/klines", params=dict(symbol="BTCUSDT", interval="5m", startTime=start, limit=1000), timeout=20).json()
            if not r: break
            rows += r; start = r[-1][0] + 1
            if len(r) < 1000: break
        if len(rows) > 1:
            t = pd.DataFrame(rows[:-1]).iloc[:, :5].astype(float)
            t.index = pd.to_datetime(t[0].astype("int64"), unit="ms", utc=True)
            t = t.rename(columns={1: "o", 2: "h", 3: "l", 4: "c"})[["o", "h", "l", "c"]]; t["spread"] = np.nan
            self.df5["BTCUSD"] = pd.concat([df, t]).pipe(lambda d: d[~d.index.duplicated(keep="last")])
        log.info("BTC history to %s", self.df5["BTCUSD"].index[-1])

    def topup_xau(self):
        df = self.df5["XAUUSD"]; frm = (df.index[-1] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        to = (pd.Timestamp.utcnow() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"); d = "/tmp/ultron_dk"
        os.makedirs(d, exist_ok=True)
        try:
            out = {}
            for p in ("bid", "ask"):
                for f in os.listdir(d): os.remove(os.path.join(d, f))
                subprocess.run(["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", frm, "-to", to, "-t", "m5", "-p", p, "-f", "csv", "-dir", d], timeout=180, capture_output=True)
                f = [x for x in os.listdir(d) if x.endswith(".csv")][0]
                x = pd.read_csv(os.path.join(d, f)); x.index = pd.to_datetime(x.timestamp, unit="ms", utc=True); out[p] = x
            t = out["bid"].rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
            t["spread"] = (out["ask"]["close"] - out["bid"]["close"]).reindex(t.index).clip(lower=0)
            t = t.iloc[:-1]
            self.df5["XAUUSD"] = pd.concat([df, t]).pipe(lambda d: d[~d.index.duplicated(keep="last")]).sort_index()
        except Exception as e:
            log.warning("XAU dukascopy top-up failed: %s", e)
        log.info("XAU history to %s", self.df5["XAUUSD"].index[-1])

    def save_live(self):
        for sym, df in self.df5.items():
            df.tail(20000).to_pickle(os.path.join(ROOT, "data", f"{sym}_m5_live.pkl"))

    # ---------- costs at fill ----------
    def fill_px(self, sym, side, ref_mid, exit_kind="market"):
        """side=+1 buy, -1 sell. XAU uses live bid/ask quote when available."""
        c = COSTS[sym]
        if sym == "XAUUSD":
            q = self.quote.get(sym); slip = c["slip_side"] if exit_kind != "target" else 0.0
            half = max((q["ask"] - q["bid"]) / 2 if q else 0, c["spread_floor"] / 2)
            return ref_mid + side * (half + c["commission_side"] + slip)
        slip = ref_mid * c["slip_frac"] if exit_kind != "target" else 0.0
        return ref_mid + side * (ref_mid * c["spread_frac"] / 2 + c["commission_side"] + slip)

    # ---------- bot logic ----------
    def on_bar_close(self, sym):
        df = self.df5[sym]; mk = Market(sym, df)
        last5 = df.index[-1]
        for tf, sec in TF_SEC.items():
            t = mk.tf[tf]; bar_open = int(t["ts"][-1]); bar_end = bar_open + sec
            if int(last5.timestamp()) + 300 < bar_end: continue          # tf bar not complete yet
            if self.last_tf_close.get((sym, tf)) == bar_open: continue
            self.last_tf_close[(sym, tf)] = bar_open
            ind = Ind(t); atr = ind.get("atr")[-1]; mid = t["c"][-1]
            for b in [b for b in self.bots.values() if b["sym"] == sym and b["g"]["tf"] == tf]:
                s = int(signals(b["g"], t, ind)[-1])
                p = self.pos.get(b["id"])
                if p and b["g"]["flip"] and s == -p["dir"]:
                    self.close(b["id"], self.fill_px(sym, -p["dir"], mid), "flip")
                    p = None
                if p is None and s != 0 and atr > 0:
                    self.open(b, s, mid, atr, bar_end, sec)

    def open(self, b, d, mid, atr, now, sec):
        g = b["g"]; sym = b["sym"]
        entry = self.fill_px(sym, d, mid)
        dist = g["sl"] * atr
        hold = (g["hold"] if g["tf"] == 1 else g["hold"] * 2) * sec
        p = dict(sym=sym, dir=d, entry_ts=now, entry=entry, stop=mid - d * dist, tgt=mid + d * g["tp"] * atr, risk=dist + abs(entry - mid), hold_until=now + hold)
        self.pos[b["id"]] = p
        self.db.execute("INSERT OR REPLACE INTO live_positions VALUES(?,?,?,?,?,?,?,?,?)", (b["id"], sym, d, now, entry, p["stop"], p["tgt"], p["risk"], p["hold_until"]))
        self.event("open", b["id"], dict(sym=sym, dir=d, entry=round(entry, 3), stop=round(p["stop"], 3), tgt=round(p["tgt"], 3)))

    def close(self, bid, px, reason, ts=None):
        p = self.pos.pop(bid); ts = ts or int(time.time())
        days = ts // 86400 - p["entry_ts"] // 86400
        pnl = p["dir"] * (px - p["entry"]) - days * COSTS[p["sym"]]["swap_day_frac"] * p["entry"]
        R = pnl / p["risk"]; w = self.bots.get(bid, {}).get("w", 0.0)
        self.db.execute("INSERT INTO live_trades(bot_id,symbol,entry_ts,exit_ts,dir,entry,exit,r,reason,weight) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, p["sym"], p["entry_ts"], ts, p["dir"], p["entry"], px, R, reason, w))
        self.db.execute("DELETE FROM live_positions WHERE bot_id=?", (bid,))
        self.db.execute("""UPDATE live_bots SET live_n=live_n+1, live_r=live_r+?, live_peak_r=MAX(live_peak_r, live_r+?),
                           live_dd_r=MIN(live_dd_r, live_r+?-MAX(live_peak_r, live_r+?)) WHERE bot_id=?""", (R, R, R, R, bid))
        self.event("close", bid, dict(sym=p["sym"], reason=reason, exit=round(px, 3), r=round(R, 3)))

    def check_exits(self, sym, hi_mid, lo_mid, now):
        for bid, p in list(self.pos.items()):
            if p["sym"] != sym: continue
            d = p["dir"]
            if (d == 1 and lo_mid <= p["stop"]) or (d == -1 and hi_mid >= p["stop"]):
                self.close(bid, self.fill_px(sym, -d, p["stop"]), "stop", now)
            elif (d == 1 and hi_mid >= p["tgt"]) or (d == -1 and lo_mid <= p["tgt"]):
                self.close(bid, self.fill_px(sym, -d, p["tgt"], "target"), "target", now)
            elif now >= p["hold_until"]:
                self.close(bid, self.fill_px(sym, -d, (hi_mid + lo_mid) / 2), "time", now)

    def append5(self, sym, ts, o, h, l, c, spread):
        idx = pd.Timestamp(ts, unit="s", tz="UTC")
        row = pd.DataFrame({"o": [o], "h": [h], "l": [l], "c": [c], "spread": [spread]}, index=[idx])
        if idx <= self.df5[sym].index[-1]: return
        self.df5[sym] = pd.concat([self.df5[sym], row]).tail(25000)
        self.on_bar_close(sym)

    # ---------- feeds ----------
    async def btc_feed(self):
        while True:
            try:
                async with websockets.connect(BWS, ping_interval=20, open_timeout=20) as ws:
                    log.info("binance ws connected")
                    async for msg in ws:
                        k = json.loads(msg)["data"]["k"]
                        now = int(time.time())
                        self.db.execute("INSERT OR REPLACE INTO live_ticks VALUES(?,?,?,?,?)", ("BTCUSD", now, float(k["c"]), float(k["c"]), "binance ws kline"))
                        if k["i"] == "1m":
                            self.check_exits("BTCUSD", float(k["h"]), float(k["l"]), now)
                        if k["i"] == "5m" and k["x"]:
                            self.append5("BTCUSD", k["t"] // 1000, float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), np.nan)
                        self.db.commit()
            except Exception as e:
                log.warning("binance ws: %s; reconnect in 5s", e); await asyncio.sleep(5)

    async def xau_feed(self):
        async with httpx.AsyncClient(timeout=10) as cl:
            last_ts = None
            while True:
                try:
                    j = (await cl.get(SQ)).json()
                    pr = [p for p in j[0]["spreadProfilePrices"] if p["spreadProfile"] == "elite"][0]
                    ts = j[0]["ts"] // 1000; bid, ask = pr["bid"], pr["ask"]
                    if ts != last_ts and time.time() - ts < 120:     # fresh tick only (market closed => stale)
                        last_ts = ts; mid = (bid + ask) / 2
                        self.quote["XAUUSD"] = dict(bid=bid, ask=ask, ts=ts)
                        self.db.execute("INSERT OR REPLACE INTO live_ticks VALUES(?,?,?,?,?)", ("XAUUSD", ts, bid, ask, "swissquote public bbo (elite)"))
                        b0 = ts - ts % 300; cb = self.cur5["XAUUSD"]
                        if cb and cb["t"] != b0:
                            # Dukascopy history is bid-based: store bid OHLC + spread, Market adds spread/2 back
                            self.append5("XAUUSD", cb["t"], cb["o"] - cb["sp"] / 2, cb["h"] - cb["sp"] / 2, cb["l"] - cb["sp"] / 2, cb["c"] - cb["sp"] / 2, cb["sp"])
                            cb = None
                        if cb is None: cb = dict(t=b0, o=mid, h=mid, l=mid, c=mid, sp=ask - bid)
                        cb["h"] = max(cb["h"], mid); cb["l"] = min(cb["l"], mid); cb["c"] = mid; cb["sp"] = ask - bid
                        self.cur5["XAUUSD"] = cb
                        self.check_exits("XAUUSD", mid, mid, ts)
                        self.db.commit()
                except Exception as e:
                    log.warning("xau quote: %s", e)
                await asyncio.sleep(5)

    async def housekeeping(self):
        last_learn = 0
        while True:
            await asyncio.sleep(60)
            self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), "heartbeat", None, json.dumps(dict(bots=len(self.bots), open=len(self.pos))))) if int(time.time()) % 3600 < 60 else None
            self.db.commit()
            self.save_live()
            flag = os.path.join(ROOT, "results", "roster_changed")
            if os.path.exists(flag):
                os.remove(flag); self.reload_roster()
            now = pd.Timestamp.utcnow()
            if now.hour == 22 and time.time() - last_learn > 20 * 3600:     # 22:00 UTC = 09:00 Sydney (AEDT)
                last_learn = time.time()
                subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "ultron.learn"], cwd=ROOT,
                                 stdout=open(os.path.join(ROOT, "logs", "learn.log"), "a"), stderr=subprocess.STDOUT)

    async def main(self):
        self.bootstrap()
        await asyncio.gather(self.btc_feed(), self.xau_feed(), self.housekeeping())

if __name__ == "__main__":
    os.nice(10)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    open(os.path.join(ROOT, "logs", "live.pid"), "w").write(str(os.getpid()))
    asyncio.run(Floor().main())
