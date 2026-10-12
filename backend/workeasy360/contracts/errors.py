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


class AuthenticationRequired(AuthorizationError):
    """No valid session. 401, and the same shape wherever it is raised."""

    code = "login_required"
    http_status = 401


class MembershipRequired(AuthorizationError):
    code = "membership_required"


class StepUpRequired(AuthorizationError):
    """A `dangerous` permission was checked without aal >= 2."""

    code = "step_up_required"
    http_status = 401


class ControlPlaneInvariantViolation(AuthorizationError):
    """A control-plane rule that holds regardless of who asks.

    409: the request was well formed and the caller may have been permitted,
    but carrying it out would break an invariant of the control plane itself
    (granting a non-control-plane key, removing the last security
    administrator).
    """

    code = "control_plane_invariant"
    http_status = 409


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


class IdempotencyConflict(IntegrationError):
    """The same idempotency key was replayed with a different payload.

    A duplicate delivery replays the *same* command and must return the
    original result. A different payload under the same key is either a
    client bug or a deliberate attempt to have one authorisation perform two
    different operations, so it is refused rather than served.
    """

    code = "idempotency_conflict"
    http_status = 409


class CausationDepthExceeded(IntegrationError):
    """An event chain exceeded the cycle guard (02-dependency-rules.md §5)."""

    code = "causation_depth_exceeded"
    http_status = 500


# --- Integration platform (Phase 6): connections, webhooks, automation ----
#
# Customer-facing control-plane errors for the org-configured integration
# surface. Three codes, not one per resource: the remedy is the same shape
# whichever resource it is ("fix the field", "reload and retry", "it is not
# there"), and ``resource``/``field`` in the context say which.


class IntegrationValidationError(IntegrationError):
    """Client-supplied integration configuration failed validation.

    Raised **before the first write** of the service that raises it - a 4xx
    does not roll back the request transaction (D-016), so anything written
    ahead of this would commit. ``field`` names the offending input, which
    is safe: it is data the caller just sent.
    """

    code = "integration_validation_error"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(
            message or "Invalid integration configuration.", field=field, **context
        )


class IntegrationNotFound(IntegrationError):
    """No such connection, subscription, delivery or rule - or not in this
    organization. The two are indistinguishable by design: a cross-tenant
    guess must not learn that the id exists (Task 59)."""

    code = "integration_not_found"
    http_status = 404

    def __init__(self, message: str = "", *, resource: str = "", **context: Any) -> None:
        super().__init__(message or "Not found.", resource=resource, **context)


class IntegrationConflict(IntegrationError):
    """The request conflicts with durable state: a stale ``version``, an
    illegal lifecycle transition, a replay of a delivery that is not dead, a
    limit that would be exceeded. Reload and retry, or change the request."""

    code = "integration_conflict"
    http_status = 409


class IntegrationRateLimited(IntegrationError):
    """A user-triggerable outbound action (a test ping) hit its limit.

    Shares the ``rate_limited`` code every other limiter answers with, so a
    client needs one branch for "wait and try again".
    """

    code = "rate_limited"
    http_status = 429


# --- Commercial ----------------------------------------------------------


class CommercialError(Workeasy360Error):
    """Base for catalog, pricing, subscription and licensing failures.

    409 rather than 400 by default: these are almost always conflicts with
    durable state ("that version is published", "that transition is not
    legal") rather than malformed input, and the remedy differs.
    """

    code = "commercial_error"
    http_status = 409


class CommercialRecordNotFound(CommercialError):
    """A commercial record does not exist, or belongs to another tenant.

    404 for both cases, deliberately and identically. Distinguishing "does
    not exist" from "exists but is not yours" is an enumeration oracle: it
    tells an attacker which subscription ids are real.
    """

    code = "commercial_record_not_found"
    http_status = 404


class CatalogValidationError(CommercialError):
    """A catalog record would be internally inconsistent.

    400, because unlike the rest of this family it *is* bad input: a feature
    key that does not belong to its product, a bundle item whose plan belongs
    to a retired product.
    """

    code = "catalog_validation_error"
    http_status = 400


class ImmutableRecord(CommercialError):
    """An attempt to edit a published catalog or pricing record.

    Editing a live plan or price in place retroactively changes what
    customers agreed to buy. Changes create a new version; existing
    subscriptions keep the version they pinned (F5, ADR-006).
    """

    code = "immutable_record"


class InvalidStateTransition(CommercialError):
    """A lifecycle transition that the state machine does not permit.

    Raised rather than silently ignored: an activation that quietly does
    nothing is indistinguishable from one that worked, and the difference is
    whether the customer has access.
    """

    code = "invalid_state_transition"

    def __init__(
        self,
        message: str = "",
        *,
        entity: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message or f"{entity}: {from_status} -> {to_status} is not permitted.",
            entity=entity,
            from_status=from_status,
            to_status=to_status,
            **context,
        )


class PriceNotAvailable(CommercialError):
    """No price matched. Never a fallback currency, region or zero.

    A missing price is a catalog defect that must surface loudly; a silent
    fallback bills someone incorrectly (ADR-006).
    """

    code = "price_not_available"


class UnsupportedCurrencyOperation(CommercialError):
    """A cross-currency operation was attempted with no settlement model.

    Refused rather than converted: a conversion needs a rate, a rate needs a
    date and a source, and silently choosing one produces numbers nobody can
    reconcile later.
    """

    code = "unsupported_currency_operation"
    http_status = 400


class SeatRemediationRequired(CommercialError):
    """A downgrade would leave more seats assigned than the new plan allows.

    Not an outright refusal and not a silent revocation: assignments beyond
    the new limit are marked over-limit with a deadline, and this error
    carries that deadline so the UI can present the choice
    (06-entitlements.md §6).
    """

    code = "seat_remediation_required"


# --- Partner channel (Phase 7) -------------------------------------------


class PartnerError(CommercialError):
    """Base for partner, capability, commission and payout failures."""

    code = "partner_error"


class PartnerRecordNotFound(PartnerError):
    """A partner-scoped record does not exist **or is not the caller's**.

    404 for both, identically: a partner that can tell "no such customer"
    from "another partner's customer" can enumerate the whole book.
    """

    code = "partner_record_not_found"
    http_status = 404


class PartnerValidationError(PartnerError):
    """Bad input to a partner-channel command. 400, unlike its 409 family."""

    code = "partner_validation_error"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message, field=field, **context)


class PartnerNotActive(PartnerError):
    """A new commercial action was attempted for a partner that may not transact.

    Suspended and terminated partners keep their history and lose the ability
    to do anything new (Phase 7 Task 19, 62).
    """

    code = "partner_not_active"
    http_status = 403


class SeparationOfDutiesViolation(PartnerError):
    """The same identity tried to both prepare and approve a payout."""

    code = "separation_of_duties"
    http_status = 403


class DomainOwnershipUnproven(PartnerError):
    """A custom domain was asked to advance before control was proven."""

    code = "domain_ownership_unproven"


# --- Product integration (the remote ecosystem boundary) -----------------
#
# These are the *control-plane* errors of product integration: registering an
# application, changing its lifecycle, managing its credentials. The runtime
# protocol errors an external product sees at the authorization and token
# endpoints are a different family with a different wire shape, mandated by
# RFC 6749 section 5.2, and live in platform/ecosystem/protocol.py. Mixing
# the two would mean either a non-conforming OAuth response or an
# administration API that speaks OAuth error codes at a human.


class ProductIntegrationError(Workeasy360Error):
    """Base for product integration registry failures.

    409 by default, like ``CommercialError`` and for the same reason: these
    are almost always conflicts with durable state ("that code is already
    registered", "a retired integration cannot be activated") rather than
    malformed input.
    """

    code = "product_integration_error"
    http_status = 409


class ProductIntegrationNotFound(ProductIntegrationError):
    """No such integration, or the caller may not see it.

    404 for both, deliberately and identically. Distinguishing them would
    tell an attacker which client ids are real.
    """

    code = "product_integration_not_found"
    http_status = 404


class ManifestRejected(ProductIntegrationError):
    """A submitted manifest failed validation.

    400, because unlike the rest of this family it *is* bad input. ``field``
    is echoed to the caller, which is safe: a manifest is data the caller
    just sent, so naming the offending key discloses nothing they do not
    already have and saves a support round trip.
    """

    code = "manifest_rejected"
    http_status = 400

    def __init__(self, message: str = "", *, field: str = "", **context: Any) -> None:
        super().__init__(message or "The manifest was rejected.", field=field, **context)


class SignInPreferenceInvalid(ProductIntegrationError):
    """A sign-in preference that cannot be stored (ADR-044): an unknown value,
    a product with no integration, or a choice the product does not offer -
    "prefer the product's sign-in" for a product that has none could never
    take effect, so it is refused rather than silently ignored. 400, with the
    offending ``field``: the caller's own input, so naming it discloses
    nothing."""

    code = "sign_in_preference_invalid"
    http_status = 400

    def __init__(self, message: str = "", *, field: str = "", **context: Any) -> None:
        super().__init__(
            message or "That sign-in preference cannot be saved.", field=field, **context
        )


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


# --- Organizations (Phase 5) -----------------------------------------------


class OrganizationValidationError(Workeasy360Error):
    """Client-supplied organization data failed validation.

    Deliberately generic (unlike the commercial catalog's typed variants):
    organization creation takes exactly one meaningful field, and a single
    code with a field-specific message is simpler than a hierarchy for one
    caller (Task 3 - "input should remain minimal").
    """

    code = "organization_validation_error"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid organization data.", field=field, **context)


# --- MFA (Phase 5) -----------------------------------------------------


class MfaError(Workeasy360Error):
    code = "mfa_error"
    http_status = 400


class MfaEnrollmentNotFound(MfaError):
    """The enrollment referenced by a confirm/cancel call does not exist, is
    already confirmed, or belongs to another user."""

    code = "mfa_enrollment_not_found"
    http_status = 404


class MfaCodeInvalid(MfaError):
    """A TOTP or recovery code did not verify.

    Deliberately does not distinguish "wrong code" from "no such factor" in
    its message - the same uniform-denial property login already applies
    (12-security.md), since either is exploitable as an enumeration oracle
    for whether a given account has MFA enrolled.
    """

    code = "mfa_code_invalid"
    http_status = 400


class MfaThrottled(MfaError):
    """Too many consecutive failed MFA codes: this account's step-up is
    locked until ``retry_after`` seconds have passed.

    Shares the ``rate_limited`` code the per-minute buckets already answer
    with, so a client needs one branch for "wait and try again".
    """

    code = "rate_limited"
    http_status = 429

    def __init__(self, message: str = "", *, retry_after: int, **context: Any) -> None:
        super().__init__(message or "Too many attempts. Try again later.", **context)
        self.retry_after = max(1, int(retry_after))


# --- Intelligence platform (Phase 8): AI, retrieval, analytics -------------
#
# The error model a product's AI, retrieval and analytics calls switch on
# (Tasks 107, 108). Each code names a remedy, never an internal: "not
# entitled" is a sales conversation, "quota exceeded" waits for the period or
# an administrator, "provider unavailable" retries later, "invalid skill"
# fixes the caller. No provider message, credential, prompt or stack ever
# reaches ``context`` - only identifiers and bounded reason codes.


class IntelligenceError(Workeasy360Error):
    code = "intelligence_error"
    http_status = 500


class AINotEntitled(IntelligenceError):
    """The organization's plan grants this product no AI, or the product is
    not entitled at all. One answer for both, so an unentitled caller learns
    nothing about which (the same property the entitlement API keeps)."""

    code = "ai_not_entitled"
    http_status = 403


class AIDisabled(IntelligenceError):
    """AI is switched off for this organization - by its administrator, or by
    the platform - or the organization is not accepting new work."""

    code = "ai_disabled"
    http_status = 403


class AIQuotaExceeded(IntelligenceError):
    """A spend limit would be exceeded by this request. ``scope`` says which
    (``organization`` or ``product``); it resets at ``resets_at``."""

    code = "ai_quota_exceeded"
    http_status = 429


class AIConcurrencyLimited(IntelligenceError):
    """The organization already has the maximum number of AI requests in
    flight. Retry shortly."""

    code = "ai_concurrency_limited"
    http_status = 429


class AISkillNotFound(IntelligenceError):
    """No such skill version, or not one this caller may invoke. The two are
    indistinguishable by design: a product guessing another product's
    private skill must not learn that it exists (Task 18)."""

    code = "ai_skill_not_found"
    http_status = 404


class AIInvalidRequest(IntelligenceError):
    """The request is malformed or out of bounds. ``field`` names the input,
    which is safe: it is data the caller just sent. Raised before any write
    or reservation."""

    code = "ai_invalid_request"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid request.", field=field, **context)


class AIContextTooLarge(IntelligenceError):
    """The trusted instructions and the input alone exceed what the selected
    model accepts. Never resolved by truncating instructions (Task 54)."""

    code = "ai_context_too_large"
    http_status = 422


class AIProviderUnavailable(IntelligenceError):
    """No approved model could answer within the deadline - provider down,
    rate limited, circuit open, or not configured. Retry later; nothing else
    on the platform is affected (Task 53)."""

    code = "ai_provider_unavailable"
    http_status = 503
    retry_after = 30


class AIInvalidOutput(IntelligenceError):
    """The model's output failed the skill's output contract after the
    bounded retries. Nothing was persisted as if it were valid (Task 19)."""

    code = "ai_invalid_output"
    http_status = 502


class AIRequestConflict(IntelligenceError):
    """An idempotency key was reused for a different request, or the first
    request under it is still running."""

    code = "ai_request_conflict"
    http_status = 409


class AIRequestNotFound(IntelligenceError):
    """No such AI request for this product and organization."""

    code = "ai_request_not_found"
    http_status = 404


class AIRegistryConflict(IntelligenceError):
    """A skill or metric version already exists with a different definition.
    Registered versions are immutable; register a new version instead."""

    code = "ai_registry_conflict"
    http_status = 409


class IndexNotReady(IntelligenceError):
    """Retrieval was required but none of the requested sources has an
    indexed document yet - indexing is pending or failed."""

    code = "index_not_ready"
    http_status = 409


class KnowledgeConflict(IntelligenceError):
    """A document version was resubmitted with different content, or the
    organization's document limit would be exceeded."""

    code = "knowledge_conflict"
    http_status = 409


class KnowledgeNotFound(IntelligenceError):
    """No such knowledge source or document for this product and
    organization - including one owned by another product (Task 32)."""

    code = "knowledge_not_found"
    http_status = 404


class MetricNotFound(IntelligenceError):
    """No such metric, or not one this caller may query. Indistinguishable by
    design: a metric key in another product's namespace is not an existence
    oracle (Task 108)."""

    code = "metric_not_found"
    http_status = 404


class AnalyticsQueryInvalid(IntelligenceError):
    """The query is malformed or exceeds a bound (range, buckets, groups).
    ``field`` names the input."""

    code = "analytics_query_invalid"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid analytics query.", field=field, **context)


# --- Phase 8 extension: account security, membership, access policy ----------
#
# Codes the organization console, the account pages and the sign-in flow
# branch on. As everywhere above, ``context`` carries identifiers and bounded
# reason codes only - never a password, token, address or session key.


class AccountSecurityError(Workeasy360Error):
    code = "account_security_error"
    http_status = 400


class PasswordPolicyViolation(AccountSecurityError):
    """A new password failed the platform validators or the strictest
    password policy of the organizations the account belongs to. ``problems``
    are the validators' human-readable messages; none repeats the password."""

    code = "password_policy_violation"

    def __init__(self, message: str = "", *, problems: list[str], **context: Any) -> None:
        super().__init__(
            message or "The new password does not meet the password policy.",
            problems=list(problems),
            **context,
        )


class CurrentPasswordIncorrect(AccountSecurityError):
    code = "current_password_incorrect"


class SessionNotFound(AccountSecurityError):
    """No such live session for this account - including another user's."""

    code = "session_not_found"
    http_status = 404


class SignupClosed(AccountSecurityError):
    """Self-service signup is switched off in the platform settings."""

    code = "signup_closed"
    http_status = 403


class SignupValidationError(AccountSecurityError):
    """A signup field is missing or malformed. Never says whether the address
    already has an account - that answer is the same for everyone."""

    code = "signup_invalid"

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid signup.", field=field, **context)


class AccountLinkInvalid(AccountSecurityError):
    """No redeemable verification or reset link for this token. One answer for
    unknown, expired and already used, like ``InvitationInvalid``: the token
    is the only credential, and a finer answer is an oracle for guessing one."""

    code = "link_invalid"
    http_status = 404


class CredentialsInvalid(AccountSecurityError):
    """The password given to finish a verification was not the account's."""

    code = "invalid_credentials"
    http_status = 401


class AccessPolicyDenied(AuthorizationError):
    """An organization's access policy refused this request after membership
    was verified: ``network_not_allowed``, ``mfa_required``,
    ``reauthentication_required`` or ``password_change_required``."""

    code = "access_policy_denied"

    def __init__(self, reason: str, message: str = "", *, http_status: int = 403) -> None:
        super().__init__(message or "Access denied by the organization's policy.")
        self.code = reason
        self.http_status = http_status


class SecurityPolicyValidationError(Workeasy360Error):
    code = "security_policy_invalid"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid security policy.", field=field, **context)


class SecurityPolicyConflict(SecurityPolicyValidationError):
    """Someone else changed the organization's access policy since this edit
    was loaded (the policy carries an optimistic version)."""

    code = "concurrent_modification"
    http_status = 409


class NetworkLockout(Workeasy360Error):
    """The allow-list being saved would exclude the address it is being saved
    from - refused rather than locking the administrator out."""

    code = "network_lockout"
    http_status = 409


class MembershipError(Workeasy360Error):
    code = "membership_error"
    http_status = 400


class MemberNotFound(MembershipError):
    code = "member_not_found"
    http_status = 404


class MembershipValidationError(MembershipError):
    code = "membership_invalid"

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid membership change.", field=field, **context)


class MembershipConflict(MembershipError):
    """The change contradicts the membership's current state (the owner
    cannot be suspended, an active member cannot be invited again...)."""

    code = "membership_conflict"
    http_status = 409


class InvitationInvalid(MembershipError):
    """No redeemable invitation for this token. Deliberately one answer for
    unknown, expired, revoked and already used: the token is the only
    credential, and a finer answer is an oracle for guessing one."""

    code = "invitation_invalid"
    http_status = 404


class InvitationSignInRequired(MembershipError):
    """The invitation is for an existing account: sign in as it, then accept."""

    code = "sign_in_required"
    http_status = 409


class InvitationEmailMismatch(MembershipError):
    """Signed in as a different account from the one invited."""

    code = "invitation_email_mismatch"
    http_status = 403


class CommunicationValidationError(Workeasy360Error):
    code = "template_invalid"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid template.", field=field, **context)


# --- Phase 8 extension: payments -----------------------------------------------
#
# The payment surface's codes. No provider message, credential, card detail or
# signature ever reaches ``context``: identifiers and bounded reasons only.


class PaymentError(CommercialError):
    code = "payment_error"
    http_status = 409


class PaymentRecordNotFound(PaymentError):
    """No such invoice, payment or provider - including another tenant's."""

    code = "payment_record_not_found"
    http_status = 404


class PaymentNotAllowed(PaymentError):
    """The invoice or payment is not in a state that allows this (already
    paid, void, nothing due, a refund above what was captured...)."""

    code = "payment_not_allowed"


class PaymentProviderUnavailable(PaymentError):
    """No enabled provider can take this payment (none configured for the
    currency, or its credential is missing). Retrying later may succeed."""

    code = "payment_provider_unavailable"
    http_status = 503


class PaymentValidationError(PaymentError):
    code = "payment_invalid"
    http_status = 400

    def __init__(
        self, message: str = "", *, field: str | None = None, **context: Any
    ) -> None:
        super().__init__(message or "Invalid payment request.", field=field, **context)


class WebhookRejected(PaymentError):
    """A provider callback failed verification (signature, freshness, size or
    shape). Deliberately says nothing about which check failed."""

    code = "webhook_rejected"
    http_status = 400


# --- Phase 9: orders ------------------------------------------------------------


class OrderError(CommercialError):
    code = "order_error"
    http_status = 409


class OrderNotFound(OrderError):
    """No such order in this organization - including another tenant's."""

    code = "order_not_found"
    http_status = 404


class TrialNotAllowed(CommercialError):
    """A product trial cannot be started or changed now (Phase 11). ``reason``
    is one of a closed set the frontend maps to copy: ``not_offered`` (no trial
    is configured, or it is switched off), ``plan_not_eligible``,
    ``product_unavailable``, ``already_used`` (this organization has trialled
    the product), ``already_owned`` (a paid line holds it), ``not_active``."""

    code = "trial_not_allowed"

    def __init__(self, message: str = "", *, reason: str, **context: Any) -> None:
        super().__init__(
            message or "This trial cannot be started.", reason=reason, **context
        )


class OrderNotAllowed(OrderError):
    """The order cannot be placed or changed now. ``reason`` is one of a closed
    set the frontend maps to copy: ``billing_account_required``,
    ``order_pending`` (another order awaits payment - its id is given),
    ``outstanding_balance``, ``subscription_not_open``, ``already_owned``,
    ``not_for_sale``, ``interval_mismatch``, ``not_pending``."""

    code = "order_not_allowed"

    def __init__(self, message: str = "", *, reason: str, **context: Any) -> None:
        super().__init__(
            message or "This order cannot be placed.", reason=reason, **context
        )
