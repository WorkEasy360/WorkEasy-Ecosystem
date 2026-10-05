"""integration/routing - events in, work out.

The top of the integration layer, and the only module that composes all the
others:

- **ingestion** (``ingestion``): an independently deployed product publishes
  an event over HTTP, authenticated with its Phase 3 client credential.
  Namespace, declaration, entitlement, size, shape and rate are checked; the
  event is recorded exactly once; a routing timer is set; the request
  returns ``202`` without waiting for anything downstream.
- **fan-out** (``fanout``): an accepted event is matched against webhook
  subscriptions and active automation rules - set-based, a fixed number of
  queries whatever the counts - and turned into deliveries and executions,
  each with its own timer.
- **retention** (``retention``): the bounded, per-organization pruning of
  everything the other modules accumulate.

Nothing in this package performs an external side effect. The network is
touched only by ``integration/webhooks``' pipeline, through
``integration/egress``.
"""
