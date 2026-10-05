"""integration/egress - the one way Workeasy360 makes an outbound HTTP request.

Phase 6 is the first phase in which the platform fetches a URL a customer
chose. That makes server-side request forgery the highest-risk surface it
has ever had (12-security.md section 9, T6): a platform that will POST to
``http://169.254.169.254/`` because a customer typed it is a platform whose
cloud credentials belong to that customer.

So every outbound request goes through ``egress.client.send`` and nothing
else - asserted structurally by
``test_outbound_http_is_confined_to_the_egress_boundary``, which fails the
build if any other module imports a socket, TLS or HTTP client library.

What ``send`` guarantees, and the attack each guarantee closes:

1. **Scheme allowlist** - https only (plain http only where a deployment
   explicitly allows it, never in production). No ``file:``, ``gopher:``,
   ``ftp:``, ``data:``.
2. **No credentials in the URL** - ``https://good@evil`` reads as ``good``.
3. **Every resolved address is checked** - loopback, private (RFC 1918,
   ULA), link-local (which is where every cloud metadata service lives),
   CGNAT, multicast, reserved and unspecified ranges are refused, including
   when smuggled inside IPv4-mapped, 6to4 or NAT64 IPv6 addresses. A host
   with one public and one private record is refused outright.
4. **Resolve once, then pin** - the TCP connection is made to an address
   validated *in the same call*, never to a name the HTTP library resolves
   again. This is what closes DNS rebinding: there is no second lookup for
   an attacker's resolver to answer differently. TLS still verifies the
   certificate against the *hostname* (SNI + hostname check), so pinning
   does not weaken authentication of the endpoint.
5. **Redirects are never followed** - a 3xx is returned as a result, not
   acted on. A public URL redirecting to the metadata service is the
   standard SSRF bypass, and not following redirects removes it entirely.
6. **Bounded in time** - separate DNS, connect and read timeouts, and one
   overall deadline no step can exceed, enforced by a watchdog that shuts
   the socket down (a socket timeout bounds one ``recv``, not a peer that
   trickles a byte per read timeout). A slow endpoint costs at most the
   deadline, never a worker.
7. **Bounded in bytes** - the request body is capped, and at most
   ``max_response_bytes`` of the response are read before the connection is
   closed. A hostile endpoint cannot stream gigabytes into a worker.
8. **TLS verification is mandatory** - there is no switch to turn it off;
   ``test_tls_verification_cannot_be_disabled`` scans for one.
9. **No ambient proxy** - ``http.client`` reads no ``HTTPS_PROXY``
   environment variable, so a stray variable on a worker cannot route
   customer traffic through an unexpected host.

Validation also runs at *save* time (``policy.parse_target``), statically -
syntax, scheme, port, literal addresses and obviously internal names - so an
administrator gets immediate feedback. It deliberately does no DNS lookup
there: the request that saves a URL runs inside the tenancy middleware's
database transaction, and external I/O inside a transaction is forbidden
(E2). The authoritative check is the one at send time, every time, because
DNS can be re-pointed after a URL is accepted.
"""
