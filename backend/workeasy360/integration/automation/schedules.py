"""Schedule definitions and next-run arithmetic (Tasks 42, 43, 45).

Three bounded kinds instead of cron (no cron parser dependency, and no way
to express "every second"):

- ``interval`` - every N minutes, N >= ``AUTOMATION_MIN_INTERVAL_MINUTES``,
  aligned to multiples of N since the Unix epoch (so "every 60" runs on the
  hour, in UTC, deterministically and with no hidden anchor state);
- ``daily``    - at HH:MM in an IANA timezone;
- ``weekly``   - on chosen weekdays at HH:MM in an IANA timezone.

**Timezones and DST (Task 43).** Wall-clock schedules are computed in the
schedule's own IANA zone with the standard library's ``zoneinfo`` - no
hand-written offset arithmetic:

- a *nonexistent* local time (the spring-forward gap - 01:30 in London on
  the last Sunday of March) runs at the equivalent instant just after the
  gap (02:30 local), i.e. the gap is skipped forward, never the run;
- an *ambiguous* local time (the autumn repeat of 01:00-02:00) runs **once**,
  at its first occurrence;
- the canonical form stores the zone name, never an offset, so a schedule
  keeps meaning "09:00 in London" across every DST change.

**Missed runs (Task 45).** After downtime the policy is *coalesce*: fire once
for the most recent missed occurrence and report how many were skipped
(``missed_occurrences`` on the fired event). Never a burst of catch-up runs.
Pure Python apart from ``zoneinfo``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

MAX_INTERVAL_MINUTES: Final = 7 * 24 * 60
WEEKDAYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RE: Final = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])\Z")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
#: How many missed occurrences are counted exactly before the count is
#: reported as "at least this many" - a bound on work after a long outage.
MAX_COUNTED_MISSES: Final = 1000


class ScheduleError(ValueError):
    def __init__(self, message: str, *, field: str = "trigger.schedule") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def normalize(raw: Any, *, min_interval_minutes: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ScheduleError("A schedule must be an object.")
    kind = raw.get("kind")
    if kind == "interval":
        _only(raw, {"kind", "every_minutes"})
        minutes = raw.get("every_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ScheduleError(
                "every_minutes must be a whole number.",
                field="trigger.schedule.every_minutes",
            )
        if not min_interval_minutes <= minutes <= MAX_INTERVAL_MINUTES:
            raise ScheduleError(
                f"every_minutes must be between {min_interval_minutes} and "
                f"{MAX_INTERVAL_MINUTES}.",
                field="trigger.schedule.every_minutes",
            )
        return {"kind": "interval", "every_minutes": minutes}
    if kind in ("daily", "weekly"):
        allowed = {"kind", "time", "timezone"} | ({"days"} if kind == "weekly" else set())
        _only(raw, allowed)
        at = raw.get("time")
        if not isinstance(at, str) or not _TIME_RE.match(at):
            raise ScheduleError(
                "time must be HH:MM (24-hour).", field="trigger.schedule.time"
            )
        zone = raw.get("timezone")
        if not isinstance(zone, str) or zone not in _zones():
            raise ScheduleError(
                "timezone must be an IANA name such as 'Europe/London'.",
                field="trigger.schedule.timezone",
            )
        result: dict[str, Any] = {"kind": kind, "time": at, "timezone": zone}
        if kind == "weekly":
            days = raw.get("days")
            if (
                not isinstance(days, list)
                or not days
                or any(d not in WEEKDAYS for d in days)
            ):
                raise ScheduleError(
                    f"days must be a non-empty list of {', '.join(WEEKDAYS)}.",
                    field="trigger.schedule.days",
                )
            result["days"] = [d for d in WEEKDAYS if d in set(days)]
        return result
    raise ScheduleError("kind must be 'interval', 'daily' or 'weekly'.")


def next_after(schedule: Mapping[str, Any], after: datetime) -> datetime:
    """The first occurrence strictly after ``after`` (UTC in, UTC out)."""
    after = after.astimezone(timezone.utc)
    if schedule["kind"] == "interval":
        step = timedelta(minutes=int(schedule["every_minutes"]))
        periods = (after - _EPOCH) // step
        return _EPOCH + step * (periods + 1)
    for candidate in _candidates(schedule, after, forward=True):
        if candidate > after:
            return candidate
    raise AssertionError("unreachable: a weekly schedule has a day within 8 days")


def latest_at_or_before(schedule: Mapping[str, Any], moment: datetime) -> datetime:
    """The most recent occurrence at or before ``moment``."""
    moment = moment.astimezone(timezone.utc)
    if schedule["kind"] == "interval":
        step = timedelta(minutes=int(schedule["every_minutes"]))
        return _EPOCH + step * ((moment - _EPOCH) // step)
    for candidate in _candidates(schedule, moment, forward=False):
        if candidate <= moment:
            return candidate
    raise AssertionError("unreachable: a weekly schedule has a day within 8 days")


def coalesce(
    schedule: Mapping[str, Any], *, due: datetime, now: datetime
) -> tuple[datetime, int, datetime]:
    """For a run that became due at ``due`` and is being handled at ``now``:
    ``(fire_for, missed, next_run)``.

    ``fire_for`` is the latest occurrence not after ``now`` (never earlier
    than ``due``); ``missed`` counts the occurrences skipped between ``due``
    and ``fire_for``; ``next_run`` is the first occurrence after ``now``.
    """
    fire_for = max(due, latest_at_or_before(schedule, now))
    missed = 0
    cursor = due
    while cursor < fire_for and missed < MAX_COUNTED_MISSES:
        cursor = next_after(schedule, cursor)
        if cursor <= fire_for:
            missed += 1
    return fire_for, missed, next_after(schedule, now)


def _candidates(schedule: Mapping[str, Any], around: datetime, *, forward: bool):
    zone = ZoneInfo(schedule["timezone"])
    hour, minute = (int(part) for part in schedule["time"].split(":"))
    local_day = around.astimezone(zone).date()
    allowed = set(schedule.get("days") or WEEKDAYS)
    offsets = range(-1, 9) if forward else range(1, -9, -1)
    for offset in offsets:
        day: date = local_day + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in allowed:
            continue
        # fold=0: a nonexistent time is read with the pre-transition offset
        # (so it lands just after the gap) and an ambiguous one resolves to
        # its first occurrence - the two DST rules in this module's
        # docstring, delegated to zoneinfo rather than re-implemented.
        local = datetime.combine(day, time(hour, minute), tzinfo=zone)
        yield local.astimezone(timezone.utc)


def _only(raw: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ScheduleError(f"Unknown schedule field(s): {', '.join(unknown)}.")


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    try:
        return frozenset(available_timezones())
    except ZoneInfoNotFoundError:  # pragma: no cover - no tz database at all
        return frozenset({"UTC"})
