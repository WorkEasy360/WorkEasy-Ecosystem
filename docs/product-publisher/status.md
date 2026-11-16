# One-page Product Publisher + Store product page - status

Brief: "WorkEasy360 - Complete Product Creation & Digital Store Redesign" (owner, 16 Nov 2026), plus the owner's note: "all form should be open as pop up and very smoothly so that user experience is premium". Decision record: ADR-068.

## 1. What existed (audit, before any change)

- **Super Admin > Products & Pricing > Product Catalog > Add Product** was a four-step wizard (Identity, Store Listing, Integration, Availability) on its own page (`/admin/products/new`). It had no plans, prices, video or merchandising. It saved nothing until Save Draft or Publish, then made one request per step.
- **The wizard's Publish could not succeed for a new product.** The catalog's publish checklist needs an active plan (`domain.PUBLISH_REQUIREMENTS`), the wizard created none, and the product was left as a draft with "a later step failed".
- **Plans** were created only in Products & Pricing > Plans: `PlanBuilder`, five calls orchestrated by the browser. The product page's Plans tab linked there; those plans are the catalog's `Plan` / `PlanVersion` / `PlanPrice` rows.
- **No endpoint saved a product and its plans together, and there was no idempotency on catalog writes.** Duplicates were held off only by the slug and plan-code uniqueness checks.
- **Preview** was a client-side approximation, with no plans, prices or video. The public Store API hides drafts.
- **The popup** (`controlplane/Modal.tsx`) had no extra-large size, no motion, no fixed footer and no scrolling body.
- **The Store product page** (ADR-065) had a gallery with no previous/next, lightbox or swipe, no video title or description, no Features or Screenshots sections, no plan descriptions, descriptions without headings, and a Web card even when there was no web app.
- **Tax** on the page already came from the server's rate (`tax.terms`). Checkout computes the real quote.
- **Not present anywhere:** reviews or ratings (none exist), a desktop platform key (Windows and macOS app links play that role), and one-time prices that can be sold (checkout refuses one-time).

## 2. What changed

**Backend**
- `commercial/controlplane/product_publisher.py` (new):
  - `bootstrap`, `state`, `save`, `publish`, `preview`;
  - the catalog's own services, one transaction per save or publish;
  - field errors as `plans.<n>.<field>`.
- `interfaces/api/v1/admin_product_manager_views.py` + `urls.py`, four routes:
  - `GET|POST admin/products/publisher`
  - `GET|PUT admin/products/<id>/publisher`
  - `POST admin/products/<id>/publisher/publish`
  - `GET admin/products/<id>/store-preview`
- Catalog:
  - `Product.video_title` (≤120) and `video_description` (≤500), migration `catalog.0014_product_video_details` (additive, database defaults);
  - media kind `video_poster` (an image, one per product; public only while the product is on the Store);
  - screenshot limit 8 → 10.
- `portal_store._product_page` takes `media_url` / `video_url`, for the staff preview; the demo video carries `title` / `description` / `poster` only when they are set.

**Frontend**
- `controlplane/Modal.tsx`: size `xl` (a sheet: fixed title, scrolling body, fixed actions; full screen on a phone). Every popup now fades and rises in (none under reduced motion), and `useClosing` gives the exit.
- `productmanager/publisher/`, all new:
  - `ProductPublisher.tsx`: the page in a popup.
  - `PlanEditor.tsx`: plan cards, Add Another Plan, reorder, Recommended, advanced plan settings.
  - `ScreenshotManager.tsx`: multiple uploads, drag reorder, Make cover, enlarge, captions, progress.
  - `StorePagePreview.tsx`, `model.ts`, `Publisher.module.css`.
- `ProductManager.tsx`: the **Add Product** button opens the publisher; the row menu has **Edit in Publisher**; `/admin/products/new` opens the list with the publisher showing.
- `ProductDetail.tsx`: **Edit in Publisher** on a product's page.
- `AddProductWizard.tsx` is **removed**, and so are its tests (the wizard is replaced, as the brief asks); the publisher's tests replace them.
- `widgets.DescriptionField` gains Heading and Numbered item. `console/MarkdownLite` renders `##` / `###` headings and numbered lists, as React elements only.
- Store product page:
  - `features/store/ProductDetails.tsx`, `Gallery.tsx`, `Lightbox.tsx`, `useSwipe.ts` (+ CSS);
  - breadcrumb Store > Products > Name; hero with highlights; sticky section bar;
  - gallery with previous/next, keys, swipe and a full-screen view;
  - a Product demo section, Features, Screenshots, plan descriptions on plan cards, App Availability for configured platforms only, and a modest trust row;
  - `StoreProductPage` in preview mode for the admin preview.

## 3. How a product is published now

1. Products → **Add Product**. The popup opens with Basic information, Product details & media, Plans & pricing and Advanced settings (closed).
2. Fill in the name, category, short description, application URL and logo; the description, screenshots, video and key features; and the plans (each with its price per billing period, highlights, Recommended, and advanced seats, trial, set-up fee, discount and visibility).
3. **Save Draft** saves the product and its plans, private. **Preview** saves a draft and shows the real Store page to staff only. **Publish to Store** saves, uploads, then publishes through the checklist.
4. The plans are immediately in Products & Pricing > Plans, and the product, once published, is in the Digital Store with its plans. Customers buy through the existing checkout (Store sign-in, organization, quote, payment, subscription, entitlement, Open product).

## 4. Test results

| Run | Result |
|---|---|
| New `backend/tests/product_publisher` | 10 / 10. Covered: one request saves product + plans as the Plans section's rows; a refused plan leaves nothing and names `plans.<n>.<field>`; a second save updates (no duplicate); a price change supersedes (the old row is ended, never edited); seats publish version 2; a draft is private; preview is staff-only, with staff media URLs and the video title; publish refuses without a logo and changes nothing, then sells with plans in the public Store; free plan; pricing grant required; one Recommended; another product's plan is refused; the video's shape is unchanged when not set |
| Affected backend suites (product_publisher, product_manager, portal_store, store_domain, store_upgrade, store_checkout, store, phase10, architecture, phase7, phase9, portals, accounts_portal, catalog, pricing) | 1,593 passed, 2 failed, both from this change and both fixed. (1) A test pinned the old 8-screenshot limit; it now reads the constant and pins 10. (2) The migration overlap guard asked for 0014's reason, which is now recorded (`sqlmigrate` shows only two `ADD COLUMN ... DEFAULT ''`). Re-run 186 passed |
| Frontend vitest, full | **1,726 / 1,726** (100 files): the publisher 11, the Store page 12 new, the Store / storefront / console suites 198 |
| Static | ruff check + format, mypy, lint-imports (18 kept), bandit, eslint, tsc (src) - clean |
| Real browser (headless Chrome over a Vite harness, 1440 px and a true 390 px) | Found and fixed two real bugs. (1) The popup's entrance animation left a `transform` in effect, which made the card the containing block of fixed or absolute children, so the whole sheet scrolled. The fix: `backwards` fill, `overflow: clip`, and the viewer portalled to the body. (2) Section jumps scrolled the backdrop, so they now scroll the sheet's body directly. Also: the description preview now draws headings and numbered lists. At 390 px the sheet is full screen with no sideways scroll |

## 5. Remaining limits and owner steps

- **One-time billing is not offered** in the publisher: the checkout sells subscriptions only and refuses a one-time price.
- **Feature entitlements, included usage, renewal and change rules, and partner terms** stay in Products & Pricing > Plans. The publisher links there and does not duplicate them. A brand-new product has no feature rows, because no UI creates Feature rows (unchanged).
- **The Store page's trust row** names platform facts only (one WorkEasy360 account, subscriptions in My Account, server-verified checkout). There are no ratings or reviews because none exist.
- **Live plans:** production has eight sample plans, named "Sample plan" with codes `<product>_sample_<tier>`, added on 2026-11-12. They show in the publisher like any other plan. Retire them in Plans before setting real prices.
