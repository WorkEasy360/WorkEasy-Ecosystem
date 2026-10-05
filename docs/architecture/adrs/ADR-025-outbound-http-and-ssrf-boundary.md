# ADR-025: One outbound HTTP boundary, pinned to validated addresses

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Architecture, Security
**Depends on:** [12-security.md §9](../12-security.md), [ADR-018](ADR-018-openid-connect-for-product-sso.md)

---

## Context

Until Phase 6 the platform fetched nothing (A16; Phase 3 T-11). Phase 6
delivers webhooks to URLs customers choose, which reaches the SSRF trigger
12-security.md §9 anticipated: a platform that will POST to
`http://169.254.169.254/` because a customer typed it hands that customer
the cloud credentials of the worker that sent it.

The standard mitigation (validate the host, then send) has a well-known
flaw: the HTTP library resolves the name again. An attacker's DNS answers
the validator with a public address and the library with a private one -
DNS rebinding. `requests` and `httpx` both resolve inside their connection
pools and expose no supported way to connect to an address chosen in
advance while still verifying TLS against the hostname.

## Decision

**All outbound HTTP goes through `integration/egress/client.send`**, built on
the standard library (`socket`, `ssl`, `http.client`) so the socket can be
opened to an address validated in the same call and handed to `http.client`
unchanged:

1. static validation (`policy.parse_target`) - https only (plain http and
   loopback only where a deployment explicitly allows them; production pins
   both off), no userinfo, allowed ports, no internal names, literal
   addresses checked;
2. resolve once, on a bounded thread pool with a DNS deadline;
3. **every** resolved address checked - loopback, private, link-local,
   CGNAT, multicast, reserved, unspecified - including IPv4 embedded in
   IPv6 (mapped, 6to4, NAT64) and Teredo;
4. connect to a validated address literal; TLS with `CERT_REQUIRED`,
   hostname verification and SNI against the *hostname*, TLS 1.2 minimum;
5. never follow a redirect - a 3xx is a result;
6. connect/read/DNS timeouts and one overall deadline;
7. request and response byte caps - the response is read in chunks and
   abandoned past the cap.

Enforced structurally: `test_outbound_http_is_confined_to_the_egress_boundary`
fails the build if any other module imports a socket, TLS or HTTP client
library; `test_tls_verification_cannot_be_disabled` fails it on any
verification bypass. Save-time validation is static only - the request that
saves a URL runs inside a database transaction and must not do network I/O
(E2) - and the authoritative check runs at send time, every time.

## Consequences

- **Guarantee:** a connection is opened only to an address that passed the
  policy in the same `send` call. There is no second resolution.
- Each request is one fresh connection (`Connection: close`) - no pool to
  exhaust or poison. The cost is a TCP+TLS handshake per delivery, accepted
  at webhook volumes; revisit only if measured delivery throughput needs it.
- ~250 lines of client code are ours to maintain. No HTTP protocol is
  implemented here - framing, chunking and header parsing are `http.client`'s.
- No new dependency.

## Alternatives rejected

- **`requests` + a validating adapter.** The adapter validates, the pool
  resolves again - the rebinding gap stays open.
- **An egress proxy (Smokescreen-style) in front of the workers.** The right
  second layer, not a replacement for the application check; out of scope
  for the application repository and recorded as an infrastructure
  recommendation (egress security groups, IMDSv2 - 12-security.md §9).
