"""Operator designation of staff identities (``w360_staff_identity``). The
first administrator's bootstrap moved to ``bootstrap_superadmin`` (Phase 9,
tests/phase9/test_super_admin.py)."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


def test_a_django_superuser_gets_no_control_plane_power_from_the_flag(user_factory):
    from workeasy360.platform.authz import staff

    root = user_factory(email="root@example.com", is_staff=True, is_superuser=True)
    assert staff.staff_permissions(root.id) == frozenset()


# --- w360_staff_identity: designating (and removing) further staff identities ------------


def identity(*args):
    call_command("w360_staff_identity", *args)


def test_a_second_person_becomes_a_staff_identity_only_through_the_operator_command(
    user_factory, staff_client
):
    """Final audit: the grant screen refuses non-staff accounts, so without
    this the only way to onboard a second staff member was raw SQL."""
    from workeasy360.contracts.errors import ControlPlaneInvariantViolation
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)
    person = user_factory(email="second.staff@example.com")
    with pytest.raises(ControlPlaneInvariantViolation):
        staff.create_grant(
            user_id=person.id,
            permission="platform.staff_customer.view",
            granted_by_id=root.id,
            reason="Not yet a staff identity",
        )
    identity("--email", person.email, "--enable", "--reason", "Joined the support team")
    person.refresh_from_db()
    assert not person.is_staff  # report only without --confirm
    identity(
        "--email",
        person.email,
        "--enable",
        "--reason",
        "Joined the support team",
        "--confirm",
    )
    person.refresh_from_db()
    assert person.is_staff and staff.staff_permissions(person.id) == frozenset()
    reader, _u = staff_client("platform.staff_audit.view")
    rows = reader.get("/api/v1/admin/audit", {"action": "staff.identity"}).json()[
        "results"
    ]
    assert [r["action"] for r in rows] == ["staff.identity_enabled"]


def test_disabling_a_staff_identity_ends_every_grant_at_once_and_keeps_the_history(
    user_factory,
):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    root = user_factory(is_staff=True)
    person = user_factory(is_staff=True)
    for permission in ("platform.staff_payout.approve", "platform.staff_customer.manage"):
        staff.create_grant(
            user_id=person.id,
            permission=permission,
            granted_by_id=root.id,
            reason="Grants of a person who then leaves",
        )
    identity(
        "--email",
        person.email,
        "--disable",
        "--reason",
        "Suspected account compromise",
        "--confirm",
    )
    assert staff.staff_permissions(person.id) == frozenset()
    assert StaffGrant.objects.filter(user=person, revoked_at__isnull=True).count() == 2


def test_the_last_permanent_security_administrator_cannot_be_undesignated(
    user_factory, monkeypatch
):
    from workeasy360.platform.authz import staff

    monkeypatch.setenv(
        "PLATFORM_SUPERADMIN_EMAIL", user_factory(email="only@example.com").email
    )
    monkeypatch.setenv("PLATFORM_SUPERADMIN_PASSWORD", "a long operator passphrase 77")
    call_command("bootstrap_superadmin")
    with pytest.raises(CommandError, match="last permanent security administrator"):
        identity(
            "--email",
            "only@example.com",
            "--disable",
            "--reason",
            "Removing the only admin",
            "--confirm",
        )
    assert staff.security_administrator_exists()
