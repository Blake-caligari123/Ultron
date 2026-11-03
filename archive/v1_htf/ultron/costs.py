"""Retail MT5 cost model (per side, in price units). Assumptions documented in COSTS dict."""
COSTS = {
    "XAUUSD": {
        # spread: real Dukascopy per-bar ask-bid, floored at 0.25 USD (typical retail MT5 gold 0.15-0.40).
        "spread_floor": 0.25,
        # ECN/raw-style commission $7 per 100oz lot round trip = $0.035/oz per side.
        "commission_side": 0.035,
        # slippage on market & stop fills (not on TP limit fills): $0.05/oz per fill.
        "slip_side": 0.05,
        # overnight swap, charged both directions, per UTC day held: 0.015% of price (~5.5%/yr).
        "swap_day_frac": 0.00015,
        "contract": "1 lot = 100 oz",
    },
    "BTCUSD": {
        # BTCUSD CFD on retail MT5: spread ~0.025% of price (~$19 at $77k), commission included.
        "spread_frac": 0.00025,
        "commission_side": 0.0,
        # slippage 0.01% of price per market/stop fill.
        "slip_frac": 0.0001,
        # crypto CFD financing ~20%/yr, charged 7 days a week: 0.055% of price per day.
        "swap_day_frac": 0.00055,
        "contract": "1 lot = 1 BTC",
    },
}
