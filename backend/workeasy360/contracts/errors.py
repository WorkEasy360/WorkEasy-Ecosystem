"""Cross-module exception types.

These live in contracts/ because a caller in one module must be able to catch
an error raised by another without importing it. Each carries a stable,
machine-readable ``code`` so that HTTP adapters and UIs can branch on the code
rather than on the exception class or its message.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


class Workeasy360Error(Exception):
    """Base for every error that crosses a module boundary."""

    code = "error"
    http_status = 500

    def __init__(self, message: str = "", **context: Any) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        """Safe-for-response representation.

        ``context`` holds identifiers and reason codes only. Never put secrets,
        credentials or PII here: 12-security.md §5 rule 3 forbids them in logs,
        and this payload reaches both logs and API responses.
        """
        return {"code": self.code, "detail": self.message, **self.context}


# --- Tenancy -------------------------------------------------------------


class TenancyError(Workeasy360Error):
    code = "tenancy_error"
    http_status = 403


class MissingTenantContext(TenancyError):
    """A tenant-scoped operation ran with no tenant context.

    Raised rather than silently returning an empty queryset. Silent emptiness
    looks like "no data" and ships; an exception is loud and is caught in
    development (ADR-009, "Why selected" §4).
    """

    code = "missing_tenant_context"
    http_status = 500


class TenantContextMismatch(TenancyError):
    """An explicit organization_id disagrees with the ambient context.

    Never resolved by overwriting. One of the two is a bug, and guessing which
    is how cross-tenant writes happen.
    """

    code = "tenant_context_mismatch"


class CrossTenantAccess(TenancyError):
    """An object from another tenant was reached. Always a security event."""

    code = "cross_tenant_access"
    http_status = 404


class OrganizationNotWritable(TenancyError):
    """The organization's status forbids writes (R-10).

    402 rather than 403: the remedy is commercial (pay, reactivate), not a
    permission change. 03-multi-tenancy.md §8.
    """

    code = "organization_not_writable"
    http_status = 402

    def __init__(
        self,
        message: str = "",
        *,
        organization_id: UUID | None = None,
        status: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message or "This organization is read-only and cannot accept writes.",
            organization_id=str(organization_id) if organization_id else None,
            organization_status=status,
            **context,
        )


# --- Authorization -------------------------------------------------------


class AuthorizationError(Workeasy360Error):
    code = "authorization_error"
    http_status = 403


class PermissionDenied(AuthorizationError):
    code = "permission_denied"

    def __init__(
        self, message: str = "", *, permission: str | None = None, **context: Any
    ) -> None:
        super().__init__(
            message or f"Insufficient permission: {permission}",
            permission=permission,
            **context,
        )


class UnknownPermission(AuthorizationError):
    """A permission key that is not in the code-declared registry.

    Fails closed: a permission that does not exist cannot be granted, so
    asking about one is a programming error, not a denial to be logged and
    ignored (12-security.md §4).
    """

    code = "unknown_permission"
    http_status = 500


class MembershipRequired(AuthorizationError):
    code = "membership_required"


class StepUpRequired(AuthorizationError):
    """A `dangerous` permission was checked without aal >= 2."""

    code = "step_up_required"
    http_status = 401


# --- Entitlement ---------------------------------------------------------


class EntitlementError(Workeasy360Error):
    code = "entitlement_error"
    http_status = 402


class EntitlementDenied(EntitlementError):
    """The organization may not use this product or feature.

    ``reason_code`` distinguishes "never bought", "lapsed" and "no seat" so the
    UI can render the right call to action (06-entitlements.md §4.3).
    """

    code = "entitlement_denied"

    def __init__(
        self,
        message: str = "",
        *,
        product: str | None = None,
        feature: str | None = None,
        reason_code: str = "not_entitled",
        upgrade_hint: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message or f"Not entitled: {product or feature}",
            product=product,
            feature=feature,
            reason_code=reason_code,
            upgrade_hint=upgrade_hint,
            **context,
        )


class SeatLimitReached(EntitlementError):
    code = "seat_limit_reached"


# --- Integration ---------------------------------------------------------


class IntegrationError(Workeasy360Error):
    code = "integration_error"


class EventSchemaError(IntegrationError):
    """A payload does not match its registered schema.

    Raised at publish time, inside the publisher's transaction, so the bad
    event never reaches the outbox (R-13).
    """

    code = "event_schema_error"
    http_status = 500


class UnknownEventType(IntegrationError):
    code = "unknown_event_type"
    http_status = 500


class CausationDepthExceeded(IntegrationError):
    """An event chain exceeded the cycle guard (02-dependency-rules.md §5)."""

    code = "causation_depth_exceeded"
    http_status = 500


# --- Platform services ---------------------------------------------------


class NotificationError(Workeasy360Error):
    code = "notification_error"


class FileError(Workeasy360Error):
    code = "file_error"
    http_status = 400


class FileNotServable(FileError):
    """Requested before scanning completed. 12-security.md §10."""

    code = "file_not_servable"
    http_status = 409


class FileTooLarge(FileError):
    code = "file_too_large"
    http_status = 413


class UnsupportedFileType(FileError):
    code = "unsupported_file_type"
    http_status = 415


class AuditWriteFailed(Workeasy360Error):
    """The one place a logging failure is allowed to fail the request.

    An unaudited privileged action is worse than a failed one
    (11-observability.md §9).
    """

    code = "audit_write_failed"
    http_status = 500


class ConfigurationError(Workeasy360Error):
    """Invalid or missing configuration. Raised at startup, never per-request."""

    code = "configuration_error"
