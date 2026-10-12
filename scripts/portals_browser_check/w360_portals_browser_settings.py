"""Settings for the scratch backend behind the multi-portal browser check
(ADR-043): the development settings with the five portals configured as they
are in production - except over plain http, because the check runs a local
copy of the production edge (deploy/edge/Caddyfile) without certificates.
TLS is rehearsed separately, with verification on (edge_rehearsal.py).

Tasks run in-process (no worker), and the offline test payment provider is on,
so a checkout completes inside the browser journey.
"""

from config.settings.development import *  # noqa: F401,F403

DOMAIN = "workeasy360.com"
W360_PORTAL_ORIGINS = {
    name: f"http://{name}.{DOMAIN}" for name in ("admin", "partner", "app", "auth", "api")
}
PORTALS_ENABLED = True
ALLOWED_HOSTS = [o.removeprefix("http://") for o in W360_PORTAL_ORIGINS.values()]
CSRF_TRUSTED_ORIGINS = [W360_PORTAL_ORIGINS[n] for n in ("admin", "partner", "app", "auth")]
W360_FRONTEND_URL = W360_PORTAL_ORIGINS["app"]
OIDC_ISSUER = W360_PORTAL_ORIGINS["auth"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
