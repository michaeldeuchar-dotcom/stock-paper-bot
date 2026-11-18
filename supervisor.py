#!/usr/bin/env python3
"""Keeps bot.py alive for one workflow slice: restarts it if it crashes (exit != 0), kills + restarts it if its
heartbeat is older than 3 minutes (hang). Stops when bot.py exits 0, or STOP / .slice_stop appears."""
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
HB = os.path.join(ROOT, "data", "heartbeat.json")
STOPS = [os.path.join(ROOT, "STOP"), os.path.join(ROOT, ".slice_stop")]
STALE = 180


def say(m):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "[supervisor]", m, flush=True)


def hb_age():
    try:
        return time.time() - json.load(open(HB))["ts"]
    except (OSError, ValueError, KeyError):
        return None


def main():
    restarts = 0
    while True:
        if any(os.path.exists(s) for s in STOPS):
            say("stop file present; done")
            return 0
        say(f"starting bot.py (restart #{restarts})")
        started = time.time()
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "bot.py")])
        while p.poll() is None:
            time.sleep(5)
            if any(os.path.exists(s) for s in STOPS):
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    p.kill()
                say("stopped on request")
                return 0
            age = hb_age()
            if time.time() - started > 90 and (age is None or age > STALE):
                say(f"heartbeat stale ({age}); killing hung bot")
                p.kill()
                p.wait()
                break
        rc = p.returncode
        if rc == 0:
            say("bot exited cleanly (idle / session over); supervisor done")
            return 0
        restarts += 1
        say(f"bot died rc={rc}; restart in 5s")
        if restarts > 30:
            say("too many restarts; giving up")
            return 1
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
