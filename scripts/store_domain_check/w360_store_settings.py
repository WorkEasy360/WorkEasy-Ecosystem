"""Settings for the scratch backend behind the Digital Store browser check
(ADR-065): the User Admin check's settings (five portals over plain http, KYC
required, the test payment provider on, tasks in-process, email printed), plus the
Store's own host - ``http://store.workeasy360.com`` - as production will configure
it with ``make_env.py enable-store``.
"""

from w360_uap_settings import *  # noqa: F403

W360_PORTAL_ORIGINS = {**W360_PORTAL_ORIGINS, "store": f"http://store.{DOMAIN}"}  # noqa: F405
ALLOWED_HOSTS = [o.removeprefix("http://") for o in W360_PORTAL_ORIGINS.values()]
CSRF_TRUSTED_ORIGINS = [
    W360_PORTAL_ORIGINS[n] for n in ("admin", "partner", "app", "auth", "store")
]
