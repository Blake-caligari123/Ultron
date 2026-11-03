"""Plain-English names and one-line rule descriptions for bots (for the non-technical floor view)."""
from .engine import TF_MIN

ASSET = {"XAUUSD": "gold", "BTCUSD": "bitcoin"}
ASSET_T = {"XAUUSD": "Gold", "BTCUSD": "Bitcoin"}
FAM_NAME = {"donchian_breakout": "Breakout", "bollinger_reversion": "Snapback", "ema_trend_cross": "Trend Rider",
            "session_orb": "Opening Range", "rsi_pullback": "Dip Buyer", "vwap_reversion": "Fair-Price Snapback",
            "momentum_burst": "Momentum Burst", "ema_pullback": "Trend Pullback",
            "lvl_retest": "Level Retest", "lvl_sweep": "Sweep Fader", "lvl_pullback": "Level Pullback", "indicator_combo": "Combo"}
SYD = 11   # AEDT offset used for plain-language session times (Sydney, daylight time)

def dur(mins):
    mins = float(mins)
    if mins < 60: return f"{int(round(mins))} min"
    h = mins / 60
    return f"{h:.0f} hours" if abs(h - round(h)) < 0.05 else f"{h:.1f} hours"

def hh(h_utc):
    h = (h_utc + SYD) % 24
    if h == 0: return "midnight"
    if h == 12: return "noon"
    return f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}"

def _special(g, sym):
    from .engine import indicators_text, rr
    from .levels import LEVEL_NAMES
    from . import indicators
    a = ASSET[sym]; f = g["fam"]
    if f == 11:
        j = "all of these agree" if int(g["lg"]) == 0 else "any of these signals (with none disagreeing)"
        r = f"Trades {a} when {j}: {indicators.text(g)}" if int(g["nc"]) > 1 else f"Trades {a} when this signal turns: {indicators.text(g)}"
        r = r.replace(" AND ", ", ").replace(" OR ", ", ")
    else:
        L = LEVEL_NAMES[int(g["p1"])]
        r = {8: f"Waits for {a} to break its {L} high (or low), then buys (sells) the first retest of that level on the 1-minute chart",
             9: f"Fades false breaks: when {a} pokes above its {L} high (below its low) but the 1-minute bar closes back inside, it sells (buys)",
             10: f"After {a} breaks its {L} high (low), buys (sells) the first 1-minute pullback once price pushes on again"}[f]
        x = indicators.text(g)
        if x: r += f", only when {x.replace(' AND ', ' and ').replace(' OR ', ' or ')} agrees"
        r += "; stop just beyond the level"
    if g["side"] == 1: r += " (buys only)"
    elif g["side"] == 2: r += " (sells only)"
    return r

def describe(g, sym):
    from .engine import rr
    tail = f" Target {rr(g)}x the stop distance (reward:risk 1:{rr(g)})."
    if g["fam"] >= 8:
        tf = TF_MIN[g["tf"]]; extra = []
        if g["slen"] < 24: extra.append(f"only trades {hh(g['ss'])}–{hh(g['ss'] + g['slen'])} Sydney time")
        extra.append(f"checks every {dur(tf)}, closes within {dur(g.get('hold_h', 8) * 60)}")
        x = "; ".join(extra); return _special(g, sym) + ". " + x[0].upper() + x[1:] + "." + tail
    return _describe(g, sym) + tail

def _describe(g, sym):
    a = ASSET[sym]; tf = TF_MIN[g["tf"]]; f = g["fam"]; L = dur(g["p1"] * tf) if f in (0, 1, 6) else ""
    R = dur(g.get("p2", 0)) if f == 3 else ""; H = hh(g["p1"]) if f == 3 else ""
    buy, sell = {
        0: (f"buys when {a} breaks above its highest price of the last {L}", f"sells when it breaks below the lowest"),
        1: (f"buys when {a} falls unusually far below its {L} average, betting on a bounce", "sells after unusual spikes"),
        2: (f"buys when {a}'s short-term trend turns up", "sells when it turns down"),
        3: (f"marks {a}'s price range in the first {R} after {H} Sydney time, then buys a break above it", "sells a break below it"),
        4: (f"buys quick dips while {a}'s bigger trend is up", "sells quick pops while it is down"),
        5: (f"buys when {a} stretches far below its average price for the day, betting it snaps back", "sells when it stretches far above"),
        6: (f"buys when {a} makes a sudden big jump within {L}, riding the burst", "sells a sudden big drop"),
        7: (f"in an uptrend, buys when {a} dips back to its short-term average and bounces", "the reverse in downtrends"),
    }[f]
    if g["side"] == 1: r = buy + " (never sells short)"
    elif g["side"] == 2:   # sell-only: phrase the sell rule on its own
        r = {0: f"sells when {a} breaks below its lowest price of the last {L}", 1: f"sells when {a} spikes unusually far above its {L} average, betting on a drop",
             2: f"sells when {a}'s short-term trend turns down", 3: f"marks {a}'s price range in the first {R} after {H} Sydney time, then sells a break below it",
             4: f"sells quick pops while {a}'s bigger trend is down", 5: f"sells when {a} stretches far above its average price for the day, betting it snaps back",
             6: f"sells when {a} makes a sudden big drop within {L}, riding the burst", 7: f"in a downtrend, sells when {a} pops back up to its short-term average and fades"}[f] + " (never buys)"
    else: r = buy + "; " + sell
    extra = []
    if g["slen"] < 24 and f != 3: extra.append(f"only trades {hh(g['ss'])}–{hh(g['ss'] + g['slen'])} Sydney time")
    extra.append(f"checks every {dur(tf)}, closes within {dur(g.get('hold_h', 8) * 60)}")
    r = r[0].upper() + r[1:]; x = "; ".join(extra); x = x[0].upper() + x[1:]
    return r + ". " + x + "."

def name(sym, family, n):
    return f"{ASSET_T[sym]} {FAM_NAME.get(family, family)} #{n}"
