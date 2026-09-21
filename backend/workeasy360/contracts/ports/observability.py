"""Ambient context providers for logging and tracing.

Solves a genuine circular need between two platform modules that may not
import each other:

- ``platform/observability`` must stamp ``organization_id`` on every log line
  and span, or the mandatory-fields rule in 11-observability.md section 4 is
  unenforceable.
- ``platform/tenancy`` owns that context.
- They are siblings, and same-layer imports inside ``platform/`` are forbidden
  (F-5). The documented replacements - events, or orchestration one layer up -
  do not work for a synchronous, per-log-line lookup.

The resolution is the same runtime-registry pattern the architecture already
uses for ports: the contract lives here in L0, tenancy registers a provider at
app-ready, and observability calls it without knowing who supplied it. No
import edge exists in either direction.

Providers must be cheap and must never raise: they run on every log line, and
a logging failure that breaks the request it was describing is worse than a
missing field.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: A callable returning fields to merge into every log event.
LogContextProvider = Callable[[], dict[str, Any]]

_providers: list[tuple[str, LogContextProvider]] = []


def register_log_context_provider(name: str, provider: LogContextProvider) -> None:
    """Register a source of ambient log fields.

    Idempotent by name, so a re-import during test setup or an autoreload does
    not stack duplicate providers.
    """
    global _providers
    _providers = [(n, p) for n, p in _providers if n != name]
    _providers.append((name, provider))


def collect_log_context() -> dict[str, Any]:
    """Merge every registered provider's fields.

    Failures are swallowed deliberately. A provider that raises - because a
    contextvar is in an odd state during shutdown, say - must degrade to a
    missing field rather than taking down the logging call, and therefore the
    operation being logged.
    """
    merged: dict[str, Any] = {}
    for _name, provider in _providers:
        try:
            merged.update(provider())
        except Exception:  # noqa: S112
            continue
    return merged


def registered_providers() -> list[str]:
    return [name for name, _ in _providers]


def reset_providers() -> None:
    """Test hook only."""
    global _providers
    _providers = []
