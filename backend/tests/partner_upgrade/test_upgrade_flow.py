"""The upgrade, end to end through the real endpoints (ADR-049).

The claim: an Affiliate that grows becomes a Reseller or White-label **on the
same login and the same partner account** - no second user, no second partner,
no password reset - and everything it earned as an affiliate is still there. Each
test drives the browser's own calls: the partner opens, fills and submits the
request; staff approve it; the same email and password then sign in to the new
landing.
"""

from __future__ import annotations

import pytest

from tests.partner_upgrade.conftest import (
    PASSWORD,
    admin_path,
    decide,
    make_affiliate,
    make_reseller,
    portal,
    pp,
    request_upgrade,
    send,
    sign_in,
)
from workeasy360.commercial.commissions.models import (
    CommissionAccrual,
    Referral,
    ReferralCode,
)
from workeasy360.commercial.partners import capabilities as caps
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerBrand,
    PartnerCapabilityGrant,
    PartnerDomain,
    PartnerMember,
    PartnerProductGrant,
    PartnerUpgradeRequest,
)
from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db

AFFILIATE_CAPS = set(caps.EXPERIENCE_BUNDLES["affiliate"])
RESELLER_CAPS = set(caps.EXPERIENCE_BUNDLES["reseller"])
WHITE_LABEL_CAPS = set(caps.EXPERIENCE_BUNDLES["white_label"])
EMAIL = "owner@af-up.example.com"


def _held(partner_id: str) -> set[str]:
    return set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, status="active"
        ).values_list("capability", flat=True)
    )


def _history(partner_id: str) -> tuple[int, int, int]:
    return (
        ReferralCode.objects.filter(partner_id=partner_id).count(),
        Referral.objects.filter(partner_id=partner_id).count(),
        CommissionAccrual.objects.filter(partner_id=partner_id).count(),
    )


def _audit_rows(prefix: str) -> list:
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the upgrade audit entries", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.unscoped.filter(
                organization_id=PLATFORM_ORGANIZATION_ID, action__startswith=prefix
            )
        )


def _identity_counts() -> tuple[int, int, int]:
    return (
        User.objects.count(),
        PartnerAccount.objects.count(),
        PartnerMember.objects.count(),
    )


# --- what the partner sees before it asks -------------------------------------------------


def test_each_experience_is_offered_only_the_upgrades_above_it(catalog, admin):
    affiliate, af_id = make_affiliate(admin, email=EMAIL)
    reseller, rs_id = make_reseller(admin, email="owner@rs-up.example.com")
    from tests.partner_experience.conftest import provisioned

    white_label, wl_id = provisioned(
        admin, "white_label", code="wl-up", email="owner@wl-up.example.com"
    )
    seen = {
        "affiliate": affiliate.get(pp(af_id, "/upgrade")).json(),
        "reseller": reseller.get(pp(rs_id, "/upgrade")).json(),
        "white_label": white_label.get(pp(wl_id, "/upgrade")).json(),
    }
    assert seen["affiliate"]["current"] == "affiliate"
    assert seen["affiliate"]["eligible"] == ["reseller", "white_label"]
    # A Reseller is never offered Reseller; White-label has nothing above it.
    assert seen["reseller"]["current"] == "reseller"
    assert seen["reseller"]["eligible"] == ["white_label"]
    assert seen["white_label"]["current"] == "white_label"
    assert seen["white_label"]["eligible"] == []
    assert all(s["can_request"] and s["open"] is None for s in seen.values())


# --- Affiliate -> Reseller, on the same login -----------------------------------------------


def test_affiliate_becomes_reseller_on_the_same_login_and_account(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    assert portal(client)[0]["landing"] == "affiliate"
    users, partners, members = _identity_counts()
    held_before = _held(partner_id)
    assert held_before == AFFILIATE_CAPS

    request = request_upgrade(client, partner_id, "reseller")
    assert request["status"] == "pending_approval"
    # Asking granted nothing at all.
    assert _held(partner_id) == held_before
    assert portal(client)[0]["landing"] == "affiliate"

    approved = decide(
        admin,
        partner_id,
        request["id"],
        "approve",
        commercial_reference="CONTRACT-2026-17",
        note="Agreed on the call",
    )
    assert approved.status_code == 200, approved.content
    body = approved.json()
    assert body["status"] == "approved"
    assert set(body["capabilities_added"]) == RESELLER_CAPS - AFFILIATE_CAPS
    assert body["capabilities_removed"] == []
    assert body["products_granted"] == ["crm"]
    assert body["commercial_reference"] == "CONTRACT-2026-17"

    # No second user, partner or membership was created anywhere along the way.
    assert _identity_counts() == (users, partners, members)

    # Log out and in again with the SAME email and password: the Reseller opens.
    again, login = sign_in(EMAIL, PASSWORD)
    assert login.status_code == 200, login.content
    entry = portal(again)[0]
    assert entry["partner_id"] == partner_id
    assert entry["landing"] == "reseller"
    assert [w["key"] for w in entry["workspaces"]] == ["reseller", "affiliate"]
    assert RESELLER_CAPS <= set(entry["capabilities"])


def test_the_same_login_can_use_the_reseller_apis_only_after_approval(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    customers = pp(partner_id, "/customers")
    # Before: the reseller's customer API refuses the affiliate.
    assert client.get(customers).status_code == 403
    request = request_upgrade(client, partner_id, "reseller")
    assert client.get(customers).status_code == 403
    assert decide(admin, partner_id, request["id"], "approve").status_code == 200
    again, _ = sign_in(EMAIL, PASSWORD)
    assert again.get(customers).status_code == 200


def test_affiliate_history_survives_the_upgrade(catalog, admin):
    from workeasy360.commercial.commissions import referrals

    client, partner_id = make_affiliate(admin, email=EMAIL)
    code = ReferralCode.objects.get(partner_id=partner_id)
    assert referrals.capture_referral(code.code) is not None
    # A ledger row the affiliate has earned, by the staff endpoint a person uses.
    currency = PartnerAccount.objects.get(id=partner_id).payout_currency
    adjusted = send(
        admin,
        admin_path(partner_id, "/adjustments"),
        {
            "amount_minor": 2500,
            "currency": currency,
            "reason": "History that must survive an upgrade",
            "idempotency_key": "upgrade-history-1",
        },
    )
    assert adjusted.status_code == 201, adjusted.content
    before = _history(partner_id)
    assert before[0] == 1 and before[1] == 1
    ledger_before = _ledger_total(partner_id)
    assert ledger_before == 2500

    request = request_upgrade(client, partner_id, "reseller")
    assert decide(admin, partner_id, request["id"], "approve").status_code == 200

    assert _history(partner_id) == before
    assert _ledger_total(partner_id) == ledger_before
    # The published referral link still works: the affiliate set was kept, so a
    # link a customer already holds keeps earning for this partner after it grew.
    assert referrals.capture_referral(code.code) is not None
    assert Referral.objects.filter(partner_id=partner_id).count() == before[1] + 1
    # Its commission and payout screens stay open to it, on the same login.
    again, _ = sign_in(EMAIL, PASSWORD)
    assert again.get(pp(partner_id, "/commissions")).status_code == 200
    assert again.get(pp(partner_id, "/payouts")).status_code == 200


def _ledger_total(partner_id: str) -> int:
    from django.db.models import Sum

    return (
        CommissionAccrual.objects.filter(partner_id=partner_id).aggregate(
            t=Sum("amount_minor")
        )["t"]
        or 0
    )


# --- Affiliate -> White-label, directly --------------------------------------------------------


def test_affiliate_goes_straight_to_white_label_without_being_a_reseller_first(
    catalog, admin
):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    users, partners, _members = _identity_counts()

    request = request_upgrade(client, partner_id, "white_label")
    assert request["from_experience"] == "affiliate"
    assert request["to_experience"] == "white_label"

    approved = decide(admin, partner_id, request["id"], "approve")
    assert approved.status_code == 200, approved.content
    assert set(approved.json()["capabilities_added"]) == WHITE_LABEL_CAPS - AFFILIATE_CAPS
    assert (User.objects.count(), PartnerAccount.objects.count()) == (users, partners)

    # The proposed brand and domain went through the brand and domain commands.
    brand = PartnerBrand.objects.get(partner_id=partner_id)
    assert brand.brand_name == "Acme Cloud"
    assert brand.theme["primary"] == "#1a73e8" and brand.theme["accent"] == "#34a853"
    domain = PartnerDomain.objects.get(partner_id=partner_id)
    assert domain.hostname == "app.acme-partner.example.org"
    # A domain is only a request until its DNS proof is seen.
    assert domain.status in ("requested", "verification_pending")

    again, _ = sign_in(EMAIL, PASSWORD)
    entry = portal(again)[0]
    assert entry["landing"] == "white_label"
    # ADR-053: additive - Reseller is offered beside White-label.
    assert [w["key"] for w in entry["workspaces"]] == [
        "reseller",
        "white_label",
        "affiliate",
    ]


# --- Reseller -> White-label ----------------------------------------------------------------------


def test_reseller_upgrades_to_white_label_on_the_same_login(catalog, admin):
    client, partner_id = make_reseller(admin, email="owner@rs-up.example.com")
    users, partners, _members = _identity_counts()
    request = request_upgrade(client, partner_id, "white_label")
    assert request["from_experience"] == "reseller"

    approved = decide(admin, partner_id, request["id"], "approve")
    assert approved.status_code == 200, approved.content
    # Only the brand and domain pair is new: the commercial set was already held.
    assert set(approved.json()["capabilities_added"]) == {
        caps.BRANDING_MANAGE,
        caps.DOMAIN_MANAGE,
    }
    assert (User.objects.count(), PartnerAccount.objects.count()) == (users, partners)

    again, _ = sign_in("owner@rs-up.example.com", PASSWORD)
    entry = portal(again)[0]
    assert entry["landing"] == "white_label"
    # ADR-053: additive - the Reseller workspace stays beside White-label.
    assert [w["key"] for w in entry["workspaces"]] == ["reseller", "white_label"]


def test_after_an_upgrade_the_upgrade_options_move_up_with_it(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert decide(admin, partner_id, request["id"], "approve").status_code == 200
    again, _ = sign_in(EMAIL, PASSWORD)
    state = again.get(pp(partner_id, "/upgrade")).json()
    # Now a Reseller: no "Upgrade to Reseller", only White-label.
    assert state["current"] == "reseller"
    assert state["eligible"] == ["white_label"]
    assert state["open"] is None
    assert [r["status"] for r in state["recent"]] == ["approved"]


# --- the lifecycle -----------------------------------------------------------------------------------


def test_the_lifecycle_runs_requested_then_pending_then_approved(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = send(
        client, pp(partner_id, "/upgrade/requests"), {"experience": "reseller"}
    )
    assert started.status_code == 201
    assert started.json()["status"] == "requested"
    staff = admin.get(admin_path(partner_id, "/upgrades")).json()
    assert staff["status"] == "requested" and staff["requested"] == "reseller"
    assert staff["current"] == "affiliate"

    request = request_upgrade_from(client, partner_id, started.json())
    assert request["status"] == "pending_approval"
    assert (
        admin.get(admin_path(partner_id, "/upgrades")).json()["status"]
        == "pending_approval"
    )

    assert decide(admin, partner_id, request["id"], "approve").status_code == 200
    staff = admin.get(admin_path(partner_id, "/upgrades")).json()
    assert staff["status"] == "none"
    assert [h["status"] for h in staff["history"]] == ["approved"]


def request_upgrade_from(client, partner_id, started):
    from tests.partner_upgrade.conftest import fill, submit

    filled = fill(client, partner_id, started, started["to_experience"])
    submitted = submit(client, partner_id, filled)
    assert submitted.status_code == 200, submitted.content
    return submitted.json()


def test_staff_can_ask_for_more_and_the_partner_resubmits(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    asked = decide(
        admin, partner_id, request["id"], "request_info", note="Please confirm VAT"
    )
    assert asked.status_code == 200
    assert asked.json()["status"] == "profile_required"

    state = client.get(pp(partner_id, "/upgrade")).json()
    assert state["open"]["status"] == "profile_required"
    assert state["open"]["info_request_note"] == "Please confirm VAT"
    assert state["open"]["editable"] is True
    # Staff cannot approve what the partner is still completing.
    assert decide(admin, partner_id, request["id"], "approve").status_code == 409

    from tests.partner_upgrade.conftest import save, submit

    edited = save(
        client,
        partner_id,
        state["open"],
        "pricing",
        {"preference": "standard", "notes": "VAT number sent"},
    )
    assert edited.status_code == 200
    again = submit(client, partner_id, edited.json())
    assert again.status_code == 200 and again.json()["status"] == "pending_approval"
    assert again.json()["info_request_note"] == ""
    assert decide(admin, partner_id, request["id"], "approve").status_code == 200


def test_a_rejected_upgrade_changes_nothing_and_the_partner_may_try_again(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    held = _held(partner_id)
    request = request_upgrade(client, partner_id, "reseller")
    rejected = decide(admin, partner_id, request["id"], "reject", note="Not yet, sorry")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert _held(partner_id) == held
    assert not PartnerProductGrant.objects.filter(partner_id=partner_id).exists()
    state = client.get(pp(partner_id, "/upgrade")).json()
    assert state["current"] == "affiliate" and state["open"] is None
    assert state["recent"][0]["decision_note"] == "Not yet, sorry"
    assert state["eligible"] == ["reseller", "white_label"]
    # A new request is allowed once the old one is closed.
    assert (
        send(
            client, pp(partner_id, "/upgrade/requests"), {"experience": "white_label"}
        ).status_code
        == 201
    )


def test_staff_can_close_a_request_the_partner_started_and_never_sent(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = send(
        client, pp(partner_id, "/upgrade/requests"), {"experience": "reseller"}
    ).json()
    assert started["status"] == "requested"
    # Approving what was never submitted is refused; declining it is allowed.
    assert decide(admin, partner_id, started["id"], "approve").status_code == 409
    rejected = decide(
        admin, partner_id, started["id"], "reject", note="Closing an unfinished request"
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert _held(partner_id) == AFFILIATE_CAPS
    assert (
        send(
            client, pp(partner_id, "/upgrade/requests"), {"experience": "white_label"}
        ).status_code
        == 201
    )


def test_a_partner_can_withdraw_its_request(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    withdrawn = send(client, pp(partner_id, f"/upgrade/requests/{request['id']}/cancel"))
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "cancelled"
    # A withdrawn request cannot be approved.
    assert decide(admin, partner_id, request["id"], "approve").status_code == 409
    assert not _held(partner_id) & (RESELLER_CAPS - AFFILIATE_CAPS)


def test_the_requests_audit_trail_names_who_what_and_when_but_no_profile_values(
    catalog, admin
):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert (
        decide(
            admin, partner_id, request["id"], "approve", commercial_reference="PO-9"
        ).status_code
        == 200
    )
    entries = {e.action: e for e in _audit_rows("partner.upgrade_")}
    assert {
        "partner.upgrade_requested",
        "partner.upgrade_submitted",
        "partner.upgrade_approved",
    } <= set(entries)
    approved = entries["partner.upgrade_approved"].changes
    assert approved["from"] == "affiliate" and approved["to"] == "reseller"
    assert approved["commercial_reference"] == "PO-9"
    assert approved["requested_by"] and approved["requested_at"]
    assert set(approved["capabilities_added"]) == RESELLER_CAPS - AFFILIATE_CAPS
    assert approved["capabilities_removed"] == []
    assert entries["partner.upgrade_approved"].correlation_id is not None
    # What the partner typed never reaches the log.
    blob = repr([e.changes for e in entries.values()])
    for typed in (
        "Acme Partner Ltd",
        "GB123456789",
        "billing@acme.example.com",
        "Volume discount",
    ):
        assert typed not in blob
    row = PartnerUpgradeRequest.objects.get(id=request["id"])
    assert row.correlation_id is not None and row.decided_by_id is not None
