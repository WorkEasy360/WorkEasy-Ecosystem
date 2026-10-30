"""App configuration for commercial/partners (Phase 7)."""

from __future__ import annotations

from django.apps import AppConfig


class PartnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.partners"
    label = "partners"
    verbose_name = "Commercial | Partners"

    def ready(self) -> None:
        # Declares the partner permissions and event schemas. A permission or
        # an event that is not declared here cannot be granted or published.
        from workeasy360.commercial.partners import (
            permissions,  # noqa: F401
            promotion_requests,  # noqa: F401 - registers its email template
            tasks,  # noqa: F401
        )

        # authz sits below this module and cannot import it, so it asks
        # *through a registry* how a partner principal's permissions are
        # computed - the same inversion tenancy uses for membership.
        from workeasy360.commercial.partners.resolver import partner_permissions
        from workeasy360.contracts.enums import PrincipalKind
        from workeasy360.contracts.events import partner_events  # noqa: F401
        from workeasy360.platform.authz import services as authz

        authz.register_principal_resolver(PrincipalKind.PARTNER, partner_permissions)

        # ADR-053: a storefront order makes its buyer the partner's customer at
        # fulfilment. payments sits below partners, so it calls back through
        # this one registration rather than importing partners.
        from workeasy360.commercial.partners.storefront import attach_customer
        from workeasy360.commercial.payments.orders import on_storefront_fulfil

        on_storefront_fulfil(attach_customer)

        # Phase 12: custom-domain state for the "custom-domain failures" alert
        # (partner tables are global, D-019, so these are real totals).
        from workeasy360.platform.observability.health import register_gauges

        register_gauges("partner_domains", _domain_gauges)


def _domain_gauges() -> list[tuple[str, dict[str, str], float]]:
    from django.db.models import Count

    from workeasy360.commercial.partners.models import PartnerDomain

    samples: list[tuple[str, dict[str, str], float]] = [
        ("w360_partner_domains", {"status": status}, float(count))
        for status, count in PartnerDomain.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    ]
    failing = PartnerDomain.objects.exclude(failure_code="").count()
    samples.append(("w360_partner_domains_failing", {}, float(failing)))
    return samples
