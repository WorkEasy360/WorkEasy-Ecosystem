"""Which events an organization may subscribe to (Task 5).

"Do not let customers subscribe to events they are unauthorized to observe"
is decided here, once, for both webhook subscriptions and automation
triggers:

- the product must be one the organization is **currently entitled to** -
  an organization that has not bought Books has no business receiving Books
  events, and has none to receive;
- the event type must be **declared** in that product's active manifest - the
  registration contract (``ProductManifest.events``) is what makes an event
  routable at all.

Finer-grained visibility *within* a product (an HR event only some
administrators should see) is not something the platform can know, because
it does not own the product's data model. Two things bound it instead:
subscribing requires ``platform.connection.manage`` - an organization
administrator - and a product decides what it publishes; the
product integration guide tells it to publish identifiers and minimal
detail, never sensitive fields (12-security.md section 11).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.contracts.errors import IntegrationValidationError
from workeasy360.contracts.events.namespaces import EventFilter, parse_event_filter


def subscribable_events(organization_id: UUID) -> list[dict[str, Any]]:
    """The organization's event catalogue, for the subscription UI."""
    return [
        {
            "ref": e.ref,
            "event_type": e.event_type,
            "version": e.version,
            "product_code": e.product_code,
            "description": e.description,
        }
        for e in _declared(organization_id)
    ]


def validate_filter(organization_id: UUID, raw: Any, *, field: str) -> EventFilter:
    """Parse ``raw`` and confirm this organization may observe it."""
    parsed = parse_event_filter(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise IntegrationValidationError(
            "An event filter is either one versioned event type "
            "('product.<code>.<aggregate>.<event>.v1') or one product's "
            "namespace ('product.<code>.*').",
            field=field,
        )
    declared = _declared(organization_id)
    if parsed.kind == "namespace":
        allowed = any(e.product_code == parsed.product_code for e in declared)
    else:
        allowed = any(e.ref == parsed.value for e in declared)
    if not allowed:
        raise IntegrationValidationError(
            "That event is not available to this organization: its product "
            "is not one the organization uses, or does not publish it.",
            field=field,
        )
    return parsed


def _declared(organization_id: UUID) -> list[Any]:
    from workeasy360.platform.ecosystem.selectors import published_events
    from workeasy360.platform.entitlements.services.read import products

    return published_events(str(code) for code in products(organization_id))


def event_catalog(ctx: Any) -> list[dict[str, Any]]:
    """The catalogue as the subscription and rule editors read it.

    Either integration permission may read it: it is what both editors pick
    from, and it discloses only which events the organization's own products
    declare.
    """
    from workeasy360.contracts.errors import PermissionDenied
    from workeasy360.platform.authz import services as authz

    if not (
        authz.check(ctx, "platform.connection.view")
        or authz.check(ctx, "platform.automation.view")
    ):
        raise PermissionDenied(permission="platform.connection.view")
    return subscribable_events(ctx.organization_id)
