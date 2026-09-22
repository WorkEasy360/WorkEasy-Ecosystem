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
