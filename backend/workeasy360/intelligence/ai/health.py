"""The AI providers' state for ``/health/deep`` - reported, never gating.

AI is a Tier 3 dependency (Task 53, ADR-011): a provider outage degrades AI
features and nothing else. This probe is registered in the ``deep`` group
only, which dashboards read and orchestration never does; readiness and
startup do not know it exists (``test_ai_health_never_gates_readiness``).
It makes no provider call - it reports what the breaker already knows.
"""

from __future__ import annotations

from django.utils import timezone

from workeasy360.intelligence.ai import credentials, registry
from workeasy360.intelligence.ai.domain import RegistryStatus


def provider_health() -> str:
    now = timezone.now()
    active = [
        m
        for m in registry.all_models()
        if m.status == RegistryStatus.ACTIVE.value and m.provider_active
    ]
    open_circuits = [m.key for m in active if m.circuit_open(now)]
    unconfigured = sorted(
        {
            m.provider_key
            for m in active
            if not credentials.is_configured(m.credential_ref)
        }
    )
    usable = [
        m
        for m in active
        if not m.circuit_open(now) and credentials.is_configured(m.credential_ref)
    ]
    summary = (
        f"{len(usable)} of {len(active)} active models usable"
        + (f"; circuit open: {', '.join(open_circuits)}" if open_circuits else "")
        + (f"; no credential: {', '.join(unconfigured)}" if unconfigured else "")
    )
    if active and not usable:
        raise RuntimeError(summary)
    return summary
