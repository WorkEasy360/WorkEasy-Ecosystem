"""Settings for the scratch Workeasy360 behind the universal-sign-in browser
check (ADR-044): the development settings in single-host mode.

Four applications run on loopback with separate cookie jars (cookies are
scoped by host name, not port):

    Workeasy360   http://127.0.0.1:3021 (Next) -> http://127.0.0.1:8021 (Django)
    Easy CRM      http://localhost:4022
    EasyBook      http://127.0.0.2:3023
    Future        http://127.0.0.3:5024   (scripts/.../future_product.py)
    Easy Project  http://127.0.0.4:8024   (one jar: SPA + API)
    Easy Help Desk http://127.0.0.5:3025 (Next) and http://127.0.0.5:4025 (API)

The issuer is the Django origin: the products' backends read discovery and
the key set from it, and the authorization endpoint is reached there by the
browser - with the Workeasy360 session cookie, which 127.0.0.1 shares across
ports. Tasks run in-process; the offline test payment provider is on.

The entitlement re-check window is 30 s instead of 900 s. It is the
platform's to set - every answer carries ``revalidate_after`` - so this both
compresses the brief's "existing session loses access within the window"
into a run and proves all four products honour the platform's cadence, with
no hook inside any product. (30 s is also every product's floor.)
"""

from config.settings.development import *  # noqa: F403

W360_FRONTEND_URL = "http://127.0.0.1:3021"
OIDC_ISSUER = "http://127.0.0.1:8021"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3021"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
OIDC_ENTITLEMENT_REVALIDATE_AFTER_S = 30
