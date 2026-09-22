# 08 — Data Ownership

> **CORRECTION (2026-09-22, [ADR-017](adrs/ADR-017-products-are-separate-applications.md)).**
> This document was written when product domains were to live *inside* this
> repository, under `products/`. They no longer do: each business product is
> a separate application in its own repository. Everything here about
> layering, ownership, isolation and enforcement still holds — but wherever
> it places a product's code in this repository, read
> [16 — Ecosystem / Product Boundary](16-ecosystem-product-boundary.md)
> instead.


**Status:** Accepted (Phase 0)
**Owner:** Architecture
**Last reviewed:** 2026-09-21

---

## 1. Purpose

For every major entity: who owns it, who may read it, who may modify it, what it publishes, and what it consumes. Ownership disputes are the root cause of most modular-monolith decay, so they are settled here in writing rather than discovered in a code review two years from now.

## 2. Ownership rules

1. **Exactly one module owns each entity.** The owner defines the schema, enforces the invariants, and is the only module that writes it.
2. **Reads cross boundaries only through selectors, ports or events** — never through another module's ORM ([02-dependency-rules.md](02-dependency-rules.md), F-6).
3. **Writes never cross boundaries.** To change another module's data, publish an event and let the owner decide, or call the owner's application service where the layer permits it.
4. **Similar names are not shared entities.** Two modules using the word "Invoice" is not evidence that they need one table. See §4.
5. **When in doubt, duplicate.** A duplicated field with a clear owner is cheaper to fix than a shared table with two owners.

## 3. The matrix

Legend — **Read:** who may read, and how. **Write:** who may write. `S` = via selector, `P` = via contract port, `E` = via event, `D` = direct (same module only).

### 3.1 Platform — identity, tenancy, access

| Entity | Owner | Read | Write | Publishes | Consumes |
|---|---|---|---|---|---|
| `User` | `platform/identity` | identity `D`; others `S` (never raw) | identity only | `identity.user.registered.v1`, `.email_verified.v1`, `.disabled.v1`, `.password_changed.v1` | `organizations.membership.removed.v1` (cleanup) |
| `Session` | `platform/identity` | identity `D` | identity only | `identity.session.started.v1`, `.revoked.v1` | `organizations.organization.suspended.v1` |
| `AuthFactor` | `platform/identity` | identity `D`; admin UI via `S` | identity only | `identity.mfa.enrolled.v1`, `.removed.v1` | — |
| `IdentityProvider` | `platform/identity` | identity `D`; org admins `S` | identity only | `identity.sso.configured.v1` | — |
| `ImpersonationGrant` | `platform/identity` | identity `D`; audit `S`; **customer-visible** | identity only | `identity.impersonation.started.v1`, `.ended.v1` | — |
| `Organization` | `platform/organizations` | all modules `S` | organizations only | `organizations.organization.created.v1`, `.suspended.v1`, `.reactivated.v1`, `.purged.v1` | `entitlements.status.changed.v1` |
| `Membership` | `platform/organizations` | authz `S`, entitlements `S`, products `S` | organizations only | `organizations.membership.added.v1`, `.removed.v1`, `.suspended.v1` | `identity.user.disabled.v1` |
| `Invitation` | `platform/organizations` | organizations `D` | organizations only | `organizations.invitation.sent.v1`, `.accepted.v1` | — |
| `Permission` | `platform/authz` | all `S` (registry) | code-declared; synced at deploy | — | — |
| `Role` | `platform/authz` | all `S` | authz only | `authz.role.created.v1`, `.permissions_changed.v1` | — |
| `RoleAssignment` | `platform/authz` | authz `D`; audit `S` | authz only | `authz.assignment.granted.v1`, `.revoked.v1`, `.expired.v1` | `organizations.membership.removed.v1` |
| `Entitlement` | `platform/entitlements` | all products `P` (`check()`) | **`commercial/provisioning` only** | `entitlements.product.granted.v1`, `.revoked.v1`, `.status.changed.v1` | — |
| `FeatureEntitlement` | `platform/entitlements` | all products `P` (`feature()`) | provisioning only | `entitlements.feature.changed.v1` | — |
| `SeatGrant` / `SeatAssignment` | `platform/entitlements` | products `P`; org admin `S` | entitlements (assignment), provisioning (grant) | `entitlements.seat.assigned.v1`, `.released.v1` | `organizations.membership.removed.v1` |
| `EntitlementGrant` | `platform/entitlements` | entitlements `D`; support UI `S` | staff via service (audited) | `entitlements.grant.issued.v1`, `.revoked.v1` | — |
| `AuditEvent` | `platform/audit` | append-only; org admins `S`, staff `S` | **all modules via the audit service; nobody updates or deletes** | — | every module's events |
| `File` | `platform/files` | owner module `S`; content via presigned URL | files only | `files.file.uploaded.v1`, `.scanned.v1`, `.deleted.v1` | — |
| `NotificationPreference` | `platform/notifications` | notifications `D`; user settings `S` | notifications only | — | — |
| `FeatureFlag` | `platform/feature_flags` | all `S` | engineering only | — | — |
| `WorkflowDefinition` / `WorkflowRun` | `platform/workflows` | workflows `D`; org admin `S` | workflows only | `workflows.run.completed.v1`, `.failed.v1` | all domain events (as triggers) |

### 3.2 Commercial

| Entity | Owner | Read | Write | Publishes | Consumes |
|---|---|---|---|---|---|
| `Product` (SKU) | `commercial/catalog` | commercial `D`; public pricing page `S` | staff only | `catalog.product.published.v1`, `.retired.v1` | — |
| `Feature` | `commercial/catalog` | commercial `D` | synced from product manifests | `catalog.feature.registered.v1` | — |
| `Plan` / `PlanVersion` / `PlanFeature` | `commercial/catalog` | commercial `D`; pricing page `S` | staff only; **immutable once published** | `catalog.plan_version.published.v1` | — |
| `Bundle` / `BundleVersion` / `BundleItem` | `commercial/catalog` | commercial `D` | staff; partner for own custom bundles | `catalog.bundle.published.v1` | — |
| `PriceBook` / `PlanPrice` / `PriceTier` | `commercial/pricing` | pricing `D`; others via `resolve_price()` | staff; partner for own books | `pricing.price_book.published.v1` | — |
| `ContractPrice` | `commercial/pricing` | pricing `D` (org-scoped, RLS) | staff with approver | `pricing.contract_price.agreed.v1` | — |
| `Subscription` / `SubscriptionItem` | `commercial/subscriptions` | commercial `D`; customer `S`; partner `S` (if managed) | subscriptions only | `subscriptions.created.v1`, `.updated.v1`, `.cancelled.v1`, `.renewed.v1`, `.status_changed.v1` | `billing.payment.succeeded.v1`, `.failed.v1` |
| `BillingInvoice` / `BillingInvoiceLine` | `commercial/billing` | billing `D`; customer `S`; partner `S` | billing only | `billing.invoice.issued.v1`, `.paid.v1`, `.overdue.v1`, `.voided.v1` | `subscriptions.renewed.v1` |
| `Payment` / `Refund` / `Credit` | `commercial/billing` | billing `D`; customer `S` | billing only | `billing.payment.succeeded.v1`, `.failed.v1`, `billing.refund.issued.v1` | provider webhooks |
| `DunningRun` | `commercial/billing` | billing `D` | billing only | `billing.dunning.escalated.v1`, `.exhausted.v1` | `billing.payment.failed.v1` |
| `PartnerAccount` / `PartnerCapability` | `commercial/partners` | partners `D`; edge branding resolver `S` | staff; partner for own profile | `partners.account.activated.v1`, `.capability_granted.v1`, `.suspended.v1` | — |
| `PartnerRelationship` | `commercial/partners` | partners `D`; subscriptions `S`; customer `S` | partners only | `partners.relationship.started.v1`, `.ended.v1` | `subscriptions.created.v1` |
| `Brand` / `CustomDomain` | `commercial/partners` | edge resolver `S`; partner `D` | partner (own), staff (verification) | `partners.domain.verified.v1`, `.revoked.v1` | — |
| `DataAccessGrant` | `commercial/partners` | partners `D`; customer `S`; authz `S` | **customer org only** | `partners.data_access.granted.v1`, `.revoked.v1` | `partners.relationship.ended.v1` |
| `AttributionRecord` | `commercial/commissions` | commissions `D`; affiliate `S` (aggregate) | commissions only | `commissions.attribution.recorded.v1`, `.converted.v1` | `organizations.organization.created.v1` |
| `CommissionRule` | `commercial/commissions` | commissions `D`; partner `S` | staff only | `commissions.rule.published.v1` | — |
| `CommissionEntry` | `commercial/commissions` | commissions `D`; partner `S` | commissions only; **append-only** | `commissions.earned.v1`, `.reversed.v1` | `billing.invoice.paid.v1`, `billing.refund.issued.v1` |
| `Payout` | `commercial/commissions` | commissions `D`; partner `S` | commissions only | `commissions.payout.approved.v1`, `.paid.v1`, `.failed.v1` | — |

### 3.3 Products (illustrative — structure, not a feature spec)

| Entity | Owner | Read | Write | Publishes | Consumes |
|---|---|---|---|---|---|
| `Contact` | `products/crm` | crm `D`; others `P` (`ContactDirectory`) | crm only | `crm.contact.created.v1`, `.updated.v1`, `.merged.v1`, `.deleted.v1` | `marketing.subscriber.unsubscribed.v1` |
| `Company` | `products/crm` | crm `D`; others `P` (`CompanyDirectory`) | crm only | `crm.company.created.v1`, `.updated.v1` | — |
| `Lead` / `Deal` / `Pipeline` | `products/crm` | crm `D` | crm only | `crm.deal.won.v1`, `.lost.v1`, `.stage_changed.v1` | `books.invoice.paid.v1` (deal health) |
| `Account` (chart of accounts) | `products/books` | books `D` | books only | `books.account.created.v1` | — |
| `JournalEntry` / `LedgerLine` | `products/books` | books `D` | books only; **append-only, reversal not edit** | `books.journal.posted.v1`, `.reversed.v1` | `inventory.stock.consumed.v1`, `payroll.payrun.finalised.v1` |
| `Invoice` (customer's AR) | `products/books` | books `D`; commerce `P` | books only | `books.invoice.issued.v1`, `.paid.v1`, `.overdue.v1` | `crm.deal.won.v1`, `commerce.order.placed.v1` |
| `Item` (SKU) | `products/inventory` | inventory `D`; others `P` (`ItemCatalog`) | inventory only | `inventory.item.created.v1`, `.discontinued.v1` | — |
| `StockLevel` / `StockMovement` | `products/inventory` | inventory `D` | inventory only; movements append-only | `inventory.stock.moved.v1`, `.depleted.v1`, `.reserved.v1` | `commerce.order.placed.v1`, `crm.deal.won.v1` |
| `Project` / `Task` / `Timesheet` | `products/projects` | projects `D` | projects only | `projects.project.created.v1`, `.completed.v1`, `projects.time.logged.v1` | `crm.deal.won.v1` |
| `Ticket` / `SLA` | `products/support` | support `D` | support only | `support.ticket.opened.v1`, `.resolved.v1`, `.sla_breached.v1` | `crm.contact.created.v1` |
| `Employee` | `products/hr` | hr `D`; payroll `P` (`EmployeeDirectory`) | hr only | `hr.employee.hired.v1`, `.terminated.v1` | `identity.user.disabled.v1` |
| `PayRun` / `PaySlip` | `products/payroll` | payroll `D` | payroll only | `payroll.payrun.finalised.v1` | `hr.employee.hired.v1`, `projects.time.logged.v1` |
| `Campaign` / `Audience` | `products/marketing` | marketing `D` | marketing only | `marketing.campaign.sent.v1`, `marketing.subscriber.unsubscribed.v1` | `crm.contact.created.v1` |
| `Order` / `Fulfilment` | `products/commerce` | commerce `D` | commerce only | `commerce.order.placed.v1`, `.fulfilled.v1`, `.refunded.v1` | `inventory.stock.depleted.v1` |

## 4. Shared concepts: the collision analysis

The brief asks for particular attention to `Customer`, `Contact`, `Organization`, `Address`, `Currency`, `Product`, `User`. Each is a word that means different things in different modules. Unifying them because they share a name is the most common way a modular monolith becomes a distributed ball of mud.

### 4.1 "Product" — three unrelated things

| Term | Module | Meaning | Lifecycle |
|---|---|---|---|
| `catalog.Product` | `commercial/catalog` | A Workeasy360 SKU we sell | Changed by us, quarterly |
| `inventory.Item` | `products/inventory` | A physical SKU the *customer* stocks | Changed by the customer, hourly |
| `commerce.ListingProduct` | `products/commerce` | A sellable listing in the customer's storefront | Customer, with channel mappings |

**Decision: three separate models. No shared base, no shared table.** They share only the English word. They have different owners, different lifecycles, different tenancy (one is control plane, two are data plane), and different fields. A unified `Product` would need a discriminator, nullable columns for every variant, and an owner nobody could name.

Naming convention to prevent drift: the commercial one is always referred to as a **SKU** or `catalog.Product` in code and conversation; inventory's is always `Item`.

### 4.2 "Invoice" — two opposite directions of money

| Term | Module | Direction | Numbering | Legal |
|---|---|---|---|---|
| `billing.BillingInvoice` | `commercial/billing` | Customer owes **us** | Our sequence, our jurisdiction | Our tax obligation |
| `books.Invoice` | `products/books` | Customer's customer owes **them** | The customer's sequence and format | Their tax obligation |

**Decision: separate, and never related.** They differ in numbering rules, tax jurisdiction, statutory retention, currency, and who may void them. A customer who uses Books to invoice their clients must see *their* invoices; ours appear only in billing settings. Sharing a table would mean one organization's accounts-receivable ledger contains our revenue records — a correctness and data-protection failure.

The same applies to `billing.Payment` (money to us) versus `books.Payment` (money to them).

### 4.3 "Customer" — deliberately not a platform entity

This one is the biggest trap, because "customer" feels universal.

| Meaning | Where it lives |
|---|---|
| A business that buys Workeasy360 | `platform.organizations.Organization` |
| A person the customer sells to | `products/crm.Contact` |
| A business the customer sells to | `products/crm.Company` |
| A party owing money in the ledger | `products/books` — an AR sub-ledger keyed by a soft ref |
| A buyer in the storefront | `products/commerce.Customer` |

**Decision: there is no `platform.Customer` and there never will be.** A platform-level `Customer` would be written by CRM, Books and Commerce simultaneously — three owners, which rule 1 forbids — and it would mean an organization using only Books is forced to carry CRM's contact model.

`crm.Contact` is the **system of record for people the customer deals with**, but only for organizations entitled to CRM. Books does not require CRM: it keeps its own minimal AR party record and *optionally* links to a CRM contact via a soft ref when CRM is present. Books has fewer fields, its own validation (tax ID mandatory, email optional — the reverse of CRM), and works standalone.

That is duplication, and it is the correct trade: the alternative is that Books cannot be sold without CRM, which contradicts the product vision.

Deduplication is handled as a **feature**, not as a schema constraint: when both products are entitled, a matching service proposes links and the user confirms. Merging is a CRM operation that publishes `crm.contact.merged.v1`; consumers update their soft refs.

### 4.4 "Address" — a value object, not a table

**Decision: `contracts.values.PostalAddress` is a shared *type*; there is no shared `Address` table.**

```python
@dataclass(frozen=True)
class PostalAddress:
    line1: str
    line2: str | None
    city: str
    region: str | None
    postal_code: str | None
    country: CountryCode        # ISO 3166-1 alpha-2
    def formatted(self, style: AddressStyle = AddressStyle.POSTAL) -> str: ...
```

Each owner embeds it as columns or JSONB:

| Address | Owner | Why not shared |
|---|---|---|
| Organization billing address | `platform/organizations` | Legal/tax significance; changes trigger tax recalculation |
| Contact address | `products/crm` | Free-form, may be incomplete, many per contact |
| Warehouse address | `products/inventory` | Operational; needs geocoding and dock notes |
| Shipping address | `products/commerce` | Per-order snapshot — **must not change when the contact changes** |
| Employee address | `products/hr` | Sensitive personal data; separate retention and access rules |

The last row is the decisive argument. A shipping address on a completed order is a **historical fact**. If it pointed at a shared, mutable address row, editing a contact's address would retroactively rewrite where a parcel was sent last year. Orders snapshot the value; that is not duplication, it is correctness.

A shared validation/formatting library is fine and encouraged. A shared table is not.

### 4.5 "Currency" — reference data with an owner

**Decision: `contracts.values.CurrencyCode` (ISO 4217 enum) plus a `platform/reference` table for metadata** (minor-unit exponent, symbol, display rules). Exchange rates are **not** reference data: they are time-series, sourced externally, and owned by `products/books` (which needs them for revaluation) and by `commercial/billing` (which needs them for settlement) — with different requirements, so each keeps its own rate table sourced from the same connector.

Non-negotiable: money is always `(integer minor units, CurrencyCode)`. `Money` refuses cross-currency arithmetic. A bare `Decimal` amount in any API, event payload or column is a review rejection.

### 4.6 "User" vs "Member" vs "Employee" vs "Contact"

Four distinct concepts, routinely conflated:

| Concept | Module | Is | Is not |
|---|---|---|---|
| `identity.User` | platform | A login identity, global across orgs | An employee; a person in the business sense |
| `organizations.Membership` | platform | A user's participation in one org | An employment record |
| `hr.Employee` | products/hr | An employment record, with or without a login | A login |
| `crm.Contact` | products/crm | A person outside the business | Anything with access |

A contractor may be an `Employee` with no `User`. A consultant may be a `User` and `Membership` with no `Employee`. An `Employee` who leaves has their `Membership` removed but their employment record retained for statutory years. **Merging any two of these breaks a real case in the first month of operation.** HR links to identity by an optional soft ref, never by requiring one.

### 4.7 Summary rule

> Two entities may share a table only if they share an **owner**, a **lifecycle**, and a **set of invariants**. Sharing a name is not evidence of any of the three.

## 5. Read patterns across boundaries

| Need | Mechanism | Cost | Staleness |
|---|---|---|---|
| Render another product's label | Contract port, batched | One in-process call | None |
| React to a change | Domain event | Async | Eventual |
| Report across products | Analytics read model built from events | Async | Eventual, monitored |
| Validate against another product | Contract port, nullable | In-process | None |
| Aggregate for a dashboard | Per-module selector, composed at `interfaces/` | N calls | None |

**Cross-product reporting deserves a specific decision.** "Revenue by CRM campaign" needs CRM and Books together. Joining their tables is forbidden. The answer is an **analytics read model** in `platform/analytics`, populated from domain events, denormalised for query, and explicitly eventually consistent. It is a *consumer* of both products and depends on neither's schema. If analytics is down, products are unaffected — the strict inverse of a shared-join design.

## 6. Data retention and deletion ownership

| Data | Owner | Retention | On org purge |
|---|---|---|---|
| Tenant business data | Each product | Customer-configurable, default indefinite | Deleted |
| `AuditEvent` | `platform/audit` | 7 years | **Retained** (tombstoned) |
| `BillingInvoice`, `Payment` | `commercial/billing` | Statutory (7–10y by jurisdiction) | **Retained** |
| `CommissionEntry` | `commercial/commissions` | 7 years | **Retained** |
| Session, token | `platform/identity` | 90 days post-expiry | Deleted |
| Files | `platform/files` | With the owning record | Deleted (S3 prefix) |
| Event outbox | `integration/events` | 30 days | Deleted |
| Analytics read models | `platform/analytics` | 2 years | Anonymised |

**Every module must implement export and purge contracts.** A module that cannot export its data or delete it on request is not releasable — data portability and erasure are legal obligations in our target markets, and retrofitting them across a dozen products is far more expensive than requiring them per module from the start.

Purge ordering is derived from the module dependency graph and executed as a resumable workflow with per-module checkpoints ([03-multi-tenancy.md](03-multi-tenancy.md)).

## 7. Related documents

- [01-domain-boundaries.md](01-domain-boundaries.md) — module responsibilities
- [02-dependency-rules.md](02-dependency-rules.md) — F-6, cross-module ORM access
- [07-cross-product-integration.md](07-cross-product-integration.md) — events and ports
- [03-multi-tenancy.md](03-multi-tenancy.md) — tenant lifecycle and purge
