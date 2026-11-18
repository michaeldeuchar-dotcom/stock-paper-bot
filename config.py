"""PAPER (fake money) stock fleet. No broker, no keys, no orders: simulated fills only."""
START_CASH = 10_000.0
POSITION_USD = 2_000.0      # max notional per position
SLIPPAGE = 0.0002           # 0.02% adverse per side
COMMISSION = 0.0            # $0
FLAT_BEFORE_CLOSE_MIN = 5   # flat by 12:55 PM PT (9:55 on early-close days)
NO_ENTRY_LAST_MIN = 35      # no new entries in the last 35 minutes (12:25 PM PT)
MAX_ENTRY_STALENESS_SEC = 180   # never open a position on bars older than this
RUN_UNTIL = "2026-12-31T13:00:00-08:00"   # chain ends after this
DATA_DIR = "data"
AUX = "SPY"                 # benchmark for the relative-strength bot (data only, never traded)

# name, symbol, strategy, params. Stops/targets are % of entry price unless the strategy sets them itself.
VARIANTS = [
    dict(name="orb_aapl", symbol="AAPL", strategy="orb", max_trades=2, cooldown_min=15,
         params=dict(or_min=15, min_rng=0.0012, max_rng=0.012, buf=0.0002, vol_mult=1.0, last_entry_m=180,
                     min_stop=0.0025, max_stop=0.006, rr=1.5)),
    dict(name="vwap_pullback_msft", symbol="MSFT", strategy="vwap_pullback", max_trades=3, cooldown_min=20,
         params=dict(min_m=45, last_entry_m=330, trend_ret=0.0010, touch=0.0007, stop=0.0025, tp=0.0050)),
    dict(name="momentum_hhll_nvda", symbol="NVDA", strategy="momentum", max_trades=3, cooldown_min=30,
         params=dict(min_m=65, last_entry_m=330, min_net=0.0035, min_eff=0.15, vol_ratio=1.15,
                     stop=0.0040, tp=0.0080, max_hold=90)),
    dict(name="rsi_vwap_revert_amzn", symbol="AMZN", strategy="rsi_revert", max_trades=3, cooldown_min=20,
         params=dict(min_m=60, last_entry_m=330, rsi_lo=25, rsi_hi=75, min_dist=0.0040, max_dist=0.015,
                     stop=0.0050, max_hold=60)),
    dict(name="ema_cross_tsla", symbol="TSLA", strategy="ema_cross", max_trades=4, cooldown_min=10,
         params=dict(min_m=30, last_entry_m=330, fast=9, slow=21, min_sep=0.0003, stop=0.0060, tp=0.0150)),
    dict(name="volume_spike_meta", symbol="META", strategy="vol_spike", max_trades=3, cooldown_min=15,
         params=dict(min_m=31, last_entry_m=330, lookback=30, vol_mult=3.0, min_range=0.0010,
                     stop=0.0030, tp=0.0060, max_hold=30)),
    dict(name="gap_and_go_jpm", symbol="JPM", strategy="gap_go", max_trades=1, cooldown_min=30,
         params=dict(min_gap=0.0030, or_min=15, last_entry_m=90, min_stop=0.0020, max_stop=0.0060, rr=2.0)),
    dict(name="gap_fade_xom", symbol="XOM", strategy="gap_fade", max_trades=1, cooldown_min=30,
         params=dict(min_gap=0.0025, first_m=16, last_entry_m=120, roll=0.0005, stop=0.0040,
                     min_tp=0.0030, max_tp=0.0120, max_hold=120)),
    dict(name="bb_squeeze_jnj", symbol="JNJ", strategy="bb_squeeze", max_trades=3, cooldown_min=20,
         params=dict(min_m=30, last_entry_m=330, bb_n=20, bb_k=2.0, pct=25, hist=150, vol_mult=1.2,
                     stop=0.0030, tp=0.0060, max_hold=60)),
    dict(name="rel_strength_wmt", symbol="WMT", strategy="rel_strength", max_trades=3, cooldown_min=30,
         params=dict(min_m=65, last_entry_m=330, lookback=60, min_rel=0.0030, spy_tol=0.0010,
                     stop=0.0030, tp=0.0060, max_hold=90)),
    dict(name="eod_momentum_ko", symbol="KO", strategy="eod_momentum", max_trades=1, cooldown_min=30,
         params=dict(window_start_left=95, window_end_left=40, min_day=0.0025, min_last=0.0008,
                     near_extreme=0.0015, stop=0.0025, tp=0.0050)),
    dict(name="trend_pullback_cat", symbol="CAT", strategy="trend_pullback", max_trades=3, cooldown_min=30,
         params=dict(min_m=30, last_entry_m=330, fast=20, slow=50, min_gap=0.0008, touch=0.0003,
                     stop=0.0020, tp=0.0040)),
]

STRATEGY_TEXT = {
    "orb": "Opening-range breakout: wait for the first 15 min (6:30-6:45 PT) to set a high/low, then buy a 1-min close above the high (short below the low) by 0.02%. Skips tiny (<0.12%) or huge (>1.2%) ranges. Stop = half the range (0.25%-0.6%), target 1.5x the stop. Max 2 trades, entries until 9:30 AM.",
    "vwap_pullback": "VWAP pullback in a trend: after 7:15 AM, if the stock is on the same side of VWAP as its trend (day move >0.10% and VWAP rising/falling), buy when it dips to within 0.07% of VWAP and closes back above it on a green bar (mirror for shorts). Stop 0.25%, target 0.5%. Max 3 trades.",
    "momentum": "1-hour momentum like trend_volume: checked every 5 min after 7:35 AM. Needs >=0.35% net move over the last hour with a straight-ish path (efficiency >=15%), higher highs and higher lows in at least 2 of 3 steps across the four 15-min chunks (lower/lower for shorts), and volume on with-trend bars >=1.15x the volume against. Stop 0.4%, target 0.8%, 90-min time stop. Max 3 trades.",
    "rsi_revert": "Mean reversion to VWAP: every 5 min, if 5-min RSI(14) is <=25 and price is >=0.40% below VWAP, buy (RSI >=75 and >=0.40% above VWAP: short). Target = 90% of the way back to VWAP, stop 0.5%, 60-min time stop. Max 3 trades.",
    "ema_cross": "9/21 EMA crossover on 5-min bars: buy when the 9 crosses above the 21 (by at least 0.03% separation), short on the opposite cross. Stop 0.6%, target 1.5%, and it also exits early if the EMAs cross back. Max 4 trades.",
    "vol_spike": "Volume-spike breakout: a 1-min bar with >=3x the average volume of the last 30 bars, a >=0.10% range, closing beyond the prior 30-min high (long) or low (short). Stop 0.3%, target 0.6%, 30-min time stop. Max 3 trades.",
    "gap_go": "Gap-and-go: needs the open to gap >=0.30% from yesterday's close. After the first 15 min, join the gap direction when price breaks the opening-range high (gap up) or low (gap down) and is on the right side of VWAP, until 8:00 AM. Stop at the other side of the range (0.2%-0.6%), target 2x stop. 1 trade a day.",
    "gap_fade": "Gap-fade: needs a >=0.25% gap. After 6:46 AM (until 8:30), if the gap is failing (price back below the open and below VWAP for a gap up; mirror for a gap down) and there is still room to fill, trade toward yesterday's close. Target 80% of the way to the fill (0.3%-1.2%), stop 0.4%, 2-hour time stop. 1 trade a day.",
    "bb_squeeze": "Bollinger squeeze breakout on 5-min bars: Bollinger(20, 2) band width must have been in its lowest 25% of the last 150 bars, then a 5-min close outside the band on >=1.2x average volume enters in the breakout direction. Stop 0.3%, target 0.6%, 60-min time stop. Max 3 trades.",
    "rel_strength": "Relative strength vs SPY: every 5 min, long when the stock beat SPY by >=0.30% over the last hour while above VWAP and SPY isn't falling (>-0.10%); short on the mirror image. Stop 0.3%, target 0.6%, 90-min time stop. Max 3 trades.",
    "eod_momentum": "End-of-day momentum: only between 11:25 AM and 12:20 PM PT. If the stock is >=0.25% from its open, still moving that way over the last 45 min (>=0.08%), on the right side of VWAP and within 0.15% of the day's high (or low for shorts), ride it into the close. Stop 0.25%, target 0.5%, otherwise flat at 12:55. 1 trade a day.",
    "trend_pullback": "Cautious trend-and-pullback with a tight stop on 5-min bars: needs EMA20 above EMA50 by >=0.08% with EMA50 rising and price above both, then buys when a bar dips to the EMA20 and closes green above it (mirror for downtrends). Stop only 0.2%, target 0.4%. Max 3 trades.",
}
