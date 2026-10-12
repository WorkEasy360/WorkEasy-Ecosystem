# ADR-042: Partner setup stores a business profile and contacts in two small tables

**Status:** accepted, 2026-10-06
**Builds on:** ADR-028 (partner channel: one account, capabilities not types), ADR-039 (authentication stays canonical), ADR-041 (Partner Control Center)
**Design:** [docs/partner-setup/design.md](../../partner-setup/design.md)

## Context

An administrator must be able to set up a reseller or a white-label partner completely, without developer help: business details, several contacts, capabilities, products, pricing, trial availability, commission, payout preferences, the people who run the account, a brand, a customer portal, a domain. Most of that already had a home. Four things did not:

1. **Business facts.** A website, phone, registration and tax numbers, an address, customer-facing support details, payout preferences and staff notes. `PartnerAccount` carried a name, a legal name, one contact name and email and a currency, and its module says it holds "only what every channel shares".
2. **Several contacts.** Primary, billing, technical and support.
3. **Two per-product choices.** May this partner's customers start the platform trial, and is the product shown on the partner's portal.
4. **The words a white-label portal shows, and the sender identity of emails sent in the partner's name.**

There is also a lifecycle need: an administrator may save an incomplete partner and finish it later, and an administrator who is named while a partner is still a draft should be invited when the partner goes live.

## Decision

**No new kind of partner.** There is still one `PartnerAccount`; Reseller, White-label and Affiliate remain labels derived from capabilities. The wizards run the existing commands one at a time against that one account (`docs/partner-setup/design.md` §1).

**Two tables.**

- `PartnerProfile`, one row per partner: the business facts above, as columns, with the address as a closed-key JSON. Kept off `PartnerAccount` so it stays "only what every channel shares", an affiliate carries no empty columns for a tax number, and a business detail can change without touching the version a status change is serialised on. No decision reads a column in it.
- `PartnerContact`, one row per partner and role. A contact is text - name, title, email, phone - never an account: it has no password, membership or permission. A role with no row means "same as the primary contact", so a copy cannot drift. The primary contact's name and email are mirrored onto `PartnerAccount.contact_name/contact_email` in the same transaction, because the lists and setup checks have always read them there.

**Columns on existing tables.**

- `PartnerProductGrant.trial_allowed` and `portal_visible`. `trial_allowed` only ever narrows: a product with no platform trial has none whatever it says, and a started trial is untouched; it is enforced where a customer starts a trial and in what Explore offers. `portal_visible` is display only and never grants or removes an entitlement.
- `PartnerBrand.portal`, a closed set of bounded plain-text fields and flags, validated on write like `theme` (no `<`, no `>`, links are plain `https` and never fetched). The public part reaches a customer's browser; the sender identity never does.
- `PartnerMember.invitation_sent_at` and `invitation_deferred`. A person invited with "send when the partner is activated" is recorded as deferred; activation emails exactly those people, once.

**Per-message sender identity** goes through the existing notification platform as an optional `_envelope` (display name and reply-to) re-validated at delivery. The From address is always the deployment's. Only one partner-originated email exists today (the invitation to a customer's owner), so that is the only one wired; welcome, trial and subscription emails do not exist and are shown as not available rather than invented.

**Draft is a real state.** "Save draft" creates the partner in `draft` with whatever was entered. A draft can do nothing until activated, so an incomplete one creates no commercial state. *Problems* (something is wrong) block everything; *readiness* (something is missing) blocks only activation, and one rule (`controlplane/readiness.py`) answers it for both the review of a draft and the saved partner's Overview. The one thing the service itself refuses is activating a partner with nobody to act for it, as before.

## Consequences

- The set of partner models grew from nine to eleven. `test_the_partner_models_are_the_frozen_set` now lists them, and `tests/partner_setup/test_architecture.py` pins what the two new ones must not become: no partner-type column or model, no password, permission, role, price, rate, token or bank field, no import of pricing, commission, payments, subscriptions or entitlements.
- One additive migration (`partners.0004`): columns with `db_default` (so the previous release keeps inserting while it runs) and two `CreateModel`s. No data is rewritten.
- A partner's profile and contacts are personal data: staff-only, never in an event, never in the audit entry (which names fields), never on a partner or customer route.
- Money is unchanged and still the server's: the review's worked commission example is `domain.commission_amount` on a fixed 10,000 whole-unit purchase.

## Alternatives considered

- **Columns on `PartnerAccount`.** Rejected: dozens of nullable columns on the row every channel shares, and a contact edit would bump the same version a suspension is serialised on.
- **A JSON "profile" blob.** Rejected for the business facts: no per-field constraint, no query by country, and validation would live only in code. Kept for the address (closed keys, never queried alone) and the portal (a fixed set of display strings, like `theme`).
- **A generic CRM contact model.** Rejected: "do not build a CRM inside PartnerAccount". Four roles, text fields, no history, no activities.
- **A `PartnerType` or a `Reseller` table.** Rejected by ADR-028 and still wrong: a partner that is a reseller and later white-label and later an affiliate is one row with more capabilities.
- **Hiding a product from a partner's customers by removing its entitlement.** Rejected: visibility is presentation; changing what a customer holds is a commercial act with its own commands.
- **A second email system or an arbitrary sender address.** Rejected: only a display name and a reply-to travel with a message, validated again at delivery.
- **Enforcing "ready to activate" in `set_partner_status`.** Rejected for now: hundreds of existing flows activate minimal partners on purpose. The rule is shared by the review and the Overview, and the wizard offers Activate only when it holds; the service keeps its existing, narrower refusal.
- **Per-partner commission hold, a minimum payout threshold, a recommended retail price, a second colour or a favicon.** Not added: the platform has no such concept, so a field would store something nothing reads.
