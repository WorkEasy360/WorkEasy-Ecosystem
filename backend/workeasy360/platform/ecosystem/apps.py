"""App configuration for platform/ecosystem."""

from __future__ import annotations

from django.apps import AppConfig


class EcosystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.platform.ecosystem"
    label = "ecosystem"
    verbose_name = "Platform | Ecosystem"

    def ready(self) -> None:
        from workeasy360.contracts.ports.signing import register_signing_key_provider
        from workeasy360.platform.ecosystem import (  # noqa: F401
            keys,
            permissions,
            tasks,
        )
        from workeasy360.platform.observability import health

        # Supplies the signing-key provider. Selected here from settings so
        # that swapping the environment-backed provider for a KMS client is a
        # change to this one line and to nothing that signs or verifies
        # (contracts/ports/signing.py).
        register_signing_key_provider(keys.EnvSigningKeyProvider())

        # A "deep" probe, never a readiness one.
        #
        # This is the rule product outages depend on. Readiness decides
        # whether an instance receives traffic; if the state of the ecosystem
        # registry could fail it, then a product's problem - or an operator
        # forgetting to promote a key - would pull the whole platform out of
        # the load balancer. Product health is Tier 3 by definition, because
        # the platform does not need a product to function
        # (16-ecosystem-product-boundary.md section 10, rule 1). Asserted by
        # test_product_health_never_affects_platform_readiness.
        health.register_probe("deep", "ecosystem", _probe_ecosystem)


def _probe_ecosystem() -> str:
    """Report the signing-key posture for dashboards. Never fails readiness.

    Deliberately returns a description instead of raising when there is no
    active key. That state is a genuine problem - token issuance is down -
    but it is a problem an operator must *see*, not one that should remove
    every healthy instance from the load balancer while logins and billing
    continue to work perfectly.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    published = SigningKey.objects.exclude(status=SigningKeyStatus.RETIRED.value).count()
    active = SigningKey.objects.filter(status=SigningKeyStatus.ACTIVE.value).count()
    if active == 0:
        return f"{published} key(s) published, NO ACTIVE SIGNING KEY"
    return f"{published} key(s) published, {active} active"
