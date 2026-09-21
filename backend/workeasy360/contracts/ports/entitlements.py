"""The entitlement read contract - what products import.

This is the single most consequential interface in the system. Because it
lives in contracts/ and is implemented in ``platform/entitlements``, a product
needs nothing at all from ``commercial/``, which is what lets the linter ban
``products -> commercial`` outright (01-domain-boundaries.md section 2).

A product asks "may this organization use this?" and gets an answer. It never
learns whether the answer came from a plan, a bundle, a trial, a partner
package or a support grant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from workeasy360.contracts.enums import EntitlementReason, EntitlementStatus
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode

#: A resolved feature value. ``bool`` for boolean features, ``int`` for quotas
#: (-1 meaning unlimited), ``str`` for enum features.
FeatureValue = bool | int | str | None

#: Quota sentinel. -1 rather than None so that "unlimited" participates in
#: max() during most-generous-wins resolution without a special case.
UNLIMITED: int = -1


@dataclass(frozen=True, slots=True)
class EntitlementDecision:
    """The answer to an entitlement question.

    ``reason_code`` is separate from ``allowed`` because the UI must render
    three different things for "you never bought this", "you bought it and it
    lapsed" and "the org has it but you have no seat". Returning a bare bool
    forces the UI to guess (06-entitlements.md section 4.3).
    """

    allowed: bool
    status: EntitlementStatus | None
    write_allowed: bool
    reason_code: EntitlementReason
    grace_ends_at: datetime | None = None
    upgrade_hint: str | None = None

    @classmethod
    def denied(
        cls,
        reason: EntitlementReason = EntitlementReason.NOT_ENTITLED,
        *,
        upgrade_hint: str | None = None,
    ) -> EntitlementDecision:
        """The default answer.

        An organization with no entitlement row for a product is denied. The
        absence of information never grants access, just as it never revokes it
        (06-entitlements.md section 4.4).
        """
        return cls(
            allowed=False,
            status=None,
            write_allowed=False,
            reason_code=reason,
            upgrade_hint=upgrade_hint,
        )


@runtime_checkable
class EntitlementService(Protocol):
    """The read API. Implemented by ``platform/entitlements``.

    Every method is a read. Nothing here writes, and no write method will be
    added: ``commercial/provisioning`` is the sole writer, enforced by an
    import-linter contract against ``platform.entitlements.services.write``.
    """

    def check(
        self, organization_id: UUID, product: ProductCode, *, write: bool = False
    ) -> EntitlementDecision:
        """Whether the organization may use ``product``.

        ``write=True`` additionally requires a status that permits writes, so
        a suspended organization reads but does not mutate.
        """
        ...

    def feature(
        self, organization_id: UUID, key: FeatureKey, *, default: FeatureValue = None
    ) -> FeatureValue:
        """Resolved value of a feature, most-generous-wins across sources."""
        ...

    def quota(self, organization_id: UUID, key: FeatureKey, *, default: int = 0) -> int:
        """Numeric limit for a quota feature. Returns ``UNLIMITED`` (-1) when
        unlimited, so callers compare with a single guard."""
        ...

    def has_seat(
        self, organization_id: UUID, membership_id: UUID, product: ProductCode
    ) -> bool:
        """Whether this member holds a seat.

        Separate from :meth:`check` because an organization may hold Books with
        5 of 5 seats assigned: user 6 is in an entitled org with no seat. That
        distinction is what makes per-seat pricing enforceable.
        """
        ...

    def products(self, organization_id: UUID) -> Sequence[ProductCode]:
        """Every product the organization currently holds."""
        ...


class EntitlementGate(Protocol):
    """The raising form, for use inside application services.

    ``require`` exists alongside ``check`` because the common case in a service
    is "stop here if not entitled", and forcing every caller to write the
    if/raise invites the check being dropped during a refactor.
    """

    def require(
        self, organization_id: UUID, product: ProductCode, *, write: bool = False
    ) -> EntitlementDecision:
        """Raise ``EntitlementDenied`` unless allowed."""
        ...

    def require_feature(self, organization_id: UUID, key: FeatureKey) -> FeatureValue:
        """Raise ``EntitlementDenied`` unless the feature is granted."""
        ...
