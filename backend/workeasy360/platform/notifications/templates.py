"""Notification templates.

Templates are **pre-registered with typed variables**, never user-controlled
strings. A user-supplied template string is a server-side template injection
primitive, which in Django reaches arbitrary attribute access
(12-security.md section 7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from workeasy360.contracts.ports.notifications import RenderedMessage


class UnknownTemplate(KeyError):
    """Rendering was requested for a template that is not registered."""


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    subject: str
    body_text: str
    #: Names that must be present in the context. Checked before rendering so
    #: a missing variable is a clear error rather than the literal string
    #: "None" arriving in a customer's inbox.
    required: tuple[str, ...] = ()
    #: Phase 8 extension: variables an override may use that a sender does
    #: not always supply; absent ones render as empty text.
    optional: tuple[str, ...] = ()
    #: What the template is for, shown to the Super Admin editing it.
    description: str = ""
    #: Example values for previews - never real data.
    samples: tuple[tuple[str, str], ...] = ()

    @property
    def variables(self) -> tuple[str, ...]:
        return (*self.required, *self.optional)

    def render(self, context: dict[str, Any]) -> RenderedMessage:
        return self.render_text(self.subject, self.body_text, context)

    def render_text(
        self, subject: str, body_text: str, context: dict[str, Any]
    ) -> RenderedMessage:
        """Render ``subject``/``body_text`` - this template's own wording or a
        validated override of it - with ``context``."""
        missing = [name for name in self.required if name not in context]
        if missing:
            raise ValueError(
                f"Template {self.key!r} is missing context variables: "
                f"{', '.join(missing)}."
            )
        values = {name: "" for name in self.optional}
        values.update({k: v for k, v in context.items() if k in self.variables})
        # str.format, not a template engine: the format string is this file's
        # or an override validated to name only declared variables, and the
        # values are plain data - no expression evaluation, no attribute
        # traversal.
        return RenderedMessage(
            subject=subject.format(**values),
            body_text=body_text.format(**values),
        )


_TEMPLATES: dict[str, Template] = {}


def register(template: Template) -> Template:
    _TEMPLATES[template.key] = template
    return template


def render(key: str, context: dict[str, Any]) -> RenderedMessage:
    template = _TEMPLATES.get(key)
    if template is None:
        raise UnknownTemplate(
            f"No template registered for {key!r}. Templates are declared in "
            f"code, never supplied at call time."
        )
    return template.render(context)


def registered_keys() -> list[str]:
    return sorted(_TEMPLATES)


def get(key: str) -> Template:
    template = _TEMPLATES.get(key)
    if template is None:
        raise UnknownTemplate(f"No template registered for {key!r}.")
    return template


# --- Phase 1 platform templates ------------------------------------------

MEMBER_INVITED: Final = register(
    Template(
        key="platform.member.invited",
        subject="You have been invited to join {organization_name}",
        body_text=(
            "You have been invited to join {organization_name} on Workeasy360.\n\n"
            "Accept the invitation: {invitation_url}\n\n"
            "This invitation expires on {expires_on}."
        ),
        required=("organization_name", "invitation_url", "expires_on"),
        optional=("inviter_name", "role_name"),
        description="Sent when someone is invited to join an organization.",
        samples=(
            ("organization_name", "Acme Ltd"),
            (
                "invitation_url",
                "https://app.example.com/invitations/accept?token=example",
            ),
            ("expires_on", "1 October 2026"),
            ("inviter_name", "Priya Shah"),
            ("role_name", "Administrator"),
        ),
    )
)

ORGANIZATION_SUSPENDED: Final = register(
    Template(
        key="platform.organization.suspended",
        subject="{organization_name} is now read-only",
        body_text=(
            "{organization_name} has been suspended and is now read-only.\n\n"
            "Your data remains available to view and export. Write access "
            "resumes once the outstanding balance is settled."
        ),
        required=("organization_name",),
        description="Sent when an organization is suspended and becomes read-only.",
        samples=(("organization_name", "Acme Ltd"),),
    )
)

ORGANIZATION_REACTIVATED: Final = register(
    Template(
        key="platform.organization.reactivated",
        subject="{organization_name} is active again",
        body_text=(
            "{organization_name} has been reactivated and write access has "
            "been restored. Nothing was deleted while it was suspended."
        ),
        required=("organization_name",),
        description="Sent when a suspended organization is reactivated.",
        samples=(("organization_name", "Acme Ltd"),),
    )
)


# --- Phase 9: self-service account email -----------------------------------
#
# Addressed to a person, not to an organization: queued under the platform
# organization by ``services.notify_account``. The link variables are
# credentials and always travel in the sealed context.

ACCOUNT_VERIFY_EMAIL: Final = register(
    Template(
        key="platform.account.verify_email",
        subject="Confirm your email address for Workeasy360",
        body_text=(
            "Hello {name},\n\n"
            "Confirm your email address to finish creating your Workeasy360 "
            "account: {verify_url}\n\n"
            "You will be asked for the password you chose. The link expires in "
            "{expires_in}.\n\n"
            "If you did not sign up, ignore this email - nothing happens unless "
            "the link is used together with the password."
        ),
        required=("name", "verify_url", "expires_in"),
        description="Sent when someone signs up, to verify their address.",
        samples=(
            ("name", "Priya"),
            ("verify_url", "https://app.example.com/verify-email#token=example"),
            ("expires_in", "24 hours"),
        ),
    )
)

ACCOUNT_SIGNUP_EXISTING: Final = register(
    Template(
        key="platform.account.signup_existing",
        subject="You already have a Workeasy360 account",
        body_text=(
            "Someone tried to create a Workeasy360 account with this email "
            "address, but it already has one.\n\n"
            "Sign in: {sign_in_url}\n"
            "Forgotten your password? Reset it: {reset_url}\n\n"
            "If this was not you, you can ignore this email."
        ),
        required=("sign_in_url", "reset_url"),
        description=(
            "Sent instead of a verification link when a signup uses an address "
            "that already has an account (so signup never reveals which "
            "addresses are registered)."
        ),
        samples=(
            ("sign_in_url", "https://app.example.com/login"),
            ("reset_url", "https://app.example.com/forgot-password"),
        ),
    )
)

ACCOUNT_PASSWORD_RESET: Final = register(
    Template(
        key="platform.account.password_reset",
        subject="Reset your Workeasy360 password",
        body_text=(
            "A password reset was requested for your Workeasy360 account.\n\n"
            "Choose a new password: {reset_url}\n\n"
            "The link expires in {expires_in} and can be used once. If you did "
            "not ask for this, ignore this email - your password is unchanged."
        ),
        required=("reset_url", "expires_in"),
        description="Sent when someone asks to reset a forgotten password.",
        samples=(
            ("reset_url", "https://app.example.com/reset-password#token=example"),
            ("expires_in", "60 minutes"),
        ),
    )
)

# --- Phase 10: reseller onboarding -----------------------------------------
#
# Both carry a one-time link, so the link variable is always sealed (see
# ``notifications.sealing``): it is never stored in the notification row after
# delivery, never logged, and travels in the URL fragment.

PARTNER_MEMBER_INVITED: Final = register(
    Template(
        key="platform.partner.member_invited",
        subject="You have been set up on Workeasy360 for {partner_name}",
        body_text=(
            "Hello,\n\n"
            "Workeasy360 has set you up as {role_name} for {partner_name}.\n\n"
            "Choose your password to sign in: {setup_url}\n\n"
            "The link can be used once and expires in {expires_in}. If you were "
            "not expecting this, ignore this email - nothing changes until the "
            "link is used."
        ),
        required=("partner_name", "role_name", "setup_url", "expires_in"),
        description=(
            "Sent when a person is added to a partner account and has no "
            "password yet: the one-time link that sets it."
        ),
        samples=(
            ("partner_name", "Acme Partners"),
            ("role_name", "Administrator"),
            ("setup_url", "https://app.example.com/reset-password#token=example"),
            ("expires_in", "7 days"),
        ),
    )
)

CUSTOMER_OWNER_INVITED: Final = register(
    Template(
        key="platform.customer.owner_invited",
        subject="{organization_name} is ready for you on Workeasy360",
        body_text=(
            "{partner_name} has set up {organization_name} on Workeasy360 and "
            "named you as its owner.\n\n"
            "Activate your account and take ownership: {claim_url}\n\n"
            "The link can be used once and expires on {expires_on}. If you were "
            "not expecting this, ignore this email - nothing changes until the "
            "link is used."
        ),
        required=("organization_name", "partner_name", "claim_url", "expires_on"),
        description=(
            "Sent to the person a partner names as the owner of a customer "
            "organization it created: the one-time link that activates their "
            "account and makes them the owner."
        ),
        samples=(
            ("organization_name", "Acme Ltd"),
            ("partner_name", "Reseller Co"),
            (
                "claim_url",
                "https://app.example.com/owner-invitations/accept#token=example",
            ),
            ("expires_on", "12 October 2026"),
        ),
    )
)

ACCOUNT_PASSWORD_CHANGED: Final = register(
    Template(
        key="platform.account.password_changed",
        subject="Your Workeasy360 password was reset",
        body_text=(
            "The password for your Workeasy360 account was reset using an "
            "emailed link, and every signed-in session was ended.\n\n"
            "If this was not you, reset your password again now and contact "
            "{support_email}."
        ),
        required=("support_email",),
        description="Sent after a password is reset through an emailed link.",
        samples=(("support_email", "support@example.com"),),
    )
)

PARTNER_SUPPORT_REQUEST: Final = register(
    Template(
        key="platform.partner.support_request",
        subject="[{priority}] {subject} - {partner_name}",
        body_text=(
            "A support request from a partner, sent from its Support page.\n\n"
            "Partner: {partner_name} ({partner_code})\n"
            "From: {requester_email}\n"
            "Priority: {priority}\n"
            "Workspace: {workspace}\n\n"
            "{message}\n\n"
            "Reply to this email to answer the partner."
        ),
        required=(
            "partner_name",
            "partner_code",
            "requester_email",
            "priority",
            "subject",
            "message",
        ),
        optional=("workspace",),
        description=(
            "Sent to the platform's partner-support address when a partner "
            "member sends a request from the partner portal's Support page."
        ),
        samples=(
            ("partner_name", "Reseller Co"),
            ("partner_code", "reseller-co"),
            ("requester_email", "admin@reseller.example.com"),
            ("priority", "normal"),
            ("subject", "Question about a customer's renewal"),
            ("message", "Our customer Acme asked when their plan renews."),
            ("workspace", "reseller"),
        ),
    )
)
