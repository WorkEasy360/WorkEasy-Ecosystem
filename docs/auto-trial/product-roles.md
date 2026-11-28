# Default product roles: the platform side and what each product must change

Decision: [ADR-078](../architecture/adrs/ADR-078-automatic-free-trial-activation-and-product-access.md), section "Default roles". Found 2026-11-28 by reading each product repository (read-only; nothing in any product repository was changed).

## 1. What the platform now sends

With the `organization` scope, the ID token carries two more claims. All four products already request that scope: connector `conf.py` DEFAULT_SCOPES, Easy Project `Workeasy360Provider.java`, Help Desk `WORKEASY360_SCOPES`.

| Claim | Example | Meaning |
|---|---|---|
| `product_role` | `"admin"` | The product-level role the product's **default-role policy** gives this person. The policy is set by Super Admin per product (Product Manager → Availability → Default product roles). It is never a platform, staff or super-admin role: the policy refuses any such name. |
| `organization_roles` | `["owner"]` | The person's WorkEasy360 roles in this organization: `owner`, `admin`, `billing_manager`, `member` or `readonly`. **Informational only**: a product must not grant anything from it. |

The platform default policy is `owner → admin`, `admin → admin`, `billing_manager → member`, `member → member` and `readonly → viewer`. A person with several roles gets the role of the strongest. Both claims are absent if the person is no longer an active member (the authorization endpoint would already have refused them).

## 2. Rules every product keeps

1. **Owner is reserved.** The product's own owner or SUPER_ADMIN stays for the person who created the tenant. A claim never grants it, and `organization_roles: ["owner"]` never does either.
2. **Absent or unknown claim: today's behaviour.** Each product falls back to its `WORKEASY360_DEFAULT_ROLE`.
3. **Never `is_staff` / `is_superuser`** or any platform-wide authority.
4. **When to apply it (owner decision).** The minimal change below applies the role only when the membership is **created**, which is the closest to today. Re-applying it at every sign-in is optional. It needs the guards listed per product, and it overrides role changes an organization's admin made inside the product.

## 3. Per product

### Shared connector (`Accounts/workeasy360-sso`, used by Easy CRM and EasyBook)

The connector reads no role today. The hook belongs on `DefaultOrganizationAdapter` (`organizations.py`), where membership is decided. Add `product_role(self, claims) -> str | None`: the claim only if it is a string matching `^[a-z][a-z0-9_]{1,31}$`, else `None`. Optionally store it in the session payload (`authentication.py`, `SESSION_KEY`) and on `SSOContext`. Mapping onto local roles stays in each product. Test it next to `tests/test_extension_points.py`.

### Easy CRM (`AI-CRM-sso-work`, branch `feat/workeasy360-sso-connector`)

- **Today:** `backend/apps/accounts/workeasy360/provisioning.py`. The creator of the organization gets `owner`. Everyone else gets `default_role_key()` from `WORKEASY360_DEFAULT_ROLE` (default `sales_rep`), and that is never re-synced.
- **Change:**
  1. Add `product_role` to `SignInClaims` / `read_claims`.
  2. Map it: `{"admin": ADMIN, "member": SALES_REP, "viewer": VIEWER}`, optionally `"manager": SALES_MANAGER`.
  3. In `_resolve_membership`, use `role_key = mapped or default_role_key()`.
  4. Accept only `ASSIGNABLE_BY_ADMIN`, never `OWNER`.
  5. Record the source in the `MEMBER_JOINED` audit metadata.
- **Re-sync (optional):** in the existing-membership branch, through `services.change_member_role` with a system actor. Skip when the current role is `owner`.

### EasyBook (branch `feat/workeasy360-sso-connector`, `backend/sso/provisioning.py`)

- **Today:** `role = Role.OWNER if organization_created else _default_role()`, where the default comes from `WORKEASY360_DEFAULT_ROLE` (default `staff`).
- **Change:**
  1. Pass `claims` to `_membership_for`.
  2. Map it: `{"admin": Role.ADMIN, "member": Role.STAFF, "viewer": Role.VIEWER}`. `accountant` comes only from an explicit product value.
  3. Use `Role.OWNER if organization_created else (mapped or _default_role())`.
  4. Never `OWNER` from the claim.
- **Re-sync (optional):** skip when `membership.role == Role.OWNER`, and audit with `record()`.

### Easy Project (`Project-Module`, branch `feat/backchannel-logout`; also `feat/workeasy360-sso-main`)

- **Today:** there is no organization-level role. Every signed-in person is `ROLE_USER` (`Workeasy360OidcUserService.loadUser`, `Workeasy360Authentication`). The only roles are per project (`ProjectRole`), and the project's creator owns it.
- **Change:**
  1. Read `token.getClaimAsString("product_role")` in `loadUser`.
  2. Map it to a **new** small enum (ADMIN / MEMBER / VIEWER), never a `ProjectRole`.
  3. Carry it on `Workeasy360Principal`. It is serialized in the session: bump `serialVersionUID`, or accept one fresh sign-in.
  4. Add e.g. `ROLE_PRODUCT_ADMIN` beside `ROLE_USER`. Absent means today's behaviour.
- **Nothing to re-sync:** the principal is rebuilt at every sign-in. Bearer and gateway sign-ins (`feat/workeasy360-sso-main`) carry no claim and keep the default.

### Easy Help Desk (`helpdesk-workeasy`, branch `feat/backchannel-logout`)

- **Today:** `backend/api/src/workeasy360/workeasy360-provisioning.service.ts`. The organization's creator gets `SUPER_ADMIN` (`provisionOrganization`). A new agent gets `WORKEASY360_DEFAULT_ROLE` (ADMIN, AGENT or LIGHT_AGENT; default AGENT). There is no re-sync.
- **Change:**
  1. Add `product_role?: string; organization_roles?: string[]` to `VerifiedIdToken` (`packages/workeasy360/src/client.ts`).
  2. In `signInToOrganization` step 3, use `systemKey: mapProductRole(claims.product_role) ?? defaultRole`, with `{admin: "ADMIN", member: "AGENT", viewer: "LIGHT_AGENT"}`.
  3. Never `SUPER_ADMIN` or `CUSTOMER`.
- **Re-sync (optional):** replace only system-role `UserRole` rows and never remove `SUPER_ADMIN`. Call `accessControl.invalidateUser` and audit with `record()`.

## 4. Matching each product's policy to its roles

Super Admin can name each product's own roles directly in its policy, which saves the product a mapping. For example, Easy CRM `member → sales_rep` and Help Desk `member → agent`. A product then accepts either the generic names or its own, as long as the result is in the product's assignable set.
