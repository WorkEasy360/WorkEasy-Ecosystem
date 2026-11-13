"""App configuration for commercial/controlplane (Phase 7).

The control plane owns no tables. It is an installed app only so Django finds
its management command; its services are ordinary modules.
"""

from __future__ import annotations

from django.apps import AppConfig


class ControlPlaneConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.commercial.controlplane"
    label = "controlplane"
    verbose_name = "Commercial | Control plane"

    def ready(self) -> None:
        # ADR-063: the reseller's Platform Wallet. A verified top-up payment
        # credits the wallet, and the subscription clock asks the wallet to pay
        # a partner-paid renewal. Both modules sit below the control plane and
        # import nothing above them, so the control plane registers here.
        from workeasy360.commercial.controlplane import partner_wallet
        from workeasy360.commercial.payments import partner_fees
        from workeasy360.commercial.subscriptions import system

        partner_fees.register(
            partner_fees.PARTNER_WALLET_TOP_UP, partner_wallet.top_up_paid
        )
        system.on_partner_wallet_renewal(partner_wallet.collect_renewal)
