import sqlite3, os, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "results", "ultron.db")
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
CREATE TABLE IF NOT EXISTS live_ticks(symbol TEXT PRIMARY KEY, ts INTEGER, bid REAL, ask REAL, source TEXT);
CREATE INDEX IF NOT EXISTS ix_trades ON trades(bot_id, window);
CREATE INDEX IF NOT EXISTS ix_evals ON evals(bot_id);
"""
def connect():
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    c = sqlite3.connect(PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL"); c.executescript(SCHEMA)
    return c
