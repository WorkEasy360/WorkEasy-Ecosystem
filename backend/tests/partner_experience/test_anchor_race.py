"""Two find-or-create paths that lost a race with a ValidationError (2026-11-18).

``test_six_creators_naming_one_address_make_one_partner_and_one_account`` failed
on CI under load with ``ValidationError({'email': ['User with this Email already
exists.']})``. The address was not the partner's: it was the control-plane
system account. The first control-plane command after a flush creates the
platform organization anchor (``ensure_platform_organization``), and six racing
creators all did. A caller that saw no system account could lose at the insert
(``IntegrityError``, handled) or a moment earlier, when ``full_clean`` re-checks
the address and sees the winner's committed row (``ValidationError``, not
handled). ``ensure_identity_for_email`` had the same gap.

These tests widen that window on purpose: each racer that saw no account pauses
a little longer than the one before it, so the first commits before the next
one's ``full_clean`` re-checks the address - the CI interleaving, on every run.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

from workeasy360.contracts.values.control_plane import (
    PLATFORM_ORGANIZATION_ID,
    PLATFORM_SYSTEM_USER_EMAIL,
)
from workeasy360.platform.identity.models import User

RACERS = 6


def staggered(real):
    """``real``, called after a pause that grows with each call."""
    order = iter(range(RACERS * 4))
    lock = threading.Lock()

    def call(*args, **kwargs):
        with lock:
            turn = next(order)
        time.sleep(0.2 + 0.25 * turn)
        return real(*args, **kwargs)

    return call


def race(worker):
    results: list = []
    unexpected: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(RACERS)

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                unexpected.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=RACERS) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(RACERS)]):
            future.result()
    return results, unexpected


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_racing_first_control_plane_calls_make_one_anchor_and_raise_nothing(
    monkeypatch,
):
    from workeasy360.platform.authz.control_plane import ensure_platform_organization
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.organizations.selectors import organization_status

    assert organization_status(PLATFORM_ORGANIZATION_ID) is None
    monkeypatch.setattr(lifecycle, "create_account", staggered(lifecycle.create_account))

    results, unexpected = race(lambda _i: ensure_platform_organization())

    assert unexpected == []
    assert len(results) == RACERS
    assert User.objects.filter(email=PLATFORM_SYSTEM_USER_EMAIL).count() == 1
    assert Organization.objects.filter(id=PLATFORM_ORGANIZATION_ID).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_racing_finds_or_creates_of_one_address_end_with_its_one_account(
    monkeypatch,
):
    from workeasy360.platform.identity.services import ensure_identity_for_email

    monkeypatch.setattr(User.objects, "create_user", staggered(User.objects.create_user))

    results, unexpected = race(
        lambda _i: ensure_identity_for_email("racer@anchor-race.example.com")
    )

    assert unexpected == []
    ids = {user_id for user_id, _created in results}
    assert len(ids) == 1 and None not in ids
    assert sum(1 for _id, created in results if created) == 1
    assert User.objects.filter(email="racer@anchor-race.example.com").count() == 1
