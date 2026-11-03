"""Prop-firm challenge rules (public, cited) + a numba challenge simulator.

Sources (fetched 2026-11-03):
- FTMO 2-Step, https://ftmo.com/en/trading-objectives/ : Phase 1 (FTMO Challenge) profit target 10%,
  Phase 2 (Verification) 5%; Maximum Daily Loss 5% of initial capital, limit = balance at 00:00 CE(S)T minus 5%,
  equity-based (incl. open P/L, swaps, commissions); Maximum Loss static 10% of initial capital (equity);
  Minimum 4 trading days (a day with at least one position opened) per phase. Target reached with positions closed.
  No time limit is stated on that page.
- FundedNext Stellar 2-Step, https://help.fundednext.com/en/articles/17255124-stellar-2-step-challenge and
  https://fundednext.com/api_docs/general-rules/cfds/trading-objectives : Phase 1 8%, Phase 2 5% (of initial balance);
  Daily Loss Limit 5% of initial balance, incl. floating P/L, swap, commission, resets 00:00 server time;
  Maximum Loss 10% static; minimum 5 trading days per phase; no time limit. (Its 'Quick Strike' rule is not modelled.)

Simulator assumptions (ours, not the firms'):
- risk per trade = fixed % of initial balance (bot gene 'risk'); P&L additive on initial balance.
- floating equity low during a trade = balance + worst adverse excursion (MAE, in R, incl. costs) * risk.
- trading day boundary = 00:00 CET approximated as 23:00 UTC (no DST); server midnight for FundedNext same approx.
- each simulated attempt starts every `step_days` days; an attempt that hasn't passed both phases within
  `cap_days` calendar days counts as 'timeout' (the firms have no time limit; this is our scoring horizon).
"""
import numpy as np
from numba import njit

FIRMS = {
    "FTMO": dict(name="FTMO 2-Step", t1=0.10, t2=0.05, daily=0.05, maxloss=0.10, mindays=4,
                 src="https://ftmo.com/en/trading-objectives/"),
    "FundedNext": dict(name="FundedNext Stellar 2-Step", t1=0.08, t2=0.05, daily=0.05, maxloss=0.10, mindays=5,
                       src="https://help.fundednext.com/en/articles/17255124-stellar-2-step-challenge"),
}
DAY_SHIFT = 3600   # CET ~ UTC+1

@njit(cache=True)
def _attempts(ets, xts, R, mae, risk, w0, w1, step, cap, t1, t2, daily, maxloss, mindays):
    n = len(R)
    n_start = 0; n_pass = 0; n_breach = 0; n_p1 = 0; days_sum = 0.0
    s0 = w0
    k0 = 0
    while s0 + 20 * 86400 <= w1:
        n_start += 1
        while k0 < n and ets[k0] < s0: k0 += 1
        phase = 1; bal = 0.0; day = -1; dstart = 0.0; tdays = 0; lastd = -1; ph_start = s0
        res = 0; k = k0
        while k < n:
            if ets[k] > s0 + cap * 86400 or xts[k] > w1: break
            if ets[k] < ph_start: k += 1; continue
            dk = (ets[k] + 3600) // 86400
            if dk != day: day = dk; dstart = bal
            if dk != lastd: tdays += 1; lastd = dk
            r = risk
            low = bal + min(mae[k], 0.0) * r
            # daily: equity may not fall below day-start balance - daily (approx: entry day)
            if low < dstart - daily or low < -maxloss: res = -1; break
            xd = (xts[k] + 3600) // 86400
            if xd != day: day = xd; dstart = bal
            bal += R[k] * r
            if bal < dstart - daily or bal < -maxloss: res = -1; break
            tgt = t1 if phase == 1 else t2
            if bal >= tgt and tdays >= mindays:
                if phase == 1:
                    phase = 2; n_p1 += 1; bal = 0.0; tdays = 0; lastd = -1; day = -1
                    ph_start = ((xts[k] + 3600) // 86400 + 1) * 86400 - 3600   # next trading day
                else:
                    res = 1; days_sum += (xts[k] - s0) / 86400.0; break
            k += 1
        if res == 1: n_pass += 1
        elif res == -1: n_breach += 1
        s0 += step * 86400
    return n_start, n_pass, n_breach, n_p1, days_sum

def challenge_stats(r, risk_pct, w0_s, w1_s, firm="FTMO", step_days=3, cap_days=90, key="R"):
    """r: run() result. Returns pass/breach/timeout rates and mean days to pass (both phases)."""
    f = FIRMS[firm]
    ts = r["ts"]
    if len(r[key]) == 0:
        return dict(attempts=0, pass_rate=0.0, breach_rate=0.0, timeout_rate=1.0, phase1_rate=0.0, avg_days_to_pass=None)
    ets = ts[r["ei"]].astype(np.int64); xts = ts[r["xi"]].astype(np.int64)
    ns, npass, nb, np1, dsum = _attempts(ets, xts, r[key].astype(np.float64), r["mae"].astype(np.float64), risk_pct / 100.0,
                                          int(w0_s), int(w1_s), step_days, cap_days, f["t1"], f["t2"], f["daily"], f["maxloss"], f["mindays"])
    if ns == 0:
        return dict(attempts=0, pass_rate=0.0, breach_rate=0.0, timeout_rate=1.0, phase1_rate=0.0, avg_days_to_pass=None)
    return dict(attempts=int(ns), pass_rate=round(npass / ns, 3), breach_rate=round(nb / ns, 3), timeout_rate=round((ns - npass - nb) / ns, 3),
                phase1_rate=round(np1 / ns, 3), avg_days_to_pass=round(dsum / npass, 1) if npass else None)
