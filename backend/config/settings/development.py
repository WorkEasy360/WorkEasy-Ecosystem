"""Local development.

Loosened deliberately and visibly. Every relaxation here is one that
production does not inherit, because production imports base and tightens
rather than importing this module.
"""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import env

ENVIRONMENT = "development"
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]
# The platform shell (localhost:3000) proxies /api to this server, so a
# browser POST arrives with the shell's Origin; without it every sign-in and
# form submission from the local frontend is refused by the origin check.
# `next dev` moves to 3001 when another project already holds 3000.
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

# Plain HTTP locally, so secure-only cookies would never be sent back.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0

LOG_FORMAT = env.choice("W360_LOG_FORMAT", ("json", "console"), default="console")
LOG_LEVEL = env.choice(
    "W360_LOG_LEVEL", ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), default="DEBUG"
)

# Email is printed rather than sent. Nothing leaves the machine.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

env.require_all(context="development configuration")
