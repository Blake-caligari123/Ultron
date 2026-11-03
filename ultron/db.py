import sqlite3, os, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR = os.environ.get("ULTRON_FLOOR", "main")          # "main" floor or the experimental "scalp" pit (fully separate DB + files)
def rfile(name, floor=None):
    """Per-floor results file: results/<name> for main, results/scalp_<name> for the scalp pit."""
    f = floor or FLOOR
    return os.path.join(ROOT, "results", name if f == "main" else f"{f}_{name}")
PATH = rfile("ultron.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started TEXT, kind TEXT, config TEXT, provenance TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS bots(bot_id INTEGER PRIMARY KEY, run_id TEXT, symbol TEXT, island INTEGER, born_gen INTEGER, died_gen INTEGER,
  parent_a INTEGER, parent_b INTEGER, origin TEXT, family TEXT, genome TEXT, train_fit REAL, train TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS generations(run_id TEXT, symbol TEXT, island INTEGER, gen INTEGER, best REAL, median REAL, alive INTEGER, born INTEGER, killed INTEGER, best_bot INTEGER);
CREATE TABLE IF NOT EXISTS evals(bot_id INTEGER, run_id TEXT, window TEXT, start TEXT, end TEXT, n INTEGER, net_r REAL, ret_pct REAL, max_dd_pct REAL, pf REAL, win REAL, exp_r REAL, t REAL);
CREATE TABLE IF NOT EXISTS trades(bot_id INTEGER, run_id TEXT, window TEXT, symbol TEXT, entry_ts INTEGER, exit_ts INTEGER, dir INTEGER, entry REAL, exit REAL, r REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS allocations(run_id TEXT, ts TEXT, bot_id INTEGER, symbol TEXT, weight REAL, basis TEXT);
CREATE TABLE IF NOT EXISTS live_bots(bot_id INTEGER PRIMARY KEY, symbol TEXT, genome TEXT, family TEXT, status TEXT, weight REAL, added_ts TEXT, removed_ts TEXT, reason TEXT,
  live_n INTEGER DEFAULT 0, live_r REAL DEFAULT 0, live_peak_r REAL DEFAULT 0, live_dd_r REAL DEFAULT 0, origin TEXT);
CREATE TABLE IF NOT EXISTS live_positions(bot_id INTEGER PRIMARY KEY, symbol TEXT, dir INTEGER, entry_ts INTEGER, entry REAL, stop REAL, tgt REAL, risk REAL, hold_until INTEGER);
CREATE TABLE IF NOT EXISTS live_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER, symbol TEXT, entry_ts INTEGER, exit_ts INTEGER, dir INTEGER, entry REAL, exit REAL, r REAL, reason TEXT, weight REAL);
CREATE TABLE IF NOT EXISTS live_events(ts TEXT, kind TEXT, bot_id INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS cand_log(ts INTEGER, sym TEXT, gen INTEGER, fam INTEGER, tf INTEGER, side INTEGER, trend INTEGER, sl REAL, tp REAL, hold_h REAL,
  ss INTEGER, slen INTEGER, fit REAL, n INTEGER, net_r REAL, cost_r REAL, origin TEXT);
CREATE INDEX IF NOT EXISTS cand_log_ts ON cand_log(ts);
CREATE TABLE IF NOT EXISTS gate_log(ts INTEGER, sym TEXT, fam INTEGER, tf INTEGER, stage TEXT);
CREATE TABLE IF NOT EXISTS lessons(ts TEXT, key TEXT, text TEXT, numbers TEXT);
CREATE TABLE IF NOT EXISTS live_ticks(symbol TEXT PRIMARY KEY, ts INTEGER, bid REAL, ask REAL, source TEXT);
CREATE TABLE IF NOT EXISTS live_challenge(bot_id INTEGER PRIMARY KEY, firm TEXT, attempt INTEGER, phase INTEGER, start_ts INTEGER, phase_start_ts INTEGER,
  balance_pct REAL, day_key INTEGER, day_start_pct REAL, worst_day_pct REAL, trading_days INTEGER, last_trade_day INTEGER, status TEXT, reason TEXT, updated INTEGER);
CREATE INDEX IF NOT EXISTS ix_trades ON trades(bot_id, window);
CREATE INDEX IF NOT EXISTS ix_evals ON evals(bot_id);
"""
def connect(path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")          # wait (not fail) when the breeder/live writer holds the lock
    for _ in range(10):                             # switching to WAL needs a moment with no other writer
        try: c.execute("PRAGMA journal_mode=WAL"); break
        except sqlite3.OperationalError: time.sleep(1)
    c.execute("PRAGMA synchronous=NORMAL"); c.executescript(SCHEMA)
    # migrations: $ risked per trade (risk manager), trading costs paid, promotion evidence
    for t, col, typ in (("live_positions", "risk_usd", "REAL"), ("live_trades", "risk_usd", "REAL"), ("live_positions", "cost_px", "REAL"),
                        ("live_trades", "cost_usd", "REAL"), ("live_bots", "evidence", "TEXT")):
        if col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
            try: c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError: pass
    return c
