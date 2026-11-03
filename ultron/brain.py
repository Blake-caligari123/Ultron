"""Ultron's search memory: learns WHERE in strategy space good candidates come from, and how big mutation steps should be.

1) Arm bandit (Thompson sampling). An arm is (family, timeframe) per market. Each randomly created candidate is a pull; it
   succeeds when its TRAINING fitness lands in the top quarter of recent random candidates on that market. New random bots
   pick their arm by sampling each arm's Beta posterior, so arms that keep producing strong candidates get explored more.
   EXPLORE (a fixed 30%) of all new random bots stay uniformly random, no arm may take more than MAX_ARM_SHARE of the guided
   picks, and old evidence decays (GAMMA per update), so no arm is ever written off for good, none can take over the search,
   and the brain follows regime changes instead of fitting itself to one stretch of old data.
   The floor's own diversity caps (same style, same indicator combo, same family; ultron/breeder.py) still apply on top.
2) Self-tuning mutation (1/5th success rule). If more than a fifth of mutated children beat their parent, steps grow;
   otherwise they shrink, within [SCALE_MIN, SCALE_MAX].

Honesty rules (same spirit as review H1 and 'What Ultron learned'):
 * only TRAINING fitness is used. Holdout / sealed results never reach the brain, so the unseen test stays unseen.
 * only randomly created candidates update the arms. Bred children copy their parents' traits, which would make whatever
   Ultron already favours look like it wins.
State persists in results/<floor_>brain.json."""
import time
from collections import deque
import numpy as np
from .db import rfile, read_json, write_json, SYMBOLS
from .engine import random_genome, FAMILIES, TF_NAME, NEW_MAIN_FAMS

EXPLORE = 0.30            # FIXED share of new bots that ignore the brain entirely (uniform over families, as before)
MAX_ARM_SHARE = 0.15      # no single arm may take more than this share of the recent guided picks (diversity, no take-over)
SHARE_WINDOW = 200        # guided picks remembered for that cap
GAMMA = 0.999             # per-candidate decay of arm evidence toward the prior (memory ~ 1000 random candidates per market)
PRIOR = (1.0, 3.0)        # Beta prior: a success rate of ~25% (the top-quarter threshold)
TOP_Q = 0.75              # success = training fitness at or above this quantile of recent random candidates
RECENT = 400              # random-candidate fitnesses kept per market for that threshold
MIN_RECENT = 40           # before this many, every arm is sampled uniformly
SCALE0, SCALE_MIN, SCALE_MAX = 0.15, 0.04, 0.40
PATH = lambda: rfile("brain.json")

class Brain:
    def __init__(self, fams=None, tfs=(0, 1, 2, 3), path=None):
        self.path = path or PATH()
        uniq = sorted(set(fams or NEW_MAIN_FAMS)); self.tfs = tuple(tfs)
        self.arms = [(f, t) for f in uniq for t in self.tfs]
        self.weights = {f: (fams or NEW_MAIN_FAMS).count(f) for f in uniq}     # uniform-explore mix (combos are 50% on the main floor)
        self.fams = list(fams or NEW_MAIN_FAMS)
        self.ab = {s: {a: list(PRIOR) for a in self.arms} for s in SYMBOLS}
        self.recent = {s: deque(maxlen=RECENT) for s in SYMBOLS}
        self.scale = {s: SCALE0 for s in SYMBOLS}
        self.mut = {s: [0, 0] for s in SYMBOLS}          # mutation children: [improved, total] since the last scale update
        self.pulls = {s: 0 for s in SYMBOLS}
        self.picks = {s: deque(maxlen=SHARE_WINDOW) for s in SYMBOLS}     # recent guided arms (in memory; rebuilds within minutes)
        self._load()

    # ---------- persistence ----------
    def _load(self):
        d = read_json(self.path) or {}
        for s in SYMBOLS:
            m = d.get(s) or {}
            for k, v in (m.get("ab") or {}).items():
                f, t = (int(x) for x in k.split(":"))
                if (f, t) in self.ab[s]: self.ab[s][(f, t)] = [float(v[0]), float(v[1])]
            self.recent[s].extend(m.get("recent") or [])
            self.scale[s] = float(min(max(m.get("scale", SCALE0), SCALE_MIN), SCALE_MAX)); self.pulls[s] = int(m.get("pulls", 0))

    def save(self):
        write_json(self.path, {s: dict(ab={f"{f}:{t}": [round(a, 4), round(b, 4)] for (f, t), (a, b) in self.ab[s].items()},
                                       recent=[round(x, 4) for x in self.recent[s]], scale=round(self.scale[s], 4), pulls=self.pulls[s])
                               for s in SYMBOLS} | {"updated": int(time.time())})

    # ---------- arms ----------
    def mean(self, sym, arm):
        a, b = self.ab[sym][arm]; return a / (a + b)

    def pick_arm(self, sym, rng):
        """Thompson sampling: one draw per arm from Beta(a, b), highest draw wins."""
        if len(self.recent[sym]) < MIN_RECENT: return None
        P = self.picks[sym]; cap = MAX_ARM_SHARE * max(len(P), 1)
        full = {a for a in set(P) if P.count(a) >= cap and len(P) >= 20}       # arms at their share cap sit this pick out
        draws = [(rng.beta(*self.ab[sym][a]), a) for a in self.arms if a not in full]
        if not draws: return None
        arm = max(draws)[1]; P.append(arm); return arm

    def propose(self, sym, rng):
        """A new random genome: EXPLORE uniform as before, otherwise from the arm the posterior favours. -> (genome, guided)"""
        arm = None if rng.random() < EXPLORE else self.pick_arm(sym, rng)
        if arm is None: return random_genome(rng, self.fams), False
        g = random_genome(rng, [arm[0]]); g["tf"] = arm[1]
        return g, True

    def learn(self, sym, results):
        """results: [(genome, train fitness)] of RANDOMLY created candidates only."""
        if not results: return
        rec = self.recent[sym]; thr = np.quantile(rec, TOP_Q) if len(rec) >= MIN_RECENT else None
        for _ in results:                                # decay every arm once per candidate toward the prior
            for a in self.arms:
                ab = self.ab[sym][a]; ab[0] = PRIOR[0] + GAMMA * (ab[0] - PRIOR[0]); ab[1] = PRIOR[1] + GAMMA * (ab[1] - PRIOR[1])
        for g, fit in results:
            arm = (int(g["fam"]), int(g["tf"]))
            if thr is not None and arm in self.ab[sym]:
                self.ab[sym][arm][0 if fit >= thr else 1] += 1.0
            rec.append(float(fit)); self.pulls[sym] += 1

    # ---------- mutation step size ----------
    def mutation_result(self, sym, improved):
        m = self.mut[sym]; m[0] += int(bool(improved)); m[1] += 1
        if m[1] >= 20:                                   # 1/5th success rule over windows of 20 mutated children
            self.scale[sym] = float(min(max(self.scale[sym] * (1.15 if m[0] / m[1] > 0.2 else 0.9), SCALE_MIN), SCALE_MAX))
            self.mut[sym] = [0, 0]

    # ---------- report (floor view) ----------
    def summary(self, top=6):
        from . import plain
        out = {}
        for s in SYMBOLS:
            rows = sorted(((self.mean(s, a), a) for a in self.arms), reverse=True)
            base = PRIOR[0] / sum(PRIOR)
            out[s] = dict(pulls=self.pulls[s], learning=len(self.recent[s]) >= MIN_RECENT, mutation_scale=round(self.scale[s], 3),
                          threshold=round(float(np.quantile(self.recent[s], TOP_Q)), 3) if len(self.recent[s]) >= MIN_RECENT else None,
                          focus=[dict(family=plain.FAM_NAME.get(FAMILIES[f], FAMILIES[f]), tf=TF_NAME[t], rate=round(100 * m, 1), lift=round(m / base, 2),
                                      evidence=round(sum(self.ab[s][(f, t)]) - sum(PRIOR), 1)) for m, (f, t) in rows[:top]],
                          avoid=[dict(family=plain.FAM_NAME.get(FAMILIES[f], FAMILIES[f]), tf=TF_NAME[t], rate=round(100 * m, 1), lift=round(m / base, 2),
                                      evidence=round(sum(self.ab[s][(f, t)]) - sum(PRIOR), 1)) for m, (f, t) in rows[-3:]])
        return out
