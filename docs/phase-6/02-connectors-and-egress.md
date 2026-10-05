# Phase 6 — Connectors and the outbound HTTP boundary

## 1. The connection registry (Task 3)

`integration/connectors.Connection` — one organization's configured instance
of a connector. Tenant-scoped, RLS, composite keys.

| Field | |
|---|---|
| `connector_key` | which registered connector (`webhook`) |
| `display_name` | ≤ 120 characters |
| `status` | `draft` → `active` ⇄ `suspended` → `disabled` (terminal) |
| `config` | validated by the connector, **non-secret** by contract |
| `revision` | optimistic-concurrency token for edits |
| health | `consecutive_failures`, `circuit_open_until`, `last_success_at`, `last_failure_at` |

Lifecycle transitions are conditional updates on the current status (two
racing changes: one wins, one 409), and every one is audited. Disabling
revokes every secret. There is no hard delete: `disabled` *is* deletion, and
history is pruned by retention. At most 50 non-disabled connections per
organization.

The webhook URL is treated as sensitive: API responses and audit entries
carry only its origin (`https://hooks.example.com/…`), because chat-service
webhook URLs carry their token in the path.

## 2. Credentials (Tasks 4, 30, 53, 82)

`ConnectionSecret` — versioned, sealed, never returned after creation.

- **Why encrypted, not hashed:** the platform must *use* a signing secret to
  compute an HMAC on every delivery. Recoverable at rest means authenticated
  encryption under a key that is not in the database.
- **Mechanism:** Fernet (`cryptography`, already pinned) under
  `INTEGRATION_SECRET_KEYS`, a key distinct from every other in the
  platform (production refuses to start if it is missing, short, or equal to
  `SECRET_KEY`, the file-signing key or the MFA key). `MultiFernet`: the first
  key seals, every configured key opens, so the master key rotates without
  losing existing secrets.
- **Bound to its row:** the sealed plaintext carries `{organization,
  connection, version}` and opening checks them; ciphertext copied to another
  row does not open (`test_the_database_holds_ciphertext_bound_to_its_row`).
- **Immutable versions:** a trigger refuses changes to a version's
  `ciphertext`, `version` or `connection`; rotation only ever adds a version.
- **Rotation with overlap:** rotating makes the old secret `previous` for
  `INTEGRATION_SECRET_OVERLAP_S` (24 h); both sign deliveries meanwhile (two
  `v1` values), so a receiver switches without dropping anything.
  `DELETE …/secret` ends the overlap immediately (e.g. after a leak).
  Exactly one `active` secret per connection (partial unique index);
  concurrent rotations are serialised on the connection row
  (`test_concurrent_rotations_leave_exactly_one_active_secret`).
- **Shown once:** the plaintext exists in the create or rotate response and
  nowhere else; those responses are `Cache-Control: no-store`. The UI keeps
  it in component state only and drops it on "I've stored it".

## 3. The connector contract (Tasks 27, 29, 30)

```python
class Connector(Protocol):
    key: str; version: int; label: str; description: str
    capabilities: Mapping[str, CapabilitySpec]   # declared, fixed
    generates_secret: bool
    def validate_config(self, raw, *, policy) -> dict          # static, no network
    def describe_config(self, config) -> dict                   # safe to show/audit
    def build_request(self, config, invocation, secrets) -> OutboundRequest
    def interpret(self, capability, response) -> Outcome        # provider error mapping
```

- **Narrow:** capabilities are declared up front and invoked by name. There
  is no `execute(action, arbitrary_json)`.
- **Least privilege, structurally:** a connector gets its connection's
  validated config, one `Invocation`, and `SecretMaterial` holding **only its
  own connection's** secrets (`selectors.secret_material(connection_id)`). It
  never opens a socket (the pipeline sends through egress), never reads the
  database or settings. The import-linter contract
  `connector-providers-are-sandboxed` fails the build if a provider imports
  Django, the platform, the connector models/selectors/services/secrets, or
  any pipeline.
- **Registry, not branching:** the pipeline looks the connector up by key;
  nothing branches on which connector it is.

**Reference connector (Task 28):** `webhook` — one capability,
`deliver_event`: the signed POST described in [03-webhooks.md](03-webhooks.md).
No vendor connectors are built.

## 4. The outbound HTTP boundary (Tasks 15–19, 87–89; ADR-025)

Every outbound request goes through `integration/egress/client.send`.
Nothing else in the codebase may import `socket`, `ssl`, `http.client`,
`urllib.request`, `urllib3`, `requests`, `httpx` or `aiohttp`
(`test_outbound_http_is_confined_to_the_egress_boundary`).

| Control | Detail |
|---|---|
| Scheme | `https` only. `http` only where `INTEGRATION_EGRESS_ALLOW_HTTP` (development/tests); production pins it off |
| URL | ≤ 2,048 characters, no whitespace/control characters, no userinfo (`user:pass@`, `good@evil`) |
| Host | a fully qualified public name; `localhost`, `*.local`, `*.internal`, `*.lan`, `*.svc`, metadata names refused before any lookup |
| Port | 443 or 8443 (`INTEGRATION_EGRESS_ALLOWED_PORTS`) |
| Addresses | **every** resolved address must be public unicast: loopback, RFC 1918, ULA, link-local (all cloud metadata), CGNAT (Alibaba metadata), multicast, reserved, unspecified, documentation and benchmarking ranges refused; IPv4-mapped, 6to4 and NAT64 addresses judged by the IPv4 they embed; Teredo refused |
| Redirects | never followed — a 3xx is a result (and a dead delivery) |
| Timeouts | DNS 2 s, connect 3 s, read 10 s, **overall 15 s** deadline no step can exceed. Enforced by a per-request watchdog that shuts the socket down at the deadline, because a socket timeout bounds only one `recv`: a peer trickling its TLS handshake, status line, headers, endless `100 Continue` responses or body one byte per read timeout is cut off at 15 s (Phase 6 audit; `test_a_trickling_endpoint_is_cut_off_at_the_overall_deadline`). Once the status line has arrived it is the answer: a body that is slow or never ends is truncated, not turned into a retry of an accepted delivery |
| Bytes | request ≤ 256 KiB; response read in chunks, at most 64 KiB kept, the rest never read |
| TLS | `create_default_context()` — `CERT_REQUIRED`, hostname check, SNI against the hostname, TLS ≥ 1.2. No switch exists to disable it (`test_tls_verification_cannot_be_disabled`) |
| Proxy | none — `http.client` reads no `HTTPS_PROXY`, so a stray environment variable cannot reroute customer traffic |
| Identity | `User-Agent: Workeasy360-Integrations/1.0`; `Connection: close` |

### DNS rebinding — exact guarantees (Task 16)

1. The host is resolved **once per attempt**, on a bounded thread pool with
   the DNS deadline (`getaddrinfo` has no timeout of its own).
2. **All** resolved addresses are checked; one bad record refuses the whole
   host.
3. The TCP connection is opened to a validated **address literal**, so no
   library performs a second lookup. TLS still verifies the certificate
   against the **hostname**.
4. Redirects are never followed, so there is no second hop to validate.
5. Every retry is a new attempt and re-resolves and re-validates.

Save-time validation is static (no DNS): the request that saves a URL runs in
a database transaction and must not do network I/O (E2). The send-time check
is authoritative; a name that later resolves internally is refused at send
time, recorded `destination_blocked`, and the delivery is dead without a byte
sent (`test_rebinding_at_send_time_kills_the_delivery_without_sending`).

### Connection budget (Task 19)

One fresh connection per request, no pool. Concurrency is bounded above the
client: at most 4 attempts in flight per connection (claim-time count under
the connection's row lock), 1 while its circuit is half-open, at most
`WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION` (8) per organization across all its
connections (claim-time count under a per-organization advisory lock), and
the heavy worker pool's size overall. One slow provider occupies at most
four workers and one organization at most eight - never the pool, which
must therefore be sized well above eight.
