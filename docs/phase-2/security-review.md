# Phase 2 Security Review

**Date:** 2026-09-21
**Scope:** The commercial attack surface — the first place in Workeasy360
where money and access meet.

---

## 1. Summary

| Severity | Found | Status |
|---|---|---|
| **P0** | 2 | Both fixed, both with regression tests |
| **P1** | 3 | All fixed |
| **P2** | 4 | Documented and carried, each with a trigger |

The two P0s were found by writing transactional tests, not by reading code.
Both are recorded in full because the *shape* of each is worth remembering.

## 2. P0 findings

### P0-1 · Cross-tenant reads returned silently empty, revoking all access

**What.** `unscoped_access()` relaxes the ORM manager (isolation Layer 2). It
does not relax PostgreSQL RLS (Layer 3), and a query with no tenant variable
set matches **zero rows** rather than raising.

Every cross-tenant selector — `commercial_state`, `active_grants`,
`stored_snapshot` — relaxed only the first layer. Called from a Celery
worker, where no tenant variable is set, they returned nothing.

**Impact.** `reconcile()` read no subscriptions, computed an empty desired
state, and `apply_entitlements` correctly applied it: **every entitlement
expired**. On the hourly schedule, for every organization. A complete loss of
access for every paying customer, caused by the reconciliation job whose
purpose is to repair access.

**Why it hid.** Non-transactional tests share one outer transaction, and a
`set_config(..., true)` from any earlier fixture survives in it. The whole
Phase 1 + Phase 2 suite passed.

**Fix.** Every cross-tenant selector now enters `organization_db_scope` as
well as `unscoped_access`. The same defect existed in Phase 1's
`bump_permission_version` and is fixed identically.

**Regression.** `test_reconciliation_works_with_no_ambient_tenant_variable`
asserts the tenant variable is unset, then asserts the computed state is
correct — testing the outcome rather than the mechanism.

**Lesson recorded.** Fail-closed must mean *raise*, not *return nothing*. A
silent empty result is the most dangerous form of a security control
working, because it looks like an answer.

### P0-2 · Two concurrent first purchases created two subscriptions

**What.** `_open_subscription` took a row lock and read. With no row yet,
there is nothing to lock; two threads both found none and both inserted.

**Impact.** Two subscriptions for one organization means two renewal cycles
and two dunning cycles. More seriously, `uq_one_open_item_per_product` is
scoped *per subscription*, so the same product could be held twice — and
billed twice — across them.

**Fix.** A partial unique constraint
`UNIQUE (organization_id) WHERE status <> 'expired'`, plus an
`IntegrityError` re-read: both callers wanted an open subscription and one
of them got it.

**Regression.**
`test_concurrent_different_purchases_do_not_create_two_subscriptions`.

## 3. P1 findings

| # | Finding | Fix |
|---|---|---|
| P1-1 | `commercial.subscription.manage` was declared `dangerous`, which demands AAL 2. No session sets AAL above 1 (Phase 1 left MFA unwired), so **no customer could buy anything over HTTP** | Removed `dangerous`. Reasoning recorded in `permissions.py`: demanding a second factor to *buy* blocks self-serve signup for a risk the payment flow already authorises, and blocking it under impersonation would stop support completing a purchase a customer asked for. `platform.billing.manage`, which changes stored payment instruments, stays dangerous |
| P1-2 | `subscription_detail_view` re-parsed a `UUID` Django had already converted, raising `AttributeError` → **HTTP 500** on the IDOR path | Views take `UUID` directly. A 500 on a security path is the one status that hides whether the control fired |
| P1-3 | The `freeze_children_of_published_parent` trigger interpolated a table name into a SQL string literal and built its lookup with an f-string | Rewritten to pass the parent table, key column and publish column as **trigger arguments** and build the lookup with PostgreSQL's own `format(%I)`, matching the reviewed pattern already in `add_tenant_fk`. Bandit clean with **zero suppressions** |

## 4. Controls, and where each is asserted

### Authorization

| Control | Mechanism | Test |
|---|---|---|
| Deny by default | Phase 1 `authz.check` | inherited |
| Customers cannot manage the global catalog | **Two barriers**: no system role grants it, *and* the service requires internal staff | `test_a_customer_cannot_reach_the_catalog_admin_endpoint`, `test_catalog_management_requires_an_internal_identity` |
| Staff status alone is not enough | Both barriers required in both directions | `test_staff_status_alone_does_not_grant_catalog_management` |
| No system role carries control-plane permissions | Asserted over `SYSTEM_ROLES` | `test_no_system_role_grants_catalog_or_pricing_management` |
| `view` is not `manage` | A second member of the same org with read only | `test_viewing_a_subscription_does_not_allow_buying_one` |
| Checks live in services, not views | Views call one service; the service authorises | `test_commercial_views_contain_no_business_logic` |

**Internal and customer scopes are distinct by construction.**
`authz.is_platform_staff` reads `User.is_staff` — control-plane data a
customer administrator cannot grant themselves. Role assignments are tenant
rows they *can* control. Gating control-plane writes on the first is what
stops a compromised customer admin becoming a platform admin.

### Tenant isolation

| Control | Test |
|---|---|
| Five new tenant models, all RLS-enforced, FORCEd, `USING` + `WITH CHECK` | the parametrised Phase 1 isolation suite, automatically |
| Every tenant-to-tenant FK has a composite constraint | `test_every_tenant_to_tenant_fk_has_a_composite_constraint`, registry-derived |
| Another tenant's subscription is not addressable | `test_a_tenant_cannot_read_another_tenants_subscription` (404) |
| Tenant switching by path | `test_tenant_switching_by_path_is_refused` |
| Idempotency keys do not cross tenants | `test_idempotency_keys_do_not_cross_tenants` |
| A foreign record and a missing one answer identically | asserted explicitly — distinguishing them is an enumeration oracle |

The composite-FK test is **registry-derived**, not a hand-written list: it
computes every tenant-to-tenant FK from the live model registry and compares
against the live schema. The three new ones
(`subscriptions_item.subscription_id`, `.parent_item_id`,
`billing_provider_customer.billing_account_id` before its removal) were
covered the moment they existed.

### Mass assignment and tampering

No serializer framework, and no `fields = "__all__"` anywhere (asserted by
AST, so a docstring explaining the rule is not a violation). Each handler
names the fields it accepts.

| Attack | Result | Test |
|---|---|---|
| Price tampering — `total_amount_minor`, `unit_amount_minor`, `price_id` in the body | Ignored; server resolves from the catalog | `test_a_client_cannot_set_the_price_on_a_purchase` |
| Currency tampering — `currency` in the body | Ignored; comes from `BillingAccount` | `test_a_purchase_cannot_be_made_in_an_arbitrary_currency` |
| Status forcing — `status`, `version` in the body | Ignored; reached only through the state machine | `test_a_client_cannot_set_the_subscription_status` |
| Cross-tenant write — `organization_id` in the body | Ignored; comes from the verified path | `test_a_client_cannot_set_the_organization_on_a_purchase` |

### Replay and duplicate requests

| Control | Test |
|---|---|
| An `Idempotency-Key` header is **required** on every command | `test_a_command_without_an_idempotency_key_is_refused` |
| A replay returns the original result | `test_a_replayed_request_does_not_buy_twice` |
| A key reused with a different body is refused | `test_replaying_a_key_with_a_different_body_is_refused` |
| Under contention, the mismatch is still refused | `test_a_reused_key_with_different_arguments_loses_the_race_cleanly` |
| A failed command does not burn its key | `test_a_failed_command_does_not_burn_its_idempotency_key` |

The key is required rather than server-generated: a key the server invents is
unique per attempt, which makes every retry a new command and defeats the
point.

### Error responses

A domain error returns a stable `code`, a safe `detail` and a
`correlation_id`. No traceback, no exception type, no internals. A denial is
logged at info, not error, so error rate stays a useful signal.

### Audit

Every actor-driven commercial mutation calls `audit.record`, asserted over
the **source** by `test_every_commercial_mutation_is_audited`, which walks
each `services.py` by AST and checks any function taking a `ctx`.

Clock-driven transitions call `audit.record_system` with `ActorKind.SYSTEM`,
a null actor and a mandatory reason — rather than fabricating a user, which
would put a person's name against something they did not do.

`billing_email` is deliberately not copied into audit metadata:
`test_audit_metadata_carries_no_personal_data`.

### Event payloads

Stable identifiers only. No money, no personal data, no negotiated terms.
Events reach every subscribed consumer and are retained in the outbox and the
dead-letter table, so an amount in a payload is an amount disclosed
downstream. `test_event_payloads_carry_identifiers_not_dumps` asserts a
forbidden-field set and a size ceiling.

### Suspension (R-10)

Preserved exactly. A suspended organization can **read** its subscription and
**cannot buy**: `test_a_suspended_organization_cannot_buy` asserts 402 on the
write and 200 on the read.

## 5. The three narrow escape hatches

Phase 2 adds three modules to the `unscoped` allowlist. Each mirrors an
existing Phase 1 precedent, each is one file, and each is recorded in the
test that maintains the list.

| Module | Why | Blast radius |
|---|---|---|
| `platform/audit/services.py` | `record_system` audits clock-driven transitions, which have no actor and so no `TenantContext` | One organization's own audit log, inside its DB scope |
| `commercial/subscriptions/selectors.py` | Provisioning reads commercial state outside a request — the same reason `platform/entitlements/selectors.py` is already allowlisted | Read-only, one organization, inside its DB scope |
| `commercial/subscriptions/system.py` | The single system write pathway. Trials, cancellations and period rolls must work for **suspended** organizations, whose writes R-10 blocks under any tenant context | One organization's own subscription rows, inside its DB scope |

**RLS is never relaxed by any of them.** `unscoped_access` is Layer 2 only;
every one of these enters `organization_db_scope` for exactly one
organization. A bug in `system.py` can corrupt one tenant's own record and
cannot reach another's.

`system.py` additionally: takes no caller-supplied status, applies exactly
one transition per function decided by a durable timestamp that has passed,
and audits every operation with a failure that fails the operation.

## 6. P2 findings — carried

| # | Finding | Why accepted | Trigger |
|---|---|---|---|
| P2-1 | The commercial API has no rate limiting | Phase 1 P2-1, unchanged. Nothing is publicly exposed; the idempotency ledger makes replay cheap to refuse | Before first production customer |
| P2-2 | No step-up (AAL 2) on any commercial operation | No session sets AAL above 1 — MFA wiring is Phase 1 P2-2. `commercial.catalog.manage` and `commercial.pricing.manage` are declared `dangerous` and will demand it the moment sessions carry it | Session/MFA policy wiring |
| P2-3 | The over-limit seat deadline is not swept | The flag and timestamp exist; automatically releasing someone's access with no way for the customer to have chosen differently would be the silent revocation the design avoids | Seat-management UI |
| P2-4 | `W360.E005` (feature key ↔ product manifest) is unimplemented | No product module exists to sync from. The enforceable half — key prefix must equal `Product.code` — is enforced and tested | The first product module |

## 7. Adversarial questions, answered

| Question | Answer |
|---|---|
| Can concurrent operations violate financial state? | No. 11 contention points tested with real threads; see the concurrency review |
| Can historical pricing mutate? | No. Database triggers refuse it; `QuerySet.update()` is the test's attack vector |
| Can a bundle produce duplicated products? | No. A partial unique constraint, proved under 8-way contention |
| Can a tenant access another tenant's subscriptions? | No. RLS, the scoped manager, composite FKs, and a 404 that discloses nothing |
| Can products bypass entitlements? | No. Products import neither `commercial` nor `platform.entitlements`, asserted by AST over the whole tree |
| Can commercial code bypass the single-writer rule? | No. Import contract, plus an AST sweep that covers modules the contract does not name |
| Can duplicate queue delivery create duplicate subscriptions? | No. Idempotency ledger, unique constraint, tested under contention |
| Can cancellation leave stale access? | No. Access is computed from durable timestamps at one moment, in one function |
| Can failure halfway through provisioning create partial access? | No. `apply_entitlements` is one transaction; a failing hook leaves the commercial record intact and reconciliation repairs access |
| Can a future product consume this without commercial coupling? | Yes. It imports `contracts/ports/entitlements` and nothing else |
