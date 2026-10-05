# Phase 6 — Webhooks: subscriptions, delivery, signatures, replay

Implemented in `integration/webhooks` (subscriptions, deliveries, attempts,
pipeline) and `integration/connectors/providers/webhook.py` (the signed HTTP
request). Delivery goes out through `integration/egress` only (see
[02-connectors-and-egress.md](02-connectors-and-egress.md)).

## 1. Subscriptions

A connection is subscribed to a set of **event filters**. A filter has
exactly one of two forms:

| Form | Example | Matches |
|---|---|---|
| exact, versioned | `product.example.record.created.v1` | that type at that version |
| product namespace | `product.example.*` | every event the product publishes |

Nothing richer (no globs, no regexes), so matching an event is one indexed
`event_filter IN (<= 2 strings)` query at any subscription count.

A filter is accepted only if the organization may observe it
(`integration/events/catalog.py`): the product must be one the organization
is **entitled to now**, and the event must be **declared** in that product's
active manifest. Subscriptions are replaced as a set (`PUT`), idempotently;
the same filter twice on one connection is one row (unique constraint).
Changing subscriptions requires `platform.connection.manage` at AAL 2.

Only **active** connections receive fanned-out events. A draft or suspended
connection's subscriptions are inert; events are not backfilled into it.

## 2. What is sent

```
POST <connection URL>
Content-Type: application/json
User-Agent: Workeasy360-Integrations/1.0
X-W360-Event-Id: <event.id>
X-W360-Event-Type: <event.type>
X-W360-Delivery-Id: <delivery id>
X-W360-Delivery-Attempt: <n>
X-W360-Correlation-Id: <correlation id>
X-W360-Signature: t=<unix seconds>,v1=<hex>[,v1=<hex>]

{
  "schema": "workeasy360.webhook.v1",
  "delivery_id": "…",
  "event": {
    "id": "…", "type": "product.example.record.created", "version": 1,
    "source": "product.example", "organization_id": "…",
    "occurred_at": "…", "correlation_id": "…", "causation_id": null,
    "data": { … the producer's payload, unchanged … }
  },
  "automation": { "rule_id": "…", "execution_id": "…" }   // automation deliveries only
}
```

`schema` versions the body. It changes additively or gets a new value; a
receiver switches on it. The body never contains an internal database id,
the platform's bookkeeping, or any secret.

## 3. The signature — exact verification procedure

```
signed_payload = str(t) + "." + raw_request_body      # bytes
expected       = hex( HMAC-SHA256( key = signing_secret, msg = signed_payload ) )
```

- `signing_secret` is the whole string you were shown (`whsec_…`), as UTF-8.
- `raw_request_body` is the **exact bytes received**. Verify **before**
  parsing JSON — re-serialising a parsed body does not reproduce the bytes.
- There may be **two** `v1` values during a secret rotation's overlap
  window (24 h by default): one per valid secret. Accept if **any** matches.

A receiver must:

1. Parse the header into `t` and every `v1`.
2. **Reject** if `|now − t| > 300` seconds (replay protection; `t` is inside
   the MAC, so it cannot be changed without breaking the signature).
3. Compute `expected` and compare with each `v1` in **constant time**.
4. **Deduplicate on `event.id`** for at least the tolerance window, and
   ideally for as long as retries run (up to `WEBHOOK_DELIVERY_MAX_AGE_S`,
   3 days). Delivery is at-least-once (§5), so duplicates are legitimate and
   must be harmless.
5. Respond `2xx` quickly; do slow work asynchronously.

Reference verification, importing nothing from Workeasy360:

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    pairs = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = [v for k, v in pairs if k.strip() == "t"]
    sigs = [v.strip() for k, v in pairs if k.strip() == "v1"]
    if len(ts) != 1 or not sigs or not ts[0].isdigit():
        return False
    t = int(ts[0])
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)
```

```javascript
import { createHmac, timingSafeEqual } from "node:crypto";

export function verify(secret, header, rawBody /* Buffer */, tolerance = 300) {
  const parts = header.split(",").map((p) => p.split("=", 2));
  const t = parts.filter(([k]) => k.trim() === "t").map(([, v]) => v);
  const sigs = parts.filter(([k]) => k.trim() === "v1").map(([, v]) => v.trim());
  if (t.length !== 1 || !sigs.length || !/^\d+$/.test(t[0])) return false;
  if (Math.abs(Date.now() / 1000 - Number(t[0])) > tolerance) return false;
  const expected = createHmac("sha256", secret)
    .update(Buffer.concat([Buffer.from(`${t[0]}.`), rawBody]))
    .digest("hex");
  return sigs.some(
    (s) => s.length === expected.length && timingSafeEqual(Buffer.from(s), Buffer.from(expected)),
  );
}
```

The same procedure is executable in the test suite as
`backend/tests/factories/webhook_receiver.py`, which is asserted to import
no platform module.

HMAC-SHA256 from the standard library; nothing here is a custom
construction. The scheme is versioned by its key: a future scheme would be
`v2` alongside `v1`, never a change to what `v1` means.

## 4. The delivery state machine

```
pending ──claim──> in_progress ──2xx────────────────────> succeeded
   ^                  │  │ ──3xx/4xx/blocked/exhausted/──> dead
   │                  │  │   expired/disabled/unsendable
   └──── retry <──────┘  │ ──408/425/429/5xx/network────> retry
                         └─ lease expired ──> in_progress (attempt recorded "abandoned", n+1)
```

`succeeded` and `dead` are terminal. One `DeliveryAttempt` row per attempt
number, never updated (a database trigger refuses updates), unique on
`(delivery, attempt_number)`.

The pipeline (`integration/webhooks/pipeline.py`) never holds a transaction
across the network:

1. **Claim** (transaction 1): lock the delivery; skip if terminal, in
   flight under a live lease, or not yet due; **hold** without charging an
   attempt if the organization is suspended, the connection is suspended
   (pings excepted), or its circuit is open; **park** it (timer moved
   `WEBHOOK_THROTTLE_HOLD_S` to twice that ahead) if its connection already has
   `WEBHOOK_MAX_INFLIGHT_PER_CONNECTION` attempts in flight or its
   organization `WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION`; otherwise mark
   `in_progress`, `attempts + 1`, lease `WEBHOOK_LEASE_S`, push the timer past
   the lease, and read the event and **this connection's** secrets. Commit.
2. **Send** (no transaction): the connector builds the signed request; egress
   sends it.
3. **Record** (transaction 2): insert attempt *n*; update the delivery
   **fenced** on `status = in_progress AND attempts = n`; move or drop the
   timer; update the connection's health; **pull forward** the
   organization's least recently touched due delivery on a connection with
   room (timer due now, enqueued after commit), so parked backlogs drain at
   full speed without re-polling the shared timer. A worker whose lease expired finds
   its attempt already recorded as abandoned (unique violation) or the fence
   unmatched, and its result is discarded (`webhook_late_completions_total`).

## 5. Semantics: at-least-once, truthfully

- **Guaranteed:** no two workers send the same attempt concurrently; exactly
  one result is recorded per attempt number; no accepted event's delivery is
  lost — every delivery has a durable timer until it is terminal, and the
  sweeper recovers anything a crash or broker outage dropped.
- **Not guaranteed, and not promised:** exactly-once receipt. A response lost
  after the receiver processed the request, or a worker killed between
  sending and recording, produces a second request for the same `event.id`
  (proved by `test_a_database_failure_while_recording_is_recovered_as_an_abandoned_attempt`).
  Receivers deduplicate on `event.id`.

## 6. Retries

| Outcome | Result |
|---|---|
| 2xx | succeeded |
| 408, 425, 429, 5xx, timeout, connection refused/reset, DNS failure, TLS failure, malformed response | retry |
| 3xx (never followed) | dead (`rejected`) |
| other 4xx (400, 401, 403, 404, 410, …) | dead (`rejected`) |
| destination refused by the egress policy (SSRF) | dead (`rejected`) |

Retry delay for attempt *n* is **equal-jitter exponential**: a uniform value
in `[c/2, c]` where `c = min(WEBHOOK_RETRY_CAP_S, WEBHOOK_RETRY_BASE_S · 2^(n−1))`
(30 s base, 2 h cap). Equal rather than full jitter because the endpoint
just failed: the delay must never collapse to near zero.

**Retry-After** (429/503) may lengthen the delay, clamped to
`WEBHOOK_RETRY_AFTER_MAX_S` (1 h); both delta-seconds and HTTP-date forms are
parsed, garbage is ignored. An endpoint cannot park a delivery for days.

A delivery is **dead** after `WEBHOOK_MAX_ATTEMPTS` (10) attempts, or once
it could not be delivered within `WEBHOOK_DELIVERY_MAX_AGE_S` (3 days) of
creation — including time held while the organization or connection was
suspended. `dead_reason` says which.

## 7. Backpressure and the circuit breaker (Tasks 49, 50)

**Trigger reached: YES.** Outbound webhooks repeatedly hitting a failing
endpoint is exactly the case the breaker debt was waiting for. Implemented
lightweight, per connection, in the connection row (no distributed
infrastructure):

- after `INTEGRATION_CIRCUIT_FAILURE_THRESHOLD` (5) consecutive failures the
  circuit opens for `30 s · 2^(failures − 5)`, capped at 1 h; while open,
  deliveries are held (timer moved to the reopening), not attempted and not
  charged; half-open allows one attempt in flight; a success closes it;
- after `INTEGRATION_AUTO_SUSPEND_FAILURES` (50) consecutive failures the
  connection is **suspended automatically**, audited (`record_system`), and
  receives no new fan-out until an administrator reactivates it;
- a connection may have at most `WEBHOOK_MAX_OPEN_PER_CONNECTION` (1,000)
  undelivered deliveries; beyond that a new delivery is created **dead**
  (`backlog_limit`) — visible and replayable, never silently dropped, and
  never a job or a timer;
- at most `WEBHOOK_MAX_INFLIGHT_PER_CONNECTION` (4) attempts in flight per
  connection, so one slow endpoint occupies at most four workers;
- at most `WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION` (8) in flight per
  organization, so one organization's many slow endpoints cannot occupy the
  delivery workers every organization shares (Phase 6 audit).

### What a queued delivery uses (decided in the Phase 6 audit)

A delivery records *which event* goes to *which connection*; it does not
snapshot the connection. Every attempt - first, retry or replay - reads the
connection **as it is at claim time**:

- **Destination:** the connection's current URL. Editing the URL (a
  `manage` action: AAL 2, audited with the old and new origins) redirects
  every queued, retrying and replayed delivery of that connection. This is
  deliberate: the connection is the receiver's identity, and a receiver
  that moves its endpoint wants its backlog to follow, not to keep failing
  against the old one. An administrator who instead wants a *new* receiver
  to get only new events creates a new connection.
- **Signature:** the secrets valid at claim time - the active one and, during
  a rotation's overlap, the previous one (two `v1` values). A delivery
  created before a rotation and sent after it is signed with the new secret
  (and the old one while it is still in its overlap). There is never a
  moment with no valid signature: rotation keeps the old secret signing for
  `INTEGRATION_SECRET_OVERLAP_S`. One generation only - a second rotation
  inside the window ends the first one's overlap, which is why the UI sends
  a single rotation per click.
- **Subscriptions:** matched once, at routing. A delivery already created
  is sent even if the subscription is removed before it goes out; removing
  a subscription stops *new* fan-out. Disabling the connection is the way
  to stop queued deliveries (they die `connection_disabled`).

## 8. Dead letter, replay, retention

A dead delivery keeps its attempts (outcome, category, HTTP status,
duration, ≤200-character printable response excerpt — never the request,
never a header). **Replay** (`POST …/deliveries/{id}/replay`, permission
`platform.webhook.replay`):

- only a `dead` delivery is eligible — one still retrying is owned by the
  automatic retry;
- creates a **new** delivery (`origin=replay`, `replay_of=<original>`) of the
  **same stored event** to the same connection; the original is not modified;
- at most one replay of a delivery in flight (partial unique index);
- audited: `integration.delivery.replayed`, with the actor.

Retention (`integration/routing/retention.py`, daily): succeeded deliveries
7 days, dead deliveries 30 days (the replay window), attempts with their
delivery. Events are kept 30 days and never pruned while any delivery or
execution refers to them.
