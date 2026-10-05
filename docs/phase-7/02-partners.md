# Partners: reseller, white-label and affiliate foundations

The model and its reasoning are in
[ADR-028](../architecture/adrs/ADR-028-partner-capability-model.md). This page
is how it behaves.

## Lifecycle

`draft -> active -> suspended <-> active -> terminated` (terminated is final).
Every transition is a staff action with a recorded reason, checked against the
row's `version` (a stale screen gets a conflict, not a silent overwrite).

| Status | Can transact | Can read history |
|---|---|---|
| draft | no | no |
| active | yes | yes |
| suspended | no (new actions refused, 403) | yes |
| terminated | no | commission and payout history only |

A partner needs at least one **admin member** to be activated. Members are
existing accounts added by staff; role is `admin` (everything the capabilities
allow) or `viewer` (read-only tier only). Removing a member ends their access on
their next request.

## Capabilities

Staff grant and revoke capabilities from the registry
(`commercial/partners/capabilities.py`); each grant records a reason, and grants
may expire. The portal exposes only what the capability set unlocks - a
"commission-only affiliate" and a "full reseller" are the same code with
different grants.

## The three shapes, as capability sets

| Shape | Typical capabilities |
|---|---|
| Affiliate | `referral.create`, `referral.view`, `commission.earn`, `commission.view`, `payout.view` |
| Reseller | `customer.provision`, `subscription.sell`, `pricing.view`, `commission.earn`, `commission.view`, `payout.view` (+ `customer.manage`, `subscription.manage`, `support.access`) |
| White-label | a reseller's set plus `branding.manage`, `domain.manage` |

These are conventions in this table only. Nothing in the code names them.

## Product scope and pricing

A partner may sell only products it has been granted (`PartnerProductGrant`,
whole product or one plan) **and** only plans in a sellable state. "Sell" means
every path that adds revenue: a new line, a plan change, **more seats** on an
existing line and **converting a trial** - each checks the live grant for every
line involved (final audit A-05; before, only the first two did). The
partner's price comes from a `PriceBook` whose `partner_id` names it
(`resolve_price` consults it before the standard books). The partner's catalog
screen shows the resolved price and its source; the sale request carries only a
plan-version id and a quantity.

## Provisioning a customer

`POST /partner/<id>/customers` (capability `customer.provision`, dangerous,
`Idempotency-Key` required) refuses an owner address that belongs to one of the
partner's own current or former members (self-dealing, final audit A-12), and
otherwise creates in **one transaction**: the organization, a
*pending* owner membership, the partner link, an ownership invitation for the
owner's email, and the customer summary. A retry with the same key replays the
first result. Nothing is created for an invalid request (validation happens
before the first write). The owner accepts the invitation at
`/organizations/select`; **the owner's credential setup is not built** (D-020).

## Selling

`POST /partner/<id>/customers/<org>/sell` re-checks, inside the transaction and
under the link lock: the partner is active, the link is active, the capability
is held, the plan is in the partner's product scope, and the customer has no
conflicting subscription. Price and interval are derived server-side and the
existing subscription service creates the subscription. Ending the link
concurrently with a sale is decided by the lock: the sale either completes
first or is refused - never half-applied.

## Customer independence

Terminating a partner ends its links (`ended_at`, status). The customer's
organization, memberships, subscriptions, entitlements and history are not
touched, and the customer keeps working. An ended relationship stays in the
partner's customer list as history only - the organization id and the dates,
none of the customer's current name, status, subscription or products (final
audit A-06). Commission on sales already made
remains owed; nothing accrues after the link ended.

## Branding

`PartnerBrand` holds display metadata only: brand name, accent colour (six-digit
hex), support email, https support URL, an optional logo. Nothing the partner
supplies is ever rendered as markup or written into a stylesheet; the accent is
applied by the browser through the CSSOM after strict validation, and an invalid
value falls back to the default theme. The logo is PNG or JPEG only (SVG is
refused - it can carry script), size-limited, virus-scan gated by the files
platform, and served same-origin. The layout, and every security prompt, remain
Workeasy360's.

## Custom domains

A partner requests a hostname and receives a TXT record to publish at
`_w360-verify.<host>`. `verify` asks DNS (dnspython, through a swappable
resolver, with timeouts) and records the outcome; **verified is not active** -
`activate` is a separate, explicit step, and only an active domain serves
branding - and only while the partner is active and still holds both
`domain.manage` and `branding.manage`: revoking either, or suspending the
partner, takes the host off air on the next lookup (final audit A-10). The platform's own domains and their subdomains can never be claimed;
several partners may request the same hostname but only one can reach verified or active (partial unique index; the loser is told `hostname_claimed`); a domain that fails re-verification
repeatedly is disabled by the scheduled re-check. `GET /public/branding?host=` resolves
brand from an explicit, canonicalised host parameter (the request's own
`Host`/`X-Forwarded-Host` headers are ignored, and the answer carries no
authority), returns nothing for an unknown host, and is rate limited. The logo
is served by `GET /public/branding/logo?host=` - same-origin, keyed by host and
never by file id, with `nosniff` and a sandboxing CSP.

## Referrals

A partner with `referral.create` makes codes. A visitor arriving with a code is
recorded (`Referral`, first touch: a later code never replaces an existing
attribution). When a signed-in user creates or converts to a customer, the
referral converts to the organization at most once. Codes are random,
unguessable, revocable, and rate limited; the public endpoint leaks nothing about
the partner beyond what the visitor was already shown. A partner's own current
or former member cannot convert its referral (self-referral, final audit A-12).
