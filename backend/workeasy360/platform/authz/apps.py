"""App configuration for platform/authz."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig


class AuthzConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.authz"
    label = "authz"
    verbose_name = "Platform | Authz"

    def ready(self) -> None:
        # Collects PermissionDeclarations into the registry. A permission not
        # declared in code cannot be granted, so this import is what makes the
        # privilege surface real.
        from workeasy360.platform.authz import permissions  # noqa: F401
        from workeasy360.platform.authz.registry import permission_registry
        from workeasy360.platform.observability.health import register_probe

        def permission_registry_probe() -> str:
            count = len(permission_registry.all_keys())
            if count == 0:
                raise RuntimeError("permission registry is empty")
            problems = permission_registry.validate_all()
            if problems:
                raise RuntimeError("; ".join(problems[:3]))
            return f"{count} permissions"

        register_probe("registries", "permissions", permission_registry_probe)
        self._register_system_role_reseed()

    def _register_system_role_reseed(self) -> None:
        """Phase 5. See ``authz.services.ensure_system_role_catalog``'s own
        docstring for why this fires on every ``migrate`` and every
        ``flush`` (the latter is what makes a ``transaction=True`` test's
        teardown, which genuinely truncates ``authz_role``, self-healing
        rather than a trap for the next test in the same session)."""
        from django.db.models.signals import post_migrate

        def _reseed(sender: Any, **kwargs: Any) -> None:
            from workeasy360.platform.authz.services import ensure_system_role_catalog

            ensure_system_role_catalog()

        post_migrate.connect(
            _reseed, sender=self, dispatch_uid="authz_reseed_system_roles"
        )
