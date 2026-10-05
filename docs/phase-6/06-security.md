# Phase 6 — Security, isolation and privacy

## 1. What an attacker would try

| Attacker | Goal | Stopped by |
|---|---|---|
| A customer admin | Make the platform call an internal address (SSRF), read metadata credentials | The egress boundary: static + send-time address checks, IP pinning, no redirects (§5, [02](02-connectors-and-egress.md) §4) |
| A customer admin | Run code on the platform through automation | Conditions are data for a fixed interpreter; actions are a registry (§8) |
| A member of org A | Read or act on org B's integrations | RLS, composite FKs, 404-not-403 (§3) |
| A compromised product | Publish as another product, or as the platform | Namespace derived from the authenticated client, never from the request (§4) |
| A compromised product | Flood the platform or one organization | Per-client and per-(client, org) rate limits, size/shape bounds before parsing, per-rule execution caps |
| A network observer / receiver impersonator | Forge or replay a webhook | HMAC-SHA256 over timestamp + body, verified in constant time with a tolerance window ([03](03-webhooks.md) §3) |
| Someone with a database dump | Recover signing secrets | Sealed under a key that is not in the database, bound to their row (§6) |
| Someone with log access | Harvest secrets or payloads | Nothing sensitive is logged (§7) |

## 2. Permissions and step-up (Tasks 76–78)

| Permission | Dangerous (AAL 2) | Granted to |
|---|---|---|
| `platform.connection.view` | | owner, admin |
| `platform.connection.manage` | **yes** | owner, admin |
| `platform.webhook.replay` | | owner, admin |
| `platform.automation.view` | | owner, admin |
| `platform.automation.manage` | **yes** | owner, admin |

- Creating, editing, rotating, disabling and subscribing are `connection.manage`.
  Rule changes are `automation.manage`. Both are *dangerous*: at AAL 1 the
  API answers `step_up_required`, and the UI runs the existing Phase 5
  step-up flow in place (`test_managing_connections_requires_step_up`,
  `test_every_phase_6_manage_permission_is_dangerous`).
- **Replay is its own permission.** Replaying can re-send data, so an
  operator who can see deliveries cannot automatically cause them
  (`test_replay_needs_its_own_permission`).
- Removing an administrator removes their access at once. Permission sets
  are version-keyed (`test_removing_an_administrator_removes_their_access_at_once`).
- **Permission-cache fix.** The upgrade rehearsal found that a deploy
  which changes what system roles grant did not invalidate cached
  permission sets. Owners would have seen the new pages as 403 for up to
  5 minutes. The cache key now carries a fingerprint of the system-role
  catalog
  (`test_a_deploy_that_changes_system_role_grants_ignores_sets_cached_before_it`).

## 3. Tenant isolation (Tasks 79, 80)

All four layers from Phase 1 apply to every new tenant table:

1. **Fail-closed managers.** Every Phase 6 model with tenant data is a
   `TenantScopedModel`. W360.E001 fails the build otherwise.
2. **Explicit scoping.** Every service runs in the caller's `TenantContext`.
   Background work re-enters the organization's context from the id the
   timer carries, and re-reads the organization's live status.
3. **RLS with `FORCE`** on all 9 tenant tables:
   - `connectors_connection`, `connectors_secret`
   - `webhooks_subscription`, `webhooks_delivery`, `webhooks_attempt`
   - `automation_rule`, `automation_rule_version`, `automation_execution`
   - `events_routed`

   Verified from the catalog on a fresh database and on the upgraded Phase 5
   copy.
4. **Composite `(id, organization_id)` foreign keys** for every
   tenant-to-tenant reference (11 new). A row cannot point at another
   organization's row even through a bug that obtained its id
   (`test_the_database_refuses_a_cross_tenant_delivery`).

Further guarantees:

- **Existence is not an oracle.**
  - Another organization's connection, delivery or rule is `404`, exactly
    like a missing one
    (`test_another_organizations_objects_are_404_never_403`).
  - At ingestion, an unknown organization is indistinguishable from an
    unentitled one
    (`test_an_unknown_organization_reads_exactly_like_an_unentitled_one`).
- **The one cross-tenant table**, `scheduling_due_work`, holds ids and
  times only (D-017, ADR-024). It is never exposed through an API.

## 4. Inbound events: authentication and ownership (Tasks 8, 9, 60, 61)

- **The same client authentication as the token endpoint:**
  `private_key_jwt` or `client_secret_basic`, with the same credential
  lifecycle, and suspension honoured.
- **No browser session can publish.** The endpoint is CSRF-exempt *because*
  a cookie authenticates nothing there
  (`test_ingestion_ignores_a_browser_session`).
- **Namespace is structural:** `product.<authenticated product_code>.*`
  only. The platform-owned roots are unreachable from any product credential
  (`test_namespace_ownership_is_structural`). A manifest cannot declare
  another product's events
  (`test_a_manifest_cannot_declare_another_products_events`).
- **Undeclared, malformed or oversized events are refused before anything
  is written** (`test_malformed_or_undeclared_events_are_refused`,
  `test_an_oversized_event_is_refused_before_parsing`, `test_nan_is_not_json`).
- **Tenant binding** is the live entitlement decision, *for writes*
  ([01-event-model.md](01-event-model.md) §4).

## 5. Outbound calls: SSRF (Tasks 15–19, 87–89)

The full control list and the DNS-rebinding guarantees are in
[02-connectors-and-egress.md](02-connectors-and-egress.md) §4. In summary:

- one outbound HTTP module (enforced by an AST test);
- `https` only;
- every resolved address must be public;
- the connection is pinned to the validated address, with TLS verified
  against the hostname;
- redirects are never followed;
- hard timeouts and byte limits;
- no proxy variables honoured.

TLS verification cannot be turned off: there is no `verify=False` path
anywhere (`test_tls_verification_cannot_be_disabled`).

## 6. Secrets (Tasks 4, 30, 53, 82)

Signing secrets are 256-bit (`token_urlsafe(32)`, prefixed `whsec_`).

- **Storage:** Fernet under `W360_INTEGRATION_SECRETS_KEYS`.
  - The sealed plaintext is bound to `{organization, connection, version}`.
  - Versions are immutable (trigger).
  - `MultiFernet` supports master-key rotation.
  - Details in [02-connectors-and-egress.md](02-connectors-and-egress.md) §2.
- **Shown once:** in the create/rotate response, with `Cache-Control:
  no-store` (`test_a_secret_is_shown_once_and_never_again`). The UI keeps
  it in React state only: never `localStorage`, never a URL.
- **Rotation overlaps, then revokes**
  (`test_rotation_overlaps_then_revokes`). Concurrent rotations leave
  exactly one active secret.
- **Production refuses to start** if the key is missing, shorter than 32
  characters, or equal to another platform key.
- **Deviation from the target architecture (D-018).**
  [12-security.md](../architecture/12-security.md) §5 specifies KMS
  envelope encryption with a per-tenant data key. Phase 6 follows the
  Phase 5 MFA precedent instead: one application key from the secret store.

  | Mitigation | Effect |
  |---|---|
  | Row binding | stops ciphertext being copied to another row |
  | Distinct key | the key is not the Django key and not the MFA key |
  | `MultiFernet` | the key can rotate |

  The gap that remains: one leaked master key opens every organization's
  signing secrets. Moving to KMS-wrapped per-tenant data keys changes only
  `connectors/secrets.py` (`seal`/`unseal`).

## 7. Logging and audit (Tasks 66, 81)

**Never logged:**
- secrets;
- signatures;
- event payloads;
- full URLs (only the origin);
- response bodies.

A test captures every log line of a create → activate → signed delivery →
rotate → delivery signed with both secrets flow. It checks for both secrets,
all three signature values and the URL's path token
(`test_no_secret_reaches_a_log_line`).

**Audited** (tenant audit log, actor + AAL), **without secret material**:
- `integration.connection.created`, `.updated`, `.activated`, `.suspended`,
  `.disabled`, `.secret_rotated`, `.previous_secret_revoked`;
- `integration.connection.auto_suspended` (a system actor);
- `integration.subscription.changed`;
- `integration.delivery.replayed`;
- `integration.automation.created`, `.updated`, `.activated`, `.paused`,
  `.archived`.

Evidence: `test_control_plane_changes_are_audited_without_secrets`.

## 8. Automation cannot execute code (Tasks 33, 34, 36)

Conditions are JSON evaluated by a fixed interpreter:
- seven operators;
- bounded paths, with `__dunder__` segments refused.

There is no expression language, template engine, `eval` or regex from
customers (`test_automation_evaluates_no_code`). Actions are registered
`ActionSpec`s whose effects are rows created through existing services.
There is no "HTTP request" action, so automation cannot reach a URL that
did not pass connection validation.

## 9. Encryption at rest for routed event payloads — decision

`RoutedEvent.payload` holds a product's event data for up to 30 days, so
that deliveries, retries and replays can re-send it. It is **not
field-level encrypted**. The decision:

1. **The architecture reserves field-level encryption for a narrow set.**
   [12-security.md](../architecture/12-security.md) §6 limits it to
   high-value, rarely queried credentials and identifiers (connector
   credentials, MFA secrets, payout details, payroll identifiers), and
   adding to the set requires an ADR. Integration signing secrets **are**
   in that set and are sealed (§6). Event payloads are ordinary business
   data, the same class of data the producing product holds in its own
   database.
2. **It would add little over what is already there.**
   - Disk, snapshot and backup encryption (§6 of the target architecture);
   - RLS with `FORCE` and composite keys;
   - a fixed 30-day retention, never extended while nothing references the
     event;
   - no payload in logs, audit entries or notifications.

   Field-level encryption would protect only against a reader of live
   table data who does not also hold the application's keys. The platform
   must decrypt the payload on every attempt anyway, so the keys would sit
   on the same workers.
3. **Products are told what not to send.** The integration guide
   (`01-event-model.md` §3) makes events notifications, not data transfers:
   - bounded to 64 KiB;
   - no files;
   - identifiers rather than documents.

   A product must not put credentials or special-category personal data in
   an event. That is the producer's classification decision, and the
   platform does not interpret payloads.

**Revisit if:**
- retention must grow beyond 30 days;
- a product needs to route special-category data;
- a customer contract requires tenant-held keys.

The change would be local to `events/services.record_routable_event` and
`events/selectors.webhook_body`.

## 10. Privacy (Tasks 58, 59, 83)

- **Data minimisation.**
  - A webhook sends the product's payload only to endpoints the
    organization itself configured.
  - `notification.send` e-mails carry the rule name and event type, never
    payload.
  - Condition paths cannot address envelope internals.
- **Retention** per [05-failure-and-operations.md](05-failure-and-operations.md) §5.
  Delivery history and response excerpts are purged with their deliveries.
- **Purpose limitation.** The platform never uses event payloads for
  anything but the organization's own subscriptions and rules. There is no
  analytics on payloads and no cross-tenant aggregation.
- **Tenant deletion.** Every Phase 6 tenant row cascades from its
  organization, as every earlier tenant table does. No tenant purge
  workflow exists yet on the platform (unchanged from Phase 5).
