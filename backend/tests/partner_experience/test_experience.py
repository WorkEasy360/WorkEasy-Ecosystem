"""Experiences: bundles, workspaces, the landing preference and changing a type later.

The claim is that an experience is a *label and a landing* over capability
grants (ADR-047), never a kind of account. So these tests pin the pure rules
(which workspaces a set of grants opens, which one a preference lands on) and
the one transition service that moves a partner between experiences after
creation - additively, atomically, audited, and without touching history.
"""

from __future__ import annotations

import json

import pytest
from django.db import IntegrityError, transaction

from tests.partner_experience.conftest import CREATE, make_body, send
from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.commissions.models import (
    CommissionAccrual,
    Referral,
    ReferralCode,
)
from workeasy360.commercial.partners import capabilities as caps
from workeasy360.commercial.partners import experience, selectors
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerProductGrant,
)
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

RESELLER, WHITE_LABEL, AFFILIATE = caps.RESELLER, caps.WHITE_LABEL, caps.AFFILIATE
AFFILIATE_CAPS = caps.EXPERIENCE_BUNDLES[AFFILIATE]


# --- the bundles (pure) ---------------------------------------------------------------


def test_every_bundle_key_is_a_registered_capability():
    for key, bundle in caps.EXPERIENCE_BUNDLES.items():
        assert set(bundle) <= set(caps.CAPABILITIES), key
        assert len(set(bundle)) == len(bundle), "no key twice"
    assert set(caps.EXPERIENCE_BUNDLES) == set(caps.EXPERIENCES)


def test_each_bundle_earns_its_own_label():
    for key, bundle in caps.EXPERIENCE_BUNDLES.items():
        assert key in [e["key"] for e in caps.experience_labels(set(bundle))]


def test_the_affiliate_bundle_reaches_no_customer_price_or_subscription():
    forbidden = {
        caps.CUSTOMER_PROVISION,
        caps.CUSTOMER_MANAGE,
        caps.SUBSCRIPTION_SELL,
        caps.SUBSCRIPTION_MANAGE,
        caps.PRICING_VIEW,
        caps.BRANDING_MANAGE,
        caps.DOMAIN_MANAGE,
        caps.SUBPARTNER_MANAGE,
        caps.SUPPORT_ACCESS,
    }
    assert not forbidden & set(caps.EXPERIENCE_BUNDLES[AFFILIATE])
    # And so the affiliate's permissions hold none of the customer-side ones.
    permissions = caps.permissions_for(
        set(caps.EXPERIENCE_BUNDLES[AFFILIATE]), admin=True
    )
    assert not {p for p in permissions if "customer" in p or "subscription" in p}


def test_the_white_label_bundle_is_the_commercial_set_plus_brand_and_domain():
    wl = set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL])
    assert {caps.BRANDING_MANAGE, caps.DOMAIN_MANAGE} <= wl
    assert set(caps._COMMERCIAL) <= wl
    assert caps.SUBPARTNER_MANAGE not in wl
    assert caps.SUBPARTNER_MANAGE in caps.EXPERIENCE_BUNDLES[RESELLER]


def test_the_money_capabilities_are_in_every_bundle():
    for bundle in caps.EXPERIENCE_BUNDLES.values():
        assert {caps.COMMISSION_EARN, caps.COMMISSION_VIEW, caps.PAYOUT_VIEW} <= set(
            bundle
        )


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        (set(), []),
        (set(caps.EXPERIENCE_BUNDLES[RESELLER]), [RESELLER]),
        # White-label holds the commercial set too; its workspace contains the
        # reseller's, so it is the one workspace.
        (set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL]), [WHITE_LABEL]),
        (set(caps.EXPERIENCE_BUNDLES[AFFILIATE]), [AFFILIATE]),
        (
            set(caps.EXPERIENCE_BUNDLES[RESELLER])
            | set(caps.EXPERIENCE_BUNDLES[AFFILIATE]),
            [RESELLER, AFFILIATE],
        ),
        (
            set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL])
            | set(caps.EXPERIENCE_BUNDLES[AFFILIATE]),
            [WHITE_LABEL, AFFILIATE],
        ),
        ({caps.SUPPORT_ACCESS}, []),
    ],
    ids=[
        "none",
        "reseller",
        "white-label",
        "affiliate",
        "reseller+affiliate",
        "wl+affiliate",
        "support-only",
    ],
)
def test_workspaces_are_a_pure_function_of_the_grants(held, expected):
    assert caps.workspaces(held) == expected


@pytest.mark.parametrize(
    ("stored", "held", "landing"),
    [
        (
            "affiliate",
            set(caps.EXPERIENCE_BUNDLES[RESELLER]) | set(AFFILIATE_CAPS),
            "affiliate",
        ),
        (
            "reseller",
            set(caps.EXPERIENCE_BUNDLES[RESELLER]) | set(AFFILIATE_CAPS),
            "reseller",
        ),
        # A preference for a workspace the grants do not open opens nothing.
        ("reseller", set(AFFILIATE_CAPS), "affiliate"),
        ("white_label", set(AFFILIATE_CAPS), "affiliate"),
        # Unset: the first workspace there is.
        ("", set(caps.EXPERIENCE_BUNDLES[RESELLER]) | set(AFFILIATE_CAPS), "reseller"),
        # Reseller preference after white-label was added: the larger workspace.
        ("reseller", set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL]), "white_label"),
        ("affiliate", set(), None),
        ("nonsense", set(AFFILIATE_CAPS), "affiliate"),
    ],
)
def test_the_landing_only_selects_among_workspaces_the_grants_open(stored, held, landing):
    assert caps.resolve_landing(stored, held) == landing


def test_the_preference_is_a_closed_set_in_the_database_too(partner_factory):
    partner = partner_factory(*AFFILIATE_CAPS)["partner"]
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerAccount.objects.filter(id=partner.id).update(default_experience="admin")
    PartnerAccount.objects.filter(id=partner.id).update(default_experience="affiliate")


def test_a_legacy_partner_with_no_preference_lands_on_what_its_grants_earn(
    partner_factory,
):
    fx = partner_factory(*caps.EXPERIENCE_BUNDLES[RESELLER])
    me = selectors.partners_for_user(fx["user"].id)[0]
    assert me["default_experience"] == "" and me["landing"] == "reseller"


# --- changing it later (the transition service) ------------------------------------------


@pytest.fixture
def affiliate_with_history(partner_factory, staff_factory, organization_factory):
    """An affiliate with a referral code, a captured and a converted referral and
    a ledger row - the things a type change must never lose."""
    from workeasy360.commercial.commissions import ledger

    fx = partner_factory(*AFFILIATE_CAPS, code="aff-history", currency="GBP")
    PartnerAccount.objects.filter(id=fx["partner"].id).update(
        default_experience="affiliate"
    )
    code = referrals.create_referral_code(fx["actor"](), label="LinkedIn")
    token = referrals.capture_referral(code.code)
    org, _membership, owner = organization_factory()
    assert referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )
    referrals.capture_referral(code.code)
    _user, commission_ctx = staff_factory("platform.staff_commission.manage")
    ledger.record_adjustment(
        commission_ctx,
        fx["partner"].id,
        amount_minor=2500,
        currency="GBP",
        reason="History that must survive a change of type",
        idempotency_key="history-1",
    )
    fx["staff"] = staff_factory(
        "platform.staff_partner.manage", "platform.staff_partner.view"
    )[1]
    return fx


def history(partner_id) -> tuple[int, int, int]:
    return (
        ReferralCode.objects.filter(partner_id=partner_id).count(),
        Referral.objects.filter(partner_id=partner_id).count(),
        CommissionAccrual.objects.filter(partner_id=partner_id).count(),
    )


def test_affiliate_to_reseller_adds_the_commercial_set_and_loses_no_history(
    catalog, affiliate_with_history
):
    fx = affiliate_with_history
    before = history(fx["partner"].id)
    assert before == (1, 2, 1)

    result = experience.transition_experience(
        fx["staff"],
        fx["partner"].id,
        to=RESELLER,
        products=["crm"],
        reason="Affiliate became a reseller",
    )
    assert set(result.capabilities_granted) == set(
        caps.EXPERIENCE_BUNDLES[RESELLER]
    ) - set(AFFILIATE_CAPS)
    assert result.products_granted == ["crm"]
    assert result.capabilities_revoked == []

    held = selectors.active_capabilities(fx["partner"].id)
    assert set(AFFILIATE_CAPS) <= held and set(caps.EXPERIENCE_BUNDLES[RESELLER]) <= held
    assert (
        PartnerProductGrant.objects.filter(
            partner_id=fx["partner"].id, status="active"
        ).count()
        == 1
    )
    # The referral codes, the referrals and the ledger are exactly as they were.
    assert history(fx["partner"].id) == before
    me = selectors.partners_for_user(fx["user"].id)[0]
    assert me["landing"] == "reseller"
    assert [w["key"] for w in me["workspaces"]] == ["reseller", "affiliate"]


def test_retiring_the_old_experience_keeps_the_money_capabilities_and_all_history(
    catalog, affiliate_with_history
):
    fx = affiliate_with_history
    before = history(fx["partner"].id)
    result = experience.transition_experience(
        fx["staff"],
        fx["partner"].id,
        to=RESELLER,
        retire=AFFILIATE,
        products=["crm"],
        reason="Moved from affiliate to reseller",
    )
    assert set(result.capabilities_revoked) == {caps.REFERRAL_CREATE, caps.REFERRAL_VIEW}
    held = selectors.active_capabilities(fx["partner"].id)
    # What a partner is owed stays visible, and still earns.
    assert {caps.COMMISSION_EARN, caps.COMMISSION_VIEW, caps.PAYOUT_VIEW} <= held
    assert history(fx["partner"].id) == before
    # A revoked grant is stamped and kept, never deleted.
    revoked = PartnerCapabilityGrant.objects.filter(
        partner_id=fx["partner"].id, capability=caps.REFERRAL_CREATE, status="revoked"
    )
    assert revoked.count() == 1 and revoked.first().revoked_at is not None
    me = selectors.partners_for_user(fx["user"].id)[0]
    assert [w["key"] for w in me["workspaces"]] == ["reseller"]
    assert me["default_experience"] == "reseller"


@pytest.fixture
def manager(staff_factory):
    """Staff who may change a partner's experience."""
    return staff_factory("platform.staff_partner.manage", "platform.staff_partner.view")[
        1
    ]


def workspace_keys(user_id) -> list[str]:
    return [w["key"] for w in selectors.partners_for_user(user_id)[0]["workspaces"]]


def test_white_label_to_reseller_swaps_the_brand_for_the_sub_partner_capability(
    catalog, partner_factory, manager
):
    """White-label contains the Reseller workspace, so a White-label partner has one
    workspace but two experiences; moving it to Reseller must work, not refuse."""
    fx = partner_factory(*caps.EXPERIENCE_BUNDLES[WHITE_LABEL], code="wl-to-rs")
    result = experience.transition_experience(
        manager,
        fx["partner"].id,
        to=RESELLER,
        retire=WHITE_LABEL,
        reason="Became a plain reseller",
    )
    assert result.capabilities_granted == [caps.SUBPARTNER_MANAGE]
    assert set(result.capabilities_revoked) == {caps.BRANDING_MANAGE, caps.DOMAIN_MANAGE}
    assert selectors.active_capabilities(fx["partner"].id) == set(
        caps.EXPERIENCE_BUNDLES[RESELLER]
    )
    assert workspace_keys(fx["user"].id) == ["reseller"]


def test_reseller_to_white_label_adds_the_brand_and_drops_only_the_sub_partner_capability(
    catalog, partner_factory, manager
):
    fx = partner_factory(*caps.EXPERIENCE_BUNDLES[RESELLER], code="rs-to-wl")
    result = experience.transition_experience(
        manager,
        fx["partner"].id,
        to=WHITE_LABEL,
        retire=RESELLER,
        reason="Became a white-label partner",
    )
    assert set(result.capabilities_granted) == {caps.BRANDING_MANAGE, caps.DOMAIN_MANAGE}
    assert result.capabilities_revoked == [caps.SUBPARTNER_MANAGE]
    assert selectors.active_capabilities(fx["partner"].id) == set(
        caps.EXPERIENCE_BUNDLES[WHITE_LABEL]
    )
    assert workspace_keys(fx["user"].id) == ["white_label"]


def test_retiring_white_label_keeps_the_reseller_set_the_partner_also_wears(
    catalog, partner_factory, manager
):
    """A partner created as Reseller, then given White-label and Affiliate, shows
    two workspaces (White-label, Affiliate) but wears three experiences. Retiring
    White-label must withdraw the brand and the domain and nothing the Reseller
    set needs - the customers it serves keep their partner."""
    everything = (
        set(caps.EXPERIENCE_BUNDLES[RESELLER])
        | set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL])
        | set(caps.EXPERIENCE_BUNDLES[AFFILIATE])
    )
    fx = partner_factory(*everything, code="rs-wl-aff")
    assert workspace_keys(fx["user"].id) == ["white_label", "affiliate"]
    result = experience.retire_experience(
        manager, fx["partner"].id, experience=WHITE_LABEL, reason="The brand ended"
    )
    assert set(result.capabilities_revoked) == {caps.BRANDING_MANAGE, caps.DOMAIN_MANAGE}
    held = selectors.active_capabilities(fx["partner"].id)
    assert (
        set(caps.EXPERIENCE_BUNDLES[RESELLER]) | set(caps.EXPERIENCE_BUNDLES[AFFILIATE])
        <= held
    )
    assert not {caps.BRANDING_MANAGE, caps.DOMAIN_MANAGE} & held
    assert workspace_keys(fx["user"].id) == ["reseller", "affiliate"]


def test_retiring_reseller_while_white_label_is_held_withdraws_only_what_white_label_does_not_need(
    catalog, partner_factory, manager
):
    both = set(caps.EXPERIENCE_BUNDLES[RESELLER]) | set(
        caps.EXPERIENCE_BUNDLES[WHITE_LABEL]
    )
    fx = partner_factory(*both, code="rs-wl")
    result = experience.retire_experience(
        manager, fx["partner"].id, experience=RESELLER, reason="No sub-partners any more"
    )
    assert result.capabilities_revoked == [caps.SUBPARTNER_MANAGE]
    assert set(caps.EXPERIENCE_BUNDLES[WHITE_LABEL]) <= selectors.active_capabilities(
        fx["partner"].id
    )


def test_the_last_selling_experience_ending_ends_the_product_grants_and_keeps_the_rows(
    catalog, affiliate_with_history
):
    """Products are sold, not promoted. When no selling experience is left, the
    grants end (stamped, never deleted) and the partner's history is untouched."""
    fx = affiliate_with_history
    experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        products=["crm"],
        reason="Became a reseller for a while",
    )
    before = history(fx["partner"].id)
    result = experience.retire_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        reason="Back to promoting only",
    )
    assert result.products_revoked == ["crm"]
    grants = PartnerProductGrant.objects.filter(partner_id=fx["partner"].id)
    assert grants.count() == 1 and not grants.filter(status="active").exists()
    assert grants.get().revoked_at is not None
    assert history(fx["partner"].id) == before


def test_retiring_the_affiliate_experience_leaves_a_sellers_product_grants_alone(
    catalog, affiliate_with_history
):
    fx = affiliate_with_history
    experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        products=["crm"],
        reason="Became a reseller as well",
    )
    result = experience.retire_experience(
        fx["staff"], fx["partner"].id, experience=AFFILIATE, reason="Stops promoting"
    )
    assert result.products_revoked == []
    assert (
        PartnerProductGrant.objects.filter(
            partner_id=fx["partner"].id, status="active"
        ).count()
        == 1
    )


@pytest.mark.parametrize("products", [5, "crm", {"crm": True}])
def test_products_that_are_not_a_list_are_refused_not_split_or_crashed(
    catalog, affiliate_with_history, products
):
    fx = affiliate_with_history
    with pytest.raises(PartnerValidationError):
        experience.grant_experience(
            fx["staff"],
            fx["partner"].id,
            experience=RESELLER,
            products=products,
            reason="Not a list of codes",
        )


def test_the_last_experience_cannot_be_retired(affiliate_with_history):
    fx = affiliate_with_history
    with pytest.raises(PartnerValidationError):
        experience.retire_experience(
            fx["staff"],
            fx["partner"].id,
            experience=AFFILIATE,
            reason="Nothing left after",
        )


def test_only_a_selling_experience_can_be_given_products(catalog, affiliate_with_history):
    fx = affiliate_with_history
    with pytest.raises(PartnerValidationError):
        experience.grant_experience(
            fx["staff"],
            fx["partner"].id,
            experience=AFFILIATE,
            products=["crm"],
            reason="Promote, not sell",
        )
    assert not PartnerProductGrant.objects.filter(partner_id=fx["partner"].id).exists()


def test_a_transition_is_atomic(catalog, affiliate_with_history):
    fx = affiliate_with_history
    held_before = selectors.active_capabilities(fx["partner"].id)
    with pytest.raises(Exception):
        experience.transition_experience(
            fx["staff"],
            fx["partner"].id,
            to=RESELLER,
            products=["no-such-product"],
            reason="Should roll back",
        )
    assert selectors.active_capabilities(fx["partner"].id) == held_before
    assert (
        PartnerAccount.objects.get(id=fx["partner"].id).default_experience == "affiliate"
    )


def test_a_transition_is_idempotent(catalog, affiliate_with_history):
    fx = affiliate_with_history
    first = experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        reason="Add reseller experience",
    )
    again = experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        reason="Add reseller experience",
    )
    assert first.capabilities_granted and again.capabilities_granted == []


def test_the_default_can_only_be_set_to_a_workspace_the_grants_open(
    affiliate_with_history,
):
    fx = affiliate_with_history
    with pytest.raises(PartnerValidationError):
        experience.set_default_experience(
            fx["staff"], fx["partner"].id, experience=RESELLER
        )
    experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        reason="Add reseller experience",
    )
    experience.set_default_experience(fx["staff"], fx["partner"].id, experience=RESELLER)
    assert (
        PartnerAccount.objects.get(id=fx["partner"].id).default_experience == "reseller"
    )
    with pytest.raises(PartnerValidationError):
        experience.set_default_experience(
            fx["staff"], fx["partner"].id, experience="distributor"
        )


def test_changing_an_experience_is_audited_without_personal_data(affiliate_with_history):
    from tests.phase10.test_10e_owner_activation import audit_of

    fx = affiliate_with_history
    experience.grant_experience(
        fx["staff"],
        fx["partner"].id,
        experience=RESELLER,
        reason="Add reseller experience",
        make_default=True,
    )
    kinds = {row["action"] for row in audit_of(fx["partner"].id, "action")}
    assert {"partner.experience_granted", "partner.default_experience_changed"} <= kinds


def test_a_terminated_partner_cannot_change_experience(affiliate_with_history):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    fx = affiliate_with_history
    services.set_partner_status(
        fx["staff"],
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Ended by test",
    )
    with pytest.raises(PartnerValidationError):
        experience.grant_experience(
            fx["staff"], fx["partner"].id, experience=RESELLER, reason="Too late to add"
        )


# --- over HTTP ------------------------------------------------------------------------------


def experience_url(partner_id) -> str:
    return f"/api/v1/admin/partners/{partner_id}/experience"


def test_the_staff_endpoint_grants_retires_transitions_and_sets_the_landing(
    catalog, admin
):
    pid = send(admin, CREATE, make_body("affiliate", name="Aff Http")).json()["id"]
    url = experience_url(pid)
    granted = send(
        admin,
        url,
        {
            "action": "grant",
            "experience": "reseller",
            "products": ["crm"],
            "reason": "Add the reseller experience",
        },
    )
    assert granted.status_code == 200, granted.content
    assert set(granted.json()["capabilities_granted"])
    default = send(
        admin,
        url,
        {"action": "default", "experience": "reseller", "reason": "Lands on reseller"},
    )
    assert default.json() == {"default_experience": "reseller"}
    retired = send(
        admin,
        url,
        {"action": "retire", "experience": "affiliate", "reason": "Stops promoting"},
    )
    assert retired.status_code == 200
    assert send(admin, url, {"action": "nonsense"}).status_code == 400
    assert (
        send(
            admin,
            url,
            {"action": "grant", "experience": "distributor", "reason": "A reason here"},
        ).status_code
        == 400
    )
    detail = admin.get(f"/api/v1/admin/partners/{pid}").json()
    assert detail["landing"] == "reseller" and [
        w["key"] for w in detail["workspaces"]
    ] == ["reseller"]


def test_the_staff_endpoint_needs_the_partner_grant(catalog, admin, staff_client):
    pid = send(admin, CREATE, make_body("affiliate", name="Aff Http Two")).json()["id"]
    viewer, _u = staff_client("platform.staff_partner.view")
    response = viewer.post(
        experience_url(pid),
        json.dumps(
            {"action": "grant", "experience": "reseller", "reason": "Not allowed"}
        ),
        content_type="application/json",
    )
    assert response.status_code == 403
