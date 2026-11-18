"""US equity market calendar (NYSE/Nasdaq regular hours), expressed in Pacific time.
Regular session 6:30-13:00 PT; early close days end 10:00 PT. Covers 2026 (the run's lifetime)."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")

# NYSE full-day closures, 2026
HOLIDAYS = {
    "2026-01-01": "New Year's Day", "2026-01-19": "MLK Day", "2026-02-16": "Presidents Day",
    "2026-04-03": "Good Friday", "2026-05-25": "Memorial Day", "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (observed)", "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving", "2026-12-25": "Christmas",
}
# 1:00 PM ET early closes = 10:00 AM PT
EARLY_CLOSE = {"2026-11-27": "Day after Thanksgiving", "2026-12-24": "Christmas Eve"}

OPEN_T = time(6, 30)
CLOSE_T = time(13, 0)
EARLY_CLOSE_T = time(10, 0)


def session(d: date):
    """(open_dt, close_dt) in PT for a trading day, or None if the market is closed that day."""
    k = d.isoformat()
    if d.weekday() >= 5 or k in HOLIDAYS:
        return None
    ct = EARLY_CLOSE_T if k in EARLY_CLOSE else CLOSE_T
    return (datetime.combine(d, OPEN_T, PT), datetime.combine(d, ct, PT))


def next_session(now: datetime):
    """First session whose close is still in the future (could be today's)."""
    d = now.astimezone(PT).date()
    for i in range(0, 12):
        s = session(d + timedelta(days=i))
        if s and s[1] > now:
            return s
    return None


def describe_day(d: date) -> str:
    k = d.isoformat()
    if d.weekday() >= 5:
        return "weekend"
    if k in HOLIDAYS:
        return f"market holiday: {HOLIDAYS[k]}"
    if k in EARLY_CLOSE:
        return f"early close 10:00 AM PT ({EARLY_CLOSE[k]})"
    return "regular session 6:30 AM-1:00 PM PT"
