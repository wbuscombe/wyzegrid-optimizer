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

The dashboard names the clock beside every pattern time. `process_clock_label`
reads it at runtime from the same process-local source as `process_local_time`,
and `zoneinfo_clock_label` is the explicit-zone counterpart, so no zone is ever
written into code or configuration for display.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .settings import EPOCH_MONDAY

LocalTime = tuple[date, int, int]
LocalTimeFn = Callable[[float], LocalTime]
ClockLabelFn = Callable[[float], str]


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


def clock_label(moment: datetime) -> str:
    """The name the dashboard shows for the clock of an aware `moment`: "UTC",
    or the zone abbreviation with its offset then, such as "ABC (UTC-03:30)"."""
    minutes = int((moment.utcoffset() or timedelta(0)).total_seconds()) // 60
    hours, mins = divmod(abs(minutes), 60)
    offset = f"UTC{'-' if minutes < 0 else '+'}{hours:02d}:{mins:02d}"
    name = moment.tzname() or ""
    if name == "UTC" and minutes == 0:
        return "UTC"
    return f"{name} ({offset})" if name and name != offset else offset


def process_clock_label(ts: float) -> str:
    """The process-local clock at `ts` (TZ or /etc/localtime), read from the same
    source as `process_local_time`."""
    return clock_label(datetime.fromtimestamp(ts).astimezone())


def zoneinfo_clock_label(zone_name: str) -> ClockLabelFn:
    """The explicit-zone counterpart of `process_clock_label` (the synthetic demo)."""
    tz = ZoneInfo(zone_name)
    return lambda ts: clock_label(datetime.fromtimestamp(ts, tz=tz))


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
