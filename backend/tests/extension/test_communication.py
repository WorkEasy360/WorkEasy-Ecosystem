"""Communication: template overrides and sender settings (Phase 8 extension,
section 4). The override is the one place a person writes text the platform
then formats - so the validator is tested like a security control."""

from __future__ import annotations

import re

import pytest
from django.core import mail

from tests.extension.conftest import url

pytestmark = pytest.mark.django_db

TEMPLATES = "/api/v1/admin/communication/templates"
SENDER = "/api/v1/admin/communication/sender"
INVITED = "platform.member.invited"


def _template():
    from workeasy360.platform.notifications import templates

    return templates.get(INVITED)


@pytest.mark.parametrize(
    "body",
    [
        "{organization_name.__class__} {invitation_url} {expires_on}",
        "{organization_name[0]} {invitation_url} {expires_on}",
        "{organization_name!r} {invitation_url} {expires_on}",
        "{organization_name:>20} {invitation_url} {expires_on}",
        "{} {invitation_url} {expires_on}",
        "{secret_key} {invitation_url} {expires_on} {organization_name}",
        "{organization_name} {invitation_url} {expires_on",
    ],
)
def test_overrides_can_only_name_declared_variables(body):
    from workeasy360.contracts.errors import CommunicationValidationError
    from workeasy360.platform.notifications.communication import validate_override

    with pytest.raises(CommunicationValidationError):
        validate_override(_template(), "Join {organization_name}", body)


def test_overrides_must_keep_required_variables_and_a_one_line_subject():
    from workeasy360.contracts.errors import CommunicationValidationError
    from workeasy360.platform.notifications.communication import validate_override

    with pytest.raises(CommunicationValidationError) as missing:
        validate_override(_template(), "Hello", "Come and join {organization_name}")
    assert "invitation_url" in missing.value.message
    with pytest.raises(CommunicationValidationError):
        validate_override(
            _template(),
            "Hi\nBcc: victim@example.com",
            "{invitation_url} {expires_on} {organization_name}",
        )
    subject, body = validate_override(
        _template(),
        "  Join {organization_name}  ",
        "Hi! {inviter_name} invited you: {invitation_url} (until {expires_on}). {{literal}}",
    )
    assert subject == "Join {organization_name}"
    rendered = _template().render_text(
        subject,
        body,
        {
            "organization_name": "Acme",
            "invitation_url": "https://x",
            "expires_on": "1 Oct",
        },
    )
    assert rendered.body_text == "Hi!  invited you: https://x (until 1 Oct). {literal}"


def test_viewing_and_editing_are_separate(staff_login):
    viewer, _ = staff_login("platform.staff_communication.view")
    assert viewer.get(TEMPLATES).status_code == 200
    body = {
        "subject": "Join {organization_name}",
        "body_text": "{invitation_url} {expires_on}",
        "version": None,
    }
    assert (
        viewer.put(
            f"{TEMPLATES}/{INVITED}", body, content_type="application/json"
        ).status_code
        == 403
    )

    editor, _ = staff_login("platform.staff_communication.manage")
    saved = editor.put(f"{TEMPLATES}/{INVITED}", body, content_type="application/json")
    assert saved.status_code == 200, saved.content

    nobody, _ = staff_login("platform.staff_audit.view")
    assert nobody.get(TEMPLATES).status_code == 403


def test_an_override_is_what_customers_receive(staff_login, console_org, login):
    editor, _ = staff_login("platform.staff_communication.manage")
    saved = editor.put(
        f"{TEMPLATES}/{INVITED}",
        {
            "subject": "{inviter_name} wants you in {organization_name}",
            "body_text": "Click {invitation_url} before {expires_on}.",
            "version": None,
        },
        content_type="application/json",
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["override"]["version"] == 1
    stale = editor.put(
        f"{TEMPLATES}/{INVITED}",
        {
            "subject": "x {organization_name}",
            "body_text": "{invitation_url} {expires_on}",
            "version": None,
        },
        content_type="application/json",
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "concurrent_modification")
    sender = editor.put(
        SENDER,
        {"sender_name": "Acme Workspace", "reply_to": "help@acme.example", "version": 0},
        content_type="application/json",
    )
    assert sender.status_code == 200 and sender.json()["version"] == 1

    org = console_org()
    login(org["owner"]).post(
        url(org["slug"], "invitations"),
        {"email": "guest@example.com"},
        content_type="application/json",
    )
    from tests.extension.test_invitations import _deliver_all

    (message,) = _deliver_all(org["org"].id)
    assert message.subject == f"Olivia Owner wants you in {org['org'].display_name}"
    assert re.search(r"Click https?://\S+#token=\S+ before ", message.body)
    assert message.from_email.startswith(
        '"Acme Workspace" <'
    ) or message.from_email.startswith("Acme Workspace <")
    assert message.reply_to == ["help@acme.example"]


def test_the_sender_cannot_change_the_sending_address(staff_login, settings):
    editor, _ = staff_login("platform.staff_communication.manage")
    for bad in (
        {"sender_name": "Evil <ceo@bank.example>", "reply_to": ""},
        {"sender_name": "Two\nLines", "reply_to": ""},
        {"sender_name": "Fine", "reply_to": "not-an-email"},
    ):
        response = editor.put(
            SENDER, {**bad, "version": 0}, content_type="application/json"
        )
        assert response.status_code == 400, bad
    assert editor.get(SENDER).json()["from_address"] == settings.DEFAULT_FROM_EMAIL


def test_preview_renders_unsaved_wording_with_samples(staff_login):
    viewer, _ = staff_login("platform.staff_communication.view")
    response = viewer.post(
        f"{TEMPLATES}/{INVITED}/preview",
        {
            "subject": "Welcome to {organization_name}",
            "body_text": "{invitation_url} {expires_on}",
        },
        content_type="application/json",
    )
    assert response.json() == {
        "subject": "Welcome to Acme Ltd",
        "body_text": "https://app.example.com/invitations/accept?token=example 1 October 2026",
    }
    bad = viewer.post(
        f"{TEMPLATES}/{INVITED}/preview",
        {"subject": "x", "body_text": "{organization_name.__init__}"},
        content_type="application/json",
    )
    assert bad.status_code == 400


def test_a_test_email_goes_only_to_the_caller(staff_login, settings):
    viewer, user = staff_login("platform.staff_communication.view")
    response = viewer.post(
        f"{TEMPLATES}/{INVITED}/test",
        {"to": "someone-else@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 202 and response.json()["to"] == user.email


def test_reset_restores_the_default_and_a_broken_override_falls_back(staff_login):
    from workeasy360.platform.notifications.communication import render_for_delivery
    from workeasy360.platform.notifications.models import TemplateOverride

    editor, _ = staff_login("platform.staff_communication.manage")
    editor.put(
        f"{TEMPLATES}/{INVITED}",
        {
            "subject": "S {organization_name}",
            "body_text": "{invitation_url} {expires_on}",
            "version": None,
        },
        content_type="application/json",
    )
    reset = editor.delete(
        f"{TEMPLATES}/{INVITED}", {"version": 1}, content_type="application/json"
    )
    assert reset.status_code == 200 and reset.json()["override"] is None

    # An override that no longer renders (a later deploy dropped a variable)
    # must not stop the email: the code's wording is sent instead.
    TemplateOverride.objects.create(
        template_key=INVITED, subject="{gone}", body_text="{gone}"
    )
    rendered = render_for_delivery(
        INVITED,
        {"organization_name": "A", "invitation_url": "https://x", "expires_on": "d"},
    )
    assert rendered.subject == "You have been invited to join A"


def test_template_changes_are_audited_without_their_text(staff_login):
    editor, _ = staff_login(
        "platform.staff_communication.manage", "platform.staff_audit.view"
    )
    editor.put(
        f"{TEMPLATES}/{INVITED}",
        {
            "subject": "Secret-looking {organization_name}",
            "body_text": "{invitation_url} {expires_on}",
            "version": None,
        },
        content_type="application/json",
    )
    audit = editor.get("/api/v1/admin/audit", {"action": "communication."}).json()[
        "results"
    ]
    assert audit[0]["action"] == "communication.template_overridden"
    assert "Secret-looking" not in str(audit[0]["changes"])
    assert mail.outbox == []
