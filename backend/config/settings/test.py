"""Automated tests.

Two properties matter more here than speed, and both are deliberate:

1. **Real PostgreSQL with RLS enabled, connected as a non-owner role.** A
   suite running as the table owner silently bypasses every policy, so the
   isolation tests would pass while production was broken
   (13-deployment-evolution.md section 2.2). The role swap happens in
   conftest.py after migrations have run as the owner.

2. **Real PgBouncer in transaction mode by default.** R-01 is the highest-risk
   operational coupling in the design; parity is the point.
"""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import DATABASES, env

ENVIRONMENT = "test"
DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# The OIDC issuer for the suite, matching the test client's host so that the
# `iss` claim, the discovery document and the client-assertion audience all
# agree without a fixture having to patch them. Set here rather than in an
# autouse fixture because it is a property of the environment, and a fixture
# would silently rewrite a setting for every unrelated test in the suite.
OIDC_ISSUER = "http://testserver"
# Loopback callbacks: the fixtures register http://localhost:9100, which is
# what a product's local development deployment looks like. Production refuses
# this and test_production_refuses_insecure_configuration proves it.
OIDC_ALLOW_INSECURE_REDIRECT_URIS = True

# Cookies are not sent over the test client's plain HTTP.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

LOG_LEVEL = env.choice(
    "W360_LOG_LEVEL",
    ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    default="WARNING",
)
LOG_FORMAT = "console"

# Deliverability is asserted through the delivery records, not by reaching a
# provider. locmem keeps the assertions honest and the suite hermetic.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Tasks run through the real queue machinery under test; individual tests opt
# into eager execution where the queue is not what is being tested.
CELERY_TASK_ALWAYS_EAGER = env.bool("W360_CELERY_EAGER", True)
CELERY_TASK_EAGER_PROPAGATES = True

# Locmem cache: a shared Redis would make tests order-dependent on each other
# and on whatever else is running on the machine. Redis-specific behaviour has
# its own integration tests.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "w360-test",
        "KEY_PREFIX": "w360",
    }
}

# Fast hashing. Argon2 is tuned to >=250ms on production hardware, which would
# add minutes to a suite that creates thousands of users. Argon2 itself is
# covered by a dedicated test that asserts the production hasher is configured.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Migrations run as the owner; conftest.py then switches the connection to the
# non-owner app role so RLS is genuinely enforced for the assertions.
DB_TEST_MIGRATOR_USER = env.str("W360_DB_MIGRATOR_USER", "w360_migrator")
DB_TEST_MIGRATOR_PASSWORD = env.secret("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw")

# The test database is created and migrated over a direct connection: CREATE
# DATABASE cannot run inside the transaction a pooler would wrap it in.
DATABASES["default"]["USER"] = DB_TEST_MIGRATOR_USER
DATABASES["default"]["PASSWORD"] = DB_TEST_MIGRATOR_PASSWORD
DATABASES["default"]["HOST"] = env.str("W360_DB_DIRECT_HOST", "127.0.0.1")
DATABASES["default"]["PORT"] = env.int("W360_DB_DIRECT_PORT", 5442)

# Coordinates conftest.py swaps to once the schema exists.
W360_TEST_APP_USER = env.str("W360_DB_APP_USER", "w360_app")
W360_TEST_APP_PASSWORD = env.secret("W360_DB_APP_PASSWORD", "w360_app_pw")
W360_TEST_PGBOUNCER_HOST = env.str("W360_DB_HOST", "127.0.0.1")
W360_TEST_PGBOUNCER_PORT = env.int("W360_DB_PORT", 6442)

# Whether assertions run through PgBouncer (parity) or direct (isolating a
# failure to the database rather than the pooler).
W360_TEST_THROUGH_PGBOUNCER = env.bool("W360_TEST_THROUGH_PGBOUNCER", True)

env.require_all(context="test configuration")
