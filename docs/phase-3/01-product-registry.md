# 01 — The Product Integration Registry

## 1. Two records, two questions

The registry is **not** the commercial catalog, and conflating them is the
first mistake available here.

| | Answers | Owned by | Changes when |
|---|---|---|---|
| `commercial.catalog.Product` | "What can we sell, and what does it cost?" | `commercial/` | Packaging changes |
| `ecosystem.ProductIntegration` | "How does this application securely connect?" | `platform/ecosystem` | A deployment changes |

They are joined by `product_code` and by nothing else — no foreign key, in
either direction, asserted by
`test_the_integration_is_separate_from_the_commercial_product`.

Three reasons, in increasing order of weight:

1. **F-4 forbids it.** `platform/` may not import `commercial/`, and a
   foreign key would put a commercial table on the authentication path.
2. **[16 §7](../architecture/16-ecosystem-product-boundary.md) specified it**
   — joined by code, "never by foreign key, for exactly the reason
   [01 §4](../architecture/01-domain-boundaries.md) gives for `ProductCode`
   being a string".
3. **The lifecycles genuinely differ.** A product is registered at deploy
   time, which is normally *before* the SKU is `available` — refusing to
   register against a `draft` product would force the commercial launch to
   precede the technical one, which is backwards.

### The link is still validated

Registering an integration for `crmm` instead of `crm` would create a client
that can never authenticate anyone: every entitlement check against a code no
plan grants denies, and the support ticket reads "SSO is broken" rather than
"the product code is misspelt".

So registration *does* check the catalog — through
`contracts/ports/catalog.py`, the runtime-registry pattern `get_gate()`
already establishes. `commercial.catalog` registers a `ProductDirectory` at
app-ready; the platform asks "is this a product?" and learns nothing else.
No import, no foreign key, no coupling.

## 2. The model

```
ProductIntegration
    product_code    unique   the ecosystem identity (crm, books)
    client_id       unique   random, 24 bytes url-safe
    status                   draft | active | suspended | retired
    manifest        JSON     the validated manifest, as accepted
    activated_at, suspended_at, suspension_reason, retired_at
    created_at, updated_at
```

**One integration per product**, enforced by a unique constraint rather than
a check-then-insert — two client ids both claiming to be CRM would make the
entitlement answer depend on which one asked. Proven under concurrency by
`test_concurrent_registrations_for_one_product_produce_one_integration`.

**The client id is random, not derived.** A client id built from the product
code would let anyone construct another product's id and probe with it — and
it is also the `aud` claim, which should carry no meaning at all.

**The manifest is stored whole, and is the only source.** Redirect URIs,
scopes, the launch URL and the auth method are *not* also columns. Two copies
of a redirect-URI list is one copy too many, and the one that drifts is always
the one being checked.

It is reconstructed on read **without re-validation**
(`from_stored_manifest`). That is deliberate: re-applying the rules on every
read would mean *tightening* a rule silently breaks every existing
integration at runtime, with no deploy and no warning — turning a policy
change into an unannounced ecosystem outage. Tightened rules take effect by
re-registration, which is an authenticated, audited act.

## 3. Lifecycle

```
        ┌──────────────────────────────┐
        ▼                              │
   ┌─────────┐   activate   ┌────────┐ │ reactivate
   │  draft  │─────────────▶│ active │─┤
   └────┬────┘              └───┬────┘ │
        │                       │      │
        │            suspend    ▼      │
        │                 ┌───────────┐│
        │                 │ suspended ├┘
        │                 └─────┬─────┘
        │   retire              │ retire
        └──────────┬────────────┘
                   ▼
              ┌─────────┐
              │ retired │   terminal
              └─────────┘
```

| State | May authenticate? | May call service endpoints? | Notes |
|---|---|---|---|
| `draft` | No | No | Where a product lives while it is being built. Credentials are issuable, so integration can be tested against a real credential before anything is live. |
| `active` | Yes | Yes | |
| `suspended` | No | No | Reversible. Registration, credentials and **all commercial records** untouched. |
| `retired` | No | No | Terminal. Deliberately not a delete. |

**Nothing is active on creation.** A registration that could authenticate the
moment it was written would mean a mistyped redirect URI is live before anyone
has looked at it. `draft → active` is a separate, separately-audited decision.

**Suspension stops the product operating, not only signing users in.** It is
enforced at client authentication, so a suspended integration cannot redeem an
in-flight authorization code or read any organization's entitlement state
either — because the usual reason to suspend is that the product is
compromised.

**Suspension does not disturb commercial history.** The registration is an
ecosystem record; the entitlement is a commercial one. Asserted by
`test_suspension_does_not_disturb_commercial_history`, because a suspension
that looked like a cancellation on the customer's invoice would be a billing
incident.

**Retirement is terminal, not a delete.** The audit trail, the commercial
history and any future support question about "what was this client id?" all
outlive the integration.

Illegal transitions are **refused, never silently ignored**: an activation
that quietly does nothing is indistinguishable from one that worked, and the
difference is whether a customer can sign in.

## 4. Who may change it

Every mutation is gated twice, exactly as catalog administration is:

```python
authz.require(ctx, "platform.integration.<x>")    # role, and dangerous ->
                                                  # AAL 2, no impersonation
authz.require_platform_staff(ctx, operation=...)  # internal identity
```

Either alone is one misconfiguration away from a customer administrator being
able to register an application against the platform, mint a credential for
it, and read every organization's entitlement state.

| Permission | Dangerous | Guards |
|---|---|---|
| `platform.integration.view` | **No** | Listing. Client ids and redirect URIs are public by construction — a client id is in every authorization URL — and gating the read would push operators to keep a copy somewhere worse. |
| `platform.integration.register` | Yes | Creating an integration |
| `platform.integration.update` | Yes | **Changing a redirect URI** — a full account-takeover primitive against every user of that product |
| `platform.integration.lifecycle` | Yes | Activation (turning on a product nobody reviewed) and suspension (an ecosystem-wide outage for that product's customers) |
| `platform.integration.credential` | Yes | Minting a credential that authenticates *as* the product |

There is **no** `platform.integration.key` permission: signing-key rotation
has no HTTP surface at all ([08](08-key-management.md), ADR-020).

## 5. Audit

| Action | Audit entry | Records |
|---|---|---|
| Register | `ecosystem.integration.registered` | product code, client id, redirect URIs, scopes |
| Update manifest | `ecosystem.integration.manifest_updated` | redirect URIs and scopes **before and after** |
| Activate / suspend / retire | `ecosystem.integration.<status>` | from, to, reason |
| Create credential | `ecosystem.credential.created` | kind, credential reference, expiry — never the secret |
| Revoke credential | `ecosystem.credential.revoked` | credential reference, reason |

The before/after on redirect URIs is the one to note. It exists so an
unauthorised change is **visible** in the trail rather than merely implied by
it — "someone updated the manifest" is not an incident report.

Written against the acting staff member's organization context, the same
shape Phase 2 established for catalog administration.

**Not audited here:** authorization denials and token issuance failures at the
OIDC endpoints. They have no tenant context — there is no session at the token
endpoint — and `AuditEvent` is tenant-scoped and customer-readable. An entry
with no tenant has no customer to show it to. They go to the security-event
stream ([11-observability.md §9](../architecture/11-observability.md)) with
metrics, which is where that data belongs.

## 6. Health metadata

`health_url` is stored and **never fetched**. The platform performs no
outbound request to any product-supplied URL, asserted structurally by
`test_the_ecosystem_module_makes_no_outbound_http_request`.

Product health is Tier 3 by definition and may never influence
`/healthz/ready` — `test_product_health_never_affects_platform_readiness` and
`test_readiness_does_not_touch_the_integration_registry` hold that.
