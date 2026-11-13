"""Partners → Affiliate, Reseller, White Label (ADR-062).

Each partner is listed under exactly one category: the type the All Partners
table already shows it as (the workspace it lands in), or - for a partner whose
grants earn none yet, such as a new draft - the type it was created as. The
database decides (``console_reads.category_q``); the row says the same
(``capabilities.partner_category``); the two are held together here over every
combination. All Partners still lists everyone.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.utils import timezone

from tests.guest_login.conftest import CREATE, make_body, send
from workeasy360.commercial.partners import capabilities

pytestmark = pytest.mark.django_db

LIST = "/api/v1/admin/partners/resellers"

#: One capability that earns each label.
EARNS = {
    capabilities.AFFILIATE: "referral.create",
    capabilities.RESELLER: "customer.provision",
    capabilities.WHITE_LABEL: "branding.manage",
}


def listed(client, **params) -> dict[str, dict]:
    response = client.get(LIST, {"scope": "all", "limit": 100, **params})
    assert response.status_code == 200, response.content
    return {row["id"]: row for row in response.json()["results"]}


def test_every_combination_is_listed_under_the_category_its_row_shows(
    catalog, admin, user_factory
):
    from workeasy360.commercial.partners.models import (
        PartnerAccount,
        PartnerCapabilityGrant,
    )

    creator = user_factory(is_staff=True)
    now = timezone.now()
    expected: dict[str, str | None] = {}
    labels = list(EARNS)
    for size in range(len(labels) + 1):
        for earned in itertools.combinations(labels, size):
            for stored in ("", *labels):
                partner = PartnerAccount.objects.create(
                    code=f"m-{uuid.uuid4().hex[:10]}",
                    display_name=f"Matrix {'+'.join(earned) or 'none'} / {stored or '-'}",
                    payout_currency="GBP",
                    status="active",
                    default_experience=stored,
                    created_by=creator,
                )
                for label in earned:
                    PartnerCapabilityGrant.objects.create(
                        partner=partner,
                        capability=EARNS[label],
                        status="active",
                        granted_by=creator,
                        granted_at=now,
                    )
                expected[str(partner.id)] = capabilities.partner_category(
                    stored, {EARNS[label] for label in earned}
                )

    everyone = listed(admin)
    assert set(expected) <= set(everyone)
    seen: dict[str, list[str]] = {pid: [] for pid in expected}
    for category in capabilities.CATEGORIES:
        rows = listed(admin, category=category)
        for pid, row in rows.items():
            if pid in seen:
                seen[pid].append(category)
                # The row says the category the database filtered it into.
                assert row["category"] == category, (row["display_name"], category)
    for pid, categories in seen.items():
        want = expected[pid]
        assert categories == ([want] if want else []), (
            everyone[pid]["display_name"],
            categories,
        )
        assert everyone[pid]["category"] == want


def test_the_real_three_types_are_each_in_exactly_one_list(
    catalog, admin, reseller, white_label, affiliate
):
    ids = {
        "reseller": reseller[1],
        "white_label": white_label[1],
        "affiliate": affiliate[1],
    }
    # ADR-063: a White Label partner holds the reseller's grants only when they
    # were granted beside it; this one was not, so the label filter agrees.
    by_label = listed(admin, type="reseller")
    assert ids["reseller"] in by_label and ids["white_label"] not in by_label
    for category, partner_id in ids.items():
        rows = listed(admin, category=category)
        assert partner_id in rows, category
        others = set(ids.values()) - {partner_id}
        assert not others & set(rows), category
        assert rows[partner_id]["primary_experience"] == category
    assert set(ids.values()) <= set(listed(admin))


@pytest.mark.parametrize("kind", ["affiliate", "reseller", "white_label"])
def test_a_new_partner_is_listed_under_its_type_at_once(catalog, admin, kind):
    response = send(
        admin,
        CREATE,
        make_body(
            kind, name=f"New {kind}", email=f"new-{kind}@fresh-partner.example.com"
        ),
    )
    assert response.status_code == 201, response.content
    partner_id = response.json()["id"]
    rows = listed(admin, category=kind)
    assert partner_id in rows
    assert rows[partner_id]["status"] == "draft"
    assert rows[partner_id]["category"] == kind
    for other in set(capabilities.CATEGORIES) - {kind}:
        assert partner_id not in listed(admin, category=other)


def test_the_category_composes_with_the_other_filters(
    catalog, admin, reseller, affiliate
):
    _owner, reseller_id = reseller
    assert reseller_id in listed(admin, category="reseller", status="active")
    assert reseller_id not in listed(admin, category="reseller", status="suspended")
    assert listed(admin, category="reseller", q="no-such-partner-anywhere") == {}


def test_an_unknown_category_is_refused(admin):
    response = admin.get(LIST, {"scope": "all", "category": "gold"})
    assert response.status_code == 400
    assert response.json()["field"] == "category"


def test_the_overview_cards_count_what_their_pages_list(
    catalog, admin, reseller, white_label, affiliate
):
    cards = {
        c["key"]: c["count"]
        for c in admin.get("/api/v1/admin/partners/overview").json()["cards"]
    }
    assert cards["affiliates"] == len(listed(admin, category="affiliate"))
    assert cards["resellers"] == len(listed(admin, category="reseller"))
    assert cards["white_label"] == len(listed(admin, category="white_label"))
    assert cards["total_partners"] == len(listed(admin))
