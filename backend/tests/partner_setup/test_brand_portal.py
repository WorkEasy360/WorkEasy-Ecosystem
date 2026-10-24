"""The brand portal: bounded plain text and flags that customers read, and the
sender identity that emails sent in a partner's name carry.

Nothing here can carry markup, nothing is fetched, nothing reaches an access
decision - and the sender identity never reaches a customer's browser.
"""

from __future__ import annotations

import json

import pytest

from tests.partner_setup.conftest import MANAGE, VIEW, put
from workeasy360.commercial.partners import branding, portal, selectors
from workeasy360.commercial.partners.inputs import Problems
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

PORTAL = {
    "short_name": "Acme",
    "support_name": "Acme Help Desk",
    "support_phone": "+91 20 5555 0100",
    "website_url": "https://acme.example",
    "welcome_message": "Welcome to Acme Cloud. We are glad you are here.",
    "footer_text": "Acme Cloud is operated by Acme Technologies.",
    "sender_name": "Acme Cloud",
    "reply_to_email": "support@acme.example",
    "login_show_welcome": True,
    "login_show_support": False,
    "use_for_invitations": True,
}


@pytest.fixture
def wl(partner_factory):
    return partner_factory("branding.manage", "domain.manage")


def brand_url(partner_id):
    return f"/api/v1/admin/partners/{partner_id}/brand"


def save_brand(client, partner_id, version=None, **body):
    payload = {"brand_name": "Acme Cloud", "accent_color": "#1a56db", **body}
    if version is not None:
        payload["expected_version"] = version
    return put(client, brand_url(partner_id), payload)


# --- validation: the closed set, plain text only -----------------------------------------------------------


def parsed(raw):
    problems = Problems()
    return portal.parse(raw, problems), problems.items


def test_a_valid_portal_is_clean():
    cleaned, problems = parsed(PORTAL)
    assert problems == [] and cleaned == PORTAL


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("welcome_message", "<script>alert(1)</script>"),
        ("welcome_message", "Hello <b>there</b>"),
        ("welcome_message", "x" * 281),
        ("footer_text", "a > b"),
        ("short_name", "S" * 25),
        ("support_name", "line one\nline two"),
        ("sender_name", "Evil <attacker@evil.example>"),
        ("support_phone", "call me maybe"),
        ("website_url", "http://acme.example"),
        ("website_url", "javascript:alert(1)"),
        ("website_url", "https://user:pw@acme.example"),
        ("reply_to_email", "not an address"),
        ("login_show_welcome", "yes"),
        ("use_for_invitations", 1),
    ],
)
def test_every_field_refuses_what_could_become_markup_or_a_fetch(key, value):
    cleaned, problems = parsed({key: value})
    assert problems and problems[0]["field"] == f"portal.{key}"
    assert key not in cleaned


def test_an_unknown_key_is_refused_so_a_typo_does_nothing_silently():
    _cleaned, problems = parsed(
        {"welcome_msg": "hello", "custom_css": "body{}", "script": "x"}
    )
    assert problems and "Unknown portal fields" in problems[0]["message"]


@pytest.mark.parametrize("junk", [[], "x", 5, True])
def test_a_portal_that_is_not_an_object_is_a_problem_not_a_crash(junk):
    _cleaned, problems = parsed(junk)
    assert problems and problems[0]["field"] == "portal"


def test_the_view_fills_every_key_with_a_default():
    full = portal.view({})
    assert set(full) == set(portal.PORTAL_FIELDS)
    assert full["login_show_welcome"] is True and full["use_for_invitations"] is False
    assert full["welcome_message"] == ""


def test_a_customers_browser_never_receives_the_sender_identity():
    public = portal.public_view(PORTAL)
    assert set(public) == set(portal.PUBLIC_FIELDS)
    assert "sender_name" not in public and "reply_to_email" not in public
    assert "use_for_invitations" not in public


# --- saving through the brand command ---------------------------------------------------------------------------


def test_staff_save_a_brand_with_its_portal(wl, manager):
    response = save_brand(manager, wl["partner"].id, portal=PORTAL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["portal"] == PORTAL
    stored = selectors.brand_for(wl["partner"].id)
    assert stored.portal == PORTAL


def test_the_portal_is_merged_so_one_screen_never_wipes_anothers_fields(wl, manager):
    first = save_brand(manager, wl["partner"].id, portal=PORTAL).json()
    second = save_brand(
        manager,
        wl["partner"].id,
        first["version"],
        portal={"welcome_message": "A new welcome."},
    ).json()
    assert second["portal"]["welcome_message"] == "A new welcome."
    assert second["portal"]["sender_name"] == "Acme Cloud"  # untouched
    assert second["portal"]["login_show_support"] is False  # untouched


def test_a_save_without_a_portal_leaves_the_stored_portal_alone(wl, manager):
    first = save_brand(manager, wl["partner"].id, portal=PORTAL).json()
    second = save_brand(
        manager, wl["partner"].id, first["version"], brand_name="Acme Cloud 2"
    ).json()
    assert second["portal"] == PORTAL


def test_an_empty_string_clears_a_portal_field(wl, manager):
    first = save_brand(manager, wl["partner"].id, portal=PORTAL).json()
    second = save_brand(
        manager, wl["partner"].id, first["version"], portal={"footer_text": ""}
    ).json()
    assert second["portal"]["footer_text"] == ""


def test_a_portal_problem_is_a_400_and_nothing_is_saved(wl, manager):
    first = save_brand(manager, wl["partner"].id, portal={"short_name": "Acme"}).json()
    bad = save_brand(
        manager,
        wl["partner"].id,
        first["version"],
        brand_name="Changed",
        portal={"welcome_message": "<img src=x>"},
    )
    assert bad.status_code == 400
    stored = selectors.brand_for(wl["partner"].id)
    assert stored.brand_name == "Acme Cloud" and stored.version == first["version"]


def test_the_audit_lists_portal_field_names_never_their_words(wl, manager):
    from tests.phase11.conftest import audit_of

    save_brand(manager, wl["partner"].id, portal=PORTAL)
    brand = selectors.brand_for(wl["partner"].id)
    entry = next(
        r
        for r in audit_of(brand.id, "action", "changes")
        if r["action"] == "partner.branding_changed"
    )
    assert "welcome_message" in entry["changes"]["portal_fields"]
    assert "glad you are here" not in str(
        entry["changes"]
    ) and "support@acme.example" not in str(entry["changes"])


def test_the_partner_may_also_save_its_own_portal_through_the_same_command(
    partner_client,
):
    client, fx = partner_client("branding.manage", "domain.manage")
    response = client.put(
        f"/api/v1/partner/{fx['partner'].id}/branding",
        json.dumps({"brand_name": "Own Brand", "portal": {"short_name": "Own"}}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["portal"]["short_name"] == "Own"
    refused = client.put(
        f"/api/v1/partner/{fx['partner'].id}/branding",
        json.dumps(
            {
                "brand_name": "Own Brand",
                "portal": {"welcome_message": "<b>x</b>"},
                "expected_version": 1,
            }
        ),
        content_type="application/json",
    )
    assert refused.status_code == 400


# --- what customers receive ---------------------------------------------------------------------------------------


def test_a_customer_of_a_white_label_partner_receives_the_public_portal_only(
    wl, manager, console_org, login
):
    from workeasy360.commercial.partners import services

    save_brand(manager, wl["partner"].id, portal=PORTAL)
    org = console_org()
    services.link_customer(
        wl["staff_ctx"], wl["partner"].id, org["org"].id, reason="A white-label customer"
    )
    response = login(org["owner"]).get(f"/api/v1/orgs/{org['slug']}/brand")
    assert response.status_code == 200
    brand = response.json()["brand"]
    assert brand["brand_name"] == "Acme Cloud"
    assert brand["portal"] == portal.public_view(PORTAL)
    assert (
        "sender_name" not in brand["portal"] and "reply_to_email" not in brand["portal"]
    )
    assert "sender_name" not in json.dumps(brand)


def test_the_host_resolved_brand_carries_the_same_public_portal(wl, manager):
    from tests.phase10.test_10d_white_label import FakeDns
    from workeasy360.commercial.partners import domains

    save_brand(manager, wl["partner"].id, portal=PORTAL)
    ctx = wl["staff_ctx"]
    row = domains.request_domain(
        ctx, hostname="portal.acme-example.com", partner_id=wl["partner"].id
    )
    fake = FakeDns(
        {domains.verification_record_name(row.hostname): [row.verification_token]}
    )
    previous = domains.set_txt_resolver(fake)
    try:
        domains.verify_domain(ctx, domain_id=row.id)
        domains.activate_domain(ctx, domain_id=row.id)
    finally:
        domains.set_txt_resolver(previous)
    brand = selectors.active_brand_for_host("portal.acme-example.com")
    assert brand is not None and brand["portal"] == portal.public_view(PORTAL)


# --- emails sent in the partner's name -----------------------------------------------------------------------------


def make_invite(wl, manager, console_org, **portal_fields):
    from workeasy360.commercial.partners import services

    save_brand(manager, wl["partner"].id, portal=portal_fields)
    return services, console_org()


def test_the_default_is_the_partners_own_name_and_no_envelope(wl):
    identity = selectors.invitation_identity(wl["partner"])
    assert identity == {"partner_name": "Test Partner", "envelope": {}}


def test_a_white_label_partner_that_opts_in_sends_under_its_brand(wl, manager):
    save_brand(manager, wl["partner"].id, portal=PORTAL)
    identity = selectors.invitation_identity(wl["partner"])
    assert identity["partner_name"] == "Acme Cloud"
    assert identity["envelope"] == {
        "sender_name": "Acme Cloud",
        "reply_to": "support@acme.example",
    }


def test_opting_out_or_losing_white_label_returns_to_the_plain_name(wl, manager):
    from workeasy360.commercial.partners import services

    save_brand(manager, wl["partner"].id, portal={**PORTAL, "use_for_invitations": False})
    assert selectors.invitation_identity(wl["partner"])["envelope"] == {}
    brand = selectors.brand_for(wl["partner"].id)
    brand.portal = {**brand.portal, "use_for_invitations": True}
    brand.save(update_fields=["portal"])
    assert selectors.invitation_identity(wl["partner"])["envelope"]
    services.revoke_capability(
        wl["staff_ctx"],
        wl["partner"].id,
        capability="domain.manage",
        reason="Revoked for the test",
    )
    assert selectors.invitation_identity(wl["partner"]) == {
        "partner_name": "Test Partner",
        "envelope": {},
    }


def test_a_delivered_owner_invitation_is_sent_under_the_brand(channel):
    from django.core import mail

    from tests.phase7.support import provision
    from tests.phase9.conftest import deliver_account_mail
    from workeasy360.commercial.partners import services

    ctx, partner = channel["staff_ctx"], channel["partner"]
    services.enable_white_label(ctx, partner.id, reason="Enabled for the test")
    branding.update_brand(
        ctx, partner_id=partner.id, brand_name="Acme Cloud", portal=PORTAL
    )
    mail.outbox.clear()
    provision(channel, name="Brand Customer")
    deliver_account_mail()
    (message,) = [m for m in mail.outbox if "Brand Customer" in m.subject]
    assert "Acme Cloud has set up" in message.body
    assert message.from_email.startswith("Acme Cloud <")
    assert message.reply_to == ["support@acme.example"]


def test_an_owner_invitation_without_the_opt_in_is_sent_as_before(channel):
    from django.core import mail

    from tests.phase7.support import provision
    from tests.phase9.conftest import deliver_account_mail

    mail.outbox.clear()
    provision(channel, name="Plain Customer")
    deliver_account_mail()
    (message,) = [m for m in mail.outbox if "Plain Customer" in m.subject]
    assert "Test Partner has set up" in message.body
    assert (
        "Acme" not in message.from_email
        and "support@acme.example" not in message.reply_to
    )


# --- the envelope can never become a header injection or a spoofed sender --------------------------------------------


@pytest.mark.parametrize(
    "envelope",
    [
        {"sender_name": "Acme\r\nBcc: victim@example.com"},
        {"sender_name": "Acme\nInjected: header"},
        {"sender_name": "Boss <boss@evil.example>"},
        {"sender_name": 'He said "hi"'},
        {"sender_name": "x" * 101},
        {"sender_name": 5},
        {"reply_to": "a@example.com\nBcc: b@example.com"},
        {"reply_to": "not-an-address"},
        {"reply_to": ["a@example.com"]},
    ],
)
def test_a_hostile_envelope_is_dropped_whoever_wrote_it(envelope):
    from workeasy360.platform.notifications.communication import clean_envelope

    assert clean_envelope(envelope) == {}


def test_a_clean_envelope_survives_and_garbage_alongside_it_does_not():
    from workeasy360.platform.notifications.communication import clean_envelope

    assert clean_envelope(
        {
            "sender_name": "Acme Cloud",
            "reply_to": "s@acme.example",
            "from": "ceo@evil.example",
        }
    ) == {
        "sender_name": "Acme Cloud",
        "reply_to": "s@acme.example",
    }
    assert clean_envelope(None) == {} and clean_envelope("x") == {}


def test_delivery_applies_the_envelope_but_never_changes_the_from_address(settings):
    from workeasy360.platform.notifications import communication, templates

    key = "platform.customer.owner_invited"
    context = {
        "organization_name": "Acme Ltd",
        "partner_name": "Acme Cloud",
        "claim_url": "https://app.example.com/x#token=t",
        "expires_on": "12 October 2026",
        "_envelope": {"sender_name": "Acme Cloud", "reply_to": "s@acme.example"},
    }
    rendered = communication.render_for_delivery(key, context)
    assert rendered.sender_name == "Acme Cloud" and rendered.reply_to == "s@acme.example"
    assert (
        "_envelope" not in rendered.body_text
        and "Acme Cloud has set up" in rendered.body_text
    )
    plain = communication.render_for_delivery(
        key, {k: v for k, v in context.items() if k != "_envelope"}
    )
    assert plain.reply_to != "s@acme.example"
    # The rendered message has a display name and a reply-to - and no from address at all:
    # the channel always uses the deployment's own.
    assert not hasattr(rendered, "from_email") and templates.get(key)


def test_the_channel_sends_from_the_deployments_own_address(settings):
    from django.core import mail

    from workeasy360.contracts.ports.notifications import (
        NotificationRequest,
        RenderedMessage,
    )
    from workeasy360.platform.notifications import channels

    settings.DEFAULT_FROM_EMAIL = "noreply@workeasy360.example"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    adapter = channels.get_channel("email")
    request = NotificationRequest(
        organization_id=__import__("uuid").uuid4(),
        template_key="platform.customer.owner_invited",
        recipient_email="owner@example.com",
    )
    adapter.send(
        request,
        RenderedMessage(
            subject="s",
            body_text="b",
            sender_name="Acme Cloud",
            reply_to="s@acme.example",
        ),
    )
    (sent,) = mail.outbox
    assert sent.from_email == "Acme Cloud <noreply@workeasy360.example>"
    assert sent.reply_to == ["s@acme.example"]


# --- partners cannot read each other's, and only staff set the staff-owned parts ---------------------------------------


def test_the_staff_brand_read_carries_the_full_portal_for_staff_only(
    wl, manager, partner_client, staff_client
):
    save_brand(manager, wl["partner"].id, portal=PORTAL)
    staff_read = manager.get(brand_url(wl["partner"].id)).json()["brand"]
    assert staff_read["portal"]["sender_name"] == "Acme Cloud"
    viewer, _u = staff_client(VIEW)
    assert viewer.get(brand_url(wl["partner"].id)).json()["brand"]["portal"][
        "reply_to_email"
    ]
    client, fx = partner_client("branding.manage", "domain.manage")
    assert client.get(brand_url(wl["partner"].id)).status_code == 403
    assert client.get(f"/api/v1/partner/{wl['partner'].id}/branding").status_code in (
        403,
        404,
    )


def test_a_staff_portal_write_needs_manage(wl, staff_client):
    viewer, _u = staff_client(VIEW)
    assert (
        save_brand(viewer, wl["partner"].id, portal={"short_name": "x"}).status_code
        == 403
    )
    manager, _w = staff_client(MANAGE)
    response = save_brand(manager, wl["partner"].id, portal={"short_name": "x"})
    assert response.status_code == 200, response.content


def test_the_service_validates_before_its_first_write(wl):
    with pytest.raises(PartnerValidationError):
        branding.update_brand(
            wl["staff_ctx"],
            partner_id=wl["partner"].id,
            brand_name="Acme",
            portal={"footer_text": "<script>"},
        )
    assert selectors.brand_for(wl["partner"].id) is None
