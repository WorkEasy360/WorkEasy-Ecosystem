# ADR-068: The one-page Product Publisher (a popup, plans inline) and the Store product page redesign

Date: 2026-11-16. Status: accepted; committed `4fff4ae` + `fe495b7` on `feat/partner-admin-panel`, pushed (PR #3). Live since 16 November 2026: release `2026.11.16-a3f1485` (`catalog.0014` applied), then `2026.11.16-fe495b7` (the field-naming fix, frontend only). Replaces the four-step Add Product wizard of ADR-051. Builds on ADR-056 (one catalog, price and commercial engine), ADR-058 (GST from the tax engine), ADR-065 (the Digital Store's own host) and the 2026-11-05 decision that every form opens in a popup.

## Context

The owner's brief "WorkEasy360 - Complete Product Creation & Digital Store Redesign" (16 Nov 2026) asks for:

- the four-step Add Product wizard (Identity, Store Listing, Integration, Availability) to become **one organized page**, which the owner wants opened as a smooth popup ("all form should be open as pop up and very smoothly");
- pricing plans created **inside that page**, saved as the same records Products & Pricing > Plans shows, never a copy;
- Save Draft, Preview of the real Store page, and Publish to Store, without duplicate records;
- a redesigned Store product page: gallery with a lightbox, demo video with title and description, features, screenshots, plans, app availability, and a purchase panel taxed by the real engine.

The audit (`docs/product-publisher/status.md`, section 1) found:

- **The wizard could not publish a new product.** The catalog's publish checklist needs an active plan, the wizard created none, and its Publish left a draft behind.
- Plans could only be made in the Plans section, through a five-call client-side sequence.
- No endpoint wrote a product and its plans together.
- There was no draft preview of the real Store page. The wizard's preview was an approximation without plans, prices or video.
- The popup primitive had no extra-large size, no animation and no fixed footer.
- The Store page lacked: a lightbox, prev/next and swipe on the gallery, video title, description and thumbnail, a screenshots section, plan descriptions, headings in descriptions, and configured-only app availability.

## Decision

1. **One page, one save, the catalog's own records.** `commercial/controlplane/product_publisher.py` composes the owners' services in one transaction. There is no second model and no second plan store:
   - **The product.** A new one is `product_manager.create`, exactly as the wizard did: a draft, hidden row; the integration when a launch URL is given; the trial. An existing one is `catalog.update_product` with only the changed fields.
   - **A new plan** is `create_plan`, then `draft_plan_version`, then `publish_price` per billing period, then `publish_plan_version`.
   - **An existing plan:**
     - Presentation changes (name, caption, highlights, visibility, Store order) go through `update_plan`.
     - A change of seats or trial publishes a **new version**. The one a subscriber bought is never edited (the triggers forbid it).
     - A changed price publishes a new row that supersedes the running one for new purchases only. A period no longer offered is closed.
   - **Recommended:** one plan per product, as `Product.recommended_plan` already enforced.
   - **Refusals:** a refused step rolls everything back. A refusal carries its field (`plans.<n>.<field>`) to the right input on the page.
   - **Duplicates:** the page keeps the product id from the first save, and the slug and plan code uniqueness refuse a second record, so repeated saves update rather than duplicate.
2. **Publish** (`POST admin/products/<id>/publisher/publish`) makes every visible, priced plan available and publishes the product, in one transaction. The catalog's own publish checklist decides, and a refusal changes nothing. Saving a product that is already live puts its new visible, priced plans on sale at once.
3. **Images and the video stay on the existing media endpoint.** The page stages them and uploads after the save (the files platform needs the product to exist), with progress, in Store order:
   - Screenshots are reordered only when the order the page shows differs from what the server holds.
   - "Make cover" puts a screenshot first. The Store's primary image is the first one (after the banner, as before).
   - The limit is now **10** screenshots (it was 8).
   - New media kind `video_poster` for the demo's thumbnail: image rules like a screenshot (320×180 minimum).
   - New product fields `video_title` (≤120) and `video_description` (≤500), in migration `catalog.0014`. Both are additive with a database default.
4. **Preview** (`GET admin/products/<id>/store-preview`, staff only) is the Store's own page builder (`portal_store._product_page`) applied to this product, draft included:
   - Images come from the staff media endpoint (a new `media_url` parameter), so nothing is made public to be previewed.
   - It is rendered by the Store's own component, `StoreProductPage`, in `preview` mode: no purchase link, no related products.
   - Preview saves a private draft first.
5. **The popup.** `Modal` gains:
   - the `xl` size: a sheet with its title fixed, its own scrolling body and its actions fixed at the bottom, full screen on a phone;
   - an enter animation for **every** popup (backdrop fade, card rise; transform and opacity only, none under reduced motion);
   - `useClosing` for a matching exit.

   The page is Basic information, Product details & media, Plans & pricing and Advanced settings (closed), with Cancel | Save Draft | Preview | Publish to Store always visible.
   - `/admin/products/new` is the product list with the page open, so every existing link still works.
   - "Edit in Publisher" opens it on the list and on a product's page.
   - Leaving with unsaved changes asks first.
6. **Safe defaults and preserved integration.** A new product is offered on the customer Store only; reseller, affiliate and white-label stay off until staff turn them on. Partners still need the product granted to them.
   - The Application URL is the catalog field. A launch URL override, single sign-on and its callback, and a health URL live under Advanced settings, and registering them still needs the integration grant.
   - **An Application URL alone registers no integration and claims no SSO.**
   - A product that already has an integration keeps editing it on its Integration tab.
7. **Rich text stays safe.** Descriptions are Markdown rendered as React elements by `MarkdownLite`, now with `##`/`###` headings (drawn below the page's section heading) and numbered lists. The editor's toolbar gains Heading and Numbered item. No HTML is ever rendered.
8. **The Store product page** (`features/store/ProductDetails.tsx`, `Gallery.tsx`, new `Lightbox.tsx`) is redesigned per the brief Part D. It still uses only the real published product and plan data; the tax estimate is the server's rate; there is exactly one Buy Now. Nothing is invented: no ratings, counts or claims.

## Consequences

- Staff create a product and its plans in one place, and those plans are immediately the Plans section's rows. The Plans section stays the place for add-ons, feature entitlements, included usage, renewal and change rules, and partner terms. The publisher links there and does not copy them.
- **One-time prices are not offered.** The checkout sells subscriptions only and refuses a one-time price (`payments.orders`). Offering it would publish a price nobody can buy.
- A plan's highlights are stored as its description, a bullet list, and shown on its Store card.
- Rollback: the previous release ignores the two new columns (they have database defaults). Its enum does not know `video_poster`, and its media lists include every kind except video, so **delete those rows first**: `DELETE FROM catalog_product_media WHERE kind = 'video_poster'`. Their files stay in storage. Then switch the release. No database restore is needed.
