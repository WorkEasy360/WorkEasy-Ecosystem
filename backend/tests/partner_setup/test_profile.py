"""A partner's business profile and contacts: parse, save, read, and what they
must never do (change money, leak a value into the audit trail, cross partners)."""

from __future__ import annotations

import pytest

from tests.partner_setup.conftest import MANAGE, VIEW, patch, profile_url, put
from workeasy360.commercial.partners import profile, selectors, services
from workeasy360.commercial.partners.models import PartnerContact, PartnerProfile
from workeasy360.contracts.errors import (
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db

FULL = {
    "business": {
        "display_name": "Acme Technologies",
        "legal_name": "Acme Technologies Pvt Ltd",
        "business_email": "hello@acme.example",
        "website": "https://acme.example",
        "phone": "+91 98765 43210",
        "alternative_email": "alt@acme.example",
        "registration_number": "U12345MH2020PTC123456",
        "tax_number": "27AAAAA0000A1Z5",
        "country": "IN",
        "address": {
            "line1": "1 MG Road",
            "line2": "Floor 4",
            "city": "Pune",
            "region": "Maharashtra",
            "postal_code": "411001",
        },
    },
    "contacts": {
        "primary": {
            "name": "Priya Shah",
            "job_title": "Director",
            "email": "priya@acme.example",
            "phone": "+91 98765 43211",
        },
        "billing": {"name": "Bala", "email": "bala@acme.example"},
        "technical": {"same_as_primary": True},
        "support": None,
    },
    "support": {
        "email": "help@acme.example",
        "phone": "+91 20 5555 0100",
        "url": "https://acme.example/help",
    },
    "payout": {"frequency": "monthly", "contact_email": "pay@acme.example"},
    "internal_notes": "Met at the Pune summit.\nPrefers quarterly reviews.",
}


def save(client, partner, body, version=None):
    version = partner.version if version is None else version
    return put(client, profile_url(partner.id), {**body, "expected_version": version})


# --- parsing: everything wrong is found at once, by the server's own field names ---------------


def test_every_problem_is_reported_at_once_under_dotted_field_names():
    parsed = profile.parse(
        {
            "business": {
                "business_email": "nope",
                "website": "http://insecure.example",
                "phone": "call me",
                "country": "Atlantis",
                "tax_number": "<script>",
                "address": {"postal_code": "!!"},
            },
            "contacts": {"billing": {"email": "x", "phone": "1"}},
            "support": {"url": "javascript:alert(1)"},
            "payout": {"frequency": "daily", "contact_email": "bad"},
            "internal_notes": "bad\x00note",
        }
    )
    fields = {p["field"] for p in parsed.problems.items}
    assert {
        "business.business_email",
        "business.website",
        "business.phone",
        "business.country",
        "business.tax_number",
        "business.address.postal_code",
        "contacts.billing.email",
        "contacts.billing.phone",
        "support.url",
        "payout.frequency",
        "payout.contact_email",
        "internal_notes",
    } <= fields


def test_messages_are_written_for_people_not_for_developers():
    parsed = profile.parse(
        {"business": {"business_email": "x", "website": "ftp://x.example"}}
    )
    messages = {p["field"]: p["message"] for p in parsed.problems.items}
    assert messages["business.business_email"] == "Enter a valid email address."
    assert messages["business.website"] == "Enter a plain https:// address."


def test_a_valid_profile_parses_clean_and_reports_what_it_would_change():
    parsed = profile.parse(FULL)
    assert parsed.problems.items == []
    assert parsed.business["country"] == "IN"
    assert parsed.contacts["technical"] is None and parsed.contacts["support"] is None
    assert "business.website" in parsed.sections and "internal_notes" in parsed.sections


def test_only_what_is_sent_is_returned():
    parsed = profile.parse({"support": {"email": "a@example.com"}})
    assert parsed.sections == ["support.email"]
    assert parsed.business == {} and parsed.contacts == {} and parsed.payout == {}


@pytest.mark.parametrize(
    "body",
    [
        {"business": {"code": "stolen"}},
        {"business": {"payout_currency": "USD"}},
        {"payout": {"currency": "XYZ"}},
        {"contacts": {"owner": {"name": "x"}}},
        {"contacts": {"billing": {"is_admin": True}}},
        {"status": "active"},
        {"business": {"address": {"country": "IN"}}},
    ],
)
def test_an_unknown_key_is_refused_never_ignored(body):
    assert profile.parse(body).problems.items, body


def test_the_primary_contact_cannot_be_the_same_as_itself():
    parsed = profile.parse({"contacts": {"primary": {"same_as_primary": True}}})
    assert [p["field"] for p in parsed.problems.items] == ["contacts.primary"]


@pytest.mark.parametrize("junk", [[], "text", 7, True, {"x": []}])
def test_garbage_is_a_problem_never_an_exception(junk):
    for body in (
        junk,
        {"business": junk},
        {"contacts": junk},
        {"support": junk},
        {"payout": junk},
    ):
        result = profile.parse(body)
        assert isinstance(result.problems.items, list)


def test_a_country_must_be_an_iso_code_and_tax_numbers_are_not_format_checked():
    assert profile.parse({"business": {"country": "in"}}).business["country"] == "IN"
    assert profile.parse({"business": {"country": "ZZ"}}).problems.items
    # No country's tax format is enforced: only what cannot be a reference at all is refused.
    for ok in ("27AAAAA0000A1Z5", "GB 123 4567 89", "12-3456789", "DE.123/456"):
        assert profile.parse({"business": {"tax_number": ok}}).problems.items == []


# --- saving ---------------------------------------------------------------------------------------


def test_a_manager_saves_and_reads_the_whole_profile(manager, draft_partner):
    response = save(manager, draft_partner, FULL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["business"]["display_name"] == "Acme Technologies"
    assert body["business"]["country"] == "IN"
    assert body["business"]["address"]["city"] == "Pune"
    assert body["contacts"]["primary"]["email"] == "priya@acme.example"
    assert body["contacts"]["billing"] == {
        "same_as_primary": False,
        "name": "Bala",
        "job_title": "",
        "email": "bala@acme.example",
        "phone": "",
    }
    assert body["contacts"]["technical"] == {"same_as_primary": True}
    assert body["support"]["url"] == "https://acme.example/help"
    assert body["payout"]["frequency"] == "monthly"
    assert body["payout"]["currency"] == "GBP"
    assert body["internal_notes"].startswith("Met at the Pune summit")
    assert body["version"] == draft_partner.version + 1
    again = manager.get(profile_url(draft_partner.id)).json()
    assert again == body


def test_the_primary_contact_is_the_accounts_contact(manager, draft_partner):
    save(manager, draft_partner, FULL)
    row = selectors.partner_row(draft_partner.id)
    assert (row.contact_name, row.contact_email) == ("Priya Shah", "priya@acme.example")
    assert row.display_name == "Acme Technologies"
    assert row.legal_name == "Acme Technologies Pvt Ltd"


def test_changing_the_account_contact_changes_the_contact_row_too(draft_partner, console):
    ctx = console["staff_ctx"]
    services.update_partner(
        ctx,
        draft_partner.id,
        expected_version=0,
        contact_name="Old",
        contact_email="old@example.com",
    )
    profile.update_profile(
        ctx,
        draft_partner.id,
        expected_version=1,
        data={"contacts": {"primary": {"name": "A", "email": "a@example.com"}}},
    )
    services.update_partner(
        ctx,
        draft_partner.id,
        expected_version=2,
        contact_name="B",
        contact_email="b@example.com",
    )
    row = PartnerContact.objects.get(partner_id=draft_partner.id, kind="primary")
    assert (row.name, row.email) == ("B", "b@example.com")


def test_a_partner_with_no_profile_reads_as_empty_with_its_own_contact(
    manager, draft_partner, console
):
    services.update_partner(
        console["staff_ctx"],
        draft_partner.id,
        expected_version=0,
        contact_name="Legacy",
        contact_email="legacy@example.com",
    )
    body = manager.get(profile_url(draft_partner.id)).json()
    assert body["business"]["website"] == "" and body["business"]["address"]["city"] == ""
    assert body["contacts"]["primary"]["name"] == "Legacy"
    assert body["contacts"]["billing"] == {"same_as_primary": True}
    assert not PartnerProfile.objects.filter(partner_id=draft_partner.id).exists()


def test_same_as_primary_stores_nothing_and_unsets_an_earlier_contact(
    manager, draft_partner
):
    save(
        manager,
        draft_partner,
        {"contacts": {"billing": {"name": "Bala", "email": "b@example.com"}}},
    )
    assert PartnerContact.objects.filter(
        partner_id=draft_partner.id, kind="billing"
    ).exists()
    version = selectors.partner_row(draft_partner.id).version
    save(
        manager,
        draft_partner,
        {"contacts": {"billing": {"same_as_primary": True}}},
        version,
    )
    assert not PartnerContact.objects.filter(
        partner_id=draft_partner.id, kind="billing"
    ).exists()


def test_a_section_not_sent_is_left_alone(manager, draft_partner):
    save(manager, draft_partner, FULL)
    version = selectors.partner_row(draft_partner.id).version
    response = save(
        manager, draft_partner, {"support": {"email": "new@acme.example"}}, version
    )
    body = response.json()
    assert body["support"]["email"] == "new@acme.example"
    assert body["support"]["url"] == "https://acme.example/help"  # untouched
    assert body["business"]["website"] == "https://acme.example"
    assert body["contacts"]["billing"]["email"] == "bala@acme.example"
    assert body["internal_notes"].startswith("Met at the Pune summit")


def test_an_empty_string_clears_a_field(manager, draft_partner):
    save(manager, draft_partner, FULL)
    version = selectors.partner_row(draft_partner.id).version
    body = save(manager, draft_partner, {"business": {"website": ""}}, version).json()
    assert body["business"]["website"] == ""


def test_nothing_to_change_is_a_400(manager, draft_partner):
    response = save(manager, draft_partner, {})
    assert response.status_code == 400


def test_a_problem_is_a_400_naming_the_field_and_writes_nothing(manager, draft_partner):
    response = save(
        manager,
        draft_partner,
        {"business": {"display_name": "Renamed", "website": "http://no"}},
    )
    assert response.status_code == 400
    assert selectors.partner_row(draft_partner.id).display_name == "Draft Co"
    assert not PartnerProfile.objects.filter(partner_id=draft_partner.id).exists()


def test_a_stale_version_is_a_409_and_nothing_is_overwritten(manager, draft_partner):
    first = save(manager, draft_partner, {"business": {"phone": "+44 20 7946 0000"}})
    assert first.status_code == 200
    stale = save(manager, draft_partner, {"business": {"phone": "+44 20 7946 9999"}}, 0)
    assert stale.status_code == 409
    assert (
        manager.get(profile_url(draft_partner.id)).json()["business"]["phone"]
        == "+44 20 7946 0000"
    )


def test_the_version_is_required(manager, draft_partner):
    response = put(
        manager, profile_url(draft_partner.id), {"support": {"email": "a@example.com"}}
    )
    assert response.status_code == 400


def test_an_unknown_partner_is_a_404(manager):
    import uuid

    assert manager.get(profile_url(uuid.uuid4())).status_code == 404
    response = put(
        manager,
        profile_url(uuid.uuid4()),
        {"support": {"email": "a@example.com"}, "expected_version": 0},
    )
    assert response.status_code == 404


def test_the_code_cannot_be_changed_here_and_the_currency_only_while_draft(
    manager, draft_partner, console
):
    for body in ({"code": "other"}, {"business": {"code": "other"}}):
        assert save(manager, draft_partner, body).status_code == 400
    row = selectors.partner_row(draft_partner.id)
    assert (row.payout_currency, row.code) == ("GBP", "draft-console")
    # ADR-048: a draft's preferred currency can still be chosen (it has no
    # prices, ledger or payouts yet); an active partner's is fixed.
    assert (
        save(manager, draft_partner, {"payout": {"currency": "USD"}}).status_code == 200
    )
    assert selectors.partner_row(draft_partner.id).payout_currency == "USD"
    active = console["reseller"]["partner"]
    before = selectors.partner_row(active.id).payout_currency
    other = "EUR" if before != "EUR" else "USD"
    current = selectors.partner_row(active.id).version
    assert (
        save(
            manager, active, {"payout": {"currency": other}}, version=current
        ).status_code
        == 400
    )
    assert selectors.partner_row(active.id).payout_currency == before


# --- what editing a profile must never touch ---------------------------------------------------------


def test_editing_the_profile_leaves_sales_commission_and_payouts_exactly_as_they_were(
    manager, console
):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    partner = console["reseller"]["partner"]
    before = list(
        CommissionAccrual.objects.filter(partner_id=partner.id).values_list(
            "id", "amount_minor", "currency", "rate_bp", "basis_minor", "rule_version"
        )
    )
    assert before, "the fixture must have a commission entry"
    response = save(manager, partner, FULL)
    assert response.status_code == 200
    after = list(
        CommissionAccrual.objects.filter(partner_id=partner.id).values_list(
            "id", "amount_minor", "currency", "rate_bp", "basis_minor", "rule_version"
        )
    )
    assert after == before
    assert selectors.partner_row(partner.id).payout_currency == "GBP"


def test_the_audit_names_the_fields_and_never_their_values(manager, draft_partner):
    from tests.phase11.conftest import audit_of

    save(manager, draft_partner, FULL)
    rows = audit_of(draft_partner.id, "action", "changes")
    entry = next(r for r in rows if r["action"] == "partner.profile_updated")
    flattened = str(entry["changes"])
    assert "business.tax_number" in flattened and "contacts.primary" in flattened
    for secret in (
        "27AAAAA0000A1Z5",
        "priya@acme.example",
        "Priya",
        "Pune summit",
        "+91 98765",
    ):
        assert secret not in flattened, secret


# --- who may ---------------------------------------------------------------------------------------


def test_a_viewer_reads_but_cannot_write(staff_client, draft_partner):
    client, _u = staff_client(VIEW)
    assert client.get(profile_url(draft_partner.id)).status_code == 200
    assert (
        save(client, draft_partner, {"support": {"email": "a@example.com"}}).status_code
        == 403
    )


def test_no_grant_at_all_cannot_even_read(staff_client, draft_partner):
    client, _u = staff_client("platform.staff_audit.view")
    assert client.get(profile_url(draft_partner.id)).status_code == 403


def test_the_service_is_authorized_in_the_service(staff_factory, draft_partner):
    _v, viewer = staff_factory(VIEW)
    with pytest.raises(PermissionDenied):
        profile.update_profile(
            viewer,
            draft_partner.id,
            expected_version=0,
            data={"support": {"email": "a@example.com"}},
        )
    with pytest.raises(PartnerValidationError):
        profile.update_profile(
            staff_factory(MANAGE)[1],
            draft_partner.id,
            expected_version=0,
            data={"support": {"email": "not-an-email"}},
        )


def test_a_partner_session_has_no_route_to_any_profile(partner_client, console):
    client, fx = partner_client("customer.provision", "subscription.sell")
    own = profile_url(fx["partner"].id)
    other = profile_url(console["reseller"]["partner"].id)
    for url in (own, other):
        assert client.get(url).status_code == 403
        assert (
            put(
                client,
                url,
                {"support": {"email": "a@example.com"}, "expected_version": 0},
            ).status_code
            == 403
        )


def test_a_signed_out_visitor_gets_nothing(anonymous, draft_partner):
    assert anonymous().get(profile_url(draft_partner.id)).status_code in (401, 403)


def test_the_profile_is_never_a_partner_delete_or_a_post(manager, draft_partner):
    assert manager.delete(profile_url(draft_partner.id)).status_code == 405
    assert (
        manager.post(
            profile_url(draft_partner.id), "{}", content_type="application/json"
        ).status_code
        == 405
    )
    assert patch(manager, profile_url(draft_partner.id), {}).status_code == 405


# --- a contact is merged field by field, like the rest of the profile ------------------------------------


def test_sending_one_contact_field_changes_only_that_field(manager, draft_partner):
    save(manager, draft_partner, FULL)
    version = selectors.partner_row(draft_partner.id).version
    body = save(
        manager,
        draft_partner,
        {"contacts": {"billing": {"phone": "+44 20 7946 1111"}}},
        version,
    ).json()
    assert body["contacts"]["billing"]["phone"] == "+44 20 7946 1111"
    assert body["contacts"]["billing"]["name"] == "Bala"
    assert body["contacts"]["billing"]["email"] == "bala@acme.example"


def test_a_phone_for_the_primary_contact_never_blanks_the_accounts_name_and_email(
    manager, draft_partner, console
):
    services.update_partner(
        console["staff_ctx"],
        draft_partner.id,
        expected_version=0,
        contact_name="Legacy",
        contact_email="legacy@example.com",
    )
    body = save(
        manager,
        draft_partner,
        {"contacts": {"primary": {"phone": "+44 20 7946 2222"}}},
        1,
    ).json()
    assert body["contacts"]["primary"]["name"] == "Legacy"
    assert body["contacts"]["primary"]["email"] == "legacy@example.com"
    assert body["contacts"]["primary"]["phone"] == "+44 20 7946 2222"
    row = selectors.partner_row(draft_partner.id)
    assert (row.contact_name, row.contact_email) == ("Legacy", "legacy@example.com")


def test_an_empty_string_clears_one_contact_field_and_the_mirror_follows(
    manager, draft_partner
):
    save(manager, draft_partner, FULL)
    version = selectors.partner_row(draft_partner.id).version
    body = save(
        manager,
        draft_partner,
        {"contacts": {"primary": {"job_title": "", "name": "Priya S."}}},
        version,
    ).json()
    assert body["contacts"]["primary"]["job_title"] == ""
    row = selectors.partner_row(draft_partner.id)
    assert row.contact_name == "Priya S." and row.contact_email == "priya@acme.example"
