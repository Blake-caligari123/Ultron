import time, pytest
from ultron.db import connect
from ultron.risk import RiskManager, trade_usd, load_cfg, syd_day_start

@pytest.fixture
def db(tmp_path):
    return connect(str(tmp_path / "ultron.db"))

def add(db, r, risk_usd=100.0, exit_ts=None, weight=None):
    db.execute("INSERT INTO live_trades(bot_id,symbol,entry_ts,exit_ts,dir,entry,exit,r,reason,weight,risk_usd) VALUES(1,'XAUUSD',0,?,1,1,1,?,'stop',?,?)",
               (int(exit_ts or time.time()), r, weight, risk_usd))

def full_scan(db, since=0):
    start = load_cfg()["equity_start_usd"]; eq = pk = start
    for r, u, w in db.execute("SELECT r, risk_usd, weight FROM live_trades WHERE exit_ts>=? ORDER BY id", (since,)):
        eq += trade_usd(dict(r=r, risk_usd=u, weight=w)); pk = max(pk, eq)
    return eq, pk

def test_ledger_matches_full_scan_incrementally(db):
    rm = RiskManager(db)
    for k, r in enumerate([1.0, -2.0, 3.0, -1.5, 0.5, -4.0]):
        add(db, r, exit_ts=time.time() - 3 * 86400 if k < 2 else None)
        eq, pk = full_scan(db)
        assert rm.equity() == pytest.approx(eq) and rm.peak_equity() == pytest.approx(pk)
    today = sum(r for r, in db.execute("SELECT r FROM live_trades WHERE exit_ts>=?", (syd_day_start(),))) * 100
    assert rm.day_pnl_usd() == pytest.approx(today)

def test_legacy_rows_use_weight(db):
    add(db, 2.0, risk_usd=None, weight=0.1)
    assert RiskManager(db).equity() == pytest.approx(10000 + 2.0 * 0.1 * 500)

def test_limits(db):
    rm = RiskManager(db)
    ok, why, risk = rm.check({}, "XAUUSD", 1, 0.05)
    assert ok and risk == pytest.approx(10000 * 0.5 / 100)                     # weight 0.05 x 10 = 0.5% of equity
    assert not rm.check({}, "XAUUSD", 1, 0.0)[0]                               # switched-off bot never trades
    pos = {i: dict(sym="XAUUSD", dir=1, risk_usd=10) for i in range(3)}
    ok, why, _ = rm.check(pos, "XAUUSD", 1, 0.05); assert not ok and "buy trades already open" in why
    assert rm.check(pos, "XAUUSD", -1, 0.05)[0]                                # other direction is fine

def test_daily_loss_halts_new_entries(db):
    add(db, -5.0, risk_usd=100.0)                                              # -$500 = -5% today (limit 4%)
    rm = RiskManager(db); ok, why, _ = rm.check({}, "BTCUSD", 1, 0.05)
    assert not ok and why.startswith("daily floor loss limit") and why.endswith("|FIRST")
    ok, why, _ = rm.check({}, "BTCUSD", 1, 0.05); assert not ok and not why.endswith("|FIRST")

def test_probation_risk_until_30_live_trades(db):
    rm = RiskManager(db)
    full = rm.check({}, "XAUUSD", 1, 0.05)[2]                                  # no live_n: normal size (0.5%)
    small = rm.check({}, "XAUUSD", 1, 0.05, live_n=3)[2]                       # new bot: 0.25x, floored at min_pct 0.25%
    assert full == pytest.approx(50.0) and small == pytest.approx(25.0)
    assert rm.check({}, "XAUUSD", 1, 0.05, live_n=30)[2] == pytest.approx(full)
