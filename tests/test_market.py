"""The live floor evaluates indicator-family bots on a trimmed tail of history (market.live_tail); their signals must equal
the full-history ones. Level families are path-dependent (they stay armed on a level across chained entries), so the live
floor deliberately gives them its full window instead; test_level_families_need_full_window documents why."""
import numpy as np, pandas as pd, pytest
from ultron import engine as E
from ultron.market import Market, live_tail

@pytest.mark.parametrize("sym", ["XAUUSD", "BTCUSD"])
@pytest.mark.parametrize("tf", [0, 1, 2, 3])
def test_live_tail_matches_full_history(frames, sym, tf):
    df = frames[sym]
    full = Market(sym, df, tfs=(tf,)).tf[tf]; tail = Market(sym, live_tail(df, tf), tfs=(tf,)).tf[tf]
    assert full["ts"][-1] == tail["ts"][-1]
    fi, ti = E.Ind(full), E.Ind(tail)
    assert fi.get("atr")[-1] == pytest.approx(ti.get("atr")[-1], rel=1e-9)
    rng = np.random.default_rng(100 + tf); fired = 0
    for _ in range(120):
        g = E.random_genome(rng); g["tf"] = tf
        a, b = E.signals(g, full, fi)[-500:], E.signals(g, tail, ti)[-500:]
        assert np.array_equal(a, b), g
        fired += int((a != 0).sum())
    assert fired > 0

def test_live_tail_starts_on_day_boundary(frames):
    t = live_tail(frames["BTCUSD"], 1)
    assert t.index[0] == t.index[0].floor("1D") and len(t) < len(frames["BTCUSD"])

def test_level_families_need_full_window(frames):
    """Documents the path dependence: with a short tail, some recent level signals differ from full history."""
    sym, tf = "XAUUSD", 0; df = frames[sym]
    full = Market(sym, df, tfs=(tf,)).tf[tf]; fi = E.Ind(full)
    short = Market(sym, df[df.index >= (df.index[-1] - pd.Timedelta(days=3)).floor("1D")], tfs=(tf,)).tf[tf]; si = E.Ind(short)
    rng = np.random.default_rng(3); diff = 0
    for _ in range(80):
        g = E.random_genome(rng, E.LEVEL_FAMS); g["tf"] = tf
        diff += int((E.level_signal(g, full, fi, sym)[0][-300:] != E.level_signal(g, short, si, sym)[0][-300:]).sum())
    assert diff > 0
