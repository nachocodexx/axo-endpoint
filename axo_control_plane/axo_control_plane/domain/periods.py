from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Tuple

# The one clock every bill counts on, for every payer: calendar months (storage
# bills, team spending limits, the Free request limit) start and end at
# midnight UTC, like the big clouds do. Never a person's own time zone (that
# is only for showing dates): a bill must not move when someone changes it,
# and two people sharing a bucket must agree on which month a cost is in.
BILLING_TIME_ZONE = "UTC"
BILLING_CLOCK = timezone.utc


def add_months(anchor: float, months: int) -> float:
    """``anchor`` moved by whole calendar months (UTC), same time of day. A
    day the month doesn't have becomes its last day, always counted from the
    anchor: 31/01 -> 28/02 -> 31/03 (never 28/03)."""
    dt = datetime.fromtimestamp(anchor, tz=BILLING_CLOCK)
    years, month0 = divmod(dt.month - 1 + months, 12)
    year, month = dt.year + years, month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day).timestamp()


def rental_period(anchor: float, anchor_index: int, index: int) -> Tuple[float, float]:
    """(start, end) of rental period ``index`` of a VE whose periods started
    at ``anchor`` with period ``anchor_index`` (a VE rented again after its
    rental ended gets a new anchor; period numbers keep counting up)."""
    offset = index - anchor_index
    return add_months(anchor, offset), add_months(anchor, offset + 1)
