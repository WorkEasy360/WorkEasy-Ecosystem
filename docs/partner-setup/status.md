# Complete reseller + white-label setup: status

**Date:** 2026-10-06
**Decision record:** [ADR-042](../architecture/adrs/ADR-042-partner-setup-profile-and-contacts.md)
**Design and contract:** [design.md](design.md)
**Browser check:** [scripts/partner_setup_browser_check/](../../scripts/partner_setup_browser_check/README.md)
**Commit state:** nothing is committed.

## Result

**RESELLER + WHITE-LABEL COMPLETE SETUP: PASS** (gate table in §2, regression line in §3).

> Can a Workeasy360 administrator now fully configure a reseller and/or white-label partner, including business details, contacts, capabilities, products, pricing, commission, users, branding and domains, without creating duplicate partner systems or weakening the existing authorization and commercial architecture?
>
> **YES.**

## 1. The 32 items

| # | Item | Result |
|---|---|---|
| 1 | Reseller fields | `/admin/partners/resellers/new` is a **9-step wizard** (the brief's 12 content steps, merged so the indicator stays at 9): Business details, Contacts, Capabilities, Products and trials, Pricing, Commission and payouts, Administrator and support, White-label, Review. Each step has Save & continue, Back and a progress list with `aria-current="step"`; **Save draft** is on every step once name, code and currency are valid. |
| 2 | Required vs optional | Required (marked `*`, and `required`): company or partner name, code, payout currency, business email, country, primary contact name and email, at least one capability, the administrator's email (to activate). Everything else says "(optional)". Save draft needs only name, code and currency. The server separates **problems** (wrong: block everything) from **readiness** (missing: blocks only Activate). |
| 3 | Business information | New `PartnerProfile`: business email, website (https only, never fetched), phone, alternative email, registration number, tax/GST/VAT number (character-checked only: no country format is enforced because none is known right for everyone), ISO country, address (line 1, 2, city, region, postal code), plus the account's own name and legal name. "Partner / company name" and "Display name" are **one field** (`display_name`): a second name would store something nothing reads. Status is Draft (default) or Active, which only chooses which button is primary on the Review. |
| 4 | Contacts | New `PartnerContact`: primary (name, job title, email, phone), billing, technical, support. "Same as primary contact" stores **no row** (a copy cannot drift). Contacts are text: no login, no membership, no permission, staff-only. The primary contact is mirrored onto the account's contact fields in one transaction. A contact is merged field by field, so sending a phone number never blanks the name or email. |
| 5 | Capabilities | Friendly groups from the server (Customer management, Products and sales, Commercial, Support, White-label, Referrals), each capability by a short plain name with its full sentence beside it, presets **Standard Reseller** and **Custom**, and "Show technical capability names". The list is the registry: nothing can be typed. "View customers" is not a capability of its own (it comes with either customer capability). |
| 6 | Products | Read from the catalog (any future product appears with no change; only `available` ones are offerable): name, status, plans, and the platform's trial summary. No product grant, no right to sell, enforced by the existing backend. |
| 7 | Pricing | **Standard partner pricing** (list price, nothing created) or **Custom partner pricing** (the existing `PriceBook`/`PlanPrice` through `publish_price`). Columns: product and plan, billing frequency, currency, list price, partner price, discount, partner margin (the server's commission) and what the platform keeps. Every figure is the server's (`resolve_price`, `price_figures`); the browser only converts typed text to minor units. A recommended retail price is **not offered**: pricing has no such concept. A partner price never changes the global list price (tested). |
| 8 | Trials | The platform's existing trial per product is shown (duration, seat limit, eligible plans). The administrator decides only whether this partner's customers may start it (`PartnerProductGrant.trial_allowed`): it can **narrow, never add**, and a started trial is untouched. Enforced where a customer starts a trial and in what Explore offers. No second trial engine. |
| 9 | Commissions | The existing `CommissionRule`: programme default or a rate for this partner, scope (all products or selected ones: one rule per product), effective date (never in the past), the hold period shown read-only (a platform setting), and a **worked example computed by the server** (a 10,000 whole-unit purchase in the partner's currency: purchase, commission, platform net, integer minor units, half-up). Old sales keep the rate they were computed under (tested). |
| 10 | Payouts | Preferences only: frequency (monthly, quarterly, manual), contact email, currency shown read-only. No bank details are collected; nothing is paid automatically; the workflow stays Draft, Ready, Approved, Paid, Cancelled. **No minimum threshold**: payouts have none. |
| 11 | Reseller admin user | First and last name, email, role, and **when to invite**: now, or when the partner is activated (a recorded `invitation_deferred` flag; activation emails exactly those people, once). Reuses `identity.User`, `PartnerMember` and the existing setup-link mechanism. No password is ever asked for or shown. |
| 12 | White-label enablement | In the reseller wizard: Not now / Yes. Yes adds the two white-label capabilities to the **same** partner and offers "Continue to white-label setup". In the white-label wizard: `enable_white_label` grants the same two capabilities together, with a reason, a confirm and a second factor. No second `PartnerAccount` ever (browser journeys W, C, A count the rows). |
| 13 | Brand information | `PartnerBrand`: brand name, short name, primary colour, support contact name, email, phone, support page, company website, welcome message, footer text (all bounded plain text). |
| 14 | Logo / assets | PNG or JPEG, 512 KB, file signature checked in the browser and again by the server through the existing Files platform. SVG, HTML and anything else is refused (tested). No remote images. |
| 15 | Theme | One primary colour; the app derives every readable shade from it. A **secondary colour and a favicon are not offered**: the theme the app renders has one derived accent, so they would store something nothing shows. No raw CSS, HTML or script. |
| 16 | Product visibility | Per product: available to partner, visible in portal, sell allowed, trial available (all derived by the server except the two saved choices). "Visible" hides a product from the customer's Explore page only; it never grants or removes an entitlement and a product the customer holds still shows and launches (tested). |
| 17 | Communication | Sender display name, reply-to address, and "use my brand on invitation emails". Only the email a partner sends in its own name exists (the invitation to a customer's owner), so that is the one wired; the From address is always Workeasy360's. Customer welcome, trial and subscription emails **do not exist** and are listed as "Not available yet". A hostile name or address is dropped at delivery (header injection tested). |
| 18 | Custom domain | The existing `PartnerDomain` lifecycle. The DNS TXT record is shown as Type / Host / Value with **Copy** buttons, **Check verification**, and a separate, deliberate **Activate** (never automatic after verification). Canonicalisation, IDNA, duplicate-ownership protection, DNS proof, AAL2 and audit are the existing code, untouched. |
| 19 | D-035 behaviour | Shown honestly, from the server: "Custom-domain branding: Active / Not active" and "Authentication: Uses Workeasy360 secure sign-in", plus the sentence that no password is ever typed on a partner's domain. D-035 is closed **by decision** (ADR-039): a custom domain shows the brand and hands over to Workeasy360's sign-in. No wildcard CSRF origin, no wildcard host, no cookie scope change. |
| 20 | Partner detail | Tabs: Overview, **Business Details**, **Contacts**, Customers, Products & Pricing, Subscriptions, Commissions, Payouts, Branding and Domains (white-label), Users & Permissions, Referrals (affiliate), Activity. Overview: derived badges, cards (Customers, Products, Subscriptions, Commission, Pending payout, Domain), the setup progress list, a "Setup incomplete" notice listing what is missing (each linking to the tab that fixes it), and Activate disabled with the reason while anything is missing. |
| 21 | Users & permissions | Name, email, role, status (Active, Invitation sent, Invitation not sent yet, Invitation pending), MFA (yes/no), last activity. Invite (with names and timing), change role (an active partner keeps one administrator), resend setup link, remove (ends partner access at once and never deletes the person's Workeasy360 account: tested over HTTP). |
| 22 | Activity | Timestamp, actor, action in words, target, reason, from the one audit log. The target comes from a closed list of facts, so no secret value can appear; the audit entry for a profile change names fields and never their values (tested). Domain requests now name their partner and appear. |
| 23 | Save-draft behaviour | Save draft creates a real `draft` partner with whatever is filled in. A draft can do nothing until activated, so an incomplete one creates no commercial state. The Overview then lists what is missing. A failed step shows "Setup did not finish" with Try again; finished steps are never repeated. A leave-page warning protects unsaved work. |
| 24 | Security | Backend tests for: a customer who is nobody special, a partner session, a signed-out visitor, a viewer, a weak second factor, no CSRF token, partner A reading partner B, price manipulation (negative, float, string, bool, overflow), commission manipulation (over 100%, float, string, backdated), capability injection, mass assignment, domain takeover, logo XSS, hostile text, header injection. All refused. See §3. |
| 25 | Responsive | 130 reflow checks at 1440, 1024, 768, 390 and 320 px over both wizards (every step) and the partner's tabs: **0 failures**. The progress list collapses to the current step on narrow screens. |
| 26 | Accessibility | Real browser: 32 pages (every step of both wizards, the confirm panels, six partner tabs, the customer pages), **0 axe WCAG 2.1 A/AA violations**, 0 elements without a visible focus, exactly one `h1` each, 0 CSP violations, 0 console errors. Labels, `*` required marks, errors tied to fields, focus to the step heading and to the error summary, keyboard operation, tabs with arrow keys. jsdom axe runs on every step too. **Manual screen-reader pass: NOT EXECUTED.** |
| 27 | Browser journeys | **7/7 passed** (production build, real backend, scratch database `w360_psetup_fresh`, clean run): R reseller wizard to activation, D save draft, W white-label for an existing partner, C combined partner (three labels, one row in every list), A existing affiliate becomes white-label (same account), U the customer sees the brand and a hidden product is not offered, S a customer is refused at both wizards. |
| 28 | Backend tests | See §3. |
| 29 | Frontend tests | **716 passed** (33 files), up from 428 (26 files). |
| 30 | Migrations | One: `partners.0004_partner_setup_profile_contacts`. Five columns (each with a `db_default`, so the previous release keeps inserting) and two tables. No data is rewritten. `makemigrations --check`: no changes. |
| 31 | Dependencies | **None added or changed by this work** (`package.json` is untouched, the lockfile is exactly what it was before it started, and no Python requirement changed). The lockfile's earlier `source-map-js` 1.2.2 bump from the Control Center work is still uncommitted, and your own `frontend/node_modules` still needs `npm install` for it (with `next dev` stopped). |
| 32 | Remaining P1/P2/P3 | See §5. |

## 2. Quality gates

| Gate | Result |
|---|---|
| Full backend `pytest` (bare, all testpaths) | see §3 |
| `ruff check backend`, `ruff format --check backend` | pass (688 files formatted) |
| `mypy` | pass (31 files) |
| `lint-imports` | 18 kept, 0 broken |
| `bandit -ll` | 0 medium or high |
| `pip-audit --strict` | no known vulnerabilities |
| `manage.py check --fail-level WARNING` (migration settings) | no issues |
| `manage.py check --deploy --fail-level ERROR` (development settings) | pass; only the 6 transport warnings CI documents as expected under development settings |
| `makemigrations --check --dry-run` | no changes |
| Clean `npm ci` (scratch copy), `next typegen`, `tsc --noEmit`, ESLint | pass |
| `vitest` | 716 passed |
| `next build`, `assert-dynamic-rendering` | pass; every route dynamic, so the CSP nonce applies |
| `npm audit` | production **0**; full 5 high, the known dev-only `braces` chain via `eslint-config-next` (no fixed version upstream; unchanged) |
| Production CSP in the real browser | 0 violations on 32 pages |
| axe in the real browser | 0 violations |
| Real-browser journeys | 7/7 |

## 3. Backend tests

**3,690 passed, 0 failed, 0 skipped** on the final tree: bare `pytest -q`, all testpaths (3 h 34 min, with the browser check and other work sharing the machine). That is up from 3,388, with **302 new tests** in `backend/tests/partner_setup/`. The run printed one warning: structlog's existing "Remove `format_exc_info`" notice, in `tests/security/test_ecosystem_security.py`. It is unrelated.

The first full run of this tree (3,688 passed) had **2 failures**: two older tests that pinned the exact key set of the public brand (`tests/phase11/test_11g_reseller_whitelabel.py` and `tests/phase7/test_branding_domains_referrals.py`). The public brand now carries the public part of the customer portal, so both now expect `portal` and assert that the sender identity is absent. The whole suite was then run again from the start: the result above.

`backend/tests/partner_setup/` holds the new tests:
- **profile and contacts** (parsing, partial edits, field-level merge, the version token, no currency or code change, the audit names fields and never values, who may);
- **review** (problems vs readiness, trials, pricing mode, commission scope, date and worked example, white-label, writes nothing, garbage is a problem and never a 500);
- **product choices** (trial gate, Explore, portal visibility, display only);
- **brand portal** (closed key set, plain text, merge, the public part only, the sender identity and header injection);
- **white-label setup** (the one read, future products, enabling, idempotence, same account);
- **members** (names, deferred invitations sent once, roles, last administrator, removal keeps the account);
- **activity and overview** (readiness, words, no values);
- **security** (the matrix above);
- **architecture** (the model set, no partner type, nothing that holds authority, one price and one commission function, no second email system, schema-only migration, the country list equals the frontend's);
- **contract shapes** (every new client type against the real response).

Existing tests changed to the new intended behaviour: the capability group keys and short labels; the partner model set; the profile step of the setup progress now needs a business email; the public brand carries the public portal (three tests pin its key set); the newest partners migration.

## 4. Browser journeys (production build, real backend, scratch DB `w360_psetup_fresh`)

- **R. Add reseller.** All nine steps typed through the UI (company, address and country, primary and billing contacts, Standard Reseller, two products, custom pricing with the server's discount, a 20% commission with the server's worked example, a monthly payout preference, an administrator invited after activation, support and notes), the Review, the confirm panel, Activate. Then the partner's page: badges, Business Details, Contacts, Products & Pricing, Users (the administrator reads "Invitation sent") and Activity (in words) show exactly what was typed.
- **D. Save draft** after step 1: "Setup incomplete", the missing items listed, Activate disabled.
- **W. White-label** for the existing Seed Reseller: partner search, brand, a real PNG through the real upload, colour, support details, all four preview modes, a product hidden from the portal, welcome message and footer, sender identity, a custom domain (DNS record shown, "published", verified, and only then **separately** activated), the honest sign-in statement, derived labels, Review, Enable. One row for the partner before and after.
- **C. Combined.** The reseller from R gets white-label, then an affiliate capability: Reseller, White-label, Affiliate on one page and exactly one row in All, Resellers and Affiliates.
- **A.** An existing affiliate becomes white-label: same account.
- **U. The customer** of the white-label partner sees the brand, the welcome message, the footer and the support details; the hidden product is not offered and the other is; nothing is stored in the browser.
- **S.** A customer who picks Administrator, then opens either wizard's address, gets "You don't have administrator access.", no form, only 403s.
- **Expected refusals:** 4, all 403s (the password-only staff session before its second factor, and the customer's). **Leave-page warnings** from the wizard's unsaved-work guard are accepted by the harness, as a person leaving would.

**Bugs the browser check and its test runs found, and fixed:**
- activating a draft emailed *every* member without a password (it must email only the deferred ones: an explicit flag now);
- a domain request's audit entry did not name its partner, so it never reached the partner's Activity;
- the partner's own brand endpoint dropped the new portal fields;
- the invitation name fields were bounded only in the review, not in the invite command;
- a profile edit without its version was accepted (it is now required);
- sending one contact field blanked the others (and the account's contact email): contacts now merge field by field;
- the commission example used the programme default instead of a scoped custom rate.

## 5. Remaining items

- **P1:** none introduced.
- **P2:**
  - Nothing is committed. The earlier Control Center work and this are one uncommitted tree.
  - Sign-in *on* a partner's own custom domain is unsupported by design (ADR-039); the wizard says so.
  - The dev-only `braces` advisory remains open upstream (no fixed version).
- **P3:**
  - The manual screen-reader pass was not executed.
  - Things the platform has no concept for are not offered: a recommended retail price, a minimum payout threshold, a secondary colour, a favicon, a per-partner commission hold, and trial, welcome and subscription emails.
  - "Company name" and "Display name" are one stored field.
  - Activation readiness is enforced by the wizard and shown on the Overview, not by `set_partner_status`, which keeps its existing, narrower refusal (an administrator is required). A partner activated from some other client skips the advice.
  - A saved draft is finished from the partner's own tabs; the wizard does not reload an existing partner.
  - The partner's own portal wizard does not yet expose the portal text fields (the API accepts them); staff set them.
  - The welcome message is also shown at the top of the customer's dashboard, so switching it off on the sign-in page does not hide it everywhere.
  - Saving business details asks for a second factor each time, because changing a partner is a dangerous action in the existing policy.
