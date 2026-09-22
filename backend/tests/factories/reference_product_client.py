"""A reference Workeasy360 integration client, written as an outsider would.

This file stands in for a product that lives in another repository, is built
by another team and may be written in another language. It is the executable
form of docs/phase-3/09-product-integration-guide.md: if the guide is wrong,
this stops working.

**The constraint that gives it its value:** it imports nothing from
``workeasy360``. Not a model, not a service, not a constant, not an enum, not
a contract. Everything it knows about the platform it learns from the
discovery document at runtime, exactly as an external application would.
``test_reference_client_imports_no_platform_module`` asserts that by reading
this file's AST, so the constraint cannot be quietly relaxed later.

Its only non-standard dependency is ``PyJWT``, which is a JOSE library and
not a Workeasy360 artefact - a product in Go would use ``golang-jwt`` and a
product in TypeScript would use ``jose``, and both would implement exactly
what ``verify_id_token`` below implements.

Transport is Django's test client, which is the only concession to running
inside this repository's test process. It is a way to make an HTTP request;
it carries no platform knowledge.

``ProductStore`` at the bottom is the product's own database: a dict keyed by
``(organization_id, ...)``. It is here to make the point concrete that the
product owns its data, scopes it by the ecosystem tenant identity, and shares
no table with the platform.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt


class IntegrationError(RuntimeError):
    """The product's own error type. It knows nothing of Workeasy360's."""

    def __init__(self, message: str, *, error: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.error = error
        self.status = status


@dataclass
class ReferenceProductClient:
    """An independently developed product integrating with Workeasy360.

    Holds exactly what a real product's deployment configuration would hold:
    the issuer, its client id, its own private key, and the redirect URI it
    registered. Everything else is discovered.
    """

    http: Any
    issuer: str
    client_id: str
    redirect_uri: str
    private_key_pem: str = ""
    credential_kid: str = ""
    client_secret: str = ""
    signing_algorithm: str = "RS256"

    #: Cached discovery metadata and key set, refreshed on demand. A real
    #: product caches both - which is exactly why the platform publishes a new
    #: signing key before it starts signing with it.
    _metadata: dict[str, Any] = field(default_factory=dict)
    _jwks: dict[str, Any] = field(default_factory=dict)

    # --- discovery --------------------------------------------------------

    def discover(self, *, refresh: bool = False) -> dict[str, Any]:
        """Fetch the OpenID Provider metadata.

        The product hard-codes the issuer and derives every endpoint from
        this document. Nothing below constructs a platform URL by hand, which
        is what makes the platform able to move an endpoint without a
        coordinated release across nine repositories.
        """
        if self._metadata and not refresh:
            return self._metadata
        response = self.http.get(f"{self.issuer}/.well-known/openid-configuration")
        if response.status_code != 200:
            raise IntegrationError("Discovery failed", status=response.status_code)
        self._metadata = response.json()
        if self._metadata.get("issuer") != self.issuer:
            # A product must refuse a document that names a different issuer:
            # it would be describing somebody else's authorization server.
            raise IntegrationError("Discovery document names a different issuer")
        return self._metadata

    def fetch_jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._jwks and not refresh:
            return self._jwks
        response = self.http.get(_path(self.discover()["jwks_uri"]))
        if response.status_code != 200:
            raise IntegrationError("JWKS fetch failed", status=response.status_code)
        self._jwks = response.json()
        return self._jwks

    # --- the authorization code flow --------------------------------------

    def begin_authorization(
        self, *, organization_id: str, scope: str = "openid"
    ) -> dict[str, str]:
        """Generate PKCE, state and nonce, and build the authorization URL.

        The three values returned to the caller are the product's own session
        state. A real product stores them in the user's browser session and
        compares them on the callback; this returns them so the test can
        demonstrate exactly that.
        """
        verifier = _pkce_verifier()
        request = {
            "state": secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
            "code_verifier": verifier,
        }
        query = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": request["state"],
            "nonce": request["nonce"],
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            # The organization the user selected in the Workeasy360 launcher,
            # which arrived on this product's launch URL. The product does not
            # get to choose it and the platform verifies the user's membership
            # in it regardless of what is sent here.
            "organization": organization_id,
        }
        request["url"] = _path(self.discover()["authorization_endpoint"])
        request["query"] = query
        return request

    def handle_callback(self, location: str, *, expected_state: str) -> str:
        """Extract the code from the redirect, after checking ``state``.

        The state comparison is the client's CSRF defence and is the product's
        responsibility, not the platform's - the platform only echoes the
        value back. A product that skips it can be made to complete an
        attacker's authorization in a victim's session.
        """
        params = parse_qs(urlsplit(location).query)
        if "error" in params:
            raise IntegrationError(params["error"][0], error=params["error"][0])
        state = (params.get("state") or [""])[0]
        if not secrets.compare_digest(state, expected_state):
            raise IntegrationError("state mismatch - refusing the callback")
        code = (params.get("code") or [""])[0]
        if not code:
            raise IntegrationError("no authorization code in the callback")
        return code

    def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        """Redeem the code at the token endpoint, authenticating as this client."""
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        body.update(self._client_authentication())

        response = self.http.post(_path(self.discover()["token_endpoint"]), data=body)
        payload = response.json()
        if response.status_code != 200:
            raise IntegrationError(
                payload.get("error_description", "token exchange failed"),
                error=payload.get("error", ""),
                status=response.status_code,
            )
        return payload

    # --- verification: what every product must implement -------------------

    def verify_id_token(
        self, id_token: str, *, expected_nonce: str = ""
    ) -> dict[str, Any]:
        """Verify the identity assertion offline. The certification checklist.

        Every step here is one a product must perform, and each closes a named
        attack in docs/phase-3/11-security-threat-model.md:

        1. Select the key by the token's ``kid`` from the cached key set, and
           reject an unknown one rather than fetching whatever the token
           points at.
        2. Verify with the algorithm **from the key set entry**, never from
           the token header - the algorithm-confusion attack.
        3. Verify ``iss`` against the configured issuer - token substitution
           from another provider.
        4. Verify ``aud`` against this product's own client id - the
           audience-confusion attack, where a token minted for another
           product is presented here.
        5. Verify ``exp`` and ``nbf`` with a small tolerance.
        6. Verify ``nonce`` against the one this product generated - replay of
           a token captured from another session.
        7. Verify ``typ`` - an access token is not an identity assertion.
        """
        header = jwt.get_unverified_header(id_token)
        key_entry = self._key_for(header.get("kid", ""))
        algorithm = key_entry["alg"]

        try:
            claims = jwt.decode(
                id_token,
                jwt.PyJWK(key_entry).key,
                algorithms=[algorithm],
                audience=self.client_id,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "typ"]},
            )
        except jwt.PyJWTError as exc:
            # Every verification failure becomes one product-level error.
            # A real product does this too: its callers care that the
            # assertion is unusable, not which of the seven checks rejected
            # it, and letting a JOSE library's exception hierarchy leak into
            # the application means the handling drifts as the library
            # changes.
            raise IntegrationError(
                f"identity token rejected: {type(exc).__name__}"
            ) from exc

        if claims.get("typ") != "id_token":
            raise IntegrationError("not an identity token")
        if expected_nonce and claims.get("nonce") != expected_nonce:
            raise IntegrationError("nonce mismatch - refusing the token")
        return claims

    def _key_for(self, kid: str) -> dict[str, Any]:
        """Find a key by id, refreshing the cached set once before failing.

        The single refresh is what makes platform key rotation invisible to a
        product: a token signed by a key published after this product last
        fetched the set resolves on the retry. Refreshing *unconditionally*
        would instead let any token with an unknown kid drive a fetch, which
        is a request-amplification lever.
        """
        for attempt in (False, True):
            for key in self.fetch_jwks(refresh=attempt).get("keys", []):
                if key.get("kid") == kid:
                    return key
        raise IntegrationError(f"no published key with kid {kid!r}")

    # --- the entitlement question ------------------------------------------

    def verify_entitlement(
        self, *, organization_id: str, write: bool = False
    ) -> dict[str, Any]:
        """Ask the platform whether this organization may use this product.

        Note what is absent: **the product does not name itself.** There is no
        ``product_code`` field to send. The platform determines the subject
        from the credential that authenticated the request, which is why this
        call cannot be pointed at another product even by a client that tries.

        Form-encoded, like the token endpoint, so client authentication is the
        same fields in the same place.
        """
        body = {"organization_id": organization_id, "write": "true" if write else "false"}
        body.update(self._client_authentication())

        response = self.http.post(
            _path(self.discover()["workeasy360_entitlement_endpoint"]), data=body
        )
        payload = response.json()
        if response.status_code != 200:
            raise IntegrationError(
                payload.get("error_description", "verification failed"),
                error=payload.get("error", ""),
                status=response.status_code,
            )
        return payload

    def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        response = self.http.get(
            _path(self.discover()["userinfo_endpoint"]),
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        payload = response.json()
        if response.status_code != 200:
            raise IntegrationError(
                payload.get("error_description", "userinfo failed"),
                error=payload.get("error", ""),
                status=response.status_code,
            )
        return payload

    # --- client authentication ---------------------------------------------

    def _client_authentication(self) -> dict[str, str]:
        """private_key_jwt when this product holds a key, else a secret."""
        if self.private_key_pem:
            return {
                "client_assertion_type": (
                    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                ),
                "client_assertion": self.build_client_assertion(),
            }
        return {"client_id": self.client_id, "client_secret": self.client_secret}

    def build_client_assertion(self, *, audience: str = "", jti: str = "") -> str:
        """RFC 7523 section 3. Signed with the product's own private key.

        The key never leaves the product, and the platform stores only its
        public half - so a breach of the platform's database yields nothing
        that can authenticate as this product.
        """
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self.client_id,
                "sub": self.client_id,
                "aud": audience or self.discover()["token_endpoint"],
                "iat": now,
                "exp": now + 60,
                "jti": jti or uuid.uuid4().hex,
            },
            self.private_key_pem,
            algorithm=self.signing_algorithm,
            headers={"kid": self.credential_kid},
        )


# --- the product's own store ---------------------------------------------


@dataclass
class ProductStore:
    """The product's database, in miniature.

    Every record is keyed by ``organization_id`` - the ecosystem tenant
    identity the platform minted - and the product enforces its own isolation
    on reads. That responsibility is the product's, not the platform's: the
    five-layer model in 03-multi-tenancy.md protects *platform* data, and a
    product that leaks tenant data damages the ecosystem's trust as well as
    its own (16-ecosystem-product-boundary.md section 9).
    """

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add(self, organization_id: str, record: dict[str, Any]) -> None:
        self.rows.setdefault(organization_id, []).append(record)

    def for_organization(self, organization_id: str) -> list[dict[str, Any]]:
        """Scoped read. The product's equivalent of the platform's Layer 2."""
        return list(self.rows.get(organization_id, ()))


# --- small helpers --------------------------------------------------------


def _pkce_verifier() -> str:
    """RFC 7636 section 4.1: 43-128 characters of unreserved alphabet."""
    return secrets.token_urlsafe(48)


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636 section 4.2: BASE64URL(SHA256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _path(absolute_url: str) -> str:
    """Reduce a discovered absolute URL to a path for the test transport.

    A real product would request the absolute URL. Django's test client takes
    a path, so this is the transport adapter and nothing more - the product
    still learns *which* URL from discovery rather than constructing it.
    """
    parts = urlsplit(absolute_url)
    return parts.path or "/"
