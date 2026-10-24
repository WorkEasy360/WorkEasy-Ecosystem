"""Structured logging: JSON in production, readable in development.

Rules enforced here rather than left to discipline (11-observability.md s4):

- ``event`` is a stable dotted machine-readable name, not a sentence. Free
  text cannot be aggregated or alerted on.
- Context goes in fields, never interpolated into the message.
- Mandatory fields (correlation, organization, environment, release) are
  injected by the processor chain, never by hand, because a field that
  depends on someone remembering it is a field that is absent when needed.
- A redaction processor strips secrets and PII as a *backstop*. It is not a
  licence to log them - it is there because one day someone will.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, Final

import structlog

from workeasy360.contracts.ports.observability import collect_log_context
from workeasy360.platform.observability.correlation import (
    current_correlation_id,
    current_request_id,
)

#: Key names whose values are replaced wholesale. Matched case-insensitively
#: against the whole key and as a substring, because real payloads contain
#: `authorization`, `db_password`, `api_key_secret` and similar.
_SENSITIVE_KEY_PATTERNS: Final = re.compile(
    r"(password|passwd|secret|token|api_?key|authorization|auth|credential"
    r"|private_?key|session_?(id|key)|csrf|cookie|signature|otp|mfa_?code"
    r"|card_?number|cvv|iban|sort_?code|account_?number|ssn|nino"
    # OAuth/OIDC bearer material. Named explicitly because none of the
    # patterns above catches them: `client_assertion` is a signed credential
    # that authenticates a whole product, `code_verifier` is the secret half
    # of PKCE, and `authorization_code` is redeemable for an identity
    # assertion. Nothing logs any of them today - this is the backstop for
    # the day something does, which is the only reason this list exists.
    r"|assertion|verifier|authorization_?code)",
    re.IGNORECASE,
)

#: PII field names. Logged as identifiers instead - join to the data when
#: authorised (11-observability.md section 4 rule 3).
_PII_KEY_PATTERNS: Final = re.compile(
    r"(^email$|_email$|^full_name$|^first_name$|^last_name$|^phone|^address"
    r"|^postal_code$|^date_of_birth$|^dob$)",
    re.IGNORECASE,
)

REDACTED: Final = "[redacted]"
REDACTED_PII: Final = "[pii]"


def _redact(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Strip secrets and PII, recursively."""

    def scrub(key: str, value: Any, depth: int = 0) -> Any:
        if depth > 6:  # guard against deeply nested or cyclic payloads
            return value
        if _SENSITIVE_KEY_PATTERNS.search(key):
            return REDACTED
        if _PII_KEY_PATTERNS.search(key):
            return REDACTED_PII
        if isinstance(value, dict):
            return {k: scrub(str(k), v, depth + 1) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [scrub(key, v, depth + 1) for v in value]
        return value

    return {k: scrub(str(k), v) for k, v in event_dict.items()}


def _add_correlation(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    correlation = current_correlation_id()
    request_id = current_request_id()
    if correlation:
        event_dict.setdefault("correlation_id", str(correlation))
    if request_id:
        event_dict.setdefault("request_id", str(request_id))
    return event_dict


def _add_ambient_context(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach fields from registered context providers.

    Tenancy registers a provider supplying organization_id, user_id and
    membership_id. Observability does not import tenancy - they are siblings
    and F-5 forbids it - so the fields arrive through the contracts-level
    registry instead.
    """
    for key, value in collect_log_context().items():
        event_dict.setdefault(key, value)
    return event_dict


def _add_deployment(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    from django.conf import settings

    event_dict.setdefault("environment", getattr(settings, "ENVIRONMENT", "unknown"))
    event_dict.setdefault("release", getattr(settings, "RELEASE", "unknown"))
    return event_dict


def _rename_message_to_event(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Normalise structlog's ``event`` key.

    structlog puts the positional message in ``event``. Our convention is that
    ``event`` is the stable dotted name, so a caller passing ``event=`` in
    kwargs wins and the positional message moves to ``message``.
    """
    positional = event_dict.get("event")
    explicit = event_dict.pop("event_name", None)
    if explicit:
        event_dict["event"] = explicit
        if positional and positional != explicit:
            event_dict["message"] = positional
    return event_dict


_SHARED_PROCESSORS: Final = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    _rename_message_to_event,
    _add_correlation,
    _add_ambient_context,
    _add_deployment,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    _redact,  # last, so it also covers fields added by the processors above
]


#: The same enrichment and redaction for records that did not come through
#: structlog - Django, Celery, psycopg, and this codebase's own stdlib loggers
#: that pass ``extra=`` (tenancy's RLS-bypass audit lines, ``task.failed``).
#: ``ExtraAdder`` lifts those ``extra`` fields into the event; ``_redact`` stays
#: last so it sees them too (Phase 12, G-14: before this, stdlib records were
#: printed as bare ``%(message)s`` - no JSON, no fields, **no redaction**).
_FOREIGN_PRE_CHAIN: Final = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.ExtraAdder(),
    _add_correlation,
    _add_ambient_context,
    _add_deployment,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    _redact,
]


def configure_logging() -> None:
    """Install the processor chain for structlog *and* stdlib. Idempotent.

    One handler on the root logger, whose ``ProcessorFormatter`` renders every
    record - a structlog event (already enriched and redacted by
    ``_SHARED_PROCESSORS``) or a stdlib record (enriched and redacted by
    ``_FOREIGN_PRE_CHAIN``) - with the same renderer. One stream, one format,
    one redaction policy.
    """
    from django.conf import settings

    log_format = getattr(settings, "LOG_FORMAT", "json")
    log_level = getattr(settings, "LOG_LEVEL", "INFO")

    # The console renderer's default exception formatter is the Rich one when Rich
    # is installed, and it prints every frame's local variables - a password
    # argument, a request body - into the log. A plain traceback is what the JSON
    # format (production) records too: where the error was, never what was in it.
    renderer = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(
            colors=False, exception_formatter=structlog.dev.plain_traceback
        )
    )

    # The console renderer formats exceptions itself (and warns when
    # ``format_exc_info`` already has); JSON needs them pre-rendered.
    def chain(processors: list[Any]) -> list[Any]:
        if log_format == "json":
            return list(processors)
        return [p for p in processors if p is not structlog.processors.format_exc_info]

    structlog.configure(
        processors=[
            *chain(_SHARED_PROCESSORS),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level)
        ),
        # A stdlib logger factory, not PrintLogger: `add_logger_name` reads
        # `.name` off the underlying logger, which PrintLogger does not have.
        # Going through stdlib also means our logs and Django's, Celery's and
        # every library's share one handler, one level policy and one format,
        # rather than two parallel streams that disagree.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=chain(_FOREIGN_PRE_CHAIN),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.getLevelName(log_level))

    # Third-party libraries are capped independently of our level. Running our
    # own code at DEBUG should not drown the output in Celery's generated task
    # signatures or every psycopg statement.
    for noisy in (
        "django.db.backends",
        "django.utils.autoreload",
        "urllib3",
        "amqp",
        "kombu",
        "celery",
        "asyncio",
        "botocore",
        "boto3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Django logs every 4xx at WARNING by default, which makes warning rate
    # useless as a signal: an expected 404 is not a degradation
    # (11-observability.md section 4 rule 6).
    logging.getLogger("django.request").setLevel(logging.ERROR)


def get_logger(name: str) -> Any:
    """Module-level logger. Use the module's ``__name__``."""
    return structlog.get_logger(name)
