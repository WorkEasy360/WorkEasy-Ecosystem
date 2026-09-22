"""The product integration manifest - what an external product submits.

This is the **boundary contract** between Workeasy360 and an application that
is not in this repository, is not written by this team and may not be written
in Python. It therefore lives in ``contracts/``: pure, Django-free, versioned,
and reachable by a test fixture or a documentation generator without booting
the platform (16-ecosystem-product-boundary.md section 7).

Validation is strict and rejects rather than repairs. A manifest describes
where a browser will be redirected with an authorization code, so every
lenient parse here is a potential open redirect or credential leak. The rules:

- **Unknown top-level fields are rejected**, not ignored. A product that sends
  ``redirect_uri`` when the field is ``redirect_uris`` must find out at
  registration time, not when its first user cannot log in.
- **Only https**, except for loopback outside production, which OAuth 2.1
  permits for native and development clients.
- **No wildcards, no query strings, no fragments** in redirect URIs. Exact
  matching (RFC 6749 section 3.1.2.2) is the only comparison the platform will
  ever perform, so anything that could only work under prefix matching is
  refused at the door rather than silently never matching.
- **No ``javascript:``, ``data:``, ``file:`` or any other scheme**, whatever
  it looks like. The check is an allowlist, because a denylist of dangerous
  schemes is a list somebody will one day be able to add to.
- **Userinfo (``user:pass@host``) is rejected**: ``https://evil.com@good.com``
  is a classic phishing shape that reads as ``good.com`` to a human.

``manifest_version`` is the manifest's own version, separate from the API
version in the URL. The API path changes when the *transport* changes; this
changes when the *shape a product submits* changes.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from workeasy360.contracts.values.identifiers import ProductCode

#: The only manifest version this platform accepts. A product submitting a
#: version it does not recognise is refused rather than best-effort parsed:
#: guessing at a future shape is how a security-relevant field gets dropped.
MANIFEST_VERSION: Final = "2026-09-01"

#: Every manifest version this platform can parse. One today; the frozenset
#: exists because accepting two during a migration window must not require
#: changing the type of this constant.
SUPPORTED_MANIFEST_VERSIONS: Final[frozenset[str]] = frozenset({MANIFEST_VERSION})

#: The scopes a product may request. Deliberately tiny.
#:
#: ``openid`` is mandatory and yields the subject, organization and product
#: binding - everything the SSO contract promises. ``profile`` and ``email``
#: add personal data and are therefore separate, denied by default, and
#: granted per integration rather than per request
#: (docs/phase-3/05-token-claims.md).
SUPPORTED_SCOPES: Final[tuple[str, ...]] = ("openid", "profile", "email")
REQUIRED_SCOPE: Final = "openid"

#: Capabilities a product declares it implements. Advertised back to the
#: ecosystem; none of them grants anything. A capability is a statement about
#: the product, never a permission.
SUPPORTED_CAPABILITIES: Final[tuple[str, ...]] = (
    # Verifies the ID token signature offline against the published JWKS
    # rather than calling the platform on every request.
    "offline_token_verification",
    # Re-checks entitlement on the documented cadence rather than trusting
    # the token for its whole lifetime.
    "entitlement_revalidation",
    # Publishes a health document at health_url for ecosystem display.
    "health_reporting",
)

#: Token-endpoint client authentication methods, RFC 8414 naming.
SUPPORTED_AUTH_METHODS: Final[tuple[str, ...]] = (
    "private_key_jwt",
    "client_secret_basic",
)

_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "manifest_version",
        "product_code",
        "display_name",
        "launch_url",
        "redirect_uris",
        "post_logout_redirect_uris",
        "scopes",
        "capabilities",
        "token_endpoint_auth_method",
        "health_url",
        "contact_email",
    }
)

_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "manifest_version",
    "product_code",
    "launch_url",
    "redirect_uris",
)

#: Bounds on every collection and string, so a manifest cannot be a denial of
#: service by volume. Registration is authenticated, but an authenticated
#: caller submitting a million redirect URIs is still an outage.
MAX_REDIRECT_URIS: Final = 10
MAX_URL_LENGTH: Final = 512
MAX_DISPLAY_NAME_LENGTH: Final = 128
MAX_CONTACT_EMAIL_LENGTH: Final = 254

_CONTACT_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class ManifestError(ValueError):
    """A manifest is malformed, unsafe or internally inconsistent.

    A ``ValueError`` rather than a ``Workeasy360Error`` because ``contracts``
    values must be usable with no platform present at all - a documentation
    generator or an external conformance harness validates manifests without
    Django. The registration service translates it into the HTTP error model.

    ``field`` names the offending key so the caller can fix it without
    guessing, which is safe: a manifest is data the caller just sent.
    """

    def __init__(self, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class ProductManifest:
    """A validated product integration manifest.

    Frozen: once parsed, a manifest is evidence of what was submitted. Code
    that needs a variant builds a new one, so the change is visible at the
    call site rather than mutating something another caller still holds.
    """

    manifest_version: str
    product_code: ProductCode
    display_name: str
    launch_url: str
    redirect_uris: tuple[str, ...]
    post_logout_redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    token_endpoint_auth_method: str
    health_url: str
    contact_email: str

    def as_dict(self) -> dict[str, Any]:
        """The round-trip form. What the registration API echoes back."""
        return {
            "manifest_version": self.manifest_version,
            "product_code": str(self.product_code),
            "display_name": self.display_name,
            "launch_url": self.launch_url,
            "redirect_uris": list(self.redirect_uris),
            "post_logout_redirect_uris": list(self.post_logout_redirect_uris),
            "scopes": list(self.scopes),
            "capabilities": list(self.capabilities),
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "health_url": self.health_url,
            "contact_email": self.contact_email,
        }

    def permits_redirect(self, candidate: str) -> bool:
        """Exact string match against the registered set. Nothing else.

        Not a prefix test, not a normalising comparison, not a host test.
        Every weakening of this predicate that has ever shipped anywhere has
        become an account-takeover advisory: ``https://good.com/cb`` matching
        ``https://good.com/cb/../../evil`` under normalisation, or
        ``https://good.com.evil.net`` under prefix matching. Exact equality
        has no such failure mode, and the cost is that a product registers
        every callback it uses - which it should be doing anyway.
        """
        return candidate in self.redirect_uris


def from_stored_manifest(payload: dict[str, Any]) -> ProductManifest:
    """Rebuild a manifest that this platform previously validated and stored.

    Deliberately does **not** re-validate. The rules in :func:`parse_manifest`
    are applied once, at the registration boundary, to data that arrived from
    outside. Re-applying them on every read would mean that *tightening* a
    rule - adding a scheme to the denylist, shortening a length bound -
    silently breaks every integration that was registered under the old rules,
    at runtime, with no deploy and no warning. A policy change would become an
    unannounced ecosystem outage.

    Tightened rules take effect by re-registration, which is an authenticated,
    audited act with an operator behind it. The stored row is trusted for the
    same reason every other control-plane row is: it is written only through
    the registration service, and anything able to write it directly can do
    considerably worse than edit a redirect URI.
    """
    return ProductManifest(
        manifest_version=str(payload["manifest_version"]),
        product_code=ProductCode(str(payload["product_code"])),
        display_name=str(payload.get("display_name", "")),
        launch_url=str(payload["launch_url"]),
        redirect_uris=tuple(str(u) for u in payload.get("redirect_uris", ())),
        post_logout_redirect_uris=tuple(
            str(u) for u in payload.get("post_logout_redirect_uris", ())
        ),
        scopes=tuple(str(s) for s in payload.get("scopes", ())),
        capabilities=tuple(str(c) for c in payload.get("capabilities", ())),
        token_endpoint_auth_method=str(payload["token_endpoint_auth_method"]),
        health_url=str(payload.get("health_url", "")),
        contact_email=str(payload.get("contact_email", "")),
    )


def parse_manifest(payload: Any, *, allow_insecure_urls: bool = False) -> ProductManifest:
    """Validate a submitted manifest, or raise :class:`ManifestError`.

    ``allow_insecure_urls`` permits ``http://`` on loopback hosts, for
    development and for the conformance fixtures. Production passes False and
    a system check asserts it (docs/phase-3/02-product-manifest.md section 6).
    """
    if not isinstance(payload, dict):
        raise ManifestError("A manifest must be a JSON object.")

    unknown = sorted(set(payload) - _MANIFEST_FIELDS)
    if unknown:
        # Rejected, not ignored. A typo in a security-relevant field name
        # would otherwise silently fall back to that field's default.
        raise ManifestError(
            f"Unknown manifest field(s): {', '.join(unknown)}. Known fields "
            f"are {', '.join(sorted(_MANIFEST_FIELDS))}.",
            field=unknown[0],
        )

    for name in _REQUIRED_FIELDS:
        if payload.get(name) in (None, "", [], ()):
            raise ManifestError(f"Manifest field {name!r} is required.", field=name)

    version = _as_str(payload["manifest_version"], "manifest_version")
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        raise ManifestError(
            f"Unsupported manifest_version {version!r}. This platform accepts "
            f"{', '.join(sorted(SUPPORTED_MANIFEST_VERSIONS))}.",
            field="manifest_version",
        )

    try:
        product_code = ProductCode(_as_str(payload["product_code"], "product_code"))
    except ValueError as exc:
        raise ManifestError(str(exc), field="product_code") from None

    launch_url = _validate_url(
        _as_str(payload["launch_url"], "launch_url"),
        field="launch_url",
        allow_insecure=allow_insecure_urls,
    )

    redirect_uris = _validate_redirect_uris(
        payload["redirect_uris"],
        field="redirect_uris",
        allow_insecure=allow_insecure_urls,
    )
    post_logout = _validate_redirect_uris(
        payload.get("post_logout_redirect_uris") or (),
        field="post_logout_redirect_uris",
        allow_insecure=allow_insecure_urls,
    )

    scopes = _validate_scopes(payload.get("scopes") or (REQUIRED_SCOPE,))
    capabilities = _validate_enumerated(
        payload.get("capabilities") or (),
        allowed=SUPPORTED_CAPABILITIES,
        field="capabilities",
    )

    auth_method = _as_str(
        payload.get("token_endpoint_auth_method") or SUPPORTED_AUTH_METHODS[0],
        "token_endpoint_auth_method",
    )
    if auth_method not in SUPPORTED_AUTH_METHODS:
        raise ManifestError(
            f"Unsupported token_endpoint_auth_method {auth_method!r}. "
            f"Supported: {', '.join(SUPPORTED_AUTH_METHODS)}.",
            field="token_endpoint_auth_method",
        )

    health_url = payload.get("health_url") or ""
    if health_url:
        health_url = _validate_url(
            _as_str(health_url, "health_url"),
            field="health_url",
            allow_insecure=allow_insecure_urls,
        )

    display_name = _as_str(payload.get("display_name") or "", "display_name")
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise ManifestError(
            f"display_name exceeds {MAX_DISPLAY_NAME_LENGTH} characters.",
            field="display_name",
        )

    contact_email = _as_str(payload.get("contact_email") or "", "contact_email")
    if contact_email:
        if len(contact_email) > MAX_CONTACT_EMAIL_LENGTH:
            raise ManifestError(
                f"contact_email exceeds {MAX_CONTACT_EMAIL_LENGTH} characters.",
                field="contact_email",
            )
        if not _CONTACT_EMAIL_RE.match(contact_email):
            raise ManifestError(
                "contact_email is not an email address.", field="contact_email"
            )

    return ProductManifest(
        manifest_version=version,
        product_code=product_code,
        display_name=display_name,
        launch_url=launch_url,
        redirect_uris=redirect_uris,
        post_logout_redirect_uris=post_logout,
        scopes=scopes,
        capabilities=capabilities,
        token_endpoint_auth_method=auth_method,
        health_url=health_url,
        contact_email=contact_email,
    )


# --- field validators -----------------------------------------------------


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(
            f"Manifest field {field!r} must be a string, got {type(value).__name__}.",
            field=field,
        )
    return value.strip()


def _validate_scopes(raw: Any) -> tuple[str, ...]:
    scopes = _validate_enumerated(raw, allowed=SUPPORTED_SCOPES, field="scopes")
    if REQUIRED_SCOPE not in scopes:
        raise ManifestError(
            f"scopes must include {REQUIRED_SCOPE!r}: an integration that does "
            f"not request an identity assertion has nothing to authenticate.",
            field="scopes",
        )
    return scopes


def _validate_enumerated(
    raw: Any, *, allowed: tuple[str, ...], field: str
) -> tuple[str, ...]:
    """Parse a list of values against a closed set, in the allowed order.

    Order is the *allowed* tuple's order, not the caller's, so two manifests
    requesting the same scopes in different orders compare equal. Duplicates
    collapse for the same reason.
    """
    if isinstance(raw, str) or not isinstance(raw, list | tuple):
        raise ManifestError(f"Manifest field {field!r} must be a list.", field=field)

    seen = set()
    for item in raw:
        value = _as_str(item, field)
        if value not in allowed:
            raise ManifestError(
                f"Unknown {field} value {value!r}. Supported: {', '.join(allowed)}.",
                field=field,
            )
        seen.add(value)
    return tuple(value for value in allowed if value in seen)


def _validate_redirect_uris(
    raw: Any, *, field: str, allow_insecure: bool
) -> tuple[str, ...]:
    if isinstance(raw, str) or not isinstance(raw, list | tuple):
        raise ManifestError(f"Manifest field {field!r} must be a list.", field=field)
    if len(raw) > MAX_REDIRECT_URIS:
        raise ManifestError(
            f"{field} may hold at most {MAX_REDIRECT_URIS} entries.", field=field
        )

    validated: list[str] = []
    for item in raw:
        uri = _validate_url(
            _as_str(item, field),
            field=field,
            allow_insecure=allow_insecure,
            is_redirect=True,
        )
        if uri in validated:
            raise ManifestError(f"Duplicate entry in {field}: {uri}.", field=field)
        validated.append(uri)
    return tuple(validated)


def _validate_url(
    value: str, *, field: str, allow_insecure: bool, is_redirect: bool = False
) -> str:
    """The single URL gate. Everything stored by the registry passes here.

    A stored URL is not a fetched URL - the platform performs no outbound
    request to any of these (docs/phase-3/11-security-threat-model.md T-11) -
    but ``launch_url`` and every redirect URI are places a *browser* is sent,
    and ``health_url`` is a place a future operator tool might look. The
    checks are therefore about where a user agent can be pointed, not about
    egress.
    """
    if len(value) > MAX_URL_LENGTH:
        raise ManifestError(
            f"{field}: URL exceeds {MAX_URL_LENGTH} characters.", field=field
        )
    if "*" in value:
        raise ManifestError(
            f"{field}: wildcards are not accepted. Redirect URIs are compared "
            f"by exact string equality, so a wildcard could never match.",
            field=field,
        )

    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ManifestError(f"{field}: unparseable URL ({exc}).", field=field) from None

    # Allowlist. A denylist of javascript:/data:/file: is a list somebody can
    # eventually find an entry missing from.
    if parts.scheme not in ("https", "http"):
        raise ManifestError(
            f"{field}: scheme {parts.scheme or '(none)'!r} is not permitted. "
            f"Only https is accepted (http on loopback outside production).",
            field=field,
        )

    try:
        hostname = parts.hostname
    except ValueError as exc:
        raise ManifestError(f"{field}: unparseable host ({exc}).", field=field) from None

    if not hostname:
        raise ManifestError(f"{field}: URL has no host.", field=field)

    # https://evil.example@good.example reads as good.example to a human and
    # resolves to evil.example. Refused outright.
    if parts.username or parts.password:
        raise ManifestError(
            f"{field}: URLs carrying credentials (user:pass@host) are refused.",
            field=field,
        )

    if parts.scheme == "http":
        if not allow_insecure:
            raise ManifestError(
                f"{field}: http is not permitted. An authorization code "
                f"delivered over plaintext is a disclosed credential.",
                field=field,
            )
        if not _is_loopback(hostname):
            raise ManifestError(
                f"{field}: http is permitted only for loopback hosts "
                f"(127.0.0.1, ::1, localhost), not {hostname!r}.",
                field=field,
            )

    if is_redirect:
        if parts.query:
            raise ManifestError(
                f"{field}: a redirect URI may not carry a query string. The "
                f"platform appends 'code' and 'state' to it, and a "
                f"pre-existing query makes the result ambiguous.",
                field=field,
            )
        if parts.fragment:
            raise ManifestError(
                f"{field}: a redirect URI may not carry a fragment (RFC 6749 "
                f"section 3.1.2).",
                field=field,
            )

    return value


def _is_loopback(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname.strip("[]")).is_loopback
    except ValueError:
        return False
