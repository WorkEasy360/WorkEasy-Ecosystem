# Self-service product activation: audit, changes, verification

Briefs:
- "WorkEasy360 - Automatic Free Trial Activation & Product Access" (28 Nov 2026)
- the approved decisions that followed on the same day: paid activation, trial policy, default roles, universal activation, complete testing, release readiness

| | |
|---|---|
| Decisions | [ADR-078](../architecture/adrs/ADR-078-automatic-free-trial-activation-and-product-access.md) |
| Changes each product needs | [product-roles.md](product-roles.md) |
| Branch | `feat/auto-activation`, from `main` `eef05dd`, worktree `~/w360wt/auto-activation` |
| State | Committed and pushed for review (pull request to `main`) on 2026-11-28, after the owner approved the verification report. **Not deployed.** The development database is untouched. |

## 0. Base branch

The first round was built on `feat/partner-admin-panel`, which is 128 commits behind `main`. It was moved onto `main`.

`main` already had ADR-073, which seats the buyer of a **paid** order (`orders.seat_the_buyer`). That is kept, and my duplicate was dropped. The ADR became 078, because 069 to 077 were taken. The only conflict was `orders.py`.

## 1. Audit: root causes

| # | Root cause | Fixed by |
|---|---|---|
| RC-1 | No organization: the checkout sent the customer to User Admin | workspace created automatically (`ensure_workspace`) |
| RC-2 | The trial starter's seat failed silently before the pool existed | seat outcome reported; Retry Setup seats them |
| RC-3 | The Store did not know what the customer held | server-decided action per product (`product-access`) |
| RC-4 | A failed setup could not be seen or retried | Setting Up / Retry Setup |
| RC-5 | A trial could start on a product with nowhere to open | refused (`product_not_ready`); the card says Buy or Not Available Yet |
| RC-6 | A zero-amount order (free plan) seated nobody; ADR-073 covered paid orders only | `seat_the_buyer` now also runs for zero-amount orders |
| RC-7 | The trial rule was fixed in code and in a unique key | configurable trial policy, and a "one running trial" key |
| RC-8 | No central role: each product picked its own default | default product-role policy, sent at sign-in |

There is no Super Admin approval anywhere on the path. `CustomerSetup` is informational and never gates launch.

## 2. Changes

**Backend**
- `commercial/provisioning/activation.py` (new):
  - `product_access`, `activate_trial`, `repair_access`, `apply_failure`
- `commercial/subscriptions/trials.py`:
  - `trial_policy_refusal` and `seat_trial_starter`
  - the starter's seat outcome, and one running trial per organization and product
- `commercial/subscriptions/access_seats.py` (new): the trial's seat with its outcome.
- `commercial/subscriptions/selectors.py`:
  - `trial_history`, `has_paid_for`, `person_trialled_elsewhere`
  - `ProductTrialDTO.started_by_user_id`
- `commercial/subscriptions/marketplace.py`: Explore offers the trial again once the policy allows it.
- `commercial/payments/orders.py`: a zero-amount order seats its buyer, using ADR-073's `seat_the_buyer`.
- `commercial/catalog/*`:
  - the trial policy: fields, validation, service, reads
  - `Product.role_policy` and `set_product_role_policy`
  - `product_role_for` and `role_policy_row`
- `platform/onboarding/services.py`: `ensure_workspace` and `default_workspace_name`.
- `interfaces/api/v1/*`:
  - the routes below
  - ID-token role claims (`ecosystem_views._role_claims`)
  - the Store host's access to product access and launch (`portal_surface`)

**Database** (two additive migrations, database defaults, no data rewritten)
- `catalog.0016_trial_and_role_policy`:
  - `ProductTrialConfig.repeat_policy`, `repeat_after_days`, `eligibility`, `one_trial_per_person`, plus two checks
  - `Product.role_policy`
- `subscriptions.0007_trial_policy`: the unique key `uq_product_trial_once` becomes `uq_product_trial_active` (one running trial per organization and product).

**API**

| Route | What |
|---|---|
| `GET /api/v1/orgs/<slug>/product-access` | each product's action: open, start_trial, buy, upgrade, setting_up, retry_setup, ask_owner, unavailable, contact_sales |
| `POST /api/v1/orgs/<slug>/product-access/<code>/trial` | Start Free Trial (Idempotency-Key, optional `plan_code`) |
| `POST /api/v1/orgs/<slug>/product-access/<code>/repair` | Retry Setup |
| `POST /api/v1/organizations/workspace` | the person's workspace, created when they have none |
| `GET`/`PUT /api/v1/admin/catalog/products/<id>/role-policy` | default product roles (staff) |
| `PUT /api/v1/admin/catalog/products/<id>/trial` | gains `repeat_policy`, `repeat_after_days`, `eligibility`, `one_trial_per_person` (absent = kept) |
| ID token, `organization` scope | gains `product_role` and `organization_roles` |

**Frontend**
- **Store:**
  - `features/store/productAccess.tsx` (provider, `AccessActions`) and `lib/api/productAccess.ts`
  - product cards, the product page's buy box, the checkout (workspace) and the trial confirmation
  - "For <organization>" when the person belongs to several
- **Super Admin, Product Manager → Availability:**
  - the trial policy fields
  - the "Default product roles" editor

## 3. Verification

Run locally on 2026-11-28, on `feat/auto-activation` (`main` `eef05dd` + this work).

| Check | Result |
|---|---|
| New backend tests (`tests/auto_trial`, 42) + `main`'s trial and launch-ready tests | **90 / 90** |
| **Full backend suite** (6,292, bare `pytest`, 3 h 30 m on a loaded machine) | **6,289 passed, 3 failed** - all three concurrency tests in code this work does not touch (below) |
| **Full frontend suite** (vitest) | **2,063 / 2,063** (112 files) |
| tsc / eslint | 0 errors / 0 errors |
| ruff check, ruff format, mypy, `makemigrations --check` | clean |
| import-linter | **18 kept, 0 broken** |
| **Real browser: regression** (`Accounts/e2e/run_universal_acceptance.py` on this checkout: Accounts sign-in, chooser, checkout purchase of Easy CRM, product entry, new product setup) | **56 / 56** |
| **Real browser: activation journeys** (`scripts/auto_activation_check`) | **40 / 40** |

**The three backend failures:**
- `phase10/test_10e_owner_activation::test_the_console_shows_each_state_and_never_the_link`: `'pending' == 'activated'`. Passes alone, and its module passes 50/50.
- `integration/test_phase6_concurrency::test_concurrent_replays_of_one_dead_delivery_create_one`: 6 of 7 conflicts. Passes alone.
- `phase8/test_concurrency::test_racing_versions_leave_only_the_newest_serving`: a Postgres deadlock among its 8 threads. Its module cannot start outside a full run on this machine (its real-commit fixtures need the session database), so **it is to be confirmed by CI**.

No file these three cover is changed here. The machine was shared and heavily loaded (Docker at most of the CPU).

**The real-browser journeys** ran in Chrome against the production frontend build, behind the production edge over https, with Easy CRM's real Django backend and Next.js frontend:

| Phase | What it proves |
|---|---|
| trial (9 checks) | A person with **no organization**, on the product page, sees Start 14-day Free Trial. One click gives **Open Product**: workspace, trial, provisioning and seat done. It shows "14 days left" and no technical words. Open Product **enters Easy CRM with no product password**, and Easy CRM's own API answers for that person. The Store card then says Open Product. axe clean. |
| trial-second (3) | A second product's trial from its Store card, in the same workspace, opens with no password. |
| paid (7) | A person with **no organization**: Buy Now, then the checkout, with no detour to User Admin. One click creates the workspace. Then billing, the server's quote, Pay, the payment provider's page, and the confirmation. The product page says **Open Product, with no seat assigned by hand**, and enters Easy CRM as the buyer. |
| concurrent (1) | Two tabs press Start Free Trial in the same instant: both end at Open Product. |
| existing (4) | An existing customer in two organizations: the page names the one it is for ("For Acme Traders"). Their paid product shows Open Product (not a trial) and opens. An organization that bought nothing is offered the trial. |
| admin (3) | Super Admin's Availability tab shows the trial policy and the default product roles (axe clean). Easy CRM's member role is set to `sales_rep`. |
| server (13) | Each new person has exactly one organization, as owner. Their products' action is `open`. Each holds the Easy CRM seat. Two simultaneous clicks made **one** trial. |

Evidence: `~/w360wt/auto-trial/work/` (report.json, journey-*.json, 15 screenshots). The first run is kept as `work-run1`; it failed 4 checks, all harness wording, a Chrome timeout or the multi-organization case. The last of those led to the "For <organization>" line.

**Not covered in a real browser:**
- Easy Project, Easy Help Desk and EasyBook. Section 5 says why.
- Failed provisioning and its recovery. This is proved in the backend tests: the provisioning listener is broken, then mended, and one Retry Setup provisions and seats. There is no way to break provisioning inside a live stack without a code hook.

## 4. Security checks

- **Platform rights.** A customer only ever becomes owner of their own organization. `is_staff` is never touched: tested.
- **Role names.**
  - A product role can never name a platform, staff or super-admin authority. This is validated on the server and in the editor; tested with `super_admin`, `platform_admin`, `Staff`.
  - Role claims are only for an active member, and only under the `organization` scope: tested.
- **Who may act.**
  - The trial and role policy endpoints are staff only; a customer is refused: tested.
  - A plain member cannot start a trial or seat themselves: tested.
  - Another organization is refused: 403 or 404 for read, trial and repair. Tested.
- **Duplicates.**
  - Two running trials are impossible at the database level, even if the policy check is bypassed: tested.
  - Simultaneous clicks make one trial, one workspace and one seat: tested at HTTP level and in Chrome.
- **Isolation.**
  - "One trial per person" reads the person's other organizations through each organization's own scope. Row-level security is never bypassed.
  - Import contracts: 18/18 kept, including A10 (the single entitlement writer).
- **Store host.** It serves product access and launch only. Seats, members and settings stay on User Admin: tested.

## 5. Remaining issues and integration blockers

- **Real-browser coverage.**
  - Easy CRM is the only real product in the local Chrome harness today.
  - Easy Project and Easy Help Desk were last run on 2026-10-13 and 2026-11-19, from build folders that have since been deleted. Their builds (JDK 21 / Maven, pnpm) must be redone before they can run in Chrome again.
  - EasyBook has an HTTP-level harness (`Accounts/e2e/run_customer_journey.py`), not a rendered one.
- **Products do not read `product_role` yet.** The changes needed per product are in [product-roles.md](product-roles.md). Until they land, each product keeps its own default role.
- **Role re-sync** on every sign-in is an owner decision (product-roles.md, section 2).
- **Live integrations.** On production (2026-11-27) every product integration was still a draft. Those products show "Buy Plan" or "Not Available Yet" instead of a trial, by design (RC-5), until their sign-in is activated.
- **Migrations.** Two migrations must be applied on deploy: `catalog.0016`, `subscriptions.0007`.
