"""Retail MT5 cost model (per side, in price units). Assumptions documented in COSTS dict."""
COSTS = {
    "XAUUSD": {
        # spread: real Dukascopy per-bar ask-bid, floored at 0.25 USD (typical retail MT5 gold 0.15-0.40).
        "spread_floor": 0.25,
        # ECN/raw-style commission $7 per 100oz lot round trip = $0.035/oz per side.
        "commission_side": 0.035,
        # slippage on market & stop fills (not on TP limit fills): $0.05/oz per fill.
        "slip_side": 0.05,
        # overnight swap, charged both directions, per broker rollover (00:00 server time = Europe/Athens, Mon-Fri nights, x3 Wednesday night): 0.015% of price.
        "swap_day_frac": 0.00015,
        "contract": "1 lot = 100 oz",
    },
    "BTCUSD": {
        # BTCUSD CFD on retail MT5: spread ~0.025% of price (~$19 at $77k), commission included.
        "spread_frac": 0.00025,
        "commission_side": 0.0,
        # slippage 0.01% of price per market/stop fill.
        "slip_frac": 0.0001,
        # crypto CFD financing ~20%/yr, charged at every server-midnight rollover, 7 days a week: 0.055% of price per rollover.
        "swap_day_frac": 0.00055,
        "contract": "1 lot = 1 BTC",
    },
}

# ---- broker rollover schedule (review S1): swap is charged at server midnight (EET/EEST = 22:00 UTC winter, 21:00 UTC summer),
# not at UTC midnight. Gold: Mon-Fri server nights, triple on Wednesday night. Bitcoin: every night.
import numpy as _np, pandas as _pd
ROLL_W = {"XAUUSD": (1, 1, 3, 1, 1, 0, 0), "BTCUSD": (1, 1, 1, 1, 1, 1, 1)}   # weight of the rollover that ends the night of Mon..Sun
SERVER_TZ = "Europe/Athens"

def roll_cum(sym, ts):
    """Cumulative swap units charged at rollovers up to and including unix time(s) ts. Swap for a hold = roll_cum(exit) - roll_cum(entry)."""
    ts = _np.atleast_1d(_np.asarray(ts, dtype=_np.int64))
    if len(ts) == 0: return _np.zeros(0)
    loc = _pd.DatetimeIndex(_pd.to_datetime(ts, unit="s", utc=True)).tz_convert(SERVER_TZ)
    dnum = ((loc.tz_localize(None).normalize() - _pd.Timestamp("1970-01-01")) // _pd.Timedelta(days=1)).values.astype(_np.int64)  # server-local day number
    d0, d1 = int(dnum.min()), int(dnum.max())
    days = _np.arange(d0, d1 + 1)
    night_wd = (_pd.to_datetime(days - 1, unit="D").day_of_week).values      # the midnight that starts day d ends the night of day d-1
    w = _np.asarray(ROLL_W[sym], dtype=_np.float64)[night_wd]
    cum = _np.cumsum(w) - w[0]                                              # anchor: 0 at the first day seen
    return cum[dnum - d0].astype(_np.float64)

def swap_units(sym, t_entry, t_exit):
    c = roll_cum(sym, [int(t_entry), int(t_exit)]); return float(c[1] - c[0])
