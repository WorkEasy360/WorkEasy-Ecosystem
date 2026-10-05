from config.settings.development import *  # noqa: F401,F403
from config.settings import development as _dev

CSRF_TRUSTED_ORIGINS = list(_dev.CSRF_TRUSTED_ORIGINS) + ["http://127.0.0.1:3999", "http://localhost:3999"]
