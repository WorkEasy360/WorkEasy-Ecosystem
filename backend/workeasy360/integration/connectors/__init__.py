"""integration/connectors - how the platform talks to an external system.

Owns three things:

- **The connection registry** (``Connection``): an organization's configured
  instance of a connector - "our order-alerts webhook", "the finance team's
  endpoint" - with a lifecycle (draft, active, suspended, disabled), a
  validated non-secret configuration and a health/circuit state.
- **Connection credentials** (``ConnectionSecret``): versioned, encrypted at
  rest, rotated with an overlap window, never returned after creation.
- **The connector framework** (``contract``, ``registry``, ``providers``): a
  deliberately narrow interface - validate configuration, build one
  request for one declared capability, interpret the response - and one
  reference implementation, the signed ``webhook`` connector.

What it does **not** own: when anything is sent (``integration/webhooks``
runs the durable delivery pipeline), what triggers it
(``integration/automation``, ``integration/routing``) or how the bytes move
(``integration/egress``). A connector never opens a socket, never reads the
database and never sees any credential but its own connection's - see
``contract.Connector``.

Named "connections" on the wire and in permissions (``platform.connection.*``)
because "integrations" already means *registered products* in this codebase
(``platform/ecosystem``, ``platform.integration.*``); reusing the word for a
customer's webhook would put two unrelated trust models behind one name.
"""
