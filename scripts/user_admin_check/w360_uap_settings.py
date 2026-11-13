"""Settings for the scratch backend behind the User Admin Panel browser check
(ADR-063).

The development settings with the five portals configured as production
configures them (each portal on its own host, host-only sessions), over plain http
behind ``scripts/guest_login_check/edge.js``. KYC is REQUIRED, as in production:
the seed takes its partners through the real application, review, approval and
activation. Malware scanning stays off (no clamd here), as in development. The
test payment provider takes the online top-up; tasks run in-process; email is
printed.
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
PARTNER_KYC_REQUIRED = True
KYC_MALWARE_SCAN_REQUIRED = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
