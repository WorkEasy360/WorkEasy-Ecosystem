# Retention and privacy

## What a partner learns about a customer

A partner sees what it sold and whether the relationship is healthy - **not who
works there** (`partner_customers_page`; asserted by `test_privacy.py`).

| Visible to the partner | Never visible to the partner |
|---|---|
| Organization id and name | Owner's or any member's email, name, phone |
| Organization status, link status and dates | The member list or roles |
| Subscription status, product codes | Billing contact, tax reference, invoices |
| The partner's own price for a plan | The customer's audit trail |
| Its own commission entries (amount, currency, dates, reason code) | Which customer or sale a ledger row came from (no organization id in the partner view) |

Where a partner *supplies* personal data - the owner's email when provisioning -
it is data the partner already holds. It is stored on the invitation and the new
owner account, and is not echoed back in any partner response. A partner's
launch diagnostic (`support.access`) reports facts about a product for a customer;
it names no member unless the partner typed that member's address, and even then
returns only the address it was given.

Staff see more, by design and by permission: the directory shows the owner's
email to holders of `staff_customer.view`, and partner members' emails are
visible to holders of `staff_partner.view`. Referral capture stores a SHA-256 of
an opaque token and the code it followed - no IP address, no user agent, no
cookie value, no identity.

## What is kept, and for how long

| Data | Kept | Removed by |
|---|---|---|
| **Commission ledger** | Indefinitely - it is a financial record | Never (append-only). A partner's termination does not delete it |
| **Payouts and payout lines** | Indefinitely | Never |
| **Commission rules** | Indefinitely - each accrual names the version it used | Never |
| **Converted referrals** | Indefinitely - they are the attribution behind a commission | Never |
| **Unconverted referrals** | 90 days to convert (`W360_REFERRAL_CAPTURE_DAYS`), then 180 days after expiry (`W360_REFERRAL_RETENTION_DAYS`) | Daily `commissions-prune-referrals`, 1,000 per call |
| **Domain verification token** | While the claim is pending, verified or active (a live claim needs its proof for re-checks) | Cleared the moment a domain is disabled or fails; a request also expires after 7 days or 30 attempts |
| **Domain rows** | Kept (hostname, status history, who requested it) | Not purged; they are the record of who claimed what |
| **Verification attempts** | Counters only (`check_count`, `missed_checks`, `last_checked_at`, a failure *code*) - no provider output, no DNS payloads | Overwritten on the row |
| **Audit entries** | Per the platform audit policy (retained as a tombstone on tenant purge, D-004) | Platform audit retention |
| **Events / outbox** | `W360_OUTBOX_RETENTION_DAYS` (30) | Existing outbox pruning |
| **Customer summary** | A projection, rebuilt on demand | Overwritten each reconciliation |
| **Partner brand and logo** | While the partner exists | Replaced on save; see the debt note below |

Rules of thumb behind the table: keep what is legally or commercially required
(money, attribution, who-did-what); do not keep a temporary artifact once it has
served its purpose (an unconverted visit, a spent DNS proof); never store what
was not needed (IPs, tracking identifiers, provider responses).

## Known gaps

- **Orphaned logo uploads.** Replacing a logo leaves the previous file in object
  storage (small, PNG/JPEG only, ownership-tracked by `PartnerLogoUpload`, and
  reachable through no public endpoint once no brand names it). A sweep that deletes unattached
  uploads older than a grace period is not built; add it if uploads become
  frequent.
- **No customer-erasure flow for partner data** beyond the platform's existing
  tenant purge contract: the ledger keeps `organization_id` (an opaque id, no
  personal data) so a purged customer's commission history stays intact.
