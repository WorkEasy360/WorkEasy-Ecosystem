"""The one authoritative "can this member launch this product?" decision.

ADR-021, Task 9 of the Phase 4 brief. Composes three things, each read
through the same channel every other caller of it uses - never re-derived,
never duplicated:

- ``platform/ecosystem`` (through its narrow ``launch_target`` selector,
  ADR-021 §4): does a launchable integration exist for this product, and is
  it currently accepting authentication?
- ``platform/entitlements`` (through ``contracts/ports/entitlements.py``,
  exactly as ``platform/ecosystem/authorization.py`` reaches it): is the
  organization entitled, and - for a per-seat product - does *this* member
  hold a seat?
- the caller's own ``TenantContext``: user, membership and organization
  validity are already guaranteed by whatever established it (the tenancy
  middleware, for every caller in this module), so nothing here re-checks
  them.

Called from two places, and must answer both identically: the app directory
(``selectors.app_directory``, informational) and the launch endpoint
(re-checked immediately before a browser is redirected at a product,
Task 16). A stale answer rendered by the first is corrected by the second
re-running this exact function - never by the launch endpoint trusting what
the browser was shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from workeasy360.contracts.enums import LicensingMode, StrEnum
from workeasy360.contracts.ports.catalog import get_product_directory
from workeasy360.contracts.ports.entitlements import get_gate
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext


class AppAccessState(StrEnum):
    """Explicit, closed states the frontend renders (Task 8) - never
    inferred client-side from a combination of raw fields.

    Ordered, in ``can_launch_product``, from the most structural reason a
    launch cannot happen to the most transient: a product that was never
    built is a different message from a product that is merely down for
    five minutes, and conflating them is exactly what Task 8 forbids
    ("do not infer UI states from arbitrary combinations in the browser").
    """

    #: Entitled, seated (or organization-wide), integration accepting
    #: authentication. The only state a launch is attempted from.
    ACTIVE = "active"
    #: Entitled, per-seat licensed, this member holds no seat.
    NO_SEAT = "no_seat"
    #: A launchable integration exists but this organization does not hold
    #: the product.
    NO_ENTITLEMENT = "no_entitlement"
    #: Entitled (and seated, where relevant), but the registered integration
    #: is reversibly suspended - an operational fact, not a commercial one.
    INTEGRATION_SUSPENDED = "integration_suspended"
    #: No launchable integration exists for this product at all, or the
    #: catalog does not know the code. Marketable products the platform has
    #: not yet built appear this way (Task 21) - informational only, no
    #: Open/Activate action renders for it.
    PRODUCT_UNAVAILABLE = "product_unavailable"

    @property
    def can_launch(self) -> bool:
        return self is AppAccessState.ACTIVE


@dataclass(frozen=True, slots=True)
class AppAccessDecision:
    """The full answer for one product, one organization, one member.

    ``launch_url`` is populated only when ``state`` is ``ACTIVE`` - it is
    read here (from ``platform/ecosystem``) purely so the launch endpoint
    that calls this function has it ready to redirect to in the same call;
    the app-directory HTTP response never serialises this field (Task 6: "do
    not expose ... redirect configuration").
    """

    product_code: str
    display_name: str
    description: str
    licensing_mode: LicensingMode
    state: AppAccessState
    launch_url: str = ""


def can_launch_product(ctx: TenantContext, product_code: str) -> AppAccessDecision:
    """The decision. Task 9's "one authoritative application service" -
    every other caller in this module and in ``interfaces/`` reaches launch
    eligibility through this function and no other."""
    from workeasy360.platform.ecosystem import selectors as ecosystem_selectors

    try:
        code = ProductCode(product_code)
    except ValueError:
        # The launch endpoint accepts a product code from the URL (Task 55) -
        # a browser can send any string. A code that is not even
        # well-formed can never be a real product; refused the same way an
        # unregistered one is, not raised as a server error.
        return AppAccessDecision(
            product_code=str(product_code),
            display_name=str(product_code),
            description="",
            licensing_mode=LicensingMode.PER_SEAT,
            state=AppAccessState.PRODUCT_UNAVAILABLE,
        )
    descriptor = get_product_directory().get(code)
    display_name = descriptor.display_name if descriptor else str(product_code)
    description = descriptor.description if descriptor else ""
    licensing_mode = descriptor.licensing_mode if descriptor else LicensingMode.PER_SEAT

    def decision(state: AppAccessState, *, launch_url: str = "") -> AppAccessDecision:
        return AppAccessDecision(
            product_code=str(product_code),
            display_name=display_name,
            description=description,
            licensing_mode=licensing_mode,
            state=state,
            launch_url=launch_url,
        )

    target = ecosystem_selectors.launch_target(str(product_code))
    if target is None or (not target.can_authenticate and not target.is_suspended):
        # Structural, not operational: nothing exists to send a browser to
        # (never registered, still draft), or never will again (retired).
        # Checked before entitlement - whether the organization holds the
        # product is moot when there is nowhere to launch it (Task 21).
        return decision(AppAccessState.PRODUCT_UNAVAILABLE)

    entitlement = get_gate().check(ctx.organization_id, code)
    if not entitlement.allowed:
        # Checked before the integration's operational status: an
        # organization that does not hold the product should never learn
        # whether the product happens to be having an outage right now
        # (Task 48 - no commercial/security internals leaked beyond what is
        # needed to render "Activate").
        return decision(AppAccessState.NO_ENTITLEMENT)

    if target.is_suspended:
        return decision(AppAccessState.INTEGRATION_SUSPENDED)

    if licensing_mode == LicensingMode.PER_SEAT:
        held = get_gate().has_seat(ctx.organization_id, ctx.membership_id, code)
        if not held:
            return decision(AppAccessState.NO_SEAT)

    return decision(AppAccessState.ACTIVE, launch_url=target.launch_url)
