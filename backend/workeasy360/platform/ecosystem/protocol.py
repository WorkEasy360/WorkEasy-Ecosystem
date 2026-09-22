"""The OAuth 2.0 / OpenID Connect wire protocol: error codes and constants.

Separate from ``contracts/errors.py`` because the wire shape is different and
mandated. RFC 6749 section 5.2 specifies ``{"error": ..., "error_description":
...}`` with a closed set of ``error`` values; a conforming client library
branches on that field. The platform's own error envelope
(``{"code": ..., "detail": ...}``) is for humans and for the administration
API. Emitting one where the other is expected means either a non-conforming
OAuth response or an administration API that speaks OAuth at an operator.

**What an error may say.** Two rules, both from RFC 6749 section 4.1.2.1 and
12-security.md section 18:

1. ``error`` is from the registered set. A bespoke code tells an integrator
   nothing their library can act on.
2. ``error_description`` is a *fixed string per condition*. It never contains
   the value the caller sent, never says whether an organization or user
   exists, and never distinguishes "no such client" from "wrong secret" -
   each of those is an oracle. The correlation id is returned instead, which
   lets support find the exact request without disclosing anything.
"""

from __future__ import annotations

from typing import Any, Final

# --- registered error codes (RFC 6749 section 5.2, OIDC Core section 3.1.2.6)

INVALID_REQUEST: Final = "invalid_request"
INVALID_CLIENT: Final = "invalid_client"
INVALID_GRANT: Final = "invalid_grant"
UNSUPPORTED_GRANT_TYPE: Final = "unsupported_grant_type"
INVALID_SCOPE: Final = "invalid_scope"
SERVER_ERROR: Final = "server_error"

# `access_denied` and `unauthorized_client` are registered by RFC 6749 and are
# deliberately **not** declared here. Nothing raises either: a denial in this
# platform always has a specific, actionable cause - membership, organization
# status, entitlement, suspension - and `access_denied` is the code you emit
# when you have decided not to say which. Declaring them anyway put two codes
# in the published `workeasy360_error_codes` list that the server could not
# produce, which is the failure this module's own docstring warns about from
# the other direction. If one is ever genuinely needed, add it here and
# `test_every_published_error_code_is_one_the_platform_can_emit` will hold the
# list honest.
TEMPORARILY_UNAVAILABLE: Final = "temporarily_unavailable"
LOGIN_REQUIRED: Final = "login_required"
INVALID_TOKEN: Final = "invalid_token"  # noqa: S105 - an RFC 6750 error code

#: Workeasy360-specific authorization failures.
#:
#: RFC 6749 allows an authorization server to define additional codes, and
#: ``access_denied`` alone would collapse five genuinely different remedies
#: into one message: pay, be invited, ask an admin, wait, contact support. A
#: product cannot render the right call to action from a single code, which
#: is the same argument ``EntitlementReason`` makes inside the platform.
#:
#: They are namespaced with a leading ``workeasy360_`` nowhere: the codes are
#: short because they appear in a redirect URL, and they are documented in
#: docs/phase-3/04-oidc-contract.md section 8 so a product can switch on them.
MEMBERSHIP_INACTIVE: Final = "membership_inactive"
ORGANIZATION_INACTIVE: Final = "organization_inactive"
PRODUCT_NOT_ENTITLED: Final = "product_not_entitled"
INTEGRATION_SUSPENDED: Final = "integration_suspended"
ORGANIZATION_REQUIRED: Final = "organization_required"
#: The organization holds the product but this member holds no seat in it.
#: Distinct from ``product_not_entitled`` so a product's client library (and
#: the launcher, which asks the identical question before it ever reaches
#: here - see ``platform/launcher``) can render "ask your administrator for a
#: seat" rather than "this organization has not purchased this product".
#: ``EntitlementReason.NO_SEAT`` was reserved for exactly this since Phase 3
#: (docs/phase-3/17-audit-findings.md, "Seat trigger"), so adding it is not a
#: breaking change to an already-documented reason set.
PRODUCT_NO_SEAT: Final = "product_no_seat"

#: Every code this platform can emit - and nothing else. Published in the
#: discovery document, and asserted against the actual raise sites by
#: ``test_every_published_error_code_is_one_the_platform_can_emit``, which
#: reads them out of the AST. A list that is merely *maintained* alongside the
#: code is a list that is eventually wrong in the direction that matters:
#: advertising a code no client will ever receive teaches an integrator to
#: write a branch that never runs.
ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        INVALID_REQUEST,
        INVALID_CLIENT,
        INVALID_GRANT,
        UNSUPPORTED_GRANT_TYPE,
        INVALID_SCOPE,
        SERVER_ERROR,
        TEMPORARILY_UNAVAILABLE,
        LOGIN_REQUIRED,
        INVALID_TOKEN,
        MEMBERSHIP_INACTIVE,
        ORGANIZATION_INACTIVE,
        PRODUCT_NOT_ENTITLED,
        INTEGRATION_SUSPENDED,
        ORGANIZATION_REQUIRED,
        PRODUCT_NO_SEAT,
    }
)

# Whether an error may be delivered by redirecting the user agent is NOT a
# property of the error code, and deliberately is not modelled as one here.
#
# RFC 6749 section 4.1.2.1 ties it to *when* the failure happened: if the
# client or the redirect URI could not be verified, the server does not know
# where the browser is supposed to go, and sending it anywhere is the open
# redirect. Once both are verified, every later failure belongs at the
# product. The same code - `invalid_request` - is therefore rendered when
# raised for a missing redirect_uri and redirected when raised for a missing
# code_challenge.
#
# That distinction lives in authorization.py, as the split between
# `resolve_client` (rendered) and `parse_request` (redirected). Encoding it
# as a set of codes here would be wrong in both directions at once.

GRANT_TYPE_AUTHORIZATION_CODE: Final = "authorization_code"
RESPONSE_TYPE_CODE: Final = "code"
PKCE_METHOD_S256: Final = "S256"
CLIENT_ASSERTION_TYPE_JWT_BEARER: Final = (
    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
)


class OAuthError(Exception):
    """A protocol-level failure, rendered in the RFC 6749 shape.

    ``description`` is a constant chosen by the raising site, never
    interpolated with caller input. Interpolation is how an error message
    becomes a reflection point and how "no user with that email" gets shipped.
    """

    def __init__(
        self,
        error: str,
        description: str = "",
        *,
        http_status: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        if error not in ERROR_CODES:
            # A programming error, caught in development. An unregistered
            # code reaching a product is a contract break.
            raise ValueError(f"{error!r} is not a registered OAuth error code.")
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.http_status = http_status
        self.headers = headers or {}

    def as_dict(self, *, correlation_id: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        if correlation_id:
            body["correlation_id"] = correlation_id
        return body


def invalid_client(description: str = "Client authentication failed.") -> OAuthError:
    """401 with ``WWW-Authenticate``, as RFC 6749 section 5.2 requires.

    One description for every cause - unknown client id, wrong secret, bad
    assertion signature, revoked credential, expired credential. Which of
    those it was is in the logs, not in the response: telling a caller that
    the client id was right but the secret was wrong is a client-enumeration
    oracle and halves the work of a credential-stuffing run.
    """
    return OAuthError(
        INVALID_CLIENT,
        description,
        http_status=401,
        headers={"WWW-Authenticate": 'Basic realm="workeasy360", charset="UTF-8"'},
    )
