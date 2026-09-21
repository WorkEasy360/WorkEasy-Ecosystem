# 14 — Repository Structure

**Status:** Accepted (Phase 0)
**Owner:** Architecture
**Last reviewed:** 2026-09-21

---

## 1. Purpose

Define where code lives, so that a developer joining in year three can find the owner of any behaviour in under a minute. Structure is a communication tool: if the layout does not make ownership obvious, the boundaries in [01-domain-boundaries.md](01-domain-boundaries.md) will not survive contact with a deadline.

## 2. Repository layout

A **single repository** (not a monorepo of many packages, not separate repos). Backend and frontend together, because API and client change together and a cross-repo PR pair is a recurring friction with no offsetting benefit at our size.

```
workeasy360/
├── ARCHITECTURE.md                 # the rules every developer must read
├── README.md
├── .importlinter                   # enforced dependency contracts
├── pyproject.toml
├── docker-compose.yml              # incl. PgBouncer in transaction mode (parity)
├── Makefile
│
├── backend/
│   ├── manage.py
│   ├── conftest.py                 # shared fixtures; swaps to the non-owner role
│   ├── config/                     # settings, not application code
│   │   ├── settings/{base,local,ci,staging,production}.py
│   │   ├── urls.py                 # includes interfaces/ only
│   │   ├── asgi.py  wsgi.py  celery.py
│   │
│   ├── workeasy360/                # ← see ADR-013: the six modules below are
│   │                               #   nested here because a top-level
│   │                               #   `platform` package shadows the Python
│   │                               #   standard library module of that name.
│   │                               #   Directory names and layer rules are
│   │                               #   unchanged; only the import prefix is.
│   │
│   ├── contracts/                  # L0 — no internal deps, no Django
│   │   ├── events/{envelope,registry,platform_events,commercial_events,product_events}.py
│   │   ├── ports/{entitlements,notifications,files,payments,ai,search,product_data}.py
│   │   ├── values/{money,address,tenant,identifiers}.py
│   │   ├── enums.py
│   │   └── errors.py
│   │
│   ├── platform/                   # L1
│   │   ├── observability/                      # P0a  (ADR-015)
│   │   ├── tenancy/                            # P0b  may import P0a
│   │   ├── identity/  organizations/           # P1
│   │   ├── authz/  entitlements/  feature_flags/  audit/   # P2
│   │   └── files/  notifications/  workflows/  # P3
│   │
│   ├── integration/                # L2
│   │   ├── events/  webhooks/  idempotency/
│   │   └── connectors/{stripe,ses,anthropic,...}/
│   │
│   ├── commercial/                 # L3
│   │   ├── catalog/  pricing/  subscriptions/  billing/
│   │   └── partners/  commissions/  provisioning/
│   │
│   ├── products/                   # L3
│   │   ├── crm/  books/  inventory/  projects/
│   │   └── marketing/  support/  hr/  payroll/  commerce/
│   │
│   ├── interfaces/                 # L4 — composition only
│   │   ├── api/{v1/urls.py, versioning.py, throttles.py, authentication.py, schema.py}
│   │   ├── admin/                  # internal staff console
│   │   └── partner/                # partner portal composition
│   │
│   └── tests/
│       ├── architecture/           # fitness rules
│       ├── isolation/              # tenant isolation + canaries
│       ├── integration/
│       └── factories/
│
├── frontend/
│   └── src/
│       ├── app/                    # Next.js App Router — routing only
│       ├── features/               # mirrors backend modules
│       ├── platform/               # auth, org switching, entitlement gates
│       ├── lib/{api,hooks,format}/
│       └── components/ui/
│
├── infra/                          # Terraform, task definitions, runbooks
├── docs/architecture/              # this documentation
└── scripts/
```

## 3. Inside a module — the canonical layout

Every module in `platform/`, `commercial/` and `products/` uses the same internal structure. Uniformity means a developer opening an unfamiliar module already knows where to look.

```
products/crm/
├── __init__.py
├── apps.py                     # AppConfig: registers manifest, events, permissions
├── manifest.py                 # ProductManifest: code, features, publishes, consumes
├── permissions.py              # PermissionDeclaration list — the module's privilege surface
│
├── models/                     # ORM. Structure + invariants. No orchestration.
│   ├── __init__.py
│   ├── contact.py  company.py  deal.py  pipeline.py
│
├── domain/                     # Pure Python. NO Django imports.
│   ├── deal_stages.py          # state machine
│   ├── scoring.py              # calculations
│   └── policies.py             # business rules
│
├── services/                   # Application services: use cases. Own transactions.
│   ├── deal_services.py
│   └── contact_services.py
│
├── selectors/                  # Read queries. Return DTOs. No writes.
│   ├── deal_selectors.py
│   └── contact_selectors.py
│
├── authz.py                    # visible_* queryset predicates, object rules
├── adapters.py                 # implementations of contracts/ ports
│
├── api/
│   ├── serializers.py  views.py  urls.py  filters.py
│
├── tasks/
│   └── deal_tasks.py           # thin Celery wrappers over services
│
├── events/
│   ├── published.py            # what this module publishes (schema refs)
│   └── handlers.py             # handlers for events it consumes
│
├── admin.py
├── migrations/
└── tests/
    ├── test_domain.py          # fast, no DB
    ├── test_services.py
    ├── test_selectors.py
    ├── test_api.py
    └── test_events.py
```

### 3.1 Layer responsibilities

| Layer | Owns | Must not |
|---|---|---|
| `models/` | Schema, field validation, DB constraints, simple properties | Orchestrate use cases, call services, do I/O, call other modules |
| `domain/` | Pure business rules, calculations, state machines | Import Django, touch the database, do any I/O |
| `services/` | Use cases, transaction boundaries, invariant enforcement across aggregates, event publication | Contain HTTP concerns, format output, be called by another module's service |
| `selectors/` | Read queries, DTO shaping, visibility filtering | Write anything |
| `api/` | HTTP: parse, delegate, serialise, status codes | Contain business logic, query models directly |
| `tasks/` | Async entry points; parse args, call one service | Contain business logic |
| `events/` | Schema refs and handlers that delegate to services | Contain business logic |

### 3.2 Why `domain/` is separate from `models/`

`domain/` holds logic with no Django dependency: a deal-stage transition table, a commission calculation, an invoice-total computation.

The payoff is concrete: these tests run in milliseconds with no database, the logic is readable without ORM noise, and it cannot accidentally trigger a query mid-calculation. When a module has little such logic, `domain/` stays small or absent — this is not a mandate to create empty layers.

### 3.3 Why selectors are separate from services

Reads and writes have genuinely different concerns: reads need visibility filtering, pagination, prefetching and DTO shaping; writes need transactions, invariants and events. Mixing them produces functions that are hard to optimise and hard to authorize correctly.

This is a light CQRS influence, not CQRS: one model, two access paths. No separate read store, no eventual consistency inside a module.

## 4. Worked example — the shape of correct code

```python
# api/views.py — thin adapter. No logic.
class DealWinView(APIView):
    def post(self, request, org_slug, deal_id):
        deal = deal_services.mark_deal_won(
            ctx=request.tenant_context,
            deal_id=deal_id,
            data=WinDealSerializer(data=request.data).validated()
        )
        return Response(DealSerializer(deal).data, status=200)


# services/deal_services.py — the use case. Owns the transaction.
def mark_deal_won(*, ctx: TenantContext, deal_id: UUID, data: WinDealInput) -> Deal:
    entitlements.require(ctx.organization_id, ProductCode("crm"), write=True)
    authz.require(ctx, "crm.deal.update")

    with transaction.atomic():
        deal = Deal.objects.select_for_update().get(pk=deal_id)
        authz.require_object(ctx, "crm.deal.update", deal)

        transition = deal_stages.transition(deal.stage, DealStage.WON)   # domain/, pure
        if not transition.allowed:
            raise InvalidTransition(transition.reason)

        deal.stage = DealStage.WON
        deal.closed_at = data.closed_at or timezone.now()
        deal.save(update_fields=["stage", "closed_at", "updated_at"])

        audit.record(ctx, "crm.deal.won", resource=deal)
        events.publish(CrmDealWonV1.from_deal(deal))     # outbox, same transaction

    return deal


# domain/deal_stages.py — pure. No Django. Milliseconds to test.
def transition(current: DealStage, target: DealStage) -> TransitionResult:
    if target not in ALLOWED_TRANSITIONS[current]:
        return TransitionResult(False, f"Cannot move from {current} to {target}")
    return TransitionResult(True, None)


# tasks/deal_tasks.py — thin wrapper.
@shared_task(base=TenantTask, queue="default", soft_time_limit=60, time_limit=120)
def recalculate_deal_scores(organization_id: str) -> None:
    deal_services.recalculate_scores(ctx=current_tenant())
```

Each layer is visible and has one job. A reviewer can tell at a glance whether something is in the wrong place.

## 5. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Module | lowercase singular | `crm`, `books` |
| Model | PascalCase singular | `Deal`, `JournalEntry` |
| Service function | verb-first, explicit | `mark_deal_won`, `issue_invoice` |
| Selector | noun-first, plural for collections | `deals_for_pipeline`, `deal_detail` |
| Event class | `<Product><Aggregate><Verb>V<n>` | `CrmDealWonV1` |
| Event type | `<product>.<aggregate>.<verb>` | `crm.deal.won` |
| Permission | `<product>.<resource>.<action>` | `crm.deal.update` |
| Feature key | `<product>.<area>.<name>` | `crm.pipelines.max` |
| Task | `<verb>_<noun>` | `send_invoice_email` |
| Queue | workload class | `billing`, `events` |
| Migration | numbered + descriptive | `0007_add_deal_probability.py` |

**Services are `verb_noun`; selectors are `noun_phrase`.** The name alone tells you whether a function writes.

## 6. Banned patterns

| Banned | Why | Instead |
|---|---|---|
| `utils.py`, `helpers.py`, `common.py`, `misc.py` | Unowned dumping grounds that accumulate business logic no one can find | Name the concern: `date_ranges.py`, `tax_ids.py` |
| `products/shared/` | Becomes a second, unowned product everyone depends on | Promote to platform, or a pure library, or events |
| Business logic in `views.py` | Bypassed by tasks and internal callers; untestable without HTTP | Application service |
| Business logic in `serializers.py` | Same, plus it runs during validation in surprising order | Application service |
| Business logic in `models.py` beyond invariants | Fat models become unnavigable and pull in cross-module concerns | `domain/` or `services/` |
| Business logic in `admin.py` | Invisible to the API and to tests | Application service |
| Cross-module Django signals | Invisible, unordered, break on bulk ops, fire inside the sender's transaction | Explicit call or outbox event |
| `from products.other.models import X` | The defining boundary violation | Event or port |
| `Model.objects.all()` in a view | Bypasses tenant scoping and visibility rules | Selector using `visible_*` |
| Service files over ~500 lines | Signals a module doing too much | Split by aggregate |
| `fields = "__all__"` on a writable serializer | Mass-assignment vulnerability | Explicit field list |
| Raw SQL with f-strings | SQL injection | Parameterised queries |
| `import *` | Breaks static analysis and the import linter | Explicit imports |

## 7. Frontend structure

```
frontend/src/
├── app/                              # App Router — routing and layout ONLY
│   ├── (auth)/login/  signup/
│   ├── (app)/[orgSlug]/
│   │   ├── layout.tsx                # org shell: nav, switcher, entitlement context
│   │   ├── crm/  books/  inventory/  projects/
│   │   └── settings/{members,roles,billing,security}/
│   ├── (partner)/partner/
│   └── (admin)/admin/
│
├── features/                         # mirrors backend modules
│   ├── crm/{api,components,hooks,types,schemas}/
│   └── books/  inventory/  ...
│
├── platform/
│   ├── auth/  org/  entitlements/    # <EntitlementGate>, useEntitlement()
│   ├── permissions/                  # <Can permission="crm.deal.update">
│   └── branding/                     # white-label theme resolution
│
├── lib/{api/client.ts, api/generated/, hooks/, format/}
└── components/ui/
```

Rules:

1. **`app/` contains routing and layout only.** Logic lives in `features/`. Next.js route files become unmaintainable when they accumulate behaviour.
2. **`features/` mirrors backend modules**, so a full-stack change touches `products/crm/` and `features/crm/`. A feature must not import another feature's internals — the same boundary rule, one layer up.
3. **The API client is generated from the OpenAPI schema.** Hand-written clients drift; generated ones fail the build when the contract changes.
4. **Entitlement gating in the UI is presentation only.** `<EntitlementGate product="books">` hides a nav item; the API still enforces it. Anyone can open dev tools.
5. **Branding is resolved server-side** and provided via context. Never fetched per component.
6. **Money is rendered from `(minorUnits, currency)`**, never from a float, using a shared formatter.

## 8. Testing structure

| Type | Location | Speed | Runs |
|---|---|---|---|
| Domain (pure) | `<module>/tests/test_domain.py` | ms, no DB | Every save |
| Service | `<module>/tests/test_services.py` | fast, DB | Every PR |
| Selector | `<module>/tests/test_selectors.py` | fast, DB | Every PR |
| API | `<module>/tests/test_api.py` | medium | Every PR |
| Event | `<module>/tests/test_events.py` | medium | Every PR |
| Architecture | `tests/architecture/` | fast | Every PR |
| Isolation | `tests/isolation/` | medium | Every PR |
| Integration | `tests/integration/` | slow | Every PR |
| Load / failure | `tests/performance/`, `tests/chaos/` | very slow | Scheduled + release |

**Non-negotiable test requirements**, because they defend the properties this architecture is built on:

- Every tenant-scoped model has an isolation test proving tenant B cannot read tenant A's rows through the API.
- Every endpoint has an authorization test for each relevant persona.
- Every event handler has a duplicate-delivery test asserting idempotency.
- Every product's API has an entitlement test asserting denial when not entitled.
- Every hot endpoint has a query-count assertion (N+1 regression guard).

## 9. Related documents

- [01-domain-boundaries.md](01-domain-boundaries.md) — what modules exist
- [02-dependency-rules.md](02-dependency-rules.md) — enforcement
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — the rules and fitness checks
