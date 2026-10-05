"""Production.

This module's job is to *refuse to start* when anything security-critical is
missing or weakened. Every check below corresponds to a control documented in
12-security.md; a deployment that cannot satisfy them is a deployment that
would be insecure in a way nobody would notice until it mattered.

``env.require_all()`` at the end reports every problem at once.
"""

from __future__ import annotations

import base64
import hashlib

from config.settings.base import *
from config.settings.base import (
    DATABASES,
    EMAIL_USE_SSL,
    EMAIL_USE_TLS,
    FILE_STORAGE_BACKEND,
    REDIS_URL,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    SESSION_REFRESH_INTERVAL_S,
    env,
)

# --- Remote product integration -----------------------------------------
#
# The issuer is what every deployed product pins and verifies, so it must be
# a real https origin rather than the development default. Insecure redirect
# URIs are refused outright: an authorization code delivered over plaintext
# http is a disclosed credential, and the loopback exemption that makes local
# development workable has no meaning on a public deployment.
OIDC_ISSUER = env.str("W360_OIDC_ISSUER", required=True)
OIDC_ALLOW_INSECURE_REDIRECT_URIS = False

ENVIRONMENT = "production"

# Non-negotiable. DEBUG=True in production leaks settings, SQL and stack
# traces through the technical 500 page.
DEBUG = False

# A metric over its series ceiling is dropped and logged once, never raised:
# telemetry must not fail the request it describes (Phase 12). Tests and
# development keep the loud failure (base.py).
METRICS_STRICT_CARDINALITY = False

# --- Required secrets ----------------------------------------------------

SECRET_KEY = env.secret("W360_SECRET_KEY", required=True, min_length=50)
DATABASES["default"]["PASSWORD"] = env.secret(
    "W360_DB_APP_PASSWORD", required=True, min_length=16
)
FILE_URL_SIGNING_KEY = env.secret(
    "W360_FILE_URL_SIGNING_KEY", required=True, min_length=32
)
# Required, never derived from SECRET_KEY here: rotating SECRET_KEY must not
# silently make every stored TOTP secret undecryptable (base.py, MFA).
_MFA_KEY_MATERIAL = env.secret("W360_MFA_ENCRYPTION_KEY", required=True, min_length=32)
MFA_ENCRYPTION_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(_MFA_KEY_MATERIAL.encode("utf-8")).digest()
)
# ...and never the same value either, which is the same coupling by hand: a
# leaked SECRET_KEY (or file-signing key) would then also decrypt every TOTP
# secret and forge every recovery-code hash.
env.check(
    not _MFA_KEY_MATERIAL or _MFA_KEY_MATERIAL not in (SECRET_KEY, FILE_URL_SIGNING_KEY),
    "W360_MFA_ENCRYPTION_KEY must differ from W360_SECRET_KEY and "
    "W360_FILE_URL_SIGNING_KEY.",
)

# Required, never derived: connection signing secrets are sealed under this
# (integration/connectors/secrets.py), so it must be its own key - rotating
# SECRET_KEY must not make every customer's webhook secret unreadable.
_INTEGRATION_SECRET_MATERIALS = [
    m.strip()
    for m in env.secret("W360_INTEGRATION_SECRETS_KEYS", required=True).split(",")
    if m.strip()
]
INTEGRATION_SECRET_KEYS = [
    base64.urlsafe_b64encode(hashlib.sha256(m.encode("utf-8")).digest())
    for m in _INTEGRATION_SECRET_MATERIALS
]
env.check(
    all(len(m) >= 32 for m in _INTEGRATION_SECRET_MATERIALS),
    "Every W360_INTEGRATION_SECRETS_KEYS entry must be at least 32 characters.",
)
env.check(
    not set(_INTEGRATION_SECRET_MATERIALS)
    & {SECRET_KEY, FILE_URL_SIGNING_KEY, _MFA_KEY_MATERIAL},
    "W360_INTEGRATION_SECRETS_KEYS must differ from W360_SECRET_KEY, "
    "W360_FILE_URL_SIGNING_KEY and W360_MFA_ENCRYPTION_KEY.",
)

# Phase 8 extension. Required, never derived: an invitation link waiting in the
# notification queue is sealed under this (platform/notifications/sealing.py),
# so it must be its own key and never one of the others.
_NOTIFICATION_SEAL_MATERIALS = [
    m.strip()
    for m in env.secret("W360_NOTIFICATION_SEAL_KEYS", required=True).split(",")
    if m.strip()
]
NOTIFICATION_SEAL_KEYS = [
    base64.urlsafe_b64encode(hashlib.sha256(m.encode("utf-8")).digest())
    for m in _NOTIFICATION_SEAL_MATERIALS
]
env.check(
    all(len(m) >= 32 for m in _NOTIFICATION_SEAL_MATERIALS),
    "Every W360_NOTIFICATION_SEAL_KEYS entry must be at least 32 characters.",
)
env.check(
    not set(_NOTIFICATION_SEAL_MATERIALS)
    & {
        SECRET_KEY,
        FILE_URL_SIGNING_KEY,
        _MFA_KEY_MATERIAL,
        *_INTEGRATION_SECRET_MATERIALS,
    },
    "W360_NOTIFICATION_SEAL_KEYS must differ from every other platform key.",
)

# Outbound HTTP to customer endpoints: https only, never loopback. Pinned,
# not configurable, in production (integration/egress).
INTEGRATION_EGRESS_ALLOW_HTTP = False
INTEGRATION_EGRESS_ALLOW_LOOPBACK = False

# AI providers are reached only at the endpoints their adapters carry. The
# override map exists for development and the test suite's loopback provider
# and is pinned empty here, so no configuration path - staff, product or
# environment - can point the gateway at another host (Phase 8, Task 103).
AI_PROVIDER_ENDPOINTS: dict[str, str] = {}

# Payments (Phase 8 extension): providers only at their adapters' own
# endpoints, and never the development test provider - pinned, not
# configurable, whatever the environment says.
PAYMENT_PROVIDER_ENDPOINTS: dict[str, str] = {}
PAYMENTS_FAKE_PROVIDER_ENABLED = False

# --- Required hosts / origins --------------------------------------------

ALLOWED_HOSTS = env.list("W360_ALLOWED_HOSTS", required=True)
CSRF_TRUSTED_ORIGINS = env.list("W360_CSRF_TRUSTED_ORIGINS", required=True)

RELEASE = env.str("W360_RELEASE", required=True)

# --- Trusted proxy topology (D-009) --------------------------------------
#
# Required, never defaulted. Every rate limit in the platform is keyed partly
# on the caller's address, and both wrong answers here are silent: too low and
# `X-Forwarded-For` is ignored so every client behind the proxy shares one
# bucket (one abuser locks out a whole customer base); too high and the
# application reads an entry the caller wrote, so a limit exists only on
# paper. See platform/observability/client_ip.py.
#
# 0 is a legitimate answer - it means nothing is in front of this process -
# and stating it is the point.
TRUSTED_PROXY_COUNT = env.int("W360_TRUSTED_PROXY_COUNT", required=True)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if TRUSTED_PROXY_COUNT > 0 else None
)

# --- Transport security --------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 63_072_000  # 2 years
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

LOG_FORMAT = "json"
LOG_LEVEL = env.choice("W360_LOG_LEVEL", ("INFO", "WARNING", "ERROR"), default="INFO")

# SMTP only (Phase 12, G-03): the variable used to be free text, so the console
# or file backend - which print every emailed link - was accepted here.
EMAIL_BACKEND = env.choice(
    "W360_EMAIL_BACKEND",
    ("django.core.mail.backends.smtp.EmailBackend",),
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env.str("W360_EMAIL_HOST", required=True)
DEFAULT_FROM_EMAIL = env.str("W360_DEFAULT_FROM_EMAIL", required=True)

# Every emailed bearer link (password reset, verification, invitations, owner
# activation) and the OIDC login/resume redirects are built on this origin.
# It defaulted to http://localhost:3000 (Phase 12, G-06).
W360_FRONTEND_URL = env.str("W360_FRONTEND_URL", required=True).rstrip("/")

# Required with no default: base.py's development default made the "broker is
# required" check below unfailable, and a production worker would have tried
# the local development broker (Phase 12, G-06).
CELERY_BROKER_URL = env.str("W360_BROKER_URL", required=True)

# Tasks never run inline in production: eager execution would put provider
# latency and provider outages on the request path.
CELERY_TASK_ALWAYS_EAGER = False

# --- Structural assertions ----------------------------------------------
#
# These catch the configuration mistakes that are individually easy to make
# and collectively catastrophic.

env.check(
    not DEBUG,
    "DEBUG must be False in production.",
)
env.check(
    "*" not in ALLOWED_HOSTS,
    "W360_ALLOWED_HOSTS must not contain '*': it defeats Host-header "
    "validation and enables cache-poisoning and password-reset-link attacks.",
)
env.check(
    all(o.startswith("https://") for o in CSRF_TRUSTED_ORIGINS),
    "Every W360_CSRF_TRUSTED_ORIGINS entry must be https://.",
)
env.check(
    SECRET_KEY != "insecure-development-key-do-not-use",  # noqa: S105 - comparison, not a credential
    "W360_SECRET_KEY is still the development default.",
)
env.check(
    DATABASES["default"]["USER"] != "w360_migrator",
    "The application must connect as the non-owner app role, not as the "
    "migration owner. Connecting as the owner is how RLS ends up silently "
    "inactive (R-02).",
)
env.check(
    DATABASES["default"]["PASSWORD"] != "w360_app_pw",  # noqa: S105 - comparison, not a credential
    "W360_DB_APP_PASSWORD is still the local development default.",
)
env.check(
    not METRICS_TOKEN or len(METRICS_TOKEN) >= 32,
    "W360_METRICS_TOKEN must be at least 32 characters (or unset, which keeps "
    "/healthz/metrics closed): it is the only thing between the scrape "
    "endpoint and anyone who can reach an api instance.",
)
env.check(
    DATABASES["default"]["CONN_MAX_AGE"] is not None
    and 0 <= DATABASES["default"]["CONN_MAX_AGE"] <= 3600,
    "CONN_MAX_AGE must be a bounded number of seconds (0-3600), never None: a "
    "connection that is never re-established would never notice a PgBouncer "
    "or DNS change (10-performance-concurrency.md section 4).",
)
env.check(
    DATABASES["default"]["CONN_MAX_AGE"] == 0
    or DATABASES["default"]["CONN_HEALTH_CHECKS"],
    "Persistent connections need CONN_HEALTH_CHECKS: without it the first "
    "request after a PgBouncer restart fails on the dead connection.",
)
env.check(
    0 <= SESSION_REFRESH_INTERVAL_S <= 300,
    "W360_SESSION_REFRESH_INTERVAL_S must be 0-300 s: the idle window may end "
    "up to this much early, and it must stay small against SESSION_COOKIE_AGE.",
)
env.check(
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"],
    "Server-side cursors must be disabled under transaction pooling.",
)
env.check(
    not DATABASES["default"]["ATOMIC_REQUESTS"],
    "ATOMIC_REQUESTS must stay False: the tenancy middleware owns the "
    "transaction that carries the RLS variable (ADR-009).",
)
env.check(
    PASSWORD_HASHERS[0]
    in {
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        # The same hasher with a concurrency bulkhead (2026-10 audit).
        "workeasy360.platform.identity.hashers.BoundedArgon2PasswordHasher",
    },
    "Argon2id must be the first password hasher (12-security.md section 3).",
)
env.check(
    1 <= PASSWORD_HASH_CONCURRENCY <= 16,
    "W360_PASSWORD_HASH_CONCURRENCY must be 1-16 hashes per process: each "
    "holds 100 MiB while it runs.",
)
env.check(
    not REDIS_URL.startswith("redis://") or env.bool("W360_ALLOW_PLAINTEXT_REDIS"),
    "Redis must use TLS (rediss://) in production. Set "
    "W360_ALLOW_PLAINTEXT_REDIS=true only for a VPC-internal deployment where "
    "that is a reviewed decision.",
)
env.check(
    FILE_STORAGE_BACKEND != "local",
    "Local file storage is not durable across container restarts. Configure "
    "W360_FILE_STORAGE_BACKEND=s3.",
)
env.check(
    FILE_STORAGE_BACKEND != "s3" or bool(S3_BUCKET),
    "W360_S3_BUCKET is required when W360_FILE_STORAGE_BACKEND=s3.",
)
env.check(
    not S3_ENDPOINT_URL
    or S3_ENDPOINT_URL.startswith("https://")
    or env.bool("W360_ALLOW_PLAINTEXT_S3"),
    "W360_S3_ENDPOINT_URL must be https:// (or blank for AWS itself): partner "
    "logos and every other stored file would otherwise cross the network in "
    "plaintext. W360_ALLOW_PLAINTEXT_S3=true only for an S3-compatible store on "
    "the same private network, as a reviewed decision (the same rule as Redis).",
)
env.check(
    bool(CELERY_BROKER_URL),
    "W360_BROKER_URL is required.",
)
env.check(
    not CELERY_BROKER_URL.startswith("amqp://")
    or env.bool("W360_ALLOW_PLAINTEXT_BROKER"),
    "The broker must use TLS (amqps://) in production. Set "
    "W360_ALLOW_PLAINTEXT_BROKER=true only for a private-network deployment "
    "where that is a reviewed decision (the same rule as Redis).",
)
env.check(
    W360_FRONTEND_URL.startswith("https://"),
    "W360_FRONTEND_URL must be the https origin of the platform shell: every "
    "emailed link and every OIDC login redirect is built on it.",
)
env.check(
    EMAIL_USE_TLS or EMAIL_USE_SSL or env.bool("W360_ALLOW_PLAINTEXT_SMTP"),
    "SMTP must use TLS in production (W360_EMAIL_USE_TLS for STARTTLS or "
    "W360_EMAIL_USE_SSL for implicit TLS): emails carry one-time sign-in links. "
    "W360_ALLOW_PLAINTEXT_SMTP=true is only for a relay on the same private "
    "network, as a reviewed decision.",
)
env.check(
    not (EMAIL_USE_TLS and EMAIL_USE_SSL),
    "W360_EMAIL_USE_TLS and W360_EMAIL_USE_SSL are mutually exclusive.",
)
env.check(
    "@" in DEFAULT_FROM_EMAIL
    and not DEFAULT_FROM_EMAIL.rsplit("@", 1)[-1].endswith(
        (".test", ".example", ".invalid", ".localhost", "localhost")
    ),
    "W360_DEFAULT_FROM_EMAIL must be a real sending address on a domain the "
    "platform's mail provider is authorised for (SPF/DKIM/DMARC).",
)
env.check(
    OIDC_ISSUER.startswith("https://"),
    "W360_OIDC_ISSUER must be an https origin. It is the `iss` claim every "
    "product pins, and a plaintext issuer means the discovery document and "
    "the key set can both be replaced in transit.",
)
env.check(
    not OIDC_ISSUER.endswith("/"),
    "W360_OIDC_ISSUER must not have a trailing slash: the `iss` claim is "
    "compared by exact string equality (OpenID Connect Core section 3.1.3.7), "
    "so a stray slash makes every product reject every token.",
)
env.check(
    not OIDC_ALLOW_INSECURE_REDIRECT_URIS,
    "W360_OIDC_ALLOW_INSECURE_REDIRECT_URIS must be False in production.",
)
env.check(
    not INTEGRATION_EGRESS_ALLOW_HTTP and not INTEGRATION_EGRESS_ALLOW_LOOPBACK,
    "Outbound integration requests must be https and never loopback in "
    "production (integration/egress).",
)
env.check(
    not AI_PROVIDER_ENDPOINTS,
    "AI provider endpoint overrides are not permitted in production "
    "(intelligence/ai/providers).",
)
env.check(
    TRUSTED_PROXY_COUNT >= 0,
    "W360_TRUSTED_PROXY_COUNT must not be negative. It is a count of proxy "
    "hops in front of this process; see platform/observability/client_ip.py.",
)

env.require_all(context="production configuration")
