"""App configuration for platform/tenancy."""

from __future__ import annotations

from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.tenancy"
    label = "tenancy"
    verbose_name = "Platform | Tenancy"

    def ready(self) -> None:
        # Registers W360.E001-E009. Imported here rather than at module scope
        # so the app registry is populated before the checks run.
        from workeasy360.platform.tenancy import checks  # noqa: F401

        self._register_log_context()
        self._register_health_probes()

    @staticmethod
    def _register_log_context() -> None:
        """Supply tenant fields to every log line and span.

        observability cannot import tenancy - they are P0 siblings and F-5
        forbids it - so the fields are pushed through the contracts-level
        registry instead of pulled by an import.
        """
        from workeasy360.contracts.ports.observability import (
            register_log_context_provider,
        )
        from workeasy360.platform.tenancy.context import current_tenant_or_none

        def tenant_fields() -> dict[str, object]:
            ctx = current_tenant_or_none()
            return ctx.for_logging() if ctx is not None else {}

        register_log_context_provider("tenancy", tenant_fields)

    @staticmethod
    def _register_health_probes() -> None:
        """Contribute the RLS role assertion to the startup probe.

        A container whose database role can bypass RLS has no tenant
        isolation at all and must fail to start rather than serve traffic
        (R-02). Tenancy owns what "healthy" means here; observability only
        runs and reports it.
        """
        from workeasy360.platform.observability.health import register_probe

        def rls_role_probe() -> str:
            from workeasy360.platform.tenancy.db import (
                assert_connection_role_cannot_bypass_rls,
            )

            problems = assert_connection_role_cannot_bypass_rls()
            if problems:
                raise RuntimeError("; ".join(problems))
            return "role cannot bypass RLS"

        register_probe("startup", "rls_role", rls_role_probe)
