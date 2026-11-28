# ADR-078: Self-service product activation (trials, purchases, free plans), the trial policy and default product roles

Date: 2026-11-28. Status: proposed; branch `feat/auto-activation`, cut from `main` at `eef05dd`; the owner approved the verification report on 2026-11-28; committed and opened as a pull request to `main`, **not deployed**. Builds on:
- ADR-038 (per-product trials, Phase 11)
- ADR-043 (portals)
- ADR-044 (product SSO)
- ADR-065 (the Store's own host)
- ADR-071 (Accounts hosts identity)
- ADR-073 (from a verified purchase to using the product)

Record: `docs/auto-trial/status.md`. Product-side changes: `docs/auto-trial/product-roles.md`.

**Numbering.** The first round was written as "ADR-069" on the stale `feat/partner-admin-panel`, but `main` already uses 069 to 077 (069 is product back-channel logout). This ADR was renumbered when the work moved onto `main`.

## Context

There were two owner briefs on 28 November 2026.
- **"Automatic Free Trial Activation & Product Access"** asks for: Signup → Start Free Trial → Open Product → Start Working.
- **The approved decisions that followed** extend it in four ways:
  - direct paid purchases activate by themselves;
  - Super Admin configures the trial's eligibility and repeat rules;
  - there is a central default product-role policy;
  - one activation path covers trials, purchases, trial-to-paid, renewals and future products.

They also ask that the changes each product needs be documented.

Root causes found by the audit (`status.md` section 1):
1. A customer without an organization could not trial or buy; the checkout sent them to User Admin.
2. The trial starter's seat failed silently when provisioning had not created the pool yet.
3. The Store did not know what the signed-in customer held.
4. A failed setup could be neither seen nor retried.
5. A trial could start on a product with nowhere to open.
6. Before ADR-073, a paid order seated nobody. `main` fixed that for **paid** orders; zero-amount orders (free plans) still seated nobody.
7. The trial rule was fixed in code and in a unique key ("once per organization and product").
8. Products chose roles on their own, and the platform sent no role.

## Decision

### 1. One activation path

`commercial/provisioning/activation.py` names no product. It composes services that authorise themselves:
- `trials.start_product_trial`
- the commercial-change notification (the provisioning listener reconciles from durable state)
- seat assignment
- `launcher.access.can_launch_product`, the decision the launch endpoint re-runs before the browser leaves.

| Activation | What makes it the organization's | Who gets a seat |
|---|---|---|
| Free trial | `start_product_trial` | the starter (`trials.seat_trial_starter`) |
| Paid purchase | `orders.fulfil` after the verified payment (ADR-073) | the buyer (`orders.seat_the_buyer`) |
| Free plan, plan-level trial (zero-amount order) | `orders._place_fulfilled` | **the buyer, now** (`orders.seat_the_buyer`, same rules) |
| Trial → paid | a paid order; the trial is settled as `converted` | the seat stays (assignments are per pool) |
| Renewal | the period advances; nothing is re-activated | the seat stays |
| A future product | being registered, priced and given a trial; no code | as above |

In every case, only that one person is seated, only while an active member, and never beyond the pool. Everyone else gets a seat from someone with seat rights.

### 2. One action per product, decided on the server

`GET /orgs/<slug>/product-access` returns one of `open`, `start_trial`, `buy`, `upgrade`, `setting_up`, `retry_setup`, `ask_owner`, `unavailable` or `contact_sales`, with a reason. `open` is returned only when the launch decision is `active`. The page never infers an action.

The Store's product cards, its product page and the trial confirmation render that answer.

### 3. Start Free Trial, Retry Setup and the workspace

- `POST /orgs/<slug>/product-access/<code>/trial` starts the trial, provisions it, seats the starter, then reads the access afresh.
  - It is idempotent.
  - Another tab finds the running trial and only repairs it.
  - A product with nowhere to open is refused (`product_not_ready`).
- `POST .../repair` (Retry Setup) notifies the change again and seats the person where they may be seated.
- `POST /api/v1/organizations/workspace` (`onboarding.ensure_workspace`) returns the person's one organization, or creates one, with them as owner.
  - Their account row is locked, so at most one is ever created.
  - With several organizations it answers `organization_required`.
- The Store checkout creates the workspace itself: in Start Free Trial for a trial, and with one click for a purchase.
- Entitlement decisions taken after a write use a fresh request memo (`entitlement_request_scope`).

### 4. The trial policy (Super Admin)

`ProductTrialConfig` gains `repeat_policy`, `repeat_after_days`, `eligibility` and `one_trial_per_person` (migration `catalog.0016`, database defaults). The defaults are today's rule.

| Setting | Values | Default |
|---|---|---|
| Repeat trials | `never` (once per organization and product) or `after_cooldown` + 1-730 days after the previous trial stopped granting | `never` |
| Who may start | `all_organizations` or `new_customers` (an organization that never paid for the product) | `all_organizations` |
| One trial per person | across all the person's organizations, so a new workspace is not a new trial | off |

- `trials.trial_policy_refusal` decides, and Explore asks it too. The refusal reasons are `already_used`, `repeat_too_soon` (with `available_from`), `not_eligible` and `already_used_elsewhere`.
- The database unique key becomes **one running trial per organization and product** (`uq_product_trial_active`, migration `subscriptions.0007`). Two at once stays impossible whatever a race does.
- A repeat first settles an expired trial the sweeper has not marked yet.
- "One per person" reads the person's other organizations from the access directory, one organization scope at a time. Row-level security is never bypassed.
- Super Admin edits it on Product Manager → Availability → Trial. A caller that names no policy (the Product Publisher, `register_products`) keeps what is stored.

### 5. The default product-role policy

`Product.role_policy` (migration `catalog.0016`) maps the organization's roles (`owner`, `admin`, `billing_manager`, `member`, `readonly`) to a **product role**.
- **Platform default:** owner → admin, admin → admin, billing_manager → member, member → member, readonly → viewer.
- **Several roles:** the strongest organization role decides.
- **Validation:** a product role is a lower-case identifier, and the policy refuses any name with super, platform, staff, root, system, w360 or workeasy in it.
- **Where it is set:** Super Admin edits it on Product Manager → Availability → Default product roles (`GET`/`PUT /admin/catalog/products/<id>/role-policy`, audited).
- **Delivered at sign-in:** under the `organization` scope, the ID token carries `product_role` and `organization_roles`. Each product maps `product_role` to its own role when it creates the membership. `organization_roles` is informational only.
- **Never sent:** no staff right, and nothing for a person who is not an active member.
- **What each product must change:** `docs/auto-trial/product-roles.md`.

## Not changed, deliberately

- **Platform staff rights.** A customer only ever becomes owner of their own organization.
- **Product role re-sync.** The minimal product change applies the role when the membership is created. Re-applying it at every sign-in is an owner decision (`product-roles.md`, section 2).
- **Other members' seats.** The organization's own seat rules still decide them.
- **The older `/orgs/<slug>/trials` endpoint.** It is unchanged, except that it applies the trial policy.

## Consequences

- Retry Setup also runs the other commercial-change listeners. They are idempotent.
- A10 (single entitlement writer) holds: activation reaches provisioning only through the notification hook. Import-linter keeps 18 of 18.
- Two migrations (`catalog.0016`, `subscriptions.0007`). Both are additive, with database defaults and no data rewrite. They are reversible: re-adding `uq_product_trial_once` needs no duplicate rows, which only exist after a repeat trial has run.
