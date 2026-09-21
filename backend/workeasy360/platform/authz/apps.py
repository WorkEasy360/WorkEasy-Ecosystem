"""App configuration for platform/authz."""

from __future__ import annotations

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
