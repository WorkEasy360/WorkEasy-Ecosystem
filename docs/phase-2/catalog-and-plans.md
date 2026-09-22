# Phase 2 — Catalog, Features and Plans

**Date:** 2026-09-21

---

## 1. Product

A sellable SKU, not a code module.

```python
Product(code, name, description, status, licensing_mode, sort_order,
        activated_at, deprecated_at, retired_at)
```

`code` is validated against `ProductCode` (`^[a-z][a-z0-9_]{1,31}$`) and is
unique. Codes are never reused: `test_product_codes_are_unique_and_validated`.

### Lifecycle

```
draft ──> available ──> deprecated ──> retired
  │                         │            (terminal)
  └────────> retired        └──> available
```

Written as a table in `catalog/domain.py`, not as a chain of conditionals.
The table *is* the specification, and an absent edge is visible rather than
implied.

| Status | New subscriptions | Existing subscriptions |
|---|---|---|
| `draft` | no | n/a |
| `available` | yes | honoured |
| `deprecated` | no | honoured, and still renew |
| `retired` | no | **honoured** |

`retired -> available` does not exist. Resurrecting a withdrawn SKU under the
same code makes historical reporting ambiguous about which product a
subscription referred to; reintroduction is a new code.

### A retired product does not invalidate history

This is structural, not a promise. A subscription item pins an immutable
`PlanVersion` by id. Retiring the product changes a row the item does not
read. The live product status is consulted only when deciding whether a
**new** subscription may be created.

Asserted by `test_retiring_a_product_leaves_its_published_versions_readable`.

## 2. Feature

The unit the runtime actually checks.

```python
Feature(product, key, name, kind, unit, default_value, enum_ordering, is_public)
```

Three kinds and deliberately no more — `boolean`, `quota`, `enum`. A fourth
"JSON config" kind is rejected by ADR-006 because it becomes a dumping
ground, cannot be shown on a pricing page and cannot be compared across
plans.

### Two rules that prevent silent grants

**The key's leading segment must equal the owning product's code.**
`crm.pipelines.max` declared under Books would be sold by Books plans and
checked by CRM code, and the mismatch surfaces to the customer as "I paid and
it still says not entitled". Validated in `domain.validate_feature_key`, one
place, and asserted by `test_feature_key_must_belong_to_its_product`.

**`key` and `kind` are immutable after creation.** `update_feature` exposes
`name`, `description`, `unit` and `is_public` and nothing else. A published
plan states a value for a feature; changing the feature's kind underneath
would change what every such plan means without touching a single plan row.
Asserted structurally, on the function signature, because the guarantee is
that no caller *can* pass them.

### Values are checked against the kind

`domain.validate_feature_value` refuses a quota stored as `"25"`. A string
quota compares as `"25" >= 10`, which is `True` for the wrong reason and only
shows up when a customer reaches their limit. `bool` is excluded from the
quota branch explicitly, because it is a subclass of `int` and `True` would
otherwise become a quota of one.

An enum feature must declare `enum_ordering`, least to most generous.
Without an ordering, overlapping grants cannot be resolved and the customer's
effective value is arbitrary.

## 3. Plan and PlanVersion

```python
Plan(product, code, name, status, is_public, sort_order)          # identity
PlanVersion(plan, version, published_at, effective_from,          # terms
            effective_to, included_seats, max_seats, trial_days)
PlanFeature(plan_version, feature, value)                         # contents
```

Plan status uses the shared `CatalogStatus`: `draft`, `available`,
`grandfathered`, `retired`. `grandfathered` is the one people forget to model
until they need it under time pressure — no new subscriptions, existing ones
continue and still renew.

### Why a row per feature rather than columns

Boolean columns on the plan cannot express a quota, cannot be compared across
plans on a pricing page, and make every new capability a migration.
`test_plans_reference_features_rather_than_embedding_booleans` walks the
plan models and asserts no capability booleans exist.

---

## 4. The immutability answer

> **Task 4 asks: is plan configuration immutable/versioned, or snapshotted at
> subscription time? Do not leave this ambiguous.**

**Both, deliberately, and each covers what the other cannot.**

### Layer 1 — the plan version is immutable once published

Publishing sets `published_at`. From that instant, `plan_id`, `version`,
`effective_from`, `included_seats`, `max_seats` and `trial_days` can never
change, and neither can the version's `PlanFeature` rows.

Enforced in two places:

- **The service** raises `ImmutableRecord`, giving a clear error to a caller
  who tries.
- **A database trigger** refuses the write. This is the one that matters: the
  application guard is bypassed by a data-fix script, by the Django admin and
  by a bulk `update()`.

A trigger that **raises**, not the `DO INSTEAD NOTHING` rule the Phase 0
sketch suggested. A rule silently discards the update, so the caller sees
success and the operator believes the change landed. A refused write is
recoverable; a silently ignored one is a support ticket six months later.

Asserted by `test_published_plan_version_terms_are_immutable_at_the_database`
and `test_published_plan_features_are_frozen_at_the_database`, both of which
go around the service and write with `QuerySet.update()`.

### Layer 2 — the subscription item pins the version

`SubscriptionItem.plan_version_id` references the exact row, with
`on_delete=PROTECT`. Publishing v2 does not move an existing subscription;
`test_a_later_plan_version_does_not_change_an_existing_item` publishes a
version raising a quota from 3 to 99 and asserts the customer still resolves
to 3.

### Layer 3 — the item denormalises the commercial facts

`product_code`, `plan_code`, `plan_version_number`, `bundle_code`,
`quantity`, `price_id`, `price_source`, `price_reason`, `unit_amount_minor`,
`total_amount_minor`, `currency`, `interval`, `price_model`, `tax_behaviour`.

Belt and braces on purpose. Task 10 requires that historical commercial facts
are not derivable *only* from catalog tables, and a catalog row can be
superseded, closed, or restored from a different backup than the
subscription. `test_an_item_preserves_its_commercial_snapshot` pins every
field.

### Publishing supersedes rather than replaces

Publishing v2 sets v1's `effective_to`, so "which version is in force?" has
exactly one answer at any instant while v1 remains fully readable.
`effective_to` is the single column a published version may still change,
and the trigger permits exactly that and nothing else.

## 5. What the catalog refuses

Every one of these has a test, and the negative cases are the point — a
catalog that accepts a malformed plan produces a purchase that cannot be
provisioned, and that failure lands at a customer's checkout.

| Refused | Why |
|---|---|
| A duplicate product or plan code | Codes are stable identifiers and are never reused |
| A malformed product code or feature key | A mistyped key is a silent grant or denial |
| A feature key whose prefix is another product | Sold by one product, checked by another |
| A plan granting another product's feature | The plan would promise what it cannot deliver |
| A plan granting an undeclared feature | Commercial may only sell what the catalog declares |
| A quota stated as a string | Compares as a string; fails at the customer's limit |
| An enum feature with no ordering | Overlapping grants cannot be resolved |
| Publishing an already-published version | Publish a new version instead |
| Changing a feature's key or kind | Rewrites what every published plan means |

## 6. W360.E005, honestly

The architecture states that feature keys are **declared by the owning
product** and synced into the catalog, checked at boot by `W360.E005`.

No product module exists, so there is nothing to sync from and the check
cannot be implemented as specified. Phase 2 enforces the enforceable half:
the feature key's product segment must equal its `Product.code`, checked at
the service boundary and covered by a test.

`W360.E005` remains unimplemented, and that is recorded here rather than
quietly marked done. Its trigger is the first product module.
