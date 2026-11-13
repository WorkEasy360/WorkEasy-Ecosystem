"""Admin panel: whoever runs the platform today receives the permissions this release adds.

A staff role is not stored. Holding one means holding a ``StaffGrant`` for each of its
keys, so a key added to the registry reaches nobody by itself, and an Admin who upgrades
would lose the new screens with a 403. ``bootstrap_superadmin`` would add them, but the
deployment does not run it. So, as with the system roles (``0004``, ``0007``, ``0008``),
the upgrade ships a migration, and the spec is frozen here rather than read from the
live registry.

**Who receives them:** every active staff member with a grant of
``platform.staff_security.manage`` in force. They can already grant themselves anything,
so this adds no power anyone lacks. A time-boxed holder's new grants end at the same
moment. Nobody else is touched: a Billing Admin is given wallet access by a person, with
a reason, because for them it is new power.

**The record is the row:** ``granted_by``, ``granted_at`` and ``reason``, which is how this
table answers who could do what, and when. No audit entry is written: that log is a hash
chain, and a migration must not depend on the code that computes it.

Reversing stamps the rows it made as revoked, because grants are never deleted, and leaves
alone any grant a person made. Running forward twice changes nothing.
"""

from __future__ import annotations

from django.db import migrations
from django.db.models import F, Q
from django.utils import timezone

SECURITY_ADMIN = "platform.staff_security.manage"

#: The control-plane keys added since the live release.
NEW_KEYS = (
    "platform.staff_kyc.review",
    "platform.staff_kyc.view",
    "platform.staff_report.view",
    "platform.staff_support.manage",
    "platform.staff_wallet.manage",
    "platform.staff_wallet.view",
)

REASON = "Release adds these to the Admin role; given to each security administrator"
REVERSED = "The release that gave this grant was reversed"
EXPIRED = "Expired; superseded by a new grant"
SYSTEM_EMAIL = "platform-control-plane@workeasy360.invalid"


def give(apps, schema_editor):
    StaffGrant = apps.get_model("authz", "StaffGrant")
    User = apps.get_model("identity", "User")
    now = timezone.now()
    in_force = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    holders = list(
        StaffGrant.objects.filter(
            in_force,
            permission=SECURITY_ADMIN,
            revoked_at__isnull=True,
            user__is_staff=True,
            user__status="active",
        )
    )
    if not holders:
        return
    system = User.objects.filter(email=SYSTEM_EMAIL).first()
    for holder in holders:
        # Nobody grants to themselves, so without the system identity the person who
        # made this holder a security administrator stands behind the grant.
        by = system.id if system is not None else holder.granted_by_id
        for key in NEW_KEYS:
            live = StaffGrant.objects.filter(
                user_id=holder.user_id, permission=key, revoked_at__isnull=True
            ).first()
            if live is not None:
                if live.expires_at is None or live.expires_at > now:
                    continue
                live.revoked_at = now
                live.revoked_by_id = by
                live.revoke_reason = EXPIRED
                live.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
            StaffGrant.objects.create(
                user_id=holder.user_id,
                permission=key,
                granted_by_id=by,
                granted_at=now,
                reason=REASON,
                expires_at=holder.expires_at,
            )


def take_back(apps, schema_editor):
    StaffGrant = apps.get_model("authz", "StaffGrant")
    StaffGrant.objects.filter(
        permission__in=NEW_KEYS, reason=REASON, revoked_at__isnull=True
    ).update(
        revoked_at=timezone.now(),
        revoked_by_id=F("granted_by_id"),
        revoke_reason=REVERSED,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("authz", "0009_billing_manage_not_dangerous"),
        ("identity", "0008_password_change_required"),
    ]

    operations = [
        migrations.RunPython(give, take_back),
    ]
