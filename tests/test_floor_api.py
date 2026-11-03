import json, time, pytest
from fastapi.testclient import TestClient
from ultron.db import connect, rfile
from ultron.engine import random_genome, FAMILIES
import numpy as np

@pytest.fixture(scope="module")
def client():
    db = connect(rfile("ultron.db", "main")); rng = np.random.default_rng(0); now = int(time.time())
    for bid in range(1, 7):
        g = random_genome(rng); st = "live" if bid <= 4 else "killed"
        db.execute("INSERT INTO bots(bot_id,run_id,symbol,island,born_gen,origin,family,genome,status) VALUES(?,?,?,?,?,?,?,?,?)",
                   (bid, "breeder", "XAUUSD", 99, bid, "breeder", FAMILIES[g["fam"]], json.dumps(g), "alive"))
        db.execute("INSERT INTO live_bots(bot_id,symbol,genome,family,status,weight,added_ts,removed_ts,reason,origin) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (bid, "XAUUSD", json.dumps(g), FAMILIES[g["fam"]], st, 0.25 if st == "live" else 0, "2026-11-01T00:00:00+0000",
                    None if st == "live" else "2026-11-02T00:00:00+0000", None if st == "live" else "lost -6.5R live", "breeder"))
    for k in range(50):
        db.execute("INSERT INTO live_trades(bot_id,symbol,entry_ts,exit_ts,dir,entry,exit,r,reason,weight,risk_usd,cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (1 + k % 6, "XAUUSD", now - 4000 + k * 60, now - 3600 + k * 60, 1, 2400, 2402, (-1.0, 2.0)[k % 2], "target", 0.25, 50.0, 1.0))
    db.execute("INSERT INTO live_positions(bot_id,symbol,dir,entry_ts,entry,stop,tgt,risk,hold_until,risk_usd,cost_px) VALUES(1,'XAUUSD',1,?,2400,2390,2420,10.2,?,50,0.2)", (now - 60, now + 3600))
    db.execute("INSERT OR REPLACE INTO live_ticks VALUES('XAUUSD',?,2405,2405.3,'t')", (now,))
    db.execute("INSERT INTO live_events VALUES('2026-11-03T10:00:00+1100','open',1,?)", (json.dumps(dict(sym="XAUUSD", dir=1, entry=2400)),))
    db.commit()
    from ultron.floor import app
    return TestClient(app)

def test_state_numbers(client):
    s = client.get("/api/state?floor=main").json(); S = s["summary"]
    assert S["bots_alive"] == 4 and S["trades_total"] == 50 and len(s["killed"]) == 2
    assert S["realised_usd"] == pytest.approx(25 * (-50) + 25 * 100)
    assert S["open_usd"] == pytest.approx(round(5 / 10.2, 3) * 50, abs=0.01)
    assert s["curve"][-1][1] == pytest.approx(10000 + S["realised_usd"])
    b1 = next(b for b in s["bots"] if b["bot_id"] == 1)
    assert b1["position"]["dir"] == 1 and b1["name"].startswith("Gold ") and len(b1["spark"]) > 0
    assert s["events"][0]["name"] == b1["name"]

def test_results_and_learned(client):
    r = client.get("/api/results?floor=main").json()
    assert len(r["promoted"]) == 4 and r["killed"][0]["reason"] == "lost -6.5R live"
    assert sum(b["live"]["trades"] for b in r["promoted"] + r["killed"]) == 50
    assert client.get("/api/learned?floor=main").status_code == 200

def test_heartbeat_staleness(client):
    s = client.get("/api/state?floor=main").json()
    assert s["stale"] is True and s["heartbeat_age_s"] is None                # no heartbeat written yet
    db = connect(rfile("ultron.db", "main"))
    db.execute("INSERT INTO live_events VALUES(?,?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%S%z"), "heartbeat", None, "{}")); db.commit()
    import ultron.floor as F; F._cache.clear()
    s = client.get("/api/state?floor=main").json()
    assert s["stale"] is False and s["heartbeat_age_s"] < 60

def test_scalp_floor_empty_and_health(client):
    s = client.get("/api/state?floor=scalp").json()
    assert s["floor"] == "scalp" and s["summary"]["bots_alive"] == 0 and s["curve"] == []
    assert client.get("/api/health").json()["ok"] is True

def test_static_index(client):
    r = client.get("/"); assert r.status_code == 200 and "ULTRON" in r.text
