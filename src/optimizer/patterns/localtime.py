"""
The one local-time mapping the pattern pipeline uses.

A `LocalTimeFn` maps epoch seconds to (local date, weekday with Monday = 0,
minute of day). Production uses `process_local_time`, which reads the same
source as the scheduler's 02:00 anchor (`datetime.now()`): the process's local
zone from TZ or /etc/localtime. `datetime.fromtimestamp` applies the offset in
force at each historical instant, so wall-clock minutes stay DST-correct.
Tests inject `zoneinfo_local_time(...)` instead.

Weeks are counted from a fixed Monday (`EPOCH_MONDAY`), never ISO week
numbers, so parity alternates cleanly across year boundaries.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .settings import EPOCH_MONDAY

LocalTime = tuple[date, int, int]
LocalTimeFn = Callable[[float], LocalTime]


def process_local_time(ts: float) -> LocalTime:
    """Production mapping: the process-local wall clock the scheduler anchors to."""
    dt = datetime.fromtimestamp(ts)
    return dt.date(), dt.weekday(), dt.hour * 60 + dt.minute


def zoneinfo_local_time(zone_name: str) -> LocalTimeFn:
    """An explicit-zone mapping (tests and the synthetic demo)."""
    tz = ZoneInfo(zone_name)

    def _local(ts: float) -> LocalTime:
        dt = datetime.fromtimestamp(ts, tz=tz)
        return dt.date(), dt.weekday(), dt.hour * 60 + dt.minute

    return _local


def week_index(day: date, epoch_monday: date = EPOCH_MONDAY) -> int:
    """Whole weeks since the fixed epoch Monday (floor division, so it never skips)."""
    return (day - epoch_monday).days // 7


def parity(day: date, epoch_monday: date = EPOCH_MONDAY) -> int:
    return week_index(day, epoch_monday) % 2


def week_start(week: int, epoch_monday: date = EPOCH_MONDAY) -> date:
    """The Monday that begins `week`."""
    return epoch_monday + timedelta(days=7 * week)


def date_for(week: int, weekday: int, epoch_monday: date = EPOCH_MONDAY) -> date:
    return week_start(week, epoch_monday) + timedelta(days=weekday)
