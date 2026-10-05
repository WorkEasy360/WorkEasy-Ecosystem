# Phase 7 status

**PHASE 7 STATUS: PASS** (2026-09-24)

> Can Workeasy360 now centrally administer customers, products, subscriptions and
> entitlements, while supporting reseller, white-label and affiliate commercial
> channels without duplicating product implementations or weakening
> customer/partner isolation? **YES.**

Phase 8 has **not** been started, no product-domain feature was built, and no real
payment transfer exists: `paid` records that a person made one elsewhere.

## Final adversarial audit (2026-09-24): PASS, after fixes

An independent adversarial audit of the implementation found **no P0** and **six
P1 defects, all fixed and each proven by a test that failed first**: Django's
`is_staff` was still an authority on the older organization-scoped catalog,
pricing and integration-registry routes (no `StaffGrant` needed, not revocable);
staff grants could not expire although the docs said they could; free trials
earned commission and a lapsed trial never reversed it; a payout could be
approved after an entry in it had been reversed; partners could add seats of or
convert trials for products outside their grants; and ended partner
relationships still showed the customer's live data. One further P1 is open and
**blocks reseller go-live, not Phase 8**: D-020, a partner-provisioned owner with
a new address cannot sign in (reclassified from P2). Ten P2 and ten P3 items
were fixed. Full record: [11-final-opus-audit.md](11-final-opus-audit.md).

## Quality gates

| Gate | Result |
|---|---|
| `pytest` (full backend suite, fresh test database) | **1,858 passed, 0 failed, 0 skipped** after the final audit (1,812 before it; the audit added 46 - probes-then-regressions, a real-DNS integration suite, a search-scale measurement, operator-command and fitness tests) |
| `ruff check`, `ruff format --check` | clean |
| `mypy` (`contracts/`, per `pyproject.toml`) | no issues in 27 source files |
| `bandit -ll` | clean |
| `pip-audit --strict` | no known vulnerabilities (one dependency added: `dnspython`) |
| `lint-imports` | 14 contracts kept, 0 broken |
| Architecture tests | pass, including the new Phase 7 rules and the final audit's two (every `is_staff` reader allowlisted; no partner-type branch anywhere in the admin/partner frontend or the partner, commission and control-plane backend) |
| `manage.py check` / `check --deploy` | clean. `--deploy` against production settings reports only `W360.W002` "could not verify RLS: password authentication failed", an artefact of a made-up database password; the RLS verification itself runs as the application role in the fresh-database rehearsal (below) |
| `makemigrations --check` | no changes |
| Frontend `npm ci`, `next typegen`, `tsc`, `eslint` | clean (scratch copy, so `next dev` was undisturbed) |
| Frontend tests | **76 passed** (72 before the final audit; it added the ended-relationship row, the derived label colour, a failed sign-out and focus-after-action) |
| `next build` + prerender assertion | 34 routes, all dynamic; no route can lose its CSP nonce |
| `npm audit` | 0 vulnerabilities |
| Bundle delta | All client JS+CSS: 711,256 B -> 1,157,335 B raw (342,309 B gzip vs 224,410 B) with the `/admin` and `/partner` route areas. Loaded per route, so only staff and partner users download it; the launcher and sign-in paths are unaffected. No dependency added |
| Production CSP and real-browser check | 20 pages in Chrome: 0 CSP violations, 0 console errors, 0 axe violations (incl. contrast), focus visible, no horizontal scroll at 320px - re-run by the final audit, unchanged ([06-frontend.md](06-frontend.md)) |
| Keyboard-only operation (final audit) | 28 of 28: sign-in, navigation, payout approval, a time-boxed staff grant, a commission rule and a custom-domain request with Tab/typing/Enter only, through the real MFA step-up (after fixing focus loss, A-27) |
| Fresh-database run and Phase 6 -> 7 upgrade | **55 of 55** checks (`scripts/rehearse_phase7_upgrade.py`), including a real rollback and re-apply, the owner unable to `TRUNCATE` the ledger, and a staff grant's expiry frozen |

## The acceptance scenario

`backend/tests/phase7/test_final_acceptance_flow.py` walks the brief's 25 steps
through real login, CSRF and middleware: Partner A is created and granted
`customer.provision`, `subscription.sell` and `commission.earn` (not
`branding.manage`), sees provisioning and commissions but is denied white-label;
provisions Customer X (own organization, invited owner who accepts), sells a plan
(ordinary provisioning creates the entitlement), Customer X sees the product in its
launcher; no product-domain table exists; Partner A earns exactly one deterministic
commission that a duplicate delivery cannot double; Partner A cannot see or sell to
unrelated Customer Y; affiliate-only Partner B can create referrals and view
commission but is refused customer management; Partner A is then granted branding
(hostile colour refused), requests a domain that stays inactive; the link is ended,
Partner A immediately loses the customer, Customer X still signs in and keeps its
product; the Super Admin diagnoses Customer X's access without secrets; the products
package still contains no code.

## Evidence by area

| Area | Where |
|---|---|
| Permission model, AAL 2 matrix, stale-permission, IDOR | `test_security_matrix.py`, `test_http_api.py`, `test_partner_control_plane.py` |
| Mass assignment, references through a real `COMMIT` | `test_client_controlled_references.py`, `test_http_api.py` |
| Concurrency on real threads (12 races) | `test_phase7_concurrency.py` |
| Commission, ledger, payouts | `test_commissions.py` |
| Branding, domains, referrals, retention | `test_branding_domains_referrals.py`, `test_channel_extras.py` |
| Partner commerce and independence after termination | `test_partner_commerce.py` |
| Staff operations, overrides, dashboard, audit | `test_staff_operations.py` |
| First administrator | `test_bootstrap_staff.py` |
| Client <-> API agreement | `test_frontend_contract_shapes.py`, `test_frontend_request_flows.py`, `test_phase7_architecture.py` |
| Privacy, cache isolation | `test_privacy.py`, `test_cache_isolation.py` |
| Performance and plans | `test_phase7_performance.py` ([07](07-performance.md)) |
| Fresh and upgrade databases | `scripts/rehearse_phase7_upgrade.py` |

## Fresh database and upgrade

`python scripts/rehearse_phase7_upgrade.py` (scratch databases it creates and drops;
the development and test databases are never touched).

**Fresh:** `migrate` from empty; models match migrations; every Phase 7 table, three
indexes and three constraints exist; the application role can `SELECT`/`INSERT` but
not `UPDATE`/`DELETE`/`TRUNCATE` the ledger; an append-only trigger exists and even
the table **owner** cannot `UPDATE` or `DELETE` a ledger row (tried for real); freeze
triggers on payout lines, rules and subscription items; the application's own system
checks - including RLS verification **as the application role** - pass.

**Upgrade:** a database built at the Phase 6 schema (every migration except Phase 7's)
holds a user, organizations, three price books (one an orphan `partner`-channel book
of the kind the data migration must handle) and audit events; `migrate` to Phase 7
loses no row; the orphan book is retired (`internal`, inactive), not deleted; the
direct book is untouched; the same guarantees hold; the customer summary starts empty
and `reconcile_all` fills it for every organization. **Rollback:** every Phase 7
migration reverses cleanly, the Phase 6 data survives, and re-applying succeeds.
Reversing drops the partner, commission and staff-grant tables, so it is safe only
before Phase 7 data matters (R7-8).

### Upgrade runbook

1. Deploy code and run the migration job (`config.settings.migration`) together. The
   changes are additive - new tables, a nullable `PriceBook.partner_id`, three
   indexes, one data update - but a check constraint now forbids a `partner`-channel
   price book with no partner, so Phase 6 code must not keep running against the
   upgraded schema.
2. Create the first administrator: `manage.py w360_bootstrap_staff --email <existing
   account> --confirm` (then grant everyone else from the Super Admin screen).
3. Run `commercial.provisioning.reconcile_all` once (or wait for the hourly beat) so
   the customer directory shows subscription and seat data.
4. Set `W360_PLATFORM_DOMAINS` (hostnames a partner may never claim) and
   `W360_DNS_RESOLVERS` if the host resolver should not be used.
5. Grant partner capabilities, product scope and a commission rule before activating
   the first partner.

## Findings

No unresolved P0. No P1 that blocks Phase 8. One open P1 is a **go-live gate**
for the reseller channel (D-020).

| ID | Sev | Finding | State |
|---|---|---|---|
| R7-1 | P1 | Partner self-dealing: commission on a customer that never pays | Mitigated, and tightened by the final audit (no commission before activation, stale-batch and manual-entry-author approval refused, self-provisioning and self-referral refused). **Residual:** a second, unrelated identity posing as a paying customer - caught only by the payout reviewer confirming customer payment (runbook in [11](11-final-opus-audit.md)) |
| R7-2 | P1 | A compromised staff account | Mitigated ([04](04-security-and-authorization.md)); the final audit closed the `is_staff` route around grants and added grant expiry and `w360_staff_identity --disable` |
| R7-3 | P1 | Custom-domain takeover / phishing | Mitigated; TLS and routing for partner domains are infrastructure, not built. **Go-live gates** before any partner domain is routed: admin/partner areas on canonical hosts only, sign-in on a canonical host or host-bound sessions, certificates only for active hostnames |
| D-020 | **P1** | A partner-provisioned owner (new address) has no way to set a credential | **Open - reseller go-live gate** (reclassified from P2; the email channel exists, the claim flow does not) |
| D-021 | P3 | Staff cannot change a customer's bundle | Open |
| D-022 | P3 | Two HTTP surfaces over one catalog implementation | Open; the authorization divergence it hid is fixed (A-01) |
| R7-5..8 | P2/P3 | Exempt tables, capability sprawl, rollback data loss | Documented |
| - | P3 | Orphaned logo uploads are not swept | Open |
| - | P3 | Customer/partner text search is an unindexed scan | Open; `pg_trgm` past ~50k organizations |

Defects found and fixed during verification (14) are listed in
[09-opus-audit.md](09-opus-audit.md). They include two production defects in Phase 2
code (a zero quantity coerced into a sale of one and an unbounded quantity that broke
reconciliation, and string values read as true booleans on trial and immediate-cancel
flags) and a Phase 2 test that silently disabled commission accrual for every later
test.

## Cleanup (Task 136)

Removed as verified dead: `partner_may_transact`, `is_capability`,
`links_for_partner`, `get_payout`, the unused `.danger` style, the unused
`customerSubscription` client call and the type that served it, and an unnecessary
export. Nothing was removed on suspicion: every permission is reachable (through a
capability or an `implies` chain), every React component and CSS class is used, and
no dependency was added to the frontend. The full suite was rerun after the cleanup.

## Dependencies

Added: `dnspython==2.7.0` (backend; DNS TXT lookups for domain proof, behind a
swappable resolver with timeouts; pure Python; pip-audit clean). Frontend: none. No
billing or payment SDK, chart library or state library.

## Not executed

- A manual screen-reader pass (NVDA/VoiceOver): needs a person.
- Keyboard operation beyond focus visibility (activation, focus return after the MFA
  prompt) beyond what native controls provide.
- A load test: numbers in [07](07-performance.md) are local shapes, not capacity.
- Real DNS against a real registrar (the resolver is swappable and tested with fakes).

## Decisions proposed to freeze for Phase 8

1. **One `authz.check`.** Staff and partners are principals, not a second system
   (ADR-027); the dangerous-permission set is pinned by a test.
2. **A partner is a set of capabilities** from one registry; there is no partner type
   (ADR-028).
3. **The commission ledger is append-only and derived**; corrections are new entries;
   real payment is added *behind* `paid` as a state reached by a verified provider
   event, without touching the ledger (ADR-029). Adopt dual approval when payment
   becomes automated, a threshold is crossed, or a second approver is routinely
   unavailable.
4. **A partner reaches a customer only through a live link**, and a miss is 404.
5. **The five tenant-scope exemptions (D-019) are the complete list**; a sixth needs an
   ADR-level reason and a register entry.
6. **Money is server-derived**; no request body carries a price, amount, rate,
   approver or status.
7. **The client and API are kept honest by tests** that parse the client's routes,
   types and capability names against the real API.
8. **Public branding is resolved from an explicit, canonicalised host and served
   same-origin** (`img-src 'self' data:` is not widened).
9. **Real-browser checks are part of the frontend's definition of done** for new
   screens (`scripts/phase7_browser_check/`).

## Exit conditions

Every condition in the brief is met: the control plane exists and does not rely on
Django superuser semantics; staff permissions are granular and dangerous ones need
AAL 2; the partner model is capability-driven with no type; reseller, white-label and
affiliate foundations work and are isolated per partner and per customer; customers
outlive partners; commission is deterministic, versioned, idempotent, append-only and
reconcilable; payouts exist without pretending payment; diagnostics are read-only and
secret-free; no product-domain access exists and no secrets are exposed; no Phase 7
mutation violates D-016 (validated before write, or raised inside `atomic`, and tested
through a real `COMMIT`); concurrency tests pass on real threads; backend and frontend
gates pass; the fresh database and the Phase 6 -> 7 upgrade pass; the lean-code review
is complete. **Stop: Phase 8 is not started.**
