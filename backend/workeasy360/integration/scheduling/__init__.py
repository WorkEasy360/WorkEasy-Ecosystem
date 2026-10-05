"""Durable due-work timers: "run this unit of work at (or after) this time".

The one piece of cross-tenant infrastructure Phase 6 adds, and the reason it
exists is Row-Level Security working exactly as designed. Deliveries,
automation executions and schedules are tenant-owned rows; a sweeper that
must find *every tenant's* due work cannot see them without entering each
tenant's scope in turn - an O(organizations) scan on every tick, which is the
pattern the Phase 5 audit already flagged as a scaling ceiling.

``DueWork`` inverts that: one row per pending unit of work, holding only
``(kind, organization_id, target_id, due_at)`` - identifiers and a time, no
tenant data - claimed across tenants with ``FOR UPDATE SKIP LOCKED`` exactly
as the outbox relay claims ``OutboxEvent``. The claimed item is dispatched to
a task that then enters *that one* organization's scope to do the real work.
Cost is O(due items), not O(tenants). See ADR-024 and debt-register D-017.

Two further jobs fall out of the same table for free:

- **Retries without broker ETAs.** A retry is a ``due_at`` in the future, not
  a Celery ``countdown``: RabbitMQ holds an ETA message unacknowledged on a
  worker until it fires and closes the channel after its consumer timeout,
  so long backoffs through the broker are a redelivery storm waiting to
  happen.
- **Broker-outage recovery.** Work is enqueued straight after commit for
  latency, and the timer is the durable record that it still needs doing. If
  the enqueue is lost - broker down, worker killed - the claim lease expires
  and the sweeper dispatches it again. Every consumer is idempotent, so a
  duplicate dispatch is a no-op, never a duplicate effect.
"""
