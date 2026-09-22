# Phase 2 — The Commercial Domain

**Date:** 2026-09-21
**Scope:** What Phase 2 built, and the shape of the thing it built.

---

## 1. What this phase is for

Phase 2 converts three inputs

```
product definition  +  pricing  +  customer purchase state
```

into three outputs

```
subscription  +  licensing  +  entitlement provisioning
```

and nothing else. It builds no CRM, no Books, no Inventory, no reseller
dashboards and no customer-facing billing UI.

## 2. The one-paragraph version

A customer buys a **plan version** or a **bundle version** from the catalog.
That produces a **subscription** with one **item** per commercialised line,
each pinning the immutable plan version it bought and the immutable price row
it paid. `commercial/provisioning` reads that durable state, computes what the
organization should be able to do, and writes it into
`platform/entitlements`. Products ask `entitlements.check()` and never learn
which of those things happened.

## 3. Module map

```
commercial/
  catalog/        Product, Feature, Plan, PlanVersion, PlanFeature,
                  Bundle, BundleVersion, BundleItem        [control plane]
  pricing/        PriceBook, PlanPrice, PriceTier          [control plane]
                  ContractPrice                            [data plane, RLS]
  billing/        BillingAccount                           [data plane, RLS]
  subscriptions/  Subscription, SubscriptionItem           [data plane, RLS]
  provisioning/   the sole entitlement writer              [no tables]

integration/
  idempotency/    IdempotencyRecord                        [data plane, RLS]
```

Internal ordering, enforced by a new import-linter contract:

```
catalog  <  pricing  <  billing  <  subscriptions  <  provisioning
```

`provisioning` reads subscriptions, so subscriptions cannot import it back.
The dependency is inverted through an explicit registry
(`subscriptions/hooks.py`), not a Django signal — see §7.

## 4. Control plane and data plane

The split is the single most load-bearing structural decision in the phase.

| Plane | Tables | Tenancy | Who writes |
|---|---|---|---|
| Control | products, features, plans, plan versions, bundles, price books, list prices, tiers | **none** — no `organization_id`, no RLS | internal staff only |
| Data | contract prices, billing accounts, subscriptions, items, idempotency records | `TenantScopedModel` + RLS + composite FKs | the customer, through services |

A control-plane table with a tenant column would need RLS, and a bug in
catalog querying could then leak one customer's bespoke packaging to another.
A data-plane table without one would be readable across tenants. Neither
mistake is possible here, and
`test_the_catalog_holds_no_tenant_references` asserts the first half
structurally.

**Where this differs from the Phase 0 sketch.** 05-commercial-model.md §3.4
gives `Bundle` an `owner_organization` foreign key for custom enterprise
bundles. Phase 2 does not implement that field. A custom enterprise bundle is
a **non-public bundle plus a `ContractPrice` for that organization** — the
org-scoped, RLS-protected side of the relationship, which is where
customer-specific data belongs. The capability is unchanged; the tenant
reference moved to the plane that already protects it.

## 5. Identity is a code, never a name

`Product.code`, `Plan.code`, `Bundle.code` and `Feature.key` are the stable
machine identifiers. Names are display strings and change freely.
`test_product_identity_does_not_depend_on_its_name` renames a product and
asserts nothing moves.

`Product.code` is a **validated string, never a foreign key into a product
module**. An FK would make catalog depend on every product package, would
turn adding a product into a catalog migration, and would stop commercial
selling a product before its code is deployed.

## 6. What products may know

Nothing. `test_products_import_nothing_commercial` walks every file under
`products/` and asserts that none of them imports `commercial` or even
`platform.entitlements` — products reach entitlements through the
`contracts/ports` protocol and know nothing else.

The eight-word version of the rule products live by:

> Ask whether a capability is available. Never ask why.

## 7. How a commercial change reaches entitlements

```
purchase / upgrade / cancel
        │
        ├── one transaction ──────────────────────────────┐
        │     write subscription state                    │
        │     claim the idempotency key                   │
        │     publish commercial.*.v1 to the outbox       │
        │     write the audit entry                       │
        ├──────────────────────────────────────────────────┘
        │
        └── after commit: notify_commercial_change(org)
                  └── provisioning.reconcile(org)
                          compute desired state  (pure)
                          apply diff             (idempotent)
```

Two triggers, **one implementation**. The hook gives immediate provisioning;
the hourly `reconcile_all` pass gives durability. Both call the same
`reconcile`, so there is no second code path that could disagree with the
first. Losing a notification delays provisioning; it cannot lose it.

The registry rather than a Django signal, because a signal fires invisibly,
in an unspecified order, and silently does nothing for bulk operations —
which is why F-8 bans them across modules. `grep -r on_commercial_change`
is the complete list of listeners.

## 8. Documents in this set

| Document | Covers |
|---|---|
| [catalog-and-plans.md](catalog-and-plans.md) | Products, features, plans, versioning, and the immutability answer |
| [pricing.md](pricing.md) | Price books, resolution order, historical correctness, currency safety |
| [bundles.md](bundles.md) | Composition, validation, and how a purchase expands |
| [subscriptions.md](subscriptions.md) | Lifecycle, items, trials, upgrade, downgrade, cancellation, renewal |
| [licensing.md](licensing.md) | Seats, the concurrency guarantee, the over-limit policy |
| [entitlement-provisioning.md](entitlement-provisioning.md) | The translator, reconciliation, drift |
| [concurrency-review.md](concurrency-review.md) | Every contention point, and what was proved |
| [security-review.md](security-review.md) | Findings, controls, and the narrow escape hatches |
| [performance-review.md](performance-review.md) | Query counts, what was measured, what was optimised |
| [failure-mode-review.md](failure-mode-review.md) | What breaks, and what happens when it does |
| [cleanup-review.md](cleanup-review.md) | What was removed and why |
| [test-report.md](test-report.md) | Counts, coverage by area, how to run it |
| [phase-2-status.md](phase-2-status.md) | The exit gate, findings, and what is frozen |
