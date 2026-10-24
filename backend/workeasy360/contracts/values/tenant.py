"""TenantContext - the resolved answer to "who is acting, in which tenant".

Established once per request at the trust boundary (TB-2) from a *verified*
membership, never from a header or request body, and then carried unchanged
through services, selectors, tasks and events.

This type lives in contracts/ because every layer reads it, including layers
that must not import ``platform/``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from workeasy360.contracts.enums import OrganizationStatus, PrincipalKind

#: The membership id carried by a control-plane principal. Staff and partner
#: users hold no ``Membership`` in the organization they act on, and a
#: fabricated one would put a real-looking id in the audit log. The nil UUID
#: matches no row anywhere, so any membership-keyed lookup denies by default.
NO_MEMBERSHIP = UUID(int=0)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable per-request tenancy and actor identity.

    Frozen because a context that can be mutated mid-request is a context that
    can be widened mid-request. Narrowing or altering it produces a new object
    via :meth:`replace_status`, which keeps the change visible at the call site.
    """

    organization_id: UUID
    membership_id: UUID
    user_id: UUID

    #: Drives the R-10 write block. Carried on the context so that the
    #: persistence chokepoint can enforce suspension without a database round
    #: trip on every save.
    organization_status: OrganizationStatus = OrganizationStatus.ACTIVE

    #: True for staff impersonation. Tagged in logs, traces and audit, and
    #: visible to the customer in near-real-time (04-identity-authz.md s6).
    is_break_glass: bool = False

    #: Required when is_break_glass is True; recorded on every audit entry.
    reason: str | None = None

    #: Whose permissions this context carries (ADR-027). ``MEMBER`` is every
    #: pre-Phase-7 caller. ``STAFF`` and ``PARTNER`` are established only by
    #: the control-plane entry points, from a verified identity, and hold
    #: ``NO_MEMBERSHIP``.
    principal: PrincipalKind = PrincipalKind.MEMBER

    #: The partner a ``PARTNER`` principal acts for. Present exactly when the
    #: principal is a partner: a stray partner id on a member context would be
    #: a scope claim nothing verified.
    partner_id: UUID | None = None

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "membership_id", "user_id"):
            value = getattr(self, field_name)
            if not isinstance(value, UUID):
                raise TypeError(
                    f"TenantContext.{field_name} must be a UUID, got "
                    f"{type(value).__name__}. Accepting strings here invites "
                    f"comparison bugs against UUID columns."
                )
        if self.is_break_glass and not self.reason:
            raise ValueError(
                "Break-glass context requires a reason. An unexplained "
                "privileged session is exactly what the control exists to "
                "prevent (04-identity-authz.md section 6)."
            )
        if (self.principal is PrincipalKind.PARTNER) != (self.partner_id is not None):
            raise ValueError(
                "A partner_id is required for, and only valid on, a PARTNER principal."
            )
        if self.principal is not PrincipalKind.MEMBER and (
            self.membership_id != NO_MEMBERSHIP
        ):
            raise ValueError(
                "A control-plane principal holds no membership; use "
                "NO_MEMBERSHIP rather than inventing one."
            )

    # --- policy predicates -------------------------------------------------

    @property
    def is_write_allowed(self) -> bool:
        """Whether the organization's status permits writes at all.

        This is tenancy-level write eligibility (R-10), not authorization and
        not entitlement. All three are checked, in that order, and none
        substitutes for another.
        """
        return self.organization_status.allows_write

    @property
    def is_control_plane(self) -> bool:
        """Whether this context speaks for staff or a partner, not a member."""
        return self.principal is not PrincipalKind.MEMBER

    @property
    def is_read_allowed(self) -> bool:
        return self.organization_status.allows_read

    def replace_status(self, status: OrganizationStatus) -> TenantContext:
        """Return a copy with a new organization status.

        Used when a long-running task re-reads the organization and finds it
        has been suspended mid-run, so the write block takes effect without
        waiting for the task to finish.
        """
        return replace(self, organization_status=status)

    # --- transport ---------------------------------------------------------

    def to_headers(self) -> dict[str, Any]:
        """Serialise for Celery task headers.

        Contextvars do not cross the process boundary into a worker, so the
        context travels with the message and is re-established in
        ``before_start`` (R-07).
        """
        return {
            "organization_id": str(self.organization_id),
            "membership_id": str(self.membership_id),
            "user_id": str(self.user_id),
            "organization_status": self.organization_status.value,
            "is_break_glass": self.is_break_glass,
            "reason": self.reason,
            "principal": self.principal.value,
            "partner_id": str(self.partner_id) if self.partner_id else None,
        }

    @classmethod
    def from_headers(cls, raw: dict[str, Any]) -> TenantContext:
        """Rebuild from task headers.

        Raises rather than defaulting on a malformed payload: a task that
        cannot establish its tenant must refuse to start and dead-letter, not
        run against a guessed context.
        """
        try:
            return cls(
                organization_id=UUID(raw["organization_id"]),
                membership_id=UUID(raw["membership_id"]),
                user_id=UUID(raw["user_id"]),
                organization_status=OrganizationStatus(
                    raw.get("organization_status", OrganizationStatus.ACTIVE.value)
                ),
                is_break_glass=bool(raw.get("is_break_glass", False)),
                reason=raw.get("reason"),
                principal=PrincipalKind(raw.get("principal", PrincipalKind.MEMBER.value)),
                partner_id=UUID(raw["partner_id"]) if raw.get("partner_id") else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed tenant context headers: {exc}") from exc

    def for_logging(self) -> dict[str, Any]:
        """Fields safe to attach to every log line and span.

        Identifiers only - no email, no name, no reason text. ``reason`` is
        operator-supplied free text and is recorded in audit, which is access
        controlled, rather than in logs, which are widely readable
        (11-observability.md section 4 rule 3).
        """
        return {
            "organization_id": str(self.organization_id),
            "user_id": str(self.user_id),
            "membership_id": str(self.membership_id),
            "is_break_glass": self.is_break_glass,
            "principal": self.principal.value,
        }
