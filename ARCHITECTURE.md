# Workeasy360 — Architecture Rules

**Read this before making any structural change.**
Full documentation: [docs/architecture/](docs/architecture/) · Decisions: [docs/architecture/adrs/](docs/architecture/adrs/)

---

## 1. What Workeasy360 is

> The shared multi-tenant **ecosystem platform** for a family of **independently commercializable** business products (CRM, Books, Inventory, Projects, …), sold through direct and partner channels that are **commercial constructs — never forks or separate deployments**.

**This repository is the platform. Products are separate applications in their own repositories** ([ADR-017](docs/architecture/adrs/ADR-017-products-are-separate-applications.md) · [16 — Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md)). Business-domain code — contacts, deals, invoices, ledgers, stock items, tasks, employees — never lives here.

Four rules follow, and everything else in this document exists to protect them:

1. **A product must ship, run and be sold without any other product.**
2. **A product must never know how a capability was purchased.** It asks "is this org entitled?" and gets a yes/no.
3. **A product integrates through a versioned contract** — never an import, never a shared database, never a platform ORM model. Since Phase 3 that contract is live: OpenID Connect for identity, an HTTP endpoint for entitlement ([docs/phase-3/](docs/phase-3/00-overview.md)).
4. **A partner never receives a copy of the product.** Partners get pricing, branding and relationship metadata on the same running system.

## 2. The module map

```
contracts/     L0   interfaces, event schemas, value objects. NO internal deps, NO Django.
platform/      L1   tenancy, identity, organizations, authz, entitlements,
                    feature_flags, audit, files, notifications, workflows,
                    ecosystem (the remote product front door — ADR-018),
                    launcher, administration (the organization console)
integration/   L2   events + outbox + routed events, idempotency, scheduling (due-work
                    timer), egress (the one outbound HTTP client), connectors,
                    webhooks, automation, routing
commercial/    L3   catalog, pricing, subscriptions, billing, partners,
                    commissions, provisioning, payments (invoices, hosted
                    checkout, webhooks, dunning)
intelligence/  L3   ai (model gateway, providers, spend), rag (retrieval,
                    pgvector), analytics (metrics, facts, aggregates), gateway
products/      L3   RESERVED AND EMPTY. Products are separate applications
                    (ADR-017); any file here fails the build.
interfaces/    L4   routing and composition only. No business logic.
```

**Dependencies point downward. Never sideways at L3. Never upward.**

| From | May import | May NOT import |
|---|---|---|
| `contracts/` | nothing internal | everything internal |
| `platform/<m>` | `contracts`, strictly lower platform layers | platform siblings, and everything above |
| `integration/` | `contracts`, `platform` | `commercial`, `products`, `interfaces` |
| `commercial/` | `contracts`, `platform`, `integration` | `products`, `intelligence`, `interfaces` |
| `intelligence/` | `contracts`, `platform`, `integration` | `commercial`, `products`, `interfaces`; and any authority writer (`ai-is-never-an-authority`) |
| `products/<p>` | — (reserved, empty) | everything — a product is a separate application |
| `interfaces/` | everything | — (but no business logic) |

Inside `platform/`: **P0** `tenancy`, `observability` → **P1** `identity`, `organizations` → **P2** `authz`, `entitlements`, `feature_flags`, `audit` → **P3** `files`, `notifications`, `workflows`, `ecosystem`, `onboarding` → **P4** `launcher` | `administration`. Lower layers only; siblings communicate via events. `launcher` (Phase 4, [ADR-021](docs/architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md)) composes `ecosystem` in addition to everything `ecosystem` itself composes, which is why it sits one layer above rather than beside it. `onboarding` (Phase 5, [ADR-023](docs/architecture/adrs/ADR-023-onboarding-orchestration-layer.md)) composes `organizations`, `identity`, `authz` and `audit` for organization creation. `administration` (Phase 8 extension, [ADR-033](docs/architecture/adrs/ADR-033-session-registry-and-organization-access-policy.md)) composes the same modules for the organization console: members, invitations, security policy, dashboard, and the Super Admin user directory.

Inside `commercial/` (import-linter `commercial-internal-layers`): `catalog` → `pricing` → `billing` → `subscriptions` → `provisioning` | `payments` → `partners` → `commissions` → `controlplane`. Payment provider adapters in `payments/providers` import nothing but `contracts` and their own contract (`payment-providers-are-sandboxed`); `payments/transport.py` is the only sender, through `integration/egress` ([ADR-034](docs/architecture/adrs/ADR-034-payments-hosted-checkout-and-provider-adapters.md)).

Inside `integration/` (Phase 6, import-linter `integration-internal-layers`): `events`, `idempotency`, `scheduling`, `egress` → `connectors` → `webhooks` → `automation` → `routing`. Connector *providers* may import only `contracts` and the connector contract (`connector-providers-are-sandboxed`). Intra-`integration` foreign keys are permitted (D-017, [ADR-024](docs/architecture/adrs/ADR-024-integration-platform-data-model-and-due-work.md)).

Inside `intelligence/` (Phase 8, import-linter `intelligence-internal-layers`): `ai` | `analytics` → `rag` → `gateway`. Provider adapters in `ai/providers` import nothing but `contracts` and their own contract (`ai-providers-are-sandboxed`); `ai/transport.py` is the only sender, through `integration/egress`. `platform` and `integration` never import `intelligence`; analytics is fed by a consumer registered with `integration/routing`, not by an import ([ADR-030](docs/architecture/adrs/ADR-030-ai-gateway-and-provider-ownership.md) … [ADR-032](docs/architecture/adrs/ADR-032-analytics-semantic-and-event-model.md)).

## 3. The eight forbidden edges

| # | Forbidden | Instead |
|---|---|---|
| **F-1** | `products/a` importing `products/b` — for any reason, including type hints | Domain event (writes) or `contracts/` port (reads) |
| **F-2** | `products/*` importing `commercial/*` | `entitlements.check()` / `.feature()` |
| **F-3** | `commercial/*` importing `products/*` | `ProductCode` strings + the manifest registry |
| **F-4** | `platform/*` importing anything above it | Events; the publisher supplies resolved content |
| **F-5** | Same-layer imports inside `platform/` | Events, or orchestration one layer up |
| **F-6** | ORM access across any module boundary | The owning module's selectors, returning DTOs |
| **F-7** | Business logic in views, serializers, admin, tasks or `interfaces/` | Application services |
| **F-8** | Django signals across modules | Explicit service call or an outbox event |

**Cross-module foreign keys** are limited to a frozen allowlist: `Organization`, `User`, `File`. Everything else is a soft reference `(product, entity, id)`. Adding a fourth requires an ADR.

## 4. Architecture fitness rules

These are enforced in CI. A PR that violates one does not merge.

### Boundaries

| # | Rule | Enforced by |
|---|---|---|
| A1 | No cross-product imports | import-linter `independence` |
| A2 | No `products → commercial` imports | import-linter `forbidden` |
| A3 | No `commercial → products` imports | import-linter `forbidden` |
| A4 | `contracts/` imports nothing internal and not Django | import-linter + bare-interpreter test |
| A5 | Layer order is respected | import-linter `layers` |
| A6 | `platform/` internal layers are respected | import-linter `layers` |
| A7 | No cross-module ORM access | AST scan |
| A8 | No cross-module FK outside the allowlist | System check `W360.E003` |
| A9 | No cross-module Django signal receivers | System check `W360.E009` |
| A10 | Only `commercial/provisioning` imports entitlement write services | import-linter `forbidden` |
| A11 | `products/` holds no code; no product package is an installed app | Fitness tests (unconditional — no exemption list) |
| A12 | No platform module branches on a product code | AST scan over `==` and `match` |
| A13 | The platform serves no product-domain route | Fitness test over the URL resolver |
| A14 | `contracts/` declares no product-domain event schema | Fitness test over the event registry |
| A15 | `platform/ecosystem` never imports `commercial/`; the catalog is reached through a `contracts/` port | import-linter `ecosystem-never-sees-commercial` |
| A16 | No product-supplied URL is ever fetched by the platform | Import scan over `platform/ecosystem` |
| A17 | Product health never reaches `/healthz/live`, `/healthz/ready` or `/healthz/startup` | Probe-registry test + a readiness test with an empty registry |
| A18 | Every `platform.integration.*` write permission is `dangerous` | Registry test |
| A19 | The issued token claim set matches the advertised one | Fitness test against the discovery document |
| A20 | `platform/launcher` sits above `platform/ecosystem` (P4), not beside it | import-linter `platform-internal-layers` |
| A21 | The launcher's product-access decision reads `platform/ecosystem` only through a narrow selector carrying no credential or redirect data | `test_no_cross_module_orm_access` (F-6) |
| A22 | No HTTP response ever serialises a product's `launch_url`; the browser POSTs a product code and the server builds the redirect | AST scan over `interfaces/` |
| A23 | The only CSRF-exempt views are the credential-authenticated ones that enumerate themselves in `CREDENTIALED_VIEWS` | AST scan over `interfaces/` |
| A24 | The platform frontend contains no file or folder named after a product | Fitness test over `frontend/src` |

### Tenancy and isolation

| # | Rule | Enforced by |
|---|---|---|
| B1 | Every model with `organization_id` inherits `TenantScopedModel` | `W360.E001` |
| B2 | Every `TenantScopedModel` has an RLS policy with `FORCE`, `USING` and `WITH CHECK` | `W360.E002` + migration-state test |
| B3 | Every index on a tenant table leads with `organization_id` | Fitness test |
| B4 | Every tenant FK to another tenant model has a composite FK | Fitness test |
| B5 | The application role is non-owner and `NOBYPASSRLS` | Startup assertion |
| B6 | Tenant context is set only via `set_config(..., true)` in a transaction | Code review + PgBouncer leakage test |
| B7 | `unscoped` manager use is allowlisted, reason-tagged and audited | AST scan + runtime audit |
| B8 | Every tenant model has an isolation test proving B cannot read A | Fitness test (test must exist) |
| B9 | Celery tasks carry tenant context unless `requires_tenant = False` | `TenantTask` + CODEOWNERS |
| B10 | Redis keys and S3 object keys are tenant-prefixed | Wrapper clients; raw client access linted |

### Identity, authorization, entitlement

| # | Rule | Enforced by |
|---|---|---|
| C1 | No product defines a user model or implements authentication | Fitness test |
| C2 | No product implements billing, pricing or subscription logic | Fitness test + A2 |
| C3 | No product bypasses `entitlements.check()` for gated capability | Entitlement test per product API |
| C4 | Permissions follow `<product>.<resource>.<action>` and are code-declared | Registry check |
| C5 | Feature keys sold in the catalog are declared by their owning product | `W360.E005` |
| C6 | Every endpoint has an authorization test per relevant persona | Generated authorization matrix |
| C7 | Checks live in application services, not only in views | Code review + task-path tests |
| C8 | No code branches on plan or role *names* | AST scan for known literals |

### Events and integration

| # | Rule | Enforced by |
|---|---|---|
| D1 | Cross-module events are versioned (`<type>.v<n>`) | Registry check `W360.E006` |
| D2 | Event schema changes are backward-compatible or bump the version | CI diff against `main` |
| D3 | Events are published via the outbox, in the business transaction | Code review + no-broker-in-transaction test |
| D4 | Event handlers are idempotent and have a duplicate-delivery test | Fitness test (test must exist) |
| D5 | Events are past-tense facts, never commands | Naming check + review |
| D6 | Publish/consume graphs have no undeclared cycles | CI graph check |
| D7 | Contract ports are read-only, nullable and batch-only | Code review |
| D8 | A product publishes only `product.<its authenticated code>.*` events, and only those its manifest declares | Ingestion checks + `test_namespace_ownership_is_structural` |
| D9 | Every outbound HTTP request goes through `integration/egress` (public addresses only, pinned, no redirects, bounded) | AST test `test_outbound_http_is_confined_to_the_egress_boundary` ([ADR-025](docs/architecture/adrs/ADR-025-outbound-http-and-ssrf-boundary.md)) |
| D10 | No external side effect inside a database transaction; automation actions create records, pipelines send them | [ADR-026](docs/architecture/adrs/ADR-026-automation-execution-semantics.md) + review |
| D11 | Pending integration work is a durable row plus a `DueWork` timer in the same transaction; the broker is only the fast path | `test_a_broker_outage_loses_no_work` ([ADR-024](docs/architecture/adrs/ADR-024-integration-platform-data-model-and-due-work.md)) |

### Resilience and concurrency

| # | Rule | Enforced by |
|---|---|---|
| E1 | Every external call has explicit connect and read timeouts | `W360.E008` |
| E2 | No external I/O inside a database transaction | Test-suite DB wrapper |
| E3 | Every Celery task declares a queue and time limits | `W360.E007` |
| E4 | Retries are bounded, jittered and never nested | Code review |
| E5 | Background jobs are idempotent where retries are possible | Code review + tests |
| E6 | `transaction.on_commit` is not used for anything that must not be lost | AST scan |
| E7 | A Tier-N dependency never fails a lower-tier operation | Degradation-matrix tests |
| E8 | List endpoints use keyset pagination with a bounded page size | Fitness test |
| E9 | Hot endpoints have query-count assertions (N+1 guard) | Fitness test |

### Data and money

| # | Rule | Enforced by |
|---|---|---|
| F1 | Money is `(integer minor units, CurrencyCode)`; never a float | Fitness test on field types and payloads |
| F2 | Financial and audit records are append-only; corrections are reversals | Schema grants + review |
| F3 | Every module implements export and purge contracts | Release checklist + contract test |
| F4 | PII fields are annotated at the schema level | Fitness test on designated models |
| F5 | Published catalog versions and prices are immutable | DB rule + application guard |

### Control plane and partner channel (Phase 7)

| # | Rule | Enforced by |
|---|---|---|
| H1 | Staff and partner principals are decided by the one `authz.check`; there is no second authorization path ([ADR-027](docs/architecture/adrs/ADR-027-control-plane-principals.md)) | Architecture tests + `test_security_matrix` |
| H2 | Control-plane code runs in an explicit scope (`organization_scope`/`acting_scope`); RLS is never relaxed, and the tables exempt from tenant scoping are exactly the five in D-019 | `test_the_exemptions_from_tenant_scoping_are_exactly_the_ones_D019_justifies` |
| H3 | A partner is a set of capabilities, never a type ([ADR-028](docs/architecture/adrs/ADR-028-partner-capability-model.md)) | `test_the_partner_model_has_no_type_column`, `test_the_partner_portal_has_no_partner_type_branch` |
| H4 | A partner reaches a customer only through a live link; a miss is 404, not 403 | Per-resource IDOR tests |
| H5 | Money is server-derived: no request carries a price, a commission amount, a rate, an approver or a status | Mass-assignment + request-flow tests |
| H6 | The commission ledger is append-only (application role and trigger); corrections are new entries; payouts move no money ([ADR-029](docs/architecture/adrs/ADR-029-commission-ledger-and-payout-model.md)) | Schema grants + trigger, verified on a fresh and an upgraded database |
| H7 | Dangerous control-plane permissions require AAL 2, and the set is pinned | `test_the_dangerous_permissions_are_exactly_the_pinned_set` |
| H8 | The frontend calls only routes that exist, reads only fields the API returns, and names only capabilities and permissions that exist | `test_every_path_the_frontend_client_requests_is_a_real_route`, `test_frontend_contract_shapes` |
| H9 | Client-supplied references are validated before the write and tested through a real `COMMIT` | `test_client_controlled_references` |

### AI, retrieval and analytics (Phase 8)

| # | Rule | Enforced by |
|---|---|---|
| I1 | AI is never an authority: `intelligence/` imports no writer of permissions, entitlements, seats, identity, organizations, staff grants or anything in `commercial/`; nothing reads model output as state | import-linter `ai-is-never-an-authority`, pinned by `test_the_ai_authority_contract_cannot_be_removed_quietly` |
| I2 | Models are offered no tools | `test_no_model_is_ever_offered_a_tool` |
| I3 | Provider keys live only in the environment from the secret manager and one request header; no column, config, log, response or prompt | `test_no_provider_secret_has_a_column`, credential tests |
| I4 | No caller supplies an AI endpoint; production pins overrides empty | `production.py` start-up check, `test_every_path_the_ai_client_requests_is_a_real_route` |
| I5 | Spend is reserved at the worst case under row locks before any provider call; a lost call is charged its reservation by a lease timer; without the database, AI fails closed | concurrency and crash tests |
| I6 | Retrieval is authorized inside the ranking statement (organization, calling product, own sources, serving version, access tags); never retrieve-then-filter | isolation tests incl. nearest-neighbour across tenants |
| I7 | No content in logs or metric labels: prompts, responses, passages, documents, embedding inputs | `test_nothing_in_intelligence_logs_content`, `test_intelligence_metric_labels_are_bounded` |
| I8 | The platform defines no product's business meaning (no comparisons to product codes, no product skills or metrics) | `test_intelligence_defines_no_product_business_meaning` |
| I9 | AI and analytics never gate readiness and never run on the critical queue | `test_ai_health_never_gates_readiness`, `test_intelligence_work_never_runs_on_the_critical_queue` |
| I10 | Analytics dimensions are enumerations; queries are fixed statements over aggregates with bounds checked first; money is never summed across currencies | analytics tests |

### Consoles, access policy and payments (Phase 8 extension)

| # | Rule | Enforced by |
|---|---|---|
| X1 | An organization's access policy (allow-list, required two-step verification, session age, password length) only ever **narrows** access: it is evaluated after membership, status and RBAC, by one pure function, in the tenancy middleware **and** the OIDC authorize path | `test_resolve_subject_refuses_what_the_policy_refuses`, `tests/extension/test_access_policy.py` |
| X2 | Security-policy, allow-list, session-revocation, member sign-out, role, refund, void, mark-paid and payment-provider permissions are dangerous: AAL 2, refused under impersonation, audited. Paying an invoice on the provider's hosted page is not (owner decision, Phase 9 - the provider authenticates the card) | `test_the_new_permissions_are_dangerous_where_they_act`, `test_security_matrix` |
| X3 | An allow-list that excludes its editor's own address is refused; staff break-glass needs `platform.staff_customer.manage`, AAL 2 and a reason | `test_the_allow_list_refuses_to_lock_out_its_editor`, `test_staff_can_break_glass_a_locked_out_organization` |
| X4 | Sessions live in the platform session registry; revoking one deletes its row (no per-request lookup); the absolute lifetime is enforced on load | `tests/extension/test_account_sessions.py` |
| X5 | Sign-in activity an organization sees is recorded *entering that organization*, in a tenant table; nobody sees a member's activity elsewhere | `test_the_dashboard_never_counts_another_tenants_activity` |
| X6 | One-time links in queued notifications travel sealed (bound to organization and notification) and are wiped after the final delivery attempt; invitation tokens are stored hashed | `test_an_invitation_link_is_never_stored_in_the_clear`, `test_seal_keys_rotate_and_a_lost_key_fails_closed` |
| X7 | Payments are hosted checkout only: no card data, no provider secret in any column, JSON, log or response; providers are configured by environment-variable reference; production pins the test provider off and endpoint overrides empty | `test_providers_are_configured_by_reference_only`, `test_provider_settings_never_expose_a_secret`, `test_production_pins_the_test_provider_off` |
| X8 | No provider call inside a database transaction; webhooks are signature-verified, de-duplicated by provider event id, settled under row locks, and a payment only lifts a suspension that dunning caused | `test_simultaneous_webhook_redeliveries_settle_once`, `test_simultaneous_pay_clicks_create_one_attempt`, `test_a_payment_never_lifts_a_suspension_staff_applied` |

### Customer journey (Phase 9)

| # | Rule | Enforced by |
|---|---|---|
| J1 | A customer's purchase is a pay-first `Order`: nothing - no subscription line, no entitlement - is written before its invoice is paid; the price is the server's, frozen at placement; only what Explore lists (public plans of listed, available products) can be ordered | `test_nothing_is_granted_until_the_order_is_paid`, `test_the_price_currency_and_status_are_the_servers`, `test_only_what_explore_lists_can_be_ordered`, `test_commercial_security` |
| J2 | At most one order per organization awaits payment (partial unique index); lock order is invoice -> order everywhere; fulfilment is after-commit, re-driven by the payments clock and runs once; paid-but-impossible and late money are refunded automatically | `test_simultaneous_orders_leave_exactly_one_awaiting_payment`, `test_redelivered_payment_events_fulfil_once`, `test_a_lost_fulfilment_is_redriven_by_the_clock`, `test_an_unpaid_order_expires_and_late_money_is_refunded` |
| J3 | A customer never chooses its billing terms: `collection_mode` and `billing_region` are set by staff (dangerous, audited) or a partner | `test_a_customer_cannot_choose_its_billing_terms` |
| J4 | A self-registered account is `pending` and cannot authenticate until the emailed link **and** its password are presented together; signup, resend and reset answer uniformly for every address | `test_verification_needs_the_link_and_the_password`, `test_a_link_clicked_by_the_wrong_person_cannot_activate_a_stranger_password`, `test_signup_for_a_registered_address_answers_the_same_and_changes_nothing` |
| J5 | Account links are 256-bit, stored as SHA-256, single use, sealed in the notification and read from the URL fragment; no credential, token or role ever travels in a query string | `test_reset_sets_the_password_signs_out_everywhere_and_is_single_use`, X6 tests |
| J6 | Explorer / Trial / Customer is derived from what a person's organizations hold (`CustomerSummary.access_class`); an identity never moves between tables | `test_buying_turns_the_same_explorer_into_a_customer` |
| J7 | Staff roles are code-declared bundles of `StaffGrant`s - no role table, no second authorization path; the last permanent security administrator cannot be revoked or disabled | `test_granting_a_role_is_dangerous_audited_and_derives_its_holders`, `test_disabling_the_last_security_administrator_is_refused` |
| J8 | No administrator credential in source, migrations, frontend, images or CI: `bootstrap_superadmin` reads `PLATFORM_SUPERADMIN_EMAIL` / `PLATFORM_SUPERADMIN_PASSWORD` from the deployment's secrets, is idempotent, never prints or resets a password, and is no back door once another administrator exists | `tests/phase9/test_super_admin.py` |
| J9 | The public site and catalog are built from the database; no product, plan or price is hard-coded in the shell, and adding a product changes no code | `test_the_catalog_is_anonymous_and_built_from_the_database`, `tests/phase9/test_product_twenty.py` |
| J10 | `User` has one writer: every flow that creates, activates, re-credentials, disables, designates or deletes an account calls `platform/identity/lifecycle.py`; Django's own `last_login` write is disconnected (the platform records `last_login_at`) | `test_only_identity_writes_the_user_table`, the write-ownership guard |
| J12 | `Organization` and `Membership` have one writer, `platform/organizations/services.py`, which locks, enforces the rows' invariants, keeps the chooser projection in the same transaction and hands back frozen records; every other module - onboarding, invitations, the member console, security settings, staff lifecycle, partner provisioning, the control-plane anchor - calls it | `test_only_the_organizations_boundary_writes_its_tables`, `test_the_ownership_scan_catches_every_way_round_the_boundary`, `tests/phase9/test_single_writer.py` |
| J13 | For the whole test session, every executed write to an owned table (`Organization`, `Membership`, the creation ledger, sign-in activity, the chooser projection, `Invitation`, `User`) must come from its owning module; a write from anywhere else fails the run | `backend/tests/write_ownership.py` (installed by the root conftest) |
| J11 | A notification is dispatched under the tenant context it was queued in, so an account email goes out at commit rather than waiting for the sweeper | `test_account_email_is_dispatched_when_the_signup_commits` |

### Code organisation

| # | Rule | Enforced by |
|---|---|---|
| G1 | No `utils.py`, `helpers.py`, `common.py`, `misc.py` | Filename check |
| G2 | No `products/shared/` module | Path check |
| G3 | `domain/` contains no Django imports | import-linter |
| G4 | Views call at most one application service | Fitness test |
| G5 | Selectors start from a `visible_*` predicate, not `.objects.all()` | Fitness test |
| G6 | No writable serializer uses `fields = "__all__"` | AST scan |
| G7 | No raw SQL built with f-strings | AST scan |

Run locally: `make lint-arch` · `make test-arch` · `python manage.py check --deploy`

## 5. The escape hatch

Rules are occasionally wrong. To add an exception:

1. Write an ADR: the edge, why, what was tried, the cost of not having it.
2. Architecture review (`CODEOWNERS`).
3. Add it **narrowly** to `.importlinter` — specific module to specific module, never a wildcard.
4. Add a dated entry to `docs/architecture/debt-register.md` with an owner and a review date.

Suppressions (`# noqa: arch`) require a ticket reference and **fail CI after 90 days**. This is deliberately more work than doing it correctly.

## 6. Where code goes

```
<module>/
  models/      ORM: structure and invariants only
  domain/      Pure Python. No Django. Fast tests.
  services/    Use cases. Own transactions. Publish events.
  selectors/   Read queries. Return DTOs. No writes.
  api/         Thin HTTP adapters
  tasks/       Thin Celery wrappers
  events/      published.py (schemas) + handlers.py
  authz.py     visible_* predicates and object rules
  manifest.py  product code, features, publishes, consumes
```

Services are `verb_noun` (`mark_deal_won`). Selectors are `noun_phrase` (`deals_for_pipeline`). The name tells you whether it writes.

## 7. Checklist before you open a PR

- [ ] Which single module **owns** this? Is it written only by that module?
- [ ] Does it cross a forbidden edge? (`make lint-arch`)
- [ ] Tenant-owned? Then `TenantScopedModel`, RLS policy, `organization_id`-leading index, composite FK, isolation test.
- [ ] Entitlement checked before permission, in the **service**, not only the view?
- [ ] New event? Versioned, schema in `contracts/`, declared in the manifest, handler idempotent with a duplicate test.
- [ ] External call? Through `integration/egress`, explicit timeouts, bounded retries, outside any transaction.
- [ ] Task? Explicit queue, time limits, idempotent, tenant context.
- [ ] Money? Integer minor units with a currency.
- [ ] List endpoint? Keyset pagination, `visible_*` predicate, query-count assertion.
- [ ] **If this module were extracted to its own service in three years, what breaks?**

That last question is the cheapest architectural test available. "Nothing — it only uses contracts and events" means the boundary is right.

## 8. Document index

| Doc | Covers |
|---|---|
| [00 System Context](docs/architecture/00-system-context.md) | Actors, external systems, trust boundaries, control vs data plane |
| [01 Domain Boundaries](docs/architecture/01-domain-boundaries.md) | Modules and what each owns |
| [02 Dependency Rules](docs/architecture/02-dependency-rules.md) | Allowed and forbidden edges, enforcement |
| [03 Multi-Tenancy](docs/architecture/03-multi-tenancy.md) | Organization model, five isolation layers, RLS |
| [04 Identity & Authz](docs/architecture/04-identity-authz.md) | Authentication, RBAC, the seven personas, break-glass |
| [05 Commercial Model](docs/architecture/05-commercial-model.md) | Catalog, pricing, bundles, partners, commissions |
| [06 Entitlements](docs/architecture/06-entitlements.md) | Subscriptions, entitlement lifecycle, seats |
| [07 Cross-Product Integration](docs/architecture/07-cross-product-integration.md) | Events, outbox, ports, idempotency |
| [08 Data Ownership](docs/architecture/08-data-ownership.md) | Ownership matrix, name-collision analysis |
| [09 Failure & Resilience](docs/architecture/09-failure-resilience.md) | Tiers, bulkheads, degradation matrix |
| [10 Performance & Concurrency](docs/architecture/10-performance-concurrency.md) | Pools, pagination, contention points, idempotency |
| [11 Observability](docs/architecture/11-observability.md) | Logs, traces, metrics, health, alerting, audit |
| [12 Security](docs/architecture/12-security.md) | Threat model, secrets, SSRF, uploads, webhooks, admin |
| [13 Deployment & Evolution](docs/architecture/13-deployment-evolution.md) | Topology, scaling path, extraction criteria |
| [14 Repository Structure](docs/architecture/14-repository-structure.md) | Folder layout, layer responsibilities, naming |
| [15 Risk Register](docs/architecture/15-risk-register.md) | P0/P1/P2 risks and open verification |
| [16 Ecosystem / Product Boundary](docs/architecture/16-ecosystem-product-boundary.md) | **What belongs in this repository and what does not** |
| [ADR-001 … ADR-020](docs/architecture/adrs/) | The twenty foundational decisions |
| [ADR-024 … ADR-026](docs/architecture/adrs/) | Phase 6: integration data model and due-work timer; the outbound HTTP/SSRF boundary; automation execution semantics |
| [ADR-027 … ADR-029](docs/architecture/adrs/) | Phase 7: staff and partner principals; the partner capability model; the commission ledger and payout model |
| [ADR-030 … ADR-032](docs/architecture/adrs/) | Phase 8: the AI gateway owns providers, spend and safety; RAG in pgvector, authorized before it ranks; product-defined metrics over routed events |
| [ADR-033 … ADR-034](docs/architecture/adrs/) | Phase 8 extension: the session registry and per-organization access policy; payments as hosted checkout behind sandboxed provider adapters |
| [ADR-035 … ADR-036](docs/architecture/adrs/) | Phase 9: pay-first orders; self-service accounts verified with link and password, staff roles as grant bundles |
| [ADR-037 … ADR-039](docs/architecture/adrs/) | Phases 10–12: partner consoles and owner activation; four-product integration and independent product trials; white-label domains keep authentication on the canonical host (D-035) |
| [ADR-040](docs/architecture/adrs/ADR-040-per-request-overhead-connections-sessions-hashing.md) | 2026-10 audit: persistent pooled connections, a throttled session idle window, bounded password hashing |
| [Phase 3 — Remote Integration](docs/phase-3/00-overview.md) | How an external product connects: registry, SSO, entitlement API, key management |
| [Phase 5 — Onboarding, access directory, MFA](docs/phase-5/00-overview.md) | Organization creation, the chooser projection, TOTP and step-up |
| [Phase 6 — Integration, webhooks, automation](docs/phase-6/00-overview.md) | Product event ingestion, signed webhooks, connectors, SSRF boundary, generic automation |
| [Phase 7 — Super Admin and the partner channel](docs/phase-7/00-overview.md) | Staff control plane, capability-based partners, reseller / white-label / affiliate foundations, commission ledger, manual payouts |
| [Phase 8 — Shared AI, retrieval and analytics](docs/phase-8/00-overview.md) | AI gateway, model registry, spend control, skills, RAG, unified analytics |
| [Phase 8 extension — consoles, access policy, communication, payments](docs/phase-8/extension/00-design.md) | Organization console and dashboard, Super Admin panel and plan builder, sessions, sign-in policy, email templates, invoices and hosted checkout |
| [Phase 9 — The customer journey](docs/phase-9/00-plan.md) | Public site and catalog, signup and verification, dashboard, pay-first orders, Super Admin roles and bootstrap ([operations](docs/phase-9/01-operations.md), [status](docs/phase-9/phase-9-status.md)) |
| [Phase 12 — Production hardening and go-live readiness](docs/phase-12/phase-12-status.md) | The go-live verdict and its blockers. Deployment topology and checklist, backup/restore/PITR, secrets and key rotation, async safety, load test, observability and alerts, runbooks, security scans ([gap list](docs/phase-12/01-audit-gap-list.md)) |
| [2026-10 architecture and performance audit](docs/audit-2026-10/architecture-performance-audit.md) | Measured per-request overhead and its fixes, before/after load tests, remaining risks |
| [Product Integration Guide](docs/phase-3/09-product-integration-guide.md) | **Start here if you are building a product** (AI, RAG and analytics: [Phase 8 guide](docs/phase-8/10-product-integration-guide.md)) |
