"""The password, the existing-account case, authorization and atomicity of Add Partner.

The brief's guarantees, each tried as the thing that would break it:

- a weak, mismatched, common, numeric or address-like password is refused and
  nothing is created;
- an address that already has an account is never overwritten - it can only be
  *linked*, and then keeps its own password;
- a refusal anywhere, including after some writes, leaves no half-made user,
  partner, grant or membership;
- the password is nowhere but the hash: not in a response, a log line, an audit
  row, an outbox event or a notification.
"""

from __future__ import annotations

import logging

import pytest

from tests.partner_experience.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    CHECK,
    CREATE,
    PASSWORD,
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


def nothing_was_created(code: str = "acme-partner", email: str = ADMIN_EMAIL) -> None:
    assert not PartnerAccount.objects.filter(code=code).exists()
    assert not User.objects.filter(email=email).exists()
    assert not PartnerCapabilityGrant.objects.filter(partner__code=code).exists()
    assert not PartnerMember.objects.filter(partner__code=code).exists()
    assert not PartnerProductGrant.objects.filter(partner__code=code).exists()


def fields(response) -> set[str]:
    return {p["field"] for p in response.json()["problems"]}


# --- the password policy ---------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "short1A!",  # shorter than the platform minimum
        "password1234",  # on the common-password list
        "123456789012345",  # digits only
        "pat.owner@acme-partner",  # close to the administrator's own address
    ],
    ids=["too-short", "common", "numeric-only", "like-the-address"],
)
def test_a_weak_password_is_refused_and_nothing_is_created(catalog, admin, password):
    response = send(admin, CREATE, make_body("reseller", password=password))
    assert response.status_code == 400, response.content
    assert "administrator.password" in fields(response)
    nothing_was_created()
    # The refusal never repeats what was typed.
    assert password not in response.content.decode()


def test_a_very_long_password_is_refused_before_any_validator_runs(catalog, admin):
    """The form allows 128 characters; the server holds the same line, so a staff
    request cannot make the similarity and hashing work arbitrarily large."""
    from workeasy360.commercial.controlplane.partner_onboarding import MAX_PASSWORD_LENGTH

    too_long = "Violet Harbor Lantern 2049 " * 6  # 162 characters
    assert len(too_long) > MAX_PASSWORD_LENGTH
    response = send(admin, CREATE, make_body("reseller", password=too_long))
    assert response.status_code == 400, response.content
    assert "administrator.password" in fields(response)
    assert f"at most {MAX_PASSWORD_LENGTH} characters" in response.content.decode()
    assert too_long not in response.content.decode()
    nothing_was_created()
    checked = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": ADMIN_EMAIL,
                "password": too_long,
                "password_confirm": too_long,
            }
        },
    ).json()
    assert any("at most" in p["message"] for p in checked["problems"])
    # The limit itself is accepted.
    edge = ("Violet Harbor Lantern 2049 " * 5)[:MAX_PASSWORD_LENGTH].rstrip() + "x"
    edge = edge[:MAX_PASSWORD_LENGTH]
    assert len(edge) == MAX_PASSWORD_LENGTH
    ok = send(admin, CREATE, make_body("reseller", password=edge))
    assert ok.status_code == 201, ok.content
    client, login = sign_in(ADMIN_EMAIL, edge)
    assert login.status_code == 200


def test_mismatched_passwords_are_refused(catalog, admin):
    response = send(admin, CREATE, make_body("reseller", confirm=ADMIN_PASSWORD + "x"))
    assert response.status_code == 400
    assert "administrator.password_confirm" in fields(response)
    nothing_was_created()


def test_a_missing_password_is_refused_for_a_new_account(catalog, admin):
    response = send(admin, CREATE, make_body("reseller", password=None))
    assert response.status_code == 400
    assert "administrator.password" in fields(response)
    nothing_was_created()


def test_a_password_that_is_not_text_is_refused_not_coerced(catalog, admin):
    body = make_body("reseller")
    body["administrator"]["password"] = 123456789012
    body["administrator"]["password_confirm"] = 123456789012
    response = send(admin, CREATE, body)
    assert response.status_code == 400
    nothing_was_created()


def test_credentials_check_reports_every_password_problem_without_writing(catalog, admin):
    response = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": ADMIN_EMAIL,
                "password": "abc",
                "password_confirm": "abd",
            }
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email_status"] == "available"
    assert data["policy"]["min_length"] >= 12
    assert {p["field"] for p in data["problems"]} >= {
        "administrator.password",
        "administrator.password_confirm",
    }
    assert "abc" not in response.content.decode().replace("abc-", "")
    assert response["Cache-Control"] == "no-store"
    assert not User.objects.filter(email=ADMIN_EMAIL).exists()


def test_credentials_check_accepts_a_good_pair(catalog, admin):
    data = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "password_confirm": ADMIN_PASSWORD,
            }
        },
    ).json()
    assert data["problems"] == [] and data["email_status"] == "available"
    assert ADMIN_PASSWORD not in str(data)


# --- an address that already has an account --------------------------------------------


@pytest.fixture
def existing(user_factory):
    """A Workeasy360 account that already exists, with a password of its own."""
    user = user_factory(email="already.here@customer.example.com")
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    return user


def test_creating_for_an_existing_address_is_refused_and_changes_nothing(
    catalog, admin, existing
):
    before = User.objects.get(pk=existing.pk).password
    response = send(
        admin,
        CREATE,
        make_body("reseller", email=existing.email, password=ADMIN_PASSWORD),
    )
    assert response.status_code == 400
    assert any(
        p["field"] == "administrator.email"
        and p["message"]
        == "This email already belongs to an existing Workeasy360 account."
        for p in response.json()["problems"]
    )
    existing.refresh_from_db()
    assert existing.password == before
    assert existing.check_password(PASSWORD) and not existing.check_password(
        ADMIN_PASSWORD
    )
    assert not PartnerAccount.objects.filter(code="acme-partner").exists()


def test_credentials_check_says_an_address_is_taken(catalog, admin, existing):
    data = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": existing.email.upper(),
                "password": ADMIN_PASSWORD,
                "password_confirm": ADMIN_PASSWORD,
            }
        },
    ).json()
    assert data["email_status"] == "existing_account"
    assert any("already belongs" in p["message"] for p in data["problems"])


def test_a_staff_account_can_neither_be_linked_nor_made_a_partner_administrator(
    catalog, admin, user_factory
):
    """Staff and partner access are separate doors: one identity is not made to
    hold both, and a mistyped address that matches a colleague links nobody."""
    colleague = user_factory(email="colleague@workeasy360.example.com", is_staff=True)
    colleague.set_password(PASSWORD)
    colleague.save(update_fields=["password"])
    checked = send(
        admin,
        CHECK,
        {"administrator": {"email": colleague.email, "mode": "link_existing"}},
    ).json()
    assert checked["email_status"] == "unavailable"
    assert any("staff account" in p["message"] for p in checked["problems"])
    refused = send(
        admin,
        CREATE,
        make_body("reseller", email=colleague.email, mode="link_existing", password=None),
    )
    assert refused.status_code == 400, refused.content
    assert "staff account" in refused.content.decode()
    # Asking to create a new account for the address is the ordinary refusal.
    created = send(admin, CREATE, make_body("reseller", email=colleague.email))
    assert created.status_code == 400
    assert not PartnerAccount.objects.filter(code="acme-partner").exists()
    colleague.refresh_from_db()
    assert colleague.check_password(PASSWORD)


def test_an_existing_account_can_be_linked_and_keeps_its_own_password(
    catalog, admin, existing
):
    response = send(
        admin,
        CREATE,
        make_body("reseller", email=existing.email, mode="link_existing", password=None),
    )
    assert response.status_code == 201, response.content
    assert response.json()["administrator"]["account_created"] is False
    existing.refresh_from_db()
    assert existing.check_password(PASSWORD)
    # They sign in with the password they already had and land in the workspace.
    client, login = sign_in(existing.email, PASSWORD)
    assert login.status_code == 200
    me = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert me["landing"] == "reseller" and me["role"] == "owner"


def test_a_password_is_refused_when_linking_an_existing_account(catalog, admin, existing):
    body = make_body("reseller", email=existing.email, mode="link_existing")
    response = send(admin, CREATE, body)
    assert response.status_code == 400
    assert "administrator.password" in fields(response)
    existing.refresh_from_db()
    assert existing.check_password(PASSWORD)
    assert not PartnerAccount.objects.filter(code="acme-partner").exists()


def test_linking_an_address_with_no_account_is_refused(catalog, admin):
    response = send(
        admin,
        CREATE,
        make_body(
            "reseller",
            mode="link_existing",
            password=None,
            email="nobody@nowhere.example.com",
        ),
    )
    assert response.status_code == 400
    assert "administrator.email" in fields(response)
    nothing_was_created(email="nobody@nowhere.example.com")


def test_a_disabled_account_cannot_be_linked(catalog, admin, existing):
    existing.status = "disabled"
    existing.save(update_fields=["status"])
    response = send(
        admin,
        CREATE,
        make_body("reseller", email=existing.email, mode="link_existing", password=None),
    )
    assert response.status_code == 400
    assert not PartnerAccount.objects.filter(code="acme-partner").exists()


# --- authorization ------------------------------------------------------------------------


def test_a_staff_member_without_the_partner_grant_cannot_create(catalog, staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    response = send(client, CREATE, make_body("reseller"))
    assert response.status_code == 403
    nothing_was_created()


def test_a_partner_user_and_an_anonymous_visitor_cannot_create(
    catalog, partner_client, anonymous
):
    client, _fx = partner_client("customer.provision")
    assert send(client, CREATE, make_body("reseller")).status_code == 403
    assert send(anonymous(), CREATE, make_body("reseller")).status_code in (401, 403)
    nothing_was_created()


def test_the_create_request_is_csrf_protected(catalog, staff_client):
    client, _u = staff_client(
        "platform.staff_partner.manage", "platform.staff_partner.view", enforce_csrf=True
    )
    response = send(client, CREATE, make_body("reseller"))
    assert response.status_code == 403
    nothing_was_created()


def test_the_experience_must_be_one_of_the_three(catalog, admin):
    for bad in ("", "distributor", None, ["reseller"]):
        body = make_body("reseller")
        body["experience"] = bad
        response = send(admin, CREATE, body)
        assert response.status_code == 400, (bad, response.content)
        assert "experience" in fields(response)
    nothing_was_created()


def test_a_browser_cannot_name_the_capabilities_it_is_granted(catalog, admin):
    """The bundle comes from the server's preset: a body that names capabilities
    is refused whole, and the preset alone is what a partner gets."""
    body = make_body("affiliate", name="Aff Sneaky")
    body["capabilities"] = ["customer.provision", "subscription.manage"]
    response = send(admin, CREATE, body)
    assert response.status_code == 400
    nothing_was_created(code="aff-sneaky")
    response = send(admin, CREATE, make_body("affiliate", name="Aff Sneaky"))
    assert response.status_code == 201
    granted = set(response.json()["capabilities"])
    assert "customer.provision" not in granted and "subscription.manage" not in granted


# --- atomicity -----------------------------------------------------------------------------


def test_a_failure_at_the_last_step_leaves_no_half_made_partner(
    catalog, admin, monkeypatch
):
    from workeasy360.commercial.partners import services

    def explode(*args, **kwargs):
        raise RuntimeError("the membership step failed")

    monkeypatch.setattr(services, "attach_user_as_member", explode)
    admin.raise_request_exception = False
    response = send(admin, CREATE, make_body("reseller"))
    assert response.status_code >= 500
    nothing_was_created()


def test_a_failure_while_creating_the_account_leaves_no_partner(
    catalog, admin, monkeypatch
):
    from workeasy360.platform.identity import lifecycle

    def explode(**kwargs):
        raise RuntimeError("the account could not be written")

    monkeypatch.setattr(lifecycle, "create_account", explode)
    admin.raise_request_exception = False
    response = send(admin, CREATE, make_body("reseller"))
    assert response.status_code >= 500
    nothing_was_created()


# --- the password reaches nothing but the hash ------------------------------------------------


def test_a_failure_while_creating_shows_no_password_in_an_error_report(
    catalog, admin, monkeypatch
):
    """Django's error report prints each frame's local variables. The entry points
    mark theirs sensitive, so a crash deep inside the command (after the password
    was read and hashed) still prints no password."""
    from django.views.debug import ExceptionReporter

    from workeasy360.commercial.partners import services

    def explode(*args, **kwargs):
        raise RuntimeError("the membership step failed")

    monkeypatch.setattr(services, "attach_user_as_member", explode)
    with pytest.raises(RuntimeError) as info:
        send(admin, CREATE, make_body("reseller"))
    report = ExceptionReporter(None, info.type, info.value, info.tb).get_traceback_text()
    assert ADMIN_PASSWORD not in report
    assert "********************" in report  # the frames were cleansed, not just absent
    nothing_was_created()


def test_a_typed_password_is_not_in_the_repr_of_what_carries_it():
    from workeasy360.commercial.controlplane import partner_onboarding as onboarding

    typed = onboarding._Admin(
        email="pat@example.com",
        typed_email="pat@example.com",
        first_name="Pat",
        last_name="Owner",
        mode="create",
        password=ADMIN_PASSWORD,
        confirm=ADMIN_PASSWORD,
    )
    plan = onboarding._Plan(
        experience="reseller",
        display_name="Acme",
        country="",
        admin=typed,
        account="new",
        user_id=None,
        problems=[],
    )
    for subject in (typed, plan):
        assert ADMIN_PASSWORD not in repr(subject)
        assert ADMIN_PASSWORD not in str(subject)


def test_the_password_is_in_no_log_audit_event_or_notification(catalog, admin, caplog):
    from workeasy360.integration.events.models import OutboxEvent
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.notifications.models import Notification

    caplog.set_level(logging.DEBUG)
    response = send(admin, CREATE, make_body("reseller"))
    assert response.status_code == 201
    haystack = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [str(getattr(r, "__dict__", "")) for r in caplog.records]
    )
    assert ADMIN_PASSWORD not in haystack

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        audits = str(list(AuditEvent.objects.values()))
        notices = str(list(Notification.objects.values()))
    outbox = str(list(OutboxEvent.objects.values()))
    for name, text in (("audit", audits), ("notifications", notices), ("outbox", outbox)):
        assert ADMIN_PASSWORD not in text, name
    # And the typed address is not in the audit trail either: counts and kinds only.
    assert ADMIN_EMAIL not in audits
    assert "partner.onboarded" in audits


def test_no_invitation_or_password_email_is_sent_for_a_typed_password(catalog, admin):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.notifications.models import Notification
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    assert send(admin, CREATE, make_body("reseller")).status_code == 201
    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        keys = [
            str(n) for n in Notification.objects.values_list("template_key", flat=True)
        ]
    assert not any("member_invited" in k or "password" in k for k in keys), keys
