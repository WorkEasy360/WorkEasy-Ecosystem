# Entitlement verification: capacity and rate limits

**Written for: whoever sizes and operates the Workeasy360 API, and whoever changes `W360_RL_VERIFY`.**

Brief item 18 (2026-10-12): the old per-client limit of 600/min served about 9,000 organizations per product at the 15-minute cadence. This note says what the products actually ask, what the platform can serve, and why the limits are what they are. Settings: `ECOSYSTEM_RATE_LIMITS` in `backend/config/settings/base.py`.

## 1. Who asks, how often

Every product (Easy CRM, EasyBook, Easy Project, Easy Help Desk; each has its own client, so its own budget) asks `POST /integrations/entitlements/verify` in two ways (`completion-design.md` §4-5):

| Question | Key the product caches | Asked again after | Volume per product |
|---|---|---|---|
| A signed-in person's request (`subject`) | (organization, person) | `allow`: 900 s; denial: 60 s | one per **active person** per 15 minutes, only when they make a request after the answer aged |
| Background work (no `subject`) | organization | 900 s for `allow` or `deny`; a refusal of the product's own client, 60 s | one per **organization with background work due** per 15 minutes |

Nothing asks per request. One process asks per key while the others use the last answer (a refresh lock in the product's shared cache).

So, per product, per minute:

```
R = P / 15  +  B / 15  +  D
P = people active in the same 15 minutes
B = linked organizations that had background work DUE in those 15 minutes
D = denied people still retrying (at most one each per minute: the 60 s re-check)
```

`D` is small and short-lived: a person whose access ended sees one page and leaves.

`B` is bounded only if a product **looks for due work before it asks**. A sweep that asks about every linked organization and then looks for work makes `B` every organization ever linked, expired 7-day trials included, growing without bound (found in the 2026-10-12 audit; EasyBook's sweeps did exactly that, and were changed). The rule for every product: find the due work first, then ask about the organizations that have some. An expired trial with nothing scheduled then costs nothing.

**Before this brief** products asked per organization only, so `R = O / 15`, which is where "9,000 organizations at 600/min" came from. Asking per person (needed so a removed member's open session ends within the window) multiplies the questions by the people active per organization. That is why the limit had to be re-derived rather than kept.

## 2. What it costs the platform

`test_report_entitlement_verification_latency` (`backend/tests/performance/test_ecosystem_queries.py`), 2026-10-12, test settings on a busy development machine, through PgBouncer, 40 calls each:

| Question | p50 | p95 |
|---|---|---|
| Organization | 36 ms | 49 ms |
| Per person | 71 ms | 116 ms |

Phase 3 measured the organization question at 7.5 ms on a quiet machine, so read these as an upper bound. The useful fact is the ratio: the personal half (account, membership, seat) roughly doubles the work.

The Phase 12 load test (`docs/phase-12/07-load-test.md`) put one API host (3 gunicorn workers) at about 110 requests/s of mixed traffic, bound by the workers' CPU.

## 3. The limits

| Bucket | Default | Scope | Why this number |
|---|---|---|---|
| `verify` (`W360_RL_VERIFY`) | **1,200/min** | per product client | Serves `P + B` = 18,000 per product (2x the old ceiling), and caps what one product, misbehaving or compromised, can take from the API: 20 requests/s |
| `verify_org` (`W360_RL_VERIFY_ORG`) | **6/min** | per (client, organization, person) | A conforming product asks about one key at most once a minute (60 s denial re-check, 60 s outage back-off). 6 lets several product processes ask at once while the product's own shared cache is down. **Asked before `verify`**, so a loop stuck on one key is refused there without spending the product's whole budget (`test_a_looping_key_does_not_spend_the_products_budget`). Keyed on the canonical UUID; every malformed subject shares one key |

**What one product at its cap costs.** 20 requests/s at the measured 71 ms (per person) is about 1.4 worker-seconds per second: about half of the Phase 12 host's three CPU-bound workers. Across a window boundary the fixed window admits up to twice that for a moment (the whole host). Four products at their caps need about two such hosts for these checks alone. The cap is therefore a bound on harm, not a target: the API must be sized for the expected rate (section 4) long before any product approaches it.
| `client_auth_failure` | 10/min | per client | Unchanged: credential guessing |

**Burst.** The limiter counts fixed 60-second windows, so a product can send up to twice its limit across a window boundary. The per-key bucket stops a burst from being concentrated on one organization or person. The realistic herd is a product starting with an empty shared cache (deploy and cache flush at once): every active person's first request asks. Requests over the limit get 429, which every product treats as an outage: the person continues (there is no answer yet, so access continues) and the product backs off for 60 s. A herd therefore degrades into "not yet re-checked for a few minutes", never into a lockout.

**Abuse protection is kept.**
- One product cannot take more than its cap.
- One key cannot take more than 6/min.
- Organization ids are random UUIDs, and the per-person answer is the same for "removed", "never a member" and "no such person". The budget is therefore no enumeration aid.
- Failed client authentication keeps its own tight bucket.

## 4. Growth: what to watch, when to change

| People active in 15 min, per product | Questions/min per product | Share of the 1,200 cap | 4 products, requests/s | API worker-seconds/s at 71 ms |
|---|---|---|---|---|
| 1,000 | 67 | 6 % | 4.4 | 0.3 |
| 5,000 | 333 | 28 % | 22 | 1.6 |
| 9,000 | 600 | 50 % | 40 | 2.8 |
| 18,000 | 1,200 | 100 % | 80 | 5.7 |

Rules:
- **Size the API before the products reach their caps.** At 9,000 active people per product, entitlement checks alone keep about three workers busy. That is the whole Phase 12 single host. Phase 12's remedy applies first: about 2 gunicorn workers per core, and more instances.
- **Alert** on `ecosystem_rate_limited_total{bucket="verify"}` above 0 for 5 minutes: a product is at its cap. The `ecosystem.rate_limited` log line names the bucket. Also alert when the verify request rate exceeds half of (products × cap / 60) requests/s.
- **Raise `W360_RL_VERIFY`** when a product's sustained rate passes 50 % of its cap and the API has the capacity measured above. Never raise it past what the API is sized for: the cap is also the API's protection.
- **Do not raise `revalidate_after` to save capacity.** It is the window the brief fixes at about 15 minutes for every cause of lost access.
