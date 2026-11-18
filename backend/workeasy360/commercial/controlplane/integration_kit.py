"""The integration kit: a product's WorkEasy360 sign-in configuration, generated
from its registration, for the framework it is built with.

Super Admin registers a product (manifest, credential, domains); this module
turns that registration into the files a product team needs - an environment
template, the framework's configuration and wiring, and the checks that prove
it works - so a new product is connected by filling in values, not by writing
OpenID Connect code.

**No secret is ever in a kit.** A private key or client secret is shown once
when it is created (``services.create_credential``) and lives only in the
product's secret manager; a kit names where it goes, never what it is. Every
value in a kit is already readable by anyone who can see the integration.

Adapters (``ADAPTERS``):

- ``django``: the ``workeasy360-sso`` connector (private package, pinned by
  commit), which owns the protocol: PKCE S256, state and nonce, ``private_key_jwt``,
  offline ID-token and logout-token verification, entitlements, back-channel logout;
- ``spring``: Spring Security's OAuth 2.0 client with ``private_key_jwt`` and
  PKCE; the product's own claim checks and logout receiver (Easy Project is the
  reference implementation);
- ``node``: ``openid-client`` and ``jose`` for NestJS, Express or Next.js
  backends; the product's own claim checks, logout receiver and per-request
  session check (Easy Help Desk is the reference implementation);
- ``other``: the contract every adapter must meet, for any other runtime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

from workeasy360.contracts.errors import ManifestRejected
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors

VIEW: Final = "platform.integration.view"
UTC: Final = timezone.utc  # the project still supports Python 3.10

#: The connector release a Django kit pins: tag ``v0.3.1`` of the private
#: WorkEasy360/workeasy360-sso repository, by commit so a moved tag cannot change it.
CONNECTOR_VERSION: Final = "0.3.1"
CONNECTOR_COMMIT: Final = (
    "a98552f8f37e1e4f1fab1de99cc5b915cb9ed5c1"  # pragma: allowlist secret - a commit id
)
CONNECTOR_REQUIREMENT: Final = (
    "workeasy360-sso @ git+ssh://git@github.com/WorkEasy360/workeasy360-sso.git@"
    + CONNECTOR_COMMIT
)

ADAPTERS: Final[dict[str, str]] = {
    "django": "Django: the workeasy360-sso connector",
    "spring": "Spring Boot: Spring Security OAuth 2.0 client",
    "node": "NestJS, Express or Next.js: openid-client and jose",
    "other": "Any other runtime: the integration contract",
}

PRIVATE_KEY_PLACEHOLDER: Final = "/run/secrets/workeasy360-client.pem"


def recommended_issuer() -> str:
    """The issuer a product configured today should trust: WorkEasy360 Accounts
    when it is one of the platform's issuers (ADR-067 stage b on), otherwise the
    canonical one. A product trusts exactly one."""
    from workeasy360.contracts.values import portals as portal_names
    from workeasy360.platform.ecosystem import tokens
    from workeasy360.platform.identity import portals

    names = tokens.issuers()
    if portals.enabled():
        accounts = portals.origin(portal_names.AUTH).rstrip("/")
        if accounts in names:
            return accounts
    return tokens.canonical_issuer()


def _first(values: list[str], placeholder: str) -> str:
    return values[0] if values else placeholder


def _facts(current: Any, credentials: list[Any]) -> dict[str, Any]:
    manifest = current.manifest or {}
    keys = [c for c in credentials if c.status == "active" and c.kind == "public_key"]
    secrets = [
        c for c in credentials if c.status == "active" and c.kind == "client_secret"
    ]
    issuer = recommended_issuer()
    return {
        "product_code": current.product_code,
        "display_name": str(manifest.get("display_name") or current.product_code),
        "client_id": current.client_id,
        "status": current.status,
        "issuer": issuer,
        "discovery": f"{issuer}/.well-known/openid-configuration",
        "redirect_uris": list(manifest.get("redirect_uris") or []),
        "post_logout_redirect_uris": list(
            manifest.get("post_logout_redirect_uris") or []
        ),
        "backchannel_logout_uri": str(manifest.get("backchannel_logout_uri") or ""),
        "launch_url": str(manifest.get("launch_url") or ""),
        "scopes": list(manifest.get("scopes") or ["openid"]),
        "sign_in_mode": str(manifest.get("sign_in_mode") or ""),
        "key_id": keys[0].credential_id if keys else "",
        "key_algorithm": keys[0].algorithm if keys else "RS256",
        "auth_method": "private_key_jwt" if keys or not secrets else "client_secret",
    }


def _env(f: dict[str, Any], *, extra: str = "") -> str:
    key_id = f["key_id"] or "<credential id: add a public-key credential in Super Admin>"
    lines = [
        f"# WorkEasy360 sign-in for {f['display_name']} ({f['product_code']}).",
        "# Generated by Super Admin from the product's registration. It holds no secret:",
        "# the private key stays in your secret manager and is mounted where",
        "# WORKEASY360_PRIVATE_KEY_FILE points. Never commit a key or this file with one.",
        f"WORKEASY360_ISSUER={f['issuer']}",
        f"WORKEASY360_CLIENT_ID={f['client_id']}",
        f"WORKEASY360_PRODUCT_CODE={f['product_code']}",
        f"WORKEASY360_REDIRECT_URI={_first(f['redirect_uris'], '<register a redirect URI first>')}",
        f"WORKEASY360_POST_LOGOUT_REDIRECT_URI={_first(f['post_logout_redirect_uris'], '')}",
        f"WORKEASY360_KEY_ID={key_id}",
        f"WORKEASY360_PRIVATE_KEY_FILE={PRIVATE_KEY_PLACEHOLDER}",
        f"WORKEASY360_SCOPES={' '.join(f['scopes'])}",
        "WORKEASY360_SSO_ONLY=true",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _path_of(url: str, fallback: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path if url else fallback


# --- adapters ---------------------------------------------------------------------------


def _django(f: dict[str, Any]) -> list[dict[str, str]]:
    callback = _path_of(_first(f["redirect_uris"], ""), "/auth/workeasy360/callback/")
    prefix = (
        callback.rsplit("callback", 1)[0]
        if callback.endswith(("callback", "callback/"))
        else "/auth/workeasy360/"
    )
    return [
        {
            "path": ".env.example",
            "purpose": "Environment template (no secrets)",
            "content": _env(f),
        },
        {
            "path": "requirements.txt",
            "purpose": "The connector, pinned by commit (private: install with a read-only deploy key)",
            "content": (
                f"# workeasy360-sso {CONNECTOR_VERSION} - private. CI and image builds load a read-only\n"
                "# deploy key into an ssh-agent; Docker builds use RUN --mount=type=ssh, so no key\n"
                "# reaches an image layer. Never vendor the wheel into a public repository.\n"
                f"{CONNECTOR_REQUIREMENT}\n"
            ),
        },
        {
            "path": "settings.py (add)",
            "purpose": "Install the connector; identity values come from the environment",
            "content": (
                'INSTALLED_APPS += ["workeasy360_sso"]\n'
                'MIDDLEWARE += ["workeasy360_sso.middleware.SSOMiddleware"]  # after AuthenticationMiddleware\n\n'
                "# Structural choices only; identity values are the WORKEASY360_* environment variables.\n"
                "WORKEASY360_SSO = {\n"
                "    # Maps the ID token's organization to this product's tenant (see the template's\n"
                "    # workspace.adapters.TenantAdapter): never authorize on the user id alone.\n"
                '    "ORGANIZATION_ADAPTER": "<your_app>.adapters.TenantAdapter",\n'
                '    "PUBLIC_PATHS": ["/", "/healthz", "/static/"],\n'
                '    "ERROR_REDIRECT_URL": "/login/",\n'
                "}\n"
                "SESSION_COOKIE_SECURE = True\n"
                "CSRF_COOKIE_SECURE = True\n"
                'SESSION_COOKIE_NAME = "__Host-sessionid"\n'
            ),
        },
        {
            "path": "urls.py (add)",
            "purpose": "Start, callback, sign-out and the back-channel logout receiver",
            "content": (
                "from django.urls import include, path\n\n"
                "urlpatterns += [\n"
                f'    path("{prefix.lstrip("/")}", include("workeasy360_sso.urls")),\n'
                "]\n"
                f"# Registered redirect URI path: {callback}\n"
                f"# Back-channel logout: {prefix}backchannel-logout/ (register it in Super Admin)\n"
            ),
        },
        {
            "path": "VERIFY.md",
            "purpose": "Prove it before activation",
            "content": (
                "python manage.py check --deploy\n"
                "python manage.py w360sso_check          # READY: discovery, keys, credential, redirect URI\n"
                "python manage.py w360sso_manifest --login-url <your login URL>   # the URIs to register\n"
                "# Then: one sign-in with a test organization, one sign-out, and Accounts/e2e's\n"
                "# conformance run against this product.\n"
            ),
        },
    ]


def _spring(f: dict[str, Any]) -> list[dict[str, str]]:
    callback = _path_of(_first(f["redirect_uris"], ""), "/login/oauth2/code/workeasy360")
    return [
        {
            "path": ".env.example",
            "purpose": "Environment template (no secrets)",
            "content": _env(f),
        },
        {
            "path": "src/main/resources/application-workeasy360.yml",
            "purpose": "Spring Security OAuth 2.0 client registration",
            "content": (
                "spring:\n  security:\n    oauth2:\n      client:\n        registration:\n          workeasy360:\n"
                "            client-id: ${WORKEASY360_CLIENT_ID}\n"
                "            client-authentication-method: private_key_jwt\n"
                "            authorization-grant-type: authorization_code\n"
                "            redirect-uri: ${WORKEASY360_REDIRECT_URI}\n"
                f"            scope: [{', '.join(f['scopes'])}]\n"
                "        provider:\n          workeasy360:\n"
                "            # Exactly the issuer the discovery document names; Spring verifies iss against it.\n"
                "            issuer-uri: ${WORKEASY360_ISSUER}\n"
                "            user-name-attribute: sub\n"
                f"# Registered redirect URI path: {callback}\n"
            ),
        },
        {
            "path": "Workeasy360SecurityConfiguration.java (outline)",
            "purpose": "PKCE, private_key_jwt and the claim checks",
            "content": (
                "// 1. PKCE for a confidential client: an authorization request resolver with\n"
                "//    OAuth2AuthorizationRequestCustomizers.withPkce().\n"
                "// 2. private_key_jwt: NimbusJwtClientAuthenticationParametersConverter with a JWK built from\n"
                f"//    WORKEASY360_PRIVATE_KEY_FILE (kid = WORKEASY360_KEY_ID, alg {f['key_algorithm']}), set on the\n"
                "//    authorization-code token response client.\n"
                "// 3. An OidcUserService that refuses the sign-in unless the ID token's product_code equals\n"
                "//    WORKEASY360_PRODUCT_CODE, organization_id is present and email_verified is true, and that\n"
                "//    links the person on (issuer, sub) - never on email alone.\n"
                "// 4. Authorize every request on the organization and role from the session, and ask the\n"
                "//    entitlement endpoint (discovery: workeasy360_entitlement_endpoint) as the contract says.\n"
                "// 5. Back-channel logout: verify the logout token (signature from the key set, iss, aud,\n"
                "//    typ logout+jwt, the back-channel event, no nonce, jti once) and end the sessions it\n"
                "//    names on EVERY instance: record sign-ins in a shared table and check it per request.\n"
                "// Reference implementation: WorkEasy360/Project-Module, package\n"
                "// com.projectmodule.security (SecurityConfiguration, Workeasy360OidcUserService,\n"
                "// backchannel.LogoutTokenVerifier, SignInRevocationFilter).\n"
            ),
        },
        {
            "path": "VERIFY.md",
            "purpose": "Prove it before activation",
            "content": (
                "Boot with the workeasy360 profile; GET <issuer>/.well-known/openid-configuration must name\n"
                f"{f['issuer']} exactly. Then one sign-in with a test organization, one sign-out, a logout\n"
                "delivered to a second instance, and Accounts/e2e's conformance run against this product.\n"
            ),
        },
    ]


def _node(f: dict[str, Any]) -> list[dict[str, str]]:
    callback = _path_of(_first(f["redirect_uris"], ""), "/api/auth/callback/workeasy360")
    return [
        {
            "path": ".env.example",
            "purpose": "Environment template (no secrets)",
            "content": _env(f),
        },
        {
            "path": "src/workeasy360/client.ts (starting point)",
            "purpose": "Authorization Code + PKCE with private_key_jwt (openid-client 6, jose)",
            "content": (
                'import * as oidc from "openid-client";\n'
                'import { importPKCS8 } from "jose";\n'
                'import { readFile } from "node:fs/promises";\n\n'
                "const env = process.env;\n"
                "export async function workeasy360() {\n"
                f'  const key = await importPKCS8(await readFile(env.WORKEASY360_PRIVATE_KEY_FILE!, "utf8"), "{f["key_algorithm"]}");\n'
                "  // The client assertion's aud must be the token endpoint (RFC 7523 section 3): WorkEasy360\n"
                "  // refuses any other. openid-client names the issuer unless told otherwise.\n"
                '  let tokenEndpoint = "";\n'
                "  const auth = oidc.PrivateKeyJwt({ key, kid: env.WORKEASY360_KEY_ID! }, {\n"
                "    [oidc.modifyAssertion]: (_header, payload) => { payload.aud = tokenEndpoint; },\n"
                "  });\n"
                "  // Discovery from the configured issuer (openid-client checks the document names it), and\n"
                "  // the ID token's signature verified against the key set, not only trusted over TLS.\n"
                "  const config = await oidc.discovery(new URL(env.WORKEASY360_ISSUER!), env.WORKEASY360_CLIENT_ID!,\n"
                "    undefined, auth, { execute: [oidc.enableNonRepudiationChecks] });\n"
                "  tokenEndpoint = config.serverMetadata().token_endpoint!;\n"
                "  return config;\n"
                "}\n\n"
                "// Start: keep verifier, state and nonce server-side (a signed, httpOnly, single-use flow cookie).\n"
                "export async function start(config: oidc.Configuration) {\n"
                "  const verifier = oidc.randomPKCECodeVerifier();\n"
                "  const flow = { verifier, state: oidc.randomState(), nonce: oidc.randomNonce() };\n"
                "  const url = oidc.buildAuthorizationUrl(config, {\n"
                "    redirect_uri: env.WORKEASY360_REDIRECT_URI!, scope: env.WORKEASY360_SCOPES!,\n"
                '    code_challenge: await oidc.calculatePKCECodeChallenge(verifier), code_challenge_method: "S256",\n'
                "    state: flow.state, nonce: flow.nonce,\n"
                "  });\n"
                "  return { url, flow };\n"
                "}\n\n"
                "// Callback: the code exchange verifies the ID token (signature, iss, aud, exp, nonce).\n"
                "export async function callback(config: oidc.Configuration, current: URL, flow: { verifier: string; state: string; nonce: string }) {\n"
                "  const tokens = await oidc.authorizationCodeGrant(config, current, {\n"
                "    pkceCodeVerifier: flow.verifier, expectedState: flow.state, expectedNonce: flow.nonce, idTokenExpected: true,\n"
                "  });\n"
                "  const claims = tokens.claims()!;\n"
                '  if (claims.product_code !== env.WORKEASY360_PRODUCT_CODE) throw new Error("wrong_product");\n'
                '  if (!claims.organization_id || claims.email_verified !== true) throw new Error("refused");\n'
                "  // Link the person on (issuer, sub), never on email alone; scope the session to organization_id;\n"
                "  // record claims.sid with the session so a logout token can end it.\n"
                "  return claims;\n"
                "}\n"
                f"// Registered redirect URI path: {callback}\n"
            ),
        },
        {
            "path": "src/workeasy360/backchannel-logout.ts (contract)",
            "purpose": "The logout receiver and the per-request session check",
            "content": (
                "// POST <backchannel_logout_uri>, form field logout_token:\n"
                "//   jwtVerify(token, createRemoteJWKSet(jwks_uri), { issuer: WORKEASY360_ISSUER,\n"
                '//     audience: WORKEASY360_CLIENT_ID, typ: "logout+jwt" }); require the\n'
                "//   http://schemas.openid.net/event/backchannel-logout event, sub and/or sid, a jti\n"
                "//   recorded once (shared store), and no nonce. Answer 200 (a duplicate too), 400 if invalid.\n"
                "// End the sessions it names in the SHARED store (PostgreSQL or Redis), and check that store\n"
                "// on every authenticated request, so an unexpired access token stops working at once on\n"
                "// every instance. Refresh must fail for an ended session.\n"
                "// Reference implementation: WorkEasy360/helpdesk-workeasy, backend/packages/workeasy360 and\n"
                "// backend/api/src/workeasy360 (session-liveness.service.ts).\n"
            ),
        },
        {
            "path": "VERIFY.md",
            "purpose": "Prove it before activation",
            "content": (
                "One sign-in with a test organization, one call to a protected API (200), Sign out\n"
                "everywhere, the same token refused (401) and refresh refused, then Accounts/e2e's\n"
                "conformance run against this product.\n"
            ),
        },
    ]


def _other(f: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "path": ".env.example",
            "purpose": "Environment template (no secrets)",
            "content": _env(f),
        },
        {
            "path": "CONTRACT.md",
            "purpose": "What any adapter must do",
            "content": (
                f"Issuer (exactly): {f['issuer']}\nDiscovery: {f['discovery']}\n"
                f"Client: {f['client_id']}, authenticated with {f['auth_method']}"
                + (f" (kid {f['key_id']}, {f['key_algorithm']})" if f["key_id"] else "")
                + "\n\n"
                "1. Authorization Code with PKCE S256, state and nonce kept server-side and used once.\n"
                "2. The code exchange at the discovered token endpoint, with a client assertion\n"
                "   (iss = sub = client id, aud = the token endpoint, 60 s, fresh jti).\n"
                "3. Verify the ID token: signature by kid from jwks_uri, iss exactly the issuer above,\n"
                "   aud = client id, exp/iat, nonce; product_code = "
                f"{f['product_code']}; organization_id present; email_verified true.\n"
                "4. Link people on (issuer, sub). Authorize on organization, role and entitlement - never\n"
                "   on sub alone. Ask the entitlement endpoint as the integration guide says.\n"
                "5. Your own session (httpOnly, Secure, host-only cookie). Record the ID token's sid.\n"
                "6. Back-channel logout at "
                + (f["backchannel_logout_uri"] or "<register a back-channel logout URI>")
                + ": verify the logout token, end the named sessions in a shared store\n"
                "   checked on every request, answer 200 (duplicates too) or 400.\n"
                "7. Product sign-out ends the product session only; offer WorkEasy360 sign-out via\n"
                "   end_session_endpoint with id_token_hint and a registered post-logout URI.\n"
            ),
        },
    ]


_BUILDERS = {"django": _django, "spring": _spring, "node": _node, "other": _other}


@in_scope
def integration_kit(
    ctx: TenantContext, *, product_code: str, framework: str
) -> dict[str, Any]:
    """The kit for one registered product and one adapter. Read-only; audited."""
    authz.require(ctx, VIEW)
    authz.require_platform_staff(
        ctx, operation="ecosystem.integration.kit", permission=VIEW
    )
    if framework not in _BUILDERS:
        raise ManifestRejected(
            f"Unknown framework {framework!r}: one of {', '.join(ADAPTERS)}.",
            field="framework",
        )
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    facts = _facts(
        current, ecosystem_selectors.credentials(ctx, client_id=current.client_id)
    )
    missing = []
    if not facts["redirect_uris"]:
        missing.append("a redirect URI")
    if not facts["key_id"] and facts["auth_method"] == "private_key_jwt":
        missing.append("a public-key credential")
    if not facts["backchannel_logout_uri"]:
        missing.append("a back-channel logout URI (needed for global sign-out)")
    audit.record(
        ctx,
        "ecosystem.integration.kit_generated",
        resource_type="ecosystem.product_integration",
        resource_id=current.client_id,
        # Names only: a kit holds no secret, and the audit trail holds no kit.
        changes={"product_code": product_code, "framework": framework},
    )
    return {
        "product_code": product_code,
        "framework": framework,
        "adapter": ADAPTERS[framework],
        "generated_at": datetime.now(UTC).isoformat(),
        "facts": {k: v for k, v in facts.items() if k != "status"}
        | {"integration_status": facts["status"]},
        "missing": missing,
        "files": _BUILDERS[framework](facts),
        "next": [
            "Store the private key in the product's secret manager; set the environment from .env.example.",
            "Apply the adapter files and deploy to the product's test environment.",
            "Prove domain ownership (Domains card) and activate the integration.",
            "Sign in once with a test organization and sign out everywhere; the readiness card "
            "records both.",
            "Run Accounts/e2e's conformance run against the product.",
            "Publish the product: the publish checklist requires a completed sign-in.",
        ],
    }
