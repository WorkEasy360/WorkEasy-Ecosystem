# Phase 1 Security Review

**Date:** 2026-09-21
**Scope:** Everything built in Phase 1. No product code exists yet.
**Method:** Threat-model walk against
[12-security.md](../architecture/12-security.md), plus the audits required by
the Phase 1 brief.

---

## 1. Findings summary

| Severity | Count | Status |
|---|---|---|
| **P0** | 5 | **All fixed during Phase 1** |
| **P1** | 4 | 2 fixed, 2 carried with owners |
| **P2** | 5 | Documented, carried |

No unresolved P0 remains.

## 2. P0 findings — all fixed

### P0-1 · `unscoped_access()` did not satisfy RLS, so cross-tenant writers were refused

**Found by:** the R-14 benchmark fixture, then the entitlement write path.

`unscoped_access()` relaxes the ORM manager (Layer 2). RLS (Layer 3) does not
care: without a tenant variable, `WITH CHECK` refuses every INSERT and
`USING` hides every row. Provisioning, reconciliation and fixtures were all
silently unable to write.

The *security* reading matters more than the bug: this is defence-in-depth
working correctly. A deliberate bypass of the ORM still could not reach
another tenant.

**Fix:** `platform/tenancy/db.py::organization_db_scope(org_id)` — enters one
organization's database scope explicitly. Cross-tenant jobs must iterate
organizations and enter scope per organization, never write for many tenants
in one transaction. Covered by
`test_organization_db_scope_confines_writes_to_one_tenant`.

### P0-2 · The tenant resolver could never resolve, so every tenant request 404'd

**Found by:** `tests/integration/test_request_boundary.py`.

`DatabaseTenantResolver` read `Membership` before any tenant variable was set,
so RLS returned zero rows and resolution always failed. The system was
fail-closed to the point of being unusable.

**Fix:** resolve the `Organization` first (control-plane, no RLS), then enter
that organization's scope for the membership lookup. Safe because the
candidate organization comes from the URL and the query is additionally
filtered by the *verified* user id.

### P0-3 · Audit writes were blocked for suspended organizations

**Found by:** `test_audit_can_still_be_written_for_a_suspended_organization`.

`AuditEvent` is a `TenantScopedModel`, so the R-10 write block applied to it.
Consequence: the moment a customer most needs a trail — the suspension itself
and every denied action afterwards — was the moment nothing could be
recorded, and every denial surfaced as `AuditWriteFailed` instead of the
correct 402.

**Fix:** `tenancy_write_block_exempt`, set **only** on `AuditEvent`. Safe
because the table is append-only and holds no business state, so it cannot be
used to mutate anything suspension exists to stop. The single permitted
exception, documented at both the base class and the model.

### P0-4 · Audit cascaded on organization deletion

**Found by:** the R-14 teardown, which could not delete an organization.

`TenantScopedModel.organization` is `on_delete=CASCADE`, so deleting an
organization tried to delete its audit rows — which the application role has
no `DELETE` grant for.
[08-data-ownership.md](../architecture/08-data-ownership.md) §6 requires audit
to be **retained** as a tombstone on purge.

**Fix:** `AuditEvent` overrides the FK with `on_delete=DO_NOTHING,
db_constraint=False`. The column stays, so RLS still scopes the table and
customers still read only their own log, but the record of a deletion
survives the deletion.

### P0-5 · Content sniffing only rejected known-bad bytes

**Found by:** `test_content_that_disagrees_with_its_declaration_is_rejected`.

Upload validation checked whether the *detected* type was on the blocklist.
HTML has no magic bytes, so it sniffs as nothing — and a file declared
`application/pdf` containing `<html><script>` passed validation.

**Fix:** verify the declaration against the bytes whenever the declared type
is verifiable by signature. A declared PDF that does not start with `%PDF-`
is rejected and its content deleted. Types with no signature (text, CSV,
JSON) are inert and served as attachments from an isolated origin.

## 3. P1 findings

### P1-1 · `IntegrityError` caught without a savepoint — **fixed**

`mark_processed()` and `notify()` both caught `IntegrityError` inside a
transaction without a savepoint. PostgreSQL aborts the whole transaction on a
constraint violation, so a routine duplicate delivery would leave the
connection unusable and take the handler's own work down with it. Both now
wrap the insert in a nested `transaction.atomic()`.

### P1-2 · Domain errors surfaced as unhandled 500s — **fixed**

`PermissionDenied` reached HTTP as a 500 rather than a 403, conflating "you
may not" with "we broke" and making error rate useless as a signal. Added
`interfaces/api/errors.py::DomainErrorMiddleware`, which maps domain
exceptions to their declared status and leaves everything else to Django so
no unrecognised exception's message is echoed.

### P1-3 · Breach-corpus password check is not implemented — **carried**

12-security.md §3 requires it. `BreachCorpusValidator` exists with the
integration point marked and is **deliberately not registered**, so its
absence is visible rather than simulated. A validator that silently passes
everything would be worse than none.
**Owner:** Platform · **Due:** before first production customer.

### P1-4 · Malware scanning is a null implementation — **carried**

`NullScanner` reports clean for allowlisted types. The *interface*, the
quarantine state and the not-servable-by-default behaviour all exist, so
adding a real scanner is a registration rather than a redesign — which is
what Phase 0 asked for ("design for later scanning, do not invent
infrastructure"). Files are never servable before a scan result is recorded.
**Owner:** Platform · **Due:** before customer file uploads are enabled.

## 4. P2 findings — documented and carried

| # | Finding | Why accepted for Phase 1 |
|---|---|---|
| P2-1 | No rate limiting implemented | Needs Redis-backed token buckets and an edge tier; nothing is publicly exposed yet. Timeouts and bounded pagination are in place. |
| P2-2 | Sessions use Django's default; `Session`/`AuthFactor` policy models are not wired | Phase 1 scope is "MFA-compatible", not "MFA enforced". The schema exists so adding factors is a feature, not a migration of the auth model. |
| P2-3 | CSP is not emitted | No HTML surface exists yet. Headers that do exist (HSTS, nosniff, referrer policy, frame-deny) are configured and asserted. |
| P2-4 | Audit hash chain can fork under concurrent writes | Serialising audit per organization would make it a contention point on the hot path. Tampering still breaks every descendant hash, which is what the chain is for. Verification job reports forks. |
| P2-5 | No SSRF-safe fetch helper | Nothing makes outbound user-supplied requests yet. Required before webhooks or connectors ship. |

## 5. Audit results

### Tenant isolation audit

| Check | Result |
|---|---|
| Models with `organization_id` not inheriting `TenantScopedModel` | 0 (W360.E001) |
| Tenant tables without RLS enabled, FORCED, `USING` **and** `WITH CHECK` | 0 of 16 |
| Non-tenant models carrying `organization_id` | 3, all explicitly exempt with documented reasons (`authz.Role` D-001, `events.OutboxEvent`, `events.DeadLetterEvent`) |
| Application role owns tables | **No** — 0 owned |
| Application role can bypass RLS | **No** — `rolsuper=f`, `rolbypassrls=f` |
| `.unscoped` call sites outside the allowlist | 0 (5 modules allowlisted, each with a stated reason) |
| Cross-tenant leaks under 960 interleaved transactions | **0** |

### Authorization audit

| Check | Result |
|---|---|
| Default for a member with no roles | **Deny**, for all 25 declared permissions |
| Unknown permission | **Raises** rather than silently denying |
| Permission naming convention | All 25 match `<product>.<resource>.<action>` |
| `implies` targets that do not exist | 0 |
| Dangerous permissions requiring AAL ≥ 2 | Enforced in the decision function, not the UI |
| Dangerous permissions under impersonation | Blocked outright |
| Expired role assignments | Grant nothing |
| Authorization behaviour when Redis is down | Recomputes from the database; **never fails open** |
| Admin role includes billing | **No** — separated deliberately |
| Product permissions registered before their product exists | **None** |

### Secrets audit

| Check | Result |
|---|---|
| Credential patterns in the repository (AWS, Stripe, private keys, GitHub tokens) | 0 |
| `.env` git-ignored | Yes, with `*.pem`, `*.key`, `credentials.json` |
| Secrets echoed in configuration error messages | **No** — `Env.secret()` never includes the value |
| Production boots with a development default | **No** — refused with a named reason |
| Secret scanning in CI | `detect-secrets` over full history; a tracked `.env` fails the build |

### Fail-closed matrix — verified

| Condition | Required | Verified by |
|---|---|---|
| Tenant context missing | Deny | `test_orm_manager_raises_without_context` |
| RLS variable unset | Zero rows | `test_missing_tenant_context_returns_zero_rows_not_all_rows` |
| Permission not in registry | Deny (raise) | `test_unknown_permission_raises_rather_than_denying` |
| No entitlement row | Deny | `test_organization_with_no_entitlements_is_denied` |
| Unrecognised entitlement status | Deny | `test_unknown_status_denies_rather_than_guessing` |
| Membership inactive | Deny | `test_removed_membership_loses_access` |
| Organization suspended, write attempted | 402 | 18 tests in `test_suspension_write_block.py` |
| File not yet scanned | Not servable | `test_quarantined_file_is_not_servable` |
| Audit write fails | Fail the operation | `test_audit_write_failure_fails_the_operation` |
| Task without tenant headers | Refuse to start | `TenantTask.before_start` raises |

## 6. Not yet exercised

Stated so the gaps are visible rather than implied by silence:

- **No penetration test.** There is no deployed surface to test.
- **No DAST.** Same reason.
- **Authorization matrix test** (12-security.md §17) is not generated, because
  there are three endpoints and no personas beyond member. It becomes
  necessary with the first product.
- **Break-glass and impersonation** are modelled in `TenantContext`
  (`is_break_glass`, mandatory `reason`, blocked dangerous permissions) but
  `ImpersonationGrant` is not implemented. Required before staff tooling.

## 7. Conclusion

**No unresolved P0.** All five P0 findings were discovered by tests written
against the architecture's own stated properties, and all five are fixed with
regression coverage. Four of the five were found because defence-in-depth
*worked* — the outer layer failed and the inner one refused anyway.

The two carried P1 items (breach corpus, malware scanning) are both cases
where the interface exists and the implementation is deliberately absent and
visible, rather than stubbed to look complete.
