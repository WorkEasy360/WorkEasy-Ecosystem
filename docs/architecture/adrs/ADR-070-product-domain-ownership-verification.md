# ADR-070: Product domain ownership verification before a product can receive sign-ins

- Status: proposed (2026-11-16). On branch `feat/product-domain-verification`; not deployed.
- Numbering: drafted as ADR-068. Renumbered ADR-070 on 2026-11-17: ADR-067 (Accounts host) and ADR-068 (Product Publisher) are live, and back-channel logout became ADR-069.
- Context: the owner's instruction "Implement appropriate domain ownership and redirect URI verification for new product registration. Ensure a new product cannot register attacker-controlled callback or logout endpoints. Preserve automatic product onboarding through the existing Super Admin APIs."
- Builds on: ADR-018 (OIDC for products), ADR-044 (universal sign-in), ADR-069 (back-channel logout). Reuses the DNS proof mechanism of partner custom domains (Phase 7, `commercial/partners/domains.py`).

## Context

A product's manifest names the URLs the platform sends things to:

- authorization codes go to `redirect_uris`;
- signed-out browsers go to `post_logout_redirect_uris`;
- launches go to `launch_url` and `login_url`;
- logout tokens go to `backchannel_logout_uri`;
- health probes go to `health_url`.

`contracts.values.manifest` already refuses a URL that is any of:

- not https (except loopback outside production);
- internal, private, link-local or metadata;
- wildcarded;
- carrying credentials, a query or a fragment.

`backchannel_logout_uri` must also share an origin with a redirect URI.

None of that establishes that the hostname **belongs to whoever registered it**. These cases all pass validation:

- a staff member types `crn.example.com` for `crm.example.com`;
- someone persuades support to "update our callback";
- a domain lapses and someone else re-registers it.

Each of these hostnames would then receive authorization codes and logout tokens, and the platform would send browsers there. A code still needs the client credential to be redeemed, but a phishing page on a trusted post-logout return, or a stream of logout tokens, is damage enough.

## Decision

1. **Ownership is proven per hostname, per integration, with a DNS TXT record.**
   - Record name: `_w360-product-verify.<hostname>`.
   - Value: a token issued by the platform, `w360-product-verify=<random>`. Each integration has its own token, so one product's proof never proves another's, even on a shared host.
   - The mechanism is the same as partner custom domains, under a different record name so the two never read each other's proofs.
   - The lookup runs in `commercial/controlplane/product_domains.py`, outside any transaction, through the resolver port partner domains already use. That gives one DNS configuration (`W360_DNS_*`) and one test double.
   - The ecosystem module still makes no network call. `platform/ecosystem/domains.py` records results and owns the rules.
   - The hostnames come from every manifest URL field. A test pins that list against `ProductManifest`, so a new URL field cannot be missed.
2. **What a proof unlocks.** These checks are enforced in `platform/ecosystem/services.py`, so every path is gated: the Super Admin console, the Add-product wizard, the registry API and management commands.
   - **Draft:** any valid hostname may be named, and each gets a pending proof. Automatic onboarding through the Super Admin APIs is unchanged up to this point.
   - **Activation** (`draft → active` and `suspended → active`) requires every hostname to be `verified`. Otherwise it is refused with `409 product_domain_unverified`, naming each hostname and its record.
   - **Editing an active or suspended product:** a hostname it **did not already use** must be verified first, or the edit is refused and nothing changes. Hostnames it already uses are not re-checked by an unrelated edit, so fixing a typo in a display name never fails on DNS.
3. **Ways a hostname becomes verified.** Each row records the method used.
   - `dns_txt`: the record carried the token.
   - `pre_existing`: carried over by migration `ecosystem.0008` for every hostname of an active or suspended integration at that moment, so the four live products are not disturbed. Staff can ask for a DNS proof at any time; the hostname stays verified meanwhile.
   - `loopback`: a loopback host, only where `OIDC_ALLOW_INSECURE_REDIRECT_URIS` already accepts plaintext loopback URLs. That is development and tests; production refuses such URLs before they reach this code.
   - `attested`: the operator running `register_products --activate-integrations` on the platform host vouches for the first-party hostnames declared in that command. Shell access is a stronger credential than a DNS record. No API can attest, and every attestation is audited.
4. **Control can lapse, and the platform notices.**
   - A beat task, `commercial.controlplane.recheck_product_domains` (every 6 h, heavy queue, at most 200 per run), re-checks DNS-proven domains of launchable products.
   - Three consecutive misses mark a domain `lapsed`. The change is audited (`ecosystem.integration.domain_lapsed`), shown to staff, and blocks reactivation and re-use until the record is republished.
   - A failed lookup is never counted as a miss.
   - A lapse does **not** suspend a live product. Turning a DNS mistake into an outage for paying customers is worse than flagging it. The product's client credential is still needed to redeem any code.
5. **Staff API.** All three are staff-only and use the existing integration grants.
   - `GET admin/products/<code>/integration/domains` (view) lists every hostname, its status and method, and the record to publish.
   - `POST` to the same path with `{"hostname"}` (manage) asks for a proof.
   - `POST .../domains/verify` with `{"hostname"}` (manage) looks up the record now. It is rate-limited (`product_domain_verify_*`, 10 per minute per user, 30 per minute per IP).
   - Audit actions: `ecosystem.integration.domain_requested`, `domain_verified`, `domain_lapsed`, `domain_attested`.
6. **SSO readiness.** `GET admin/products/<code>/integration/readiness` answers one question: can people sign in, and what is missing?
   - It covers: registration, sign-in callback, an active credential, domain ownership, status, back-channel logout registration and recent delivery, and address health.
   - Each item is `ok`, `action`, `warning` or `info`. The verdict is `ready`, `attention` or `not_ready`.
   - It reads stored facts only and asks no product.

## Consequences

- A new product cannot receive an authorization code, a logout token or a signed-out browser at a hostname nobody proved, by any registration path.
- **Onboarding gains one step before activation:** publish a TXT record, then choose Verify. Draft registration through the API needs nothing new. Products on the local E2E harness (loopback) need nothing new.
- The four live products keep working with no action, through the `pre_existing` carry-over. Replacing it with DNS proofs is recommended and can be done at any time without downtime.
- The partner resolver module gains one public accessor, `txt_resolver()`. Its behaviour is unchanged.
- Test fixtures that activate a product attest its test hostnames, exactly as the operator command does, and say so. The gate itself is exercised in `tests/phase11/test_product_domains.py`.

## Rejected

- **An HTTP well-known file** such as `/.well-known/workeasy360-verification`. It proves control of one web server rather than the name. It needs an outbound HTTP fetch per check. And many products sit behind an edge that would need a route for it. DNS is the stronger proof, and the platform already operates it for partners.
- **One global owner per hostname**, which is the partner rule. Two first-party products may share a host on different paths. Proof per integration gives the same protection without forcing a single owner.
- **Suspending a product whose domain lapsed.** As explained in decision 4, it would turn a DNS slip into an outage.
- **Checking only `redirect_uris`.** A post-logout return, a login page or a back-channel URI on a stranger's host is just as harmful.
