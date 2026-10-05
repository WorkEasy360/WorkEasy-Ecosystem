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
import unicodedata
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from workeasy360.contracts.events.namespaces import MAX_EVENT_VERSION, product_owns
from workeasy360.contracts.values.identifiers import ProductCode

#: The current manifest version. A product submitting a version it does not
#: recognise is refused rather than best-effort parsed: guessing at a future
#: shape is how a security-relevant field gets dropped.
#:
#: ``2026-10-01`` (Phase 6) adds one optional field, ``events``: the event
#: types the product publishes into the ecosystem. It is the contract that
#: makes a product's events routable at all - an event type not declared
#: here is refused at ingestion.
MANIFEST_VERSION: Final = "2026-10-01"

#: The Phase 3 shape. Still accepted, unchanged: every product registered
#: before Phase 6 keeps working without re-registering, and simply publishes
#: no events until it moves to the current version.
LEGACY_MANIFEST_VERSION: Final = "2026-09-01"

#: Every manifest version this platform can parse. The frozenset exists so
#: that accepting two during a migration window - which is now - did not
#: require changing the type of this constant.
SUPPORTED_MANIFEST_VERSIONS: Final[frozenset[str]] = frozenset(
    {LEGACY_MANIFEST_VERSION, MANIFEST_VERSION}
)

#: Versions whose shape includes ``events``.
_VERSIONS_WITH_EVENTS: Final[frozenset[str]] = frozenset({MANIFEST_VERSION})

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
        "events",
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
#: A product declares every event type it publishes; a hundred is far past
#: any real product and keeps the manifest from being a bulk data channel.
MAX_EVENT_DECLARATIONS: Final = 100
MAX_EVENT_DESCRIPTION_LENGTH: Final = 200
_EVENT_DECLARATION_FIELDS: Final[frozenset[str]] = frozenset(
    {"type", "version", "description"}
)

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
class EventDeclaration:
    """One event type a product publishes (manifest ``2026-10-01``).

    Name and version only, plus a human description. Deliberately **no
    schema**: the product owns what its events mean and contain, and a
    schema declared here would make Workeasy360 the owner of a product's
    domain model (A14). The platform bounds the payload's size and shape at
    ingestion; it does not interpret it.
    """

    type: str
    version: int
    description: str = ""

    @property
    def ref(self) -> str:
        return f"{self.type}.v{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "version": self.version,
            "description": self.description,
        }


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
    #: Event types this product publishes. Empty for a legacy manifest.
    events: tuple[EventDeclaration, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The round-trip form. What the registration API echoes back.

        ``events`` is present only when declared, so a legacy (``2026-09-01``)
        manifest round-trips to exactly the shape it was submitted in.
        """
        body: dict[str, Any] = {
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
        if self.events:
            body["events"] = [e.as_dict() for e in self.events]
        return body

    def declares_event(self, event_type: str, version: int) -> bool:
        """Whether this product registered ``event_type`` at ``version``."""
        return any(e.type == event_type and e.version == version for e in self.events)

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
        events=tuple(
            EventDeclaration(
                type=str(e["type"]),
                version=int(e["version"]),
                description=str(e.get("description", "")),
            )
            for e in payload.get("events", ())
        ),
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

    raw_events = payload.get("events")
    if raw_events is not None and version not in _VERSIONS_WITH_EVENTS:
        raise ManifestError(
            f"Manifest field 'events' requires manifest_version "
            f"{MANIFEST_VERSION!r}; {version!r} predates it.",
            field="events",
        )
    events = _validate_events(raw_events or (), product_code=str(product_code))

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
        events=events,
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


def _validate_events(raw: Any, *, product_code: str) -> tuple[EventDeclaration, ...]:
    """Validate declared event types against the product's own namespace.

    The namespace rule is applied here, at registration, as well as at
    ingestion: a manifest declaring ``product.books.*`` for the ``crm``
    product is refused at the door rather than accepted and then never
    publishable.
    """
    if isinstance(raw, str) or not isinstance(raw, list | tuple):
        raise ManifestError("Manifest field 'events' must be a list.", field="events")
    if len(raw) > MAX_EVENT_DECLARATIONS:
        raise ManifestError(
            f"events may hold at most {MAX_EVENT_DECLARATIONS} entries.",
            field="events",
        )

    declared: list[EventDeclaration] = []
    seen: set[tuple[str, int]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ManifestError("Each events entry must be an object.", field="events")
        unknown = sorted(set(item) - _EVENT_DECLARATION_FIELDS)
        if unknown:
            raise ManifestError(
                f"Unknown events entry field(s): {', '.join(unknown)}.", field="events"
            )
        event_type = _as_str(item.get("type", ""), "events")
        if not product_owns(product_code, event_type):
            raise ManifestError(
                f"Event type {event_type!r} is outside this product's namespace. "
                f"Every declared type must be 'product.{product_code}.<aggregate>."
                f"<past_tense_verb>'.",
                field="events",
            )
        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ManifestError("events[].version must be an integer.", field="events")
        if not 1 <= version <= MAX_EVENT_VERSION:
            raise ManifestError(
                f"events[].version must be between 1 and {MAX_EVENT_VERSION}.",
                field="events",
            )
        description = _as_str(item.get("description") or "", "events")
        if len(description) > MAX_EVENT_DESCRIPTION_LENGTH:
            raise ManifestError(
                f"events[].description exceeds {MAX_EVENT_DESCRIPTION_LENGTH} "
                f"characters.",
                field="events",
            )
        if (event_type, version) in seen:
            raise ManifestError(
                f"Duplicate event declaration {event_type}.v{version}.", field="events"
            )
        seen.add((event_type, version))
        declared.append(
            EventDeclaration(type=event_type, version=version, description=description)
        )
    return tuple(declared)


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

    # A browser reads "\" as "/" in an http(s) URL, so the host a user agent
    # opens is not the one this parser sees ("https://127.0.0.1\.evil.com/"
    # opens 127.0.0.1). Refused anywhere (Phase 12, final audit).
    if "\\" in value:
        raise ManifestError(
            f"{field}: a backslash is not accepted in a URL.", field=field
        )

    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ManifestError(f"{field}: unparseable URL ({exc}).", field=field) from None

    # A browser percent-decodes the host before using it ("127.0.0.%31" opens
    # 127.0.0.1); a registered host has no reason to be encoded.
    if "%" in parts.netloc:
        raise ManifestError(
            f"{field}: a percent-encoded host is not accepted.", field=field
        )

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

    # The loopback exemption exists for plaintext local development
    # (``http://localhost:9100``, already validated above) - it must never
    # extend to ``https``. A registered ``https://localhost/`` is exactly as
    # wrong in development as in production, so it is refused unconditionally.
    _refuse_internal_host(
        hostname, field=field, allow_loopback=allow_insecure and parts.scheme == "http"
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


#: Names that can only mean "this machine or this network". The list is a
#: convenience for a clear message; the address test below is the control.
_INTERNAL_NAMES: Final[frozenset[str]] = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data", "kubernetes"}
)
_INTERNAL_SUFFIXES: Final[tuple[str, ...]] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home.arpa",
    ".svc",
    ".cluster.local",
)


def _refuse_internal_host(hostname: str, *, field: str, allow_loopback: bool) -> None:
    """A registered address is where a *browser* is sent and where the health
    probe looks. Neither may point at this machine, a private network or a
    cloud metadata service (Phase 11): ``https://169.254.169.254/`` and
    ``https://localhost/`` are syntactically fine and must not register.

    A literal address must be globally routable; a name must not be an internal
    one. Loopback is allowed only where plaintext loopback already is
    (development and tests). A public *name* that merely resolves to a private
    address cannot be caught here without a lookup - the probe's egress client
    checks every resolved address at connection time, and a browser redirect to
    it is the customer's own network's business.
    """
    # Judge the host a *browser* would use, not the string as typed (Phase
    # 12, G-11): fullwidth letters fold to ASCII, and a host ending in a number
    # is an IPv4 address in any of its legacy spellings - ``2130706433``,
    # ``0177.0.0.1``, ``0x7f.0.0.1`` and ``127.1`` all open 127.0.0.1.
    # The ideographic full stop (U+3002; NFKC also folds the halfwidth one to
    # it) is a label separator to a browser's IDNA mapping (UTS #46), so
    # "127。0。0。1" opens 127.0.0.1 (final audit).
    host = (
        unicodedata.normalize("NFKC", hostname)
        .replace("。", ".")
        .strip("[]")
        .lower()
        .rstrip(".")
    )
    if allow_loopback and _is_loopback(host):
        return
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = (
            ipaddress.ip_address(host)
        )
    except ValueError:
        try:
            address = _whatwg_ipv4(host)
        except ValueError:
            raise ManifestError(
                f"{field}: the host is not a valid address.", field=field
            ) from None
    if address is None:
        if host in _INTERNAL_NAMES or host.endswith(_INTERNAL_SUFFIXES):
            raise ManifestError(
                f"{field}: internal and local host names are refused.", field=field
            )
        return
    if not all(a.is_global for a in _with_embedded_ipv4(address)):
        raise ManifestError(
            f"{field}: the address must be publicly routable - loopback, private, "
            f"link-local and metadata addresses are refused.",
            field=field,
        )


def _whatwg_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """The WHATWG URL Standard's IPv4 parser, or ``None`` for a domain name.

    A host whose last label is numeric is an IPv4 address to every browser;
    one that fails to parse as such raises ``ValueError`` and is refused (a
    browser would refuse the URL too), so an odd spelling can never slip
    through as a name.
    """
    labels = host.split(".")
    if labels and labels[-1] == "" and len(labels) > 1:
        labels = labels[:-1]
    last = labels[-1] if labels else ""
    if not (last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last)):
        return None
    if len(labels) > 4:
        raise ValueError("not a valid IPv4 host")
    numbers: list[int] = []
    for label in labels:
        try:
            if label.startswith("0x"):
                numbers.append(int(label[2:] or "0", 16))
            elif len(label) > 1 and label.startswith("0"):
                numbers.append(int(label, 8))
            else:
                numbers.append(int(label, 10))
        except ValueError:
            raise ValueError("not a valid IPv4 host") from None
    if any(n > 255 for n in numbers[:-1]) or numbers[-1] >= 256 ** (5 - len(numbers)):
        raise ValueError("not a valid IPv4 host")
    value = numbers[-1]
    for index, n in enumerate(numbers[:-1]):
        value += n * 256 ** (3 - index)
    return ipaddress.IPv4Address(value)


def _with_embedded_ipv4(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """The address plus any IPv4 address carried inside an IPv6 one.

    ``64:ff9b::7f00:1`` (NAT64) is "globally routable" by itself and reaches
    127.0.0.1; IPv4-mapped, 6to4 and Teredo addresses carry theirs the same
    way. The egress client unwraps the same set at connection time.
    """
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            found.append(address.ipv4_mapped)
        if address.sixtofour:
            found.append(address.sixtofour)
        if address.teredo:
            found.extend(address.teredo)
        if address in _NAT64:
            found.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return found


_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")


def _is_loopback(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname.strip("[]")).is_loopback
    except ValueError:
        return False
