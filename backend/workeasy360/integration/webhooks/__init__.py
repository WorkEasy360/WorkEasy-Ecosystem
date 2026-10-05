"""integration/webhooks - durable, signed, at-least-once outbound delivery.

Owns what a customer subscribes to and what happened to every attempt:

- ``EventSubscription`` - which connection receives which events.
- ``Delivery`` - one logical "send this event to this connection", with an
  explicit state machine (pending -> in_progress -> succeeded | retry | dead).
- ``DeliveryAttempt`` - one immutable record per HTTP attempt.

**The semantics, truthfully (Task 23): at-least-once.** A delivery is
retried until it succeeds or is dead; a receiver may see the same event more
than once (a response lost after the receiver processed it, a worker killed
between sending and recording). Receivers deduplicate on ``event.id``. The
platform guarantees that at most one *attempt record* wins per attempt
number and that no two workers send the same delivery attempt concurrently;
it does not - and no system spanning a network can - promise exactly-once
receipt.

The pipeline never holds a database transaction across the network call:
claim (transaction 1, committed) -> send (no transaction) -> record
(transaction 2, fenced on the attempt number it claimed). See
``pipeline.run_delivery``.
"""
