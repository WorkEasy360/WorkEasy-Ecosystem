# Partner categories and Login as Guest: status (2026-11-10)

The owner's brief of 2026-11-10 ("Update Existing Partner Management UI & Add Login as Guest"). Decisions: [ADR-062](../architecture/adrs/ADR-062-partner-categories-and-login-as-guest.md). Committed as `74d94b0` on `feat/admin-panel` (pushed). **Live since 2026-11-10 21:00 IST as release `2026.11.10-74d94b0`**: partners.0019, tenancy.0017 and authz.0011 applied in production after backup `20261110T152406Z`; rollback target `2026.11.10-4cc247b` (the schema moved forward, so a full rollback means restoring that backup). Checked live: the Admin host's CSP allows the form post to `partner.` only; the entry refuses a missing or foreign Origin and a bad ticket; it is not served on `admin.`; no error in the logs; the Super Admin holds the new permission. The dev database was migrated the same evening (backup `20261110-2050-pre-guestlogin.dump`).

## What the audit found first

| Finding | What it meant |
| --- | --- |
| Login as Guest was broken in production since 2026-11-07 | The guest grant reached `partner.` only through the central hand-off, which no page has started since per-portal sign-in (ADR-059). With host-only cookies, the button opened the partner sign-in page. Tests passed because they ran on one host or still used the old hand-off. |
| "Partner type" overlapped | A White-label partner holds the reseller's selling grants, so it was listed, and counted, under Reseller too. |
| A new draft had no type in the lists | Its workspace comes from grants it does not hold yet. |
| The Partner Email form was already live | Since 4cc247b (ADR-061). There was nothing to fix; it is re-verified below. |

## Results

| Gate | Result |
| --- | --- |
| Backend: new tests (`tests/guest_login`, 3 files) | **50 / 50** |
| Backend: the whole suite (bare `pytest`, 5,384 tests, run in two parts) | **5,384 / 5,384** after fixes. Part one, 2,788 tests: 4 architecture pins failed, each asking for the new code to be declared (now declared with its reason); 7 tests errored only because of a `-p no:logging` flag; all 11 then passed. Part two, 2,594 tests: all passed. The guest, portal and pinned tests were rerun on the final code (251/251). |
| Frontend: vitest, whole suite | 1,565 / 1,565 (`form-popup.test.tsx` has a pre-existing focus race, about 1 run in 3, unrelated) |
| Static gates | ruff check and format, mypy, 18/18 import contracts, bandit, detect-secrets (no new finding), `manage.py check`, `makemigrations --check`, tsc, eslint: all clean |
| Real browser, separate hosts, production build | **65 / 65** (`scripts/guest_login_check`) |

The real-browser check found two bugs that the unit tests did not, both fixed:

- **New partner bounced to the Reseller list.** The `?type=` that chooses the form's type was taken for an old address.
- **The partner portal's avatar button failed axe** (`label-content-name-mismatch`).

## The brief's tests, item by item

| # | Brief item | Evidence |
| --- | --- | --- |
| 1-3 | Each type only under its own page | `test_categories.py` (every grant/stored-type combination, SQL = Python; the real three types; a new draft at once); browser C1 |
| 4 | All Partners still lists everyone | `test_categories.py`; browser C0 |
| 5 | The table design is unchanged | vitest "the same table on a type's own page" (columns); browser C2 (headers identical), C5 (tabs identical), screenshots |
| 6 | Filters and sorting still work | vitest (status, profile, KYC and search go to the server and stay on the page; Partner type moves between pages); existing sort tests unchanged |
| 7 | Login as Guest on every eligible partner | vitest row menu; browser G0; a draft shows it disabled (G1) |
| 8 | It opens the correct dashboard | `test_guest_entry.py` (three types); browser L1 |
| 9-10 | Enforced by the backend; no protected writes | `test_guest_entry.py::test_every_write_is_refused_on_every_endpoint` (partner, KYC, payout, members, API keys, the staff member's own password); `test_guest_rules.py` (KYC, payout and tax details hidden); browser L5 |
| 11 | No other partner's records | `test_another_partner_is_not_reachable`; browser L7 |
| 12 | Exit returns to the Admin session | `test_exit_ends_the_view_and_returns_to_the_admin_session`; browser X1-X3 |
| 13 | Expiry and revocation | `test_it_ends_by_itself`, `test_revoking_…`, `test_the_sweep_records_expired_views_once`; browser V4 |
| 14 | KYC, commissions, wallets, subscriptions and reports keep working | the affected-folder run above |
| 15 | Partner Email Address | existing `tests/admin_panel` and `tests/partner_management` (staff address refused, `created_by` stored); browser P1 |
| 16 | Records are not deleted or reclassified | categories are computed, not stored: no partner row changes (`test_a_new_partner_is_listed_under_its_type_at_once`) |
| 17 | Mobile and desktop | browser L9 (390 px) and the existing admin reflow checks; sidebar drawer unchanged |

## Owner steps

- None for the release itself. Merging `feat/admin-panel` into `main` is still the owner's step, as for 4cc247b.
- Login as Guest went to every security administrator (authz.0011); give it to anyone else from Staff access.

## Not done, and why

- **The menu order is per browser**, like the menu's fold. There is no server-side store of staff UI preferences, and adding one for this alone was not worth a new table.
- **Touch screens reorder with the keyboard only.** HTML drag and drop does not fire on touch.
- **Write buttons are still drawn in Guest Mode** (for example the partner's "Upgrade plan"). Pressing one is refused by the backend with "Guest Mode is read-only."
- **A manual screen-reader pass was not run.** axe checks are automated only.

## 2026-11-10 (later): Guest Login column replaces Earnings

The owner asked for a **Guest Login** column in the partner lists where **Earnings** was (All Partners, Affiliate, Reseller, White Label share the one table, so all four change together):

- Column order: Partner | Type | Status | Country | Currency | **Guest Login** | Last login | Joined | actions. Earnings are gone from the list only; the partner's own page, the commission ledger, wallets, payouts and every report are untouched (no backend change).
- Each active, suspended or terminated partner has its own green **Login ↗** button (tooltip "Login as Guest", spinner while it opens, the server's reason above the table if it refuses). A draft shows "Once active". The column is shown only to staff holding `platform.staff_partner.guest`; the server checks again.
- The Guest Mode banner now begins "Guest Mode – <partner name>".
- At 1366 px the row's ⋯ actions now need the table's sideways scroll (the button is wider than "₹0.00"); at 1920 px everything is in view.

Results: vitest 1,570 / 1,570; the backend tests that read frontend source 593 / 593; real browser 72 / 72 (the guest journeys now start from the Login button; new checks T1-T4 and B1).
