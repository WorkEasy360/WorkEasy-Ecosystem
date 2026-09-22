# 06 — The Remote Entitlement API

The remote form of what `contracts/ports/entitlements.py` already is
in-process, keeping the same promise: a product asks whether an organization
may use it and gets an answer. It never learns whether that answer came from
a plan, a bundle, a trial, a partner package or a support grant, what anything
cost, or when it renews.

## 1. The call

`POST /api/v1/integrations/entitlements/verify`

```
Content-Type: application/x-www-form-urlencoded

organization_id=b21c…
write=false
+ client authentication
```

```json
{
  "decision": "allow",
  "reason": "entitled",
  "product_code": "crm",
  "organization_id": "b21c…",
  "write_allowed": true,
  "checked_at": "2026-09-22T10:14:02.117Z",
  "revalidate_after": 900
}
```

`Cache-Control: no-store` — an answer cached by an intermediary outlives the
revocation it was supposed to reflect.

## 2. There is no `product_code` parameter

The subject comes from the **authenticated client's registration**.

This is stronger than validating a supplied code against the caller's
identity, because there is nothing to validate: a CRM client asking about
Books *cannot express the question*. `test_a_product_cannot_ask_about_another_product`
sends `product_code`, `product`, `aud` and `scope` all set to `books` and
asserts the answer is still about `crm`.

## 3. Why form-encoded, not JSON

Client authentication travels as form fields — `client_assertion` per RFC
7523, or HTTP Basic per RFC 6749 §2.3.1. Accepting a JSON body would mean a
second way to authenticate a caller, and `private_key_jwt` has no standard
placement outside a form. Inventing a header for it would be a proprietary
authentication mechanism in a phase whose entire point is not having one.

One authentication path is worth more than a fashionable request body. The
response is JSON; the token endpoint has exactly the same shape.

## 4. Decisions and reasons

`decision` is `allow` or `deny`. `reason` is from a **closed, documented set**:

| Reason | With | Means |
|---|---|---|
| `entitled` | `allow` | The organization holds this product |
| `not_entitled` | `deny` | Never purchased, **or the organization does not exist** |
| `expired` | `deny` | Held and lapsed. Show a resubscribe path |
| `suspended` | `deny` | Held but suspended |
| `read_only` | `deny` (on `write=true`) | Reads permitted, writes are not |
| `no_seat` | `deny` | Reserved; see §7 |

These are **mapped** from the platform's internal `EntitlementReason` enum,
not passed through. That enum is a platform internal which commercial code may
extend freely; a product switching on a value that changed underneath would
break in production with no deploy of its own. The mapping is the versioned
contract; the enum behind it is not.

## 5. `write_allowed` is resolved, never assumed

A read-only answer does not imply a writable one. An entitlement in
`suspended` permits reads and refuses writes — exactly the state a customer
whose card failed is in, where the whole point is that they can still see and
export their data.

Reporting `write_allowed: true` because the *read* check passed would tell the
product to offer a save button that the platform then refuses, and the product
would look broken for a condition it was never told about.

So `EntitlementDecision` carries both, one read answers both, and
`test_write_allowed_is_resolved_not_assumed` pins it.

## 6. A platform failure is never a denial

**The most consequential behaviour in this module.**

If the entitlement read itself fails — database unreachable, snapshot table
unreadable, gate unregistered — the answer is `503 temporarily_unavailable`,
**not** `deny`.

A product that receives `deny` will tell a paying customer they have no
licence and may tear down their session. A product that receives 503 will
retry or honour its last known answer. Collapsing an outage into a denial
converts a platform incident into an ecosystem-wide false lockout.

`test_a_platform_failure_is_a_503_and_never_a_denial` asserts the response
does not contain the string `deny` at all.

Operational failures and legitimate denials stay distinguishable internally
too: denials increment `ecosystem_entitlement_verifications_total{decision}`,
failures increment `ecosystem_entitlement_unavailable_total` and log at
`error`.

### The read path, and why it is not `get_gate()`

`services.read.check` reads a tenant-scoped snapshot through the scoped
manager, so it needs an ambient `TenantContext` — meaning a user and a
membership. **There is neither here**: this is one server asking another about
an organization that may have nobody signed in at all. Fabricating an actor to
satisfy the manager would put an invented person's identifier into the logs of
every service-to-service check, which is the objection `audit.record_system`
already makes.

So this path uses `entitlements.selectors.decision_for_organization`, which
relaxes **only** the ORM layer, audibly, and still enters the organization's
database scope so RLS confines the read to one tenant. Layer 3 is never
relaxed: a bug there returns nothing rather than another tenant's row.

Both paths reach the same `services.read.decide()`, so the platform and a
product can never be told different things about the same entitlement.

## 7. Seats: a stated gap

**Seat enforcement does not happen at sign-in**, and that is a deliberate,
documented decision rather than an oversight.

The reasoning: `entitlements.has_seat()` cannot distinguish "this user has no
seat" from "this product is not seat-licensed". For a per-organization
product there are no seat grants at all, so blanket enforcement would deny
**every** user of **every** per-org product. Distinguishing the two requires
`LicensingMode`, which is commercial data that `platform/ecosystem` may not
read (F-4).

**Consequence:** an organization holding CRM with 5 of 5 seats assigned will
still authenticate user 6 into CRM. Seat enforcement inside the platform is
unchanged (`authz.check` step 5).

**And a product cannot enforce it on the platform's behalf either.** This is
the part worth stating explicitly, because "the product can handle it" is the
obvious assumption and it is wrong here: the ID token deliberately carries no
`membership_id` ([05 §4](05-token-claims.md)), and this endpoint takes an
organization and nothing else. There is therefore no field anywhere in the
remote contract in which a product could ask, or be told, whether *this user*
holds a seat. The gap is closed by the platform or it is not closed.

**Carried as P2** with two candidate resolutions: extend the entitlement
snapshot to carry a seat-licensed flag the platform can read without
commercial, or add a `no_seat` answer keyed on membership — which would also
require the token to carry a per-member identifier, or this endpoint to
accept a `sub`. The `no_seat` reason code is already reserved in the contract
so that adding it later is not a breaking change.

**Not a security gap.** A user reaching a product without a seat is an
authenticated member of an organization that genuinely holds the product;
nothing is escalated and no tenant boundary is crossed. It is a commercial
licensing gap, which is why it is P2 and not P1, and why it belongs with the
app launcher — the surface that already has to answer "which products can
*this person* open?" and render "ask your administrator for a seat".

## 8. What never crosses this boundary

`test_the_response_carries_no_commercial_detail` asserts the response text
contains none of: `plan`, `price`, `subscription`, `amount`, `currency`,
`invoice`, `renew`, `trial`, `seats`, `bundle`.

Nor does it disclose tenant existence. An unknown organization and a known
but unentitled one return **the same decision, the same reason and the same
field set** — any difference would make this an organization-enumeration
oracle for every product in the ecosystem.

## 9. Cadence

`revalidate_after` (default 900 s) is returned in **every** response, so the
cadence is the platform's to change rather than nine products' to each guess
differently. It is also advertised in the discovery document.

A product should:

- call this when a user first arrives;
- call it again when `revalidate_after` elapses;
- call it before a privileged or irreversible operation, with `write=true`;
- **not** call it per request — that is the tight coupling this design exists
  to avoid;
- on `503`, **continue honouring its last known answer** and retry.

## 10. Performance

Measured on the local stack, through PgBouncer, over 40 calls:

| | |
|---|---|
| p50 | **7.5 ms** |
| p95 | **8.3 ms** |
| p99 | **8.6 ms** |
| Query budget | ≤ 12, asserted, and flat in the number of registered integrations |

Full method and caveats in [13](13-performance-review.md).
