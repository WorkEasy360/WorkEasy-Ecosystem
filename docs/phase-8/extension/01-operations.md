# Phase 8 extension — operations

## 1. Deploying

The extension adds ten migrations (`identity 0003`, `organizations 0005`,
`notifications 0002`, `authz 0008`, `catalog 0002`, `pricing 0003`,
`subscriptions 0003`, `payments 0001`, `tenancy 0009`, `tenancy 0010`). None
needs a superuser step. Run them as the migrator role, as for every phase:

```
python backend/manage.py migrate
```

What changes for users on deploy:

- **Everyone is signed out once.** Sessions move to the platform session
  registry (`identity_session`); rows in Django's old `django_session` table are
  no longer read. They can be deleted at leisure.
- Existing organizations get an empty access policy (no allow-list, no
  required two-step verification, no session cap, no raised password minimum),
  so nobody is refused anything they were allowed before.
- Members with the Billing Manager role can now read the organization's
  subscription (view only).
- Invitation rows take safe defaults for the new columns (role `member`, sent
  once). The Phase 1 invitation model had no writer before this extension, so
  a production database normally has none.

Rehearsed by `scripts/rehearse_extension_upgrade.py` (fresh database, Phase 8 →
extension upgrade with byte-level checksums of every pre-existing column,
rollback and re-apply).

## 2. New secrets

| Variable | Required | Rule |
|---|---|---|
| `W360_NOTIFICATION_SEAL_KEYS` | production | Comma-separated; each ≥ 32 characters; distinct from every other platform key (start-up refuses otherwise). The first key seals, every key opens: rotate by prepending a new key and remove the old one once queued notifications have been delivered (a day is ample). A lost key makes queued invitations undeliverable (they are dead-lettered and the link wiped; resend the invitation). |
| `W360_PAYMENT_CREDENTIAL_<REF>` | per payment provider | Stripe: the secret key. Razorpay: `key_id:key_secret`. |
| `W360_PAYMENT_WEBHOOK_SECRET_<REF>` | per payment provider | The provider's webhook signing secret. |

`<REF>` is the provider's `credential_ref` / `webhook_secret_ref`, upper-cased,
with any character that is not a letter or digit written as `_`. Values are
read per call, so rotating one is a secret-manager change with no deploy.
Only the variable **name** is ever logged.

## 3. Setting up a payment provider

1. Put the two secrets in the secret manager for the environment.
2. Super Admin → Billing → Payments → add a provider (or
   `POST /api/v1/admin/payments/providers`, `platform.staff_payment.manage`,
   AAL 2): `key` (1–32 lowercase letters, digits, `-`; it becomes part of the
   webhook URL), `adapter` (`stripe` or `razorpay`), `mode` (`test`/`live`),
   `credential_ref`, `webhook_secret_ref`, `currencies` (ISO codes it collects),
   `priority` (lower wins when several serve a currency). It is created
   **disabled**.
3. At the provider, register the webhook URL
   `https://<API origin>/api/v1/public/payments/webhooks/<key>` with these events:
   - Stripe: `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
     `checkout.session.async_payment_failed`, `checkout.session.expired`,
     `refund.created`, `refund.updated`, `refund.failed`.
   - Razorpay: `payment_link.paid`, `payment_link.expired`,
     `payment_link.cancelled`, `refund.processed`, `refund.failed`.
4. Enable the provider. Enabling is refused until both secrets are present.

The staff page shows whether each secret is configured, never its value.

**Development:** `W360_PAYMENTS_FAKE_PROVIDER=true` (the default when `DEBUG`)
allows the `fake` adapter: a "hosted page" served by the platform itself that
completes the payment through the real webhook path. Production pins it off,
and pins `PAYMENT_PROVIDER_ENDPOINTS` empty.

## 4. Scheduled jobs

| Beat entry | Task | Every | Does |
|---|---|---|---|
| `payments-process-due` | `commercial.payments.process_due` | 5 min (`W360_BEAT_PAYMENTS_S`) | issues missing invoices, expires stale checkouts (`PAYMENT_ATTEMPT_TTL_MINUTES`, 60), moves overdue subscriptions through past due → grace (7 days) → suspended (21 days) |
| `administration-prune-activity` | `platform.administration.prune_activity` | daily (`W360_BEAT_IDENTITY_PRUNE_S`) | deletes expired sessions, account events older than `ACCOUNT_EVENT_RETENTION_DAYS` (400) and member sign-ins older than `MEMBER_SIGN_IN_RETENTION_DAYS` (400) |

Payment provider calls (checkout, refund) run as due-work tasks, so a broker
outage delays them and the sweeper picks them up; nothing is lost.

## 5. Rate limits (per minute, `W360_RL_*` overrides)

| Bucket | Default |
|---|---|
| invitations sent, per user / per IP | 30 / 60 |
| public invitation preview and accept, per IP | 20 |
| password change, per user / per IP | 5 / 20 |
| session revocation, per user / per IP | 20 / 60 |
| test email, per user / per IP | 5 / 20 |
| payment start, per user / per IP | 10 / 30 |
| payment webhooks, per IP | 600 |

## 6. Runbook

| Symptom | Cause and action |
|---|---|
| A whole organization gets `network_not_allowed` | Its allow-list excludes where people now are (an office moved, a VPN changed). The editor could not have locked *themselves* out, but others can be. Super Admin → the customer → reset network policy (`platform.staff_customer.manage`, AAL 2, reason). |
| Members keep being asked to sign in again | The organization's session age limit, or an administrator signed them out. Both are visible in the organization's audit log. |
| Invitation emails stop arriving | Check the notification dead-letter queue; a seal-key change without keeping the old key dead-letters queued invitations — resend them. |
| Payment "Pay" spins and then says it could not start | No enabled provider for the invoice's currency (503), or the provider refused / was unreachable after retries. The staff webhook log and attempt list say which; secrets missing are logged by variable name. |
| Webhooks answer 400 `webhook_rejected` | Wrong or missing webhook secret, or clock skew over 300 s (Stripe). Deliveries are retried by the provider; nothing settles until one verifies. A *disabled* provider's webhooks are still accepted, so payments already in flight settle. |
| A refund shows `needs_review` | Razorpay did not confirm a refund submission (timeout / 5xx). Check the Razorpay dashboard; never resend blindly (debt D-030). |
| A customer paid but stays suspended | The suspension was not dunning's (staff applied it for another reason); lift it deliberately. |
