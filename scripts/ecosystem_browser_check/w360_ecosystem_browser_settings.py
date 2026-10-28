"""Settings for the scratch backend behind the ecosystem browser check (ADR-053).

The development settings, single-host (the production frontend build proxies
``/api`` to this backend), with tasks run in-process (no worker), the offline
test payment provider on (so a checkout completes inside the journey), emails
printed to the console (the journey reads the verification and activation
links from the backend's log) and a partner-support address configured.
"""

from config.settings.development import *  # noqa: F403

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
W360_PARTNER_SUPPORT_EMAIL = "partners@support.workeasy360.dev"
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3999", "http://localhost:3999"]
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
# Single-host: every emailed link and the test checkout page point at the
# production build this check drives, never the developer's own dev server.
W360_FRONTEND_URL = "http://127.0.0.1:3999"
