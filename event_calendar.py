"""
Known scheduled events (macro releases + earnings for the tracked universe)
used to label snapshots as "quiet" vs "near a known event", and, in a live
agent, to gate liquidity provision off ahead of real news.

This only covers *scheduled* events (Fed decisions, CPI, jobs reports,
earnings dates), not surprise headlines. A real news feed would be needed to
catch unscheduled events (geopolitics, guidance cuts, etc), that is out of
scope for this validation kit.

Checked 2026-09-23 by web search. Confirmed: no FOMC/CPI/jobs release and no
earnings date for any of the 15 tracked tickers falls inside the Sep 23-27
collection window. Closest is Micron's fiscal Q4 earnings on 2026-09-30,
three days after the hackathon deadline. This means the data collected this
week is clean "quiet" baseline, not event-driven, worth saying plainly in
the submission rather than implying the event-gate was exercised against a
real event, it was not, there wasn't one to gate around.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# (event name, ET datetime, symbols affected; None means macro / all symbols)
KNOWN_EVENTS = [
    ("FOMC decision + presser", datetime(2026, 9, 16, 14, 0, tzinfo=ET), None),
    ("Micron fiscal Q4 earnings", datetime(2026, 9, 30, 16, 30, tzinfo=ET), ["RMUUSDT"]),
    ("Nonfarm payrolls", datetime(2026, 10, 2, 8, 30, tzinfo=ET), None),
    ("CPI release", datetime(2026, 10, 13, 8, 30, tzinfo=ET), None),
]


def near_known_event(ts_utc: datetime, symbol: str, window_minutes: int = 60) -> str | None:
    """Returns the event name if ts_utc falls within window_minutes of a
    known scheduled event relevant to this symbol, else None."""
    ts_et = ts_utc.astimezone(ET)
    for name, event_time, symbols in KNOWN_EVENTS:
        if symbols is not None and symbol not in symbols:
            continue
        delta_minutes = abs((ts_et - event_time).total_seconds()) / 60
        if delta_minutes <= window_minutes:
            return name
    return None
