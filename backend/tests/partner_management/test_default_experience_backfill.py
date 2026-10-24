"""partners.0008: every existing partner gets an explicit landing workspace.

The migration freezes ``capabilities.resolve_landing`` with an empty
preference; these tests hold the two equal, and show that the backfill only
fills empty rows and never touches a capability.
"""

from __future__ import annotations

import importlib
from itertools import combinations

import pytest
from django.apps import apps

from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners.models import PartnerAccount, PartnerCapabilityGrant

migration = importlib.import_module(
    "workeasy360.commercial.partners.migrations.0008_backfill_default_experience"
)

_KEYS = sorted(
    {
        *migration._RESELLER,
        *migration._WHITE_LABEL,
        *migration._AFFILIATE,
        capabilities.SUPPORT_ACCESS,
    }
)


def test_the_frozen_rule_equals_resolve_landing_for_every_grant_mix():
    for size in range(len(_KEYS) + 1):
        for held in combinations(_KEYS, size):
            expected = capabilities.resolve_landing("", frozenset(held)) or ""
            assert migration._landing(set(held)) == expected, held


def test_the_frozen_key_sets_match_the_live_ones():
    assert migration._RESELLER == set(capabilities.experience_keys(capabilities.RESELLER))
    assert migration._WHITE_LABEL == set(
        capabilities.experience_keys(capabilities.WHITE_LABEL)
    )
    assert migration._AFFILIATE == set(
        capabilities.experience_keys(capabilities.AFFILIATE)
    )


@pytest.mark.django_db
def test_it_fills_empty_rows_and_keeps_chosen_ones(reseller, white_label, affiliate):
    ids = {
        kind: fixture[1]
        for kind, fixture in (
            ("reseller", reseller),
            ("white_label", white_label),
            ("affiliate", affiliate),
        )
    }
    # The rows as an older release left them, except the affiliate, whose
    # administrator chose the reseller workspace (kept even though it holds no
    # reseller grant: the read path resolves it, the backfill never rewrites it).
    PartnerAccount.objects.filter(id__in=[ids["reseller"], ids["white_label"]]).update(
        default_experience=""
    )
    PartnerAccount.objects.filter(id=ids["affiliate"]).update(
        default_experience="reseller"
    )
    grants_before = sorted(
        PartnerCapabilityGrant.objects.values_list("partner_id", "capability", "status")
    )

    migration.backfill(apps, None)

    stored = {
        str(k): v
        for k, v in PartnerAccount.objects.values_list("id", "default_experience")
    }
    assert stored[ids["reseller"]] == "reseller"
    assert stored[ids["white_label"]] == "white_label"
    assert stored[ids["affiliate"]] == "reseller"
    assert grants_before == sorted(
        PartnerCapabilityGrant.objects.values_list("partner_id", "capability", "status")
    )
