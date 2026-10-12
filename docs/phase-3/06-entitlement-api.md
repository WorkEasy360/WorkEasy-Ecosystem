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
subject=7f3e…            (optional, since 2026-10-12: the `sub` the product signed the person in with)
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

The product being asked about comes from the **authenticated client's
registration**.

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
| `no_seat` | `deny` (with `subject`) | A per-seat product, and this person holds no seat; see §7 |
| `membership_inactive` | `deny` (with `subject`) | This person may no longer use the organization: account disabled or locked by an administrator, membership suspended or removed, or not a member at all; see §7 |

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

## 7. Seats and people: the optional `subject` (2026-10-12)

**Before 2026-10-12** this endpoint answered for the organization only, so a
product session outlived a removed member, a disabled account or an
unassigned seat until the person's next sign-in: the authorization and token
endpoints check all three (`authorization.py` steps 6 and 9), but a session
already open never asked again. This section used to carry that as a stated
P2 gap and named its fix: let this endpoint accept a `sub`.

**Now a product may send `subject`**, the `sub` of the ID token it signed the
person in with (ADR-044 addendum). The platform first answers for the
organization exactly as before; only if that is `allow` does it ask the
personal questions sign-in asks, through the same functions
(`authorization.person_access_reason`):

1. the account may still authenticate (`User.status` is `active`: an
   administrator's DISABLED or LOCKED ends it; the failed-password brake does
   **not** - it is a guess about a password, and folding it in would let anyone
   who knows an address end its owner's product sessions);
2. the membership in this organization is active;
3. for a per-seat product, the member holds a seat.

The answers are `deny` + `membership_inactive` (1 or 2) or `deny` + `no_seat`
(3). An unknown or malformed subject, and a person from another organization,
are `membership_inactive` too: one answer for "removed", "never a member" and
"no such person". The work behind those answers is the same as well (the
account and the membership are always both read; a malformed subject is asked
about as the nil UUID), so their timing does not tell them apart either
(`test_every_denied_person_costs_the_same_reads`). What remains is inherent:
`allow` and `no_seat` say the person is an active member, which is what a
product asking about someone it signed in needs to know. Only an authenticated
product client can ask, only about its own product, and only with user ids it
already holds.
The access policy (MFA, allow-list, session age) is not applied: it judges a
browser session at sign-in, and a server asking later has none to describe.

**Without `subject` nothing changes**, which is what a product asks for its
background work, where there is no person.

**Not covered:** "sign out of all sessions" for a member, or a password reset,
does not reach product sessions through this question (it ends Workeasy360's own
sessions; products have no front- or back-channel logout, ADR-044). Removal,
suspension, an administrator disabling or locking the account, and an
unassigned seat do.

**Cost.** One account read, the membership resolution and (per-seat products)
the seat read, on top of the organization's answer: measured below.

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
- send `subject` for a signed-in person's requests, so their removal reaches
  their open session within the window, and leave it out for background work;
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

**2026-10-12, re-measured** with `test_report_entitlement_verification_latency`
(test settings, a busy development machine, through PgBouncer, 40 calls each):
the organization question p50 36 ms / p95 49 ms, the per-person question
(`subject`) p50 71 ms / p95 116 ms. The absolute numbers are this machine's;
the ratio - the personal half roughly doubles the work - is what
`docs/universal-sso/capacity.md` uses.
