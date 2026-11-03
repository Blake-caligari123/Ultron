"""Tests run against an isolated ULTRON_HOME (temp dir) so they never touch real data/, results/ or config/."""
import os, tempfile, shutil
HOME = tempfile.mkdtemp(prefix="ultron_test_home_")
os.environ["ULTRON_HOME"] = HOME
os.environ.setdefault("NUMBA_NUM_THREADS", "1")
import numpy as np, pandas as pd, pytest

def synth(sym, start, end, seed=1):
    """Random-walk 1m bars in the format of data/{sym}_m1.pkl (bid OHLC + spread for gold, trade prices for bitcoin)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    p0, vol = (2400.0, 0.00035) if sym == "XAUUSD" else (60000.0, 0.0007)
    c = p0 * np.exp(np.cumsum(rng.normal(0, vol, len(idx)))); o = np.r_[p0, c[:-1]]
    w = np.abs(rng.normal(0, vol * 0.6, len(idx))) * c
    df = pd.DataFrame(dict(o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c), index=idx)
    df["spread"] = 0.3 if sym == "XAUUSD" else np.nan
    return df

@pytest.fixture(scope="session")
def home():
    return HOME

@pytest.fixture(scope="session")
def frames():
    return {s: synth(s, "2026-08-01", "2026-11-01", i + 1) for i, s in enumerate(("XAUUSD", "BTCUSD"))}

def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(HOME, ignore_errors=True)
