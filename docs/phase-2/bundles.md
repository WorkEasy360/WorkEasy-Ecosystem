# Phase 2 — Bundles

**Date:** 2026-09-21

---

## 1. Model

```python
Bundle(code, name, kind, status, is_public)
BundleVersion(bundle, version, published_at, effective_from, effective_to)
BundleItem(bundle_version, plan_version, quantity, is_optional, seat_pool)
```

**A bundle grants plan versions, not products.** "The Full Suite includes
CRM" is ambiguous and unpriceable; "includes CRM Professional v3" is not.

Versioned and immutable-once-published on exactly the same mechanism as plan
versions, and for the same reason.

## 2. No nested bundles

`BundleItem` has no bundle-to-bundle reference and `Bundle` has no
self-reference. Phase 0 does not require nesting, and recursion turns
expansion, pricing and commission into algorithms nobody can explain to a
customer.

Asserted on the **schema** rather than by a service check, because a service
check can be removed: `test_a_bundle_cannot_contain_a_bundle` inspects the
foreign keys.

## 3. Validation

Deliberately strict at draft and re-checked at publish. A malformed bundle
discovered during checkout is a lost sale; discovered during expansion it is
a half-provisioned customer.

| Refused | Why | Test |
|---|---|---|
| The same plan version twice | Expands into two identical items and bills twice | `test_bundle_rejects_duplicate_items` |
| Two plans of the same product | Which tier the customer holds would be ambiguous | `test_bundle_rejects_two_plans_of_the_same_product` |
| An unpublished plan version | A draft can still change under the bundle | `test_bundle_rejects_an_unpublished_plan_version` |
| A retired plan | The purchase could not be fulfilled | at draft and at publish |
| A non-sellable product | Same | at draft |
| An empty item list | A bundle of nothing | `test_bundle_rejects_an_empty_item_list` |
| Activating a bundle containing a retired plan | The bundle would be on sale and unfulfillable | `test_activating_a_bundle_with_a_retired_plan_is_refused` |
| A public `custom` bundle | An enterprise package on the public pricing page | DB check constraint + test |

The duplicate case is also a **database constraint**
(`uq_bundle_item` on `(bundle_version, plan_version)`), so the service check
is the good error message and the constraint is the guarantee.

Re-validation at publish matters on its own: a plan can be retired between
drafting and publishing, and publishing a bundle that grants a withdrawn plan
produces a purchase that cannot be provisioned.

## 4. Purchase expansion

A bundle purchase produces **two kinds of line**:

```
Sales Suite purchase
│
├─ bundle line      bundle_version=…, product_code="",  price=6999   ← billed
│   │
│   ├─ derived line plan_version=CRM Professional v1,   price=None   ← access
│   └─ derived line plan_version=Books Professional v1, price=None   ← access
```

**Why two kinds.** A bundle is priced as one thing (ADR-006 rejects derived
bundle pricing outright). If each component carried an amount, summing the
subscription would invent revenue. If the bundle line granted access
directly, that would be the entitlement shortcut the architecture forbids.

So: the bundle line carries the price, the derived lines carry the access,
and `parent_item` links them. Three database check constraints hold the
shape:

- `ck_derived_item_is_a_plan` — a derived line is always a plan line
- `ck_derived_item_has_no_price` — a derived line carries no money
- `ck_priced_item_is_snapshotted` — a priced line is fully snapshotted or not priced at all

`test_bundle_components_carry_no_price` asserts the subscription bills 6999
once, not the sum of its parts.

### Derived lines are ordinary lines

Entitlement computation reads plan-version items uniformly and never asks how
one came to exist. `test_bundle_entitlements_use_the_ordinary_product_path`
buys a bundle and asserts `check(org, "crm")`, `check(org, "books")` and the
CRM pipeline quota all resolve through the normal path.

## 5. Expansion is atomic, idempotent, deterministic and auditable

| Property | Mechanism | Test |
|---|---|---|
| **Atomic** | One transaction around the bundle line and every derived line | `test_an_interrupted_bundle_expansion_leaves_nothing` |
| **Idempotent** | An idempotency key, plus `uq_one_open_item_per_product` as the backstop | `test_a_repeated_bundle_expansion_does_not_duplicate_items` |
| **Deterministic** | The bundle version's items, in a stable order | the version is immutable, so the input cannot vary |
| **Auditable** | One audit entry for the bundle, one `commercial.subscription.item_added.v1` per line | `test_sensitive_operations_are_audited` |

The interruption test is the sharpest one: it patches `_create_plan_item` to
fail on the **second** component and asserts zero items, zero subscriptions
and zero events remain. The customer either holds every component or none.

### The duplication guarantee is a database constraint

> *Can a bundle produce duplicated products?*

```sql
UNIQUE (subscription_id, product_code) WHERE removed_at IS NULL AND product_code <> ''
```

At most one open line per product, per subscription, enforced by PostgreSQL
rather than by expansion logic remembering to check. A future-dated plan
change sets the outgoing line's `removed_at`, which takes it out of the
index, so scheduled changes still work.

Proved under contention by
`test_concurrent_bundle_expansion_does_not_duplicate_products`: eight threads
expand the same bundle simultaneously and exactly three lines exist
afterwards.

## 6. Optional components

`BundleItem.is_optional` lets a customer decline a component within the
bundle. Declining a **mandatory** component is refused, and declining every
component is refused — there would be nothing to buy.

The bundle price is unchanged when a component is declined. That is correct:
the bundle price is a real price, not a computed sum, so removing a part does
not remove an addend.

## 7. Bundles and existing plans

Buying a bundle that grants a product the subscription already holds is
refused with a clear message, rather than silently creating a second source.
`test_a_bundle_clashing_with_an_existing_plan_is_refused`.

Note that **holding one product from two sources is legitimate** at the
entitlement level — 06-entitlements.md §4.1 requires exactly that, resolved
most-generously. What is refused is two open lines for one product *within a
single subscription*, which is a modelling error rather than a commercial
arrangement.

## 8. Shared seat pools

`BundleItem.seat_pool` names a pool. A bundle can grant 10 seats usable
across CRM and Projects rather than 10 each. Empty means per-product seats,
which is the common case.

Modelled now because retrofitting shared pools into a per-product seat model
is a painful migration, and "10 users across the suite" is a normal
enterprise ask. `test_a_bundle_seat_pool_is_shared` buys a pooled bundle and
asserts 15 seats in the shared pool and 0 in the per-product one.
