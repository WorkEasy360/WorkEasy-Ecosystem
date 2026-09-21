# ADR-006 — Product Catalog, Pricing and Bundles

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture / Commercial

---

## Context

We must support individual product purchase, product-specific plans, predefined bundles, an ecosystem bundle, custom enterprise bundles, upgrades and downgrades, trials, seat-based and organization-based licensing, multiple regions and currencies, multiple billing intervals, reseller prices, negotiated enterprise prices, and pricing strategies we have not thought of yet.

Pricing must not be embedded in individual products — a product that knows what it costs cannot be repackaged.

## Decision

**A versioned catalog in `commercial/catalog`, with all price resolution in `commercial/pricing` behind a single function.**

- `Product` (SKU) references product modules by validated **string code**, never a foreign key.
- `Feature` has exactly three kinds: `boolean`, `quota`, `enum`. Feature keys are declared by the owning product and synced into the catalog.
- `Plan` → `PlanVersion` → `PlanFeature`. **Published versions are immutable**, enforced by a database rule as well as application code.
- `Bundle` → `BundleVersion` → `BundleItem`, granting **plan versions**, not products. Bundle prices are explicit, not derived from components.
- `PriceBook` segments by `channel × partner × region × currency × time`.
- Negotiated prices are `ContractPrice` — org-scoped, RLS-protected, **not** rows in the global price book.
- `resolve_price()` is the single entry point, with a deterministic order: contract → partner book → regional book → default book → **raise**.
- Money is always `(integer minor units, CurrencyCode)`.

## Alternatives considered

### A. Pricing attributes on the product/plan record

`plan.monthly_price`, `plan.annual_price`.

- **Pro:** simple; obvious.
- **Con:** cannot express multiple currencies, regions, partner prices or negotiated rates without a column per case. Every new pricing dimension is a migration and a code change.
- **Rejected:** fails the "future pricing strategies" requirement immediately.

### B. Mutable plans, no versioning

- **Pro:** simpler editing; one row per plan.
- **Con:** changing a limit retroactively changes what every existing customer bought, with no record of the original agreement. Grandfathering becomes impossible. Historical invoices cannot be reproduced.
- **Rejected:** a correctness and trust failure, and a compliance problem.

### C. Delegate pricing entirely to the payment provider

Stripe products/prices as the source of truth.

- **Pro:** less to build; billing integration is simpler.
- **Con:** provider lock-in at the core of the business model; partner price books and negotiated contracts do not map cleanly; entitlement computation would depend on an external API; multi-provider or regional-provider strategies become impossible.
- **Rejected:** the catalog is a core domain asset. The provider is a payment rail, and is modelled behind a port. Prices are *mirrored* to the provider for checkout, not owned by it.

### D. Derived bundle pricing (sum of parts minus a discount)

- **Pro:** bundles stay consistent when component prices change.
- **Con:** a component price change silently alters bundle pricing, including for existing customers; historical invoices become irreproducible; the discount needed to hit a target price is a computation nobody can explain.
- **Rejected:** bundle prices are real prices.

### E. Versioned catalog with segmented price books (selected)

## Why selected

1. **Segmentation by price book makes new strategies additive.** A new region, partner or channel is new rows, not new columns and not new code.
2. **Versioning preserves what the customer agreed to.** Grandfathering, migrations and historical reproduction all become straightforward.
3. **Plane separation holds.** `PriceBook` is control plane with no tenancy; `ContractPrice` is data plane with RLS. A bug in one cannot leak the other.
4. **One resolution function** means "why is this customer paying this?" is answerable — `PriceQuote` records which rule matched.
5. **Three feature kinds are enough** and keep packaging comparable on a pricing page. A JSON-config kind would become an unreviewable dumping ground.
6. **String product codes** keep catalog independent of product modules, so adding a product needs no catalog migration and commercial can sell a product that is not yet deployed.

## Consequences

**Positive**

- New pricing strategies are configuration.
- Partner and enterprise pricing use the same machinery as list pricing.
- Published plans and prices are auditable and immutable.
- Prices pinned on subscription items mean renewals do not surprise customers.
- Integer minor units eliminate floating-point money defects.

**Negative, accepted**

- More tables than a naive model. Justified by the stated requirements, every one of which a simpler model fails.
- Catalog administration needs real internal tooling. Budgeted explicitly.
- Version proliferation over time. Managed by lifecycle statuses (`grandfathered`, `retired`) and periodic cleanup of unreferenced drafts.
- Mirroring prices to the payment provider requires reconciliation. Handled by a sync job with drift alerting.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Price resolves wrongly for a segment | **High** | Deterministic order; `PriceQuote` records the reason; resolution test matrix over segments |
| Missing price silently falls back | **High** | `PriceNotAvailable` raised — never a fallback currency, region or zero |
| Published version edited in place | High | DB rule + application guard |
| Catalog and code drift (feature key not implemented) | Medium | Startup check `W360.E005` fails boot |
| Bundle grants an unintended plan tier | Medium | Bundles grant explicit plan versions; publish-time validation |
| Currency mismatch in arithmetic | Medium | `Money` refuses cross-currency operations |
| Partner price undercuts a floor | Medium | Validated at partner price-book publish |

## Future extraction path

`commercial/catalog` and `commercial/pricing` are strong extraction candidates in principle — they are read-heavy, rarely written, and off the product hot path. In practice there is little reason: they are small, cheaply cached, and extraction would add a network call to checkout with no measurable benefit.

If extracted, `resolve_price()` becomes an HTTP call behind the same signature, and quotes are cached aggressively since list prices change rarely.

## Related

- [05-commercial-model.md](../05-commercial-model.md), [06-entitlements.md](../06-entitlements.md)
- [ADR-005](ADR-005-product-entitlement-model.md), [ADR-010](ADR-010-partner-reseller-whitelabel-model.md)
