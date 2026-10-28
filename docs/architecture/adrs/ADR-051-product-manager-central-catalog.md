# ADR-051: The Product Manager - one central product catalog

Status: accepted, 2026-10-27 (Admin Product Manager, Phase 1). Builds on Phase 9 (product presentation), Phase 11 (category, icon, per-product trial, the products console) and ADR-044 (product sign-in).

## Context

The owner asked for a professional Super Admin "Product Manager": create, edit, preview, publish, unpublish and archive products, with logos, store listing, URLs, availability and trial - and explicitly **one** Product record, no second subscription, entitlement, plan or pricing system, and no product-specific code.

Most of it already existed: `catalog.Product` (code, name, slug, tagline, description, marketing URL, category, icon, status, listing), `ProductTrialConfig`, the integration registry (launch URL, sign-in mode, health URL, credentials) and the Phase 11 console.

## Decisions

1. **One Product row, minimal additions.** `catalog.0005` adds nine columns, each with a default: `store_title`, `store_summary`, `application_url`, `documentation_url`, `support_url`, and four eligibility flags `customer_store_enabled`, `reseller_enabled`, `affiliate_enabled`, `white_label_enabled` (default **true**, so every product that sold before sells exactly where it did). One table is new: `catalog_product_media` (logo, banner, screenshots). Nothing is renamed or dropped.
2. **Reuse, by mapping:**
   | Product Manager | Stored as |
   |---|---|
   | Draft / Active / Archived | `status` draft / available / deprecated or retired (`manager_status`, derived) |
   | Published / Hidden | `listing` listed / hidden (`coming_soon` still shown as such) |
   | Short description, Full description, Learn More URL | `tagline`, `description` (light Markdown, rendered as text), `marketing_url` |
   | Product icon | `icon`: one of the built-in drawings (no upload, no URL) |
   | Launch URL, SSO enabled, Integration type, Health check | the integration registry (`sign_in_mode` sso_only = OIDC / local_only = External); no second auth system |
   | Trial | `ProductTrialConfig` |
   | Plans, Pricing | the existing Plans & add-ons and Currency & pricing screens, opened with `?product=<code>` |
   | Activity | the one audit log: entries about the product row or naming its code |
3. **Product code from the slug.** A new product's permanent code is its slug with `-` -> `_` (`workeasy-test-app` -> `workeasy_test_app`), so a slug is at most 32 characters. The **slug is fixed once the product has been active** (`activated_at`): links, the public site and partner pages use it.
4. **Lifecycle actions.** Publish = active + listed, refused unless the checklist holds: name, slug, category, short description, logo, application URL, the integration's launch URL, not archived. The same check guards a `listing -> listed` edit. Unpublish = hidden (status kept). Archive = `deprecated` + hidden, from draft too (new `draft -> deprecated` edge, reversible); Restore = active + hidden. **Publishing grants nothing**: access is an entitlement, made only by a trial or a purchase.
5. **Delete only what nothing used:** a draft never activated, with no plans, features or integration, and no control-plane record naming its code (overrides, grants, rules, credentials - read from the audit log). Everything else is archived.
6. **Customer store rule.** `store_products()` = active AND published AND customer-store enabled. The flag is applied to every customer-facing listing (public catalog, marketplace offers, trial offers), so "Customer Store: Not allowed" really hides it. The reseller, affiliate and white-label flags are **eligibility configuration only** in this phase: they do not replace `PartnerProductGrant` or capabilities, and the partner flows do not read them yet.
7. **Images through the shared files platform.** PNG or JPEG only (SVG, GIF, WebP and everything else refused), size and pixel bounds per kind, read from the bytes (no decoder); the files platform re-checks the signature, stores under the platform organization's prefix and scans before serving. Served same-origin with `nosniff` and a sandbox CSP; a media id resolves only through its own product. Previews use `data:` URLs (the CSP is not loosened for `blob:`).
8. **URLs** pass the integration registry's gate (`validate_browser_url`): https only in every environment, no credentials, backslash or percent-encoded host, no loopback/private/metadata/internal name. The marketing URL now passes it too.
9. **Trial without plans.** A trial may be switched on before the product has a plan; nobody can start it until staff mark a plan eligible (the offer and the start already require one). Previously the switch itself was refused.
10. **Manifest:** `redirect_uris` is required unless `sign_in_mode` is `local_only` (the authorize endpoint refuses `local_only` anyway), so a product with SSO switched off can register its launch URL. A switch back to an SSO mode without a redirect URI is refused.

## Consequences

- The Phase 11 products console screen is replaced by the Product Manager (list, wizard, detail); its integration and trial components moved unchanged into `features/controlplane/productmanager/`. The `GET admin/products/console` read is extended, not duplicated.
- A product with an unpublished slug change after activation needs a deliberate data fix; the UI says the slug is fixed.
- Reseller / affiliate / white-label enforcement of the new flags is future work (no partner-flow change in this phase).
