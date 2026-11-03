# ADR-057: Unified partner dashboard - one menu, level locks, helpdesk and withdrawals

Date: 2026-11-03. Status: accepted (built locally, not deployed).

## Context

The owner's specification (WE360-PRD-2026-V4.2) asks for **one** partner dashboard for every level - Affiliate (1), Reseller (2), White Label (3) - with the same fourteen sidebar entries. An entry a level does not include is either locked (an upgrade prompt) or hidden. It also asks for:

- period presets and period-over-period changes;
- a product-wise sales split;
- a two-way helpdesk with SLA tracking;
- a withdrawal queue with a ₹1,000 minimum, TDS (194H) and verified PAN and bank details;
- staff seat limits (Reseller 5, White-label unlimited, Affiliate none);
- a UTM builder;
- a persistent "Upgrade Plan" action.

The panel already composed three workspaces from one set of pages (ADR-047), each with its own consolidated menu (2026-11-02 and 2026-11-03).

## Decision

1. **One menu, computed at runtime.** `experiences.ts` builds the same fourteen entries for every workspace. An entry carries the `levels` that include it.
   - A level outside them sees the entry **locked**: it is drawn with a lock, opens the upgrade dialog, and its pages show an upgrade prompt.
   - A member whose permissions make an entry useless does not see it at all: upgrading would not help, so nothing is offered.
   - The level is the partner's largest workspace (`capabilities.partner_level`), never a stored field.
   - The workspace addresses stay (`/partner/<level>/...`); a contained level's address redirects to the partner's level.
2. **Locks are presentation.** Every page is still authorized by the backend from the grants. A locked page renders no data and makes no request for it.
3. **Helpdesk: one table, two directions.**
   - `platform`: any partner member asks Workeasy360.
   - `customer`: a member of a live customer organization asks its partner (the desk needs `support.access`).
   - Messages are append-only; internal notes are never shown to the asking side.
   - The SLA is two deadlines fixed at opening; whether one is missed is derived on read.
   - `PartnerTicket` is exempt from customer-tenant RLS for the same reason as the partner link and the ledger: a desk spans many organizations. Each side reaches it only through `partners.tickets`, from its verified context.
4. **Withdrawals reuse payouts.**
   - A request snapshots the whole available balance (the payout engine pays all cleared rows), the TDS and the destination.
   - Staff "Start payout" calls `payouts.prepare_payout`. Maker-checker approval and the manual "paid" confirmation are unchanged; the request then reads its state from the payout.
   - The account number and PAN are Fernet-sealed under their own optional key. Verification is a staff decision, because there is no KYC provider.
5. **Seat limits** are enforced where a partner adds a member (not staff), from `W360_PARTNER_STAFF_SEATS` by level.
6. **Charts** follow the data-visualization method:
   - Sales and commission share one money axis: two lines, never two y-axes.
   - The product-wise distribution is a segmented bar with a labelled legend, not a donut. A part-to-whole read at a glance is the segmented bar's job; the specification's figure shows a donut, and its text asks for a "segmented distribution chart".
   - Both charts have a table view or legend values.

## Consequences

- New tables (one migration, `partners.0016`): `partners_ticket`, `partners_ticket_message`, `partners_canned_response`, `partners_payout_method`, `partners_withdrawal_request`.
- New permissions: `partner_payout.request` (dangerous), `partner_desk.view` and `partner_desk.manage`.
- Bundles are **unchanged**. The specification gives every level the Affiliate module and White-label the downline. Staff grant these per partner today; putting them in the default bundles changes the upgrade and transition rules (ADR-047/049) and is left to the owner.
- The `NOT_IMPLEMENTED` entry `partner_tickets` is removed.
- Not built, and still recorded as gaps: GST invoices, automated bank and UPI payouts, penny-drop and PAN verification, WhatsApp and SMS broadcast, impressions, last-click attribution.
