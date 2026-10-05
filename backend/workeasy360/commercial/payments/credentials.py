"""Payment provider secrets: read from the secret manager by reference, per call.

The pattern the AI gateway uses for vendor keys (``intelligence/ai/credentials``),
for the same reasons:

- ``PaymentProvider`` holds **references** (``credential_ref``,
  ``webhook_secret_ref``), never a secret - not in a column, a JSON config, an
  audit entry or an API response;
- a reference names an environment variable the deployment's secret manager
  fills: ``W360_PAYMENT_CREDENTIAL_<REF>`` (the API key; for Razorpay the
  ``key_id:key_secret`` pair) and ``W360_PAYMENT_WEBHOOK_SECRET_<REF>``;
- it is read **live**, so rotation is a secret-manager change with no deploy;
- the value is used for one call and dropped. Only the variable *name* is ever
  logged, which is what an operator needs to fix a missing one.

The development test provider may fall back to a key derived from
``SECRET_KEY`` - and only while ``PAYMENTS_FAKE_PROVIDER_ENABLED``, which
production pins off.
"""

from __future__ import annotations

import hashlib
import os

from django.conf import settings

from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


class CredentialUnavailable(RuntimeError):
    """No secret is configured for a reference. Never carries a value."""


def _variable(prefix: str, reference: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in reference).upper()
    return f"{prefix}{safe}"


def api_key_variable(reference: str) -> str:
    return _variable(settings.PAYMENT_CREDENTIAL_ENV_PREFIX, reference)


def webhook_secret_variable(reference: str) -> str:
    return _variable(settings.PAYMENT_WEBHOOK_SECRET_ENV_PREFIX, reference)


def _read(name: str, *, adapter: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    if adapter == "fake" and settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
        return hashlib.sha256(f"{settings.SECRET_KEY}:{name}".encode()).hexdigest()
    logger.error("payments.credential.unresolved", variable=name)
    raise CredentialUnavailable(f"No payment credential at {name}.")


def api_key(reference: str, *, adapter: str) -> str:
    return _read(api_key_variable(reference), adapter=adapter)


def webhook_secret(reference: str, *, adapter: str) -> str:
    return _read(webhook_secret_variable(reference), adapter=adapter)


def is_configured(reference: str, *, adapter: str, secret: bool = False) -> bool:
    """Whether a value is present - what the staff page shows, never the value."""
    name = webhook_secret_variable(reference) if secret else api_key_variable(reference)
    if os.environ.get(name, ""):
        return True
    return adapter == "fake" and bool(settings.PAYMENTS_FAKE_PROVIDER_ENABLED)
