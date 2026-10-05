"""The Super Admin application services (Phase 7, ADR-027).

The top of ``commercial/``: it composes catalog, pricing, subscriptions,
provisioning, partners and commissions with the platform's organizations,
entitlements, audit and launcher into **staff-authorized commands and
read models** - and nothing else. It owns no table and no business rule.

Every function takes the actor's ``TenantContext`` (a ``STAFF`` principal
bound to the platform organization), authorizes through the one decision
function against an explicit ``platform.staff_*`` permission (dangerous ones
demand AAL 2), and reaches every commercial fact through the *same* services a
customer's own purchase uses. Nothing here writes a subscription, an
entitlement, a price or a ledger row directly; the only things it writes are
its own audit entries, and the one lifecycle flag ``organizations`` exposes.

It never reads a product-domain record: there is none in this repository, and
the customer detail assembles only commercial and access facts.
"""
