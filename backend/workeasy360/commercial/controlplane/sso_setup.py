"""Automatic product sign-in setup (ADR-074): Super Admin adds a product, and
WorkEasy360 prepares, verifies and activates its sign-in - with no manual
identity-provider registration for a product built on the standard connector.

The sequence, every step through the registry's own authorized, audited
services (never a second write path):

1. **Prepare** (``prepare``, in the Add Product transaction, no network). The
   product's one setup row is created, and - once it has an application URL -
   its one OIDC client is registered as a *draft* on that origin. A draft
   cannot authenticate anyone, and ``ProductIntegration.product_code`` is
   unique, so a retry or a concurrent request can never make a second client.
2. **Check** (``check``: the Celery sweep, or Super Admin's *Check now*). Reads
   ``/.well-known/workeasy360-sso`` from the application's own origin through
   the egress client (resolve, validate every address, pin; no redirects) with
   a fresh challenge, and checks the document (``evaluate_document``, pure):
   product code, client id and issuer match; every address is on the
   application's host; the published keys are public signing keys; the proof
   verifies with one of them, names the challenge and is fresh.
3. **Domain** - ADR-070's DNS proof for each hostname, looked up now.
4. **Configure** - the registration takes the document's addresses
   (``update_manifest``) and its keys (``create_credential`` under the
   product's own key id). A key that stops being published is revoked only
   after :data:`KEY_ABSENCES_BEFORE_REVOKE` checks in a row, and never the last.
5. **Activate** - ``transition(draft -> active)``, which refuses an unproven
   hostname by itself. Never before every mandatory check has passed; never a
   suspended or retired integration; never anything for a document that failed.

**Who acts.** The checks act for the staff member who added the product, or
who last chose *Check now* (``ProductSignInSetup.requested_by_id``), with their
permissions checked live on every write. No system identity holds standing
power here: if that person loses the integration grants, the checks stop and
say so.

**What never happens.** An active integration is never deactivated by a check
(a product that stops serving its document keeps working, and the setup
reports it); a product registered by hand is never touched until staff choose
*Check now*; no secret is read, stored or shown - the document is public.

Nothing here names a product.
"""

from __future__ import annotations

import contextlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

VIEW: Final = "platform.integration.view"
WELL_KNOWN_PATH: Final = "/.well-known/workeasy360-sso"
SCHEMA: Final = "workeasy360-sso/1"
PROOF_TYPE: Final = "w360-sso-setup+jwt"
#: A proof may live at most this long (the connector issues 120 s).
PROOF_MAX_AGE_S: Final = 300
CLOCK_LEEWAY_S: Final = 60
MAX_DOCUMENT_BYTES: Final = 65536  # integration.health.sign_in_document caps the body
#: The Django connector release that serves the document.
CONNECTOR_NAME: Final = "workeasy360-sso"
MIN_CONNECTOR_VERSION: Final = (0, 4, 0)
KEY_ABSENCES_BEFORE_REVOKE: Final = 3
AUTOMATIC_LABEL: Final = "automatic setup (ADR-074)"
#: The draft's callback until the document names the real one. On the
#: application's own origin, and harmless: a draft authenticates nobody.
PLACEHOLDER_CALLBACK_PATH: Final = "/auth/workeasy360/callback/"
DEFAULT_SCOPES: Final = ("openid", "email", "profile", "organization")
#: JWK members that only a private key has. A document carrying any is refused.
PRIVATE_JWK_MEMBERS: Final = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
CHECKS_TOTAL: Final = "ecosystem_sign_in_setup_checks_total"

PASSED, PENDING, FAILED = "passed", "pending", "failed"

STATE_LABELS: Final[dict[str, str]] = {
    "pending_configuration": "Pending Configuration",
    "configured": "Configured",
    "verification_required": "Verification Required",
    "active": "Active",
    "failed": "Failed",
}


# --- the checks' vocabulary -----------------------------------------------------


@dataclass
class _Checks:
    rows: list[dict[str, str]] = field(default_factory=list)

    def add(self, key: str, label: str, status: str, detail: str) -> None:
        self.rows.append({"key": key, "label": label, "status": status, "detail": detail})

    def status_of(self, key: str) -> str | None:
        return next((r["status"] for r in self.rows if r["key"] == key), None)

    @property
    def any_failed(self) -> bool:
        return any(r["status"] == FAILED for r in self.rows)

    @property
    def all_passed(self) -> bool:
        return bool(self.rows) and all(r["status"] == PASSED for r in self.rows)


@dataclass(frozen=True)
class PublishedKey:
    kid: str
    algorithm: str
    public_key_pem: str


@dataclass
class Evaluation:
    """What a compatibility document says, checked. ``ok`` only when every
    document check passed; the registration may then be brought in line."""

    checks: list[dict[str, str]]
    ok: bool
    addresses: dict[str, Any] = field(default_factory=dict)
    keys: list[PublishedKey] = field(default_factory=list)
    connector: dict[str, str] = field(default_factory=dict)


# --- 2. the document, judged (pure: no database, no network) -------------------


def _version_tuple(raw: str) -> tuple[int, ...]:
    out = []
    for part in str(raw).split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


_DEFAULT_PORTS = {"https": 443, "http": 80}


def _effective_port(parts: Any) -> int | None:
    """The port an address really uses (its scheme's default when it names none),
    or ``None`` when the port it names is not a valid one."""
    try:
        port = parts.port
    except ValueError:
        return None
    return port if port is not None else _DEFAULT_PORTS.get(parts.scheme)


def _address_problem(url: Any, *, host: str, scheme: str, port: int | None = None) -> str:
    """Why ``url`` is not an address on the application's own origin, or ``""``.

    2026-11-27 (alignment audit F.3): the origin is the scheme, host *and port* -
    another port on the same host is another service, so a callback there is
    refused too. ``port`` is the application's effective port."""
    if not isinstance(url, str) or not url:
        return "is missing"
    parts = urlsplit(url)
    if parts.scheme != scheme:
        return f"is not {scheme}://"
    if parts.username or parts.password or parts.fragment:
        return "carries credentials or a fragment"
    if (parts.hostname or "").lower() != host:
        return f"is not on {host}"
    if port is not None and _effective_port(parts) != port:
        return f"is not on the application's port ({port})"
    return ""


def _jwk_to_key(jwk: Any) -> tuple[PublishedKey | None, str]:
    """A published JWK as a registrable public key, or the reason it is not."""
    from workeasy360.platform.ecosystem.keys import SUPPORTED_ALGORITHMS
    from workeasy360.platform.ecosystem.services import CREDENTIAL_ID_RE

    if not isinstance(jwk, dict):
        return None, "a key is not a JSON object"
    if PRIVATE_JWK_MEMBERS & set(jwk):
        return (
            None,
            "PRIVATE KEY MATERIAL IS PUBLISHED - remove it and rotate the key now",
        )
    kid = str(jwk.get("kid") or "")
    if not CREDENTIAL_ID_RE.match(kid):
        return None, "a key has no usable kid (4-64 of letters, digits, . _ : -)"
    algorithm = str(jwk.get("alg") or "")
    if algorithm not in SUPPORTED_ALGORITHMS:
        return (
            None,
            f"key {kid} uses {algorithm or 'no algorithm'}; WorkEasy360 accepts {', '.join(SUPPORTED_ALGORITHMS)}",
        )
    if jwk.get("use", "sig") != "sig":
        return None, f"key {kid} is not a signing key"
    try:
        if algorithm == "RS256" and jwk.get("kty") == "RSA":
            public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        elif (
            algorithm == "ES256" and jwk.get("kty") == "EC" and jwk.get("crv") == "P-256"
        ):
            public = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(jwk))
        else:
            return None, f"key {kid}: its type does not match {algorithm}"
    except (ValueError, TypeError, KeyError, jwt.InvalidKeyError):
        return None, f"key {kid} cannot be read"
    if hasattr(public, "private_bytes"):  # pragma: no cover - members checked above
        return None, "PRIVATE KEY MATERIAL IS PUBLISHED"
    pem = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return PublishedKey(kid=kid, algorithm=algorithm, public_key_pem=pem), ""


def _proof_problem(
    token: Any,
    keys: dict[str, PublishedKey],
    *,
    client_id: str,
    issuer: str,
    challenge: str,
) -> str:
    if not isinstance(token, str) or not token:
        return "no proof was returned for the challenge"
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        return "the proof is not a JWT"
    if header.get("typ") != PROOF_TYPE:
        return f"the proof's type is not {PROOF_TYPE}"
    key = keys.get(str(header.get("kid") or ""))
    if key is None:
        return "the proof is signed with a key the document does not publish"
    if header.get("alg") != key.algorithm:
        return "the proof's algorithm is not its key's"
    public = serialization.load_pem_public_key(key.public_key_pem.encode())
    try:
        claims = jwt.decode(
            token,
            public,  # type: ignore[arg-type]
            algorithms=[key.algorithm],
            audience=f"{issuer.rstrip('/')}/sso-setup",
            issuer=client_id,
            leeway=CLOCK_LEEWAY_S,
            options={"require": ["iss", "sub", "aud", "iat", "exp", "nonce"]},
        )
    except jwt.InvalidTokenError as exc:
        return f"the proof does not verify ({type(exc).__name__})"
    if claims.get("sub") != client_id:
        return "the proof names another client"
    if claims.get("nonce") != challenge:
        return "the proof is not for the challenge just sent"
    if int(claims["exp"]) - int(claims["iat"]) > PROOF_MAX_AGE_S:
        return f"the proof lives longer than {PROOF_MAX_AGE_S} seconds"
    return ""


def evaluate_document(
    raw: bytes,
    *,
    product_code: str,
    client_id: str,
    issuers: tuple[str, ...],
    application_url: str,
    challenge: str,
) -> Evaluation:
    """Judge a fetched compatibility document. Pure: the same bytes always get
    the same answer, so every rule here is tested without a product."""
    checks = _Checks()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        checks.add(
            "document", "Compatibility document", FAILED, "The answer is not JSON."
        )
        return Evaluation(checks=checks.rows, ok=False)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        checks.add(
            "document",
            "Compatibility document",
            FAILED,
            f"The document's schema is not {SCHEMA}.",
        )
        return Evaluation(checks=checks.rows, ok=False)
    connector = doc.get("connector") if isinstance(doc.get("connector"), dict) else {}
    name, version = str(connector.get("name") or ""), str(connector.get("version") or "")
    if name == CONNECTOR_NAME and _version_tuple(version) < MIN_CONNECTOR_VERSION:
        checks.add(
            "document",
            "Compatibility document",
            FAILED,
            f"{name} {version} is too old: use 0.4.0 or later.",
        )
    else:
        checks.add(
            "document",
            "Compatibility document",
            PASSED,
            f"Served by {name or 'an unnamed connector'} {version}".rstrip() + ".",
        )

    # Identity: the document must describe *this* registration.
    problems = []
    if doc.get("product_code") != product_code:
        problems.append(
            f"product code is {doc.get('product_code')!r}, not {product_code!r}"
        )
    if doc.get("client_id") != client_id:
        problems.append(
            "client id is not the one WorkEasy360 prepared (shown on this tab)"
        )
    issuer = str(doc.get("issuer") or "").rstrip("/")
    if issuer not in {i.rstrip("/") for i in issuers}:
        problems.append(f"issuer {issuer or '(none)'} is not one WorkEasy360 serves")
    if doc.get("token_endpoint_auth_method") != "private_key_jwt":
        problems.append("the product does not authenticate with private_key_jwt")
    checks.add(
        "identity",
        "Product code, client and issuer",
        FAILED if problems else PASSED,
        ("The document's " + "; ".join(problems) + ".")
        if problems
        else "Match this registration.",
    )

    # Addresses: every one on the application's own host.
    app = urlsplit(application_url)
    host, scheme = (app.hostname or "").lower(), app.scheme
    port = _effective_port(app)
    redirect_uris = doc.get("redirect_uris")
    post_logout = doc.get("post_logout_redirect_uris") or []
    address_problems = []
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or len(redirect_uris) > 10
    ):
        address_problems.append("redirect_uris must list 1-10 callback addresses")
        redirect_uris = []
    if not isinstance(post_logout, list) or len(post_logout) > 10:
        address_problems.append("post_logout_redirect_uris must be a list of at most 10")
        post_logout = []
    named = [("launch_url", doc.get("launch_url"))]
    named += [("redirect_uri", u) for u in redirect_uris]
    named += [("post_logout_redirect_uri", u) for u in post_logout]
    backchannel = doc.get("backchannel_logout_uri") or ""
    if backchannel:
        named.append(("backchannel_logout_uri", backchannel))
    for label, url in named:
        problem = _address_problem(url, host=host, scheme=scheme, port=port)
        if problem:
            address_problems.append(f"{label} {url!r} {problem}")
    checks.add(
        "addresses",
        "Callback and logout addresses",
        FAILED if address_problems else PASSED,
        ("; ".join(address_problems) + ".")
        if address_problems
        else f"{len(redirect_uris)} callback, {len(post_logout)} sign-out return"
        + (", back-channel logout" if backchannel else "")
        + f", all on {host}.",
    )

    # Keys: public signing keys only.
    jwks = doc.get("jwks") if isinstance(doc.get("jwks"), dict) else {}
    raw_keys = jwks.get("keys") if isinstance(jwks.get("keys"), list) else []
    keys: list[PublishedKey] = []
    key_problems = []
    for jwk in raw_keys[:10]:
        key, problem = _jwk_to_key(jwk)
        if key is None:
            key_problems.append(problem)
        else:
            keys.append(key)
    if not raw_keys:
        key_problems.append("no public signing key is published")
    if len({k.kid for k in keys}) != len(keys):
        key_problems.append("two keys share a kid")
    checks.add(
        "keys",
        "Public signing key",
        FAILED if key_problems else PASSED,
        ("; ".join(key_problems) + ".")
        if key_problems
        else ", ".join(f"{k.kid} ({k.algorithm})" for k in keys) + ".",
    )

    # Proof: the application on this origin holds one of those keys.
    if key_problems or problems:
        checks.add(
            "proof",
            "Key possession proof",
            PENDING,
            "Checked once the keys and identity pass.",
        )
        proof_ok = False
    else:
        problem = _proof_problem(
            doc.get("proof"),
            {k.kid: k for k in keys},
            client_id=client_id,
            issuer=issuer,
            challenge=challenge,
        )
        checks.add(
            "proof",
            "Key possession proof",
            FAILED if problem else PASSED,
            (problem[0].upper() + problem[1:] + ".")
            if problem
            else "The application holds the key it publishes.",
        )
        proof_ok = not problem

    ok = proof_ok and not checks.any_failed
    return Evaluation(
        checks=checks.rows,
        ok=ok,
        addresses={
            "launch_url": doc.get("launch_url"),
            "redirect_uris": list(redirect_uris),
            "post_logout_redirect_uris": list(post_logout),
            "backchannel_logout_uri": backchannel,
        },
        keys=keys,
        connector={"name": name[:64], "version": version[:32]},
    )


# --- 1. prepare (Add Product's transaction; no network) ------------------------


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _application_url_problem(url: str) -> str:
    if not url:
        return "missing"
    parts = urlsplit(url)
    allowed = ("https", "http") if settings.INTEGRATION_EGRESS_ALLOW_HTTP else ("https",)
    if parts.scheme not in allowed or not parts.hostname:
        return "not an https:// address"
    if parts.username or parts.password:
        return "carries credentials"
    return ""


def _register_draft(
    ctx: Any, *, product_code: str, name: str, application_url: str
) -> tuple[Any, bool]:
    """The product's one OIDC client, as a draft on the application's origin,
    and whether this call registered it. Idempotent: an existing integration
    (registered by the wizard, by hand or by a concurrent setup) is returned
    untouched."""
    from workeasy360.commercial.controlplane import products as console
    from workeasy360.contracts.enums import ProductSignInMode
    from workeasy360.contracts.errors import ProductIntegrationError
    from workeasy360.platform.ecosystem import domains

    existing = domains.integration_by_code(product_code)
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            console.register_integration(
                ctx,
                product_code=product_code,
                spec={
                    "display_name": (name or product_code)[:128],
                    "launch_url": application_url,
                    "sign_in_mode": ProductSignInMode.SSO_ONLY.value,
                    "scopes": list(DEFAULT_SCOPES),
                    "redirect_uris": [
                        _origin(application_url) + PLACEHOLDER_CALLBACK_PATH
                    ],
                },
            )
    except ProductIntegrationError:
        # Lost a race to another registration of the same product: use it.
        return domains.integration_by_code(product_code), False
    return domains.integration_by_code(product_code), True


def prepare(ctx: Any, *, product_code: str) -> None:
    """Start automatic setup for a product Super Admin just added (or whose
    application URL just changed). In the caller's transaction; the first
    check is queued for after it commits."""
    from workeasy360.commercial.catalog import selectors as catalog_selectors
    from workeasy360.contracts.errors import PermissionDenied
    from workeasy360.platform.ecosystem import domains, sign_in_setup

    facts = catalog_selectors.application_facts(product_code) or {}
    application_url = str(facts.get("application_url") or "").strip()
    integration = domains.integration_by_code(product_code)
    created = False
    if integration is None and not _application_url_problem(application_url):
        try:
            integration, created = _register_draft(
                ctx,
                product_code=str(product_code),
                name=str(facts.get("name") or ""),
                application_url=application_url,
            )
        except PermissionDenied:
            # Adding a product never fails for want of the integration grant:
            # the client is prepared by the first check made by someone who
            # holds it ("Check now"), and the setup says so.
            integration = None
    sign_in_setup.prepared(
        str(product_code),
        requested_by_id=ctx.user_id,
        application_url=application_url,
        integration=integration,
        created_integration=created,
        pending_checks=[
            {
                "key": "application_url",
                "label": "Application URL",
                "status": PENDING,
                "detail": "Add the application URL (https://...) to prepare the sign-in.",
            }
        ],
    )
    code = str(product_code)
    transaction.on_commit(lambda: _enqueue(code))


def _enqueue(product_code: str) -> None:
    from workeasy360.commercial.controlplane.tasks import check_product_sign_in

    try:
        check_product_sign_in.delay(product_code)
    except Exception:
        logger.warning(
            "ecosystem.sign_in_setup.enqueue_failed", product_code=product_code
        )


# --- 2-5. check (network, then the registry's own services) --------------------


def _fetch_document(url: str) -> tuple[str, bytes, str]:
    """``(outcome, body, detail)`` (``integration.health.sign_in_document``)."""
    from workeasy360.integration.health import sign_in_document

    return sign_in_document.fetch(url)


#: Injectable for tests (the module test replaces it; nothing else does).
fetch_document = _fetch_document


def _manifest_with(
    current: dict[str, Any], addresses: dict[str, Any], *, draft: bool
) -> dict[str, Any]:
    manifest = dict(current)
    manifest["launch_url"] = addresses["launch_url"]
    manifest["redirect_uris"] = list(addresses["redirect_uris"])
    manifest["post_logout_redirect_uris"] = list(addresses["post_logout_redirect_uris"])
    if addresses["backchannel_logout_uri"]:
        manifest["backchannel_logout_uri"] = addresses["backchannel_logout_uri"]
    else:
        manifest.pop("backchannel_logout_uri", None)
    if draft:
        manifest["sign_in_mode"] = "sso_only"
    # Each manifest version is a superset of the one before: stamp the one the
    # fields now need (back-channel logout's), never an older one.
    from workeasy360.commercial.controlplane import products as console

    needed = console._manifest_version_for(manifest)
    if str(manifest.get("manifest_version") or "") < needed:
        manifest["manifest_version"] = needed
    return manifest


def _differs(current: dict[str, Any], proposed: dict[str, Any]) -> bool:
    keys = (
        "launch_url",
        "redirect_uris",
        "post_logout_redirect_uris",
        "backchannel_logout_uri",
        "sign_in_mode",
    )
    return any(current.get(k) != proposed.get(k) for k in keys)


def _sync_keys(
    ctx: Any, record: Any, integration: Any, keys: list[PublishedKey]
) -> tuple[str, str]:
    """Register published keys under their own ids; revoke this setup's keys
    that stopped being published - after several misses, never the last."""
    from workeasy360.platform.ecosystem import services as ecosystem
    from workeasy360.platform.ecosystem import sign_in_setup

    rows = sign_in_setup.public_keys(integration)
    added, problems = [], []
    for key in keys:
        row = rows.get(key.kid)
        if row is None:
            ecosystem.create_credential(
                ctx,
                client_id=integration.client_id,
                kind="public_key",
                public_key_pem=key.public_key_pem,
                algorithm=key.algorithm,
                label=AUTOMATIC_LABEL,
                credential_id=key.kid,
            )
            added.append(key.kid)
        elif row.public_key_pem.strip() != key.public_key_pem.strip():
            problems.append(
                f"key id {key.kid} is registered with a different key: "
                "publish the new key under a new key id"
            )
        elif not row.usable:
            problems.append(
                f"key {key.kid} is revoked or expired here: publish a new key id"
            )
    published = {k.kid for k in keys}
    absences = {
        kid: n
        for kid, n in dict(record.key_absences or {}).items()
        if kid not in published
    }
    # Re-read: what this integration can authenticate with *now*, among the
    # published keys. Revocation is only ever considered while one remains.
    current = sign_in_setup.public_keys(integration)
    usable_published = [
        kid for kid in published if kid in current and current[kid].usable
    ]
    revoked = []
    for kid, row in current.items():
        if kid in published or row.label != AUTOMATIC_LABEL or not row.usable:
            continue
        absences[kid] = int(absences.get(kid, 0)) + 1
        if absences[kid] >= KEY_ABSENCES_BEFORE_REVOKE and usable_published:
            ecosystem.revoke_credential(
                ctx,
                client_id=integration.client_id,
                credential_id=kid,
                reason="No longer published in the compatibility document "
                "(automatic setup, ADR-074).",
            )
            revoked.append(kid)
            absences.pop(kid)
    record.key_absences = absences
    if problems:
        return FAILED, "; ".join(problems) + "."
    detail = "Registered: " + ", ".join(sorted(published)) + "."
    if added:
        detail += f" Added now: {', '.join(added)}."
    if revoked:
        detail += f" Revoked (no longer published): {', '.join(revoked)}."
    waiting = [k for k, n in absences.items() if n]
    if waiting:
        detail += f" Not published, kept for now: {', '.join(sorted(waiting))}."
    return PASSED, detail


def _domains_step(
    ctx: Any, integration: Any, hostnames: list[str], checks: _Checks
) -> bool:
    from workeasy360.commercial.controlplane import product_domains
    from workeasy360.platform.ecosystem import domains

    domains.ensure_domains(integration, hostnames)
    verified = domains.verified_hostnames(integration)
    for hostname in [h for h in hostnames if h not in verified]:
        try:
            product_domains.verify_domain(
                ctx, product_code=integration.product_code, hostname=hostname
            )
        except Exception as exc:
            logger.info(
                "ecosystem.sign_in_setup.domain_lookup_failed",
                product_code=integration.product_code,
                error_type=type(exc).__name__,
            )
    integration.refresh_from_db()
    verified = domains.verified_hostnames(integration)
    missing = [h for h in hostnames if h not in verified]
    if missing:
        listed = domains.domains_json(integration)["domains"]
        records = [
            f'TXT {d["record_name"]} = "{d["record_value"]}"'
            for d in listed
            if d["hostname"] in missing and d.get("record_value")
        ]
        checks.add(
            "domain",
            "Domain ownership",
            PENDING,
            "Publish "
            + "; ".join(records or [f"the proof for {', '.join(missing)}"])
            + ", then Check now.",
        )
        return False
    checks.add("domain", "Domain ownership", PASSED, ", ".join(hostnames) + " proven.")
    return True


@dataclass
class _Outcome:
    state: str
    checks: list[dict[str, str]]
    connector: dict[str, str] = field(default_factory=dict)
    activated: bool = False
    integration: Any = None
    created_integration: bool = False


def _run(ctx: Any, record: Any) -> _Outcome:
    from workeasy360.commercial.catalog import selectors as catalog_selectors
    from workeasy360.contracts.errors import (
        ManifestRejected,
        ProductDomainUnverified,
        ProductIntegrationError,
    )
    from workeasy360.platform.ecosystem import domains, tokens
    from workeasy360.platform.ecosystem import services as ecosystem

    checks = _Checks()
    facts = catalog_selectors.application_facts(record.product_code)
    if facts is None:
        checks.add(
            "application_url",
            "Application URL",
            FAILED,
            "This product is no longer in the catalog.",
        )
        return _Outcome("failed", checks.rows)
    application_url = str(facts.get("application_url") or "").strip()
    record.application_url = application_url
    integration = domains.integration_by_code(record.product_code)
    live = integration is not None and integration.status == "active"

    def outcome(
        state: str, connector: dict[str, str] | None = None, **extra: Any
    ) -> _Outcome:
        return _Outcome(
            state,
            checks.rows,
            connector or {},
            integration=integration,
            created_integration=created,
            **extra,
        )

    created = False
    problem = _application_url_problem(application_url)
    if problem:
        checks.add(
            "application_url",
            "Application URL",
            PENDING,
            f"The application URL is {problem}: add the https:// address on this tab.",
        )
        return outcome("active" if live else "pending_configuration")
    checks.add("application_url", "Application URL", PASSED, application_url)

    if integration is None:
        integration, created = _register_draft(
            ctx,
            product_code=record.product_code,
            name=str(facts.get("name") or ""),
            application_url=application_url,
        )
    checks.add(
        "client",
        "OIDC client",
        PASSED,
        f"Client id {integration.client_id}. Configure the product with it.",
    )
    if integration.status in ("suspended", "retired"):
        checks.add(
            "activation",
            "Sign-in active",
            PENDING,
            f"{integration.status.title()} by staff. Automatic setup never resumes or changes it.",
        )
        return outcome("configured")

    challenge = secrets.token_urlsafe(32)
    document_url = f"{_origin(application_url)}{WELL_KNOWN_PATH}"
    fetched, body, detail = fetch_document(f"{document_url}?challenge={challenge}")
    if fetched != "ok":
        status = FAILED if fetched in ("refused", "too_large") else PENDING
        advice = (
            " Install workeasy360-sso 0.4.0 or later and add *well_known() to the root URLconf, "
            "or serve the document (connector docs/compatibility.md)."
            if fetched in ("missing", "unreachable")
            else ""
        )
        checks.add(
            "document",
            "Compatibility document",
            status,
            f"Pending integration: {document_url} {detail}.{advice}",
        )
        state = "failed" if status == FAILED else "pending_configuration"
        return outcome("active" if live else state)

    evaluation = evaluate_document(
        body,
        product_code=record.product_code,
        client_id=integration.client_id,
        issuers=tokens.issuers(),
        application_url=application_url,
        challenge=challenge,
    )
    checks.rows.extend(evaluation.checks)
    if not evaluation.ok:
        return outcome("active" if live else "failed", evaluation.connector)

    host = (urlsplit(application_url).hostname or "").lower()
    proven = _domains_step(ctx, integration, [host], checks)

    # Configure: the document's addresses and keys, through the registry.
    proposed = _manifest_with(
        integration.manifest or {}, evaluation.addresses, draft=not live
    )
    try:
        if _differs(integration.manifest or {}, proposed):
            ecosystem.update_manifest(
                ctx, client_id=integration.client_id, manifest=proposed
            )
            integration.refresh_from_db()
        checks.add(
            "registration",
            "Registration matches the product",
            PASSED,
            "Addresses registered.",
        )
    except (ManifestRejected, ProductDomainUnverified, ProductIntegrationError) as exc:
        checks.add("registration", "Registration matches the product", FAILED, str(exc))
        return outcome("active" if live else "failed", evaluation.connector)
    try:
        status, detail = _sync_keys(ctx, record, integration, evaluation.keys)
    except ProductIntegrationError as exc:
        status, detail = FAILED, str(exc)
    checks.add("credentials", "Credentials", status, detail)
    if status == FAILED:
        return outcome("active" if live else "failed", evaluation.connector)

    if not proven:
        checks.add(
            "activation", "Sign-in active", PENDING, "Waiting for the domain proof."
        )
        return outcome(
            "active" if live else "verification_required", evaluation.connector
        )

    activated = False
    if not live:
        try:
            ecosystem.transition(ctx, client_id=integration.client_id, to_status="active")
            activated = True
        except ProductDomainUnverified as exc:
            checks.add("activation", "Sign-in active", PENDING, str(exc))
            return outcome("verification_required", evaluation.connector)
    checks.add(
        "activation",
        "Sign-in active",
        PASSED,
        "Activated now: every check passed." if activated else "Active.",
    )
    return outcome("active", evaluation.connector, activated=activated)


def _next_check(state: str, attempts: int) -> Any:
    now = timezone.now()
    if state == "active":
        return now + timedelta(hours=24)  # keys and addresses follow the document
    return now + min(timedelta(minutes=5) * (2 ** min(attempts, 7)), timedelta(hours=6))


def _actor_refused(detail: str) -> _Outcome:
    return _Outcome(
        "failed",
        [
            {
                "key": "actor",
                "label": "Acting staff member",
                "status": FAILED,
                "detail": detail,
            }
        ],
    )


def check(*, product_code: str, actor_user_id: Any = None) -> dict[str, Any]:
    """Run every setup check for one product now and record the result.

    ``actor_user_id`` - a staff member choosing *Check now* - becomes the
    identity the checks act for (and is authorized live, like any write). The
    scheduled sweep passes none and acts for whoever is recorded."""
    from workeasy360.contracts.errors import PermissionDenied
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context
    from workeasy360.platform.ecosystem import sign_in_setup

    record = sign_in_setup.ensure(str(product_code), requested_by_id=actor_user_id)
    ctx = None
    if record.requested_by_id is None:
        result = _actor_refused(
            "Nobody is recorded to act for these checks: choose Check now."
        )
    else:
        ctx = platform_context(user_id=record.requested_by_id)
        try:
            with acting_scope(ctx):
                result = _run(ctx, record)
        except PermissionDenied:
            result = _actor_refused(
                "The staff member these checks act for may no longer manage integrations: "
                "someone who may should choose Check now."
            )

    attempts = 0 if result.state == "active" else record.attempts + 1
    # The result and its audit entry are written as the person the checks act
    # for, in the platform organization's scope - the scheduled run has no
    # request to provide one.
    scope = acting_scope(ctx) if ctx is not None else contextlib.nullcontext()
    with scope, transaction.atomic():
        stored = sign_in_setup.record_result(
            record.pk,
            state=result.state,
            checks=result.checks,
            application_url=record.application_url,
            integration=result.integration,
            created_integration=result.created_integration,
            key_absences=record.key_absences,
            connector=result.connector,
            activated=result.activated,
            next_check_at=_next_check(result.state, attempts),
        )
        if ctx is not None and stored.integration_id is not None:
            audit.record(
                ctx,
                "ecosystem.integration.sign_in_setup_checked",
                resource_type="ProductIntegration",
                resource_id=stored.integration_id,
                changes={
                    "product_code": stored.product_code,
                    "state": stored.state,
                    "activated": result.activated,
                    "automatic": actor_user_id is None,
                    "not_passed": [
                        c["key"] for c in result.checks if c["status"] != PASSED
                    ],
                },
            )
    metrics.increment(CHECKS_TOTAL, state=result.state)
    logger.info(
        "ecosystem.sign_in_setup.checked",
        product_code=str(product_code),
        state=result.state,
        activated=result.activated,
    )
    return setup_json(str(product_code))


def sweep(*, limit: int = 50) -> dict[str, int]:
    """The scheduled run: every setup whose next check is due."""
    from workeasy360.platform.ecosystem import sign_in_setup

    counts: dict[str, int] = {}
    for code in sign_in_setup.due(limit=limit):
        try:
            state = check(product_code=code)["state"]
        except Exception as exc:
            logger.error(
                "ecosystem.sign_in_setup.check_error",
                product_code=code,
                error_type=type(exc).__name__,
            )
            state = "error"
        counts[state] = counts.get(state, 0) + 1
    return counts


# --- what Super Admin shows ------------------------------------------------------


def setup_json(product_code: str) -> dict[str, Any]:
    """The setup as Super Admin's Integration tab shows it. Stored facts only:
    the result of the last check, never a label the browser computed."""
    from workeasy360.platform.ecosystem import domains, sign_in_setup

    stored = sign_in_setup.view(product_code)
    integration = domains.integration_by_code(product_code)
    application_url = stored["application_url"] if stored else ""

    def iso(value: Any) -> str | None:
        return value.isoformat() if value else None

    return {
        "product_code": str(product_code),
        "automatic": stored is not None,
        "state": stored["state"] if stored else None,
        "state_label": STATE_LABELS.get(stored["state"], "")
        if stored
        else "Set up by hand",
        "checks": stored["checks"] if stored else [],
        "application_url": application_url,
        "document_url": (_origin(application_url) + WELL_KNOWN_PATH)
        if application_url
        else "",
        "client_id": integration.client_id if integration else "",
        "integration_status": integration.status if integration else "",
        "connector": {
            "name": stored["connector_name"],
            "version": stored["connector_version"],
        }
        if stored
        else None,
        "attempts": stored["attempts"] if stored else 0,
        "last_checked_at": iso(stored["last_checked_at"]) if stored else None,
        "next_check_at": iso(stored["next_check_at"]) if stored else None,
        "activated_at": iso(stored["activated_at"]) if stored else None,
    }


def setup_for_staff(ctx: Any, *, product_code: str) -> dict[str, Any]:
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, VIEW)
    authz.require_platform_staff(
        ctx, operation="ecosystem.integration.sign_in_setup.view", permission=VIEW
    )
    return setup_json(product_code)


def check_for_staff(ctx: Any, *, product_code: str) -> dict[str, Any]:
    """*Check now*: the person choosing it must be able to manage integrations;
    the checks then act for them."""
    from workeasy360.commercial.catalog import selectors as catalog_selectors
    from workeasy360.contracts.errors import CommercialRecordNotFound
    from workeasy360.platform.authz import services as authz

    update = "platform.integration.update"
    authz.require(ctx, update)
    authz.require_platform_staff(
        ctx, operation="ecosystem.integration.sign_in_setup.check", permission=update
    )
    if catalog_selectors.application_facts(product_code) is None:
        raise CommercialRecordNotFound("No such product.", product_code=str(product_code))
    return check(product_code=product_code, actor_user_id=ctx.user_id)
