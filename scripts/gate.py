#!/usr/bin/env python3
"""Cheap market-hours gate (stdlib only). Prints 'go=true|false' + reason. go=true only while we're inside
[open-15min, close+5min] (--chain: until close-1min, so a finished session isn't chained again) of a US trading day (PT) and before the run's end time.  --at ISO to test a moment."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config  # noqa: E402
from market_cal import PT, next_session, describe_day  # noqa: E402

at = datetime.fromisoformat(sys.argv[sys.argv.index("--at") + 1]) if "--at" in sys.argv else datetime.now(PT)
at = at.astimezone(PT)
if at >= datetime.fromisoformat(config.RUN_UNTIL):
    print("go=false reason=past run end")
    sys.exit(0)
s = next_session(at - timedelta(minutes=5))
if not s:
    print("go=false reason=no session ahead")
elif s[0] - timedelta(minutes=15) <= at <= s[1] + timedelta(minutes=(-1 if "--chain" in sys.argv else 5)):
    print(f"go=true reason=inside window for session {s[0]:%a %b %d} {s[0]:%H:%M}-{s[1]:%H:%M} PT")
else:
    print(f"go=false reason=market closed now ({describe_day(at.date())}); next session {s[0]:%a %b %d} {s[0]:%H:%M}-{s[1]:%H:%M} PT")
