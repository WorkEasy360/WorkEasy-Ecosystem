"""Simplified Super Admin partner management (ADR-048).

Add Partner asks only for what the account needs (name, administrator email and
password, type, optional country) and creates a *draft*; everything else is set
in Complete Your Profile, whose completion the server computes. These tests hold
the brief's rules:

- creation is short, atomic and refuses anything beyond its few fields;
- the type is a capability preset, never a stored kind - a partner can later hold
  several, and every filter and requirement follows the grants;
- profile completion is computed on the server, differs per type, never counts an
  optional section, and the SQL filter agrees with the per-partner answer;
- All Partners filters (type, status, profile, name-or-email search) run on the
  server; the dashboard counts are factual;
- staff may give a referral partner its code; the preferred currency changes
  only while nothing is kept in it.
"""

from __future__ import annotations

import pytest

from tests.partner_experience.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    CREATE,
    activate,
    admin_path,
    complete_profile,
    make_body,
    send,
    sign_in,
)
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerMember,
    PartnerProductGrant,
)
from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db

LIST = "/api/v1/admin/partners/resellers"
OVERVIEW = "/api/v1/admin/partners/overview"


def create(admin, experience="reseller", **kw):
    response = send(admin, CREATE, make_body(experience, **kw))
    assert response.status_code == 201, response.content
    return response.json()


def summary(admin, partner_id):
    response = admin.get(admin_path(partner_id, "/summary"))
    assert response.status_code == 200, response.content
    return response.json()


def listed(admin, **query):
    response = admin.get(LIST, {"scope": "all", "limit": 100, **query})
    assert response.status_code == 200, response.content
    return {row["id"]: row for row in response.json()["results"]}


# --- Add Partner ------------------------------------------------------------------------------


def test_add_partner_creates_a_draft_with_the_type_bundle_and_a_typed_login(
    catalog, admin
):
    from workeasy360.commercial.partners.capabilities import EXPERIENCE_BUNDLES

    created = create(admin, "reseller")
    partner = PartnerAccount.objects.get(id=created["id"])
    assert created["status"] == "draft"
    assert partner.status == "draft"
    assert partner.code == "acme-partner"
    assert partner.default_experience == "reseller"
    assert partner.payout_currency == "GBP"  # from the country
    held = set(
        PartnerCapabilityGrant.objects.filter(
            partner=partner, status="active"
        ).values_list("capability", flat=True)
    )
    assert held == set(EXPERIENCE_BUNDLES["reseller"])
    # Nothing beyond the account: no product, no price, no commission yet.
    assert not PartnerProductGrant.objects.filter(partner=partner).exists()
    member = PartnerMember.objects.get(partner=partner)
    assert member.role == "owner"
    user = User.objects.get(email=ADMIN_EMAIL)
    assert member.user_id == user.id
    assert user.check_password(ADMIN_PASSWORD)
    assert ADMIN_PASSWORD not in user.password
    assert "password" not in str(created).lower()


def test_the_country_is_optional_and_sets_the_starting_currency(catalog, admin):
    created = create(admin, "affiliate", country=None)
    partner = PartnerAccount.objects.get(id=created["id"])
    assert partner.payout_currency == "INR"  # the platform default
    profile = admin.get(admin_path(created["id"], "/profile")).json()
    assert profile["business"]["country"] == ""


def test_a_second_partner_with_the_same_name_gets_its_own_code(catalog, admin):
    first = create(admin, "reseller")
    second = create(admin, "affiliate", email="other@acme.example.com")
    codes = set(
        PartnerAccount.objects.filter(id__in=[first["id"], second["id"]]).values_list(
            "code", flat=True
        )
    )
    assert codes == {"acme-partner", "acme-partner-2"}


@pytest.mark.parametrize(
    "extra",
    [
        {"products": ["crm"]},
        {"capabilities": ["customer.provision"]},
        {"status": "active"},
        {"partner": {"display_name": "Acme", "payout_currency": "USD"}},
    ],
    ids=["products", "capabilities", "status", "partner-extra-field"],
)
def test_add_partner_refuses_anything_beyond_its_fields_and_creates_nothing(
    catalog, admin, extra
):
    response = send(admin, CREATE, make_body("reseller", **extra))
    assert response.status_code == 400, response.content
    assert not PartnerAccount.objects.exists()
    assert not User.objects.filter(email=ADMIN_EMAIL).exists()


def test_name_email_password_and_type_are_required(catalog, admin):
    response = send(admin, CREATE, {"partner": {}, "administrator": {}})
    assert response.status_code == 400
    fields = {p["field"] for p in response.json()["problems"]}
    assert {"experience", "partner.display_name", "administrator.email"} <= fields
    assert not PartnerAccount.objects.exists()


def test_an_unknown_country_is_refused(catalog, admin):
    response = send(admin, CREATE, make_body("reseller", country="XX"))
    assert response.status_code == 400
    assert not PartnerAccount.objects.exists()


def test_an_existing_email_is_never_overwritten_and_can_be_linked(
    catalog, admin, user_factory
):
    existing = user_factory(email="taken@example.com")
    existing.set_password("An Old Password 1984")
    existing.save(update_fields=["password"])
    response = send(admin, CREATE, make_body("reseller", email="taken@example.com"))
    assert response.status_code == 400
    assert any(
        p["message"] == "This email already belongs to an existing Workeasy360 account."
        for p in response.json()["problems"]
    )
    existing.refresh_from_db()
    assert existing.check_password("An Old Password 1984")
    assert not PartnerAccount.objects.exists()

    linked = send(
        admin,
        CREATE,
        make_body(
            "reseller", email="taken@example.com", mode="link_existing", password=None
        ),
    )
    assert linked.status_code == 201, linked.content
    existing.refresh_from_db()
    assert existing.check_password("An Old Password 1984")
    assert (
        PartnerMember.objects.get(partner_id=linked.json()["id"]).user_id == existing.id
    )


def test_the_new_partner_cannot_use_the_portal_until_it_is_activated(catalog, admin):
    created = create(admin, "reseller")
    client, login = sign_in(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert login.status_code == 200
    portal = client.get("/api/v1/partner/portal")
    partners = portal.json().get("partners", []) if portal.status_code == 200 else []
    assert all(
        p.get("id") != created["id"] or p.get("status") == "draft" for p in partners
    )
    customers = client.get(f"/api/v1/partner/{created['id']}/customers")
    assert customers.status_code in (403, 404)


# --- profile completion -------------------------------------------------------------------------


def section_states(view):
    return {
        s["key"]: (s["state"], s["required"])
        for s in view["profile_completion"]["sections"]
    }


def test_a_new_reseller_profile_is_incomplete_with_what_it_needs(catalog, admin):
    created = create(admin, "reseller")
    view = summary(admin, created["id"])
    completion = view["profile_completion"]
    assert completion["complete"] is False
    assert completion["setup_status"] == "setup_required"
    states = section_states(view)
    # Business info needs the contact and business email (the country was given).
    assert states["business"] == ("incomplete", True)
    assert states["products"] == ("incomplete", True)
    assert states["commission"][1] is True
    assert states["administrator"] == ("complete", True)
    assert states["capabilities"] == ("complete", True)
    # Optional sections are shown but never required.
    assert states["pricing"] == ("optional", False)
    assert "brand" not in states and "referral" not in states and "domain" not in states
    assert 0 < completion["percent"] < 100
    assert view["activation"]["can_activate"] is False
    assert {m["key"] for m in view["activation"]["missing"]} == {
        m["key"] for m in completion["missing"]
    }


@pytest.mark.parametrize(
    ("experience", "required"),
    [
        (
            "reseller",
            {"business", "capabilities", "products", "commission", "administrator"},
        ),
        (
            "white_label",
            {
                "business",
                "capabilities",
                "products",
                "commission",
                "brand",
                "administrator",
            },
        ),
        (
            "affiliate",
            {"business", "capabilities", "commission", "referral", "administrator"},
        ),
    ],
)
def test_each_type_needs_its_own_sections_and_completes_to_ready(
    catalog, admin, experience, required
):
    created = create(admin, experience)
    view = summary(admin, created["id"])
    assert {k for k, (_s, req) in section_states(view).items() if req} == required
    complete_profile(admin, created["id"])
    view = summary(admin, created["id"])
    completion = view["profile_completion"]
    assert completion["complete"] is True, completion
    assert completion["percent"] == 100
    assert completion["setup_status"] == "ready"
    assert view["activation"] == {"can_activate": True, "missing": []}
    assert activate(admin, created["id"]).status_code == 200
    assert summary(admin, created["id"])["profile_completion"]["setup_status"] == "active"


def test_the_custom_domain_and_prices_never_count(catalog, admin):
    created = create(admin, "white_label")
    complete_profile(admin, created["id"])
    states = section_states(summary(admin, created["id"]))
    assert states["domain"] == ("optional", False)
    assert states["pricing"] == ("optional", False)


def test_a_combined_partner_needs_the_union_of_its_capabilities(catalog, admin):
    created = create(admin, "reseller")
    complete_profile(admin, created["id"])
    assert summary(admin, created["id"])["profile_completion"]["complete"] is True
    # The same account later also becomes an affiliate: one account, two labels.
    response = send(
        admin,
        admin_path(created["id"], "/experience"),
        {
            "action": "grant",
            "experience": "affiliate",
            "reason": "Also refers customers now",
        },
    )
    assert response.status_code == 200, response.content
    view = summary(admin, created["id"])
    labels = {e["key"] for e in view["experience"]}
    assert {"reseller", "affiliate"} <= labels
    assert section_states(view)["referral"] == ("incomplete", True)
    assert view["profile_completion"]["complete"] is False
    complete_profile(
        admin, created["id"], business=False, products=(), commission=False, brand=False
    )
    assert summary(admin, created["id"])["profile_completion"]["complete"] is True


def test_completion_grants_nothing(catalog, admin):
    """A complete profile adds no capability, product or membership."""
    created = create(admin, "affiliate")
    before = set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=created["id"], status="active"
        ).values_list("capability", flat=True)
    )
    complete_profile(admin, created["id"])
    after = set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=created["id"], status="active"
        ).values_list("capability", flat=True)
    )
    assert before == after
    assert not PartnerProductGrant.objects.filter(partner_id=created["id"]).exists()


def test_the_sql_filter_agrees_with_every_partners_own_answer(catalog, admin):
    """Partners in many states: the database's complete/incomplete filter and the
    per-partner evaluation say the same thing about each of them."""
    ids = []
    for i, (experience, steps) in enumerate(
        [
            ("reseller", {}),
            ("reseller", {"commission": False}),
            ("reseller", {"products": ()}),
            ("reseller", None),
            ("white_label", {"brand": False}),
            ("white_label", None),
            ("affiliate", {"referral": False}),
            ("affiliate", {"business": False}),
            ("affiliate", None),
        ]
    ):
        created = create(
            admin, experience, name=f"Matrix {i}", email=f"m{i}@matrix.example.com"
        )
        if steps is None:
            complete_profile(admin, created["id"])
        elif steps:
            complete_profile(admin, created["id"], **steps)
        ids.append(created["id"])
    complete_ids = set(listed(admin, profile="complete"))
    incomplete_ids = set(listed(admin, profile="incomplete"))
    assert complete_ids.isdisjoint(incomplete_ids)
    for partner_id in ids:
        own = summary(admin, partner_id)["profile_completion"]["complete"]
        assert (partner_id in complete_ids) is own
        assert (partner_id in incomplete_ids) is (not own)
    rows = listed(admin)
    for partner_id in ids:
        assert (
            rows[partner_id]["profile_completion"]["complete"]
            is summary(admin, partner_id)["profile_completion"]["complete"]
        )
        assert (
            rows[partner_id]["profile_completion"]["percent"]
            == summary(admin, partner_id)["profile_completion"]["percent"]
        )


# --- All Partners filters -----------------------------------------------------------------------


def test_the_type_filter_follows_the_grants_and_includes_combined_partners(
    catalog, admin
):
    reseller = create(admin, "reseller", name="Only Reseller", email="r@x.example.com")
    wl = create(admin, "white_label", name="Only White", email="w@x.example.com")
    aff = create(admin, "affiliate", name="Only Affiliate", email="a@x.example.com")
    both = create(admin, "reseller", name="Both Kinds", email="b@x.example.com")
    send(
        admin,
        admin_path(both["id"], "/experience"),
        {
            "action": "grant",
            "experience": "affiliate",
            "reason": "Refers customers as well",
        },
    )
    assert set(listed(admin, type="affiliate")) == {aff["id"], both["id"]}
    assert set(listed(admin, type="reseller")) >= {reseller["id"], both["id"]}
    assert aff["id"] not in listed(admin, type="reseller")
    assert set(listed(admin, type="white_label")) == {wl["id"]}
    row = listed(admin)[both["id"]]
    assert {e["key"] for e in row["experience"]} == {"reseller", "affiliate"}


def test_unknown_filters_are_refused(catalog, admin):
    assert admin.get(LIST, {"scope": "all", "type": "platinum"}).status_code == 400
    assert admin.get(LIST, {"scope": "all", "profile": "half"}).status_code == 400
    assert admin.get(LIST, {"scope": "all", "status": "deleted"}).status_code == 400


def test_search_finds_a_partner_by_name_or_administrator_email(catalog, admin):
    one = create(
        admin, "reseller", name="Northwind Traders", email="boss@northwind.example.com"
    )
    two = create(admin, "affiliate", name="Contoso", email="lead@contoso.example.com")
    assert set(listed(admin, q="northwind")) == {one["id"]}
    assert set(listed(admin, q="lead@contoso")) == {two["id"]}
    complete_profile(admin, two["id"])
    assert set(listed(admin, q="hello@partner.example")) == {
        two["id"]
    }  # the business email


def test_status_filter_and_row_shape(catalog, admin):
    draft = create(admin, "reseller", name="Draft One", email="d1@x.example.com")
    live = create(admin, "affiliate", name="Live One", email="l1@x.example.com")
    complete_profile(admin, live["id"])
    assert activate(admin, live["id"]).status_code == 200
    assert set(listed(admin, status="draft")) == {draft["id"]}
    assert set(listed(admin, status="active")) == {live["id"]}
    row = listed(admin)[live["id"]]
    assert row["administrator"]["email"] == "l1@x.example.com"
    assert isinstance(row["version"], int)
    assert set(row["profile_completion"]) == {"percent", "complete", "setup_status"}


# --- the dashboard --------------------------------------------------------------------------------


def test_the_dashboard_counts_are_factual(catalog, admin):
    create(admin, "reseller", name="R One", email="r1@x.example.com")
    wl = create(admin, "white_label", name="W One", email="w1@x.example.com")
    aff = create(admin, "affiliate", name="A One", email="a1@x.example.com")
    complete_profile(admin, aff["id"])
    assert activate(admin, aff["id"]).status_code == 200
    complete_profile(admin, wl["id"])
    assert activate(admin, wl["id"]).status_code == 200
    suspended = send(
        admin,
        admin_path(wl["id"], "/status"),
        {
            "status": "suspended",
            "reason": "Paused for review",
            "expected_version": admin.get(admin_path(wl["id"])).json()["version"],
        },
    )
    assert suspended.status_code == 200, suspended.content
    data = admin.get(OVERVIEW).json()
    cards = {c["key"]: c["count"] for c in data["cards"]}
    assert list(cards) == [
        "total_partners",
        "active_partners",
        "resellers",
        "white_label",
        "affiliates",
        "suspended_partners",
        "profiles_incomplete",
    ]
    assert cards["total_partners"] == 3
    assert cards["active_partners"] == 1
    # The white-label bundle includes selling, so that partner wears both labels.
    assert cards["resellers"] == 2
    assert cards["white_label"] == 1
    assert cards["affiliates"] == 1
    assert cards["suspended_partners"] == 1
    assert cards["profiles_incomplete"] == 1
    assert not any(k in str(data).lower() for k in ("mrr", "arr", "growth"))
    recent = data["recent_partners"]
    assert len(recent) == 3
    assert {"percent", "complete", "setup_status"} == set(recent[0]["profile_completion"])


def test_the_dashboard_and_list_need_the_partner_view_grant(catalog, staff_client):
    client, _user = staff_client()
    assert client.get(OVERVIEW).status_code in (403, 404)
    assert client.get(LIST, {"scope": "all"}).status_code in (403, 404)


# --- staff referral codes --------------------------------------------------------------------------


def test_staff_can_give_an_affiliate_its_referral_code(catalog, admin):
    created = create(admin, "affiliate")
    response = send(
        admin, admin_path(created["id"], "/referral-codes"), {"label": "Spring"}
    )
    assert response.status_code == 201, response.content
    code = response.json()["code"]
    read = admin.get(admin_path(created["id"], "/referrals")).json()
    assert read["codes"][0]["code"] == code
    assert read["codes"][0]["url"].endswith(f"/r/{code}")  # ADR-053
    assert read["attribution_window_days"] > 0
    assert read["can_create"] is True


def test_a_partner_without_the_referral_capability_gets_no_code(catalog, admin):
    created = create(admin, "reseller")
    response = send(
        admin, admin_path(created["id"], "/referral-codes"), {"label": "Spring"}
    )
    assert response.status_code == 400
    assert (
        admin.get(admin_path(created["id"], "/referrals")).json()["can_create"] is False
    )


def test_a_staff_member_without_manage_cannot_mint_a_code(catalog, admin, staff_client):
    created = create(admin, "affiliate")
    viewer, _user = staff_client("platform.staff_partner.view")
    response = send(
        viewer, admin_path(created["id"], "/referral-codes"), {"label": "Spring"}
    )
    assert response.status_code == 403


def test_a_partner_cannot_reach_the_staff_referral_code_command(
    catalog, admin, affiliate
):
    client, partner_id = affiliate
    response = send(client, admin_path(partner_id, "/referral-codes"), {"label": "Mine"})
    assert response.status_code in (403, 404)


# --- the preferred currency --------------------------------------------------------------------------


def change_currency(admin, partner_id, currency):
    import json

    return admin.put(
        admin_path(partner_id, "/profile"),
        json.dumps(
            {
                "expected_version": admin.get(admin_path(partner_id)).json()["version"],
                "payout": {"currency": currency},
            }
        ),
        content_type="application/json",
    )


def test_the_currency_changes_while_draft_and_not_after_activation(catalog, admin):
    created = create(admin, "affiliate")
    response = change_currency(admin, created["id"], "USD")
    assert response.status_code == 200, response.content
    assert PartnerAccount.objects.get(id=created["id"]).payout_currency == "USD"
    complete_profile(admin, created["id"])
    assert activate(admin, created["id"]).status_code == 200
    refused = change_currency(admin, created["id"], "EUR")
    assert refused.status_code == 400
    assert PartnerAccount.objects.get(id=created["id"]).payout_currency == "USD"


def test_the_currency_does_not_change_once_the_partner_has_prices(catalog, admin):
    created = create(admin, "reseller")
    complete_profile(admin, created["id"])
    pricing = admin.get(admin_path(created["id"], "/pricing")).json()
    row = pricing["results"][0]
    priced = send(
        admin,
        admin_path(created["id"], "/prices"),
        {
            "plan_version_id": row["plan_version_id"],
            "interval": row["interval"],
            "amount_minor": 1000,
        },
    )
    assert priced.status_code in (200, 201), priced.content
    refused = change_currency(admin, created["id"], "USD")
    assert refused.status_code == 400
    assert "prices" in str(refused.json())


def test_an_unknown_currency_is_refused(catalog, admin):
    created = create(admin, "reseller")
    assert change_currency(admin, created["id"], "XYZ").status_code == 400


# --- the brief's security checks, against partners made through the new flow ------------------


def test_a_reseller_without_the_domain_grant_cannot_configure_a_domain_or_brand(
    catalog, reseller
):
    client, pid = reseller
    domain = send(
        client, f"/api/v1/partner/{pid}/domains", {"hostname": "app.reseller-check.com"}
    )
    assert domain.status_code in (403, 404), domain.content
    brand = client.put(
        f"/api/v1/partner/{pid}/branding",
        '{"brand_name": "Mine"}',
        content_type="application/json",
    )
    assert brand.status_code in (403, 404), brand.content


def test_partners_cannot_grant_themselves_products_prices_commission_or_codes(
    catalog, reseller, affiliate
):
    client, pid = reseller
    for path, body in (
        (admin_path(pid, "/product-grants"), {"product_code": "books"}),
        (
            admin_path(pid, "/prices"),
            {
                "plan_version_id": "00000000-0000-0000-0000-000000000000",
                "interval": "monthly",
                "amount_minor": 1,
            },
        ),
        (
            admin_path(pid, "/commission"),
            {"rate_bp": 9000, "reason": "Give me more money now", "expected_version": 1},
        ),
        (
            admin_path(pid, "/capabilities"),
            {"capability": "branding.manage", "reason": "Make me white-label please"},
        ),
        (
            admin_path(pid, "/experience"),
            {
                "action": "grant",
                "experience": "white_label",
                "reason": "Make me white-label please",
            },
        ),
        (
            admin_path(pid, "/status"),
            {
                "status": "active",
                "reason": "Reactivate myself please",
                "expected_version": 1,
            },
        ),
    ):
        response = send(client, path, body)
        assert response.status_code in (401, 403, 404), (path, response.status_code)
    assert not PartnerProductGrant.objects.filter(
        partner_id=pid, product__code="books"
    ).exists()
    aff_client, aff_id = affiliate
    # An affiliate reaching for a reseller's data, or its own customers, is refused.
    assert aff_client.get(f"/api/v1/partner/{pid}/customers").status_code in (403, 404)
    assert aff_client.get(f"/api/v1/partner/{aff_id}/customers").status_code in (403, 404)


def test_completion_never_opens_the_portal_for_a_draft(catalog, admin):
    """A complete profile is guidance: until staff activate it, the partner can do nothing."""
    created = create(
        admin, "reseller", name="Ready Draft", email="ready@draft.example.com"
    )
    complete_profile(admin, created["id"])
    assert summary(admin, created["id"])["profile_completion"]["setup_status"] == "ready"
    client, login = sign_in("ready@draft.example.com", ADMIN_PASSWORD)
    assert login.status_code == 200
    assert client.get(f"/api/v1/partner/{created['id']}/customers").status_code in (
        403,
        404,
    )
    assert PartnerAccount.objects.get(id=created["id"]).status == "draft"


def test_a_terminated_partner_is_listed_by_type_but_never_counted_as_incomplete(
    catalog, admin
):
    created = create(admin, "affiliate", name="Gone Co", email="gone@x.example.com")
    response = send(
        admin,
        admin_path(created["id"], "/status"),
        {
            "status": "terminated",
            "reason": "Programme ended for them",
            "expected_version": admin.get(admin_path(created["id"])).json()["version"],
        },
    )
    assert response.status_code == 200, response.content
    assert created["id"] in listed(admin, type="affiliate")
    assert created["id"] not in listed(admin, profile="incomplete")
    cards = {c["key"]: c["count"] for c in admin.get(OVERVIEW).json()["cards"]}
    # Each card is the size of the list it opens.
    assert cards["affiliates"] == len(listed(admin, type="affiliate"))
    assert cards["profiles_incomplete"] == len(listed(admin, profile="incomplete"))
