"""Always-on PAPER floor. Live prices only, no broker, no orders.
BTCUSD: Binance public websocket (btcusdt@kline_1m) on port 443 -> closed 1m bars + intrabar highs/lows for exits.
XAUUSD: Swissquote public BBO quotes (bid/ask) polled every 5s -> 1m mid bars built from ticks.
Each roster bot evaluates its genome on every closed bar of its timeframe (1m/5m/15m); paper positions are managed
on every tick / 1m update. (Prop-firm challenge tracking was removed at the user's request; kills are performance-based only.)
Learning (cut losers / breed / promote / rebalance) runs NONSTOP in a sibling low-priority process (ultron.breeder, run by run.sh)."""
import asyncio, json, os, time, subprocess, sys, logging
import numpy as np, pandas as pd, httpx, websockets
from .risk import RiskManager, load_cfg, syd_day_start
from .market import Market, ROOT
from .engine import signals, level_signal, LEVEL_FAMS, Ind, hold_bars, TF_MIN, entry_ok, entry_blocked
from . import levels
from .costs import COSTS, swap_units
from .db import connect, rfile, FLOOR
from .prop import FIRMS

log = logging.getLogger("ultron.live")
TF_SEC = {k: v * 60 for k, v in TF_MIN.items()}
BUDGET_PCT = 5.0     # floor account: total paper risk budget per trade-set, split by allocator weights
START_EQUITY = 10000.0
FIRM = "FTMO"
SQ = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
BWS = "wss://stream.binance.com:443/ws/btcusdt@kline_1m"
KEEP_DAYS = 20

def day_key(ts): return (int(ts) + 3600) // 86400      # FTMO day = 00:00 CE(S)T ~ 23:00 UTC

TICK_WARN_S = 120; TICK_DIE_S = 600     # watchdog: warn when a market's last tick is older than this; exit(2) past the second

def gold_open(ts):
    """Spot gold CFD hours (UTC, conservative): closed Fri 21:00 -> Sun 23:00 and the daily 21:00-23:00 break."""
    d = time.gmtime(ts); wd, h = d.tm_wday, d.tm_hour      # Mon=0
    if wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 23): return False
    return not (21 <= h < 23)

class Floor:
    def __init__(self):
        self.db = connect()
        self.df1 = {}; self.cur1 = {"XAUUSD": None}; self.quote = {}; self.last_tf_close = {}
        self.risk = RiskManager(self.db)
        self.t0 = int(time.time()); self.gold_open_since = self.t0; self.was_gold_open = gold_open(self.t0)
        self.last_stale_log = {}; self.last_skip_log = 0
        self.reload_roster()

    # ---------- state ----------
    def reload_roster(self):
        rows = self.db.execute("SELECT bot_id,symbol,genome,weight,live_n FROM live_bots WHERE status IN ('live','probation')").fetchall()
        old_bots = getattr(self, "bots", {})
        self.bots = {r[0]: dict(id=r[0], sym=r[1], g=json.loads(r[2]), w=r[3] or 0.0, n=r[4] or 0) for r in rows}
        self.pos = {r[0]: dict(sym=r[1], dir=r[2], entry_ts=r[3], entry=r[4], stop=r[5], tgt=r[6], risk=r[7], hold_until=r[8], risk_usd=r[9], cost_px=r[10])
                    for r in self.db.execute("SELECT bot_id,symbol,dir,entry_ts,entry,stop,tgt,risk,hold_until,risk_usd,cost_px FROM live_positions")}
        self.chal = {}      # prop-challenge tracking disabled (all chal_* hooks are no-ops when a bot has no challenge)
        now = int(time.time())
        for bid, p in list(self.pos.items()):        # a bot cut by the breeder closes its paper trade at the current price
            if bid in self.bots: continue
            q = self.quote.get(p["sym"]); df = self.df1.get(p["sym"])
            mid = (q["bid"] + q["ask"]) / 2 if q else (float(df["c"].iloc[-1]) if df is not None and len(df) else None)
            if mid is None: continue
            if bid in old_bots: self.bots[bid] = old_bots[bid]
            self.close(bid, self.fill_px(p["sym"], -p["dir"], mid), "bot cut", now)
            self.bots.pop(bid, None)
        self.db.commit()
        log.info("roster %d bots, %d open paper positions", len(self.bots), len(self.pos))

    def new_challenge(self, bid, attempt, now):
        c = dict(bot_id=bid, firm=FIRM, attempt=attempt, phase=1, start_ts=now, phase_start_ts=now, balance_pct=0.0, day_key=day_key(now),
                 day_start_pct=0.0, worst_day_pct=0.0, trading_days=0, last_trade_day=-1, status="active", reason=None, updated=now)
        self.chal[bid] = c; self.save_chal(c)

    def save_chal(self, c):
        self.db.execute("INSERT OR REPLACE INTO live_challenge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(c[k] for k in (
            "bot_id", "firm", "attempt", "phase", "start_ts", "phase_start_ts", "balance_pct", "day_key", "day_start_pct", "worst_day_pct",
            "trading_days", "last_trade_day", "status", "reason", "updated")))

    def unreal_usd(self):
        u = 0.0
        for p in self.pos.values():
            q = self.quote.get(p["sym"])
            if q and p.get("risk_usd"): u += p["dir"] * (((q["bid"] if p["dir"] > 0 else q["ask"])) - p["entry"]) / p["risk"] * p["risk_usd"]
        return u

    def save_risk_state(self):
        s = self.risk.snapshot(self.pos, self.unreal_usd()); c = s.pop("cfg")
        s.update(blocked_today=self.risk.blocked_today if self.risk.block_day else 0,
                 halted=self.risk.halt_day is not None and self.risk.halt_day == syd_day_start(),
                 updated=int(time.time()), limits={k: v for k, v in c.items() if not k.startswith("_")})
        f = rfile("risk_state.json"); json.dump(s, open(f + ".tmp", "w")); os.replace(f + ".tmp", f)

    def event(self, kind, bot, detail):
        self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), kind, bot, json.dumps(detail, default=str)))
        self.db.commit()

    # ---------- challenge accounting (percent of $10,000 initial) ----------
    def chal_roll_day(self, c, now):
        dk = day_key(now)
        if dk != c["day_key"]:
            c["day_key"] = dk; c["day_start_pct"] = c["balance_pct"]; c["worst_day_pct"] = 0.0

    def chal_check(self, bid, equity_pct, now):
        c = self.chal.get(bid)
        if not c or c["status"] != "active": return
        self.chal_roll_day(c, now); f = FIRMS[FIRM]
        used = max(0.0, c["day_start_pct"] - equity_pct); c["worst_day_pct"] = max(c["worst_day_pct"], used)
        why = None
        if used > f["daily"] * 100: why = f"daily loss limit hit ({used:.2f}% in one day, limit {f['daily']*100:.0f}%)"
        elif equity_pct < -f["maxloss"] * 100: why = f"max loss limit hit (down {-equity_pct:.2f}%, limit {f['maxloss']*100:.0f}%)"
        if why:
            c["status"] = "failed"; c["reason"] = why; c["updated"] = now; self.save_chal(c)
            self.event("challenge_failed", bid, dict(sym=self.bots.get(bid, {}).get("sym"), phase=c["phase"], attempt=c["attempt"], reason=why))
            if bid in self.pos:
                p = self.pos[bid]; q = self.mid(p["sym"])
                if q: self.close(bid, self.fill_px(p["sym"], -p["dir"], q), "challenge breach", now)

    def chal_on_close(self, bid, R, now):
        c = self.chal.get(bid); b = self.bots.get(bid)
        if not c or not b or c["status"] != "active": return
        c["balance_pct"] += R * b["g"].get("risk", 1.0); c["updated"] = now
        self.chal_check(bid, c["balance_pct"], now)
        if c["status"] != "active": return
        f = FIRMS[FIRM]; tgt = (f["t1"] if c["phase"] == 1 else f["t2"]) * 100
        if c["balance_pct"] >= tgt and c["trading_days"] >= f["mindays"]:
            if c["phase"] == 1:
                self.event("challenge_phase1_passed", bid, dict(sym=b["sym"], attempt=c["attempt"], days=round((now - c["start_ts"]) / 86400, 1)))
                c.update(phase=2, phase_start_ts=now, balance_pct=0.0, day_start_pct=0.0, worst_day_pct=0.0, trading_days=0, last_trade_day=-1)
            else:
                c["status"] = "passed"; c["reason"] = f"passed both phases in {(now - c['start_ts']) / 86400:.1f} days"
                self.event("challenge_passed", bid, dict(sym=b["sym"], attempt=c["attempt"], days=round((now - c["start_ts"]) / 86400, 1)))
        self.save_chal(c)

    def chal_on_open(self, bid, now):
        c = self.chal.get(bid)
        if not c or c["status"] != "active": return
        self.chal_roll_day(c, now); dk = day_key(now)
        if dk != c["last_trade_day"]: c["trading_days"] += 1; c["last_trade_day"] = dk
        self.save_chal(c)

    # ---------- history ----------
    def bootstrap(self):
        for sym in ("XAUUSD", "BTCUSD"):
            df = pd.read_pickle(os.path.join(ROOT, "data", f"{sym}_m1.pkl"))
            lp = os.path.join(ROOT, "data", f"{sym}_m1_live.pkl")
            if os.path.exists(lp):
                df = pd.concat([df, pd.read_pickle(lp)]); df = df[~df.index.duplicated(keep="last")].sort_index()
            self.df1[sym] = df[df.index >= df.index[-1] - pd.Timedelta(days=KEEP_DAYS)]
        self.topup_btc(); self.topup_xau()

    def topup_btc(self):
        from .data import rest_klines
        df = self.df1["BTCUSD"]
        try:
            t = rest_klines(int(df.index[-1].timestamp() * 1000) + 1)
            if t is not None and len(t):
                t["spread"] = np.nan
                self.df1["BTCUSD"] = pd.concat([df, t]).pipe(lambda d: d[~d.index.duplicated(keep="last")]).sort_index()
        except Exception as e:
            log.warning("BTC REST top-up failed: %s", e)
        log.info("BTC history to %s", self.df1["BTCUSD"].index[-1])

    def topup_xau(self):
        df = self.df1["XAUUSD"]; frm = (df.index[-1] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        to = (pd.Timestamp.now("UTC") + pd.Timedelta(days=1)).strftime("%Y-%m-%d"); d = f"/tmp/ultron_dk1_{FLOOR}"       # per-floor folder: both floors top up at start, a shared folder let them delete each other's files
        os.makedirs(d, exist_ok=True)
        try:
            out = {}
            for p in ("bid", "ask"):
                f = []
                for attempt in range(3):
                    for x in os.listdir(d): os.remove(os.path.join(d, x))
                    r = subprocess.run(["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", frm, "-to", to, "-t", "m1", "-p", p, "-f", "csv", "-dir", d, "-s"], timeout=240, capture_output=True, text=True)
                    f = [x for x in os.listdir(d) if x.endswith(".csv")]
                    if f: break
                    time.sleep(5)
                if not f:
                    raise RuntimeError(f"no {p} file from dukascopy after 3 tries (rc={r.returncode}: {(r.stderr or r.stdout)[-160:].strip()})")
                f = f[0]
                x = pd.read_csv(os.path.join(d, f)); x.index = pd.to_datetime(x.timestamp, unit="ms", utc=True); out[p] = x
            t = out["bid"].rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
            t["spread"] = (out["ask"]["close"] - out["bid"]["close"]).reindex(t.index).clip(lower=0)
            t = t.iloc[:-1]
            self.df1["XAUUSD"] = pd.concat([df, t]).pipe(lambda d: d[~d.index.duplicated(keep="last")]).sort_index()
        except Exception as e:
            log.warning("XAU dukascopy top-up failed: %s", e)
        log.info("XAU history to %s", self.df1["XAUUSD"].index[-1])

    def save_live(self):
        for sym, df in self.df1.items():
            if FLOOR != "main": continue      # only the main floor writes the shared live-bar cache
            df.tail(KEEP_DAYS * 1440).to_pickle(os.path.join(ROOT, "data", f"{sym}_m1_live.pkl"))

    # ---------- prices & costs ----------
    def mid(self, sym):
        q = self.quote.get(sym)
        return (q["bid"] + q["ask"]) / 2 if q else None

    def fill_px(self, sym, side, ref_mid, exit_kind="market"):
        """side=+1 buy, -1 sell. XAU uses live bid/ask quote when available."""
        c = COSTS[sym]
        if sym == "XAUUSD":
            q = self.quote.get(sym); slip = c["slip_side"] if exit_kind != "target" else 0.0
            half = max((q["ask"] - q["bid"]) / 2 if q else 0, c["spread_floor"] / 2)
            cost = half + c["commission_side"] + slip
        else:
            slip = ref_mid * c["slip_frac"] if exit_kind != "target" else 0.0
            cost = ref_mid * c["spread_frac"] / 2 + c["commission_side"] + slip
        self.last_cost = cost        # spread + commission + slippage paid on this fill (price units)
        return ref_mid + side * cost

    # ---------- bot logic ----------
    def on_bar_close(self, sym):
        df = self.df1[sym]; last1 = int(df.index[-1].timestamp()); mk = None
        for tf, sec in TF_SEC.items():
            bots = [b for b in self.bots.values() if b["sym"] == sym and b["g"]["tf"] == tf]
            if not bots: continue
            if mk is None: mk = Market(sym, df)
            t = mk.tf[tf]; bar_open = int(t["ts"][-1])
            if bar_open + sec > last1 + 60: continue                   # tf bar not complete yet
            if self.last_tf_close.get((sym, tf)) == bar_open: continue
            self.last_tf_close[(sym, tf)] = bar_open
            ind = Ind(t); atr = ind.get("atr")[-1]; mid = t["c"][-1]; now = bar_open + sec
            rt = 2 * (t["cost"][-1] + t["slip"][-1])
            for b in bots:
                lvl_stop = None
                if b["g"]["fam"] in LEVEL_FAMS:
                    sg, stp = level_signal(b["g"], t, ind, sym); s = int(sg[-1]); lvl_stop = float(stp[-1])
                    if s != 0:      # same skip rules as the backtest: stop <= 3 ATR away, target >= 3x round-trip cost
                        dist = s * (mid - lvl_stop)
                        if not (0 < dist <= levels.MAX_STOP_ATR * atr and b["g"]["tp"] * dist >= levels.MIN_TP_COST_MULT * rt): s = 0
                else:
                    s = int(signals(b["g"], t, ind)[-1])
                p = self.pos.get(b["id"])
                if p and b["g"]["flip"] and s == -p["dir"]:
                    self.close(b["id"], self.fill_px(sym, -p["dir"], mid), "flip", now); p = None
                c = self.chal.get(b["id"])
                if s != 0 and p is None:      # same entry filters as the backtest: gold 20-23 UTC blocked, cost <= 0.3 ATR, stop >= 8x cost
                    dist = s * (mid - lvl_stop) if lvl_stop is not None else b["g"]["sl"] * atr
                    if entry_blocked(sym, now) or not entry_ok(dist, rt, atr): s = 0
                if p is None and s != 0 and atr > 0 and (c is None or c["status"] != "failed"):
                    self.open(b, s, mid, atr, now, sec, lvl_stop)

    def dd_kill_check(self, now=None):
        """Floor-wide peak-to-trough kill: on the first breach log 'floor_dd_kill' and flatten every open paper position."""
        if getattr(self, "dd_flattened", False) or not self.risk.dd_kill_hit(self.unreal_usd()): return False
        self.dd_flattened = True; now = int(now or time.time())
        self.event("floor_dd_kill", None, dict(dd_pct=round(self.risk.floor_dd_pct(self.unreal_usd()), 2), limit=load_cfg().get("max_floor_drawdown_pct"), open=len(self.pos)))
        for bid, p in list(self.pos.items()):
            m = self.mid(p["sym"])
            if m: self.close(bid, self.fill_px(p["sym"], -p["dir"], m), "floor dd kill", now)
        self.db.commit(); return True

    def open(self, b, d, mid, atr, now, sec, lvl_stop=None):
        g = b["g"]; sym = b["sym"]
        ok, why, risk_usd = self.risk.check(self.pos, sym, d, b["w"], self.unreal_usd(), now, live_n=b.get("n", 0))   # < 30 live trades: 0.25x risk
        if not ok:
            if why.endswith("|FIRSTDD"):
                why = why[:-8]; self.dd_kill_check(now)
            if why.endswith("|FIRST"):
                why = why[:-6]; s = self.risk.snapshot(self.pos, self.unreal_usd(), now)
                self.event("floor_halt", None, dict(day_pnl_pct=round(s["day_pnl_pct"], 2), limit=s["cfg"]["daily_floor_loss_limit_pct"]))
            if self.risk.should_log_block(b["id"], now):
                self.event("blocked", b["id"], dict(sym=sym, dir=d, reason=why))
            log.info("blocked by floor limit: bot %s %s %s: %s", b["id"], sym, d, why)
            return
        entry = self.fill_px(sym, d, mid); cost_px = self.last_cost
        dist = g["sl"] * atr; tdist = g["tp"] * atr
        if lvl_stop is not None:            # level bots: stop just beyond the level / wick, target = tp x R
            dist = d * (mid - lvl_stop); tdist = g["tp"] * dist
        p = dict(sym=sym, dir=d, entry_ts=now, entry=entry, stop=mid - d * dist, tgt=mid + d * tdist, risk=dist + abs(entry - mid),
                 hold_until=now + hold_bars(g) * sec, risk_usd=risk_usd, cost_px=cost_px)
        self.pos[b["id"]] = p
        self.db.execute("INSERT OR REPLACE INTO live_positions(bot_id,symbol,dir,entry_ts,entry,stop,tgt,risk,hold_until,risk_usd,cost_px) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (b["id"], sym, d, now, entry, p["stop"], p["tgt"], p["risk"], p["hold_until"], risk_usd, cost_px))
        self.chal_on_open(b["id"], now)
        self.event("open", b["id"], dict(sym=sym, dir=d, entry=round(entry, 3), stop=round(p["stop"], 3), tgt=round(p["tgt"], 3)))

    def close(self, bid, px, reason, ts=None):
        p = self.pos.pop(bid); ts = int(ts or time.time())
        days = swap_units(p["sym"], p["entry_ts"], ts)        # broker rollovers crossed (server midnight, x3 Wed for gold)
        pnl = p["dir"] * (px - p["entry"]) - days * COSTS[p["sym"]]["swap_day_frac"] * p["entry"]
        R = pnl / p["risk"]; w = self.bots.get(bid, {}).get("w", 0.0)
        cost_usd = ((p.get("cost_px") or 0) + getattr(self, "last_cost", 0)) / p["risk"] * (p.get("risk_usd") or 0); self.last_cost = 0.0
        self.db.execute("INSERT INTO live_trades(bot_id,symbol,entry_ts,exit_ts,dir,entry,exit,r,reason,weight,risk_usd,cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (bid, p["sym"], p["entry_ts"], ts, p["dir"], p["entry"], px, R, reason, w, p.get("risk_usd"), cost_usd))
        self.db.execute("DELETE FROM live_positions WHERE bot_id=?", (bid,))
        self.db.execute("""UPDATE live_bots SET live_n=live_n+1, live_r=live_r+?, live_peak_r=MAX(live_peak_r, live_r+?),
                           live_dd_r=MIN(live_dd_r, live_r+?-MAX(live_peak_r, live_r+?)) WHERE bot_id=?""", (R, R, R, R, bid))
        if bid in self.bots: self.bots[bid]["n"] = self.bots[bid].get("n", 0) + 1
        self.event("close", bid, dict(sym=p["sym"], reason=reason, exit=round(px, 3), r=round(R, 3)))
        if reason != "challenge breach": self.chal_on_close(bid, R, ts)

    def check_exits(self, sym, hi_mid, lo_mid, now):
        for bid, p in list(self.pos.items()):
            if p["sym"] != sym: continue
            d = p["dir"]
            if (d == 1 and lo_mid <= p["stop"]) or (d == -1 and hi_mid >= p["stop"]):
                q = self.mid(sym)                      # gapped through the stop (gold or BTC): fill at the worse of stop and current market
                ref = p["stop"] if q is None else (min(p["stop"], q) if d == 1 else max(p["stop"], q))
                self.close(bid, self.fill_px(sym, -d, ref), "stop", now)
            elif (d == 1 and hi_mid >= p["tgt"]) or (d == -1 and lo_mid <= p["tgt"]):
                self.close(bid, self.fill_px(sym, -d, p["tgt"], "target"), "target", now)
            elif now >= p["hold_until"]:
                self.close(bid, self.fill_px(sym, -d, (hi_mid + lo_mid) / 2), "time", now)
            else:   # floating equity for the bot's challenge account (worst price this update, exit costs included)
                worst = lo_mid if d == 1 else hi_mid
                ur = d * (self.fill_px(sym, -d, worst) - p["entry"]) / p["risk"]
                c = self.chal.get(bid); b = self.bots.get(bid)
                if c and b: self.chal_check(bid, c["balance_pct"] + ur * b["g"].get("risk", 1.0), now)

    def append1(self, sym, ts, o, h, l, c, spread):
        idx = pd.Timestamp(ts, unit="s", tz="UTC")
        if idx <= self.df1[sym].index[-1]: return
        row = pd.DataFrame({"o": [o], "h": [h], "l": [l], "c": [c], "spread": [spread]}, index=[idx])
        self.df1[sym] = pd.concat([self.df1[sym], row]).tail(KEEP_DAYS * 1440)
        try:
            self.on_bar_close(sym)
        except Exception as e:
            log.exception("bar close %s: %s", sym, e)

    # ---------- feeds ----------
    async def btc_feed(self):
        while True:
            try:
                async with websockets.connect(BWS, ping_interval=20, open_timeout=20) as ws:
                    log.info("binance ws connected")
                    async for msg in ws:
                        k = json.loads(msg)["k"]; now = int(time.time()); px = float(k["c"])
                        self.quote["BTCUSD"] = dict(bid=px, ask=px, ts=now)
                        self.db.execute("INSERT OR REPLACE INTO live_ticks VALUES(?,?,?,?,?)", ("BTCUSD", now, px, px, "binance ws kline_1m"))
                        self.check_exits("BTCUSD", float(k["h"]), float(k["l"]), now)
                        if k["x"]:
                            self.append1("BTCUSD", k["t"] // 1000, float(k["o"]), float(k["h"]), float(k["l"]), px, np.nan)
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
                    if ts != last_ts and time.time() - ts >= 120 and time.time() - self.last_skip_log >= 60:
                        self.last_skip_log = time.time(); log.info("xau: skipping stale quote (%ds old)", int(time.time() - ts))
                    if ts != last_ts and time.time() - ts < 120:     # fresh tick only (market closed => stale)
                        last_ts = ts; mid = (bid + ask) / 2
                        self.quote["XAUUSD"] = dict(bid=bid, ask=ask, ts=ts)
                        self.db.execute("INSERT OR REPLACE INTO live_ticks VALUES(?,?,?,?,?)", ("XAUUSD", ts, bid, ask, "swissquote public bbo (elite)"))
                        b0 = ts - ts % 60; cb = self.cur1["XAUUSD"]
                        if cb and cb["t"] != b0:
                            # Dukascopy history is bid-based: store bid OHLC + spread, Market adds spread/2 back
                            hs = cb["sp"] / 2
                            self.append1("XAUUSD", cb["t"], cb["o"] - hs, cb["h"] - hs, cb["l"] - hs, cb["c"] - hs, cb["sp"])
                            cb = None
                        if cb is None: cb = dict(t=b0, o=mid, h=mid, l=mid, c=mid, sp=ask - bid)
                        cb["h"] = max(cb["h"], mid); cb["l"] = min(cb["l"], mid); cb["c"] = mid; cb["sp"] = ask - bid
                        self.cur1["XAUUSD"] = cb
                        self.check_exits("XAUUSD", mid, mid, ts)
                        self.db.commit()
                except Exception as e:
                    log.warning("xau quote: %s", e)
                await asyncio.sleep(5)

    async def housekeeping(self):
        n = 0     # 10-second ticks; the continuous breeder (ultron.breeder, run by run.sh) signals roster changes via results/roster_changed
        while True:
            await asyncio.sleep(10); n += 1
            now = int(time.time())
            if n % 6 == 0:
                for bid, c in self.chal.items():        # day roll for idle bots so 'daily loss used' resets at CE(S)T midnight
                    if c["status"] == "active" and day_key(now) != c["day_key"]: self.chal_roll_day(c, now); self.save_chal(c)
            ages = self.tick_ages(now)
            if n % 6 == 0:
                lb = {s: (str(self.df1[s].index[-1])[5:16] if s in self.df1 and len(self.df1[s]) else "-") for s in ("BTCUSD", "XAUUSD")}
                log.info("alive: tick age BTC %s XAU %s%s | last bar BTC %s XAU %s | %d bots, %d open",
                         self.fmt_age(ages["BTCUSD"]), self.fmt_age(ages["XAUUSD"]), "" if gold_open(now) else " (gold closed)",
                         lb["BTCUSD"], lb["XAUUSD"], len(self.bots), len(self.pos))
            if n % 30 == 0 or n == 1:      # every 5 min (was hourly), plus one right after start: the floor page treats > 10 min without one as stale
                self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), "heartbeat", None,
                                json.dumps(dict(bots=len(self.bots), open=len(self.pos), tick_age_s={k: (None if v is None else int(v)) for k, v in ages.items()}))))
            self.watchdog(now, ages)
            self.db.commit()
            if n % 30 == 0: self.save_live()
            try: self.dd_kill_check(now)
            except Exception as e: log.warning("dd kill: %s", e)
            try: self.save_risk_state()
            except Exception as e: log.warning("risk state: %s", e)
            flag = rfile("roster_changed")
            if os.path.exists(flag):
                os.remove(flag); self.reload_roster()
            elif n % 6 == 0:
                # safety net: a bot cut straight in the DB (no roster_changed flag) must stop trading within a minute, and any
                # open paper trade whose bot is gone is closed at market by reload_roster (until a price exists it keeps being
                # managed to its stop / target / max hold by check_exits and stays in floor equity via unreal_usd)
                try:
                    ids = {r[0] for r in self.db.execute("SELECT bot_id FROM live_bots WHERE status IN ('live','probation')")}
                    if ids != set(self.bots) or any(b not in ids for b in self.pos): self.reload_roster()
                except Exception as e: log.warning("roster sync: %s", e)

    def tick_ages(self, now):
        out = {}
        for s in ("BTCUSD", "XAUUSD"):
            q = self.quote.get(s); out[s] = (now - q["ts"]) if q and q.get("ts") else None
        return out

    @staticmethod
    def fmt_age(a): return "none" if a is None else f"{int(a)}s"

    def watchdog(self, now, ages):
        """Warn when a feed goes quiet (> 120s; gold only while its market is open); exit(2) past 600s so run.sh restarts us.
        Positions live in the DB (live_positions) and are reloaded on start, so a restart does not close trades."""
        go = gold_open(now)
        if go and not self.was_gold_open: self.gold_open_since = now        # stale clock for gold starts at the market open
        self.was_gold_open = go
        for s in ("BTCUSD", "XAUUSD"):
            if s == "XAUUSD" and not go: continue
            q = self.quote.get(s); base = max(q["ts"] if q and q.get("ts") else 0, self.t0, self.gold_open_since if s == "XAUUSD" else 0)
            age = now - base
            if age > TICK_DIE_S:
                log.error("watchdog: %s no fresh tick for %ds (> %ds); exiting rc=2 for a supervisor restart", s, age, TICK_DIE_S)
                self.db.commit(); logging.shutdown(); os._exit(2)        # hard exit: an exception inside asyncio.gather would only cancel tasks
            if age > TICK_WARN_S and now - self.last_stale_log.get(s, 0) >= 60:
                self.last_stale_log[s] = now; log.warning("watchdog: %s last tick %ds ago (> %ds)", s, age, TICK_WARN_S)

    async def main(self):
        self.bootstrap()
        await asyncio.gather(self.btc_feed(), self.xau_feed(), self.housekeeping())

if __name__ == "__main__":
    os.nice(10)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    open(os.path.join(ROOT, "logs", f"live_{FLOOR}.pid"), "w").write(str(os.getpid()))
    asyncio.run(Floor().main())
