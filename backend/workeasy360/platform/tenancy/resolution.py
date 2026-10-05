"""Tenant resolution: the shape of the answer, and who supplies it.

``platform/tenancy`` is P0 and ``platform/organizations`` is P1, so tenancy
must not import organizations - upward edges inside platform are forbidden
(F-4, and the layer table in 01-domain-boundaries.md section 5).

But resolution genuinely needs the Organization and Membership tables. The
sanctioned replacement for a forbidden edge is runtime resolution through a
registry (07-cross-product-integration.md section 4): tenancy declares the
protocol and owns the registry; organizations implements it and registers
itself at app-ready. There is no import edge, so the linter stays satisfied
and the layering is real rather than nominal.
"""

from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.values.access_policy import AccessPolicy, SessionFacts
from workeasy360.contracts.values.tenant import TenantContext

#: Organization is taken from the URL path and nowhere else (TB-1/TB-2).
#: A header or body value is attacker-controlled; a path segment is at least
#: matched against a membership the server verified.
_ORG_PATH_RE: Final = re.compile(
    r"^/api/v\d+/orgs/(?P<slug>[a-z0-9][a-z0-9-]{0,62})(/|$)"
)


@dataclass(frozen=True, slots=True)
class TenantResolution:
    """Everything the middleware needs to admit or deny a request."""

    organization_id: UUID
    organization_slug: str
    organization_status: OrganizationStatus
    membership_id: UUID
    membership_status: MembershipStatus
    user_id: UUID
    aal: int = 1
    is_break_glass: bool = False
    reason: str | None = None
    #: The organization's access policy plus this membership's
    #: ``sessions_valid_after`` (Phase 8 extension). Empty by default, which
    #: denies nothing.
    access_policy: AccessPolicy = dataclasses.field(default_factory=AccessPolicy)

    def to_context(self) -> TenantContext:
        return TenantContext(
            organization_id=self.organization_id,
            membership_id=self.membership_id,
            user_id=self.user_id,
            organization_status=self.organization_status,
            aal=self.aal,
            is_break_glass=self.is_break_glass,
            reason=self.reason,
        )


class TenantResolver(Protocol):
    """Implemented by ``platform/organizations``, registered at app-ready."""

    def resolve(
        self, *, user_id: UUID, organization_slug: str
    ) -> TenantResolution | None:
        """Return the resolution, or None if there is no such pairing.

        None must be returned for *both* "no such organization" and "user is
        not a member". Distinguishing them here would let the caller build an
        organization-enumeration oracle out of the difference.
        """
        ...


_resolver: TenantResolver | None = None


def register_resolver(resolver: TenantResolver) -> None:
    """Called by ``organizations.apps.OrganizationsConfig.ready()``."""
    global _resolver
    _resolver = resolver


def get_resolver() -> TenantResolver | None:
    return _resolver


def organization_slug_from_path(path: str) -> str | None:
    """Extract the organization slug, or None if this is not a tenant URL."""
    match = _ORG_PATH_RE.match(path)
    return match.group("slug") if match else None


def resolve_tenant_from_path(request: HttpRequest) -> TenantResolution | None:
    """Resolve (principal, organization) for this request.

    Returns None when the URL is not tenant-scoped. Raises nothing for an
    unauthenticated user on a tenant URL - that is handled as a denial by the
    caller, which keeps the response uniform.
    """
    slug = organization_slug_from_path(request.path)
    if slug is None:
        return None

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return _anonymous_denial(slug)

    resolver = get_resolver()
    if resolver is None:  # pragma: no cover - startup check prevents this
        raise RuntimeError(
            "No TenantResolver is registered. platform/organizations should "
            "register one in AppConfig.ready()."
        )

    resolution = resolver.resolve(user_id=user.pk, organization_slug=slug)
    if resolution is None:
        return _anonymous_denial(slug)

    # Phase 5 fix: DatabaseTenantResolver has no session to read, so every
    # resolution came back with the dataclass default aal=1, regardless of
    # what request.session["w360_aal"] actually held. That made session
    # step-up (auth_views/mfa_views setting it to 2) inert for every
    # tenant-scoped `dangerous` permission check - authz.check() would
    # demand step-up, the user would complete it, and the very next request
    # would demand it again. The session, not the resolver, is the one place
    # that can answer "has this browser completed step-up", so it is
    # applied here, at the one call site with access to both (Task 39).
    aal = session_aal(getattr(request, "session", None))
    if aal != resolution.aal:
        resolution = dataclasses.replace(resolution, aal=aal)
    return resolution


#: Session keys for the assurance level. Written only through
#: ``reset_session_aal`` (login) and ``record_step_up`` (a verified MFA code),
#: read only through ``session_aal``.
SESSION_AAL_KEY: Final = "w360_aal"
SESSION_STEP_UP_AT_KEY: Final = "w360_step_up_at"
#: Phase 8 extension - facts an organization's access policy is judged on.
#: When this session signed in (epoch seconds; written by the login view).
SESSION_AUTHENTICATED_AT_KEY: Final = "w360_authenticated_at"
#: Whether this session has verified a second factor at least once. Unlike
#: AAL 2 it does not lapse: it answers "did this sign-in pass MFA", which is
#: what an organization's "require MFA" asks.
SESSION_MFA_VERIFIED_KEY: Final = "w360_mfa_verified"
#: Organization ids whose password policy this session's password did not
#: meet at sign-in (``auth_views.login_view``). Cleared by a password change.
SESSION_PASSWORD_NONCOMPLIANT_KEY: Final = "w360_password_noncompliant"  # noqa: S105 - a session key name


def session_aal(session: Any) -> int:
    """The assurance level this session holds *now*.

    AAL2 is time-boxed: 12-security.md section 2 requires re-auth within
    ``STEP_UP_MAX_AGE_SECONDS`` for ``dangerous`` permissions, so a step-up
    older than that - or one with no recorded time - reads as AAL1. Every
    unreadable value also reads as AAL1: defaulting *down* is fail-closed.
    """
    if session is None:
        return 1
    try:
        level = int(session.get(SESSION_AAL_KEY, 1) or 1)
        stepped_up_at = float(session.get(SESSION_STEP_UP_AT_KEY) or 0)
    except (TypeError, ValueError):
        return 1
    if level < 2:
        return 1
    age = time.time() - stepped_up_at
    if not 0 <= age <= settings.STEP_UP_MAX_AGE_SECONDS:
        return 1
    return level


def record_step_up(session: Any) -> None:
    session[SESSION_AAL_KEY] = 2
    session[SESSION_STEP_UP_AT_KEY] = time.time()
    session[SESSION_MFA_VERIFIED_KEY] = True


def reset_session_aal(session: Any) -> None:
    """Back to AAL1 - a fresh sign-in never inherits an earlier step-up."""
    session[SESSION_AAL_KEY] = 1
    session.pop(SESSION_STEP_UP_AT_KEY, None)
    session.pop(SESSION_MFA_VERIFIED_KEY, None)


def session_mfa_verified(session: Any) -> bool:
    """Whether this sign-in has verified a second factor (it does not lapse).

    Anything but an explicit ``True`` - no session, a missing or corrupted
    value - reads as not verified.
    """
    return session is not None and session.get(SESSION_MFA_VERIFIED_KEY) is True


def session_facts(request: HttpRequest, organization_id: UUID) -> SessionFacts:
    """What this request can vouch for, for ``access_policy.evaluate``.

    Unreadable values read as the *less* trusted answer: no sign-in time
    (too old whenever age matters) and not verified.
    """
    from workeasy360.platform.observability.client_ip import client_ip

    session = getattr(request, "session", None)
    authenticated_at: float | None = None
    mfa_verified = False
    noncompliant = False
    if session is not None:
        try:
            raw = session.get(SESSION_AUTHENTICATED_AT_KEY)
            authenticated_at = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            authenticated_at = None
        mfa_verified = session_mfa_verified(session)
        flagged = session.get(SESSION_PASSWORD_NONCOMPLIANT_KEY) or []
        noncompliant = isinstance(flagged, list) and str(organization_id) in flagged
    return SessionFacts(
        client_ip=client_ip(request),
        authenticated_at=authenticated_at,
        mfa_verified=mfa_verified,
        password_noncompliant=noncompliant,
    )


def _anonymous_denial(slug: str) -> TenantResolution:
    """A resolution that always fails the access check.

    Returning a denying resolution rather than None keeps one code path in the
    middleware: every denial goes through the same uniform response, so an
    unauthenticated request, a non-member and a non-existent organization are
    indistinguishable from the outside.
    """
    nil = UUID(int=0)
    return TenantResolution(
        organization_id=nil,
        organization_slug=slug,
        organization_status=OrganizationStatus.LOCKED,
        membership_id=nil,
        membership_status=MembershipStatus.REMOVED,
        user_id=nil,
    )
