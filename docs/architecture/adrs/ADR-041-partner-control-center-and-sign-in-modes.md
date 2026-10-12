# ADR-041: One Partner Control Center; "Administrator / User" at sign-in is a destination, not a privilege

**Status:** Accepted (2026-10-06)
**Builds on:**
- ADR-028: partners are capability sets, with no type.
- ADR-037: the partner consoles and derived labels.
- Phase 7/12: `StaffGrant`, `authz.check`, and D-037 (a staff session must have verified a second factor).

**Record:** [docs/partner-center/status.md](../../partner-center/status.md).

## Context

Two requests arrived together.

**1. The partner screens.** The Super Admin sidebar had grown a Partners section of seven links: Overview, Resellers, White-label, Customers, Products & Pricing, Commissions and Payouts. Affiliates, the third derived label, had no screen at all.

**2. The sign-in page.** The owner asked it to offer **Administrator** and **User**. This had two hard constraints:
- choosing Administrator must never grant anything;
- there must not be a second authentication system.

## Decision

1. **One sidebar entry, one Control Center.** The sidebar shows a single **Partners** entry. It opens `/admin/partners`, a Control Center with its own navigation:
   - Dashboard, All Partners, Resellers, White-label, Affiliates;
   - Customers, Products & Pricing, Commissions, Payouts.

   The three views of partners and their labels follow ADR-037:
   - **All Partners**, **Resellers** and **Affiliates** are one list endpoint, `/admin/partners/resellers?scope=all|resellers|affiliates`, over `PartnerAccount`. A scope is a filter on effective grants, computed by `capabilities.experience_keys`. It is not a table or a column.
   - White-label stays the existing filtered view.
   - The detail page's badges, and which tabs it shows, come from the server's `experience_labels`. Branding and Domains appear with White-label, and Referrals with Affiliate.

   Old addresses keep working: `/admin/commissions`, `/admin/payouts` and `/admin/partners/resellers?scope=all` redirect into the Center with their filters kept.

2. **The dashboard's recent activity is one bounded read of existing data.** It is `/admin/partners/recent`: the newest partners, the newest customer links (`customers_page`), ledger entries and open payouts (`commissions.selectors`), and unproven domains. No activity table and no event subscriber were added.
   - The money sections need `platform.staff_commission.view`, exactly as their full pages do.
   - Without that grant they are `null`, which means *not shown*. They are not `[]`, which would mean *none*.

3. **Add Partner offers a business purpose: Reseller, White-label, Affiliate or Custom.** A purpose only picks which `CAPABILITY_PRESETS` entry the existing set-up flow starts with ticked. Two presets were added: `white_label` and `affiliate`.
   - The purpose travels in the address bar (`?purpose=`). It is never sent to the server and never stored.
   - Creation is the same command sequence as before: create the partner, then grant each capability.

4. **The sign-in mode is a destination chosen in the browser.** The flow is:
   - `/login` asks "How would you like to sign in?". Each answer is a link to `?mode=admin` or `?mode=user`.
   - Both modes then use the one form and the one `POST /api/v1/auth/login`, whose body is only an email and a password. Rate limits, lockout, CSRF, the session and the audit trail are unchanged.
   - Administrator then goes to `/admin`, or to a validated `next` path under `/admin`. User goes to `/dashboard?mode=user`.
   - `mode=user` only *removes* the dashboard's redirect of staff to the console. It can lead nowhere a plain visit could not.
   - The mode is not kept in browser storage, not sent to the server, and not read by anything that decides access.

5. **"Administrator" is decided by `/api/v1/admin/session` on every console load.** It requires all three of:
   - a staff identity (`is_staff`);
   - a session that verified a second factor (D-037, asked *first*, so the refusal order reveals nothing);
   - **at least one `StaffGrant` in force**, read live.

   The last condition is new. A staff identity whose every grant was revoked or has expired used to get an empty console. Now it gets `403 permission_denied` on its next request. This matches the `consoles.staff` hint the session already gave.

   `is_superuser` is never read. The console then shows "You don't have administrator access." and offers **Continue as User**. It names no permission and loads nothing from the console.

## Consequences

- No partner type, no Reseller, WhiteLabel or Affiliate model, no AdminUser, and no second login route or authentication backend. This is pinned by:
  - `tests/phase10/test_10f_architecture.py`, unchanged;
  - `tests/partner_center/test_architecture.py`, new: one `ModelBackend`, one route named `*login*`, a `login_view` that reads only `email`/`password`, a staff gate that reads no `is_superuser` and no request input, unchanged migration heads, and no browser storage in the sign-in files.
- An unauthenticated visit to `/admin/...` goes to `/login?mode=admin&next=<that page>`. A `next` that is not a same-site path under `/admin` is ignored: other hosts, `//host`, backslashes, control characters, and `..` paths that leave `/admin`.
- Product sign-ins (`?resume=`) and partner-branded sign-ins (`?brand=`) skip the choice: they are always the User mode.
- The list endpoint does 3 more grouped queries per page (paid payouts, referral codes, referral counts). The count is still constant in page size and data size, and `tests/phase10/test_10f_performance.py` still passes.
- A staff identity holding no grant can no longer open an empty console. Nothing depended on that.
