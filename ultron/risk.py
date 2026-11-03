"""Central floor-wide risk manager for the PAPER floor. Every bot entry goes through RiskManager.check().
Limits live in config/floor_risk.json (reloaded when the file changes):
  max total open risk (% of equity), max open trades, max same-direction trades per symbol, daily floor loss limit
  (all new entries halt until the next Sydney day), max bots on the floor (enforced by the breeder).
Blocked signals are skipped and logged as 'blocked by floor limit'."""
import json, os, time
import pandas as pd
from .db import ROOT, CODE, FLOOR

def cfg_path(floor=None):
    """config/<floor>_risk.json under ULTRON_HOME, falling back to the copy shipped with the code."""
    f = floor or FLOOR
    name = "floor_risk.json" if f == "main" else f"{f}_risk.json"
    p = os.path.join(ROOT, "config", name)
    return p if os.path.exists(p) else os.path.join(CODE, "config", name)
CFG_PATH = cfg_path()
_cache = {}

def load_cfg(floor=None):
    p = cfg_path(floor) if floor else CFG_PATH
    m = os.path.getmtime(p); c = _cache.setdefault(p, {})
    if c.get("mtime") != m:
        with open(p) as f: c["cfg"] = json.load(f)
        c["mtime"] = m
    return c["cfg"]

def syd_day_start(ts=None):
    t = pd.Timestamp(ts if ts is not None else time.time(), unit="s", tz="UTC").tz_convert("Australia/Sydney")
    return int(t.normalize().timestamp())

def trade_usd(t):
    """$ result of a closed trade row (dict with r, risk_usd, weight). Old rows (before the risk manager) use weight x 5% x $10k."""
    if t.get("risk_usd") is not None: return t["r"] * t["risk_usd"]
    return t["r"] * (t.get("weight") or 0) * 0.05 * 10000

def _usd(r, risk_usd, weight):
    return r * risk_usd if risk_usd is not None else r * (weight or 0) * 500.0

class Ledger:
    """Closed-trade equity, read incrementally (only rows with a higher id than last time).
    Equity counts EVERY closed paper trade, whatever the bot's status now: retiring or killing a bot must not erase its losses.
    An intentional account reset is explicit: set "equity_reset_ts" (unix s) in the config; trades that exited before it are
    ignored. Changing the reset time or starting equity rebuilds the ledger from scratch."""
    def __init__(self, db):
        self.db = db; self.key = None

    def _reset(self, key):
        self.key = key; self.last_id = 0; self.start = key[1]; self.eq = key[1]; self.peak = key[1]; self.rows = []   # rows: (exit_ts, usd)

    def sync(self):
        cfg = load_cfg(); key = (int(cfg.get("equity_reset_ts", 0) or 0), float(cfg["equity_start_usd"]))
        if key != self.key: self._reset(key)
        new = self.db.execute("SELECT id, r, risk_usd, weight, exit_ts FROM live_trades WHERE id>? AND exit_ts>=? ORDER BY id",
                              (self.last_id, key[0])).fetchall()
        for i, r, u, w, x in new:
            d = _usd(r, u, w); self.eq += d; self.peak = max(self.peak, self.eq); self.rows.append((x, d)); self.last_id = i
        if new:
            day_ago = min(syd_day_start() - 86400, time.time() - 2 * 86400)     # keep ~2 days of rows for day P&L
            if self.rows and self.rows[0][0] < day_ago: self.rows = [x for x in self.rows if x[0] >= day_ago]
        return self

    def pnl_since(self, ts):
        return sum(d for x, d in self.rows if x >= ts)

class RiskManager:
    def __init__(self, db):
        self.db = db; self.last_block = {}; self.halt_day = None; self.blocked_today = 0; self.block_day = None
        self.dd_halted = False; self.ledger = Ledger(db)

    def equity(self):
        return self.ledger.sync().eq

    def day_pnl_usd(self, unreal_usd=0.0, now=None):
        return self.ledger.sync().pnl_since(syd_day_start(now)) + unreal_usd

    def peak_equity(self):
        """Highest closed-trade equity since start / equity_reset_ts (never below equity_start_usd)."""
        return self.ledger.sync().peak

    def floor_dd_pct(self, unreal_usd=0.0):
        """Peak-to-trough floor drawdown in % (<= 0), current equity including open (unrealised) P&L."""
        L = self.ledger.sync(); return 100 * ((L.eq + unreal_usd) / L.peak - 1) if L.peak > 0 else 0.0

    def dd_kill_hit(self, unreal_usd=0.0):
        """True when the floor-wide drawdown kill is armed ("max_floor_drawdown_pct" in config) and breached.
        Latches for the life of the process; after a restart it re-evaluates from the DB. To resume after a real kill,
        set "equity_reset_ts" (new peak from that time) or raise the threshold."""
        lim = load_cfg().get("max_floor_drawdown_pct")
        if lim is None: return False
        if self.dd_halted: return True
        if self.floor_dd_pct(unreal_usd) <= -float(lim): self.dd_halted = True
        return self.dd_halted

    def risk_usd_for(self, weight, equity, live_n=None):
        c = load_cfg(); rp = c["risk_per_trade"]
        if (weight or 0) <= 0: return 0.0              # weight 0 = switched off by the allocator: no risk, no trade (min_pct is NOT applied)
        pct = min(rp["max_pct"], max(rp["min_pct"], (weight or 0) * rp["pct_per_unit_weight"]))
        pb = c.get("probation_risk", {"min_live_trades": 30, "risk_mult": 0.25})
        if live_n is not None and live_n < pb["min_live_trades"]:     # new bots trade small until they have a live record
            pct = max(rp["min_pct"], pct * pb["risk_mult"])
        return equity * pct / 100

    def snapshot(self, positions, unreal_usd=0.0, now=None):
        cfg = load_cfg(); L = self.ledger.sync(); eq = L.eq
        open_risk = sum((p.get("risk_usd") or 0) for p in positions.values())
        day_closed = L.pnl_since(syd_day_start(now))
        day0_eq = eq - day_closed                                      # equity at the start of the Sydney day
        day_pnl = day_closed + unreal_usd
        return dict(equity=eq, open_risk_usd=open_risk, open_risk_pct=100 * open_risk / eq if eq > 0 else 0, open_trades=len(positions),
                    day_pnl_usd=day_pnl, day_pnl_pct=100 * day_pnl / day0_eq if day0_eq > 0 else 0, peak=L.peak, cfg=cfg)

    def check(self, positions, sym, d, weight, unreal_usd=0.0, now=None, live_n=None):
        """-> (ok, reason, risk_usd). positions: {bot_id: {sym, dir, risk_usd}}"""
        s = self.snapshot(positions, unreal_usd, now); c = s["cfg"]; now = now or time.time()
        day = syd_day_start(now)
        if self.block_day != day: self.block_day = day; self.blocked_today = 0
        risk = self.risk_usd_for(weight, s["equity"], live_n)
        if risk <= 0:
            return False, "allocator weight is 0 (bot switched off)", 0.0
        if not self.dd_halted and self.dd_kill_hit(unreal_usd):
            return False, "floor drawdown kill (%.2f%% from peak, limit %s%%)|FIRSTDD" % (self.floor_dd_pct(unreal_usd), c["max_floor_drawdown_pct"]), risk
        if self.dd_halted:
            return False, "floor drawdown kill active (%.2f%% from peak)" % self.floor_dd_pct(unreal_usd), risk
        if s["day_pnl_pct"] <= -c["daily_floor_loss_limit_pct"] or self.halt_day == day:
            first = self.halt_day != day; self.halt_day = day
            return False, ("daily floor loss limit hit (%.2f%% today)" % s["day_pnl_pct"]) + ("|FIRST" if first else ""), risk
        if s["open_trades"] >= c["max_open_trades"]:
            return False, f"{s['open_trades']} trades already open (max {c['max_open_trades']})", risk
        same = sum(1 for p in positions.values() if p["sym"] == sym and p["dir"] == d)
        if same >= c["max_same_direction_per_symbol"]:
            return False, f"{same} {'buy' if d > 0 else 'sell'} trades already open on this market (max {c['max_same_direction_per_symbol']})", risk
        if s["open_risk_usd"] + risk > c["max_total_open_risk_pct"] / 100 * s["equity"] + 1e-9:
            return False, f"risk in use {s['open_risk_pct']:.2f}% + {100 * risk / s['equity']:.2f}% would exceed {c['max_total_open_risk_pct']}%", risk
        return True, "", risk

    def should_log_block(self, bot_id, now):
        self.blocked_today += 1
        thr = load_cfg().get("blocked_event_throttle_s", 900)
        if now - self.last_block.get(bot_id, 0) >= thr:
            self.last_block[bot_id] = now; return True
        return False
