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

    def render(self, context: dict[str, Any]) -> RenderedMessage:
        missing = [name for name in self.required if name not in context]
        if missing:
            raise ValueError(
                f"Template {self.key!r} is missing context variables: "
                f"{', '.join(missing)}."
            )
        # str.format, not a template engine: the format string comes from this
        # file and the values are plain data, so there is no expression
        # evaluation and no attribute traversal.
        return RenderedMessage(
            subject=self.subject.format(**context),
            body_text=self.body_text.format(**context),
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
    )
)
