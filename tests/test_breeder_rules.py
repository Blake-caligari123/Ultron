import numpy as np, pytest
from ultron import evolve as ev, breeder as B
from ultron.engine import random_genome, signature
from ultron.market import Market

def g(fam, tf=1, side=0, seed=0, **kw):
    x = random_genome(np.random.default_rng(seed), [fam]); x.update(tf=tf, side=side, **kw); return x

def test_diversity_caps():
    G = dict(B.GUARDS)
    floor = [g(2, tf=1, seed=1), g(4, seed=9), g(5, seed=3)]
    assert B.diversity_block(g(6, tf=1, seed=50), floor, floor, G) is None
    # classic families: their combo identity IS the family, so the 2-per-combo cap stops a third one
    assert "indicator combo" in B.diversity_block(g(2, tf=3, side=1, seed=5), floor + [g(2, tf=2, seed=6)], floor + [g(2, tf=2, seed=6)], G)
    # Combo family: every bot can use a different indicator combination, so the family cap is what stops a take-over
    rng = np.random.default_rng(0); combos = []
    while len(combos) < G["max_same_family"]:
        c = random_genome(rng, [11]); c["tf"] = len(combos) % 4; c["side"] = len(combos) // 4
        if signature(c) not in {signature(x) for x in combos}: combos.append(c)
    new = random_genome(rng, [11]); new.update(tf=0, side=2)
    while signature(new) in {signature(x) for x in combos}: new = random_genome(rng, [11]); new.update(tf=0, side=2)
    assert "strategy family" in B.diversity_block(new, combos, combos, G)
    assert B.diversity_block(new, combos[:-1], combos[:-1], G) is None
    two = [dict(combos[0], tf=1, side=1), dict(combos[1], tf=1, side=1)]
    assert B.diversity_block(dict(new, tf=1, side=1), two, two, G) == "same style already on the floor"

def test_corr_helper():
    a = np.r_[np.zeros(10), 1.0, -1.0, 2.0]; assert B.corr(a, a) == pytest.approx(1.0)
    assert B.corr(a, np.zeros(13)) == 0.0 and B.corr(a, a[:5]) == 0.0

def test_earned_fitness_uses_raw_r(frames):
    ctx = ev.Ctx("BTCUSD", Market("BTCUSD", frames["BTCUSD"]))
    rng = np.random.default_rng(1); W = ("2026-08-01", "2026-10-15")
    for _ in range(40):
        x = random_genome(rng, [2]); x["tf"] = 0
        r = ctx.bt(x, W)
        if len(r["R"]) >= 60 and not np.allclose(r["R"], r["Ra"]): break
    raw = ev.year_split_fitness(r, W, 60, None, key="R")[0]; adj = ev.year_split_fitness(r, W, 60, None)[0]
    assert ev.fitness(ctx, x, W, min_n=60, key="R")[0] == pytest.approx(raw, rel=1e-6) or raw == adj

def test_plateau_with_earned_key(frames):
    """Regression: plateau_ok's gene loop used to shadow its `key` argument."""
    ctx = ev.Ctx("BTCUSD", Market("BTCUSD", frames["BTCUSD"])); rng = np.random.default_rng(2)
    x = random_genome(rng, [11]); x["tf"] = 1
    ok, med = ev.plateau_ok(ctx, x, 0.1, rng, ("2026-08-01", "2026-10-15"), min_n=10, k=3, key="R")
    assert isinstance(med, float)
