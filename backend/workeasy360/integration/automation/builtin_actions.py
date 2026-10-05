"""The two platform actions Phase 6 ships (Task 35).

Both are platform/integration capabilities, never product-domain ones:

- ``connector.invoke`` - invoke a declared capability of one of the
  organization's connections (today: deliver the triggering event through
  a signed webhook). This is "send webhook" and "invoke registered
  connector" as one action, because a webhook *is* a connector.
- ``notification.send`` - notify named members that the rule ran. The
  message names the rule and the event type and carries **no event data**:
  an email is a plaintext channel the platform does not control.

Nothing here creates a CRM deal, posts a journal or moves stock. A product
that wants to be *acted on* exposes that through its own versioned contract,
and a future action for it would be registered - by a module allowed to
know that contract - exactly as these two are.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from workeasy360.contracts.errors import IntegrationValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.automation.actions import (
    DONE,
    FAILED,
    ActionInvocation,
    ActionResult,
    ActionSpec,
    ConfigField,
    register_action,
)

MAX_NOTIFY_RECIPIENTS: Final = 10
NOTIFICATION_TEMPLATE: Final = "integration.automation.triggered"


# --- connector.invoke -------------------------------------------------------------


def _validate_invoke(ctx: TenantContext, raw: Mapping[str, Any]) -> dict[str, Any]:
    from workeasy360.integration.connectors.registry import get_connector
    from workeasy360.integration.connectors.selectors import runtime_connection

    _only(raw, {"connection_id", "capability"})
    try:
        connection_id = UUID(str(raw.get("connection_id")))
    except ValueError:
        raise IntegrationValidationError(
            "connection_id must be a connection's id.", field="config.connection_id"
        ) from None
    connection = runtime_connection(connection_id)
    if connection is None or connection.is_disabled:
        # Foreign, unknown and disabled are one answer (Task 59).
        raise IntegrationValidationError(
            "No such connection.", field="config.connection_id"
        )
    connector = get_connector(connection.connector_key)
    capability = raw.get("capability") or "deliver_event"
    if connector is None or capability not in connector.capabilities:
        raise IntegrationValidationError(
            "That connection has no such capability.", field="config.capability"
        )
    return {"connection_id": str(connection_id), "capability": capability}


def _execute_invoke(invocation: ActionInvocation) -> ActionResult:
    from workeasy360.integration.connectors.selectors import runtime_connection
    from workeasy360.integration.webhooks.services import create_action_delivery

    connection_id = UUID(str(invocation.config["connection_id"]))
    connection = runtime_connection(connection_id)
    if connection is None or not connection.is_active:
        return ActionResult(FAILED, detail="connection_unavailable")
    delivery_id, _created = create_action_delivery(
        invocation.event,
        connection_id=connection_id,
        capability=str(invocation.config["capability"]),
        execution_id=invocation.execution_id,
        rule_id=invocation.rule_id,
        action_key=invocation.action_key,
    )
    return ActionResult(DONE, reference=f"delivery:{delivery_id}")


CONNECTOR_INVOKE: Final = ActionSpec(
    key="connector.invoke",
    version=1,
    label="Send to a connection",
    description=(
        "Deliver the triggering event through one of this organization's "
        "connections (for a webhook: a signed HTTPS POST)."
    ),
    validate=_validate_invoke,
    execute=_execute_invoke,
    config_fields=(
        ConfigField("connection_id", "connection", "Connection"),
        ConfigField("capability", "string", "Capability", required=False),
    ),
)


# --- notification.send --------------------------------------------------------------


def _validate_notify(ctx: TenantContext, raw: Mapping[str, Any]) -> dict[str, Any]:
    from workeasy360.platform.organizations.selectors import active_membership_exists

    _only(raw, {"membership_ids"})
    ids = raw.get("membership_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_NOTIFY_RECIPIENTS:
        raise IntegrationValidationError(
            f"membership_ids must list 1 to {MAX_NOTIFY_RECIPIENTS} members.",
            field="config.membership_ids",
        )
    normalized: list[str] = []
    for raw_id in ids:
        try:
            membership_id = UUID(str(raw_id))
        except ValueError:
            raise IntegrationValidationError(
                "membership_ids must be member ids.", field="config.membership_ids"
            ) from None
        if not active_membership_exists(ctx, membership_id):
            raise IntegrationValidationError(
                "Every recipient must be an active member of this organization.",
                field="config.membership_ids",
            )
        if str(membership_id) not in normalized:
            normalized.append(str(membership_id))
    return {"membership_ids": normalized}


def _execute_notify(invocation: ActionInvocation) -> ActionResult:
    from workeasy360.contracts.enums import NotificationChannel
    from workeasy360.contracts.ports.notifications import NotificationRequest
    from workeasy360.platform.notifications.services import notify
    from workeasy360.platform.organizations.selectors import active_membership_exists
    from workeasy360.platform.tenancy.context import current_tenant

    ctx = current_tenant()
    sent = 0
    for raw_id in invocation.config["membership_ids"]:
        membership_id = UUID(str(raw_id))
        # Re-checked at run time, not trusted from save time: a member who
        # left since the rule was written is not notified (Task 55).
        if not active_membership_exists(ctx, membership_id):
            continue
        notify(
            NotificationRequest(
                organization_id=invocation.organization_id,
                template_key=NOTIFICATION_TEMPLATE,
                recipient_membership_id=membership_id,
                channels=(NotificationChannel.EMAIL,),
                context={
                    "rule_name": invocation.rule_name,
                    "event_type": invocation.event.ref,
                },
                # Stable across execution retries: the notification service's
                # own unique constraint makes a retry a no-op, not a second
                # email (Task 37).
                idempotency_key=(
                    f"automation:{invocation.execution_id}:"
                    f"{invocation.action_key}:{membership_id}"
                ),
            )
        )
        sent += 1
    return ActionResult(DONE, reference=f"notifications:{sent}")


NOTIFICATION_SEND: Final = ActionSpec(
    key="notification.send",
    version=1,
    label="Notify members",
    description=(
        "Email chosen members that the automation ran. The message carries "
        "the rule name and event type only - never event data."
    ),
    validate=_validate_notify,
    execute=_execute_notify,
    config_fields=(ConfigField("membership_ids", "members", "Recipients"),),
)


def register_builtin_actions() -> None:
    register_action(CONNECTOR_INVOKE)
    register_action(NOTIFICATION_SEND)


def register_notification_template() -> None:
    from workeasy360.platform.notifications.templates import Template, register

    register(
        Template(
            key=NOTIFICATION_TEMPLATE,
            subject='Automation "{rule_name}" ran',
            body_text=(
                'The automation "{rule_name}" ran for a {event_type} event.\n\n'
                "This message intentionally contains no event data. Open "
                "Workeasy360 to see the execution."
            ),
            required=("rule_name", "event_type"),
        )
    )


def _only(raw: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(raw, Mapping):
        raise IntegrationValidationError("config must be an object.", field="config")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise IntegrationValidationError(
            f"Unknown config field(s): {', '.join(unknown)}.", field="config"
        )
