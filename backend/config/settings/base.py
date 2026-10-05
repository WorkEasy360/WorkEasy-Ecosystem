"""Settings common to every environment.

Per-environment modules (development, test, production) import * from here and
then tighten. Nothing in this file may be insecure-by-default: development
loosens deliberately and visibly, rather than production having to remember to
tighten.
"""

from __future__ import annotations

import base64
import hashlib
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

# Ordered by platform layer (P0 -> P4) so that a missing dependency surfaces as
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
    # P3 (Phase 5, ADR-023). Organization onboarding: the orchestrating use
    # case that composes organizations, identity, authz and audit to create
    # an organization and bootstrap its owner in one transaction. Same tier
    # and same reason as ecosystem - it composes several P0-P2 modules that
    # do not import each other.
    "workeasy360.platform.onboarding",
    # P4 (Phase 4, ADR-021). The app launcher: the one authoritative
    # "can this member launch this product" decision, and the app-directory
    # read model built from it. Sits above ecosystem because it composes
    # ecosystem's integration/launch data in addition to everything
    # ecosystem itself already composes.
    "workeasy360.platform.launcher",
    # P4 (Phase 8 extension). Organization administration: the organization
    # console's use cases - invitations, roles, suspension, sign-out, access
    # policy, dashboard. Composes notifications (P3) for the invitation email,
    # so it sits beside launcher; it owns no tables.
    "workeasy360.platform.administration",
]

# Ordered by internal layer (ADR-024): the timer table and the event log
# first, then connectors < webhooks < automation < routing, so a missing
# dependency surfaces as an import error at the right place.
INTEGRATION_APPS = [
    "workeasy360.integration.events",
    "workeasy360.integration.idempotency",
    "workeasy360.integration.scheduling",
    "workeasy360.integration.connectors",
    "workeasy360.integration.webhooks",
    "workeasy360.integration.automation",
    "workeasy360.integration.routing",
    # Phase 11: the bounded reachability check of registered products. Tier 3,
    # above egress (its only way out) and beside the rest: nothing imports it
    # and it changes no access decision.
    "workeasy360.integration.health",
]

# Phase 8 (ADR-030/031/032). The shared AI, RAG and analytics platform:
# a top-level package beside commercial/ (neither imports the other) and above
# integration/, whose egress boundary, due-work timer and routed events it
# builds on. Ordered by its own internal layering: the model gateway and the
# analytics store first, retrieval above the gateway, skill invocation on top.
INTELLIGENCE_APPS = [
    "workeasy360.intelligence.ai",
    "workeasy360.intelligence.analytics",
    "workeasy360.intelligence.rag",
    "workeasy360.intelligence.gateway",
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
    # Phase 8 extension. Beside provisioning: invoices, hosted checkout
    # through provider adapters, webhooks, refunds and dunning. Reads
    # subscriptions and billing; moves subscription status only through
    # subscriptions/system.py.
    "workeasy360.commercial.payments",
    # Phase 7. Above provisioning: a partner sells and provisions through the
    # ordinary commercial services and must be able to import them; the
    # commission ledger reads partners and sales, so it sits above both.
    "workeasy360.commercial.partners",
    "workeasy360.commercial.commissions",
    # Owns no tables: an installed app only so its management command is found.
    "workeasy360.commercial.controlplane",
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
    DJANGO_APPS
    + PLATFORM_APPS
    + INTEGRATION_APPS
    + INTELLIGENCE_APPS
    + COMMERCIAL_APPS
    + PRODUCT_APPS
)

# --- Middleware ----------------------------------------------------------

# Order matters and is load-bearing:
#   1. Correlation first, so every later failure is traceable.
#   2. Security headers before anything that can short-circuit.
#   3. Domain error handling just outside tenancy, so denials become their
#      declared status codes. NOTE: Django calls process_exception inside
#      the innermost handler, i.e. still inside tenancy's transaction, and
#      tenancy rolls back only a >= 500 response - so a write made before a
#      4xx domain error COMMITS. Validate before the first write, or keep the
#      write inside an inner atomic() the error propagates out of (D-016).
#   4. Tenancy LAST of the request-processing entries, because it needs an
#      authenticated principal and it opens the database transaction that
#      carries the RLS variable for the rest of the request.
MIDDLEWARE = [
    # First, i.e. OUTERMOST: Django runs the list top-down on the way in and
    # bottom-up on the way out, so only the first entry sees every response -
    # including one an inner middleware builds without calling the view, such
    # as tenancy's 404/402 denials. (It used to sit last, the *innermost*
    # position, and those responses left without CSP or ``no-store``.)
    "workeasy360.platform.observability.middleware.SecurityHeadersMiddleware",
    # Before the probes, so a failing probe's log line carries an id too.
    "workeasy360.platform.observability.middleware.CorrelationIdMiddleware",
    # Orchestrator probes, before the https redirect and host validation below
    # would refuse a load balancer's plain-http, IP-addressed probe (Phase 12).
    # Probes therefore skip the request log and HTTP metrics as well.
    "workeasy360.platform.observability.middleware.HealthProbeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Django's SessionMiddleware, refreshing the idle window at most once per
    # SESSION_REFRESH_INTERVAL_S rather than on every request (2026-10 audit).
    "workeasy360.platform.identity.middleware.SlidingSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Immediately after: every view downstream must see a revoked session as
    # logged out, not merely be able to check for it (Phase 4, Task 33).
    "workeasy360.platform.identity.middleware.SessionEpochMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workeasy360.platform.observability.middleware.RequestLogMiddleware",
    # Converts domain exceptions to their declared status. It runs inside
    # tenancy's transaction (see note 3 above): a 4xx does not roll back.
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

# Migrations use the owner role over a direct connection; both are read by
# config/settings/migration.py (W360_DB_MIGRATOR_*, W360_DB_DIRECT_*).

# Statement timeout per tier (09-failure-resilience.md section 4.2). A runaway
# query is killed by PostgreSQL rather than held by the application.
DB_STATEMENT_TIMEOUT_MS = env.int("W360_DB_STATEMENT_TIMEOUT_MS", 10_000)
DB_LOCK_TIMEOUT_MS = env.int("W360_DB_LOCK_TIMEOUT_MS", 5_000)
DB_IDLE_IN_TRANSACTION_TIMEOUT_MS = env.int(
    "W360_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 15_000
)
DB_CONNECT_TIMEOUT_S = env.int("W360_DB_CONNECT_TIMEOUT_S", 2)


# Persistent connections, with a bounded lifetime (2026-10 audit). PgBouncer
# runs in *transaction* pooling (deploy/pgbouncer/pgbouncer.ini): it lends a
# client a server connection only for the length of one transaction, so an
# idle Django connection pins nothing. What CONN_MAX_AGE = 0 did cost was a new
# TCP connection, a DNS lookup and a SCRAM login on every request: measured on
# the staging stack at ~6 ms and ~2.8 ms of API CPU per request, a quarter of
# the in-process request time (10-performance-concurrency.md section 4).
# Reuse is safe because nothing sets session-level state: the RLS variable is
# set_config(..., true) only (B6); prepared statements and server-side cursors
# are off. CONN_HEALTH_CHECKS re-checks a reused connection once per request,
# so a PgBouncer restart costs a reconnect, not an error.
DB_CONN_MAX_AGE = env.int("W360_DB_CONN_MAX_AGE", 300)


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
        # Django's RedisCache plus a per-process circuit breaker (Phase 12):
        # once Redis fails, calls fail fast for a few seconds so each caller's
        # PostgreSQL fallback runs at once instead of after its own timeout
        # (platform/observability/cache.py).
        "BACKEND": "workeasy360.platform.observability.cache.ResilientRedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "w360",
        "OPTIONS": {
            # Aggressive timeouts are the actual cascading-failure control:
            # the timeout, not the outage, is what exhausts a worker pool.
            "socket_connect_timeout": REDIS_CONNECT_TIMEOUT_S,
            "socket_timeout": REDIS_OPERATION_TIMEOUT_S,
            # One request per process pays for discovering an outage (a
            # vanished host costs a DNS lookup, ~4 s on the staging stack);
            # the cooldown bounds how often that happens (Phase 12).
            "breaker_cooldown_s": env.float("W360_REDIS_BREAKER_COOLDOWN_S", 30.0),
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
#
# Phase 8 extension: Django's database backend, writing ``identity_session``
# rows that know whose session they are - per-session sign-out, the "where am
# I signed in" list, and the absolute lifetime below, finally enforced
# (platform/identity/sessions.py). Deploying it signs every browser out once.
SESSION_ENGINE = "workeasy360.platform.identity.sessions"
SESSION_COOKIE_NAME = "w360_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8h idle
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
# The sliding idle window is refreshed at most once per
# SESSION_REFRESH_INTERVAL_S (platform.identity.middleware.SlidingSessionMiddleware),
# not on every request (2026-10 audit). Saving every request was an UPDATE and
# a commit on every read - the public catalog included - ~3 ms and one pooler
# transaction each. The interval can only make an idle session end earlier
# (by at most the interval), never later; a modified session still saves at
# once, and revocation is enforced when the row is loaded.
SESSION_SAVE_EVERY_REQUEST = False
SESSION_REFRESH_INTERVAL_S = env.int("W360_SESSION_REFRESH_INTERVAL_S", 60)
SESSION_ABSOLUTE_TIMEOUT = 60 * 60 * 24 * 30  # 30d hard cap

# --- CSRF / security -----------------------------------------------------

CSRF_COOKIE_NAME = "w360_csrf"
CSRF_COOKIE_HTTPONLY = False  # double-submit needs JS to read it
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_USE_SESSIONS = False
# JSON in the API's error shape, not Django's HTML page (Phase 12).
CSRF_FAILURE_VIEW = "workeasy360.interfaces.api.errors.csrf_failure"
CSRF_TRUSTED_ORIGINS = env.list("W360_CSRF_TRUSTED_ORIGINS", [])

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# --- Trusted proxy topology (D-009) --------------------------------------
#
# How many hops of infrastructure *we* control sit in front of this process.
# 0 = directly exposed (local, CI, a bare server); 1 = one ALB/ingress;
# 2 = CloudFront in front of an ALB. It is the one fact the application
# cannot discover about itself, and both wrong answers fail silently, so
# production requires it to be stated rather than defaulted
# (config/settings/production.py).
#
# Read by platform.observability.client_ip, which explains the whole rule.
TRUSTED_PROXY_COUNT = env.int("W360_TRUSTED_PROXY_COUNT", 0)
CLIENT_IP_HEADER = "HTTP_" + env.str(
    "W360_CLIENT_IP_HEADER", "X-Forwarded-For"
).upper().replace("-", "_")

# Conditional on the same declaration, for the same reason. X-Forwarded-Proto
# is a plain request header: trusting it with nothing in front lets any caller
# tell Django "this arrived over TLS", which defeats SECURE_SSL_REDIRECT and
# makes `request.is_secure()` - and therefore secure-cookie handling - answer
# to the client rather than to the transport.
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if TRUSTED_PROXY_COUNT > 0 else None
)

# --- Frontend origin (Phase 4) --------------------------------------------
#
# The platform's own Next.js shell (docs/phase-4/01-frontend-architecture.md).
# Used only to build the two URLs a *browser* is redirected to when an OIDC
# authorization request cannot proceed without a human first - the login page
# and the organization chooser (PendingAuthorization, Tasks 31/32). Never
# used to build a URL a *product* is sent to; those come from
# ProductIntegration's own registered manifest (Task 55), never from this
# setting, and this value is not itself a registered redirect URI.
W360_FRONTEND_URL = env.str("W360_FRONTEND_URL", "http://localhost:3000").rstrip("/")

# --- Content Security Policy (Phase 4) ------------------------------------
#
# The CSP trigger stated in the Phase 4 brief: Phase 3 carried no HTML
# surface (asserted unconditionally - zero templates in the repository) and
# so had nothing to scope a policy around. The frontend changes that. This
# platform serves no server-rendered HTML of its own even now - the Next.js
# app is a separate deployment - but the API origin still answers browser
# requests carrying the session cookie, and a strict CSP on *every* response
# this Django process emits is a real defence-in-depth layer against a
# markup-injection bug in any future HTML this process ever serves, at zero
# cost to a JSON API that renders nothing (docs/phase-4/08-csp-and-browser-
# security.md).
#
# default-src 'none' rather than 'self': there is no legitimate script,
# style or media origin for a JSON API to declare, so the safer default is
# refusing everything and naming exceptions, not the reverse.
CONTENT_SECURITY_POLICY = {
    "default-src": ["'none'"],
    "frame-ancestors": ["'none'"],  # belt-and-braces alongside X-Frame-Options
    "base-uri": ["'none'"],
    "form-action": ["'none'"],
}
PERMISSIONS_POLICY = {
    "camera": [],
    "microphone": [],
    "geolocation": [],
    "payment": [],
}

# --- Password policy -----------------------------------------------------

# Argon2id first (12-security.md section 3). The remaining hashers exist only
# so that an older stored hash can be verified and transparently upgraded.
#
# The first is Django's Argon2 hasher, unchanged, with a per-process bulkhead:
# at most PASSWORD_HASH_CONCURRENCY hashes at once (100 MiB and eight lanes
# each), so a burst of sign-ins cannot take every thread's worth of memory and
# CPU (2026-10 audit, platform/identity/hashers.py).
PASSWORD_HASHERS = [
    "workeasy360.platform.identity.hashers.BoundedArgon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]
PASSWORD_HASH_CONCURRENCY = env.int("W360_PASSWORD_HASH_CONCURRENCY", 2)

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
    # while still running (R-09). The longest is 1,200 s (reconcile_all, the
    # commission sweep, prune_activity); it was 900 until Phase 12 (G-36).
    # RabbitMQ ignores it (its own consumer_timeout, 30 min by default, must
    # likewise exceed 1,200 s); SQS and Redis transports honour it.
    "visibility_timeout": env.int("W360_BROKER_VISIBILITY_TIMEOUT", 3600),
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
    # Phase 6. The integration platform's one sweeper: every due webhook
    # retry, automation execution, schedule firing and routing job - and the
    # recovery path for work whose after-commit enqueue was lost to a broker
    # outage (Task 68). Frequent because a retry's precision is this
    # interval; cheap because the claim is one indexed statement over due
    # rows only (integration/scheduling).
    "integration-dispatch-due": {
        "task": "integration.scheduling.dispatch_due",
        "schedule": env.float("W360_BEAT_INTEGRATION_DISPATCH_S", 5.0),
        "options": {"queue": "default", "expires": 30},
    },
    # Phase 11. A glance at each registered product's front door, so the
    # dashboard can say "temporarily unavailable" from a stored answer instead
    # of making a request while a customer's page renders. Telemetry only.
    "integration-product-health": {
        "task": "integration.health.check_all",
        "schedule": env.float("W360_BEAT_PRODUCT_HEALTH_S", 300.0),
        "options": {"queue": "heavy", "expires": 240},
    },
    "integration-prune": {
        "task": "integration.routing.prune",
        "schedule": env.float("W360_BEAT_INTEGRATION_PRUNE_S", 86400.0),
        "options": {"queue": "heavy", "expires": 3600},
    },
    # Phase 7. The guarantee behind the after-sale commission listener:
    # derives every commission from durable sale facts, idempotent through the
    # ledger's unique key. Heavy, never critical (Task 101).
    "commissions-sweep": {
        "task": "commercial.commissions.sweep",
        "schedule": env.float("W360_BEAT_COMMISSION_SWEEP_S", 3600.0),
        "options": {"queue": "heavy", "expires": 3500},
    },
    "commissions-prune-referrals": {
        "task": "commercial.commissions.prune_referrals",
        "schedule": env.float("W360_BEAT_REFERRAL_PRUNE_S", 86400.0),
        "options": {"queue": "heavy", "expires": 3600},
    },
    # Domain control can lapse (a name expires, a record is removed). A
    # verified domain is re-checked and disabled after repeated misses. Heavy:
    # a run is up to 200 DNS lookups of several seconds each, which must never
    # hold a worker that subscription due-processing is waiting for.
    "partners-recheck-domains": {
        "task": "commercial.partners.recheck_domains",
        "schedule": env.float("W360_BEAT_DOMAIN_RECHECK_S", 21600.0),
        "options": {"queue": "heavy", "expires": 21000},
    },
    # Phase 8. Retention for AI execution metadata and results, RAG
    # tombstones and analytics facts and aggregates. Everything else the
    # intelligence platform does runs off the integration due-work timer
    # (reservations, AI jobs, indexing, projection), so it needs no beat entry
    # of its own. Heavy, never critical (Task 40).
    "intelligence-prune": {
        "task": "intelligence.prune",
        "schedule": env.float("W360_BEAT_INTELLIGENCE_PRUNE_S", 86400.0),
        "options": {"queue": "heavy", "expires": 3600},
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

# How long a parked authorization request survives while the browser
# completes login or organization selection at the platform's own UI (Phase
# 4, PendingAuthorization). Long enough for a human to type a password and,
# if needed, pick an organization; short enough that an abandoned resume link
# does not stay live in someone's inbox or browser history for long. Compare
# OIDC_AUTHORIZATION_CODE_TTL_S, which is 60s because it crosses no human
# interaction at all - this one deliberately does.
OIDC_PENDING_AUTHORIZATION_TTL_S = env.int("W360_OIDC_PENDING_AUTHORIZATION_TTL_S", 600)

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
    # Per caller address. Phase 4's authorization-resume endpoint is reached
    # before any client is known, so it cannot be keyed on one.
    "resume": (env.int("W360_RL_RESUME", 60), 60),
    # Phase 6, event ingestion (Task 51). Per product client - the budget
    # one product has, whatever organizations it publishes for...
    "event_ingest": (env.int("W360_RL_EVENT_INGEST", 3000), 60),
    # ...and per (product client, organization), so one busy tenant cannot
    # spend its product's whole budget and starve that product's others.
    "event_ingest_org": (env.int("W360_RL_EVENT_INGEST_ORG", 600), 60),
    # Phase 8 (Task 57). AI is bounded per product client, per (client,
    # organization) and per (client, organization, skill) - never by caller
    # address, which says nothing about a server-to-server caller. These are
    # the *rate* guard and degrade with the cache (Task 100); spend and
    # concurrency are decided by the database and never degrade open.
    "ai_invoke": (env.int("W360_RL_AI_INVOKE", 1200), 60),
    "ai_invoke_org": (env.int("W360_RL_AI_INVOKE_ORG", 300), 60),
    "ai_invoke_skill": (env.int("W360_RL_AI_INVOKE_SKILL", 120), 60),
    # Registering skills and metrics is rare; a loop doing it is a bug.
    "ai_registry": (env.int("W360_RL_AI_REGISTRY", 60), 60),
    "rag_ingest": (env.int("W360_RL_RAG_INGEST", 1200), 60),
    "rag_ingest_org": (env.int("W360_RL_RAG_INGEST_ORG", 300), 60),
    "rag_retrieve_org": (env.int("W360_RL_RAG_RETRIEVE_ORG", 300), 60),
    "analytics_query_org": (env.int("W360_RL_ANALYTICS_QUERY_ORG", 300), 60),
}

# --- Platform authentication rate limits (Phase 4 audit, D-009) ----------
#
# The ecosystem buckets above guard the *product* trust boundary. These guard
# the platform's own, which Phase 4 made publicly reachable for the first
# time and which Phase 4 shipped unthrottled.
#
# Login is counted on two independent keys, because each is blind to what
# the other catches:
#
# - Per address, `login_ip`. The one that matters most. It catches one
#   address spraying a single password across thousands of accounts, which
#   no per-account counter can see, and it is the CPU guard: Argon2id is
#   tuned to ~250ms of memory-hard work and Django hashes a dummy password
#   even for an unknown email (its own timing-attack defence), so an
#   unthrottled login endpoint is a workload anyone on the internet can
#   schedule.
# - Per account, `login_account`. Bounds guesses against one person
#   regardless of how many addresses they come from - a botnet defeats the
#   address bucket entirely, and this is what is left.
#
# **What this does not fix**, stated because the alternative is implying it
# does: `identity.services` locks an account for fifteen minutes after ten
# failures, and ten requests per fifteen minutes is under any throttle worth
# having, so an attacker who wants a chosen account locked can still manage
# it. The lockout is time-boxed precisely because it is a denial-of-service
# lever; removing that lever means changing the lockout policy, not tuning a
# limiter. `login_account` is therefore deliberately set *above*
# MAX_FAILED_ATTEMPTS: below it, the throttle would mask the lockout and a
# genuinely locked-out user would be told "too many requests" instead of
# being told to wait, which is worse for them and no harder for an attacker.
#
# The authorization-resume endpoint is throttled too, but as an
# `ECOSYSTEM_RATE_LIMITS` bucket, because it is part of the OIDC flow and
# answers in the protocol's own error shape.
AUTH_RATE_LIMITS = {
    "login_ip": (env.int("W360_RL_LOGIN_IP", 30), 60),
    "login_account": (env.int("W360_RL_LOGIN_ACCOUNT", 20), 60),
}

# Phase 5, Task 11. Deliberately generous: this is abuse protection (a
# scripted loop), not a business limit on how many organizations a real
# customer may have - the brief explicitly forbids inventing the latter
# without a product requirement.
ORGANIZATION_CREATION_RATE_LIMITS = {
    "org_create_ip": (env.int("W360_RL_ORG_CREATE_IP", 20), 60),
    "org_create_account": (env.int("W360_RL_ORG_CREATE_ACCOUNT", 10), 60),
}

# Phase 7, Task 87. High-cost or abusable control-plane and partner
# operations. Per *actor* (the authenticated user) and per address, both,
# because the abuse is either one account scripting a loop or one address
# rotating accounts. The window is one minute unless stated. Public endpoints
# are limited on address alone - they have no account.
CONTROL_PLANE_RATE_LIMITS = {
    # Phase 8 extension: the organization console and account pages.
    "invite_user": (env.int("W360_RL_INVITE_USER", 30), 60),
    "invite_ip": (env.int("W360_RL_INVITE_IP", 60), 60),
    "invitation_public_ip": (env.int("W360_RL_INVITATION_PUBLIC_IP", 20), 60),
    "password_change_user": (env.int("W360_RL_PASSWORD_CHANGE_USER", 5), 60),
    "password_change_ip": (env.int("W360_RL_PASSWORD_CHANGE_IP", 20), 60),
    "session_revoke_user": (env.int("W360_RL_SESSION_REVOKE_USER", 20), 60),
    "session_revoke_ip": (env.int("W360_RL_SESSION_REVOKE_IP", 60), 60),
    "communication_test_user": (env.int("W360_RL_COMMUNICATION_TEST_USER", 5), 60),
    "communication_test_ip": (env.int("W360_RL_COMMUNICATION_TEST_IP", 20), 60),
    "payment_start_user": (env.int("W360_RL_PAYMENT_START_USER", 10), 60),
    "payment_start_ip": (env.int("W360_RL_PAYMENT_START_IP", 30), 60),
    "payment_webhook_ip": (env.int("W360_RL_PAYMENT_WEBHOOK_IP", 600), 60),
    "partner_provision_user": (env.int("W360_RL_PARTNER_PROVISION_USER", 20), 60),
    "partner_provision_ip": (env.int("W360_RL_PARTNER_PROVISION_IP", 60), 60),
    "domain_verify_user": (env.int("W360_RL_DOMAIN_VERIFY_USER", 10), 60),
    "domain_verify_ip": (env.int("W360_RL_DOMAIN_VERIFY_IP", 30), 60),
    "diagnose_user": (env.int("W360_RL_DIAGNOSE_USER", 30), 60),
    "diagnose_ip": (env.int("W360_RL_DIAGNOSE_IP", 90), 60),
    "public_branding_ip": (env.int("W360_RL_PUBLIC_BRANDING_IP", 120), 60),
    "public_referral_ip": (env.int("W360_RL_PUBLIC_REFERRAL_IP", 20), 60),
    "referral_convert_user": (env.int("W360_RL_REFERRAL_CONVERT_USER", 10), 60),
}

# Phase 7. Custom-domain verification and commission timing.
#: Hostnames the platform itself serves; a partner may never claim them or a
#: subdomain of them (comma-separated in the environment).
W360_PLATFORM_DOMAINS = tuple(
    d.strip()
    for d in env.str("W360_PLATFORM_DOMAINS", "workeasy360.com").split(",")
    if d.strip()
)
#: Resolvers the platform asks for ownership proof. Empty = the host's own.
W360_DNS_RESOLVERS = tuple(
    r.strip() for r in env.str("W360_DNS_RESOLVERS", "").split(",") if r.strip()
)
#: The port of those resolvers (only used when W360_DNS_RESOLVERS is set).
W360_DNS_PORT = env.int("W360_DNS_PORT", 53)
W360_DNS_TIMEOUT_SECONDS = env.int("W360_DNS_TIMEOUT_SECONDS", 3)
W360_DNS_LIFETIME_SECONDS = env.int("W360_DNS_LIFETIME_SECONDS", 5)
#: Days a commission is held before it is payable (the refund window).
W360_COMMISSION_HOLDBACK_DAYS = env.int("W360_COMMISSION_HOLDBACK_DAYS", 30)
#: Retention: unconverted referral captures live this long past expiry.
W360_REFERRAL_CAPTURE_DAYS = env.int("W360_REFERRAL_CAPTURE_DAYS", 90)
W360_REFERRAL_RETENTION_DAYS = env.int("W360_REFERRAL_RETENTION_DAYS", 180)

# Phase 5, Task 39/58. Tighter than login's: a 6-digit TOTP code has ~20 bits
# of entropy, so an online guesser is a real threat an unthrottled endpoint
# would hand a meaningful window to.
MFA_STEP_UP_RATE_LIMITS = {
    "step_up_ip": (env.int("W360_RL_STEP_UP_IP", 20), 60),
    "step_up_account": (env.int("W360_RL_STEP_UP_ACCOUNT", 8), 60),
}

# --- Integration platform (Phase 6) ----------------------------------------
#
# Every number below bounds something a customer, a product or a failing
# endpoint could otherwise grow without limit. docs/phase-6/ gives the
# reasoning for each; they are settings rather than constants so an operator
# can move one without a release.

# Outbound HTTP (integration/egress). Timeouts are 09-failure-resilience.md
# section 4.2's "customer webhook delivery" row.
INTEGRATION_EGRESS_CONNECT_TIMEOUT_S = env.float("W360_EGRESS_CONNECT_TIMEOUT_S", 3.0)
INTEGRATION_EGRESS_READ_TIMEOUT_S = env.float("W360_EGRESS_READ_TIMEOUT_S", 10.0)
INTEGRATION_EGRESS_TOTAL_TIMEOUT_S = env.float("W360_EGRESS_TOTAL_TIMEOUT_S", 15.0)
INTEGRATION_EGRESS_DNS_TIMEOUT_S = env.float("W360_EGRESS_DNS_TIMEOUT_S", 2.0)
INTEGRATION_EGRESS_MAX_RESPONSE_BYTES = env.int("W360_EGRESS_MAX_RESPONSE_BYTES", 65_536)
INTEGRATION_EGRESS_MAX_REQUEST_BYTES = env.int("W360_EGRESS_MAX_REQUEST_BYTES", 262_144)
INTEGRATION_EGRESS_ALLOWED_PORTS = [
    int(p) for p in env.list("W360_EGRESS_ALLOWED_PORTS", ["443", "8443"])
]
# Development and the test receiver only; production pins both False.
INTEGRATION_EGRESS_ALLOW_HTTP = env.bool("W360_EGRESS_ALLOW_HTTP", False)
INTEGRATION_EGRESS_ALLOW_LOOPBACK = env.bool("W360_EGRESS_ALLOW_LOOPBACK", False)

# Connection credentials (integration/connectors/secrets.py). Comma-separated
# key materials; the first seals, every one opens - so the master key rotates
# by prepending a new one. Distinct from every other key in the platform;
# production requires it (production.py).
_INTEGRATION_SECRET_MATERIALS = [
    m.strip()
    for m in env.secret("W360_INTEGRATION_SECRETS_KEYS", "").split(",")
    if m.strip()
] or [f"{SECRET_KEY}:integration-secrets"]
INTEGRATION_SECRET_KEYS = [
    base64.urlsafe_b64encode(hashlib.sha256(m.encode("utf-8")).digest())
    for m in _INTEGRATION_SECRET_MATERIALS
]
# How long the previous signing secret keeps signing after a rotation.
INTEGRATION_SECRET_OVERLAP_S = env.int("W360_INTEGRATION_SECRET_OVERLAP_S", 86_400)

INTEGRATION_MAX_CONNECTIONS = env.int("W360_INTEGRATION_MAX_CONNECTIONS", 50)
# Circuit breaker per connection (Task 49): opens after this many consecutive
# failures for base * 2^(failures - threshold) seconds, capped; the
# connection is suspended outright after the auto-suspend count (Task 50).
INTEGRATION_CIRCUIT_FAILURE_THRESHOLD = env.int("W360_INTEGRATION_CIRCUIT_THRESHOLD", 5)
INTEGRATION_CIRCUIT_BASE_S = env.float("W360_INTEGRATION_CIRCUIT_BASE_S", 30.0)
INTEGRATION_CIRCUIT_CAP_S = env.float("W360_INTEGRATION_CIRCUIT_CAP_S", 3600.0)
INTEGRATION_AUTO_SUSPEND_FAILURES = env.int("W360_INTEGRATION_AUTO_SUSPEND", 50)

# Inbound events (Task 11).
INTEGRATION_EVENT_MAX_BYTES = env.int("W360_INTEGRATION_EVENT_MAX_BYTES", 65_536)
INTEGRATION_EVENT_MAX_DEPTH = 8
INTEGRATION_EVENT_MAX_NODES = 2_000
INTEGRATION_EVENT_MAX_STRING = 8_192
INTEGRATION_EVENT_MAX_ARRAY = 500

# Webhook delivery (Tasks 19-25).
WEBHOOK_MAX_ATTEMPTS = env.int("W360_WEBHOOK_MAX_ATTEMPTS", 10)
WEBHOOK_RETRY_BASE_S = env.float("W360_WEBHOOK_RETRY_BASE_S", 30.0)
WEBHOOK_RETRY_CAP_S = env.float("W360_WEBHOOK_RETRY_CAP_S", 7200.0)
# The longest an endpoint may defer us with Retry-After (Task 21).
WEBHOOK_RETRY_AFTER_MAX_S = env.float("W360_WEBHOOK_RETRY_AFTER_MAX_S", 3600.0)
WEBHOOK_DELIVERY_MAX_AGE_S = env.int("W360_WEBHOOK_DELIVERY_MAX_AGE_S", 3 * 86_400)
# Must exceed the delivery task's hard time limit (75 s), so a worker is
# always killed before another may legitimately re-claim its attempt.
WEBHOOK_LEASE_S = env.int("W360_WEBHOOK_LEASE_S", 90)
WEBHOOK_MAX_INFLIGHT_PER_CONNECTION = env.int("W360_WEBHOOK_MAX_INFLIGHT", 4)
# The bulkhead per organization. Without it one organization's 50 slow
# connections could hold 200 delivery workers - every heavy worker there is.
# Keep it well below the heavy worker pool's total concurrency.
WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION = env.int("W360_WEBHOOK_MAX_INFLIGHT_ORG", 8)
# How long a delivery refused a slot by either bulkhead is parked. A fallback
# only: a finishing attempt pulls the next waiting delivery forward at once
# (pipeline.py), so a backlog drains at full speed without re-polling the
# shared timer every few seconds.
WEBHOOK_THROTTLE_HOLD_S = env.float("W360_WEBHOOK_THROTTLE_HOLD_S", 300.0)
WEBHOOK_MAX_OPEN_PER_CONNECTION = env.int("W360_WEBHOOK_MAX_OPEN", 1_000)
WEBHOOK_MAX_SUBSCRIPTIONS_PER_CONNECTION = 50
WEBHOOK_HOLD_RECHECK_S = env.float("W360_WEBHOOK_HOLD_RECHECK_S", 300.0)
WEBHOOK_DISPATCH_GRACE_S = env.float("W360_WEBHOOK_DISPATCH_GRACE_S", 30.0)
WEBHOOK_PING_RATE_LIMIT = (10, 60)

# Automation (Tasks 31-46).
AUTOMATION_MAX_RULES = env.int("W360_AUTOMATION_MAX_RULES", 100)
AUTOMATION_MAX_ACTIONS = 5
AUTOMATION_MIN_INTERVAL_MINUTES = env.int("W360_AUTOMATION_MIN_INTERVAL_MINUTES", 15)
AUTOMATION_MAX_CHAIN_DEPTH = env.int("W360_AUTOMATION_MAX_CHAIN_DEPTH", 5)
AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR = env.int(
    "W360_AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR", 1_000
)
AUTOMATION_MAX_ATTEMPTS = 3
AUTOMATION_RETRY_BASE_S = 10.0
AUTOMATION_RETRY_CAP_S = 600.0
AUTOMATION_LEASE_S = 120

# The due-work timer (integration/scheduling).
SCHEDULING_CLAIM_BATCH = env.int("W360_SCHEDULING_CLAIM_BATCH", 200)
SCHEDULING_LEASE_S = env.float("W360_SCHEDULING_LEASE_S", 120.0)
SCHEDULING_LEASE_CAP_S = env.float("W360_SCHEDULING_LEASE_CAP_S", 3600.0)
SCHEDULING_STUCK_CLAIMS = 10

# Retention (Tasks 25, 76) - integration/routing/retention.py.
INTEGRATION_RETENTION_SUCCEEDED_DAYS = env.int("W360_RETENTION_SUCCEEDED_DAYS", 7)
INTEGRATION_RETENTION_DEAD_DAYS = env.int("W360_RETENTION_DEAD_DAYS", 30)
INTEGRATION_RETENTION_EXECUTION_DAYS = env.int("W360_RETENTION_EXECUTION_DAYS", 30)
INTEGRATION_RETENTION_EVENT_DAYS = env.int("W360_RETENTION_EVENT_DAYS", 30)

# --- Intelligence platform (Phase 8) ---------------------------------------
#
# Every number here bounds cost, latency or storage that a product, a tenant
# or a failing provider could otherwise grow without limit. docs/phase-8/
# gives the reasoning; they are settings so an operator can move one without
# a release.

# Provider endpoints. The adapters carry their vendors' public https
# endpoints; this map can override one *per adapter* for development and the
# test suite's loopback provider. Production refuses any override
# (production.py), so neither staff nor a product can ever point the gateway
# at an arbitrary URL (Tasks 103, 104).
AI_PROVIDER_ENDPOINTS: dict[str, str] = {}
#: Provider API keys live in the secret manager, surfaced as environment
#: variables named by a *reference* the registry row holds - never in the
#: database, a setting or a log (Task 6).
AI_CREDENTIAL_ENV_PREFIX = env.str("W360_AI_CREDENTIAL_ENV_PREFIX", "W360_AI_CREDENTIAL_")
#: The currency estimated AI infrastructure cost is accounted in. A
#: cost-control unit, never an invoice (Task 9).
AI_ACCOUNTING_CURRENCY = "USD"

# Timeouts (Task 49): connect per attempt, and one overall deadline per
# operation that every retry and fallback shares. ADR-011: 30 s interactive,
# 120 s batch.
AI_CONNECT_TIMEOUT_S = env.float("W360_AI_CONNECT_TIMEOUT_S", 3.0)
AI_DNS_TIMEOUT_S = env.float("W360_AI_DNS_TIMEOUT_S", 2.0)
AI_SYNC_DEADLINE_S = env.float("W360_AI_SYNC_DEADLINE_S", 30.0)
AI_ASYNC_DEADLINE_S = env.float("W360_AI_ASYNC_DEADLINE_S", 120.0)
AI_EMBEDDING_DEADLINE_S = env.float("W360_AI_EMBEDDING_DEADLINE_S", 60.0)
AI_MAX_REQUEST_BYTES = env.int("W360_AI_MAX_REQUEST_BYTES", 2 * 1024 * 1024)
AI_MAX_RESPONSE_BYTES = env.int("W360_AI_MAX_RESPONSE_BYTES", 2 * 1024 * 1024)
AI_EMBEDDING_MAX_RESPONSE_BYTES = env.int(
    "W360_AI_EMBEDDING_MAX_RESPONSE_BYTES", 16 * 1024 * 1024
)
# Retries (Task 50): transient failures only, bounded, equal jitter.
AI_MAX_ATTEMPTS = env.int("W360_AI_MAX_ATTEMPTS", 3)
AI_RETRY_BASE_S = env.float("W360_AI_RETRY_BASE_S", 0.5)
AI_RETRY_CAP_S = env.float("W360_AI_RETRY_CAP_S", 4.0)
# Per-model circuit breaker (Task 51).
AI_BREAKER_THRESHOLD = env.int("W360_AI_BREAKER_THRESHOLD", 5)
AI_BREAKER_BASE_S = env.float("W360_AI_BREAKER_BASE_S", 30.0)
AI_BREAKER_CAP_S = env.float("W360_AI_BREAKER_CAP_S", 300.0)
# Request bounds (Tasks 10, 35, 54).
AI_MAX_INPUT_BYTES = env.int("W360_AI_MAX_INPUT_BYTES", 65_536)
AI_MAX_INPUT_TOKENS = env.int("W360_AI_MAX_INPUT_TOKENS", 100_000)
AI_MAX_OUTPUT_TOKENS = env.int("W360_AI_MAX_OUTPUT_TOKENS", 4_096)
AI_STRUCTURED_OUTPUT_RETRIES = env.int("W360_AI_STRUCTURED_OUTPUT_RETRIES", 1)
# Spend and concurrency (Tasks 10, 11, 58, 59). The platform cap applies to
# every organization whatever its entitlement says - "unlimited" in a plan is
# never unlimited spend. 50,000,000 micros = 50.00 USD a month.
AI_DEFAULT_ORG_MONTHLY_CAP_MICROS = env.int(
    "W360_AI_DEFAULT_ORG_MONTHLY_CAP_MICROS", 50_000_000
)
AI_MAX_CONCURRENT_PER_ORG = env.int("W360_AI_MAX_CONCURRENT_PER_ORG", 8)
#: How long a reservation is held before a crashed caller's reservation is
#: settled at its full reserved amount (fail closed on cost, Task 59).
AI_SYNC_LEASE_S = env.int("W360_AI_SYNC_LEASE_S", 120)
AI_ASYNC_LEASE_S = env.int("W360_AI_ASYNC_LEASE_S", 300)
# Retention (Tasks 46, 84). Results exist only so an idempotent retry and an
# async poll can read them; execution metadata is the audit and usage trail.
AI_RESULT_RETENTION_S = env.int("W360_AI_RESULT_RETENTION_S", 900)
AI_JOB_RESULT_RETENTION_S = env.int("W360_AI_JOB_RESULT_RETENTION_S", 86_400)
AI_EXECUTION_RETENTION_DAYS = env.int("W360_AI_EXECUTION_RETENTION_DAYS", 400)
AI_PRUNE_BATCH = 5_000

# Retrieval-augmented generation (Tasks 27, 28, 35).
RAG_MAX_DOCUMENT_CHARS = env.int("W360_RAG_MAX_DOCUMENT_CHARS", 200_000)
RAG_CHUNK_CHARS = env.int("W360_RAG_CHUNK_CHARS", 1_000)
RAG_CHUNK_OVERLAP_CHARS = env.int("W360_RAG_CHUNK_OVERLAP_CHARS", 100)
RAG_MAX_CHUNKS_PER_DOCUMENT = env.int("W360_RAG_MAX_CHUNKS_PER_DOCUMENT", 256)
RAG_EMBED_BATCH = env.int("W360_RAG_EMBED_BATCH", 64)
RAG_MAX_DOCUMENTS_PER_ORGANIZATION = env.int("W360_RAG_MAX_DOCUMENTS_PER_ORG", 50_000)
RAG_DEFAULT_TOP_K = 5
RAG_MAX_TOP_K = env.int("W360_RAG_MAX_TOP_K", 20)
RAG_MAX_QUERY_CHARS = env.int("W360_RAG_MAX_QUERY_CHARS", 2_000)
RAG_MAX_SOURCES_PER_QUERY = env.int("W360_RAG_MAX_SOURCES_PER_QUERY", 20)
RAG_MAX_CONTEXT_CHARS = env.int("W360_RAG_MAX_CONTEXT_CHARS", 24_000)
RAG_MAX_ACCESS_TAGS = 20
RAG_MAX_INDEX_ATTEMPTS = env.int("W360_RAG_MAX_INDEX_ATTEMPTS", 5)
RAG_RETRY_BASE_S = env.float("W360_RAG_RETRY_BASE_S", 30.0)
RAG_RETRY_CAP_S = env.float("W360_RAG_RETRY_CAP_S", 3_600.0)
RAG_INDEX_LEASE_S = env.int("W360_RAG_INDEX_LEASE_S", 300)
RAG_PURGE_BATCH = 5_000
RAG_TOMBSTONE_RETENTION_DAYS = env.int("W360_RAG_TOMBSTONE_RETENTION_DAYS", 30)

# Analytics (Tasks 65, 69, 75, 83).
ANALYTICS_PROJECTION_BATCH = env.int("W360_ANALYTICS_PROJECTION_BATCH", 500)
ANALYTICS_PROJECTION_GRACE_S = env.float("W360_ANALYTICS_PROJECTION_GRACE_S", 5.0)
ANALYTICS_MAX_RANGE_DAYS = {"hour": 7, "day": 400, "month": 1_100}
ANALYTICS_MAX_ROWS = env.int("W360_ANALYTICS_MAX_ROWS", 5_000)
ANALYTICS_MAX_METRICS_PER_PRODUCT = 200
ANALYTICS_FACT_RETENTION_DAYS = env.int("W360_ANALYTICS_FACT_RETENTION_DAYS", 400)
ANALYTICS_HOURLY_RETENTION_DAYS = env.int("W360_ANALYTICS_HOURLY_RETENTION_DAYS", 90)
ANALYTICS_DAILY_RETENTION_DAYS = env.int("W360_ANALYTICS_DAILY_RETENTION_DAYS", 1_830)

# --- Files ---------------------------------------------------------------

FILE_STORAGE_BACKEND = env.choice(
    "W360_FILE_STORAGE_BACKEND", ("local", "s3"), default="local"
)
FILE_STORAGE_ROOT = Path(env.str("W360_FILE_STORAGE_ROOT", str(ROOT_DIR / ".files")))
# The S3 adapter (platform/files/storage_s3.py, Phase 12). Credentials are never
# settings: boto3's default chain (a task/instance role, or AWS_* variables from
# the secret store). The endpoint is only for an S3-compatible store; blank
# means AWS itself.
S3_BUCKET = env.str("W360_S3_BUCKET", "")
S3_REGION = env.str("W360_S3_REGION", "")
S3_ENDPOINT_URL = env.str("W360_S3_ENDPOINT_URL", "")
S3_KEY_PREFIX = env.str("W360_S3_KEY_PREFIX", "")
S3_SSE = env.choice("W360_S3_SSE", ("AES256", "aws:kms"), default="AES256")
S3_KMS_KEY_ID = env.str("W360_S3_KMS_KEY_ID", "")
FILE_MAX_BYTES = env.int("W360_FILE_MAX_BYTES", 25 * 1024 * 1024)
FILE_URL_TTL_SECONDS = env.int("W360_FILE_URL_TTL_SECONDS", 900)
# Signing key for local download URLs. Distinct from SECRET_KEY so that
# rotating one does not invalidate the other.
FILE_URL_SIGNING_KEY = env.secret("W360_FILE_URL_SIGNING_KEY", "") or SECRET_KEY

# --- MFA (Phase 5, Task 37) ------------------------------------------------
#
# Field-level encryption for TOTP secrets (12-security.md section 6,
# platform/identity/mfa.py). Any string works as input - it is put through
# SHA-256 and base64url-encoded into a valid 32-byte Fernet key below, so
# operators do not need to hand-generate a correctly-shaped key. Distinct
# from SECRET_KEY and FILE_URL_SIGNING_KEY: rotating this one specifically
# invalidates every stored TOTP secret and forces re-enrollment, which
# should never be an accidental side effect of rotating an unrelated key.
_MFA_KEY_MATERIAL = env.secret("W360_MFA_ENCRYPTION_KEY", "") or SECRET_KEY
MFA_ENCRYPTION_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(_MFA_KEY_MATERIAL.encode("utf-8")).digest()
)

# How long a completed step-up keeps the session at AAL2. 12-security.md
# section 2: `dangerous` permissions require aal >= 2 *and* re-auth within
# 15 minutes - without this window a single step-up would last the whole
# session (up to SESSION_ABSOLUTE_TIMEOUT).
STEP_UP_MAX_AGE_SECONDS = env.int("W360_STEP_UP_MAX_AGE_S", 15 * 60)

# --- Notifications -------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = env.str("W360_DEFAULT_FROM_EMAIL", "no-reply@workeasy360.test")
# The SMTP relay (Phase 12, G-03). Before Phase 12 none of these was read, so
# production's SMTP backend could only reach localhost:25, unauthenticated and
# in plaintext. The password is a secret from the secret store. STARTTLS
# (EMAIL_USE_TLS) or implicit TLS (EMAIL_USE_SSL); production requires one.
EMAIL_HOST = env.str("W360_EMAIL_HOST", "localhost")
EMAIL_PORT = env.int("W360_EMAIL_PORT", 25)
EMAIL_HOST_USER = env.str("W360_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env.secret("W360_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env.bool("W360_EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env.bool("W360_EMAIL_USE_SSL", False)
# One timeout for the connection and every socket operation on it: Django's
# SMTP backend has no separate connect timeout (a W360_EMAIL_CONNECT_TIMEOUT_S
# setting existed and was never applied; removed in Phase 12).
EMAIL_READ_TIMEOUT_S = env.float("W360_EMAIL_READ_TIMEOUT_S", 10.0)
NOTIFICATION_MAX_ATTEMPTS = env.int("W360_NOTIFICATION_MAX_ATTEMPTS", 5)
# Phase 8 extension: the key credential-bearing template variables (an
# invitation link) are sealed under while a notification waits to be
# delivered (platform/notifications/sealing.py). Comma-separated; the first
# seals, every one opens, so it can be rotated. Required in production.
NOTIFICATION_SEAL_KEYS = [
    base64.urlsafe_b64encode(hashlib.sha256(m.strip().encode("utf-8")).digest())
    for m in (
        env.secret("W360_NOTIFICATION_SEAL_KEYS", "") or f"{SECRET_KEY}:notification-seal"
    ).split(",")
    if m.strip()
]

# --- Observability -------------------------------------------------------

LOG_LEVEL = env.choice(
    "W360_LOG_LEVEL", ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), default="INFO"
)
LOG_FORMAT = env.choice("W360_LOG_FORMAT", ("json", "console"), default="json")
READINESS_CACHE_SECONDS = env.int("W360_READINESS_CACHE_SECONDS", 5)
# Bearer token for /healthz/metrics (Phase 12). Unset: the endpoint answers 404.
METRICS_TOKEN = env.secret("W360_METRICS_TOKEN", "")
# A metric exceeding its label-combination ceiling raises (a design error,
# caught in tests and development); production drops it instead.
METRICS_STRICT_CARDINALITY = True

# --- i18n ----------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# --- Phase 8 extension: consoles, access controls, payments ----------------

#: How long an invitation link stays valid.
INVITATION_TTL_DAYS = env.int("W360_INVITATION_TTL_DAYS", 7)
#: How long a person's own security trail and an organization's sign-in
#: activity are kept.
ACCOUNT_EVENT_RETENTION_DAYS = env.int("W360_ACCOUNT_EVENT_RETENTION_DAYS", 400)
MEMBER_SIGN_IN_RETENTION_DAYS = env.int("W360_MEMBER_SIGN_IN_RETENTION_DAYS", 400)

CELERY_BEAT_SCHEDULE["administration-prune-activity"] = {
    "task": "platform.administration.prune_activity",
    "schedule": env.float("W360_BEAT_IDENTITY_PRUNE_S", 86400.0),
    "options": {"queue": "heavy", "expires": 3600},
}

# --- Payments (Phase 8 extension) --------------------------------------------
#
# Provider secrets are references to environment variables with these
# prefixes, filled by the secret manager and read per call
# (commercial/payments/credentials.py) - never stored in the database.
PAYMENT_CREDENTIAL_ENV_PREFIX = env.str(
    "W360_PAYMENT_CREDENTIAL_ENV_PREFIX", "W360_PAYMENT_CREDENTIAL_"
)
PAYMENT_WEBHOOK_SECRET_ENV_PREFIX = env.str(
    "W360_PAYMENT_WEBHOOK_SECRET_ENV_PREFIX", "W360_PAYMENT_WEBHOOK_SECRET_"
)
#: Development/test override of an adapter's vendor endpoint. Production pins
#: it empty, so nothing can point payments at another host.
PAYMENT_PROVIDER_ENDPOINTS: dict[str, str] = {}
#: The offline test provider (a "hosted page" that is the platform's own).
#: Production pins it off.
PAYMENTS_FAKE_PROVIDER_ENABLED = env.bool("W360_PAYMENTS_FAKE_PROVIDER", DEBUG)
PAYMENT_ATTEMPT_TTL_MINUTES = env.int("W360_PAYMENT_ATTEMPT_TTL_MINUTES", 60)
#: Days from issue to due: automatic collection is due at once; an invoice
#: account (pay by transfer) gets terms.
PAYMENT_TERMS_DAYS = env.int("W360_PAYMENT_TERMS_DAYS", 0)
PAYMENT_INVOICE_TERMS_DAYS = env.int("W360_PAYMENT_INVOICE_TERMS_DAYS", 14)
#: Dunning thresholds, in days after the due date.
PAYMENT_GRACE_AFTER_DAYS = env.int("W360_PAYMENT_GRACE_AFTER_DAYS", 7)
PAYMENT_SUSPEND_AFTER_DAYS = env.int("W360_PAYMENT_SUSPEND_AFTER_DAYS", 21)
PAYMENT_CONNECT_TIMEOUT_S = env.float("W360_PAYMENT_CONNECT_TIMEOUT_S", 3.0)
PAYMENT_TOTAL_TIMEOUT_S = env.float("W360_PAYMENT_TOTAL_TIMEOUT_S", 20.0)
PAYMENT_MAX_REQUEST_BYTES = env.int("W360_PAYMENT_MAX_REQUEST_BYTES", 64 * 1024)
PAYMENT_MAX_RESPONSE_BYTES = env.int("W360_PAYMENT_MAX_RESPONSE_BYTES", 256 * 1024)
PAYMENT_WEBHOOK_MAX_BYTES = env.int("W360_PAYMENT_WEBHOOK_MAX_BYTES", 256 * 1024)

CELERY_BEAT_SCHEDULE["payments-process-due"] = {
    "task": "commercial.payments.process_due",
    "schedule": env.float("W360_BEAT_PAYMENTS_S", 300.0),
    "options": {"queue": "default", "expires": 280},
}


# --- Phase 9: self-service accounts, public catalog, orders -------------------

#: How long a signup's verification link stays valid, and how long a password
#: reset link does. Short on purpose: each is a credential for its purpose.
ACCOUNT_VERIFY_TTL_HOURS = env.int("W360_ACCOUNT_VERIFY_TTL_HOURS", 24)
PASSWORD_RESET_TTL_MINUTES = env.int("W360_PASSWORD_RESET_TTL_MINUTES", 60)
#: Signups that never verified are deleted after this many days, which frees
#: the address (an unverified account holds nothing and cannot sign in).
PENDING_ACCOUNT_TTL_DAYS = env.int("W360_PENDING_ACCOUNT_TTL_DAYS", 7)
#: The public catalog's currency when the platform settings name none.
PUBLIC_CATALOG_DEFAULT_CURRENCY = env.str("W360_PUBLIC_CATALOG_CURRENCY", "INR")
#: How long an unpaid order waits for its payment before it expires and its
#: invoice is voided.
ORDER_TTL_HOURS = env.int("W360_ORDER_TTL_HOURS", 24)

CONTROL_PLANE_RATE_LIMITS.update(
    {
        # Signup and its links. Per address (the client IP) and per account
        # (a hash of the normalised email, never the email): the abuse is
        # either one client scripting the form or many clients mail-bombing
        # one inbox. Windows are an hour where the concern is the inbox.
        "signup_ip": (env.int("W360_RL_SIGNUP_IP", 10), 60),
        "signup_account": (env.int("W360_RL_SIGNUP_ACCOUNT", 5), 3600),
        "verify_email_ip": (env.int("W360_RL_VERIFY_EMAIL_IP", 20), 60),
        "verify_resend_ip": (env.int("W360_RL_VERIFY_RESEND_IP", 10), 60),
        "verify_resend_account": (env.int("W360_RL_VERIFY_RESEND_ACCOUNT", 5), 3600),
        "password_reset_ip": (env.int("W360_RL_PASSWORD_RESET_IP", 10), 60),
        "password_reset_account": (env.int("W360_RL_PASSWORD_RESET_ACCOUNT", 5), 3600),
        "password_reset_confirm_ip": (
            env.int("W360_RL_PASSWORD_RESET_CONFIRM_IP", 20),
            60,
        ),
        # The public site: catalog and site configuration.
        "public_catalog_ip": (env.int("W360_RL_PUBLIC_CATALOG_IP", 120), 60),
        # Placing orders: an owner scripting purchases, or an address doing so.
        "order_place_user": (env.int("W360_RL_ORDER_PLACE_USER", 20), 60),
        "order_place_ip": (env.int("W360_RL_ORDER_PLACE_IP", 60), 60),
        # Phase 11: a staff member pressing "check now" on a product. Each
        # press makes one outbound request to a third party's server.
        "product_health_check_user": (env.int("W360_RL_PRODUCT_HEALTH_USER", 10), 60),
        "product_health_check_ip": (env.int("W360_RL_PRODUCT_HEALTH_IP", 30), 60),
    }
)


# --- Phase 10: reseller onboarding and customer-owner activation ---------------

#: How long the link that sets up a new partner administrator stays valid. It is
#: a password-setting credential emailed to a person who may not read it today,
#: so it is days, not the sixty minutes of a forgotten-password link - and it is
#: single use, hashed at rest, and replaced by the next one sent.
PARTNER_ADMIN_INVITE_DAYS = env.int("W360_PARTNER_ADMIN_INVITE_DAYS", 7)
#: How long a customer-owner activation link stays valid (the partner can send a
#: new one). Matches the lifetime a provisioned owner invitation always had.
OWNER_INVITE_DAYS = env.int("W360_OWNER_INVITE_DAYS", 14)

CONTROL_PLANE_RATE_LIMITS.update(
    {
        # The public owner-activation page: previewing and claiming are both
        # unauthenticated and both answer to a bearer link, so they are
        # limited per client address (a guess costs a request each).
        "owner_invitation_public_ip": (
            env.int("W360_RL_OWNER_INVITATION_PUBLIC_IP", 20),
            60,
        ),
        # Staff and partners sending or withdrawing an activation link.
        "owner_invitation_send_user": (
            env.int("W360_RL_OWNER_INVITATION_SEND_USER", 20),
            60,
        ),
    }
)
