"""Settings for the scratch backend behind the Login as Guest browser check (ADR-062).

The development settings with the five portals configured as production configures
them - each portal on its own host with its own host-only session (ADR-059) - over
plain http, because the check runs a local Host-preserving proxy instead of the TLS
edge. Tasks run in-process; email is printed; KYC is not required, so the seed can
activate partners through the real endpoints.
"""

from config.settings.development import *  # noqa: F403

DOMAIN = "workeasy360.com"
W360_PORTAL_ORIGINS = {
    name: f"http://{name}.{DOMAIN}" for name in ("admin", "partner", "app", "auth", "api")
}
PORTALS_ENABLED = True
ALLOWED_HOSTS = [o.removeprefix("http://") for o in W360_PORTAL_ORIGINS.values()]
CSRF_TRUSTED_ORIGINS = [
    W360_PORTAL_ORIGINS[n] for n in ("admin", "partner", "app", "auth")
]
W360_FRONTEND_URL = W360_PORTAL_ORIGINS["app"]
OIDC_ISSUER = W360_PORTAL_ORIGINS["auth"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PARTNER_KYC_REQUIRED = False
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
