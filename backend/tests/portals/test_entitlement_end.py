"""An entitlement ends at its end by server time - not when a sweeper next
rewrites the snapshot (found while proving the 7-day trials, ADR-043).

Before this, the snapshot entry carried no end and ``decide`` read only its
status, so a trial kept granting access past ``ends_at`` until the trial
sweeper ran. Every source already had the right ``effective_to``; the entry
now carries it as ``ends_at`` and the one decision function enforces it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from workeasy360.platform.entitlements.services.read import decide, more_generous_product


def _entry(status="trial", ends_at=None, **extra):
    return {
        "status": status,
        "seats": 5,
        "write": True,
        "grace_ends_at": None,
        "ends_at": ends_at,
        **extra,
    }


def test_an_entry_is_allowed_until_its_end_and_denied_from_it(monkeypatch):
    ends = timezone.now() + timedelta(days=7)
    entry = _entry(ends_at=ends.isoformat())
    monkeypatch.setattr(timezone, "now", lambda: ends - timedelta(microseconds=1))
    assert decide(entry, "easy_crm").allowed
    monkeypatch.setattr(timezone, "now", lambda: ends)
    denied = decide(entry, "easy_crm")
    assert not denied.allowed
    assert denied.status.value == "expired"
    assert denied.upgrade_hint == "resubscribe:easy_crm"


def test_an_open_ended_entry_never_ends_by_time():
    assert decide(_entry(status="active"), "easy_crm").allowed


def test_an_entry_written_before_ends_at_existed_behaves_as_before():
    legacy = {"status": "trial", "seats": 5, "write": True, "grace_ends_at": None}
    assert decide(legacy, "easy_crm").allowed


@pytest.mark.parametrize("raw", ["not a date", "2026-13-45T00:00:00"])
def test_an_unreadable_end_fails_closed(raw):
    assert not decide(_entry(ends_at=raw), "easy_crm").allowed


def test_overlapping_sources_end_when_the_last_one_does():
    soon = (timezone.now() + timedelta(days=1)).isoformat()
    later = (timezone.now() + timedelta(days=9)).isoformat()
    merged = more_generous_product(_entry(ends_at=soon), _entry(ends_at=later))
    assert merged["ends_at"] == later
    # A paid, open-ended line wins over a trial's end: buying never brings
    # the end forward.
    paid = more_generous_product(_entry(ends_at=soon), _entry(status="active"))
    assert paid["ends_at"] is None and paid["status"] == "active"
