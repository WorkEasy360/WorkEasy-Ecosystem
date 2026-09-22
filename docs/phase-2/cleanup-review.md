# Phase 2 Cleanup Review

**Date:** 2026-09-21
**Method:** Grep the whole tree for callers. Remove only what has none.

---

## 1. Rule applied

Nothing was removed because it *looked* unused. Every item below was checked
by searching the entire package — production code, tests and fixtures — for a
caller, and removed only when there was none.

One distinction governed the borderline cases:

> A **reader for state the system deliberately writes** is necessary even if
> only a test reads it today — without it the state would be write-only.
> A **convenience wrapper over readers that already exist** is not.

That is why `seats.over_limit_assignments` stayed (it is the only way to
observe the over-limit flag the system sets) and `seats.usage` went (it
combined `capacity` and `assigned_count`, both of which have production
callers).

## 2. Removed

| Removed | Verified by | Why |
|---|---|---|
| `commercial/billing` · `BillingProviderCustomer` model | no reader, no writer, no test | See §3 |
| `commercial/billing` · `link_provider_customer` service | no caller | Its only purpose was the model above |
| `contracts/enums` · `PaymentProvider` | no remaining reference | Went with them |
| `commercial/catalog/selectors` · `current_bundle_version` | no caller | `require_bundle_version` covers every purchase path |
| `commercial/pricing/resolution` · `PriceQuote.total_minor` | no caller | `quote.total` is used directly |
| `platform/entitlements/services/seats` · `usage()` + `SeatUsage` | one caller, a test | A wrapper over two existing readers |
| `commercial/subscriptions/models` · `Subscription.access_ends_at` | no caller | Every caller uses the DTO's |
| `commercial/subscriptions/models` · `SubscriptionItem.is_effective_at` | no caller | Every caller uses the DTO's |
| `commercial/billing/models` · `BillingAccount.currency_enum` | no caller | No site converts it |
| `commercial/pricing/models` · `PlanPrice.is_published` | no caller | Publication is checked by the trigger and by `valid_to` |
| One duplicated test (`test_a_scheduled_downgrade_applies_at_its_moment`) | identical assertions | `test_a_reconciliation_pass_applies_time_based_changes` already covers it, from the module that owns the behaviour |
| A duplicated G6 check in the security suite | same assertion, two homes | Consolidated into the architecture suite, and upgraded to AST |

**One table and one service removed. Six dead methods. One duplicate test.
One duplicate check.**

## 3. Why `BillingProviderCustomer` went

Task 22 lists a payment-provider customer reference as a **possible** concept
and says to add only what the architecture needs.

Nothing in Phase 2 writes it, reads it or tests it. The architecture
(05-commercial-model.md) does not contain it. The payment integration that
would define its real shape — how many providers, whether the reference is
per-account or per-subscription, what happens on provider migration — is
explicitly out of scope.

Shipping it now means shipping a guess that will probably be wrong when it
finally has a user, in a schema that is expensive to change once it holds
production references. `BillingEvent` and `BillingAttempt` were never added,
for the same reason, and the module docstring now records all three
omissions with the same trigger:

> **Trigger: the first real payment-provider integration**, which will define
> their shape from a live API rather than from a guess.

`BillingAccount` stays. It is load-bearing: it fixes the organization's
billing currency, and every purchase depends on it.

## 4. Kept deliberately, despite looking removable

| Kept | Why |
|---|---|
| `seats.over_limit_assignments` | The only way to observe a flag the system sets. Removing it would make the over-limit state write-only |
| `provisioning.grant_product` | Used by the `entitled` fixture and the Phase 1 entitlement suite. Rewriting those to build full subscriptions would make platform tests depend on the whole commercial stack — worse coupling than the helper |
| `system.suspend_for_non_payment` | Not called by Phase 2 code, but it is the dunning entry point and has its own test. It is the documented `suspended` transition, which the status machine would otherwise be unable to reach |
| `PriceModel.USAGE` | Declared, refused at publish, tested. The model is documented by ADR-006; leaving it out would mean re-deciding it later |
| `PriceBookChannel.PARTNER` | The segmentation axis exists and `RESELLER_GOLD` is creatable. Only *selection* is absent, deliberately |
| `Subscription.version` | Real optimistic concurrency, exposed by the API and enforced by `_lock` |
| `ChangeTiming`, `TrialSource`, `CollectionMode`, `RenewalPolicy`, `TaxBehaviour` | All stored, all read |

## 5. Duplication consolidated

**The G6 mass-assignment check** existed in both the architecture suite and
the commercial security suite. One check, one home: it now lives in
`tests/architecture` only.

Both were also **text searches**, which flagged `commercial_views.py` because
its docstring explains the rule. Both replaced by an AST check that reads
assignments. A check that cannot tell documentation from code teaches people
to avoid mentioning the rule, which is the opposite of the point.

**The over-limit marker** briefly existed in two places — `seats.py` and
`write.py` — during implementation. Consolidated into `write.py`, because
flagging is a consequence of applying commercial capacity and belongs to the
writer that applies it.

## 6. Queries reduced

Covered in full in the performance review. In summary:

| Path | Before | After |
|---|---|---|
| Full reconciliation, 2-product bundle | 51 statements | under 40 |
| Per additional subscription line | +13 statements | within the pinned bound |
| Over-limit marking | 3 statements **per pool** | 1 SELECT + ≤ 2 UPDATEs for the whole organization |

Achieved by replacing per-row `update_or_create` with
`INSERT ... ON CONFLICT DO UPDATE`, and by identifying stale rows with
`computation_id` instead of a Python tuple comparison. Idempotence unchanged.

## 7. Dependencies

**None added. None removed.**

Phase 2 introduced no third-party package. Everything it needed —
`bulk_create(update_conflicts=…)`, partial unique constraints, `ON CONFLICT`,
advisory locks, plpgsql triggers — is Django 5.2 and PostgreSQL 16.

`pip-audit --strict` is clean.

## 8. Configuration

| Change | Reason |
|---|---|
| `CELERY_BEAT_SCHEDULE` added | Phase 2 reaches the documented trigger — see the failure-mode review §7 |
| Five new apps in `INSTALLED_APPS` | catalog, pricing, billing, subscriptions, idempotency |
| Two new import-linter contracts | Commercial internal layering; products may not write seats |

Nothing obsolete was found in settings. No setting was removed.

## 9. What was **not** removed, and why that is not manufactured restraint

Some categories in the brief's cleanup list had no instances:

| Category | Finding |
|---|---|
| Unused migrations helpers | None. `make_immutable_when_published` and `freeze_children_of_published_parent` are both used by `tenancy/0003` |
| Duplicate validators | None. `domain.py` in catalog and pricing are the only validators, and they do not overlap |
| Duplicate services | None after §5 |
| Unneeded dependencies | None added |
| Obsolete settings | None |
| Unused event definitions | All six commercial schemas are published by a service and asserted by a test |
| Unnecessary indexes | Each is listed against the query it serves in the performance review |
| Unnecessary middleware | None added |
| Unused files | None. Every new module has a caller or a test |
| Excessive abstractions | One found and removed (`SeatUsage`) |

This section exists because the brief asks for it explicitly: if nothing
should be removed, say so rather than manufacturing activity. In these ten
categories, nothing should be.

## 10. Architectural extension points — untouched

The brief warns against deleting extension points an ADR requires. These were
reviewed and kept:

- `PriceBookChannel.PARTNER` and the documented partner step in
  `resolve_price` (ADR-010)
- `BundleItem.seat_pool` (06-entitlements.md §6 — retrofitting shared pools
  would be a painful migration)
- `Bundle.kind = custom` (05-commercial-model.md §3.4)
- `EntitlementGrant` as an input to computation (ADR-005)
- `LicensingMode.USAGE` (05-commercial-model.md §3.1)
- The three feature kinds, and no fourth (ADR-006)
