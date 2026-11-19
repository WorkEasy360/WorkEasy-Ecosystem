# ADR-074: The account chooser before the access answer, and automatic product sign-in setup

- Status: proposed (2026-11-19). Branch `feat/universal-login-complete`, on `integration/universal-login` (PR #9 + #10 + #11 + #12 + `main`). Not deployed.
- Context: the owner's brief "Complete, connect & prepare Universal Login for production" (19 November 2026). From User Admin, a customer must be able to choose any configured product and meet "Continue as [name]" / "Use another account", **whether or not the organization bought it**. And Super Admin must register, verify and activate a new compatible product's sign-in **without manual identity-provider registration**.
- Builds on: ADR-044 (sign-in preferences and the access chooser), ADR-066 (Ask once), ADR-070 (domain ownership), ADR-071 (Accounts hosts identity), PR #11 (the account question before the organization question).
- Amends: ADR-044 section "the preference is read only after every access check". The entitlement and seat answers are still *asked* in the same order. They are *delivered* after the account question when the person's preference asks it.

## Context

1. **The chooser never appeared for a product the organization had not bought.** `authorize` refused `product_not_entitled` / `product_no_seat` (steps 8-9) before it read the person's preference (step 10). A person signed in to the wrong account (the one without the purchase) could not reach "Use another account": they went straight to the product's "no access" page. User Admin's launcher refused such products too (`NO_ENTITLEMENT`), and its Explore page ("Plans & Trials") offered nothing but Buy or Start trial.
2. **The chooser's words** were "Continue with Same Account" / "Use Different Account".
3. **Super Admin's Add Product created an integration only when a Launch URL was typed.** That integration was a draft with no credential. The public key, the real addresses, the domain proof and activation were separate manual steps. Nothing recorded where a product's setup stood.

## Decision

### 1. The account question comes before the commercial answer (ADR-044 amended)

`authorization.commercial_refusal(subject, product_code)` runs steps 8 and 9 and *returns* the refusal instead of raising it. `authorize` and `resume` then:

1. run steps 4-7 exactly as before (`resolve_subject` still raises membership, organization and access-policy refusals at once);
2. ask `commercial_refusal`;
3. read the preference (step 10);
4. if the preference or the product asks which account to use, **park the request for the chooser, whatever the commercial answer**;
5. otherwise raise the commercial refusal, if there is one;
6. only then issue a code.

What does not change:

- **No code is minted for an unentitled organization or an unseated member.** The resume re-runs every check for whoever answered, and delivers the refusal to the product there.
- **Automatic** (`auto_sso`) asks nothing, so it is refused at once, as before.
- **`prompt=none`** shows nothing, so the commercial refusal (the more useful answer) is returned at once.
- **The product's own "no access" page** is what the person sees after "Continue as", because the refusal reaches its registered redirect URI as `error=product_not_entitled` (or `product_no_seat`). The connector maps these to `not_entitled` / `no_seat`, which every product already renders.
- **Ask once** records an entry only when a code is issued. So a refused product asks again next time, rather than going silently to the same refusal.
- Only `product_not_entitled` and `product_no_seat` may follow the chooser (`COMMERCIAL_REFUSALS`, pinned by a test).

### 2. "Sign in" from Explore Products

- `POST /api/v1/orgs/<slug>/apps/<code>/sign-in` (`launcher_views.sign_in_view`) sends the browser to any product whose WorkEasy360 sign-in is **available**, with `organization=<uuid>`. Available means an active integration, a mode that offers WorkEasy360 sign-in, and a usable credential (`SignInState`, from the registry only).
  - It reads **no entitlement**. That is the point, and it is safe because the authorization endpoint decides access after the chooser.
  - It is a POST under the tenant path, so CSRF and membership apply. Its destination is built from the registry, never from the request.
  - It uses the same route rule as the launch, so the person's sign-in preference is honoured.
- The launch endpoint is unchanged and remains **the** access decision. My Products is unchanged.
- Explore listings (and the organization's Store, which shares them) carry `sign_in: available | suspended | unavailable` per product.
- The User Admin page is renamed **Explore Products** (it was "Plans & Trials"). A product the organization does not hold offers **Sign in to <product>**: primary for a member, secondary beside Buy or Start trial for an owner.
  - Every card says whether the organization has the product ("In My Products" / "Not in your organization yet. You can still sign in; the product decides what you can use"), so being able to sign in is never read as having bought it.
- The chooser reads **Continue as <name>** / **Use another account**.

### 3. Automatic product sign-in setup

**The contract.** The product serves `/.well-known/workeasy360-sso` (schema `workeasy360-sso/1`).

- The Django connector serves it from 0.4.0 (`*well_known()`). Any other stack serves the same JSON (connector `docs/compatibility.md`).
- It names the product code, client id, issuer, connector and version, the launch, callback, sign-out-return and back-channel-logout addresses, and the **public** JWK set.
- With `?challenge=` it adds a short-lived proof: a JWT signed with the private key, with `typ` `w360-sso-setup+jwt`, `aud` `<issuer>/sso-setup` and `nonce` = the challenge.
- The proof's audience is never the token endpoint, so it can never be a client assertion (pinned by tests on both sides).

**The record.** `ecosystem.ProductSignInSetup` (migration `ecosystem.0011`), one row per catalog product code.

- It is linked to the product's one `ProductIntegration`, whose `product_code` is unique.
- It holds the state, the ordered checks with their reasons, the connector seen, `requested_by_id` (who the checks act for), back-off timing, and `key_absences`.

**The states.** Super Admin's five words are stored results of checks, never browser labels:

| State | Meaning |
|---|---|
| **Pending Configuration** | No application URL yet, or the document is not served yet ("Pending integration: ... install workeasy360-sso 0.4.0 or later and add `*well_known()` ...") |
| **Configured** | The document matches. For a suspended or retired integration, which setup never changes, this is where it stays |
| **Verification Required** | Everything matches; the domain proof (ADR-070 TXT record, shown) is missing |
| **Active** | Every check passed and the integration authenticates |
| **Failed** | A check found something the product must fix. The reason is named; nothing is activated |

**The flow.** Every write goes through the registry's own authorized, audited services.

1. **Prepare** (`sso_setup.prepare`), in the Add Product transaction, with no network. It runs from `product_manager.create`, from the Publisher's save when the application URL arrives or changes, and from the Integration tab's application-URL save.
   - It creates the setup row.
   - Once there is an application URL, it registers the one OIDC client as a draft on that origin, with a placeholder callback that a draft can never use.
   - It queues the first check for after commit.
   - It never fails Add Product for want of the integration grant: the client is then prepared by the first check of someone who holds it.
2. **Check** (`sso_setup.check`): the `products-sign-in-setup` beat (every 300 s, due rows only) or *Check now*.
   - It fetches the document through the egress client, which resolves, validates and pins every address and follows no redirects (64 KB, 8 s), with a fresh challenge.
   - `evaluate_document` (pure) checks:
     - the schema, and connector version 0.4.0 or later;
     - the product code, client id, an issuer the platform serves, and `private_key_jwt`;
     - every address: https, no credentials or fragment, on the application's own host;
     - the keys: RS256/ES256 public signing keys only. Private members are refused and called out ("PRIVATE KEY MATERIAL IS PUBLISHED");
     - the proof: type, a published `kid` and its algorithm, audience, issuer and subject, the nonce, at most 300 s of life, and freshness.
3. **Domain.** ADR-070's TXT lookup for the application host, through `product_domains.verify_domain`.
4. **Configure.**
   - `update_manifest` takes the document's addresses and stamps the manifest version they need. A draft accepts any hostname; an active integration is still refused an unproven one by ADR-070.
   - `create_credential(credential_id=<kid>)` registers each published key under the product's own key id. `create_credential` now accepts a product-chosen id of 4-64 safe characters, unique per integration, and refuses a reused one.
   - A registered `kid` that comes back with a different key **fails** ("publish the new key under a new key id").
   - A key this setup registered that stops being published is revoked only after **3 checks in a row** without it, and only while a published key remains usable. A rolling deploy that serves old and new documents side by side keeps working. Credentials registered by hand are never revoked by setup.
5. **Activate.** `transition(draft -> active)`, which refuses an unproven hostname by itself. It happens only when every check has passed, and never for a suspended or retired integration.

**Who acts.** The checks act for `requested_by_id`: the staff member who added the product, or who last chose *Check now*. They do so through `platform_context`, with permissions checked live on every write.

- There is no system principal with standing power.
- If that person loses the grants, the checks stop and say so (`actor`).
- Every check is audited as that person (`ecosystem.integration.sign_in_setup_checked`, with `automatic`, `state`, `activated` and the checks not passed).

**What never happens.**

- An active integration is never deactivated by a check. A product that stops serving its document keeps working; the setup reports it, and its state stays Active.
- A product registered by hand is not touched until staff choose *Check now*.
- No secret is read, stored or shown.

**Retries.** Back-off of 5 min × 2^n, up to 6 h, until Active; then a daily re-check, so keys and addresses follow the document. Retries, concurrent requests and re-preparation never create a second client, setup row or credential (tested).

**API.**

- `GET admin/products/<code>/integration/sign-in-setup` needs `platform.integration.view`.
- `POST .../sign-in-setup/check` needs `platform.integration.update` and is rate-limited (`product_sign_in_setup_user` 10/min, `_ip` 30/min).
- Super Admin's Integration tab shows the *Automatic sign-in setup* card: the state, the client id with a copy button, the document address, the connector, the last and next check, and each check with its reason. *Check now* is for managers only.

## Consequences

- A customer of any organization reaches "Continue as" / "Use another account" for any configured product. Access is still decided only by the authorization endpoint and the product. Tests prove the chooser first, the same refusal after, and zero codes.
- A new product built on connector 0.4.0 goes from Add Product to Active with three things from its team:
  - deploying with the client id Super Admin shows;
  - publishing its key in the document;
  - publishing one TXT record.
- Products on Spring or Node serve the same JSON to use it. Until they do, they show **Pending Configuration** with the exact requirement, and can still be registered by hand as before.
- Existing products are unaffected until someone chooses *Check now*.
- Rollback: the migration adds one table that the previous release neither reads nor writes. Reverting the code leaves it unused; `migrate ecosystem 0010` drops it.
