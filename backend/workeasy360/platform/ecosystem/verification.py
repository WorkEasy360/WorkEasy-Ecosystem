"""Remote entitlement verification: "may this organization use me?"

The remote form of what ``contracts/ports/entitlements.py`` already is
in-process, and it keeps the same promise: a product asks whether an
organization may use it and gets an answer. It never learns whether that
answer came from a plan, a bundle, a trial, a partner package or a support
grant, what anything cost, or when the subscription renews.

Three properties carry the security of this endpoint.

**The product cannot choose the subject.** ``product_code`` comes from the
authenticated client's registration and is not a request parameter at all. A
CRM client cannot ask about Books because the question has no field in which
to name Books. This is stronger than validating a supplied code against the
caller's identity - there is nothing to validate, because there is nothing to
supply (docs/phase-3/06-entitlement-api.md section 4).

**An unknown organization is indistinguishable from an unentitled one.** Both
return ``deny`` with ``not_entitled``. Any difference - a different reason, a
different status, a measurably different latency - turns this endpoint into
an organization-enumeration oracle for every product in the ecosystem.

**A platform failure is never reported as a denial.** If the entitlement read
itself fails, the answer is 503 ``temporarily_unavailable``, not ``deny``. The
distinction is the whole reason this function is written the way it is: a
product that receives ``deny`` will tell a paying customer they have no
licence and may tear down their session; a product that receives 503 will
retry or serve from its last known good answer. Collapsing an outage into a
denial converts a platform incident into an ecosystem-wide false lockout
(docs/phase-3/06-entitlement-api.md section 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from workeasy360.contracts.enums import EntitlementReason
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics, timed

logger = get_logger(__name__)

ALLOW = "allow"
DENY = "deny"

#: Reason codes a product may switch on. A closed, documented set.
#:
#: Mapped from ``EntitlementReason`` rather than passed through, because that
#: enum is a *platform internal* that commercial code may extend freely, and
#: a product switching on a value that changed under it would break in
#: production with no deploy of its own. This mapping is the versioned
#: contract; the enum behind it is not.
_REASON_MAP: dict[str, str] = {
    EntitlementReason.OK.value: "entitled",
    EntitlementReason.NOT_ENTITLED.value: "not_entitled",
    EntitlementReason.EXPIRED.value: "expired",
    EntitlementReason.SUSPENDED.value: "suspended",
    EntitlementReason.READ_ONLY.value: "read_only",
    EntitlementReason.NO_SEAT.value: "no_seat",
    EntitlementReason.UNKNOWN.value: "not_entitled",
}

#: The answer given for any organization the platform cannot positively
#: confirm. Named so the two call sites that need it cannot drift apart.
UNKNOWN_ORGANIZATION_REASON = "not_entitled"


class VerificationUnavailable(RuntimeError):
    """The platform could not determine an answer.

    Deliberately not a denial. See this module's docstring.
    """


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The wire contract. Versioned by the API path, not by this class."""

    decision: str
    reason: str
    product_code: str
    organization_id: str
    write_allowed: bool
    checked_at: str
    revalidate_after: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "product_code": self.product_code,
            "organization_id": self.organization_id,
            "write_allowed": self.write_allowed,
            "checked_at": self.checked_at,
            # How long the product may trust this answer. Returned rather
            # than documented-only, so the cadence is the platform's to
            # change and not nine products' to each guess differently.
            "revalidate_after": self.revalidate_after,
        }


def verify(
    *,
    organization_id: UUID,
    product_code: str,
    write: bool = False,
    subject: str | None = None,
) -> VerificationResult:
    """Decide whether ``organization_id`` may use ``product_code``.

    ``write`` lets a product ask the stronger question before a mutation, so
    that a suspended organization is read-only inside the product exactly as
    it is inside the platform (R-10) - without the product having to know
    what "suspended" means or that the concept exists.

    ``subject`` (optional; the ``sub`` of the ID token the product signed the
    person in with) adds the personal half of sign-in's checks, so a product
    session ends within one revalidation window when the person's account is
    disabled, their membership is removed or their seat is unassigned - not
    only at their next sign-in (ADR-044 addendum). Asked only once the
    organization itself is allowed: an organization-level denial is the more
    useful answer and says nothing about the person. Answered ``deny`` with
    ``membership_inactive`` or ``no_seat``; an unknown or malformed subject is
    ``membership_inactive``, indistinguishable from a removed member.
    """
    from workeasy360.platform.ecosystem.authorization import person_access_reason
    from workeasy360.platform.entitlements.selectors import decision_for_organization
    from workeasy360.platform.organizations.selectors import organization_status

    now = timezone.now()
    revalidate_after = int(settings.OIDC_ENTITLEMENT_REVALIDATE_AFTER_S)
    person_reason: str | None = None

    try:
        with timed("ecosystem_entitlement_verify_duration_seconds"):
            # The **service** read path, not ``get_gate()``.
            #
            # The gate reads a tenant-scoped snapshot through the scoped
            # manager, so it needs an ambient ``TenantContext`` - and a
            # context needs a user and a membership. There is neither here:
            # this request is one server asking another about an organization
            # that may have nobody signed in at all. Fabricating an actor to
            # satisfy the manager would put an invented person's identifier
            # into the logs of every service-to-service check.
            #
            # ``decision_for_organization`` is the sanctioned route for that
            # caller: it relaxes only the ORM layer, audibly, and still enters
            # the organization's database scope so RLS confines the read to
            # one tenant. It reaches the same ``decide()`` the gate does, so
            # the platform and the product can never be told different things.
            decision = decision_for_organization(
                organization_id, product_code, write=write
            )
            # Phase 12 (G-24): the organization's own lifecycle, not only its
            # entitlement. A staff-suspended organization is read-only on the
            # platform (the R-10 write block reads this same status) but was
            # reported ``write_allowed: true`` here, because the entitlement
            # decision only knows about *subscription* suspension.
            status = organization_status(organization_id)
            if (
                subject is not None
                and decision.allowed
                and status is not None
                and status.allows_read
            ):
                person_reason = _person_reason(
                    subject, organization_id, product_code, person_access_reason
                )
    except Exception as exc:
        # Everything else - the database is down, the snapshot table is
        # unreadable, the gate is unregistered. None of these is a statement
        # about the customer's licence, so none of them may be returned as
        # one.
        logger.error(
            "ecosystem.entitlement.unavailable",
            product_code=product_code,
            error_type=type(exc).__name__,
        )
        metrics.increment("ecosystem_entitlement_unavailable_total")
        raise VerificationUnavailable(
            "The entitlement decision could not be determined."
        ) from exc

    if status is None or not status.allows_read:
        metrics.increment(
            "ecosystem_entitlement_verifications_total",
            decision=DENY,
            reason=UNKNOWN_ORGANIZATION_REASON,
        )
        return VerificationResult(
            decision=DENY,
            reason=UNKNOWN_ORGANIZATION_REASON,
            product_code=product_code,
            organization_id=str(organization_id),
            write_allowed=False,
            checked_at=now.isoformat(),
            revalidate_after=revalidate_after,
        )
    organization_writable = status.allows_write
    if decision.allowed and write and not organization_writable:
        metrics.increment(
            "ecosystem_entitlement_verifications_total", decision=DENY, reason="suspended"
        )
        return VerificationResult(
            decision=DENY,
            reason="suspended",
            product_code=product_code,
            organization_id=str(organization_id),
            write_allowed=False,
            checked_at=now.isoformat(),
            revalidate_after=revalidate_after,
        )

    if not decision.allowed:
        reason = _REASON_MAP.get(decision.reason_code.value, UNKNOWN_ORGANIZATION_REASON)
        metrics.increment(
            "ecosystem_entitlement_verifications_total", decision=DENY, reason=reason
        )
        return VerificationResult(
            decision=DENY,
            reason=reason,
            product_code=product_code,
            organization_id=str(organization_id),
            write_allowed=False,
            checked_at=now.isoformat(),
            revalidate_after=revalidate_after,
        )

    if person_reason is not None:
        metrics.increment(
            "ecosystem_entitlement_verifications_total",
            decision=DENY,
            reason=person_reason,
        )
        return VerificationResult(
            decision=DENY,
            reason=person_reason,
            product_code=product_code,
            organization_id=str(organization_id),
            write_allowed=False,
            checked_at=now.isoformat(),
            revalidate_after=revalidate_after,
        )

    metrics.increment(
        "ecosystem_entitlement_verifications_total", decision=ALLOW, reason="entitled"
    )
    return VerificationResult(
        decision=ALLOW,
        reason="entitled",
        product_code=product_code,
        organization_id=str(organization_id),
        # Reported from the decision, never assumed from the read answer.
        #
        # A read-only entitlement does not imply a writable one: ``suspended``
        # permits reads and refuses writes, which is exactly the state a
        # customer whose card failed is in. Telling that product
        # ``write_allowed: true`` would have it offer a save button the
        # platform then refuses, and the product would look broken for a
        # condition it was never told about.
        #
        # One read answers both, because ``EntitlementDecision`` already
        # carries ``write_allowed`` alongside ``allowed`` - which is why the
        # decision is returned rather than raised on this path.
        write_allowed=decision.write_allowed and organization_writable,
        checked_at=now.isoformat(),
        revalidate_after=revalidate_after,
    )


def _person_reason(
    subject: str, organization_id: UUID, product_code: str, check: Any
) -> str | None:
    """``check`` (``authorization.person_access_reason``) for a subject that is
    a UUID; a subject this platform could never have issued (every ``sub`` is
    a user id) is a person who is not a member, not a malformed request - a
    400 would read to the product as its own credential being refused. It is
    still asked about, as the nil UUID (no account has it), so a malformed
    subject costs the same reads as an unknown one."""
    try:
        user_id = UUID(subject)
    except ValueError:
        check(
            user_id=UUID(int=0),
            organization_id=organization_id,
            product_code=product_code,
        )
        return "membership_inactive"
    return check(
        user_id=user_id, organization_id=organization_id, product_code=product_code
    )
