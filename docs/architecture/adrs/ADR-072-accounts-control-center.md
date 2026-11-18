# ADR-072: The Accounts Control Center on WorkEasy360 Accounts

- Status: proposed (2026-11-19). Branch `feat/accounts-control-center`, stacked on PR #9 (`4b183c6`). Not deployed.
- Context: the owner's brief for the WorkEasy360 Accounts Control Center (19 November 2026), and `review/PLATFORM-CAPABILITY-AUDIT.md` section H in the Accounts repository.
- Builds on: ADR-043 (hosts and sign-ins), ADR-044/066 (product sign-in, Switch Account), ADR-067 (the `accounts.` name), ADR-069 (back-channel logout), ADR-070 (domain ownership), ADR-071 (Accounts hosts identity).
- Amends: ADR-071's "Not done here: a dedicated profile editor" - there is one now, for the name only.

## Context

ADR-071 put a person's account page on WorkEasy360 Accounts: who is signed in, links to their organizations, and the security panels reused from User Admin. The owner asked for a Zoho-style control center there: the person's products across all their organizations, the products they have signed in to, where they are signed in, their sign-in history, their organizations, security and settings - and a Super Admin view of the same mapping.

The audit (section H) found what the platform could and could not say:

- `/api/v1/orgs/*` is served on `app.` only, so Accounts could not show products without a person-level endpoint.
- `ProductSignIn` (ADR-069) is per platform sign-in, for back-channel products only, has no organization by design, and is purged 30 days after its sign-in ends. It cannot be the record of "has this person ever signed in to this product, in this organization".
- No signal says a product session is live. A product's own sign-out or its session timeout never reaches the platform.
- The person's own history stopped at 20 entries and had no product name for SSO sign-ins.
- **A security defect:** revoking one session deleted that row only. In multi-portal mode one sign-in has a session on Accounts and one on each portal it was handed to (the shared `sign_in_id`), so the same sign-in stayed alive on its other hosts while its products were told it had ended. "Sign out the other sessions" had the opposite slip: it ended the caller's own sign-in on its other hosts.

## Decision

1. **Behind ADR-071's switch.** Every new page and endpoint exists only while `W360_ACCOUNTS_IDENTITY` is on. Off - production today - `/account` is ADR-071's page exactly, the new pages are not found, the new endpoints answer 404, and the Super Admin user view is unchanged and unaudited. Two things apply in every mode, because they must:
   - the session-revoke fix (item 6);
   - recording (items 3 and 5), so history exists on the day the switch is turned on.

2. **Person-level endpoints, `/api/v1/auth/account/*`, on the Accounts host only** (`portal_surface`: a narrowing). No user id is ever accepted; each acts on the session's own person.

   | Endpoint | What |
   |---|---|
   | `GET products` | Every organization the person belongs to, their roles, and - where this session is admitted - the launcher's directory of it, Explore's trial facts and open setup. |
   | `POST products/<org>/<code>/launch` | Open: the server-built `launch_url?organization=<uuid>`, only when access is granted. |
   | `GET connections` | Connected Logins (item 4). |
   | `POST connections/<code>/sign-out` | Sign out of one product (item 4). |
   | `GET sessions` | Active Sessions, one row per sign-in (item 6). |
   | `GET history` | Login History, cursor-paged over the retention window, with product names. |
   | `GET`/`PATCH profile` | The person's name (and only the name). |

   **No second entitlement computation.** Each organization is visited the way the tenancy middleware admits a request to `/orgs/<slug>/` - the same resolver and the same checks (`tenancy.middleware.admission_denial`: membership, organization status, the organization's access policy, the write block for a launch) - and then asked the launcher's own questions: `launcher_views.directory_entries` (the app directory's assembly, now shared) and `launcher.services.launch` / `launch_route`. An organization whose policy refuses this session is listed as restricted, with the same code User Admin would answer, and nothing of it is shown or opened. The page classifies rows with My Products' own `productRows`, from the same three facts User Admin reads. Buying, billing, seats and members stay on User Admin and the Store; the page links there.

3. **`ProductConnection`, the durable record behind Connected Logins** (`ecosystem.0010`). One row per (person, product, organization), for every product:
   - `first_sign_in_at`, `last_sign_in_at` and `sign_in_count`, written at the token endpoint;
   - `last_activity_at`, written when the product calls `/integrations/entitlements/verify` with `subject` - only for an existing row (a product cannot create one by naming a subject), and at most every 5 minutes;
   - deleted with the account (cascade), and after `ACCOUNT_EVENT_RETENTION_DAYS` (400) with neither sign-in nor activity, by the hourly ecosystem prune.

   It is exempt from tenant scoping (`TENANT_SCOPE_EXEMPT`), for `AuthorizationCode`'s reason: it is written by two server-to-server endpoints with no tenant context. It is the person's, read only by that person or by staff with `platform.staff_user.view`. `organization_id` labels the sign-in; it decides nothing. Recording never fails a token or an entitlement answer: an error is logged and counted.

4. **Connected Logins say only what is known.** Per product: name, launch hostname and whether it is a verified domain (ADR-070), the person's `sub`, and per organization the launcher's access (purchased or trial, seat assigned or not), whether an SSO connection was established, when they last signed in and when they were last seen. The session line is about the **WorkEasy360 sign-in** that opened the product:
   - "still open" - a `ProductSignIn` of a live platform sign-in;
   - "signed out" - every such sign-in ended;
   - "unknown" - the product takes no back-channel logout.

   **Never "Active"**: the frontend's word list is tested against every state. "Sign out of this product" queues one ADR-069 logout naming `sub` only, so the product ends every session of the person, wherever it was opened. It closes that product's `ProductSignIn` rows, keeps every WorkEasy360 sign-in open, and is reported as `delivered`, `pending` or `failed` (or `unsupported` for a product without back-channel logout) after the same short wait "sign out everywhere" uses. The reason, `product_signed_out`, is operator-facing only; it lives in `ecosystem.connections`, not in `contracts.ports.sign_ins`, because no WorkEasy360 sign-in ends.

5. **Login History.** The token endpoint records a `product_sign_in` account event: the product code, plus the coarse network and device of the WorkEasy360 sign-in that authorized the code (the token endpoint itself is the product's server). Login History shows successful and failed sign-ins and product sign-ins, cursor-paged, with the product's catalog name; `scope=all` shows every security event. The short security trail (`/api/v1/auth/activity`, the panels on every portal, the Super Admin's latest 20) excludes product sign-ins, so it reads as before. `profile_updated` and `product_signed_out` are new events too (`identity.0010`, choices only).

6. **Revoking a session ends its whole sign-in** (every mode). `revoke_session` deletes every session of the named session's `sign_in_id`, and still tells that sign-in's products (`session_revoked`). A session of the caller's own sign-in on another host is refused as the caller's own session is ("use sign out"). `revoke_other_sessions` keeps the caller's sign-in whole. A session older than sign-in ids is its own sign-in. Regression tests: `tests/accounts_center/test_session_revoke.py`. Active Sessions lists one row per sign-in (device): its most recent session's device and network, its earliest sign-in time, its latest activity and how many portal sessions it holds.

7. **Account Settings.** `PATCH profile {full_name}` changes the display name through `identity.account.update_profile` (identity stays the only writer of `User`). The name is whitespace-collapsed, never empty, at most 201 characters (signup's 100 + 100), with no control characters. The sign-in email keeps ADR-066's confirmed change and the password its own form; recovery is the existing forgot/reset flow. **Switch account** on `/account` is ADR-066's: while Accounts hosts identity, `POST /api/v1/auth/switch-account` is also answered on the Accounts host, whose sign-in is the one that ends.

8. **Super Admin.** `GET /api/v1/admin/users/<uuid>` keeps `platform.staff_user.view`. With the switch on it adds:
   - `central_user_id`;
   - the person's seats per organization;
   - their product connections;
   - their sign-in history (latest 50, product names);
   - their sessions grouped per sign-in.

   There is no password, token, session key or full address: sessions carry public ids and network prefixes only. **Viewing is audited**: `staff.user_viewed` in the control-plane log, at most once per staff member, per person, per 15 minutes (`staff_users.VIEW_AUDIT_WINDOW_S`), so a reloaded page is one entry. Roles without the permission (Product Admin, Billing Admin) are refused, and their attempt writes no view entry.

## Consequences

- Turning `W360_ACCOUNTS_IDENTITY` on now also turns on the Control Center and the Super Admin view's additions; turning it off removes them. The recorded data stays either way.
- One query set per organization per page load (the directory, Explore, setup). A person with many organizations pays for each; the organization list is the chooser's own page of at most 100.
- A product that never asks about `subject` never has a "last seen". The page says "unknown" rather than inventing one.
- `ProductConnection` grows with (people × products × organizations) and is bounded by retention.

## Rejected

- **Calling `/api/v1/orgs/*` from Accounts**, or serving it there: it would widen the customer portal's surface onto the identity host.
- **Computing access again for the person-level page:** two answers to "can I open this?" would eventually disagree. The launcher's functions are called instead.
- **Extending `ProductSignIn`** with an organization or a longer life: it is the logout outbox's list of who must be told, and ADR-069 keeps it minimal on purpose.
- **Labelling a product "Active"** when its sign-in is open or it was seen recently: neither proves a session exists.
- **Auditing every view:** a support call would flood the log. Once per window says who looked, and when.
