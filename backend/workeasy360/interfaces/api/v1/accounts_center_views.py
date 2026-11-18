"""The Accounts Control Center: a person's own account on WorkEasy360 Accounts
(ADR-072).

Session-authenticated and person-level, like ``account_views``: every endpoint
here acts on the caller's own account and takes no user id. Served only on the
WorkEasy360 Accounts host (``portal_surface``), and only while
``W360_ACCOUNTS_IDENTITY`` is on - off, each answers 404 exactly as before this
module existed.

**No second entitlement computation.** ``/api/v1/orgs/*`` is the customer
portal's, so a person's products across their organizations are assembled here
by visiting each organization the way the tenancy middleware admits a request
to it (``tenancy.middleware.admission_denial``: membership, organization
status, the organization's access policy), then asking the launcher's own
directory (``launcher_views.directory_entries``) and, to open a product, the
launcher's own decision and route (``launcher.services.launch`` /
``launch_route``). Buying, billing and seat management stay on User Admin and
the Store; this page links there.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.contracts.errors import AuthenticationRequired, MembershipValidationError
from workeasy360.contracts.values import json_input
from workeasy360.interfaces.api.v1.auth_views import connection_facts
from workeasy360.interfaces.api.v1.control_plane_http import limited
from workeasy360.platform.identity import account
from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: How long "Sign out of this product" waits for the product to confirm, as
#: "sign out everywhere" does (``account_views.SIGN_OUT_EVERYWHERE_WAIT_S``).
PRODUCT_SIGN_OUT_WAIT_S = 4.0


def _not_found() -> JsonResponse:
    return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)


def center(view: Callable[..., JsonResponse]) -> Callable[..., JsonResponse]:
    """Only while WorkEasy360 Accounts hosts identity (ADR-071); otherwise
    the endpoint does not exist. Then: a signed-in person, never cached."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        if not portal_links.accounts_identity():
            return _not_found()
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise AuthenticationRequired("Sign in to continue.")
        return view(request, user, *args, **kwargs)

    return never_cache(wrapper)


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json_input.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MembershipValidationError("Malformed JSON body.", field="body") from exc
    if not isinstance(payload, dict):
        raise MembershipValidationError("The body must be a JSON object.", field="body")
    return payload


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# --- the person's organizations, admitted as the tenancy middleware admits -----------


@dataclass(frozen=True, slots=True)
class Visit:
    """One of the person's organizations, and whether a request to it would
    be admitted right now (``denial`` is the code the customer portal would
    answer, e.g. ``network_not_allowed``)."""

    organization_id: str
    slug: str
    name: str
    status: str
    is_owner: bool
    resolution: Any
    denial: str | None


def _visits(request: HttpRequest, user: Any) -> list[Visit]:
    """Every organization the person actively belongs to (the chooser's own
    projection), each resolved and judged exactly as a request to
    ``/api/v1/orgs/<slug>/...`` would be."""
    from workeasy360.platform.organizations.selectors import (
        MAX_PAGE_SIZE,
        organizations_for_user,
    )
    from workeasy360.platform.tenancy.middleware import admission_denial
    from workeasy360.platform.tenancy.resolution import get_resolver

    resolver = get_resolver()
    page = organizations_for_user(user.id, limit=MAX_PAGE_SIZE)
    visits: list[Visit] = []
    for item in page.items:
        resolution = (
            resolver.resolve(user_id=user.id, organization_slug=item["slug"])
            if resolver is not None
            else None
        )
        denial: str | None = "not_found"
        if resolution is not None:
            refused = admission_denial(request, resolution)
            if refused is None:
                denial = None
            else:
                try:
                    denial = str(json.loads(refused.content).get("code") or "not_found")
                except (ValueError, AttributeError):
                    denial = "not_found"
        visits.append(
            Visit(
                organization_id=str(item["organization_id"]),
                slug=str(item["slug"]),
                name=str(item["display_name"]),
                status=str(item["status"]),
                is_owner=bool(item["is_owner"]),
                resolution=resolution if denial is None else None,
                denial=denial,
            )
        )
    visits.sort(key=lambda v: (v.name.lower(), v.slug))
    return visits


@contextmanager
def _inside(visit: Visit) -> Iterator[Any]:
    """Both isolation layers for one organization, as the middleware binds
    them for a request to it."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    ctx = visit.resolution.to_context()
    with tenant_context(ctx), tenant_db_scope(ctx):
        yield ctx


def _supplementary(what: str, read: Callable[[], Any], default: Any) -> Any:
    """Explore's trial facts and the setup desk are supplementary, as they are
    on My Products: if one cannot be read, the directory still answers. In a
    savepoint, so a failure leaves the organization's transaction usable."""
    from workeasy360.contracts.errors import Workeasy360Error

    try:
        with transaction.atomic():
            return read()
    except Workeasy360Error as exc:
        # A refusal (this member's role may not read it), exactly as User
        # Admin would answer: the card shows what the directory says.
        logger.info("accounts_center.supplementary_refused", what=what, code=exc.code)
        return default
    except Exception:
        logger.warning("accounts_center.supplementary_failed", what=what, exc_info=True)
        return default


def _organization_products(ctx: Any, *, full: bool = True) -> dict[str, Any]:
    """One organization's directory for this member, with - when ``full`` -
    what My Products reads beside it: trial facts (Explore) and open setup
    (the setup desk)."""
    from workeasy360.commercial.provisioning import setup as customer_setup
    from workeasy360.interfaces.api.v1.launcher_views import directory_entries
    from workeasy360.interfaces.api.v1.trial_views import _explore_listing
    from workeasy360.platform.authz import services as authz

    apps = directory_entries(ctx)
    if not full:
        return {"apps": apps}
    roles = authz.system_role_keys([ctx.membership_id]).get(str(ctx.membership_id), [])
    permissions = authz.effective_permissions(ctx)

    def explore() -> list[dict[str, Any]]:
        return [
            {
                "code": p["code"],
                "trial_state": p.get("trial_state"),
                "trial_outcome": p.get("trial_outcome"),
                "trial_ends_at": p.get("trial_ends_at"),
                "plans": [
                    {
                        "plan_code": plan.get("plan_code"),
                        "billing_type": plan.get("billing_type"),
                        "prices": [
                            {"amount_minor": price.get("amount_minor")}
                            for price in plan.get("prices", [])
                        ],
                    }
                    for plan in p.get("plans", [])
                ],
            }
            for p in _explore_listing(ctx)["products"]
        ]

    def setup() -> list[dict[str, Any]]:
        return [
            {"id": r["id"], "product_code": r["product_code"], "status": r["status"]}
            for r in customer_setup.customer_view(ctx)["results"]
        ]

    return {
        "roles": roles,
        "may_manage_billing": "commercial.subscription.view" in permissions,
        "may_assign_seats": "platform.seat.assign" in permissions,
        "apps": apps,
        "explore": _supplementary("explore", explore, None),
        "setup": _supplementary("setup", setup, None),
    }


def _products_by_organization(
    request: HttpRequest, user: Any, *, full: bool = True
) -> list[tuple[Visit, dict[str, Any] | None]]:
    found: list[tuple[Visit, dict[str, Any] | None]] = []
    for visit in _visits(request, user):
        if visit.denial is not None:
            found.append((visit, None))
            continue
        with _inside(visit) as ctx:
            found.append((visit, _organization_products(ctx, full=full)))
    return found


def _visit_json(visit: Visit) -> dict[str, Any]:
    return {
        "organization_id": visit.organization_id,
        "slug": visit.slug,
        "name": visit.name,
        "status": visit.status,
        "is_owner": visit.is_owner,
        # ``None``: open to this session; otherwise why the customer portal
        # would refuse it now (its access policy, a locked organization).
        "restricted": visit.denial,
    }


@require_http_methods(["GET"])
@center
def products_view(request: HttpRequest, user: Any) -> JsonResponse:
    """My Products and Organizations: every organization the person belongs
    to, their role in it, and - where this session is admitted - the
    launcher's directory of it (purchased, trial, seat state), Explore's
    trial facts and the setup still owed. Nothing here grants anything: each
    "Open" is re-decided by :func:`launch_view`."""
    organizations = []
    for visit, products in _products_by_organization(request, user):
        organizations.append({**_visit_json(visit), **(products or {"apps": []})})
    return JsonResponse(
        {
            "organizations": organizations,
            "app_origin": portal_links.app_url(),
            "store_origin": portal_links.store_url(),
        }
    )


@require_http_methods(["POST"])
@center
def launch_view(
    request: HttpRequest, user: Any, organization_id: uuid.UUID, product_code: str
) -> JsonResponse:
    """Open a product in one organization: the launcher's own re-check and
    route (``launcher.services``), exactly as User Admin's launch - the
    server-built ``launch_url?organization=<uuid>``, only when access is
    granted. An organization the person does not belong to, or one that
    would refuse this session, is the same 404/denial the customer portal
    answers."""
    from workeasy360.platform.launcher import services

    visit = next(
        (v for v in _visits(request, user) if v.organization_id == str(organization_id)),
        None,
    )
    if visit is None:
        return _not_found()
    if visit.denial is not None:
        return JsonResponse(
            {"code": visit.denial, "detail": "Your organization does not allow this."},
            status=404 if visit.denial == "not_found" else 403,
        )
    with _inside(visit) as ctx:
        decision = services.launch(ctx, product_code)
        if not decision.state.can_launch:
            metrics.increment("launcher_launch_denied_total", state=decision.state.value)
            return JsonResponse(
                {
                    "code": "launch_denied",
                    "state": decision.state.value,
                    "product_code": product_code,
                },
                status=409,
            )
        route = services.launch_route(ctx, decision)
    metrics.increment("launcher_launch_allowed_total", sign_in=route.sign_in)
    return JsonResponse(
        {
            "redirect_url": route.url,
            "state": decision.state.value,
            "sign_in": route.sign_in,
        }
    )


# --- Connected Logins -------------------------------------------------------------


def _product_names(codes: list[str]) -> dict[str, dict[str, str]]:
    from workeasy360.commercial.catalog import selectors as catalog

    return catalog.product_presentations(sorted(set(codes))) if codes else {}


@require_http_methods(["GET"])
@center
def connections_view(request: HttpRequest, user: Any) -> JsonResponse:
    """Connected Logins: per product, what is truthfully known.

    - **Access**, per organization: the launcher's directory state
      (purchased, trial, assigned seat, not assigned).
    - **SSO connection established** and **last signed in**, per organization:
      the token endpoint's record (``ProductConnection``).
    - **Last seen**: the product's last entitlement question about this
      person. Never "active": a product's own sign-out or timeout never
      reaches the platform.
    - **WorkEasy360 sign-in**: for a product that takes back-channel logout,
      whether a WorkEasy360 sign-in that opened it is still open, or ended;
      otherwise not tracked.
    - **Last sign-out**: the newest back-channel logout to it, delivered or
      pending.

    The ``subject`` is the person's own id (the ``sub`` every product links
    them by). No token, session key, credential or address is returned.
    """
    from workeasy360.platform.ecosystem import connections

    by_org = _products_by_organization(request, user, full=False)
    member_of = {v.organization_id: v for v, _ in by_org}
    access: dict[str, dict[str, dict[str, Any]]] = {}
    for visit, products in by_org:
        for app in (products or {}).get("apps", []):
            if not app.get("entitlement_status") and app.get("state") != "active":
                continue
            access.setdefault(app["product_code"], {})[visit.organization_id] = {
                "state": app.get("state"),
                "can_launch": bool(app.get("can_launch")),
                "entitlement_status": app.get("entitlement_status"),
                "licensing_mode": app.get("licensing_mode"),
                "trial_ends_at": (app.get("trial") or {}).get("ends_at"),
            }
    known = connections.connections_for_user(
        user.id,
        live_sign_in_ids=account.live_sign_in_ids(user.id),
        product_codes=set(access),
    )
    shown = _product_names(list(known))
    products = []
    for code, entry in sorted(known.items(), key=lambda kv: kv[1].display_name.lower()):
        connected = {str(o.organization_id): o for o in entry.organizations}
        organization_ids = list(dict.fromkeys([*access.get(code, {}), *connected]))
        rows = []
        for organization_id in organization_ids:
            visit = member_of.get(organization_id)
            link = connected.get(organization_id)
            rows.append(
                {
                    "organization_id": organization_id,
                    # A former organization is not named: only what the
                    # person can still see on User Admin is.
                    "name": visit.name if visit else "",
                    "slug": visit.slug if visit else "",
                    "member": visit is not None,
                    "access": access.get(code, {}).get(organization_id),
                    "connection": {
                        "first_sign_in_at": _iso(link.first_sign_in_at),
                        "last_sign_in_at": _iso(link.last_sign_in_at),
                        "sign_in_count": link.sign_in_count,
                        "last_activity_at": _iso(link.last_activity_at),
                    }
                    if link
                    else None,
                }
            )
        presentation = shown.get(code, {})
        last_sign_in = max((o.last_sign_in_at for o in entry.organizations), default=None)
        last_activity = max(
            (o.last_activity_at for o in entry.organizations if o.last_activity_at),
            default=None,
        )
        products.append(
            {
                "product_code": code,
                "name": presentation.get("name") or entry.display_name,
                "icon": presentation.get("icon", ""),
                "domain": {
                    "host": entry.launch_host,
                    "verified": entry.domain_verified,
                },
                "subject": str(user.id),
                "integration_status": entry.integration_status,
                "sso_connected": bool(entry.organizations),
                "last_sign_in_at": _iso(last_sign_in),
                "last_activity_at": _iso(last_activity),
                "session": {
                    "platform_sign_in": entry.platform_sign_in,
                    "since": _iso(entry.platform_sign_in_since),
                    "ended_at": _iso(entry.platform_sign_in_ended_at),
                },
                "last_sign_out": entry.last_sign_out,
                "can_sign_out": entry.takes_backchannel_logout
                and bool(entry.organizations),
                "organizations": rows,
            }
        )
    return JsonResponse({"subject": str(user.id), "products": products})


@require_http_methods(["POST"])
@center
def product_sign_out_view(
    request: HttpRequest, user: Any, product_code: str
) -> JsonResponse:
    """ "Sign out of this product": a back-channel logout of every session this
    person has in it (ADR-069 machinery). Reports what is true - ``delivered``
    once the product confirmed, ``pending`` while the worker retries, or
    ``unsupported`` for a product that takes no back-channel logout. Only a
    product the person is connected to."""
    from workeasy360.platform.ecosystem import connections
    from workeasy360.platform.identity.models import AccountEvent

    blocked = limited(request, "session_revoke", user=user)
    if blocked is not None:
        return blocked
    code = str(product_code)[:32]
    if not connections.is_connected(user_id=user.id, product_code=code):
        return _not_found()
    started = time.monotonic()
    asked = connections.sign_out_of_product(user_id=user.id, product_code=code)
    where = connection_facts(request)
    if not asked["supported"]:
        return JsonResponse({"product_code": code, "status": "unsupported"})
    account.record_account_event(
        user.id,
        AccountEvent.Kind.PRODUCT_SIGNED_OUT,
        reason=code,
        ip_prefix=where["ip_prefix"],
        device_label=where["device_label"],
    )
    status = connections.delivery_for(user.id, code, since=asked["since"])
    while status == "pending" and time.monotonic() - started <= PRODUCT_SIGN_OUT_WAIT_S:
        time.sleep(0.25)
        status = connections.delivery_for(user.id, code, since=asked["since"])
    return JsonResponse({"product_code": code, "status": status or "pending"})


# --- sessions, history, profile --------------------------------------------------


@require_http_methods(["GET"])
@center
def sessions_view(request: HttpRequest, user: Any) -> JsonResponse:
    """Active Sessions: one row per WorkEasy360 sign-in (device), not one per
    portal session. Revoke one with ``POST /api/v1/auth/sessions/<id>/revoke``,
    which ends the whole sign-in."""
    from workeasy360.platform.identity.sessions import session_public_id

    return JsonResponse(
        {
            "sessions": account.list_sign_ins(
                user.id, current_public_id=session_public_id(request.session)
            )
        }
    )


@require_http_methods(["GET"])
@center
def history_view(request: HttpRequest, user: Any) -> JsonResponse:
    """Login History: successful and failed sign-ins and product sign-ins
    (``scope=all``: every security event), newest first, paged by cursor
    over the whole retention window. Coarse network and device only."""
    try:
        limit = int(request.GET.get("limit") or 25)
    except ValueError:
        limit = 25
    everything = request.GET.get("scope") == "all"
    page = account.account_event_page(
        user.id,
        kinds=None if everything else account.SIGN_IN_KINDS,
        limit=limit,
        cursor=request.GET.get("cursor") or None,
    )
    product_kinds = {"product_sign_in", "product_signed_out"}
    names = _product_names(
        [
            r["reason"]
            for r in page["results"]
            if r["kind"] in product_kinds and r["reason"]
        ]
    )
    for row in page["results"]:
        row["product_name"] = (
            (names.get(row["reason"], {}).get("name") or row["reason"])
            if row["kind"] in product_kinds
            else None
        )
    return JsonResponse(page)


def _profile_json(user: Any) -> dict[str, Any]:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "name": user.full_name,
        "email_verified": user.email_verified_at is not None,
        "created_at": _iso(user.created_at),
        "last_sign_in_at": _iso(user.last_login_at),
    }


@require_http_methods(["GET", "PATCH"])
@center
def profile_view(request: HttpRequest, user: Any) -> JsonResponse:
    """The person's profile. ``PATCH {"full_name"}`` changes the display name
    (and only that: the sign-in email has its own confirmed change)."""
    if request.method == "PATCH":
        body = _body(request)
        where = connection_facts(request)
        user = account.update_profile(
            user.id,
            full_name=body.get("full_name"),
            ip_prefix=where["ip_prefix"],
            device_label=where["device_label"],
        )
    return JsonResponse(_profile_json(user))
