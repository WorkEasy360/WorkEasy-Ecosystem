"""The OpenID Provider metadata document, and the endpoint URLs it names.

Two jobs in one small module, because they must not disagree.

**Endpoint construction.** Every absolute URL the ecosystem emits - the
discovery document's entries, the audience a client assertion must name - is
built here from the configured issuer. Never from ``request.build_absolute_uri``:
that reads the ``Host`` header, which is client-supplied and therefore never
truthful (TB-1). A forged Host would otherwise let an attacker publish a
discovery document pointing at their own token endpoint.

**The document itself.** RFC 8414 / OpenID Connect Discovery 1.0. It exists so
a product integrates by reading one URL instead of the platform's source, and
so that moving an endpoint is a deployment change rather than a coordinated
release across every product (TASK 27's requirement, and the reason the
conformance suite fetches it rather than hard-coding paths).

Every advertised capability below is one this platform actually implements,
asserted by a test. A discovery document that promises more than the server
does is worse than none: an integrator's library will use the advertised
feature and fail in a way that looks like their bug.
"""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings

from workeasy360.contracts.values.manifest import SUPPORTED_SCOPES
from workeasy360.platform.ecosystem import protocol

#: Paths, relative to the issuer. Single source: ``interfaces/`` routes them
#: and a fitness test asserts that every path named here actually resolves,
#: so the document cannot advertise an endpoint that 404s.
PATHS: Final[dict[str, str]] = {
    "jwks": "/.well-known/jwks.json",
    "authorization": "/api/v1/integrations/oauth/authorize",
    "token": "/api/v1/integrations/oauth/token",
    "userinfo": "/api/v1/integrations/oauth/userinfo",
    "entitlement_verify": "/api/v1/integrations/entitlements/verify",
}


def endpoint(name: str) -> str:
    """The absolute URL of a named endpoint."""
    from workeasy360.platform.ecosystem import tokens

    return f"{tokens.issuer()}{PATHS[name]}"


def document() -> dict[str, Any]:
    """The OpenID Provider metadata.

    The omissions are as deliberate as the entries:

    - no ``registration_endpoint``: dynamic client registration (RFC 7591)
      would let anyone create a client. Registration here is an authenticated,
      audited, staff-performed control-plane act.
    - no ``refresh_token`` in ``grant_types_supported``: there are none, by
      decision (tokens.py).
    - ``response_types_supported`` is ``["code"]`` only: the implicit and
      hybrid flows put tokens in URLs, and OAuth 2.1 removes them.
    - ``code_challenge_methods_supported`` is ``["S256"]`` only: ``plain``
      sends the verifier in the same request as the challenge and protects
      nothing.
    """
    from workeasy360.platform.ecosystem import keys, tokens

    return {
        "issuer": tokens.issuer(),
        "authorization_endpoint": endpoint("authorization"),
        "token_endpoint": endpoint("token"),
        "userinfo_endpoint": endpoint("userinfo"),
        "jwks_uri": endpoint("jwks"),
        "scopes_supported": list(SUPPORTED_SCOPES),
        "response_types_supported": [protocol.RESPONSE_TYPE_CODE],
        "response_modes_supported": ["query"],
        "grant_types_supported": [protocol.GRANT_TYPE_AUTHORIZATION_CODE],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": list(keys.SUPPORTED_ALGORITHMS),
        "token_endpoint_auth_methods_supported": [
            "private_key_jwt",
            "client_secret_basic",
            "client_secret_post",
        ],
        "token_endpoint_auth_signing_alg_values_supported": list(
            keys.SUPPORTED_ALGORITHMS
        ),
        "code_challenge_methods_supported": [protocol.PKCE_METHOD_S256],
        "claims_supported": [
            "iss",
            "sub",
            "aud",
            "iat",
            "nbf",
            "exp",
            "jti",
            "typ",
            "nonce",
            # The three ecosystem claims. Documented in full, with their
            # meaning and stability guarantee, in
            # docs/phase-3/05-token-claims.md.
            "organization_id",
            "product_code",
            "aal",
            # Scope-gated, and only for integrations registered for them.
            "email",
            "email_verified",
            "name",
        ],
        "request_parameter_supported": False,
        "request_uri_parameter_supported": False,
        "claims_parameter_supported": False,
        # Not part of the OIDC schema. Advertised because a product needs it
        # to implement the revalidation cadence the platform expects, and
        # discovering it beats hard-coding it in nine repositories.
        "workeasy360_entitlement_endpoint": endpoint("entitlement_verify"),
        "workeasy360_entitlement_revalidate_after": int(
            settings.OIDC_ENTITLEMENT_REVALIDATE_AFTER_S
        ),
        "workeasy360_error_codes": sorted(protocol.ERROR_CODES),
    }
