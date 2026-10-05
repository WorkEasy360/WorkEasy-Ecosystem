"""Provider API keys: resolved from the secret manager by reference, per call.

The pattern ``contracts/ports/signing.py`` established for the platform's
signing key, applied to AI vendor keys (Task 6):

- the database holds a **reference** (``AIProvider.credential_ref``), never a
  key - not in a column, not in a JSON config, not in an audit entry;
- the reference names an environment variable
  (``W360_AI_CREDENTIAL_<REF>``) that the deployment's secret manager fills;
- it is read **live**, so rotating a key is a secret-manager change with no
  deploy and no restart;
- the value is used for one request and dropped. It is never logged (only the
  variable *name* is, which is what an operator needs), never returned by an
  API, never sent to a product and never placed in a prompt.

Products never see a shared provider credential: they reach AI only through
the gateway, which authenticates *them* with their own Phase 3 credential.
"""

from __future__ import annotations

import os

from django.conf import settings

from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


class CredentialUnavailable(RuntimeError):
    """No key is configured for a reference. Never carries a value."""


def variable_name(reference: str) -> str:
    """``anthropic-primary`` -> ``W360_AI_CREDENTIAL_ANTHROPIC_PRIMARY``."""
    safe = "".join(c if c.isalnum() else "_" for c in reference).upper()
    return f"{settings.AI_CREDENTIAL_ENV_PREFIX}{safe}"


def api_key(reference: str) -> str:
    name = variable_name(reference)
    value = os.environ.get(name, "")
    if not value:
        logger.error("ai.credential.unresolved", variable=name)
        raise CredentialUnavailable(f"No AI provider credential at {name}.")
    return value


def is_configured(reference: str) -> bool:
    """Whether a key is present - for the staff registry view, which shows
    *that* a credential is configured and never what it is."""
    return bool(os.environ.get(variable_name(reference), ""))
