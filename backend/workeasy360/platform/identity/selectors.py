"""Read queries over identity, returning plain data.

Exists so that nothing outside ``platform/identity`` queries the ``User``
table. A queryset over another module's model welds the caller to its schema
(F-6), and ``User`` is the model most likely to grow fields that must not
leave the module.

One function today, and it is the one place personal data crosses the
ecosystem boundary - which is exactly why it is here rather than inline in a
view, where the scope gate would be one refactor from being dropped.
"""

from __future__ import annotations

from typing import Any

#: OIDC standard claims, mapped to the scope that releases each one.
#:
#: A closed table rather than a conditional chain, so "what does the profile
#: scope actually release?" has a single, greppable answer and adding a claim
#: requires choosing its scope.
_SCOPE_CLAIMS: dict[str, tuple[str, ...]] = {
    "profile": ("name",),
    "email": ("email", "email_verified"),
}


def profile_claims(*, user_id: Any, scopes: tuple[str, ...]) -> dict[str, Any]:
    """The personal claims the granted scopes permit, and no others.

    Returns ``{}`` when only ``openid`` was granted - which is the default for
    a newly registered integration, because a product that only needs to know
    *which* user should not be handed *who* the user is
    (docs/phase-3/05-token-claims.md section 4).

    An unknown user yields ``{}`` rather than raising. The caller has already
    verified the subject against a live membership; a user row that has since
    gone is a reason to omit optional decoration, not to fail an
    authentication that was legitimately granted.
    """
    from workeasy360.platform.identity.models import User

    wanted: set[str] = set()
    for scope in scopes:
        wanted.update(_SCOPE_CLAIMS.get(scope, ()))
    if not wanted:
        return {}

    # No tenant scope is entered and none is bypassed. ``User`` is global and
    # carries no ``organization_id``, so it is not a ``TenantScopedModel`` and
    # its manager is an ordinary one - this needs no ``unscoped_access``, and
    # taking one would suggest a tenant boundary is being crossed when there
    # is none (identity/models.py: "control-plane-adjacent").
    #
    # What guards this read is upstream: the caller reaches it only after
    # verifying an active membership for this exact user, and only for the
    # scopes that membership's integration was registered for.
    row = (
        User.objects.filter(id=user_id)
        .values("email", "email_original", "full_name", "email_verified_at")
        .first()
    )
    if row is None:
        return {}

    available = {
        # The normalised address, which is the login identifier and the one
        # that is unique. email_original is display casing only and two users
        # can never differ by it alone.
        "email": row["email"],
        "email_verified": row["email_verified_at"] is not None,
        "name": row["full_name"],
    }
    # Empty strings are omitted rather than sent: OIDC Core section 5.3.2 says
    # a claim with no value should be left out, and a product rendering ""
    # as a display name shows a blank where it expected a person.
    return {k: v for k, v in available.items() if k in wanted and v not in ("", None)}


def active_user_id_for_email(email: str) -> Any:
    """The id of the *active* account with this address, or ``None``.

    For a privileged caller that must attach an existing identity to
    something (a partner member) and is itself authorised to do so. The one
    ``None`` covers "no such account" and "disabled", so a caller cannot use
    the difference as an account-existence probe.
    """
    from workeasy360.platform.identity.email import normalize_email
    from workeasy360.platform.identity.models import User

    return (
        User.objects.filter(email=normalize_email(email or ""), status="active")
        .values_list("id", flat=True)
        .first()
    )


def has_usable_password(user_id: Any) -> bool:
    """Whether this account can already sign in with a password.

    ``False`` for an identity created uncredentialed - a person a partner or an
    operator *named* before they ever signed in - which is exactly the state in
    which an emailed one-time link must be what sets the first password. Reports
    a fact about the account, never the hash.
    """
    from workeasy360.platform.identity.models import User

    user = User.objects.filter(id=user_id).first()
    return bool(user is not None and user.has_usable_password())


def contact_address(user_id: Any) -> str:
    """The address to write to for this account, as its owner typed it (display
    casing preserved: some providers do treat the local part as case-sensitive).
    Empty when there is no such account."""
    from workeasy360.platform.identity.models import User

    row = User.objects.filter(id=user_id).values("email", "email_original").first()
    return (row["email_original"] or row["email"]) if row else ""


def user_facts_for_email(email: str) -> dict[str, Any] | None:
    """A user's id and account status by address, for a *diagnostic*.

    Returns state, never a credential: no hash, no factor, no session. Unlike
    ``active_user_id_for_email`` it reports disabled and locked accounts,
    because "why can this person not sign in?" is exactly the question.
    """
    from workeasy360.platform.identity.email import normalize_email
    from workeasy360.platform.identity.models import User

    row = (
        User.objects.filter(email=normalize_email(email or ""))
        .values("id", "status", "locked_until", "email_verified_at")
        .first()
    )
    if row is None:
        return None
    from django.utils import timezone

    return {
        "user_id": row["id"],
        "status": row["status"],
        "is_locked_out": bool(
            row["locked_until"] and row["locked_until"] > timezone.now()
        ),
        "email_verified": row["email_verified_at"] is not None,
    }


def account_statistics(*, since: Any) -> dict[str, int]:
    """Platform-wide account counts for the Super Admin dashboard (Phase 8
    extension). Counts only - no identities. The control-plane system user
    is not a person and is excluded."""
    from django.db.models import Count, Q

    from workeasy360.contracts.values.control_plane import PLATFORM_SYSTEM_USER_EMAIL
    from workeasy360.platform.identity.models import User

    counts = User.objects.exclude(email=PLATFORM_SYSTEM_USER_EMAIL).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status="active")),
        new=Count("id", filter=Q(created_at__gte=since)),
        signed_in=Count("id", filter=Q(last_login_at__gte=since)),
    )
    return {key: int(value or 0) for key, value in counts.items()}
