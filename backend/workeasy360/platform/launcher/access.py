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
from workeasy360.contracts.ports.catalog import (
    ProductDescriptor,
    get_product_directory,
)
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
    #: Phase 9: the organization's entitlement status when it holds the
    #: product (``trial``, ``active``, ``past_due``...), so My Products can say
    #: "Trial" - read from the same gate answer, never a second lookup.
    entitlement_status: str = ""
    #: ADR-044: the product's own sign-in page, populated with ``launch_url``
    #: (only for ``ACTIVE``), for a person whose effective sign-in is the
    #: product's own. Never serialised to the app directory either.
    product_login_url: str = ""


def can_launch_product(
    ctx: TenantContext,
    product_code: str,
    *,
    descriptor: ProductDescriptor | None = None,
) -> AppAccessDecision:
    """The decision. Task 9's "one authoritative application service" -
    every other caller in this module and in ``interfaces/`` reaches launch
    eligibility through this function and no other.

    ``descriptor`` is an optimisation and nothing more: a caller that has
    *just* read this product's catalog descriptor through the same port -
    ``selectors.app_directory``, which gets the whole marketable list in one
    query - passes it back rather than making this function re-read the
    identical row per product. Measured: that duplicate read was one of the
    two queries every product in the directory cost
    (docs/phase-4/09-performance.md).

    It carries no security weight. ``licensing_mode`` is the only field the
    decision branches on, it is intrinsic to the product rather than to
    anything a caller controls, and every field that *can* change underneath
    a stale answer - entitlement, seat, integration status - is still read
    live below, every call. A caller that has no descriptor to hand omits it
    and gets the identical answer.
    """
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
    if descriptor is None:
        descriptor = get_product_directory().get(code)
    display_name = descriptor.display_name if descriptor else str(product_code)
    description = descriptor.description if descriptor else ""
    licensing_mode = descriptor.licensing_mode if descriptor else LicensingMode.PER_SEAT

    def decision(
        state: AppAccessState,
        *,
        launch_url: str = "",
        status: str = "",
        product_login_url: str = "",
    ) -> AppAccessDecision:
        return AppAccessDecision(
            product_code=str(product_code),
            display_name=display_name,
            description=description,
            licensing_mode=licensing_mode,
            state=state,
            launch_url=launch_url,
            entitlement_status=status,
            product_login_url=product_login_url,
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

    status = entitlement.status.value if entitlement.status is not None else ""
    if target.is_suspended:
        return decision(AppAccessState.INTEGRATION_SUSPENDED, status=status)

    if licensing_mode == LicensingMode.PER_SEAT:
        held = get_gate().has_seat(ctx.organization_id, ctx.membership_id, code)
        if not held:
            return decision(AppAccessState.NO_SEAT, status=status)

    return decision(
        AppAccessState.ACTIVE,
        launch_url=target.launch_url,
        status=status,
        product_login_url=target.product_login_url,
    )


class SignInState(StrEnum):
    """Whether a product can be *signed in to* through WorkEasy360 right now
    (ADR-074) - a different question from :class:`AppAccessState`'s "may this
    member use it". Closed, and rendered as-is by Explore Products."""

    #: An active integration that offers WorkEasy360 sign-in and holds a
    #: credential the token endpoint accepts.
    AVAILABLE = "available"
    #: Registered and reversibly stopped by staff.
    SUSPENDED = "suspended"
    #: Never registered, still a draft, retired, or registered without
    #: WorkEasy360 sign-in (or without a usable credential).
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SignInTarget:
    """Where "Sign in" sends a browser for one product, and nothing else.

    ``launch_url`` is populated only for ``AVAILABLE`` and, like
    :class:`AppAccessDecision`'s, is never serialised to a listing."""

    product_code: str
    state: SignInState
    launch_url: str = ""
    product_login_url: str = ""


def sign_in_states(
    ctx: TenantContext, product_codes: list[str]
) -> dict[str, SignInState]:
    """:class:`SignInState` for each code.

    **Not an access decision, by design** (ADR-074). It never reads an
    entitlement or a seat: "Sign in" is offered for any configured product so
    the person reaches WorkEasy360's account chooser whether or not their
    organization bought it. Access is still decided where it always was - the
    authorization endpoint refuses ``product_not_entitled`` / ``product_no_seat``
    after the chooser and issues no code, and the product shows its own "no
    access" page. Nothing here can make a refused sign-in succeed.
    """
    from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
    from workeasy360.platform.ecosystem import sign_in

    codes = sorted({str(code) for code in product_codes})
    if not codes:
        return {}
    # One query for every registered product's doors (drafts and retired
    # products are absent: neither can be entered).
    doors = sign_in.product_sign_in(ctx.user_id, codes)
    states: dict[str, SignInState] = {}
    for code in codes:
        found = doors.get(code)
        if found is None:
            states[code] = SignInState.UNAVAILABLE
        elif found.doors.sso_available:
            states[code] = SignInState.AVAILABLE
        else:
            # Registered but not enterable through WorkEasy360: suspended by
            # staff, or offering no WorkEasy360 sign-in / no usable credential.
            target = ecosystem_selectors.launch_target(code)
            states[code] = (
                SignInState.SUSPENDED
                if target is not None and target.is_suspended
                else SignInState.UNAVAILABLE
            )
    return states


def sign_in_target(ctx: TenantContext, product_code: str) -> SignInTarget:
    """Re-read immediately before "Sign in" sends a browser at a product -
    the same no-trusting-the-page rule as ``services.launch``."""
    from workeasy360.platform.ecosystem import selectors as ecosystem_selectors

    try:
        code = str(ProductCode(product_code))
    except ValueError:
        return SignInTarget(product_code=str(product_code), state=SignInState.UNAVAILABLE)
    state = sign_in_states(ctx, [code])[code]
    if state is not SignInState.AVAILABLE:
        return SignInTarget(product_code=code, state=state)
    target = ecosystem_selectors.launch_target(code)
    if target is None or not target.can_authenticate:  # changed in between
        return SignInTarget(product_code=code, state=SignInState.UNAVAILABLE)
    return SignInTarget(
        product_code=code,
        state=SignInState.AVAILABLE,
        launch_url=target.launch_url,
        product_login_url=target.product_login_url,
    )
