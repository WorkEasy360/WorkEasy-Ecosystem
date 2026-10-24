"""Access diagnostics: "why can user X not launch product Y?" (Tasks 14, 50-53, 107-108).

**Read-only, and it says so structurally**: nothing in this module writes, and
the test suite runs it with the database's write path instrumented. The answer
is a *reason chain* - an ordered list of facts, each true or not, ending at the
first one that blocks - and never a guess at a root cause. A diagnostic reports
state (statuses, counts, codes); it never returns a secret, a credential, a
signing key, an SMTP setting or an integration secret, because
none of those is read.

Two callers, one implementation, different authority:

- **Staff** (``platform.staff_support.view``): any customer; may identify the
  person by address.
- **A partner** (``platform.partner_support.access``): only a customer it has an
  *active or suspended relationship* with, bounded to the same statuses. It may
  identify a member by address too, and learns nothing beyond the chain - no
  name, no roles, no member list. An address that is **not a member of that
  customer** gets one answer, ``not_a_member``, whether or not it belongs to a
  Workeasy360 account anywhere else: the account's existence and lock state
  are the platform's to know, not a partner's (Phase 7 final audit).

The chain follows the launcher's own decision function for its last steps, so
the diagnostic and the real launch cannot disagree about what "entitled" or
"seated" means.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.partners import access
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.contracts.enums import (
    LicensingMode,
    MembershipStatus,
    OrganizationStatus,
    PrincipalKind,
    UserStatus,
)
from workeasy360.contracts.errors import PartnerRecordNotFound, PermissionDenied
from workeasy360.contracts.ports.entitlements import get_gate
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import (
    acting_scope,
    context_for_organization,
)
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.identity import selectors as identity_selectors
from workeasy360.platform.observability.metrics import metrics
from workeasy360.platform.organizations import selectors as org_selectors

STAFF_PERMISSION = "platform.staff_support.view"
PARTNER_PERMISSION = "platform.partner_support.access"

DIAGNOSTICS = "access_diagnostics_total"


def _step(name: str, ok: bool, code: str, **detail: Any) -> dict[str, Any]:
    return {"step": name, "ok": ok, "code": code, **detail}


def diagnose(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    product_code: str,
    member_email: str | None = None,
) -> dict[str, Any]:
    """The reason chain for one (customer, product[, member]) - read-only."""
    if ctx.principal is PrincipalKind.PARTNER:
        authz.require(ctx, PARTNER_PERMISSION)
        # A live relationship is required; suspended links may still be read.
        customer = access.customer_context(ctx, organization_id, write=False)
    elif ctx.principal is PrincipalKind.STAFF:
        authz.require(ctx, STAFF_PERMISSION)
        customer = context_for_organization(ctx, organization_id)
    else:  # pragma: no cover - contexts are STAFF or PARTNER here
        raise PermissionDenied(permission=STAFF_PERMISSION)

    try:
        code = ProductCode(str(product_code or ""))
    except ValueError:
        return _finish(
            [_step("product", False, "invalid_product_code")],
            organization_id,
            str(product_code),
        )

    chain: list[dict[str, Any]] = []

    lifecycle = org_selectors.organization_lifecycle(organization_id)
    if lifecycle is None:
        raise PartnerRecordNotFound("No such customer.")
    status = OrganizationStatus(lifecycle["status"])
    chain.append(
        _step(
            "organization",
            status.allows_login and status.allows_read,
            f"organization_{status.value}",
            can_write=status.allows_write,
        )
    )

    membership_id: UUID | None = None
    if member_email:
        facts = identity_selectors.user_facts_for_email(member_email)
        membership = None
        if facts is not None:
            with acting_scope(customer):
                membership = org_selectors.membership_for_user(customer, facts["user_id"])
        if ctx.principal is PrincipalKind.PARTNER and membership is None:
            # Unknown address and "someone else's account" are one answer.
            chain.append(_step("membership", False, "not_a_member"))
        elif facts is None:
            chain.append(_step("user", False, "user_not_found"))
        else:
            user_ok = (
                facts["status"] == UserStatus.ACTIVE.value and not facts["is_locked_out"]
            )
            chain.append(
                _step(
                    "user",
                    user_ok,
                    "user_active"
                    if user_ok
                    else (
                        "user_locked_out"
                        if facts["is_locked_out"]
                        else f"user_{facts['status']}"
                    ),
                )
            )
            if membership is None:
                chain.append(_step("membership", False, "not_a_member"))
            else:
                active = membership["status"] == MembershipStatus.ACTIVE.value
                chain.append(
                    _step("membership", active, f"membership_{membership['status']}")
                )
                if active:
                    membership_id = membership["id"]

    with acting_scope(customer):
        subscriptions = subscription_selectors.subscriptions_for_organization()
        open_subs = [s for s in subscriptions if s.status.value != "expired"]
        holds_product = any(
            i.product_code == str(code) and i.removed_at is None
            for s in open_subs
            for i in s.items
        )
        sub_status = open_subs[-1].status.value if open_subs else "none"
        chain.append(
            _step(
                "subscription",
                bool(open_subs) and holds_product,
                "subscription_includes_product"
                if holds_product
                else ("subscription_lacks_product" if open_subs else "no_subscription"),
                subscription_status=sub_status,
            )
        )

        decision = get_gate().check(organization_id, code)
        chain.append(
            _step(
                "entitlement",
                bool(decision.allowed),
                "entitled"
                if decision.allowed
                else f"not_entitled_{decision.reason_code.value}",
            )
        )

        target = ecosystem_selectors.launch_target(str(code))
        licensing = LicensingMode.PER_SEAT
        if membership_id is not None and decision.allowed:
            per_seat = _is_per_seat(str(code))
            licensing = (
                LicensingMode.PER_SEAT if per_seat else LicensingMode.PER_ORGANIZATION
            )
            if per_seat:
                held = bool(get_gate().has_seat(organization_id, membership_id, code))
                chain.append(
                    _step("seat", held, "seat_assigned" if held else "no_seat_assigned")
                )
            else:
                chain.append(_step("seat", True, "seat_not_required"))

    if target is None or (not target.can_authenticate and not target.is_suspended):
        chain.append(_step("integration", False, "integration_not_available"))
    elif target.is_suspended:
        chain.append(_step("integration", False, "integration_suspended"))
    else:
        chain.append(_step("integration", True, "integration_active"))
    chain.append(
        _step(
            "sso",
            bool(target and target.can_authenticate),
            "sso_available" if target and target.can_authenticate else "sso_unavailable",
        )
    )
    return _finish(chain, organization_id, str(code), licensing=licensing.value)


def _is_per_seat(product_code: str) -> bool:
    from workeasy360.contracts.ports.catalog import get_product_directory

    descriptor = get_product_directory().get(ProductCode(product_code))
    return descriptor is None or descriptor.licensing_mode == LicensingMode.PER_SEAT


def _finish(
    chain: list[dict[str, Any]], organization_id: UUID, product_code: str, **extra: Any
) -> dict[str, Any]:
    blocking = next((s for s in chain if not s["ok"]), None)
    metrics.increment(DIAGNOSTICS, outcome="blocked" if blocking else "clear")
    return {
        "organization_id": str(organization_id),
        "product_code": product_code,
        "can_launch": blocking is None,
        "blocking_step": blocking["step"] if blocking else None,
        "chain": chain,
        "read_only": True,
        **extra,
    }
