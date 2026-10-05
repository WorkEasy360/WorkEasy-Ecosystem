# Frontend: `/admin` and `/partner`

No new dependency was added. The screens use the existing CSS-module approach,
the existing `apiFetch` (no-store, CSRF header), and the existing MFA step-up
hook (`useStepUp`).

## Routes

| Route | Screen | Nav shown when |
|---|---|---|
| `/admin` | Overview | always (staff) |
| `/admin/customers`, `/admin/customers/[orgId]` | Directory, detail with lifecycle, subscriptions, overrides, diagnostics, audit | `staff_customer.view` |
| `/admin/products` | Catalog | `staff_catalog.view` |
| `/admin/partners`, `/admin/partners/[partnerId]` | Partners, capabilities, members, product grants | `staff_partner.view` |
| `/admin/commissions`, `/admin/payouts` | Rules, ledger, reconciliation; payouts | `staff_commission.view` |
| `/admin/diagnostics` | Access diagnostics | `staff_support.view` |
| `/admin/audit`, `/admin/security` | Audit trail; staff grants | `.view` |
| `/partner` | Partner chooser | member of any partner |
| `/partner/[partnerId]/...` | Overview, customers, products, referrals, commissions, payouts, branding, domains | by **capability** |

Navigation is a convenience. A hidden link is not a permission; a visible one is
not either - every request is authorized by the backend, and a refusal is shown
in plain words.

## Customer detail

One screen answers "what is this customer's situation": lifecycle (suspend /
reactivate), subscription lines with suspend / resume / cancel-at-period-end
(each sends the row's `version`, so a stale screen gets a conflict), entitlements
and seats, **staff overrides** (grant a product or feature for 7, 30 or 90 days
with a reason; revoke with a reason), members, billing, the partner link, the
launch diagnostic, and the customer's recent audit trail.

## Patterns

- **One action flow.** `useAction` runs a mutation through `useStepUp`, so every
  dangerous action prompts for the second factor exactly once and retries; it
  shows a status message or an error, never a stack or an exception message.
- **Reasons are collected in the UI** (`ReasonAction`, minimum length) and
  recorded by the backend; the confirm button is disabled until the reason is
  long enough.
- **Idempotency keys are minted per intent**, held across a retry and renewed
  after success, so a double click or a flaky network replays rather than
  duplicates (tested for provisioning and payout preparation).
- **Money is display-only.** Amounts arrive as integer minor units plus a
  currency and are formatted; the browser never computes, sums or sends one.
- **Keyset pagination** ("Load more") everywhere a list can grow; searches run on
  submit, not per keystroke.
- **Empty, loading, forbidden and error states** are distinct and announced.

## Security

- **CSP-safe.** No `style=` attributes, `dangerouslySetInnerHTML` or `innerHTML`
  in these screens (asserted). Styling is CSS modules; the one dynamic value - a
  partner's accent colour - is validated as `#rrggbb` and applied through
  `element.style.setProperty`, which the CSP permits. Anything else falls back to
  the default theme. The existing D-010 `unsafe-inline` allowance for legacy
  inline styles is **not** widened.
- **Logo preview** uses a `data:` URL of the locally chosen, type- and size-checked
  file; SVG is refused before upload. Saved logos are served same-origin.
- **No secrets in the client.** The DNS verification value is public by design;
  nothing else sensitive is stored in `localStorage`.

## Accessibility and browser verification

Tables have captions and scoped headers, forms have visible labels, errors are
`role="alert"` and tied to their field, status is text before colour, and every
screen has an axe-core (WCAG 2.1 A/AA, non-visual rules) test in jsdom.

**Real browser.** jsdom paints nothing, so contrast, focus visibility and reflow
were checked in real Chrome against the **production build** and a real Django
backend (`scripts/phase7_browser_check/`, not CI, no new dependency): 20 pages
(11 admin, 9 partner, each signed in through the real login form).

| Check | Result |
|---|---|
| Content-Security-Policy violations | **0** |
| Console errors | **0** |
| axe, WCAG 2.1 A + AA, including colour contrast | **0 violations** on all 20 pages |
| Keyboard: first 40 tab stops per page show a focus indicator | **all do** |
| Horizontal page scroll at 320px | **none** after the fix below |
| Partner accent colour: valid hex applied through the CSSOM, hostile value not applied and Save disabled | as designed, 0 CSP violations |

The reflow check found real defects on 11 of the 20 pages (a page scrolled to
525-582px wide at a 320px viewport): grid items take their content's minimum
width, so one wide table or long identifier stretched the whole page, and the
shared header would not shrink for a long partner name. Fixed with
`minmax(0, 1fr)` grids and a wrapping header brand group; the tables now scroll
inside their own container.

**Re-run by the final audit** (2026-09-24, production build, 20 pages): 0 CSP
violations, 0 console errors, 0 axe violations (incl. contrast), every tab stop
visibly focused, no horizontal scroll at 320px - the reflow regression stays
closed.

**Keyboard operation (final audit).** `keyboard_check.js` drives the flows with
Tab, typing and Enter only - sign-in, navigation, **payout approval**, a
**time-boxed staff grant** (the new expiry date field), a **commission rule**,
and a partner's **custom-domain request** - including the real MFA step-up,
completed from the keyboard. It found one defect: after an action that ran
through the reason prompt or the MFA prompt, both of which unmount on submit,
**focus fell to `<body>`**, sending a keyboard or screen-reader user back to the
top of the page (WCAG 2.4.3). `useAction` now moves focus to the announced
outcome in exactly that case (never when focus is somewhere the user put it).
After the fix: 28 of 28 keyboard checks pass. Commission *adjustments and
reversals* have no UI (staff use the API), so there is no keyboard flow for them.

The final audit also derived the sign-in button's label colour from the
partner's accent (WCAG contrast), so an accent such as `#ffffff` can no longer
make the label unreadable, and made a failed sign-out say so instead of
navigating to `/login` (which would resume the still-live session).

**Not executed:** a manual screen-reader pass (NVDA/VoiceOver). It needs a
person and assistive technology this environment does not have.

## Bundle

No dependency was added (no chart library, admin framework or state library;
tables and forms are plain elements). Measured by building the same tree with and
without the `/admin` and `/partner` routes: total client JS and CSS grew from
711,256 B to 1,157,335 B raw (224,410 B to 342,309 B gzip). Next.js splits by route,
so the cost falls on staff and partner users only; the launcher and sign-in pages
gain only the small branding hook and one CSS module. The delta is larger than
the line count suggests and was not broken down per chunk; if it starts to matter,
inspect the per-route chunks first (the shared screen helpers are the likely
candidate for hoisting).

## White-label sign-in

The sign-in page asks `GET /public/branding` for the current host. On a verified,
active partner domain it shows the partner's brand name and accent; anywhere else
(and on any failure) it shows the default. The sign-in form itself, its errors
and every security prompt are unchanged.
