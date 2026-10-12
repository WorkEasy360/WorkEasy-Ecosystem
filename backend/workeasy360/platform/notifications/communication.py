"""Communication: what the platform's emails say, and who they say they are from.

The Super Admin's Communication page (Phase 8 extension). Templates stay
declared in code (``templates.py``) with a fixed set of variables; a Super
Admin may override a template's **subject and body text** and set the sender's
display name and reply-to address. Nothing here can introduce a template, a
variable, HTML, or a sending domain.

**Why an override is not a template-injection primitive.** It is rendered with
``str.format`` after :func:`validate_override` has parsed it with
``string.Formatter`` and refused every replacement field that is not exactly
one of the template's declared variable names - no attribute access
(``{x.__class__}``), no indexing (``{x[0]}``), no conversion (``!r``), no format
spec, no positional fields. What remains can only substitute plain values.

Authorization: ``platform.staff_communication.view`` to read and preview,
``.manage`` (dangerous: AAL 2, audited) to change anything.
"""

from __future__ import annotations

import dataclasses
import string
from typing import Any, Final

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from workeasy360.contracts.errors import CommunicationValidationError
from workeasy360.contracts.ports.notifications import RenderedMessage
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.notifications import templates
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

VIEW: Final = "platform.staff_communication.view"
MANAGE: Final = "platform.staff_communication.manage"

MAX_SUBJECT: Final = 200
MAX_BODY: Final = 10_000
MAX_SENDER_NAME: Final = 100


class ConcurrentEdit(CommunicationValidationError):
    code = "concurrent_modification"
    http_status = 409


# --- delivery-time rendering (no authorization: the worker, not a person) -----


def render_for_delivery(key: str, context: dict[str, Any]) -> RenderedMessage:
    """The message to send: the override's wording when there is one, else the
    template's own, with the platform's sender settings.

    An override that no longer renders (a later deploy removed a variable it
    used) falls back to the code's wording rather than failing the delivery:
    a customer still gets the email, and the failure is logged for staff.
    """
    from workeasy360.platform.notifications.models import (
        CommunicationSettings,
        TemplateOverride,
    )

    template = templates.get(key)
    override = TemplateOverride.objects.filter(template_key=key).first()
    rendered = None
    if override is not None:
        try:
            rendered = template.render_text(override.subject, override.body_text, context)
        except (KeyError, IndexError, ValueError):
            logger.error("notifications.override.render_failed", template=key)
    if rendered is None:
        rendered = template.render(context)
    sender = CommunicationSettings.objects.filter(id=1).first()
    if sender is not None:
        rendered = dataclasses.replace(
            rendered, sender_name=sender.sender_name, reply_to=sender.reply_to
        )
    return _with_envelope(rendered, context)


#: The context key a publisher may carry a per-message sender identity under.
ENVELOPE_KEY: Final = "_envelope"


def clean_envelope(raw: Any) -> dict[str, str]:
    """The display name and reply-to a publisher may attach to one message, or
    ``{}``. **Never an address the message is sent from**: the From address is
    always the deployment's own (``channels.py``); only the name shown beside it
    and where a reply goes can differ. Anything that is not a single line of
    text / a valid address is dropped, whoever wrote it - so a notification row
    edited by hand cannot make a mail header out of a newline."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    name = raw.get("sender_name")
    if (
        isinstance(name, str)
        and name.strip()
        and len(name.strip()) <= MAX_SENDER_NAME
        # One printable line with no address-list punctuation: control
        # characters (a newline would start another header), angle brackets and
        # a double quote (they end a display name and begin an address).
        and not any(ch in '<>"' for ch in name)
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
    ):
        out["sender_name"] = name.strip()
    reply = raw.get("reply_to")
    if isinstance(reply, str) and reply.strip() and len(reply.strip()) <= 254:
        try:
            validate_email(reply.strip())
        except ValidationError:
            pass
        else:
            out["reply_to"] = reply.strip()
    return out


def _with_envelope(rendered: RenderedMessage, context: dict[str, Any]) -> RenderedMessage:
    envelope = clean_envelope(context.get(ENVELOPE_KEY))
    if not envelope:
        return rendered
    return dataclasses.replace(rendered, **envelope)


# --- validation ----------------------------------------------------------------


def validate_override(
    template: templates.Template, subject: Any, body: Any
) -> tuple[str, str]:
    """Return the cleaned ``(subject, body)`` or raise, naming the field."""
    if not isinstance(subject, str) or not subject.strip():
        raise CommunicationValidationError("A subject is required.", field="subject")
    if not isinstance(body, str) or not body.strip():
        raise CommunicationValidationError("A body is required.", field="body_text")
    subject = subject.strip()
    body = body.replace("\r\n", "\n").strip()
    if len(subject) > MAX_SUBJECT:
        raise CommunicationValidationError(
            f"The subject may be at most {MAX_SUBJECT} characters.", field="subject"
        )
    if len(body) > MAX_BODY:
        raise CommunicationValidationError(
            f"The body may be at most {MAX_BODY:,} characters.", field="body_text"
        )
    # A line break in a subject is an email-header injection attempt, not
    # wording (Django would refuse it at send time; refusing it here says why).
    if any(ch in subject for ch in "\r\n\x00"):
        raise CommunicationValidationError(
            "The subject must be a single line.", field="subject"
        )
    used = _fields(subject, "subject", template) | _fields(body, "body_text", template)
    missing = [name for name in template.required if name not in used]
    if missing:
        raise CommunicationValidationError(
            "The wording must still include "
            + ", ".join("{" + name + "}" for name in missing)
            + ".",
            field="body_text",
            missing=missing,
        )
    return subject, body


def _fields(text: str, field: str, template: templates.Template) -> set[str]:
    allowed = set(template.variables)
    found: set[str] = set()
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise CommunicationValidationError(
            "Unbalanced braces. Write {{ or }} for a literal brace.", field=field
        ) from exc
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if name not in allowed or spec or conversion:
            raise CommunicationValidationError(
                "Only these placeholders may be used: "
                + ", ".join("{" + v + "}" for v in template.variables)
                + ".",
                field=field,
            )
        found.add(name)
    return found


# --- staff services ----------------------------------------------------------------


def _describe(template: templates.Template, override: Any) -> dict[str, Any]:
    return {
        "key": template.key,
        "description": template.description,
        "variables": list(template.variables),
        "required": list(template.required),
        "default_subject": template.subject,
        "default_body_text": template.body_text,
        "override": None
        if override is None
        else {
            "subject": override.subject,
            "body_text": override.body_text,
            "version": override.version,
            "updated_at": override.updated_at.isoformat(),
        },
    }


@in_scope
def list_templates(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.platform.notifications.models import TemplateOverride

    authz.require(ctx, VIEW)
    overrides = {o.template_key: o for o in TemplateOverride.objects.all()}
    return [
        _describe(templates.get(key), overrides.get(key))
        for key in templates.registered_keys()
    ]


def _template_or_invalid(key: str) -> templates.Template:
    try:
        return templates.get(key)
    except templates.UnknownTemplate as exc:
        raise CommunicationValidationError("No such template.", field="key") from exc


@in_scope
def set_override(
    ctx: TenantContext,
    *,
    key: str,
    subject: Any,
    body_text: Any,
    expected_version: int | None,
) -> dict[str, Any]:
    """Create or replace an override. ``expected_version`` is the version the
    editor loaded (``None`` when there was no override yet)."""
    from workeasy360.platform.notifications.models import TemplateOverride

    authz.require(ctx, MANAGE)
    template = _template_or_invalid(key)
    subject, body_text = validate_override(template, subject, body_text)
    with transaction.atomic():
        row = (
            TemplateOverride.objects.select_for_update().filter(template_key=key).first()
        )
        current = row.version if row is not None else None
        if current != expected_version:
            raise ConcurrentEdit(
                "Someone else changed this template. Reload it and try again.",
                field="version",
            )
        if row is None:
            try:
                with transaction.atomic():
                    row = TemplateOverride.objects.create(
                        template_key=key,
                        subject=subject,
                        body_text=body_text,
                        updated_by_id=ctx.user_id,
                    )
            except IntegrityError as exc:
                raise ConcurrentEdit(
                    "Someone else changed this template. Reload it and try again.",
                    field="version",
                ) from exc
        else:
            row.subject = subject
            row.body_text = body_text
            row.version += 1
            row.updated_by_id = ctx.user_id
            row.save(
                update_fields=[
                    "subject",
                    "body_text",
                    "version",
                    "updated_by_id",
                    "updated_at",
                ]
            )
        audit.record(
            ctx,
            "communication.template_overridden",
            resource_type="notifications.template",
            resource_id=key,
            changes={
                "version": row.version,
                "subject_length": len(subject),
                "body_length": len(body_text),
            },
        )
    return _describe(template, row)


@in_scope
def reset_override(
    ctx: TenantContext, *, key: str, expected_version: int
) -> dict[str, Any]:
    from workeasy360.platform.notifications.models import TemplateOverride

    authz.require(ctx, MANAGE)
    template = _template_or_invalid(key)
    with transaction.atomic():
        row = (
            TemplateOverride.objects.select_for_update().filter(template_key=key).first()
        )
        if row is None:
            return _describe(template, None)
        if row.version != expected_version:
            raise ConcurrentEdit(
                "Someone else changed this template. Reload it and try again.",
                field="version",
            )
        row.delete()
        audit.record(
            ctx,
            "communication.template_reset",
            resource_type="notifications.template",
            resource_id=key,
        )
    return _describe(template, None)


@in_scope
def preview(
    ctx: TenantContext, *, key: str, subject: Any = None, body_text: Any = None
) -> dict[str, str]:
    """Render with the template's sample values - the saved wording, or the
    unsaved wording passed in (validated exactly as saving would)."""
    from workeasy360.platform.notifications.models import TemplateOverride

    authz.require(ctx, VIEW)
    template = _template_or_invalid(key)
    samples = dict(template.samples)
    if subject is not None or body_text is not None:
        subject, body_text = validate_override(template, subject, body_text)
        rendered = template.render_text(subject, body_text, samples)
    else:
        override = TemplateOverride.objects.filter(template_key=key).first()
        rendered = (
            template.render_text(override.subject, override.body_text, samples)
            if override is not None
            else template.render(samples)
        )
    return {"subject": rendered.subject, "body_text": rendered.body_text}


@in_scope
def send_test(ctx: TenantContext, *, key: str, recipient_email: str) -> None:
    """Queue the saved wording, with sample values, to the caller's own
    address - never to an address the caller types."""
    from workeasy360.contracts.ports.notifications import NotificationRequest
    from workeasy360.platform.notifications.services import notify

    authz.require(ctx, VIEW)
    template = _template_or_invalid(key)
    notify(
        NotificationRequest(
            organization_id=ctx.organization_id,
            template_key=template.key,
            recipient_email=recipient_email,
            context=dict(template.samples),
        )
    )
    audit.record(
        ctx,
        "communication.test_sent",
        resource_type="notifications.template",
        resource_id=key,
    )


@in_scope
def sender_settings(ctx: TenantContext) -> dict[str, Any]:
    from django.conf import settings

    from workeasy360.platform.notifications.models import CommunicationSettings

    authz.require(ctx, VIEW)
    row = CommunicationSettings.objects.filter(id=1).first()
    return {
        "sender_name": row.sender_name if row else "",
        "reply_to": row.reply_to if row else "",
        "from_address": settings.DEFAULT_FROM_EMAIL,
        "version": row.version if row else 0,
    }


@in_scope
def update_sender_settings(
    ctx: TenantContext, *, sender_name: Any, reply_to: Any, expected_version: int
) -> dict[str, Any]:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from workeasy360.platform.notifications.models import CommunicationSettings

    authz.require(ctx, MANAGE)
    name = str(sender_name or "").strip()
    address = str(reply_to or "").strip()
    if len(name) > MAX_SENDER_NAME or any(ch in name for ch in '\r\n\x00<>"'):
        raise CommunicationValidationError(
            f"The sender name must be one line of at most {MAX_SENDER_NAME} "
            "characters, without < > or quotes.",
            field="sender_name",
        )
    if address:
        try:
            validate_email(address)
        except ValidationError as exc:
            raise CommunicationValidationError(
                "The reply-to address is not a valid email address.", field="reply_to"
            ) from exc
    with transaction.atomic():
        row = CommunicationSettings.objects.select_for_update().filter(id=1).first()
        # Version 0 means "never saved" - the value ``sender_settings`` reports
        # before the row exists.
        current = row.version if row is not None else 0
        if current != expected_version:
            raise ConcurrentEdit(
                "Someone else changed the sender settings. Reload and try again.",
                field="version",
            )
        if row is None:
            row = CommunicationSettings(id=1)
        before = {"sender_name": row.sender_name, "reply_to": row.reply_to}
        row.sender_name = name
        row.reply_to = address
        row.version = current + 1
        row.updated_by_id = ctx.user_id
        try:
            # Savepoint: two first-ever saves race to insert the one row; the
            # loser is told to reload, not handed a 500.
            with transaction.atomic():
                row.save()
        except IntegrityError as exc:
            raise ConcurrentEdit(
                "Someone else changed the sender settings. Reload and try again.",
                field="version",
            ) from exc
        audit.record(
            ctx,
            "communication.sender_updated",
            resource_type="notifications.communication_settings",
            resource_id="1",
            changes={
                "before": before,
                "after": {"sender_name": name, "reply_to": address},
            },
        )
    return sender_settings(ctx)
