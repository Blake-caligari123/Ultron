> **ultron reigns supreme**
>
> ⚠️ **Paper trading only.** No broker connection, no real orders, no real money. Every trade is simulated.

# ULTRON — 1m–30m day-trading edition (research engine + live PAPER floor). No broker, no orders, no credentials.

Paper only: prices come from public feeds (Binance kline_1m for BTCUSD, Swissquote public BBO for XAUUSD); every trade is simulated.
Served at `$ULTRON_PUBLIC_URL/ultron/` (set `ULTRON_PUBLIC_URL` in your environment; the UI uses relative paths only). `./run.sh start|stop|restart|status` runs three supervised,
nice'd, memory-capped processes: `ultron.live` (paper floor), `ultron.breeder` (continuous evolution), uvicorn `ultron.floor:app` on :8795.

Timeframes: 1m, 5m, 15m, 30m. Fills: signal on bar close → next bar open, stop wins same-bar ties, spread/slippage from ultron/costs.py.

## Continuous breeder (ultron/breeder.py) — replaces the old daily learning round
Steady-state GA per symbol, alternating XAU/BTC at ~50% of one core: pool crossover+mutation, children of live bots, random immigrants (new families).
Fitness = fold t-stat of drift-adjusted R on TRAIN only (2023-11-01 → now−180d). No prop-firm terms.
Gate: fit ≥ 0.3 → parameter plateau → rolling 180-day holdout (≥25 trades, PF ≥ 1.2, net R > 0, t ≥ 1, DD ≥ −15%, never used for fitness)
→ max 2 bots of the same style → correlation < 0.7 vs roster (train and holdout) → max 3 promotions/hour → max 15 per symbol, 30 on the floor.
New bots start on TRIAL at 0.25× weight; graduate after ≥10 live trades and ≥48 h. Kills (performance only, checked every 20 s):
live net ≤ −6R after 10 trades, drawdown ≤ −10R, PF < 0.8 after 20 trades. Allocator rebalances on every roster change (cap 25% per bot).
Stats: results/breeder_stats.json. Pool: results/breeder_pool.json.

## Central floor risk manager (ultron/risk.py, settings in config/floor_risk.json)
Every entry must pass: total open risk ≤ 5% of paper equity; ≤ 8 open trades; ≤ 3 trades in the same direction per symbol;
daily floor loss limit 4% (realised + open, from Sydney midnight) → all new entries halt until next Sydney day; ≤ 30 bots (breeder).
Risk per trade = allocator weight × 10, clamped 0.25%–1% of equity. Blocked signals are skipped and logged "blocked by floor limit".
Live state: results/risk_state.json; shown on the floor as "Risk in use: x% of 5%" plus open-trade count.

Honest caveat: the 180-day holdout is reused for every candidate, so promoted bots carry selection bias; the live trial is the real test.
Research verdict from the 1m–15m re-run: no robust edge found.

## Scalp Pit (experimental, fully separate floor) — `ULTRON_FLOOR=scalp`
Own DB (results/scalp_ultron.db), own $10k paper account, own risk limits (config/scalp_risk.json), own breeder + live process, own leaderboard.
Same pipeline as the main floor (breed → train fitness → plateau → 180-day holdout with full costs → style/correlation → hourly cap → trial),
but genes are restricted to 1m bars, 3–30 min holds, stop 0.3–1.5 ATR, target 0.3–2.5 ATR, and the holdout bar is deliberately lenient
(train fitness ≥ −2.5, plateau within slack, holdout ≥150 trades, PF ≥ 0.85, net ≥ −25R, t ≥ −2.5, DD ≥ −35%, and beat ≥75% of
60 random scalp bots on the same holdout), so bots can be losing after costs and are labelled EXPERIMENTAL. Kills: −8R after 20 trades, −12R drawdown,
PF < 0.8 after 40 trades. Spread/commission/slippage paid are recorded per trade (cost_usd) and shown on the page.
Entries are on 1m bar closes; stops/targets are checked on every Binance kline update (~2 s) and every 5 s gold quote. No tick-level entries.

## Results tab
`api/results?floor=main|scalp`: for every promoted bot, the evidence stored at promotion (training and holdout trades, R, % at 1% risk,
win rate, wins/losses, profit factor, worst drop, percentile vs 60 random bots on the same holdout), promotion time, live paper results since,
and recently cut bots with reasons. Bots promoted before evidence was recorded are re-checked and marked "re-checked after it joined".

## Indicator combos and reward:risk (3 Nov 2026)
* `ultron/indicators.py`: RSI, MACD, EMA cross, SMA cross, Bollinger, Keltner, ATR expansion, ADX, Stochastic, VWAP (time-weighted:
  neither feed has real volume, so OBV and true VWAP are left out rather than faked), Supertrend, Ichimoku (cloud shifted so no lookahead),
  Donchian, daily pivot. Each gives a bullish/bearish/neutral state per bar; a condition may be "fade" (inverted).
* Family 11 `indicator_combo`: 1-3 conditions joined by AND or OR; entry when the combined state turns. Crossover swaps whole
  conditions between parents; mutation can swap an indicator, its period or the AND/OR. Half of new random main-floor bots are combos.
* Diversity: at most 2 live bots per indicator combination on the main floor (3 in the Scalp Pit), plus the old max-same-style rule.
* Reward:risk: every new genome is forced to take-profit = 2.0-3.0 x stop distance (`engine.rr_fix`). Bots promoted before this rule keep
  their tested settings and are flagged on their card as older bots.
* Cards show the bot's indicators and R:R; Live Trades shows R:R for open and closed trades.

## Scalp Pit level families (`ultron/levels.py`)
1m entries around higher-timeframe levels, all computed without lookahead: previous completed 1h and 4h high/low, previous UTC day
high/low, Asia (00-07 UTC, usable from 07:00) and London (07-12 UTC, usable from 12:00) high/low, and round numbers (gold $10, bitcoin $500).
* `lvl_retest`: break of the level, then a retest within p2 bars that closes on the break side.
* `lvl_sweep`: wick through the level (at most 3*p3 ATR) that closes back inside; fade it.
* `lvl_pullback`: first 1m pullback after a break, then a close beyond the prior bar.
Stop = just beyond the level / wick / pullback extreme by sl x ATR; target = 2-3R; hold 6-45 min. Optional 0-2 indicator confirmations.
A signal is skipped if the stop is > 3 ATR away or the target is < 3x the round-trip cost (same rule in backtest and live).
Scalp fitness needs >= 300 training trades (steeper penalty below), so evolution no longer collapses onto ~59-trade bots.
Gates unchanged, including "must beat 75% of random bots on the unseen 6 months".

## Breeder heartbeat
Every batch prints `HB <floor> <sym> gen .. batch .. new .. best_fit .. | last hour tested .. | rejects <top 3 reasons>` to
logs/breed*.log. Reject reasons (plain words) are in breeder_stats (`reject_last_hour`, `reject_today`, `top_reject`) and on the
floor status line: "Breeding: tested N in the last hour · top reject reason: X".

## Development
* Setup: `uv sync` (creates `.venv`, installs the package from `./ultron` plus pytest). Run anything as `.venv/bin/python -m ultron.<module>`.
* Tests: `.venv/bin/python -m pytest` — synthetic data only, no network, runs in an isolated temporary `ULTRON_HOME`.
* `ULTRON_HOME` (default: the repo root) holds the runtime state: `data/`, `results/`, `logs/`, and optionally `config/`
  (falls back to the repo's `config/`). Point it somewhere else to run an isolated copy against its own data.
* Floor view API (read-only, gzip, cached ~2 s per floor): `api/state`, `api/results`, `api/learned` (each `?floor=main|scalp`),
  `api/replay` (research run) and `api/health` (are the live floor and breeder of each floor reporting?).

## Performance notes (Nov 2026 overhaul)
* Floor view: closed trades are read incrementally into memory (only new rows per poll); `api/state` went from ~490 ms / 1.8 MB to
  ~20 ms / ~110 KB on a floor with 40k trades, and the page no longer downloads every killed bot and an unused equity series.
* Risk manager: equity / peak / day P&L come from an incremental ledger instead of rescanning `live_trades` on every check.
* Live floor: each bar close builds only the timeframes that are due. Indicator-family bots run on a trimmed tail of history
  (`market.live_tail`), proven signal-identical to full history by `tests/test_market.py`; a main-floor 1m close went from ~100 ms to ~15 ms.
* Level families (Scalp Pit) keep the full 20-day live window. Their setups stay armed on a level across chained entries, so their
  signals have no bounded look-back: live (20 days of history) and the backtest (years) can disagree on long-running setups.
  This was already true before the overhaul; `tests/test_market.py::test_level_families_need_full_window` documents it.
