# Reseller and white-label setup: design

**Status:** built 2026-10-06. Decision record: [ADR-042](../architecture/adrs/ADR-042-partner-setup-profile-and-contacts.md).
**Read first:** [the Partner Control Center](../partner-center/status.md) (what already existed).

This is the design the Add Reseller wizard, the white-label setup wizard and the partner detail tabs were built from. It says what is stored, what is read, which endpoint carries it, and which rules every screen obeys.

## 1. The rule that decides everything

There is **one `PartnerAccount`**. "Reseller", "White-label" and "Affiliate" are labels the server derives from the capabilities that account holds (`capabilities.experience_labels`). Nothing in this work adds a partner type, a `ResellerPartner`, a `WhiteLabelPartner`, a reseller user or a second partner record, and a wizard only ever creates one account, then runs the *existing* commands against it:

| Wizard step | Existing command it runs |
|---|---|
| Create the partner (draft) | `create_partner` |
| Business details, contacts, support, payout preferences | `update_profile` (new, below) |
| Capabilities | `grant_capability`, one per key |
| Products | `grant_product` (now also carries two per-grant choices) |
| Partner pricing | `set_partner_price` through `pricing.publish_price` |
| Commission | `assign_partner_commission` |
| Administrator | `invite_member` (now takes a name and may defer the email) |
| Activate | `set_partner_status` |
| White-label | `enable_white_label` (the same two grants, together), `update_brand`, `upload_logo`, `request_domain`, `verify_domain`, `activate_domain` |

## 2. What is new in the data

Two small tables and four columns. Each says why it is not somewhere else.

| Addition | Why |
|---|---|
| `PartnerProfile` (one row per partner) | Business facts to reach and invoice a company: business email, website, phone, alternative email, registration and tax numbers, country, address (a closed-key JSON), customer-facing support email/phone/URL, payout frequency and contact email, staff-only notes. Kept off `PartnerAccount` so that table stays "only what every channel shares" and an affiliate carries no empty columns for a tax number. Nothing a decision reads. |
| `PartnerContact` (one row per partner and role) | Primary, billing, technical and support. **Not an account**: no password, no membership, no permission. A role with no row means "same as the primary contact", so a copy can never drift. The primary contact's name and email are mirrored onto `PartnerAccount.contact_name/contact_email` in the same transaction because the lists and setup checks have always read them there. |
| `PartnerProductGrant.trial_allowed` | May this partner's customers start the product's *existing* platform trial? It only narrows: no trial configured means none, and a trial that has started is untouched. Enforced where a customer starts a trial (`trial_views`) and in what Explore offers. |
| `PartnerProductGrant.portal_visible` | Is the product shown on this partner's white-label portal? Display only. It never grants or removes an entitlement; a customer who holds the product still launches it. |
| `PartnerBrand.portal` | A closed set of bounded plain-text fields and flags (`portal.py`): short name, support name and phone, website, welcome message, footer text, sender display name, reply-to address, and three on/off choices. Validated on write like `theme`; **no markup**: `<` and `>` are refused, links are plain `https` and never fetched. |
| `PartnerMember.invitation_sent_at` | When a setup link was last emailed. Null with no password means "named, not yet invited", which is what lets a draft partner's administrator be invited when the partner is activated. Backfilled from `added_at` for existing members. |

**Not added, on purpose.** A recommended retail price (no such concept in pricing), a minimum payout threshold (payouts have none), a second colour or favicon (the theme the front end renders has one derived accent), per-partner commission hold (it is a platform setting), trial, subscription and welcome emails (no such notification exists), a second trial engine, a second email system, bank details.

## 3. Reads and writes (staff, under `/api/v1/admin/partners/`)

All writes need `platform.staff_partner.manage`, which is *dangerous*: a second factor (AAL 2), an audit entry, and refusal under impersonation. Reads need `platform.staff_partner.view`. A partner session gets 403 on every one.

| Endpoint | Purpose |
|---|---|
| `POST reseller-review` | The dry run. Writes nothing. Returns every problem (wrong), `readiness` (missing before *activation*), `warnings`, `missing_optional`, and every figure the server computes: prices, discount, commission, platform net, a worked commission example, trial summary. |
| `GET/PUT {id}/profile` | Profile and contacts. `PUT` takes `expected_version` (the partner's version) and any of `business`, `contacts`, `support`, `payout`, `internal_notes`; only what is sent changes. Currency and code cannot be sent. |
| `PATCH {id}/product-grants/{grant}` | `trial_allowed`, `portal_visible`. |
| `POST {id}/administrators` | Now `first_name`, `last_name`, `send_invitation: "now" \| "on_activation"`. |
| `POST {id}/members/{member}/role` | Administrator or viewer. An active partner keeps one administrator. |
| `GET {id}/white-label` | One read for the whole wizard: labels, brand and portal, every catalog product with its four facts, the domain, and the authentication statement. |
| `POST {id}/white-label/enable` | Grants `branding.manage` + `domain.manage` together. |
| `PUT {id}/brand` | Now takes `portal`, **merged**: the keys sent replace those keys. |
| `GET {id}/summary` | Now returns `activation` (the readiness rule, shared with the review) and the new setup progress. |
| `GET {id}/activity` | Each entry now also carries `label`, `actor` and `target`. The screen shows only those (and the timestamp and reason); the recorded `changes` still travel with the entry but name fields, never values, and are never drawn. |

## 4. Rules every screen obeys

1. **The server computes money.** The browser turns typed text into integers (an amount into minor units, a percentage into basis points) and displays what comes back. It never adds, rounds, converts, or works out a price, discount, margin, commission or example.
2. **Problems and readiness are different.** A *problem* is something wrong and blocks everything. *Readiness* is what is missing before **activation**; a draft may be saved without it. Required vs optional is shown on every field.
3. **Save Draft creates an inert partner.** A `draft` partner can do nothing until activated, so an incomplete one creates no commercial state. The Overview then lists exactly what is missing and links to the tab that fixes it.
4. **Activation is the dangerous act.** It is offered only when the review says `can_activate`, and it runs through `set_partner_status`, which still demands an administrator.
5. **A custom domain shows the brand; sign-in stays Workeasy360's.** The honest statement is returned by the server (`authentication`) and shown verbatim (ADR-039).
6. **No raw backend errors.** Server problems arrive as `{field, message}`, already written for a person, and are shown on the field they name.
7. **No markup, anywhere.** Every string is drawn as a React text node. The portal fields are plain text; the preview is drawn from the draft in memory and stores nothing.
8. **No lifecycle deletion.** A partner moves draft → active → suspended → terminated. There is no delete for a partner with history.

## 5. Front-end files

| Concern | Files |
|---|---|
| Wire types and calls | `lib/api/partnerConsole.ts`, `lib/api/controlPlane.ts` |
| Shared profile form model and fields | `features/controlplane/profileDraft.ts`, `ProfileForms.tsx`, `countries.ts` |
| Shared widgets (additive: `showRequired`, `RequiredMark`, `TextAreaField`) | `features/controlplane/widgets.tsx` |
| Add Reseller wizard | `features/controlplane/AddReseller.tsx` |
| White-label wizard | `features/controlplane/WhiteLabelSetup.tsx` (staff), `WhiteLabelWizard.tsx` (the partner's own portal, unchanged) |
| Partner detail | `AdminPartners.tsx`, `ResellerTabs.tsx`, `PartnerProfileTabs.tsx` |
