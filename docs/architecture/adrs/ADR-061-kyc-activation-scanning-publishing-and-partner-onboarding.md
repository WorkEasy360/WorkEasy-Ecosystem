# ADR-061: Two-step partner activation, fail-closed KYC scanning, one publish checklist

Date: 2026-11-10. Status: accepted (built on `feat/admin-panel`, not deployed). Amends ADR-060 decisions 6 and 8; extends ADR-047/048 (Add Partner) and ADR-051 (Product Manager).

## Context

The owner's follow-up brief of 2026-11-10 reported bugs found while inspecting the Admin Panel on a local database, and asked for the partner onboarding and launch gaps to be closed. The audit behind this ADR found:

- **Three active partners, no approved KYC.** All three were activated on 16 to 27 October 2026, before KYC existed, and have no KYC row at all. They are legacy accounts, not a bypass. But the console said nothing about it.
- **One real bypass.** A parent partner could activate a draft sub-partner (`hierarchy.activate_sub_partner`), and that path never asked about KYC.
- **Approval activated the partner.** Approval was implicitly also the activation. The brief asks for "KYC approved + authorized Admin activation".
- **The New Partner form said "Admin email".** It promised an invitation "when you activate it", although invitations had been sent at creation since KYC arrived. It also reported "sent" when the email had only been queued.
- **Easy CRM published without an Application URL.** Its integration launches it, but the checklist demanded both URLs. A duplicate `crm` product with no plans reached the store through the status command, which never ran the checklist: products are created "listed".
- **The malware scanner was a stub.** It answered "clean" for every allowlisted file, including KYC PDFs.
- **Product trials looked like overrides.** Trials, subscriptions and staff overrides are different records. The Entitlements page called any access without a subscription an "Override", and the customer page did not list trials at all.

## Decision

1. **Two steps to an active partner.** KYC approval (`platform.staff_kyc.review`) records the decision, the reviewer, the date and the reason. It also records the requested capabilities, which confer nothing while the partner is a draft. Activation is a separate command for `platform.staff_partner.manage`, and it checks the approval again. Every path out of draft calls `kyc.assert_may_activate`: the staff status command, and now a parent activating a sub-partner. Draft, submitted, under review, correction required and rejected are all refused.
2. **Legacy partners are named, not changed.** A partner that is active or suspended without an approved application is labelled "Active before KYC" in lists and counted on the Applications page. It keeps its status, because marking it approved would forge a review.
3. **The Partner Email is the partner's own login.** It is also the contact address, unless a separate contact email is given. A mobile number is optional.
   - A live partner with the same name is refused unless the Admin confirms it is a different partner (`partner.allow_same_name`).
   - A live partner with the same contact address is refused.
   - An address with a pending partner invitation is refused, and the refusal names the waiting partner.
   - Both ways to give access stay. **Send an invitation** (the default) leaves the account without a password until the partner sets one from the link. **Set a password now** stores only its hash and, by default, flags the account so the partner must replace it at first sign-in: staff know the password they typed. The flag is written by `identity.lifecycle.create_account`, the one writer of accounts. No password is ever emailed.
4. **An invitation is described as what happened.** The create answer says `queued`, `deferred` or `not_sent`, never "delivered". The partner's member list then reads the email's delivery record: queued, sent (accepted by the mail server), retrying, failed or suppressed. A development server prints mail to its log, and the console says so.
5. **Accept Invitation is the partner portal's page** (`/partner/accept-invitation#token=`). It uses the same single-use reset token. Accepting signs nobody in and activates nothing.
6. **Draft partners open on their KYC page.** The portal never sends them to a workspace that says "no access".
7. **Continue as Guest is a static preview** (`/partner/preview`). It makes no API call, holds no session and grants no rights. Its figures are labelled as examples. A guest is never a partner. No guest sign-in endpoint exists: a real demo tenant would need shared credentials and real APIs, which the brief forbids.
8. **One publish checklist.** The checklist asks for:
   - a launch destination: an Application URL, the connected product's launch URL, or an app link;
   - approved return destinations, when the product signs people in with Workeasy360;
   - at least one active plan;
   - at least one sales channel;
   - plus the existing name, slug, category, short description, logo and "not archived".

   The HTTP status command checks it when a *listed* product is made available. The operator's `register_products` command and the catalog fixtures keep the unchecked path, because changing a deployment's seeding step could unpublish a live product. A product already published that fails the checklist is flagged "Published, but not ready" and is not unpublished behind anyone's back.
9. **KYC documents fail closed.** `KYC_MALWARE_SCAN_REQUIRED` is on by default, and production refuses to start with it off.
   - With it on, a KYC document is opened only after a real scanner (`FILES_MALWARE_SCANNER=clamd`, ClamAV over TCP with INSTREAM) has said it is clean.
   - The ClamAV adapter opens a socket, so it lives in `integration/egress/clamd.py` beside every other way out. The files platform sits below integration, so the adapter registers itself with `platform/files/scanning.py` at start-up (`EgressConfig.ready`). A scanner name with no registered adapter counts as no scanner.
   - No scanner, a scanner error, or anything but a verdict leaves the file quarantined.
   - An application cannot be approved while a current document is unscanned. Corrections and rejections stay possible.
   - `platform.files.rescan_quarantined` retries every five minutes.
   - Infected content is deleted and only the signature name is kept.
   - Development turns the rule off by default and says "not scanned" on every document.
10. **Ledgers refuse TRUNCATE.** The wallet ledger and the KYC decision and access logs refuse TRUNCATE (`tenancy.0016`), as the commission ledger already did.
11. **No future-dated usage.** A product's usage event dated more than five minutes ahead is refused. A plan's free allowance counts from the event's month, so a future date could otherwise draw on an allowance that has not started.
12. **Trials, subscriptions and overrides stay distinct.**
    - Entitlement rows already carry their source kind. The Entitlements page and the customer page now name it: subscription, bundle, staff override or product trial.
    - The customer page lists product trials. A trial past its end shows as ended, even before the scheduled job marks its row.
    - No subscription is fabricated to make counts match.
13. **A customer's partner can be assigned or changed by an Admin.** The current relationship ends and the new one starts in one audited transaction. The partner must be active and able to serve customers: an affiliate earns through referrals, so it is never a customer's partner. Commissions follow the relationship live at the moment of each sale, so a change rewrites none.

## Consequences

- **A KYC application cannot be approved in production until a ClamAV daemon is reachable.** This is a deliberate deployment blocker: add a `clamav` service and set `W360_FILES_MALWARE_SCANNER=clamd`.
- **A partner approved today is not active until someone activates it.** The dashboard's Pending Actions and the notification bell show "Approved partners to activate".
- **Invitation emails now link to `/partner/accept-invitation`.** Links already sent to `/reset-password` keep working.
- **One wallet race remains open.** The spent top-up refund check runs when the refund is requested. The money leaves the wallet when the provider confirms, so a customer who spends in between makes that confirmation fail. The safe fix is to hold the amount when the refund is requested, and credit it back if the refund fails. It is designed but not built, so it is not hidden: see `docs/admin-panel/status.md`.
