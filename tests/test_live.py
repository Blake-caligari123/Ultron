"""Paper-floor accounting without any network: positions open/close through the real Floor methods against a temp DB."""
import json, time, pytest
import ultron.live as L
from ultron.db import connect
from ultron.engine import random_genome
import numpy as np

@pytest.fixture
def floor(tmp_path, monkeypatch, frames):
    monkeypatch.setattr(L, "connect", lambda: connect(str(tmp_path / "ultron.db")))
    f = L.Floor(); f.df1 = {s: frames[s].iloc[-20000:] for s in frames}
    return f

def add_bot(f, bid, sym, g, w=0.05):
    f.db.execute("INSERT INTO live_bots(bot_id,symbol,genome,family,status,weight,added_ts,origin) VALUES(?,?,?,?,?,?,?,?)",
                 (bid, sym, json.dumps(g), "x", "live", w, "2026-11-01T00:00:00+0000", "test"))
    f.db.commit(); f.reload_roster()

def test_open_then_target_books_profit_and_costs(floor):
    g = random_genome(np.random.default_rng(1)); g.update(fam=2, sl=1.0, tp=2.0, hold_h=8.0)
    add_bot(floor, 1, "BTCUSD", g)
    floor.quote["BTCUSD"] = dict(bid=70000.0, ask=70000.0, ts=int(time.time()))
    floor.open(floor.bots[1], 1, 70000.0, 100.0, int(time.time()), 60)
    p = floor.pos[1]; assert p["stop"] == pytest.approx(69900) and p["tgt"] == pytest.approx(70200)
    floor.check_exits("BTCUSD", 70250.0, 70100.0, int(time.time()) + 60)
    assert 1 not in floor.pos
    r, reason, usd, cost = floor.db.execute("SELECT r, reason, r*risk_usd, cost_usd FROM live_trades").fetchone()
    # entry pays half spread (0.0125%) + slippage (0.01%); the target exit pays half spread only; stop distance 100 + entry costs = 1R
    entry = 70000 * (1 + 0.000125 + 0.0001); exit_ = 70200 * (1 - 0.000125)
    assert reason == "target" and r == pytest.approx((exit_ - entry) / (100 + entry - 70000)) and usd > 0 and cost > 0
    n, live_r = floor.db.execute("SELECT live_n, live_r FROM live_bots WHERE bot_id=1").fetchone()
    assert n == 1 and live_r == pytest.approx(r)

def test_cut_bot_closes_its_position(floor):
    g = random_genome(np.random.default_rng(2)); g.update(fam=2)
    add_bot(floor, 7, "BTCUSD", g)
    floor.quote["BTCUSD"] = dict(bid=70000.0, ask=70000.0, ts=int(time.time()))
    floor.open(floor.bots[7], -1, 70000.0, 100.0, int(time.time()), 60)
    floor.db.execute("UPDATE live_bots SET status='killed' WHERE bot_id=7"); floor.db.commit(); floor.reload_roster()
    assert 7 not in floor.pos and floor.db.execute("SELECT reason FROM live_trades WHERE bot_id=7").fetchone()[0] == "bot cut"

def test_bar_close_only_on_completed_tf_bars(floor, monkeypatch):
    calls = []
    monkeypatch.setattr(floor, "bot_on_bar", lambda b, sym, t, *a: calls.append((b["id"], int(t["ts"][-1]))))
    rng = np.random.default_rng(3)
    for bid, tf in ((1, 0), (2, 1)):
        g = random_genome(rng); g.update(fam=2, tf=tf); add_bot(floor, bid, "BTCUSD", g)
    df = floor.df1["BTCUSD"]; last = int(df.index[-1].timestamp())
    floor.last_tf_close = {}; floor.on_bar_close("BTCUSD")
    five_done = (last + 60) % 300 == 0
    assert (1, last) in calls and any(c[0] == 2 for c in calls) == five_done
    calls.clear(); floor.on_bar_close("BTCUSD"); assert calls == []           # same bars never evaluated twice
