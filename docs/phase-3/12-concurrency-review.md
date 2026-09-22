# 12 — Concurrency Review

Every race on this boundary, what makes it deterministic, and the test that
proves it. All of them run against **real PostgreSQL** with
`transaction=True` — the default test transaction would serialise the threads
and turn each one into a test of nothing.

Eight threads released together on a barrier. A stagger would mean the threads
never collide, and a race test that does not collide passes for the wrong
reason.

---

## 1. One authorization code, eight simultaneous redemptions

**The one that matters.** A code redeemed twice is two sessions from one
authorization — the difference between "a user signed in" and "a user and
whoever stole their code both signed in".

**Mechanism:** a conditional UPDATE.

```sql
UPDATE ecosystem_authorization_code
   SET redeemed_at = now()
 WHERE id = %s AND redeemed_at IS NULL
```

PostgreSQL applies that atomically: of two concurrent redemptions, exactly one
sees `rowcount == 1`.

**Why not read-then-write:** under READ COMMITTED both threads would observe
`redeemed_at IS NULL` and both would issue tokens — and a sequential test
would pass every time.

**Why not `SELECT FOR UPDATE`:** it would also work, and would additionally
serialise every redemption on the row. The conditional update needs no lock
at all.

**Result:** exactly one `200`, seven `400 invalid_grant`. One row, redeemed
once.

`test_one_authorization_code_yields_exactly_one_token_set` ·
`test_concurrent_redemptions_leave_one_spent_code_and_no_duplicates`

## 2. Concurrent registrations for the same product

Two client ids both claiming to be CRM would make the entitlement answer
depend on which one asked.

**Mechanism:** the unique constraint on `product_code`, with `IntegrityError`
caught — **not** an `exists()` pre-check, which is itself the race: both
callers see "no row" and both insert.

**Result:** one succeeds, three fail, one row.

`test_concurrent_registrations_for_one_product_produce_one_integration`

## 3. Concurrent lifecycle transitions

Two operators racing to suspend and reactivate.

**Mechanism:** `select_for_update()` on the integration row inside the
transaction, so the transitions serialise and each one validates against the
state it actually finds.

**Result:** whatever the interleaving, the row holds one of the two legal
outcomes — never a torn state, and a `suspended` row always has its reason.

`test_concurrent_lifecycle_transitions_serialise`

## 4. Concurrent signing-key promotion

Two operators promoting different keys. Two active keys would make "which key
signed this?" depend on query ordering, and a half-finished rotation
indistinguishable from a finished one.

**Mechanism:** a partial unique index —

```sql
UNIQUE (status) WHERE status = 'active'
```

— plus `select_for_update()` on both the candidate and the incumbent. The
loser gets an integrity error rather than a silent second active key.

**Result:** exactly one active key out of four candidates.

`test_only_one_signing_key_survives_concurrent_promotion`

## 5. Client assertion replay under concurrency

**Mechanism:** `cache.add`, which is atomic on Redis and returns `False` when
the key exists. "First use wins" is therefore a property of the cache rather
than a read-then-write race between threads.

**Result:** at most one request gets past client authentication, so at most
one token set is ever issued — even before the code's own single-use guard.

`test_a_client_assertion_cannot_be_replayed_under_concurrency`

## 6. Rate-limit counting

**Mechanism:** `cache.add` to seed with an expiry, then `cache.incr`, which is
atomic on Redis. A get/set pair would let two concurrent requests both read 4
and both write 5.

The `add`/`incr` race — the key expiring between the two — is handled by
re-seeding; losing a single count at a window boundary is immaterial.

## 7. Revocation and suspension mid-flight

Not a data race, but the sequencing question an incident response depends on:
is a code issued *before* a revocation still redeemable *after* it?

**No.** Client authentication reads the credential and the integration status
on every request — there is no cached credential state anywhere — so the
exchange fails even though the code is otherwise perfectly valid.

`test_a_credential_revoked_mid_flight_stops_working` ·
`test_an_integration_suspended_mid_flight_cannot_complete_the_exchange`

---

## What is deliberately not serialised

**Entitlement reads.** They are reads of a materialised snapshot. A check
racing a provisioning write returns the pre-write answer, which is *stale*,
not wrong — the customer keeps what they had, which is the governing rule
already established in `entitlements/services/read.py`.

**Credential `last_used_on` stamping.** An unconditional `UPDATE` at day
granularity. Two concurrent writes set the same value; there is nothing to
lose.

**Audit entry hash chaining.** Unchanged from Phase 1: concurrent writes may
fork the chain, and serialising every audit write per organization would make
audit a contention point on the hot path. The verification job reports forks,
and tampering still breaks the descendants of any rewritten entry.

## Transaction boundaries

| Operation | Transaction | Why |
|---|---|---|
| Register, update, transition, credential create/revoke | `transaction.atomic()` with the audit entry inside | A mutation that was not audited did not happen |
| Code redemption | `atomic()` around the conditional UPDATE only | Kept minimal; the surrounding validation holds no locks |
| Key promotion | `atomic()` with `select_for_update()` on both rows | The incumbent and the candidate change together or not at all |
| Token signing | None | No writes |
| Entitlement verification | Read-only, inside the organization's DB scope | |

**No external I/O inside any transaction** (E2). The module makes no outbound
calls at all (T-11), so this is satisfied structurally.
