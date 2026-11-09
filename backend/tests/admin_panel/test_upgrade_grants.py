"""An upgrade must not lock the Admin out of what the release added.

A staff role is only a set of grants, so a new permission reaches nobody by itself. The
live Admin would meet a 403 on the wallet, KYC, report and support screens. Migration
``authz.0010`` gives the new keys to whoever already holds security administration.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone

from workeasy360.platform.authz import staff
from workeasy360.platform.authz.models import StaffGrant
from workeasy360.platform.authz.staff_roles import role, roles_held

pytestmark = pytest.mark.django_db

migration = importlib.import_module(
    "workeasy360.platform.authz.migrations.0010_admin_panel_staff_permissions"
)
NEW = set(migration.NEW_KEYS)

#: Every staff permission this release knows. Adding one? Existing Admins will not hold it.
#: Ship a migration like ``authz.0010`` that gives it to them, then add it here.
KNOWN_STAFF_KEYS = {
    "platform.staff_ai.manage",
    "platform.staff_ai.view",
    "platform.staff_audit.view",
    "platform.staff_billing.view",
    "platform.staff_catalog.manage",
    "platform.staff_catalog.view",
    "platform.staff_commission.manage",
    "platform.staff_commission.view",
    "platform.staff_communication.manage",
    "platform.staff_communication.view",
    "platform.staff_customer.manage",
    "platform.staff_customer.view",
    "platform.staff_integration.manage",
    "platform.staff_integration.view",
    "platform.staff_kyc.review",
    "platform.staff_kyc.view",
    "platform.staff_partner.manage",
    "platform.staff_partner.view",
    "platform.staff_payment.manage",
    "platform.staff_payment.view",
    "platform.staff_payout.approve",
    "platform.staff_payout.manage",
    "platform.staff_pricing.manage",
    "platform.staff_report.view",
    "platform.staff_security.manage",
    "platform.staff_security.view",
    "platform.staff_settings.manage",
    "platform.staff_settings.view",
    "platform.staff_subscription.manage",
    "platform.staff_support.manage",
    "platform.staff_support.view",
    "platform.staff_user.manage",
    "platform.staff_user.view",
    "platform.staff_wallet.manage",
    "platform.staff_wallet.view",
}


def _forward():
    migration.give(apps, None)


def _reverse():
    migration.take_back(apps, None)


def _live(user):
    return set(
        StaffGrant.objects.filter(user_id=user.id, revoked_at__isnull=True).values_list(
            "permission", flat=True
        )
    )


@pytest.fixture
def grant(user_factory):
    root = user_factory(is_staff=True)

    def give(user, permission, **terms):
        return staff.create_grant(
            user_id=user.id,
            permission=permission,
            granted_by_id=root.id,
            reason="Test fixture grants a control-plane permission",
            **terms,
        )[0]

    return give


def _as_the_live_release_left_it(user, grant):
    """The whole Admin role, minus what this release added."""
    for key in role("SUPER_ADMIN").permissions - NEW:
        grant(user, key)


def test_the_admin_of_the_live_release_keeps_the_whole_role_after_the_upgrade(
    user_factory, grant
):
    admin = user_factory(is_staff=True)
    _as_the_live_release_left_it(admin, grant)
    assert "SUPER_ADMIN" not in roles_held(_live(admin))

    _forward()

    assert NEW <= _live(admin)
    assert "SUPER_ADMIN" in roles_held(_live(admin))
    assert NEW <= staff.staff_permissions(admin.id)


def test_each_new_grant_says_why_and_is_not_a_self_grant(user_factory, grant):
    admin = user_factory(is_staff=True)
    grant(admin, staff.SECURITY_ADMIN)

    _forward()

    rows = StaffGrant.objects.filter(user_id=admin.id, permission__in=NEW)
    assert rows.count() == len(NEW)
    for row in rows:
        assert row.reason == migration.REASON
        assert row.granted_by_id != admin.id
        assert row.expires_at is None


def test_a_time_boxed_administrator_gets_nothing_that_outlasts_their_access(
    user_factory, grant
):
    admin = user_factory(is_staff=True)
    ends = timezone.now() + timedelta(days=3)
    grant(admin, staff.SECURITY_ADMIN, expires_at=ends)

    _forward()

    rows = StaffGrant.objects.filter(user_id=admin.id, permission__in=NEW)
    assert rows.count() == len(NEW)
    assert {row.expires_at for row in rows} == {ends}


def test_nobody_else_gains_anything(user_factory, grant):
    billing = user_factory(is_staff=True)
    grant(billing, "platform.staff_billing.view")
    grant(billing, "platform.staff_payout.manage")
    before = _live(billing)

    _forward()

    assert _live(billing) == before


def _two_days_on(monkeypatch):
    """A grant's expiry is frozen by the database, so it is the clock that moves."""
    later = timezone.now() + timedelta(days=2)
    monkeypatch.setattr(timezone, "now", lambda: later)


def test_someone_no_longer_in_charge_gains_nothing(user_factory, grant, monkeypatch):
    revoked = user_factory(is_staff=True)
    row = grant(revoked, staff.SECURITY_ADMIN)
    # Someone else must remain, or the last holder could not be removed.
    grant(user_factory(is_staff=True), staff.SECURITY_ADMIN)
    staff.end_grant(
        grant_id=row.id, revoked_by_id=row.granted_by_id, reason="No longer staff"
    )

    expired = user_factory(is_staff=True)
    grant(expired, staff.SECURITY_ADMIN, expires_at=timezone.now() + timedelta(days=1))

    deactivated = user_factory(is_staff=True)
    grant(deactivated, staff.SECURITY_ADMIN)
    type(deactivated).objects.filter(id=deactivated.id).update(status="deactivated")

    _two_days_on(monkeypatch)
    _forward()

    for person in (revoked, expired, deactivated):
        assert not (_live(person) & NEW), person.email


def test_running_it_twice_changes_nothing(user_factory, grant):
    admin = user_factory(is_staff=True)
    grant(admin, staff.SECURITY_ADMIN)

    _forward()
    first = set(
        StaffGrant.objects.filter(permission__in=NEW).values_list("id", flat=True)
    )
    _forward()

    assert (
        set(StaffGrant.objects.filter(permission__in=NEW).values_list("id", flat=True))
        == first
    )


def test_an_expired_grant_of_a_new_key_is_closed_and_replaced(
    user_factory, grant, monkeypatch
):
    admin = user_factory(is_staff=True)
    grant(admin, staff.SECURITY_ADMIN)
    row = grant(
        admin, "platform.staff_wallet.view", expires_at=timezone.now() + timedelta(days=1)
    )

    _two_days_on(monkeypatch)
    _forward()

    row.refresh_from_db()
    assert row.revoked_at is not None
    assert row.revoke_reason == migration.EXPIRED
    assert "platform.staff_wallet.view" in staff.staff_permissions(admin.id)


def test_reversing_stamps_its_own_rows_and_leaves_a_persons_grant(user_factory, grant):
    admin = user_factory(is_staff=True)
    grant(admin, staff.SECURITY_ADMIN)
    colleague = user_factory(is_staff=True)
    given_by_a_person = grant(colleague, "platform.staff_wallet.view")

    _forward()
    made = StaffGrant.objects.filter(user_id=admin.id, permission__in=NEW).count()
    _reverse()

    # Never deleted, and no longer in force.
    assert StaffGrant.objects.filter(user_id=admin.id, permission__in=NEW).count() == made
    assert not (_live(admin) & NEW)
    assert (
        StaffGrant.objects.filter(
            user_id=admin.id, permission__in=NEW, revoke_reason=migration.REVERSED
        ).count()
        == made
    )
    given_by_a_person.refresh_from_db()
    assert given_by_a_person.revoked_at is None

    # And it can be applied again.
    _forward()
    assert NEW <= _live(admin)


def test_a_new_staff_permission_cannot_arrive_without_an_upgrade_plan():
    assert role("SUPER_ADMIN").permissions == KNOWN_STAFF_KEYS
