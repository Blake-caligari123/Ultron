import numpy as np, pytest
from ultron import brain as BR
from ultron.brain import Brain

def run_world(b, rng, good, n=3000, sym="XAUUSD"):
    """Synthetic world: every arm scores N(0,1) fitness; arm `good` scores N(1,1). Returns share of guided picks on `good`."""
    hits = guided = 0
    for i in range(n):
        g, gd = b.propose(sym, rng)
        arm = (g["fam"], g["tf"])
        fit = rng.normal(1.0 if arm == good else 0.0, 1.0)
        b.learn(sym, [(g, fit)])
        if i > n // 2 and gd: guided += 1; hits += arm == good
    return hits / max(guided, 1)

def test_bandit_finds_the_good_arm(tmp_path):
    b = Brain(path=str(tmp_path / "b.json")); rng = np.random.default_rng(0)
    good = (2, 1)                                             # EMA trend cross on 5m
    share = run_world(b, rng, good)
    assert 0.08 < share <= BR.MAX_ARM_SHARE + 0.02, share     # clearly favoured (uniform = 1/36 ~ 3%) but capped
    s = b.summary()["XAUUSD"]
    assert s["focus"][0]["family"] == "Trend Rider" and s["focus"][0]["tf"] == "5m"

def test_keeps_exploring(tmp_path):
    b = Brain(path=str(tmp_path / "b.json")); rng = np.random.default_rng(1)
    run_world(b, rng, (2, 1), n=1500)
    picks = [b.propose("XAUUSD", rng) for _ in range(2000)]
    assert abs(sum(not gd for _, gd in picks) / len(picks) - BR.EXPLORE) < 0.04   # fixed 30% stay fully random
    assert len({(g["fam"], g["tf"]) for g, _ in picks}) > 30                # most arms still get tried

def test_forgets_when_the_world_changes(tmp_path):
    b = Brain(path=str(tmp_path / "b.json")); rng = np.random.default_rng(2)
    run_world(b, rng, (2, 1), n=2000)
    share = run_world(b, rng, (6, 3), n=6000)                 # the edge moves to Momentum Burst 30m
    assert share > 0.08, share

def test_persistence_roundtrip(tmp_path):
    p = str(tmp_path / "b.json"); b = Brain(path=p); rng = np.random.default_rng(3)
    run_world(b, rng, (2, 1), n=600); b.save()
    c = Brain(path=p)
    assert c.ab["XAUUSD"][(2, 1)] == pytest.approx(b.ab["XAUUSD"][(2, 1)], abs=1e-3) and len(c.recent["XAUUSD"]) == len(b.recent["XAUUSD"])

def test_mutation_scale_follows_success(tmp_path):
    b = Brain(path=str(tmp_path / "b.json"))
    for _ in range(200): b.mutation_result("BTCUSD", True)
    assert b.scale["BTCUSD"] == BR.SCALE_MAX
    for _ in range(800): b.mutation_result("BTCUSD", False)
    assert b.scale["BTCUSD"] == BR.SCALE_MIN

def test_scalp_arms_are_level_families_only(tmp_path):
    b = Brain(fams=[8, 9, 10], tfs=(0, 1), path=str(tmp_path / "b.json")); rng = np.random.default_rng(4)
    run_world(b, rng, (9, 0), n=400)
    for _ in range(200):
        g, gd = b.propose("BTCUSD", rng); assert g["fam"] in (8, 9, 10) and (g["tf"] in (0, 1) or not gd)   # breeder.scalpify clamps the rest

def test_no_single_arm_takes_over_even_with_a_huge_edge(tmp_path):
    b = Brain(path=str(tmp_path / "b.json")); rng = np.random.default_rng(5)
    arms = []
    for i in range(4000):
        g, gd = b.propose("XAUUSD", rng); arm = (g["fam"], g["tf"])
        b.learn("XAUUSD", [(g, rng.normal(5.0 if arm == (11, 0) else 0.0, 1.0))])   # one arm vastly better than everything
        if i > 2000: arms.append(arm)
    top = max(arms.count(a) for a in set(arms)) / len(arms)
    assert top < 0.20, top                                    # <= 15% of guided (70%) + its uniform share of the 30% random
