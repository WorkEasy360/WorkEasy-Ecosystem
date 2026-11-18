# ADR-062: Partner categories in the Admin, and Login as Guest across hosts

Date: 2026-11-10. Status: accepted (built on `feat/admin-panel`, not deployed). Amends ADR-060 (the flat Admin sidebar), ADR-048 (a combined partner listed under every label) and the guest view of ADR-047; keeps ADR-059 (each portal signs in on its own host).

## Context

The owner's brief of 2026-11-10 asked for three things on the Admin's partner screens, without redesigning the All Partners table:

- **Partners ▼ Affiliate, Reseller, White Label**, each listing only that type, filtered on the server, reusing the table.
- **Login as Guest** on every partner: the partner's real dashboard, read-only, short-lived, audited, with an exit back to the Admin session, enforced by the backend and never using the partner's credentials.
- **The New Partner form asking for the Partner Email**, not an admin's address.

The audit behind this ADR found:

- **Login as Guest was broken in production.** A "Guest login (read-only)" row action existed (ADR-047). It held its grant in the cache under the staff member's sign-in, and that grant reached the partner portal through the central hand-off (ADR-043). ADR-059 (deployed 2026-11-07) gave each portal its own sign-in and stopped every page from starting a hand-off. Session cookies are host-only, so since then the action sent the browser to `partner.` with no session there, and the partner sign-in page opened. Every automated check passed, because the tests either ran on one host or still went through the hand-off on `auth.`.
- **The type filter overlapped.** "Partner type" filtered on capability grants. A White-label partner holds the reseller's selling grants, so it appeared under Reseller as well, and an upgraded affiliate appeared under two types. The Type column, though, shows one type: the workspace the partner lands in.
- **A new draft had no type in the lists.** A draft's workspace comes from its grants. For a partner created moments ago, that left only the type chosen at creation.
- **The Partner Email form was already live** (ADR-061 decision 3). Nothing said "Admin Email" any more. A staff address is refused as a Partner Email, and the creating staff member is stored in `created_by` and in the audit entry.
- **No guest view could be revoked by another Admin,** none outlived the cache, and the permission was the general `platform.staff_partner.manage`.

## Decision

1. **One category per partner, decided by the server.** `capabilities.partner_category` gives the type the Type column already shows (`resolve_landing`: the stored type when the grants allow it, else the largest earned workspace). A partner whose grants earn no workspace yet (a new draft) falls back to the type it was created as. `console_reads.category_q` is the same rule in SQL. A test holds the two together over every combination of grants and stored type. The list endpoint takes `category=affiliate|reseller|white_label`, and each row carries `category`. It is a listing, never an authority: what a partner may do still comes from its grants. The old `type=` label filter is kept for API callers.
2. **The three pages are the All Partners table.** `/admin/partners/affiliate`, `/reseller` and `/white-label` render `AllPartnersList` with `category` fixed. The columns, filters, sorting, paging, row menu and module tabs are unchanged; only the title and the empty state name the type. "Partner type" now moves between those pages and All partners, so the filter always means the Type column. An old `/admin/partners/all?type=…`, the overview cards and the old plural addresses open the type's page. The overview's three type cards count what their pages list, so a White-label partner is no longer also counted as a Reseller.
3. **Partners ▼ in the sidebar** lists Affiliate, Reseller, White Label, then All Partners. All Partners was added so that "view all categories together" is one click. It is the only module that opens beneath itself; its tabs are unchanged. The staff member may reorder the entries: drag them, or press Alt with the Up or Down arrow (with instructions read out and the move announced). The order is kept in this browser's `localStorage` (`w360.admin.menu.partners`), like the fold of the menu. It is a preference only and changes no partner's type. There is no server-side store for staff UI preferences, so the order does not follow the person to another browser.
4. **Login as Guest is a record, not a cache entry.** `PartnerGuestSession` (partners.0019) stores:
   - the partner and the staff member (who acts);
   - the staff member's own sign-in id, the reason and the Admin page to return to;
   - the start and expiry times (30 minutes);
   - the SHA-256 of a one-time entry ticket, and when the ticket was spent;
   - how and when the view ended (exited, revoked, signed out, replaced, expired) and by whom.

   The database allows one open view per sign-in. Every guest request reads the row, so ending or revoking a view takes effect on the next request. Starting needs a new permission, `platform.staff_partner.guest`. It is dangerous: audited, and refused under impersonation, so a guest view can never start another. It implies `platform.staff_partner.view` and is checked again on every guest request. `authz.0011` gives it to each security administrator, as `authz.0010` did, so the live Admin keeps the action after an upgrade. A draft partner has no dashboard, so starting a view of one is refused.
5. **Reaching the partner host without a URL token.** In multi-portal mode the start answer carries `entry: {action, ticket}`. The ticket is 256 bits, single use and valid for sixty seconds, and is stored only as a digest. The Admin page posts it in a hidden form, as a top-level navigation, to `partner.`'s `/api/v1/partner/guest-view/enter`, so the ticket is never in a URL, the history, a log line or a Referer. That endpoint is CSRF-exempt and replaces CSRF with two checks: the `Origin` must be the configured Admin origin, and the ticket must be good. `PortalSurfaceMiddleware` lets exactly that path, from exactly that origin, through its cross-portal write rule. The frontend's CSP gives the Admin host alone `form-action 'self' <partner origin>`. The endpoint checks again that the staff member is active staff holding the permission. It then opens a fresh session on the partner host for the staff member (never the partner) carrying:
   - the guest view's id;
   - the Admin's sign-in id, so signing out of the Admin ends it;
   - the view's end time;
   - no authentication time, so anything that needs a recent password refuses it.

   The response is a 303 to the partner's own dashboard (`guest.portal_path`). If anything is wrong, it is a 303 to All Partners with `?guest=expired|denied`. In single-host mode (development) the view attaches to the one shared session, as before.
6. **A guest-only session reads one partner and nothing else.** `GuestSessionMiddleware` reads the view on every request and flushes the session once the view is over. It refuses every unsafe method on every endpoint (partner, account, password) except "Exit Guest Mode" and signing out. `partner_view` answers such a session only with the guest context of the viewed partner, which is a 404 for every other partner, even one the staff member belongs to. `staff_context` and the staff-only diagnostics refuse it on any host, and the admin API is not served on `partner.` anyway. Beneath all that, the existing layers stay: the portal adapter refuses a guest's writes, the resolver gives the viewer tier only, and the decision function refuses dangerous permissions under break-glass.
7. **What a guest is not shown.** On top of the viewer tier, a guest gets:
   - the KYC application's state only (no details such as PAN, GSTIN or addresses, no document names; document bytes were already refused);
   - payout details as "on file" with their verification state, but no holder, bank, IFSC, account or UPI hint;
   - the business profile without registration and tax numbers;
   - no bulk export (already refused).
8. **The way out.** "Exit Guest Mode" ends the view (`exited`), flushes the partner host's session, and returns to the Admin page Login as Guest was chosen on. The server builds `return_url` from configuration and a validated path, and the frontend accepts it only on the Admin origin under `/admin/partners`. The Admin's own session lives on the Admin host and is untouched. Signing out on the partner host in Guest Mode ends that view (`signed_out`) and that session only (ADR-059). Signing out of the Admin ends both. Expired views are refused from their expiry; a sweep (`commercial.partners.close_guest_sessions`, every five minutes) writes their end once.
9. **Visible and auditable.** Start and end are audited against the partner (the partner's Activity shows them), with the guest view's id (`guest_view_id`: the audit log redacts any key containing "session"), the end reason and the actor. **Manage Guest Access** (row menu and the partner's page) lists the last 20 views: who, why, when, status. Anyone with `platform.staff_partner.view` can read the list; revoking needs the guest permission. The Guest Mode banner names the partner, its type and the staff member, gives the end time, and has a prominent Exit Guest Mode button.
10. **Rate limits:** starting is limited to 10 a minute per staff member and 30 per address; entering to 30 per address.

## Consequences

- Login as Guest works on the production host layout again, and now proves it in a real browser across the five hosts (`scripts/guest_login_check`, 65 checks).
- The Reseller list and card no longer include White-label partners. That is the intended change; it is visible on the overview's counts.
- A guest-only partner session shares its sign-in id with the Admin session, so activity in Guest Mode also keeps the Admin session's idle window sliding (`_slide_sign_in`), as any two sessions of one sign-in do.
- Open guest views held in the cache by the previous release are ignored after deploy; they did not work in production anyway.
- The partner's own buttons that would write (for example "Upgrade plan") are still drawn in Guest Mode; pressing one is refused by the backend with "Guest Mode is read-only."
