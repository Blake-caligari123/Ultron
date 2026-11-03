import numpy as np, pytest
from ultron import engine as E
from ultron.market import Market

def bars(o, h, l, c):
    """Minimal tf dict for engine.simulate."""
    n = len(c); f = lambda x: np.asarray(x, np.float64)
    return dict(o=f(o), h=f(h), l=f(l), c=f(c), cost=np.zeros(n), slip=np.zeros(n), ts=np.arange(n, dtype=np.int64) * 60,
                roll=np.zeros(n), blk=np.zeros(n, np.int8), at=np.ones(n))

def sim(t, sig, sl=1.0, tp=2.0, hold=10**9, flip=0):
    return E.simulate(t["o"], t["h"], t["l"], t["c"], t["cost"], t["slip"], t["ts"], t["roll"], t["blk"], t["at"],
                      np.asarray(sig, np.int8), 0, len(t["c"]), sl, tp, hold, flip, 0.0, 0.0, 1.0)

def test_long_hits_target_next_bar():
    t = bars([100, 100, 100, 101], [100, 100.5, 102.5, 101], [100, 99.5, 99.8, 100.5], [100, 100, 101, 101])
    ei, xi, dr, ep, xp, R, rs, rk, mae = sim(t, [1, 0, 0, 0])
    assert list(rs) == [1] and R[0] == pytest.approx(2.0)          # entry at open of bar 1 (100), target 102 hit in bar 2

def test_stop_wins_same_bar_tie():
    t = bars([100, 100, 100, 100], [100, 100.2, 103, 100], [100, 99.9, 98, 100], [100, 100, 100, 100])
    *_, R, rs, _, _ = sim(t, [1, 0, 0, 0])
    assert list(rs) == [0] and R[0] == pytest.approx(-1.0)        # both stop and target inside bar 2: stop is assumed first

def test_gap_through_stop_fills_at_open():
    t = bars([100, 100, 97, 97], [100, 100.3, 97.5, 97], [100, 99.8, 96, 97], [100, 100, 97, 97])
    *_, R, rs, _, _ = sim(t, [1, 0, 0, 0])
    assert list(rs) == [0] and R[0] == pytest.approx(-3.0)        # gapped open at 97 is worse than the 99 stop

def test_metrics_basic():
    m = E.metrics(np.array([2.0, -1.0, 2.0, -1.0]))
    assert m["n"] == 4 and m["net_r"] == 2.0 and m["pf"] == 2.0 and m["win"] == 50.0
    assert E.metrics(np.array([]))["n"] == 0

@pytest.mark.parametrize("seed", range(20))
def test_genomes_stay_in_bounds(seed):
    rng = np.random.default_rng(seed)
    for fams in (None, E.LEVEL_FAMS):
        a, b = E.random_genome(rng, fams), E.random_genome(rng, fams)
        for g in (a, E.mutate(a, rng), E.crossover(a, b, rng)):
            for k, (lo, hi, _) in {**E.COMMON, **E.FAM[g["fam"]]}.items():
                if k == "tp": continue                                   # tp is re-derived from the reward:risk rule
                assert lo <= g[k] <= hi, (k, g[k])
            assert E.RR[0] - 1e-6 <= E.rr(g) <= E.RR[1] + 1e-6

def test_ind_cache_respects_budget_and_pins(frames):
    t = Market("BTCUSD", frames["BTCUSD"].iloc[:20000], tfs=(0,)).tf[0]
    ind = E.Ind(t, budget_mb=1)                                    # 1 MB: holds ~6 float arrays of 20k bars
    a = ind.get("atr"); ind.get("atr_ratio")
    for n in range(10, 60): ind.get("ema", n)
    assert ind.bytes <= ind.budget + a.nbytes * 2
    assert ("atr",) in ind.cache and ("atr_ratio",) in ind.cache   # pinned, never evicted
    assert ind.get("atr") is a
