"""Settings common to every environment.

Per-environment modules (development, test, production) import * from here and
then tighten. Nothing in this file may be insecure-by-default: development
loosens deliberately and visibly, rather than production having to remember to
tighten.
"""

from __future__ import annotations

from pathlib import Path

from config.env import Env

# backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# repository root
ROOT_DIR = BASE_DIR.parent

env = Env()
env.load_dotenv(ROOT_DIR / ".env")

ENVIRONMENT = env.choice(
    "W360_ENVIRONMENT",
    ("development", "test", "ci", "staging", "production"),
    default="development",
)
RELEASE = env.str("W360_RELEASE", "dev")

# --- Core ---------------------------------------------------------------

# Overridden and *required* in production; the development default exists so a
# fresh clone runs without ceremony and is never used outside development.
SECRET_KEY = env.secret("W360_SECRET_KEY", "insecure-development-key-do-not-use")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("W360_ALLOWED_HOSTS", [])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "identity.User"

# --- Applications --------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

# Ordered by platform layer (P0 -> P3) so that a missing dependency surfaces as
# an import error at the right place rather than as a mysterious app-registry
# failure.
PLATFORM_APPS = [
    "workeasy360.platform.tenancy",
    "workeasy360.platform.observability",
    "workeasy360.platform.identity",
    "workeasy360.platform.organizations",
    "workeasy360.platform.authz",
    "workeasy360.platform.entitlements",
    "workeasy360.platform.audit",
    "workeasy360.platform.files",
    "workeasy360.platform.notifications",
    # P3. The ecosystem front door: the registry of remotely integrated
    # products, their credentials, the signing keys and the OIDC flow. Sits
    # above authz/entitlements/audit because it composes all three, and
    # inside platform/ because everything it owns is identity, tenancy,
    # access and audit - never commerce (ADR-018).
    "workeasy360.platform.ecosystem",
]

INTEGRATION_APPS = [
    "workeasy360.integration.events",
    "workeasy360.integration.idempotency",
]

# Ordered by internal dependency (catalog -> pricing -> billing ->
# subscriptions -> provisioning), so a missing dependency surfaces as an
# import error at the right place rather than as an app-registry failure.
COMMERCIAL_APPS = [
    "workeasy360.commercial.catalog",
    "workeasy360.commercial.pricing",
    "workeasy360.commercial.billing",
    "workeasy360.commercial.subscriptions",
    "workeasy360.commercial.provisioning",
]

# Products are NOT part of this application. Each business product (CRM,
# Books, Inventory, ...) is a separate application in its own repository,
# integrating through the contracts in 16-ecosystem-product-boundary.md.
# The packages under products/ are a reserved, permanently empty namespace:
# they exist only so the import-linter independence contracts and
# test_products_are_structural_placeholders_only have modules to bind to.
# This list must stay empty (ADR-017; test_no_product_apps_are_installed).
PRODUCT_APPS: list[str] = []

INSTALLED_APPS = (
    DJANGO_APPS + PLATFORM_APPS + INTEGRATION_APPS + COMMERCIAL_APPS + PRODUCT_APPS
)

# --- Middleware ----------------------------------------------------------

# Order matters and is load-bearing:
#   1. Correlation first, so every later failure is traceable.
#   2. Security headers before anything that can short-circuit.
#   3. Domain error handling just outside tenancy, so denials become their
#      declared status codes after the transaction has rolled back.
#   4. Tenancy LAST of the request-processing entries, because it needs an
#      authenticated principal and it opens the database transaction that
#      carries the RLS variable for the rest of the request.
MIDDLEWARE = [
    "workeasy360.platform.observability.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workeasy360.platform.observability.middleware.RequestLogMiddleware",
    # Outside tenancy, so a domain exception has already propagated through
    # the request transaction (rolling it back) before the response is built.
    "workeasy360.interfaces.api.errors.DomainErrorMiddleware",
    "workeasy360.platform.tenancy.middleware.TenancyMiddleware",
    # Inside tenancy, so the memo spans exactly the request that has a tenant.
    "workeasy360.platform.entitlements.middleware.EntitlementRequestScopeMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ------------------------------------------------------------

DB_NAME = env.str("W360_DB_NAME", "workeasy360")
DB_HOST = env.str("W360_DB_HOST", "127.0.0.1")
DB_PORT = env.int("W360_DB_PORT", 6442)  # PgBouncer by default, for parity.

# Two roles (ADR-009). The application connects as a non-owner NOBYPASSRLS
# role; migrations connect as the owner. A startup assertion refuses to run if
# the runtime role can bypass RLS.
DB_APP_USER = env.str("W360_DB_APP_USER", "w360_app")
DB_APP_PASSWORD = env.secret("W360_DB_APP_PASSWORD", "w360_app_pw")
DB_MIGRATOR_USER = env.str("W360_DB_MIGRATOR_USER", "w360_migrator")
DB_MIGRATOR_PASSWORD = env.secret("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw")

# Direct (pooler-bypassing) coordinates. Migrations and any operation needing
# session-level state must not go through a transaction-mode pooler.
DB_DIRECT_HOST = env.str("W360_DB_DIRECT_HOST", "127.0.0.1")
DB_DIRECT_PORT = env.int("W360_DB_DIRECT_PORT", 5442)

# Statement timeout per tier (09-failure-resilience.md section 4.2). A runaway
# query is killed by PostgreSQL rather than held by the application.
DB_STATEMENT_TIMEOUT_MS = env.int("W360_DB_STATEMENT_TIMEOUT_MS", 10_000)
DB_LOCK_TIMEOUT_MS = env.int("W360_DB_LOCK_TIMEOUT_MS", 5_000)
DB_IDLE_IN_TRANSACTION_TIMEOUT_MS = env.int(
    "W360_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 15_000
)
DB_CONNECT_TIMEOUT_S = env.int("W360_DB_CONNECT_TIMEOUT_S", 2)

# R-01 outcome. "transaction" routes the web tier through PgBouncer in
# transaction mode; "session" is the documented fallback. Recorded here so the
# decision is configuration, not a code change.
DB_POOL_MODE = env.choice(
    "W360_DB_POOL_MODE", ("transaction", "session", "direct"), default="transaction"
)

# CONN_MAX_AGE = 0 with an external pooler is deliberate: persistent Django
# connections plus PgBouncer means two pools fighting, and idle Django
# connections pin PgBouncer server connections, defeating the pooler
# (10-performance-concurrency.md section 4).
DB_CONN_MAX_AGE = env.int("W360_DB_CONN_MAX_AGE", 0)


def _db_options() -> dict[str, object]:
    # Applied per connection. `options` reaches PostgreSQL as startup
    # parameters, so these hold for every transaction on the connection and
    # survive PgBouncer's reuse of it.
    settings_flags = (
        f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
        f" -c lock_timeout={DB_LOCK_TIMEOUT_MS}"
        f" -c idle_in_transaction_session_timeout="
        f"{DB_IDLE_IN_TRANSACTION_TIMEOUT_MS}"
    )
    return {
        "connect_timeout": DB_CONNECT_TIMEOUT_S,
        "options": settings_flags,
        # Transaction pooling breaks server-side cursors and prepared
        # statements (10-performance-concurrency.md section 4).
        "prepare_threshold": None,
    }


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": DB_APP_USER,
        "PASSWORD": DB_APP_PASSWORD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": DB_CONN_MAX_AGE,
        "CONN_HEALTH_CHECKS": True,
        "ATOMIC_REQUESTS": False,  # tenancy middleware owns the transaction
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "OPTIONS": _db_options(),
        "TEST": {"NAME": env.str("W360_TEST_DB_NAME", "test_workeasy360")},
    }
}

# --- Cache / Redis -------------------------------------------------------

REDIS_URL = env.str("W360_REDIS_URL", "redis://127.0.0.1:6392/0")
# Aggressive by design: the timeout, not the outage, is what causes cascading
# failure. A 5s Redis timeout on every request exhausts the worker pool in
# seconds; 100ms does not (09-failure-resilience.md section 3.1).
REDIS_CONNECT_TIMEOUT_S = env.float("W360_REDIS_CONNECT_TIMEOUT_S", 0.05)
REDIS_OPERATION_TIMEOUT_S = env.float("W360_REDIS_OPERATION_TIMEOUT_S", 0.1)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "w360",
        "OPTIONS": {
            # Aggressive timeouts are the actual cascading-failure control:
            # the timeout, not the outage, is what exhausts a worker pool.
            "socket_connect_timeout": REDIS_CONNECT_TIMEOUT_S,
            "socket_timeout": REDIS_OPERATION_TIMEOUT_S,
        },
    }
}

# NOTE: Django's built-in RedisCache has no `ignore_exceptions` option - that
# belongs to the third-party django-redis package, and passing it here fails
# at connection construction. Redis-outage tolerance is therefore implemented
# where it belongs: every cache read and write on a correctness path is
# wrapped at the call site and falls back to PostgreSQL
# (entitlements/services/read.py, authz/services.py). A Redis outage degrades
# latency, never correctness (09-failure-resilience.md section 3.1).

# --- Sessions ------------------------------------------------------------

# PostgreSQL is the source of truth; Redis is a read-through cache, so a Redis
# outage degrades latency rather than logging everyone out
# (04-identity-authz.md section 3.2).
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "w360_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8h idle
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True  # sliding idle window
SESSION_ABSOLUTE_TIMEOUT = 60 * 60 * 24 * 30  # 30d hard cap

# --- CSRF / security -----------------------------------------------------

CSRF_COOKIE_NAME = "w360_csrf"
CSRF_COOKIE_HTTPONLY = False  # double-submit needs JS to read it
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_USE_SESSIONS = False
CSRF_TRUSTED_ORIGINS = env.list("W360_CSRF_TRUSTED_ORIGINS", [])

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Never wildcarded, never reflected (12-security.md section 8).
CORS_ALLOWED_ORIGINS = env.list("W360_CORS_ALLOWED_ORIGINS", [])
CORS_ALLOW_CREDENTIALS = True

# --- Password policy -----------------------------------------------------

# Argon2id first (12-security.md section 3). The remaining hashers exist only
# so that an older stored hash can be verified and transparently upgraded.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# Minimum 12 characters, no composition rules and no forced rotation: both are
# counterproductive per current NIST guidance and push users toward
# predictable mutations.
PASSWORD_MIN_LENGTH = env.int("W360_PASSWORD_MIN_LENGTH", 12)
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": PASSWORD_MIN_LENGTH},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {
        "NAME": "workeasy360.platform.identity.password_validation."
        "UserAttributeSimilarityValidator"
    },
]

# --- Celery --------------------------------------------------------------

CELERY_BROKER_URL = env.str("W360_BROKER_URL", "amqp://w360:w360_pw@127.0.0.1:5682//")
CELERY_RESULT_BACKEND = env.str("W360_RESULT_BACKEND", "")
CELERY_TASK_DEFAULT_QUEUE = "default"

# R-12: three queue groups, not seven. Task-level routing exists from day one
# so splitting later is a deployment change, not a code change.
CELERY_QUEUE_GROUPS = ("critical", "default", "heavy")

CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_ACKS_LATE = True  # paired with idempotency
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # long tasks must not hoard messages
CELERY_TASK_SOFT_TIME_LIMIT = env.int("W360_TASK_SOFT_TIME_LIMIT", 60)
CELERY_TASK_TIME_LIMIT = env.int("W360_TASK_TIME_LIMIT", 120)
CELERY_BROKER_CONNECTION_TIMEOUT = 1.0
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Must exceed the longest task_time_limit or a slow task is redelivered
    # while still running (R-09).
    "visibility_timeout": env.int("W360_BROKER_VISIBILITY_TIMEOUT", 900),
    "max_retries": 3,
}
# JSON only. pickle is banned including as a Celery serializer
# (12-security.md section 7).
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# --- Scheduled work ------------------------------------------------------
#
# Phase 1 deliberately left Beat unconfigured: the relay and sweeper tasks
# existed but nothing depended on them running. Phase 2 changes that. The
# commercial clock - trials ending, cancellations taking effect, periods
# rolling - has no actor to trigger it, and entitlement drift repair is the
# recovery mechanism the whole provisioning design rests on. Both need a
# scheduler, so the documented trigger for Beat is now reached
# (docs/phase-2/phase-2-status.md, Task 37).
#
# Intervals are deliberately unambitious. Every task here is idempotent, so
# running one late is a delay and running one twice is a no-op; the cost of a
# tight schedule is constant database load for no correctness gain.
CELERY_BEAT_SCHEDULE = {
    "outbox-relay": {
        "task": "integration.events.relay_outbox",
        "schedule": env.float("W360_BEAT_RELAY_S", 5.0),
        "options": {"queue": "critical", "expires": 30},
    },
    "outbox-release-stale-claims": {
        "task": "integration.events.release_stale_claims",
        "schedule": env.float("W360_BEAT_STALE_CLAIMS_S", 300.0),
        "options": {"queue": "default", "expires": 280},
    },
    "commercial-process-due": {
        "task": "commercial.subscriptions.process_due",
        "schedule": env.float("W360_BEAT_COMMERCIAL_CLOCK_S", 300.0),
        "options": {"queue": "default", "expires": 280},
    },
    "entitlement-reconcile-all": {
        "task": "commercial.provisioning.reconcile_all",
        "schedule": env.float("W360_BEAT_RECONCILE_S", 3600.0),
        "options": {"queue": "heavy", "expires": 3500},
    },
    # The recovery path for deliveries that were never queued because the
    # broker was unreachable at commit time. 09-failure-resilience.md names it
    # as the reason a broker blip delays a notification rather than losing it,
    # which is only true if something runs it.
    "notifications-sweep-pending": {
        "task": "platform.notifications.sweep_pending",
        "schedule": env.float("W360_BEAT_NOTIFICATION_SWEEP_S", 300.0),
        "options": {"queue": "default", "expires": 280},
    },
    "outbox-prune-published": {
        "task": "integration.events.prune_published",
        "schedule": env.float("W360_BEAT_PRUNE_S", 86400.0),
        "options": {"queue": "heavy", "expires": 3600},
    },
    # Authorization codes are issued on every product sign-in, live for sixty
    # seconds, and are then worthless. Without this the table grows for the
    # life of the deployment and the redemption lookup - which a user waits
    # for - gets slower with every login that ever happened.
    "ecosystem-prune-authorization-codes": {
        "task": "platform.ecosystem.prune_authorization_codes",
        "schedule": env.float("W360_BEAT_AUTHCODE_PRUNE_S", 3600.0),
        "options": {"queue": "heavy", "expires": 3500},
    },
}

# Bounded retry policy shared by every task (09-failure-resilience.md s4.3).
TASK_MAX_RETRIES = env.int("W360_TASK_MAX_RETRIES", 5)
TASK_RETRY_BACKOFF_BASE_S = env.float("W360_TASK_RETRY_BACKOFF_BASE_S", 2.0)
TASK_RETRY_BACKOFF_CAP_S = env.float("W360_TASK_RETRY_BACKOFF_CAP_S", 3600.0)

# --- Outbox --------------------------------------------------------------

OUTBOX_RELAY_BATCH_SIZE = env.int("W360_OUTBOX_RELAY_BATCH_SIZE", 200)
OUTBOX_MAX_ATTEMPTS = env.int("W360_OUTBOX_MAX_ATTEMPTS", 5)
OUTBOX_RETENTION_DAYS = env.int("W360_OUTBOX_RETENTION_DAYS", 30)

# --- Entitlements --------------------------------------------------------

# Budget from 10-performance-concurrency.md section 3. Measured in Phase 1 by
# the R-14 benchmark; see docs/phase-1/entitlement-benchmark.md.
ENTITLEMENT_CACHE_TTL_S = env.int("W360_ENTITLEMENT_CACHE_TTL_S", 300)
ENTITLEMENT_P50_BUDGET_MS = env.float("W360_ENTITLEMENT_P50_BUDGET_MS", 1.0)
ENTITLEMENT_P95_BUDGET_MS = env.float("W360_ENTITLEMENT_P95_BUDGET_MS", 5.0)

# --- Remote product integration (OIDC) -----------------------------------
#
# The settings behind docs/phase-3/. Every one of them is a security or
# availability trade-off that an operator may need to move without a code
# change, which is why none of them is a constant in the module that uses it.

# The `iss` claim, and the base of the discovery document. Products pin this
# value and reject any token whose issuer differs, so changing it invalidates
# every deployed integration - it is deliberately not derived from the
# request Host header, which is client-supplied and therefore never truthful
# (TB-1).
OIDC_ISSUER = env.str("W360_OIDC_ISSUER", "http://localhost:8000")

# Token lifetimes. The security/availability trade-off is stated in full in
# docs/phase-3/09-product-integration-guide.md section 6: signatures are
# verified offline, so a revoked user keeps a *valid token* until it expires.
# The ID token's lifetime is therefore the maximum revocation window, and it
# is short because the product exchanges it once for its own session rather
# than presenting it per request.
OIDC_ID_TOKEN_TTL_S = env.int("W360_OIDC_ID_TOKEN_TTL_S", 300)
OIDC_ACCESS_TOKEN_TTL_S = env.int("W360_OIDC_ACCESS_TOKEN_TTL_S", 900)

# An authorization code is a bearer credential in a URL, in a browser's
# history and in any referrer header. 60 seconds is ample for a server-side
# exchange and hostile to a stolen code (RFC 6749 section 4.1.2 recommends a
# maximum of ten minutes; there is no reason to take it).
OIDC_AUTHORIZATION_CODE_TTL_S = env.int("W360_OIDC_AUTHORIZATION_CODE_TTL_S", 60)

# Tolerance for clock difference between the platform and a product, applied
# to `exp`/`nbf`/`iat`. Deliberately small: a large window extends the life of
# every expired token by that amount. 30s is well inside what NTP-synchronised
# hosts drift and well under the ID token lifetime.
OIDC_CLOCK_SKEW_S = env.int("W360_OIDC_CLOCK_SKEW_S", 30)

# How old a private_key_jwt client assertion may be. Short, because the
# assertion is single-use by `jti` and a long window enlarges the replay
# cache for no benefit.
OIDC_CLIENT_ASSERTION_MAX_AGE_S = env.int("W360_OIDC_CLIENT_ASSERTION_MAX_AGE_S", 300)

# RS256 or ES256. RS256 is the default because OpenID Connect Core requires
# every implementation to support it, so it is the one algorithm an
# independently written product is guaranteed to verify. ES256 is offered for
# deployments that prefer smaller keys and tokens; the code path is identical.
OIDC_SIGNING_ALGORITHM = env.choice(
    "W360_OIDC_SIGNING_ALGORITHM", ("RS256", "ES256"), default="RS256"
)

# Where EnvSigningKeyProvider looks: a SigningKey row storing
# private_key_ref="k1" resolves to W360_OIDC_SIGNING_KEY_K1. The private half
# never enters the database (contracts/ports/signing.py).
OIDC_SIGNING_KEY_ENV_PREFIX = env.str(
    "W360_OIDC_SIGNING_KEY_ENV_PREFIX", "W360_OIDC_SIGNING_KEY_"
)

# How long a product should wait before re-asking "may this org still use
# me?". Returned to the product in every verification response so the cadence
# is the platform's to change, not each product's to guess.
OIDC_ENTITLEMENT_REVALIDATE_AFTER_S = env.int(
    "W360_OIDC_ENTITLEMENT_REVALIDATE_AFTER_S", 900
)

# http:// redirect URIs on loopback hosts. Permitted for local development
# and the conformance fixtures; production overrides this to False and an
# assertion in production.py pins it.
OIDC_ALLOW_INSECURE_REDIRECT_URIS = env.bool(
    "W360_OIDC_ALLOW_INSECURE_REDIRECT_URIS", ENVIRONMENT != "production"
)

# Targeted limits on the externally reachable trust boundary, as
# (max_requests, window_seconds). Nothing else in the platform is throttled by
# this: a global throttle would make one product's misbehaviour everyone's
# outage, which is the failure mode the ecosystem design exists to prevent
# (docs/phase-3/11-security-threat-model.md T-12).
ECOSYSTEM_RATE_LIMITS = {
    # Per (client, client IP). Generous: a real launcher sends one per user
    # sign-in, and an over-tight limit here breaks a customer's morning.
    "authorize": (env.int("W360_RL_AUTHORIZE", 60), 60),
    # Per client. A token exchange follows an authorization, so the ceiling
    # only has to exceed the authorize rate.
    "token": (env.int("W360_RL_TOKEN", 120), 60),
    # Per client. Products revalidate on OIDC_ENTITLEMENT_REVALIDATE_AFTER_S,
    # so this supports a large tenant population without being a free
    # enumeration budget.
    "verify": (env.int("W360_RL_VERIFY", 600), 60),
    # Per client, counted only on *failed* authentication. An order of
    # magnitude tighter than the success path: credential brute force is the
    # thing this one exists for.
    "client_auth_failure": (env.int("W360_RL_CLIENT_AUTH_FAILURE", 10), 60),
}

# --- Files ---------------------------------------------------------------

FILE_STORAGE_BACKEND = env.choice(
    "W360_FILE_STORAGE_BACKEND", ("local", "s3"), default="local"
)
FILE_STORAGE_ROOT = Path(env.str("W360_FILE_STORAGE_ROOT", str(ROOT_DIR / ".files")))
FILE_MAX_BYTES = env.int("W360_FILE_MAX_BYTES", 25 * 1024 * 1024)
FILE_URL_TTL_SECONDS = env.int("W360_FILE_URL_TTL_SECONDS", 900)
# Signing key for local download URLs. Distinct from SECRET_KEY so that
# rotating one does not invalidate the other.
FILE_URL_SIGNING_KEY = env.secret("W360_FILE_URL_SIGNING_KEY", "") or SECRET_KEY

# --- Notifications -------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = env.str("W360_DEFAULT_FROM_EMAIL", "no-reply@workeasy360.test")
EMAIL_CONNECT_TIMEOUT_S = env.float("W360_EMAIL_CONNECT_TIMEOUT_S", 2.0)
EMAIL_READ_TIMEOUT_S = env.float("W360_EMAIL_READ_TIMEOUT_S", 10.0)
NOTIFICATION_MAX_ATTEMPTS = env.int("W360_NOTIFICATION_MAX_ATTEMPTS", 5)

# --- Observability -------------------------------------------------------

LOG_LEVEL = env.choice(
    "W360_LOG_LEVEL", ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), default="INFO"
)
LOG_FORMAT = env.choice("W360_LOG_FORMAT", ("json", "console"), default="json")
READINESS_CACHE_SECONDS = env.int("W360_READINESS_CACHE_SECONDS", 5)

# --- i18n ----------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
