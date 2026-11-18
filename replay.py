#!/usr/bin/env python3
"""Dry run: replay the most recent N sessions of real 1-minute bars through all 12 strategies (paper, in a temp
dir unless --out is given). Proves every strategy computes without crashing. Usage: python3 replay.py [--days 5]"""
import argparse
import os
import sys
import tempfile
import time
import traceback

import pandas as pd

import config as C
import data
from engine import Trader, load_state, TRADE_COLS
from market_cal import session

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=5)
ap.add_argument("--out", default=None)
ap.add_argument("--verbose", action="store_true")
a = ap.parse_args()
out = a.out or tempfile.mkdtemp(prefix="replay_")
os.makedirs(out, exist_ok=True)
syms = sorted({v["symbol"] for v in C.VARIANTS} | {C.AUX})
import pickle
cache = "/tmp/stock_replay_bars.pkl"
if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 3600 and not os.environ.get("NO_CACHE"):
    bars, errs = pickle.load(open(cache, "rb")), {}
else:
    bars, errs = data.fetch_many(syms, spacing=0.5, budget=120)
    if not errs:
        pickle.dump(bars, open(cache, "wb"))
if errs:
    print("FETCH ERRORS:", errs)
    sys.exit(2)
dates = sorted({t.date() for t in bars[C.AUX].index if session(t.date())})[-a.days:]
print(f"replaying {len(dates)} sessions {dates[0]}..{dates[-1]} -> {out}")
state = load_state(os.path.join(out, "state.json"))
log = print if a.verbose else (lambda *_: None)
traders = [Trader(v, state["bots"][v["name"]], out, log) for v in C.VARIANTS]
crashed = 0
t0 = time.time()
for d in dates:
    s_open, s_close = session(d)
    clock = pd.date_range(s_open, s_close - pd.Timedelta(minutes=1), freq="1min")
    for tm in clock:
        now = tm + pd.Timedelta(minutes=1) + pd.Timedelta(seconds=5)
        for tr in traders:
            df = bars[tr.v["symbol"]]
            df = df[df.index < now]               # no look-ahead
            spy = bars[C.AUX][bars[C.AUX].index < now]
            try:
                tr.process(df.iloc[-1700:], spy, now, start_after=s_open - pd.Timedelta(minutes=1) if tr.s["last_ts"] is None else None)
            except Exception:
                crashed += 1
                print(f"CRASH {tr.v['name']} at {tm}:\n{traceback.format_exc()[-600:]}")
                if crashed > 5:
                    sys.exit(3)
print(f"done in {time.time() - t0:.0f}s, crashes: {crashed}")
import summarize  # noqa: E402
summarize.main(["--dir", out, "--no-open"])
sys.exit(0 if crashed == 0 else 3)
