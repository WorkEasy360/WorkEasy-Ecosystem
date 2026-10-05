"""App configuration for platform/identity."""

from __future__ import annotations

from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.identity"
    label = "identity"
    verbose_name = "Platform | Identity"

    def ready(self) -> None:
        from django.contrib.auth.signals import user_logged_in

        # Django's own ``update_last_login`` saves ``User.last_login`` on every
        # sign-in, from whichever module called ``login()``. Nothing reads that
        # field: the platform records ``last_login_at`` itself
        # (``account.record_sign_in``), so the receiver was a redundant UPDATE
        # per sign-in and a second writer of ``User`` outside this module.
        user_logged_in.disconnect(dispatch_uid="update_last_login")
