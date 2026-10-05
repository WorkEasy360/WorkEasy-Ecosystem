"""Settings for the scratch backend behind the extension browser check: the
development settings, the production frontend's origin trusted for CSRF, and
tasks run in-process (no worker), so a payment's checkout task runs when the
request that starts it commits."""

from config.settings import development as _dev
from config.settings.development import *  # noqa: F401,F403

CSRF_TRUSTED_ORIGINS = list(_dev.CSRF_TRUSTED_ORIGINS) + ["http://127.0.0.1:3999", "http://localhost:3999"]
W360_FRONTEND_URL = "http://127.0.0.1:3999"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
PAYMENTS_FAKE_PROVIDER_ENABLED = True
