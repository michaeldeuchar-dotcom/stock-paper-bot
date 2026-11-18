# stock-paper-bot — PAPER-TRADING EXPERIMENT ONLY

A fake-money fleet of **12 US-stock bots, one stock each, each with a different strategy**. It never trades
real money: there is **no broker connection, no API key, no order code and no secrets**. Prices come from the
free, keyless Yahoo Finance chart endpoint (1-minute bars). Fills are simulated. Nothing here is financial advice.

| rule | value |
|---|---|
| account per bot | $10,000 fake cash, one position at a time, ≈ $2,000 per position (whole shares) |
| long / short | both allowed (paper) |
| costs | slippage 0.02% per side, commission $0 |
| exits | per-trade stop-loss + take-profit (stop assumed first if both touch in one bar), optional time stop |
| hours | regular session only, 6:30 AM–1:00 PM PT; **flat by 12:55 PM PT**, no new entries in the last 35 min; never overnight |
| calendar | weekends and US holidays skipped (Thanksgiving Nov 26 closed; Nov 27 early close 10:00 AM PT → flat 9:55); see `market_cal.py` |
| risk | max trades per day per bot, 10-min–30-min cooldown after a loss, bot stops for the day at −$100 |

Bots (`config.py` has every number; `STRATEGY_TEXT` explains each in words):
`orb_aapl`, `vwap_pullback_msft`, `momentum_hhll_nvda`, `rsi_vwap_revert_amzn`, `ema_cross_tsla`,
`volume_spike_meta`, `gap_and_go_jpm`, `gap_fade_xom`, `bb_squeeze_jnj`, `rel_strength_wmt`,
`eod_momentum_ko`, `trend_pullback_cat`. (SPY is read as a benchmark by `rel_strength_wmt`; never traded.)

## How it runs
`.github/workflows/stock-bot.yml`: a cron starts a slice on weekday mornings (plus an hourly backup); a cheap gate
job exits in seconds when the market is closed or a run is already active. A slice runs up to 5h40m
(`scripts/ci_run.sh` → `supervisor.py` → `bot.py`), saves state to the `data` branch every 10 min, and starts the next slice
itself while the session is still open (concurrency group `stock-bot-slice`, so slices never overlap). After the
close the chain ends and the next morning's cron restarts it. The bot is bar-driven, so a few minutes between slices
are caught up from the bars (stops/targets are checked against every missed bar).

* Results: `data` branch → `data/state.json`, `data/trades_<bot>.csv` (every trade has `entry_reason` and `exit_reason`),
  `data/heartbeat.json`, `data/bot_tail.log`.
* Summary: copy `data/` from the `data` branch, then `python3 summarize.py [--bot NAME] [--json]`
* Dry run on the latest real sessions: `python3 replay.py --days 5`
* **Stop it:** commit a file named `STOP` to `main` (the gate and chain both honor it), or disable the workflow.
* Run ends automatically after 2026-12-31 (see `RUN_UNTIL` in `config.py`).
