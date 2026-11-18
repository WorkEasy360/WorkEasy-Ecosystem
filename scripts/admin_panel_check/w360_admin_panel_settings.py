"""Settings for the scratch backend behind the Admin Panel browser check (2026-11-09).

Development settings, single-host (the production frontend build proxies ``/api`` here),
tasks run in-process, the offline test payment provider on, emails printed to the console
and KYC required, as in production.
"""

from config.settings.development import *  # noqa: F403

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
PARTNER_KYC_REQUIRED = True
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
W360_PARTNER_SUPPORT_EMAIL = "partners@support.workeasy360.dev"
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3999", "http://localhost:3999"]
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
W360_FRONTEND_URL = "http://127.0.0.1:3999"
