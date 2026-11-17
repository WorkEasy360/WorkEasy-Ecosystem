# Migrations `ecosystem.0007` and `ecosystem.0008`: what they do, and how to apply and undo them

- `0007_backchannel_logout` is in PR #4 (ADR-069, back-channel logout).
- `0008_product_domains` is in the stacked domain-verification PR (ADR-070).
- **Neither is applied in production.** Both were applied and tested only on the isolated development stack and the CI and test databases.
- Order: `0006_sign_in_ask_once` (live), then `0007`, then `0008`. `0008` depends on `0007`.

## `ecosystem.0007_backchannel_logout`

| Operation | SQL | Lock and cost |
|---|---|---|
| `AddField AuthorizationCode.sign_in_id` | `ALTER TABLE ecosystem_authorization_code ADD COLUMN sign_in_id uuid NULL` | Nullable with no default, so it is a catalog-only change in PostgreSQL 11+ and no rows are rewritten. Takes a brief `ACCESS EXCLUSIVE` lock on a table of short-lived codes. |
| `CreateModel BackchannelLogout` | `CREATE TABLE ecosystem_backchannel_logout`, plus FK to `ecosystem_product_integration` and indexes `ix_bcl_due` and `ix_bcl_user` | New, empty table. Creating the FK takes a short `SHARE ROW EXCLUSIVE` lock on `ecosystem_product_integration` (four rows). |
| `CreateModel ProductSignIn` | `CREATE TABLE ecosystem_product_sign_in`, plus FK, the partial unique index `uq_psi_live_per_sign_in` (`WHERE ended_at IS NULL`), and `ix_psi_user_live` and `ix_psi_sign_in` | New, empty table. Same short FK lock. |

- **Data touched:** none. No existing row is read for writing or updated.
- **Behaviour on deploy:**
  - Codes issued before the deploy have `sign_in_id = NULL`, so they record no `ProductSignIn`. Such a session gets no back-channel logout; its product's entitlement re-check still ends it, as it does today.
  - Nothing is delivered until a product registers a `backchannel_logout_uri`.
- **Reverse:** `migrate ecosystem 0006` drops both tables and the column.
  - The only data lost is delivery history and sign-in-to-product records, neither of which existed before.
  - The code of a release without ADR-069 never reads them.

## `ecosystem.0008_product_domains`

| Operation | SQL | Lock and cost |
|---|---|---|
| `CreateModel ProductDomain` | `CREATE TABLE ecosystem_product_domain`, plus FK to `ecosystem_product_integration`, unique `(integration_id, hostname)` (`uq_product_domain`), and index `ix_product_domain_check` | New table. Short FK lock as above. |
| `RunPython carry_over` (reverse: no-op) | Inserts rows only | One pass over `ecosystem_product_integration` (four rows in production), with a few rows inserted per integration. |

**The carry-over** applies a frozen copy of the hostname rules, which reads every URL field of the stored manifest:

- **Active or suspended integrations:** every hostname their manifest uses at that moment is inserted as `verified`, with method `pre_existing`. All four live products are covered, so they keep working with no action.
- **Draft:** each hostname is inserted as `pending`, with a fresh token. Activation now needs a DNS proof (or `--attest-domains` for first-party hosts).
- **Retired:** nothing is inserted.

**Data touched:** inserts into the new table only. No existing row of any table is updated.

**Reverse:** `migrate ecosystem 0007` drops the table, and the carry-over goes with it. The code of a release without ADR-070 never reads it.

## Applying (production runbook)

1. Take a backup or snapshot first, as for every release.
2. `migrate` runs with the migrator role (`W360_DB_MIGRATOR_USER`), as today.
   - New tables get the application role's privileges through the default privileges in `infra/postgres/init.sql`, so no manual `GRANT` is needed.
   - These tables are platform-level, with no `organization_id`, and are not under RLS, like `ecosystem_product_integration`.
3. Check the result:
   ```sql
   SELECT app, name FROM django_migrations
    WHERE app = 'ecosystem' AND name IN ('0007_backchannel_logout', '0008_product_domains');
   -- every active/suspended integration has all its hostnames verified:
   SELECT i.product_code, d.hostname, d.status, d.method
     FROM ecosystem_product_domain d
     JOIN ecosystem_product_integration i ON i.id = d.integration_id
    ORDER BY 1, 2;
   SELECT count(*) FROM ecosystem_backchannel_logout;   -- 0 right after deploy
   ```
4. Deploy order:
   1. `migrate`;
   2. web;
   3. workers and beat. The beat runs `backchannel-logout-deliver` (15 s), `backchannel-logout-purge` and `products-recheck-domains` (6 h, heavy queue).
5. Optional, recommended: replace each `pre_existing` hostname with a DNS proof.
   1. Super Admin, then Products, then Integration, then Domains, then Request proof.
   2. Publish `_w360-product-verify.<host>  TXT  "<value>"`.
   3. Choose Verify.
   
   The hostname stays verified throughout.

## Rolling back

- **Code only.** Redeploy the previous image. The new tables are simply unused, so this is safe without touching the database.
- **Database too.** Use this only if the tables themselves must go:
  1. Stop the workers and beat.
  2. Run `python backend/manage.py migrate ecosystem 0006_sign_in_ask_once` with the release that contains the migrations, before redeploying the old image.
  3. Redeploy the old image.
- Neither path changes users, sessions, organizations, entitlements, credentials or any product's data.
