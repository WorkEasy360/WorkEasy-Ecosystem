"""Login as Guest: whoever runs the platform today receives its new permission.

``platform.staff_partner.guest`` (ADR-062) is new, and a staff role is only the
``StaffGrant`` rows its holder has, so without this migration the live Admin would
lose the guest login the release ships (it used to need
``platform.staff_partner.manage``). As in ``0010``, the key is given to every active
staff member holding ``platform.staff_security.manage`` in force - they can already
grant themselves anything, so this adds no power anyone lacks - and a time-boxed
holder's new grant ends at the same moment. Nobody else is touched: anyone else gets
it from a person, with a reason.

The record is the row (``granted_by``, ``granted_at``, ``reason``); no audit entry is
written, because that log is a hash chain and a migration must not depend on the code
that computes it. Reversing stamps the rows this made as revoked; running forward
twice changes nothing.
"""

from __future__ import annotations

from django.db import migrations
from django.db.models import F, Q
from django.utils import timezone

SECURITY_ADMIN = "platform.staff_security.manage"

#: The control-plane keys added since the previous release.
NEW_KEYS = ("platform.staff_partner.guest",)

REASON = (
    "Release adds Login as Guest to the Admin role; given to each security administrator"
)
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
        ("authz", "0010_admin_panel_staff_permissions"),
        ("identity", "0008_password_change_required"),
    ]

    operations = [
        migrations.RunPython(give, take_back),
    ]
