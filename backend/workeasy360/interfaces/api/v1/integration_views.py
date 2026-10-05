"""HTTP adapters for an organization's own integrations (Phase 6).

Connections, webhook subscriptions, deliveries and automation rules - the
customer control plane of ``integration/``. Thin, as every view here is:
parse, call one service or selector, shape the response (F-7). Every
decision - authorization, validation, D-016 write ordering - lives in the
service, where the task path and any internal caller get it too.

Everything is under the tenant path (``/orgs/<slug>/...``), so the tenancy
middleware has already resolved the caller's organization from a verified
membership; no organization id is read from a body or a query string.
Session-authenticated and CSRF-protected like every other tenant write.

Two responses carry a secret - creating a connection and rotating its
secret - and both are ``never_cache``: the value exists in exactly one HTTP
response, ever, and must not be stored by anything on the way (Task 82).
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import IntegrationValidationError
from workeasy360.contracts.values import json_input

# --- catalogues ----------------------------------------------------------------


@require_http_methods(["GET"])
def connectors_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """The connector types an administrator may configure."""
    from workeasy360.integration.connectors.permissions import CONNECTION_VIEW
    from workeasy360.integration.connectors.registry import catalog
    from workeasy360.platform.authz import services as authz

    authz.require(request.tenant_context, CONNECTION_VIEW)  # type: ignore[attr-defined]
    return JsonResponse({"results": catalog()})


@require_http_methods(["GET"])
def event_catalog_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    """The events this organization may subscribe to or trigger on."""
    from workeasy360.integration.events.catalog import event_catalog

    return JsonResponse({"results": event_catalog(request.tenant_context)})  # type: ignore[attr-defined]


@require_http_methods(["GET"])
def automation_actions_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.integration.automation.actions import catalog
    from workeasy360.integration.automation.permissions import AUTOMATION_VIEW
    from workeasy360.platform.authz import services as authz

    authz.require(request.tenant_context, AUTOMATION_VIEW)  # type: ignore[attr-defined]
    return JsonResponse({"results": catalog()})


# --- connections ---------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@never_cache
def connections_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.integration.connectors import selectors, services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        page = selectors.connections_page(
            ctx,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor"),
            status=request.GET.get("status"),
        )
        return JsonResponse(page.as_dict())

    body = _body(request)
    created = services.create_connection(
        ctx,
        connector_key=body.get("connector_key"),
        display_name=body.get("display_name"),
        config=body.get("config"),
    )
    payload: dict[str, Any] = {"connection": created.connection.as_dict()}
    if created.secret is not None:
        payload["secret"] = created.secret
        payload["notice"] = (
            "This signing secret is shown once and cannot be retrieved again. "
            "Store it in your receiver now; if it is lost, rotate it."
        )
    return JsonResponse(payload, status=201)


@require_http_methods(["GET", "PATCH"])
def connection_detail_view(
    request: HttpRequest, org_slug: str, connection_id: str
) -> JsonResponse:
    from workeasy360.integration.connectors import selectors, services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(selectors.connection_detail(ctx, connection_id))

    body = _body(request)
    updated = services.update_connection(
        ctx,
        connection_id,
        expected_revision=body.get("revision"),
        display_name=body.get("display_name"),
        config=body.get("config"),
    )
    return JsonResponse(updated.as_dict())


@require_http_methods(["POST"])
def connection_status_view(
    request: HttpRequest, org_slug: str, connection_id: str
) -> JsonResponse:
    from workeasy360.integration.connectors import services

    body = _body(request)
    updated = services.transition_connection(
        request.tenant_context,  # type: ignore[attr-defined]
        connection_id,
        to_status=body.get("status"),
        reason=body.get("reason", ""),
    )
    return JsonResponse(updated.as_dict())


@require_http_methods(["POST", "DELETE"])
@never_cache
def connection_secret_view(
    request: HttpRequest, org_slug: str, connection_id: str
) -> JsonResponse:
    """POST rotates (the new secret is in this response only); DELETE ends a
    rotation's overlap now, revoking the previous secret."""
    from workeasy360.integration.connectors import services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "DELETE":
        revoked = services.revoke_previous_secrets(ctx, connection_id)
        return JsonResponse({"revoked": revoked})

    issued = services.rotate_secret(ctx, connection_id)
    return JsonResponse(
        {
            "version": issued.version,
            "secret": issued.secret,
            "previous_valid_until": issued.previous_valid_until.isoformat(),
            "notice": (
                "This signing secret is shown once. The previous one keeps "
                "signing until previous_valid_until so your receiver can switch "
                "without dropping deliveries."
            ),
        },
        status=201,
    )


@require_http_methods(["GET", "PUT"])
def connection_subscriptions_view(
    request: HttpRequest, org_slug: str, connection_id: str
) -> JsonResponse:
    from workeasy360.integration.webhooks import selectors, services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(
            {"event_filters": selectors.subscriptions(ctx, connection_id)}
        )

    body = _body(request)
    filters = services.set_subscriptions(
        ctx, connection_id, event_filters=body.get("event_filters")
    )
    return JsonResponse({"event_filters": filters})


@require_http_methods(["POST"])
def connection_ping_view(
    request: HttpRequest, org_slug: str, connection_id: str
) -> JsonResponse:
    from workeasy360.integration.webhooks import services

    delivery_id = services.ping(request.tenant_context, connection_id)  # type: ignore[attr-defined]
    return JsonResponse({"delivery_id": str(delivery_id)}, status=202)


# --- deliveries ---------------------------------------------------------------------


@require_http_methods(["GET"])
def deliveries_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.integration.webhooks import selectors

    page = selectors.deliveries_page(
        request.tenant_context,  # type: ignore[attr-defined]
        limit=request.GET.get("limit"),
        cursor=request.GET.get("cursor"),
        status=request.GET.get("status"),
        connection_id=request.GET.get("connection_id"),
    )
    return JsonResponse(page.as_dict())


@require_http_methods(["GET"])
def delivery_detail_view(
    request: HttpRequest, org_slug: str, delivery_id: str
) -> JsonResponse:
    from workeasy360.integration.webhooks import selectors

    return JsonResponse(
        selectors.delivery_detail(request.tenant_context, delivery_id)  # type: ignore[attr-defined]
    )


@require_http_methods(["POST"])
def delivery_replay_view(
    request: HttpRequest, org_slug: str, delivery_id: str
) -> JsonResponse:
    from workeasy360.integration.webhooks import services

    replay_id = services.replay(request.tenant_context, delivery_id)  # type: ignore[attr-defined]
    return JsonResponse({"delivery_id": str(replay_id)}, status=202)


# --- automation -------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def automations_view(request: HttpRequest, org_slug: str) -> JsonResponse:
    from workeasy360.integration.automation import selectors, services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        page = selectors.rules_page(
            ctx,
            limit=request.GET.get("limit"),
            cursor=request.GET.get("cursor"),
            status=request.GET.get("status"),
        )
        return JsonResponse(page.as_dict())

    body = _body(request)
    rule = services.create_rule(
        ctx,
        name=body.get("name"),
        description=body.get("description", ""),
        definition=body.get("definition"),
    )
    return JsonResponse(rule, status=201)


@require_http_methods(["GET", "PUT"])
def automation_detail_view(
    request: HttpRequest, org_slug: str, rule_id: str
) -> JsonResponse:
    from workeasy360.integration.automation import selectors, services

    ctx = request.tenant_context  # type: ignore[attr-defined]
    if request.method == "GET":
        return JsonResponse(selectors.rule_detail(ctx, rule_id))

    body = _body(request)
    rule = services.update_rule(
        ctx,
        rule_id,
        expected_revision=body.get("revision"),
        name=body.get("name"),
        description=body.get("description"),
        definition=body.get("definition"),
    )
    return JsonResponse(rule)


@require_http_methods(["POST"])
def automation_status_view(
    request: HttpRequest, org_slug: str, rule_id: str
) -> JsonResponse:
    from workeasy360.integration.automation import services

    body = _body(request)
    rule = services.transition_rule(
        request.tenant_context,  # type: ignore[attr-defined]
        rule_id,
        to_status=body.get("status"),
        expected_revision=body.get("revision"),
    )
    return JsonResponse(rule)


@require_http_methods(["GET"])
def automation_executions_view(
    request: HttpRequest, org_slug: str, rule_id: str
) -> JsonResponse:
    from workeasy360.integration.automation import selectors

    page = selectors.executions_page(
        request.tenant_context,  # type: ignore[attr-defined]
        rule_id,
        limit=request.GET.get("limit"),
        cursor=request.GET.get("cursor"),
    )
    return JsonResponse(page.as_dict())


def _body(request: HttpRequest) -> dict[str, Any]:
    """A JSON object body, or a 400 - raised before any service runs, so a
    malformed request never reaches a write (D-016)."""
    try:
        payload = json_input.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise IntegrationValidationError("The request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise IntegrationValidationError("The request body must be a JSON object.")
    return payload
