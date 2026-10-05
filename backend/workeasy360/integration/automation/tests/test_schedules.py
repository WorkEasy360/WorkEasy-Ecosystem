"""Schedules: cadence bounds, timezones, DST and missed runs (Tasks 42-45)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from workeasy360.integration.automation.schedules import (
    ScheduleError,
    coalesce,
    latest_at_or_before,
    next_after,
    normalize,
)

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")


def _daily(at: str, zone: str = "Europe/London"):
    return normalize(
        {"kind": "daily", "time": at, "timezone": zone}, min_interval_minutes=15
    )


def test_a_sub_minimum_interval_is_refused():
    """No every-second schedules: the floor is a setting (15 minutes)."""
    with pytest.raises(ScheduleError):
        normalize({"kind": "interval", "every_minutes": 1}, min_interval_minutes=15)
    assert normalize({"kind": "interval", "every_minutes": 15}, min_interval_minutes=15)


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "cron", "expression": "* * * * *"},
        {"kind": "daily", "time": "25:00", "timezone": "Europe/London"},
        {"kind": "daily", "time": "09:00", "timezone": "Mars/Olympus"},
        {"kind": "daily", "time": "09:00", "timezone": "+01:00"},
        {"kind": "weekly", "time": "09:00", "timezone": "UTC", "days": []},
        {"kind": "weekly", "time": "09:00", "timezone": "UTC", "days": ["funday"]},
        {"kind": "interval", "every_minutes": True},
        {"kind": "interval", "every_minutes": 60, "extra": 1},
    ],
)
def test_malformed_schedules_are_refused(raw):
    with pytest.raises(ScheduleError):
        normalize(raw, min_interval_minutes=15)


def test_interval_schedules_align_to_the_epoch():
    schedule = normalize(
        {"kind": "interval", "every_minutes": 60}, min_interval_minutes=15
    )
    after = datetime(2026, 9, 23, 10, 20, tzinfo=UTC)
    assert next_after(schedule, after) == datetime(2026, 9, 23, 11, 0, tzinfo=UTC)
    assert latest_at_or_before(schedule, after) == datetime(
        2026, 9, 23, 10, 0, tzinfo=UTC
    )


def test_a_daily_schedule_keeps_its_local_time_across_dst():
    """09:00 London is 08:00 UTC in summer and 09:00 UTC in winter."""
    schedule = _daily("09:00")
    summer = next_after(schedule, datetime(2026, 7, 1, 0, 0, tzinfo=UTC))
    winter = next_after(schedule, datetime(2026, 12, 1, 0, 0, tzinfo=UTC))
    assert summer == datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    assert winter == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


def test_a_nonexistent_local_time_runs_just_after_the_gap():
    """01:30 does not exist in London on 29 March 2026 (01:00 -> 02:00). The
    run is not skipped; it happens at the equivalent instant after the gap."""
    schedule = _daily("01:30")
    fired = next_after(schedule, datetime(2026, 3, 28, 12, 0, tzinfo=UTC))
    assert fired == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert fired.astimezone(LONDON).strftime("%H:%M") == "02:30"


def test_an_ambiguous_local_time_runs_once():
    """01:30 happens twice in London on 25 October 2026. Exactly one run."""
    schedule = _daily("01:30")
    first = next_after(schedule, datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    second = next_after(schedule, first)
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # the BST occurrence
    assert second.date() == datetime(2026, 10, 26, tzinfo=UTC).date()


def test_weekly_schedules_run_on_their_days_only():
    schedule = normalize(
        {"kind": "weekly", "days": ["mon", "fri"], "time": "08:30", "timezone": "UTC"},
        min_interval_minutes=15,
    )
    # Wednesday 23 September 2026 -> next is Friday 25th.
    assert next_after(schedule, datetime(2026, 9, 23, 12, 0, tzinfo=UTC)) == datetime(
        2026, 9, 25, 8, 30, tzinfo=UTC
    )


def test_missed_runs_are_coalesced_into_one():
    """After a 5-hour outage an hourly rule fires once, for the latest missed
    hour, and reports how many it skipped - never five catch-up runs."""
    schedule = normalize(
        {"kind": "interval", "every_minutes": 60}, min_interval_minutes=15
    )
    due = datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
    now = datetime(2026, 9, 23, 10, 20, tzinfo=UTC)
    fire_for, missed, next_run = coalesce(schedule, due=due, now=now)
    assert fire_for == datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    assert missed == 5
    assert next_run == datetime(2026, 9, 23, 11, 0, tzinfo=UTC)


def test_an_on_time_run_misses_nothing():
    schedule = _daily("09:00", "UTC")
    due = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
    fire_for, missed, _next = coalesce(schedule, due=due, now=due)
    assert (fire_for, missed) == (due, 0)
