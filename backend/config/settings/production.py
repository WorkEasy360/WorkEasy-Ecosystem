"""Production.

This module's job is to *refuse to start* when anything security-critical is
missing or weakened. Every check below corresponds to a control documented in
12-security.md; a deployment that cannot satisfy them is a deployment that
would be insecure in a way nobody would notice until it mattered.

``env.require_all()`` at the end reports every problem at once.
"""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import (
    CELERY_BROKER_URL,
    DATABASES,
    FILE_STORAGE_BACKEND,
    REDIS_URL,
    env,
)

ENVIRONMENT = "production"

# Non-negotiable. DEBUG=True in production leaks settings, SQL and stack
# traces through the technical 500 page.
DEBUG = False

# --- Required secrets ----------------------------------------------------

SECRET_KEY = env.secret("W360_SECRET_KEY", required=True, min_length=50)
DATABASES["default"]["PASSWORD"] = env.secret(
    "W360_DB_APP_PASSWORD", required=True, min_length=16
)
FILE_URL_SIGNING_KEY = env.secret(
    "W360_FILE_URL_SIGNING_KEY", required=True, min_length=32
)

# --- Required hosts / origins --------------------------------------------

ALLOWED_HOSTS = env.list("W360_ALLOWED_HOSTS", required=True)
CSRF_TRUSTED_ORIGINS = env.list("W360_CSRF_TRUSTED_ORIGINS", required=True)
CORS_ALLOWED_ORIGINS = env.list("W360_CORS_ALLOWED_ORIGINS", [])

RELEASE = env.str("W360_RELEASE", required=True)

# --- Transport security --------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 63_072_000  # 2 years
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

LOG_FORMAT = "json"
LOG_LEVEL = env.choice("W360_LOG_LEVEL", ("INFO", "WARNING", "ERROR"), default="INFO")

EMAIL_BACKEND = env.str(
    "W360_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)

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
    "*" not in CORS_ALLOWED_ORIGINS,
    "W360_CORS_ALLOWED_ORIGINS must not contain '*'. CORS is an explicit "
    "allowlist of verified origins (12-security.md section 8).",
)
env.check(
    all(o.startswith("https://") for o in CSRF_TRUSTED_ORIGINS),
    "Every W360_CSRF_TRUSTED_ORIGINS entry must be https://.",
)
env.check(
    all(o.startswith("https://") for o in CORS_ALLOWED_ORIGINS),
    "Every W360_CORS_ALLOWED_ORIGINS entry must be https://.",
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
    DATABASES["default"]["CONN_MAX_AGE"] == 0,
    "CONN_MAX_AGE must be 0 behind PgBouncer: persistent Django connections "
    "pin server connections and defeat the pooler "
    "(10-performance-concurrency.md section 4).",
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
    "django.contrib.auth.hashers.Argon2PasswordHasher" == PASSWORD_HASHERS[0],
    "Argon2id must be the first password hasher (12-security.md section 3).",
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
    bool(CELERY_BROKER_URL),
    "W360_BROKER_URL is required.",
)

env.require_all(context="production configuration")
