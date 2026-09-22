> **SUPERSEDED — CRM MOVED OUTSIDE PLATFORM SCOPE.**
>
> This document describes the CRM product as it was built *inside* this
> repository during Phase 3. That code was removed by the Phase 3R scope
> correction: products are now separate applications in their own
> repositories ([ADR-017](../architecture/adrs/ADR-017-products-are-separate-applications.md),
> [16 Ecosystem / Product Boundary](../architecture/16-ecosystem-product-boundary.md)).
>
> Retained as historical audit evidence only. **Nothing here describes the
> current repository.** For what was kept from this work and why, see
> [Phase 3R status](../phase-3R/phase-3R-status.md).

---

# Phase 3 — CRM Domain Model

**Status:** Accepted (Phase 3)
**Owner:** Products/CRM

---

## 1. Purpose

CRM is Phase 3's business-product proof of the platform: the first real
tenant of `platform/entitlements`, `platform/authz`, `platform/audit`,
`platform/notifications`, `integration/events` and `platform/tenancy` from
outside the control plane. Everything below is built to consume those
services, never to duplicate them (Phase 3 Task 59).

## 2. Package layout

```
backend/workeasy360/products/crm/
├── apps.py              # registers permissions, notifications, tasks, events
├── authz.py              # require_crm_access(): entitlement + permission gate
├── notifications.py       # registered notification templates
├── permissions.py         # the CRM privilege surface (see permissions.md)
├── domain/                # pure logic - no Django import anywhere in this package
│   ├── enums.py
│   ├── errors.py
│   ├── deal_stages.py      # stage-transition classification
│   └── custom_fields.py    # custom-field value validation
├── models/                 # schema + constraints, one file per aggregate
├── services/                # use cases, transactions, events, audit
├── selectors/                # read DTOs, pagination, search, KPIs, forecast
├── api/                      # thin HTTP adapters + urls.py
└── tests/                    # domain, service, API, entitlement, task tests
```

This is the canonical layout `docs/architecture/14-repository-structure.md`
describes, with one deliberate omission: **no `manifest.py`.** The
architecture doc's `ProductManifest` (code, features, publishes, consumes)
has no consumer anywhere in the codebase yet - no product registry reads
it, no other module imports it - so adding it now would be exactly the
"unnecessary abstraction layer" the lean-code requirement forbids. If a
product registry is introduced later, CRM's `permissions.py` (privilege
surface), `contracts/events/product_events.py` (publishes) and
`platform.entitlements` (consumes, via `authz.require_crm_access`) already
contain everything a manifest would summarise - there was nothing left to
express that those three don't already say. Also absent: `admin.py` -
`commercial/*` sets the actual precedent (no module has one), not the
aspirational tree.

## 3. Entities

| Model | Table | Tenant-scoped | Notes |
|---|---|---|---|
| `Company` | `crm_company` | yes | The CRM customer's own customer/account - never confused with `organizations.Organization` (see companies-contacts.md) |
| `Contact` | `crm_contact` | yes | Email is not unique (Task 6) |
| `CompanyContact` | `crm_company_contact` | yes | Explicit M2M, one `is_primary` per contact (partial unique index) |
| `Lead` | `crm_lead` | yes | Distinct from `Contact`; converts atomically (see leads-conversion.md) |
| `Pipeline` | `crm_pipeline` | yes | Exactly one `is_default=True` per org (partial unique index) |
| `PipelineStage` | `crm_pipeline_stage` | yes | `category` (open/won/lost) drives semantics, never `name` |
| `Deal` | `crm_deal` | yes | No separate `status` field - `stage.category` is the only source of truth (see pipelines-deals.md) |
| `DealStageHistory` | `crm_deal_stage_history` | yes | Append-only (DB grant + trigger) |
| `DealContact` | `crm_deal_contact` | yes | Role per (deal, contact) pair |
| `Tag`, `CompanyTag`, `ContactTag`, `LeadTag`, `DealTag` | `crm_tag*` | yes | Explicit join tables, no `GenericForeignKey` (see custom-fields-tags.md) |
| `CustomFieldDefinition` | `crm_custom_field_definition` | yes | Normalised; values live denormalised-but-validated on each entity |
| `Activity` | `crm_activity` | yes | Task/call/meeting, one model with a type discriminator |
| `Note` | `crm_note` | yes | Ordinary CRUD, not append-only, not an `AuditEvent` |

**17 tenant-scoped tables, zero control-plane tables.** CRM has no global
reference data of its own - unlike `commercial/catalog`, nothing here is
shared across organizations.

## 4. Layer responsibilities (as built)

Followed exactly as `14-repository-structure.md` section 3.1 specifies:

- `models/` - fields, `Meta.constraints`, `Meta.indexes`, small properties
  (`status_enum`, `is_open`). No service calls, no cross-entity logic.
- `domain/` - `deal_stages.transition()` and
  `custom_fields.validate_custom_fields()` are the only two pieces of logic
  substantial enough to be worth pulling out of `services/`; both are pure
  functions tested with zero database access (`tests/test_domain.py`, 18
  tests, no `db` fixture).
- `services/` - every write. Each public function's first two lines are
  always `require_crm_access(ctx, PERMISSION, write=...)` then input
  validation; every write path is wrapped in exactly one outer
  `transaction.atomic()`.
- `selectors/` - every read. Returns plain dicts (DTOs), never a model
  instance or a bare queryset, and every list function takes the same
  `(status, owner_id, tag, search, sort, limit, cursor)` shape.
- `api/` - one file per resource group, `_parsing.py` for the shared
  body/query parsing helpers (mirrors `interfaces/api/v1/commercial_views.py`,
  which is the only existing precedent for this repository's HTTP-adapter
  shape).

## 5. What consumes what

```
api/            -> services/, selectors/
services/       -> domain/, models/, platform.entitlements, platform.authz,
                   platform.audit, integration.events, integration.idempotency
selectors/      -> models/, platform.authz, platform.entitlements
domain/         -> (nothing - pure)
```

No file in `products/crm/` imports anything from `commercial/` - verified by
`lint-imports` (contract `F-2`, `products must not import commercial`,
kept) and by `test_no_cross_module_orm_access`, which additionally confirms
every model import CRM performs targets only `organizations`, `identity`,
`files` or `platform.tenancy` - the F-6 allowlist.

## 6. Related documents

- [companies-contacts.md](companies-contacts.md)
- [leads-conversion.md](leads-conversion.md)
- [pipelines-deals.md](pipelines-deals.md)
- [activities.md](activities.md)
- [custom-fields-tags.md](custom-fields-tags.md)
- [permissions.md](permissions.md)
- [tenant-isolation-review.md](tenant-isolation-review.md)
- [concurrency-review.md](concurrency-review.md)
- [security-review.md](security-review.md)
- [performance-review.md](performance-review.md)
- [failure-mode-review.md](failure-mode-review.md)
- [observability-review.md](observability-review.md)
- [frontend-review.md](frontend-review.md)
- [cleanup-review.md](cleanup-review.md)
- [test-report.md](test-report.md)
- [phase-3-status.md](phase-3-status.md)
