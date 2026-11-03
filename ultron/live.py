"""Always-on PAPER floor. Live prices only, no broker, no orders.
BTCUSD: Binance public websocket (btcusdt@kline_1m) on port 443 -> closed 1m bars + intrabar highs/lows for exits.
XAUUSD: Swissquote public BBO quotes (bid/ask) polled every 5s -> 1m mid bars built from ticks.
Each roster bot evaluates its genome on every closed bar of its timeframe (1m/5m/15m/30m); paper positions are managed
on every tick / 1m update. Kills are performance-based only (prop-firm challenge tracking was removed at the user's request).
Learning (cut losers / breed / promote / rebalance) runs NONSTOP in a sibling low-priority process (ultron.breeder, run by run.sh)."""
import asyncio, json, os, time, subprocess, logging, tempfile
import numpy as np, pandas as pd, httpx, websockets
from .risk import RiskManager, load_cfg, syd_day_start
from .market import Market, live_tail, load_history
from .engine import signals, level_signal, LEVEL_FAMS, Ind, hold_bars, TF_MIN, entry_ok, entry_blocked
from . import levels
from .costs import COSTS, swap_units
from .db import connect, rfile, data_file, write_json, ROOT, FLOOR, SYMBOLS

log = logging.getLogger("ultron.live")
TF_SEC = {k: v * 60 for k, v in TF_MIN.items()}
SQ = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
BWS = "wss://stream.binance.com:443/ws/btcusdt@kline_1m"
KEEP_DAYS = 20
TICK_WRITE_S = 5          # live_ticks row (shown on the floor view) is written at most this often per symbol
TICK_WARN_S = 120; TICK_DIE_S = 600     # watchdog: warn when a market's last tick is older than this; exit(2) past the second
HEARTBEAT_EVERY = 30      # housekeeping ticks (10 s) between heartbeats: the floor page treats > 10 min without one as stale

def gold_open(ts):
    """Spot gold CFD hours (UTC, conservative): closed Fri 21:00 -> Sun 23:00 and the daily 21:00-23:00 break."""
    d = time.gmtime(ts); wd, h = d.tm_wday, d.tm_hour      # Mon=0
    if wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 23): return False
    return not (21 <= h < 23)

def now_s(): return time.strftime("%Y-%m-%dT%H:%M:%S%z")

class Floor:
    def __init__(self):
        self.db = connect()
        self.df1 = {}; self.cur1 = {"XAUUSD": None}; self.quote = {}; self.last_tf_close = {}; self.tick_written = {}
        self.dd_flattened = False
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
        now = int(time.time())
        for bid, p in list(self.pos.items()):        # a bot cut by the breeder closes its paper trade at the current price
            if bid in self.bots: continue
            mid = self.mid(p["sym"])
            if mid is None:
                df = self.df1.get(p["sym"]); mid = float(df["c"].iloc[-1]) if df is not None and len(df) else None
            if mid is None: continue        # no price yet: keep managing it to stop / target / max hold
            if bid in old_bots: self.bots[bid] = old_bots[bid]
            self.close(bid, *self.fill(p["sym"], -p["dir"], mid), "bot cut", now)
            self.bots.pop(bid, None)
        self.db.commit()
        log.info("roster %d bots, %d open paper positions", len(self.bots), len(self.pos))

    def unreal_usd(self):
        u = 0.0
        for p in self.pos.values():
            q = self.quote.get(p["sym"])
            if q and p.get("risk_usd"): u += p["dir"] * ((q["bid"] if p["dir"] > 0 else q["ask"]) - p["entry"]) / p["risk"] * p["risk_usd"]
        return u

    def save_risk_state(self):
        s = self.risk.snapshot(self.pos, self.unreal_usd()); c = s.pop("cfg")
        s.update(blocked_today=self.risk.blocked_today if self.risk.block_day else 0,
                 halted=self.risk.halt_day is not None and self.risk.halt_day == syd_day_start(),
                 dd_halted=self.risk.dd_halted, updated=int(time.time()), limits={k: v for k, v in c.items() if not k.startswith("_")})
        write_json(rfile("risk_state.json"), s)

    def event(self, kind, bot, detail, commit=True):
        self.db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (now_s(), kind, bot, json.dumps(detail, default=str)))
        if commit: self.db.commit()

    # ---------- history ----------
    def bootstrap(self):
        for sym in SYMBOLS:
            df = load_history(sym)
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
        to = (pd.Timestamp.now("UTC") + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            out = {}
            with tempfile.TemporaryDirectory(prefix=f"ultron_dk1_{FLOOR}_") as d:   # private folder: the two floors never share files
                for p in ("bid", "ask"):
                    f = []; r = None
                    for attempt in range(3):
                        for x in os.listdir(d): os.remove(os.path.join(d, x))
                        r = subprocess.run(["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", frm, "-to", to, "-t", "m1", "-p", p, "-f", "csv", "-dir", d, "-s"],
                                           timeout=240, capture_output=True, text=True)
                        f = [x for x in os.listdir(d) if x.endswith(".csv")]
                        if f: break
                        time.sleep(5)
                    if not f:
                        raise RuntimeError(f"no {p} file from dukascopy after 3 tries (rc={r.returncode}: {(r.stderr or r.stdout)[-160:].strip()})")
                    x = pd.read_csv(os.path.join(d, f[0])); x.index = pd.to_datetime(x.timestamp, unit="ms", utc=True); out[p] = x
            t = out["bid"].rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
            t["spread"] = (out["ask"]["close"] - out["bid"]["close"]).reindex(t.index).clip(lower=0)
            t = t.iloc[:-1]
            self.df1["XAUUSD"] = pd.concat([df, t]).pipe(lambda d: d[~d.index.duplicated(keep="last")]).sort_index()
        except Exception as e:
            log.warning("XAU dukascopy top-up failed: %s", e)
        log.info("XAU history to %s", self.df1["XAUUSD"].index[-1])

    def save_live(self):
        if FLOOR != "main": return            # only the main floor writes the shared live-bar cache
        for sym, df in self.df1.items():
            p = data_file(f"{sym}_m1_live.pkl"); tmp = f"{p}.tmp"
            df.tail(KEEP_DAYS * 1440).to_pickle(tmp); os.replace(tmp, p)    # atomic: the breeder may be reading it

    # ---------- prices & costs ----------
    def mid(self, sym):
        q = self.quote.get(sym)
        return (q["bid"] + q["ask"]) / 2 if q else None

    def fill(self, sym, side, ref_mid, exit_kind="market"):
        """-> (fill price, cost paid in price units). side=+1 buy, -1 sell. XAU uses the live bid/ask quote when available.
        Cost = half spread + commission + slippage (no slippage on target fills)."""
        c = COSTS[sym]
        if sym == "XAUUSD":
            q = self.quote.get(sym); slip = c["slip_side"] if exit_kind != "target" else 0.0
            half = max((q["ask"] - q["bid"]) / 2 if q else 0, c["spread_floor"] / 2)
            cost = half + c["commission_side"] + slip
        else:
            slip = ref_mid * c["slip_frac"] if exit_kind != "target" else 0.0
            cost = ref_mid * c["spread_frac"] / 2 + c["commission_side"] + slip
        return ref_mid + side * cost, cost

    # ---------- bot logic ----------
    def on_bar_close(self, sym):
        df = self.df1[sym]; last1 = int(df.index[-1].timestamp())
        for tf, sec in TF_SEC.items():
            bots = [b for b in self.bots.values() if b["sym"] == sym and b["g"]["tf"] == tf]
            if not bots: continue
            # the tf bar holding the newest 1m bar is complete only when that 1m bar is its last minute
            bar_open = last1 - (last1 % sec)
            if bar_open + sec != last1 + 60 or self.last_tf_close.get((sym, tf)) == bar_open: continue
            # Indicator families have bounded look-backs, so a trimmed tail gives identical signals (tests/test_market.py).
            # Level families keep the full live window: their setups stay armed on a level across chained entries, with no
            # bounded memory, so they see exactly the history they always did.
            src = df if any(b["g"]["fam"] in LEVEL_FAMS for b in bots) else live_tail(df, tf)
            mk = Market(sym, src, tfs=(tf,)); t = mk.tf[tf]
            if not len(t["ts"]) or int(t["ts"][-1]) != bar_open: continue   # no data in that bar (market closed)
            self.last_tf_close[(sym, tf)] = bar_open
            ind = Ind(t, budget_mb=64); atr = ind.get("atr")[-1]; mid = t["c"][-1]; now = bar_open + sec
            rt = 2 * (t["cost"][-1] + t["slip"][-1])
            for b in bots:
                try: self.bot_on_bar(b, sym, t, ind, atr, mid, now, sec, rt)
                except Exception as e: log.exception("bot %s on %s %s: %s", b["id"], sym, tf, e)

    def bot_on_bar(self, b, sym, t, ind, atr, mid, now, sec, rt):
        g = b["g"]; lvl_stop = None
        if g["fam"] in LEVEL_FAMS:
            sg, stp = level_signal(g, t, ind, sym); s = int(sg[-1]); lvl_stop = float(stp[-1])
            if s != 0:      # same skip rules as the backtest: stop <= 3 ATR away, target >= 3x round-trip cost
                dist = s * (mid - lvl_stop)
                if not (0 < dist <= levels.MAX_STOP_ATR * atr and g["tp"] * dist >= levels.MIN_TP_COST_MULT * rt): s = 0
        else:
            s = int(signals(g, t, ind)[-1])
        p = self.pos.get(b["id"])
        if p and g["flip"] and s == -p["dir"]:
            self.close(b["id"], *self.fill(sym, -p["dir"], mid), "flip", now); p = None
        if s != 0 and p is None:      # same entry filters as the backtest: gold 20-23 UTC blocked, cost <= 0.3 ATR, stop >= 8x cost
            dist = s * (mid - lvl_stop) if lvl_stop is not None else g["sl"] * atr
            if entry_blocked(sym, now) or not entry_ok(dist, rt, atr): s = 0
        if p is None and s != 0 and atr > 0:
            self.open(b, s, mid, atr, now, sec, lvl_stop)

    def dd_kill_check(self, now=None):
        """Floor-wide peak-to-trough kill: on the first breach log 'floor_dd_kill' and flatten every open paper position."""
        if self.dd_flattened or not self.risk.dd_kill_hit(self.unreal_usd()): return False
        self.dd_flattened = True; now = int(now or time.time())
        self.event("floor_dd_kill", None, dict(dd_pct=round(self.risk.floor_dd_pct(self.unreal_usd()), 2), limit=load_cfg().get("max_floor_drawdown_pct"), open=len(self.pos)))
        for bid, p in list(self.pos.items()):
            m = self.mid(p["sym"])
            if m: self.close(bid, *self.fill(p["sym"], -p["dir"], m), "floor dd kill", now)
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
        entry, cost_px = self.fill(sym, d, mid)
        dist = g["sl"] * atr; tdist = g["tp"] * atr
        if lvl_stop is not None:            # level bots: stop just beyond the level / wick, target = tp x R
            dist = d * (mid - lvl_stop); tdist = g["tp"] * dist
        p = dict(sym=sym, dir=d, entry_ts=now, entry=entry, stop=mid - d * dist, tgt=mid + d * tdist, risk=dist + abs(entry - mid),
                 hold_until=now + hold_bars(g) * sec, risk_usd=risk_usd, cost_px=cost_px)
        self.pos[b["id"]] = p
        self.db.execute("INSERT OR REPLACE INTO live_positions(bot_id,symbol,dir,entry_ts,entry,stop,tgt,risk,hold_until,risk_usd,cost_px) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (b["id"], sym, d, now, entry, p["stop"], p["tgt"], p["risk"], p["hold_until"], risk_usd, cost_px))
        self.event("open", b["id"], dict(sym=sym, dir=d, entry=round(entry, 3), stop=round(p["stop"], 3), tgt=round(p["tgt"], 3)))

    def close(self, bid, px, exit_cost, reason, ts=None):
        p = self.pos.pop(bid); ts = int(ts or time.time())
        days = swap_units(p["sym"], p["entry_ts"], ts)        # broker rollovers crossed (server midnight, x3 Wed for gold)
        pnl = p["dir"] * (px - p["entry"]) - days * COSTS[p["sym"]]["swap_day_frac"] * p["entry"]
        R = pnl / p["risk"]; w = self.bots.get(bid, {}).get("w", 0.0)
        cost_usd = ((p.get("cost_px") or 0) + exit_cost) / p["risk"] * (p.get("risk_usd") or 0)
        self.db.execute("INSERT INTO live_trades(bot_id,symbol,entry_ts,exit_ts,dir,entry,exit,r,reason,weight,risk_usd,cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (bid, p["sym"], p["entry_ts"], ts, p["dir"], p["entry"], px, R, reason, w, p.get("risk_usd"), cost_usd))
        self.db.execute("DELETE FROM live_positions WHERE bot_id=?", (bid,))
        self.db.execute("""UPDATE live_bots SET live_n=live_n+1, live_r=live_r+?, live_peak_r=MAX(live_peak_r, live_r+?),
                           live_dd_r=MIN(live_dd_r, live_r+?-MAX(live_peak_r, live_r+?)) WHERE bot_id=?""", (R, R, R, R, bid))
        if bid in self.bots: self.bots[bid]["n"] = self.bots[bid].get("n", 0) + 1
        self.event("close", bid, dict(sym=p["sym"], reason=reason, exit=round(px, 3), r=round(R, 3)), commit=False)

    def check_exits(self, sym, hi_mid, lo_mid, now):
        for bid, p in list(self.pos.items()):
            if p["sym"] != sym: continue
            d = p["dir"]
            if (d == 1 and lo_mid <= p["stop"]) or (d == -1 and hi_mid >= p["stop"]):
                q = self.mid(sym)                      # gapped through the stop (gold or BTC): fill at the worse of stop and current market
                ref = p["stop"] if q is None else (min(p["stop"], q) if d == 1 else max(p["stop"], q))
                self.close(bid, *self.fill(sym, -d, ref), "stop", now)
            elif (d == 1 and hi_mid >= p["tgt"]) or (d == -1 and lo_mid <= p["tgt"]):
                self.close(bid, *self.fill(sym, -d, p["tgt"], "target"), "target", now)
            elif now >= p["hold_until"]:
                self.close(bid, *self.fill(sym, -d, (hi_mid + lo_mid) / 2), "time", now)

    def append1(self, sym, ts, o, h, l, c, spread):
        idx = pd.Timestamp(ts, unit="s", tz="UTC")
        if idx <= self.df1[sym].index[-1]: return
        row = pd.DataFrame({"o": [o], "h": [h], "l": [l], "c": [c], "spread": [spread]}, index=[idx])
        self.df1[sym] = pd.concat([self.df1[sym], row]).tail(KEEP_DAYS * 1440)
        try:
            self.on_bar_close(sym)
        except Exception as e:
            log.exception("bar close %s: %s", sym, e)

    def tick(self, sym, ts, bid, ask, source):
        """Remember the quote; persist it for the floor view at most every TICK_WRITE_S seconds."""
        self.quote[sym] = dict(bid=bid, ask=ask, ts=ts)
        if ts - self.tick_written.get(sym, 0) >= TICK_WRITE_S:
            self.tick_written[sym] = ts
            self.db.execute("INSERT OR REPLACE INTO live_ticks VALUES(?,?,?,?,?)", (sym, ts, bid, ask, source))

    # ---------- feeds ----------
    async def btc_feed(self):
        back = 5
        while True:
            try:
                async with websockets.connect(BWS, ping_interval=20, open_timeout=20) as ws:
                    log.info("binance ws connected"); back = 5
                    async for msg in ws:
                        k = json.loads(msg)["k"]; now = int(time.time()); px = float(k["c"])
                        self.tick("BTCUSD", now, px, px, "binance ws kline_1m")
                        self.check_exits("BTCUSD", float(k["h"]), float(k["l"]), now)
                        if k["x"]:
                            self.append1("BTCUSD", k["t"] // 1000, float(k["o"]), float(k["h"]), float(k["l"]), px, np.nan)
                        self.db.commit()
            except Exception as e:
                log.warning("binance ws: %s; reconnect in %ss", e, back); await asyncio.sleep(back); back = min(back * 2, 60)

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
                        self.tick("XAUUSD", ts, bid, ask, "swissquote public bbo (elite)")
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
        flag = rfile("roster_changed")
        while True:
            await asyncio.sleep(10); n += 1
            now = int(time.time())
            ages = self.tick_ages(now)
            if n % 6 == 0:
                lb = {s: (str(self.df1[s].index[-1])[5:16] if s in self.df1 and len(self.df1[s]) else "-") for s in SYMBOLS}
                log.info("alive: tick age BTC %s XAU %s%s | last bar BTC %s XAU %s | %d bots, %d open",
                         self.fmt_age(ages["BTCUSD"]), self.fmt_age(ages["XAUUSD"]), "" if gold_open(now) else " (gold closed)",
                         lb["BTCUSD"], lb["XAUUSD"], len(self.bots), len(self.pos))
            if n % HEARTBEAT_EVERY == 0 or n == 1:      # every 5 min, plus one right after start
                self.event("heartbeat", None, dict(bots=len(self.bots), open=len(self.pos),
                           tick_age_s={k: (None if v is None else int(v)) for k, v in ages.items()}), commit=False)
            self.watchdog(now, ages)
            self.db.commit()
            if n % 30 == 0:
                try: await asyncio.to_thread(self.save_live)
                except Exception as e: log.warning("save live bars: %s", e)
            try: self.dd_kill_check(now)
            except Exception as e: log.warning("dd kill: %s", e)
            try: self.save_risk_state()
            except Exception as e: log.warning("risk state: %s", e)
            if os.path.exists(flag):
                os.remove(flag); self.reload_roster()
            elif n % 6 == 0:
                # safety net: a bot cut straight in the DB (no roster_changed flag) must stop trading within a minute, and any
                # open paper trade whose bot is gone is closed at market by reload_roster (until a price exists it keeps being
                # managed to its stop / target / max hold by check_exits and stays in floor equity via unreal_usd)
                try:
                    rows = self.db.execute("SELECT bot_id, weight FROM live_bots WHERE status IN ('live','probation')").fetchall()
                    ids = {r[0] for r in rows}
                    if ids != set(self.bots) or any(b not in ids for b in self.pos): self.reload_roster()
                    else:
                        for bid, w in rows: self.bots[bid]["w"] = w or 0.0     # weights can change without a roster change
                except Exception as e: log.warning("roster sync: %s", e)

    def tick_ages(self, now):
        out = {}
        for s in SYMBOLS:
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
        for s in SYMBOLS:
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
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", f"live_{FLOOR}.pid"), "w") as f: f.write(str(os.getpid()))
    asyncio.run(Floor().main())
