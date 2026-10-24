"""Give every existing partner an explicit landing workspace.

Partners created before 0007 hold ``default_experience = ""``. The read path
already resolves an empty preference (``capabilities.resolve_landing``: the
first workspace the live grants open), so this changes nothing anyone sees -
it records that same answer, so each partner has an explicit primary workspace
an administrator can read and change.

The rule, frozen here rather than imported (a migration must not change when
the code does), is ``resolve_landing`` with an empty preference:

- a partner holding a reseller capability and no white-label one: ``reseller``;
- a partner holding a white-label capability: ``white_label`` (it subsumes
  the reseller workspace - the same commercial grants plus branding);
- else a partner holding a referral capability: ``affiliate``;
- else (no workspace at all, e.g. support-only): left empty - the generic
  panel opens, as before.

Only rows still empty are written; a value set by an administrator is kept.
Capabilities are read, never changed. Reverse is a no-op: the value is a
preference the previous release never reads.
"""

from __future__ import annotations

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

_RESELLER = {"customer.provision", "customer.manage", "subscription.sell", "subscription.manage"}
_WHITE_LABEL = {"branding.manage", "domain.manage"}
_AFFILIATE = {"referral.create", "referral.view"}


def _landing(held: set[str]) -> str:
    if held & _WHITE_LABEL:
        return "white_label"
    if held & _RESELLER:
        return "reseller"
    if held & _AFFILIATE:
        return "affiliate"
    return ""


def backfill(apps, schema_editor):
    PartnerAccount = apps.get_model("partners", "PartnerAccount")
    PartnerCapabilityGrant = apps.get_model("partners", "PartnerCapabilityGrant")
    now = timezone.now()
    for partner_id in (
        PartnerAccount.objects.filter(default_experience="")
        .values_list("id", flat=True)
        .iterator()
    ):
        held = set(
            PartnerCapabilityGrant.objects.filter(partner_id=partner_id, status="active")
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .values_list("capability", flat=True)
        )
        value = _landing(held)
        if value:
            PartnerAccount.objects.filter(id=partner_id, default_experience="").update(
                default_experience=value
            )


class Migration(migrations.Migration):
    overlap_safety = (
        "Data only: fills the 0007 column on rows that hold the empty default; "
        "no schema change, and the previous release never reads the column"
    )

    dependencies = [("partners", "0007_partner_default_experience")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
