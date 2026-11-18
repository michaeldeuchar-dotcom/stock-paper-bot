#!/usr/bin/env python3
"""Live PAPER loop for the 12-bot stock fleet. Fake money only: no broker, no keys, no orders.
Polls free Yahoo Finance 1-minute bars once a minute during regular hours, feeds each completed bar to the
strategies, and keeps per-bot fake accounts in data/state.json + data/trades_<bot>.csv.
Exit code 0 = nothing (more) to do right now (market closed / session over / STOP); the workflow chains or the
cron restarts it for the next open. Any exception in a cycle is logged and the loop continues."""
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta

import pandas as pd

import config as C
import data
from engine import Trader, load_state, save_state
from market_cal import PT, next_session, describe_day

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BOT_DATA_DIR") or os.path.join(ROOT, C.DATA_DIR)
os.makedirs(DATA, exist_ok=True)
LOG = os.path.join(DATA, "bot.log")
STATE = os.path.join(DATA, "state.json")
HB = os.path.join(DATA, "heartbeat.json")
STOP_FILES = [os.path.join(ROOT, "STOP"), os.path.join(ROOT, ".slice_stop")]
PRE_OPEN_MIN = 15       # start working this long before the open
POST_CLOSE_MIN = 5      # keep working this long after the close (final flat-out)
_stop = False
# test hook only: BOT_TEST_NOW=2026-11-18T12:50:00-08:00 shifts the bot's clock (paper replay of a live-code run)
_OFFSET = (datetime.fromisoformat(os.environ["BOT_TEST_NOW"]) - datetime.now(PT)) if os.environ.get("BOT_TEST_NOW") else timedelta(0)


def clock_now():
    return datetime.now(PT) + _OFFSET


def say(msg):
    line = f"{clock_now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _sig(*_):
    global _stop
    _stop = True


def heartbeat(**kw):
    kw.update(ts=time.time(), ts_pt=f"{clock_now():%Y-%m-%d %H:%M:%S}")
    tmp = HB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, HB)


def stopped():
    return _stop or any(os.path.exists(p) for p in STOP_FILES)


def sleep_until(ts, hb_kw):
    while not stopped() and time.time() < ts:
        heartbeat(**hb_kw)
        time.sleep(min(20, max(0.5, ts - time.time())))


def main():
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    state = load_state(STATE)
    traders = [Trader(v, state["bots"][v["name"]], DATA, say) for v in C.VARIANTS]
    syms = sorted({v["symbol"] for v in C.VARIANTS} | {C.AUX})
    bars = {}
    cycle, fails_in_row, started = 0, 0, time.time()
    end = datetime.fromisoformat(C.RUN_UNTIL)
    say(f"bot start: {len(traders)} paper bots, symbols {syms}; PAPER ONLY")
    while True:
        now = clock_now()
        if stopped():
            say("STOP requested; exiting")
            return 0
        if now >= end:
            say("run end time reached; exiting")
            return 0
        sess = next_session(now - timedelta(minutes=POST_CLOSE_MIN))
        if sess is None:
            say("no upcoming session found; exiting")
            return 0
        o, cl = sess
        base = dict(phase="idle", session_open=o.isoformat(), session_close=cl.isoformat(), cycle=cycle)
        if now < o - timedelta(minutes=PRE_OPEN_MIN):
            heartbeat(**base, note="market closed")
            say(f"idle: market closed ({describe_day(now.date())}); next session {o:%a %b %d} {o:%H:%M}-{cl:%H:%M} PT. Exiting; "
                f"cron/chain restarts me for the open.")
            return 0
        if now >= cl + timedelta(minutes=POST_CLOSE_MIN):
            return 0
        final = now >= cl
        phase = "final" if final else ("pre-open" if now < o else "trading")
        try:
            have = [s for s in syms if s in bars]
            ok_new, err = {}, {}
            if len(have) == len(syms) and now < o - timedelta(minutes=2):
                pass                                   # pre-open and already loaded: nothing new to fetch
            elif len(have) == len(syms):
                ok_new, err = data.fetch_many(syms, rng="1d", spacing=0.3, budget=40)
            else:
                ok_new, err = data.fetch_many(syms, rng="5d", spacing=0.4, budget=60)
            for s, df in ok_new.items():
                bars[s] = data.merge(bars.get(s), df)
            fails_in_row = fails_in_row + 1 if (err and not ok_new) else 0
            if err:
                say(f"WARN fetch errors ({len(err)}/{len(syms)}): {dict(list(err.items())[:3])}")
            lag = {}
            for s in syms:
                if s in bars and len(bars[s]):
                    lag[s] = round((clock_now() - bars[s].index[-1] - timedelta(minutes=1)).total_seconds())
            if not all(s in bars for s in syms):
                say("WARN not all symbols loaded yet; waiting")
            else:
                spy = bars[C.AUX]
                clock = clock_now()
                for tr in traders:
                    df = bars[tr.v["symbol"]]
                    try:
                        start_after = None
                        if tr.s["last_ts"] is None:
                            start_after = df.index[-1]       # fresh account: don't back-trade history
                        n = tr.process(df, spy, clock, start_after=start_after)
                        if tr.s["last_ts"] is None:
                            tr.s["last_ts"] = df.index[-1].isoformat()
                        if final and tr.s["pos"] is not None and clock >= cl + timedelta(minutes=1):
                            tr.force_flat(clock, float(df["close"].iloc[-1]), "force flat after close (no bars)")
                    except Exception:  # noqa: BLE001  one bot's bug must not stop the others
                        say(f"ERROR in {tr.v['name']}: {traceback.format_exc()[-500:]}")
                    save_state(STATE, state)
            cycle += 1
            n_open = sum(1 for t in traders if t.s["pos"])
            heartbeat(phase=phase, session_open=o.isoformat(), session_close=cl.isoformat(), cycle=cycle,
                      open_positions=n_open, fetch_errors=len(err), consecutive_fail_cycles=fails_in_row,
                      max_data_lag_sec=max(lag.values()) if lag else None, data_lag_sec=lag,
                      last_bar={s: bars[s].index[-1].isoformat() for s in bars},
                      uptime_sec=round(time.time() - started))
            if cycle % 5 == 1:
                say(f"HEARTBEAT phase={phase} cycle={cycle} open={n_open} errors={len(err)} max_lag={max(lag.values()) if lag else None}s "
                    f"fleet_pnl=${sum(t.s['realized'] for t in traders):+.2f}")
            if lag and max(lag.values()) > C.MAX_ENTRY_STALENESS_SEC and phase == "trading":
                say(f"WARN data lag {max(lag, key=lag.get)}={max(lag.values())}s: new entries paused for stale symbols")
            if final and not any(t.s["pos"] for t in traders):
                say("session over, all flat; exiting")
                return 0
        except Exception:  # noqa: BLE001
            fails_in_row += 1
            say(f"ERROR cycle: {traceback.format_exc()[-600:]}")
            if fails_in_row >= 30:
                say("too many failing cycles; exiting non-zero so the supervisor restarts me")
                return 1
        # next wake: just after the next minute boundary (bars close on the minute)
        wake = (time.time() // 60 + 1) * 60 + 7
        if now < o:
            wake = min(wake, time.time() + 60)
        sleep_until(wake, dict(phase=phase, cycle=cycle))
        if fails_in_row:
            time.sleep(min(60, 5 * fails_in_row))


if __name__ == "__main__":
    sys.exit(main())
